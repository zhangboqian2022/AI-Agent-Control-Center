from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aacc import public_version
from aacc.codex_quota import (
    CodexQuotaSnapshot,
    CodexQuotaStatus,
    parse_app_server_rate_limits,
)
from aacc.security import redact
from aacc.windows_broker import (
    WINDOWS_PROCESS_CREATION_FLAGS as WINDOWS_PROCESS_CREATION_FLAGS,
)
from aacc.windows_broker import (
    BrokerCommand,
)

APP_SERVER_TIMEOUT_SECONDS = 10.0
MAX_APP_SERVER_LINE_CHARS = 65_536
APP_SERVER_QUEUE_SIZE = 32
WhichExecutable = Callable[[str], str | None]
IsRegularFile = Callable[[Path], bool]
PopenFactory = Callable[..., subprocess.Popen[str]]
ProcessCommandFactory = Callable[[Path], BrokerCommand]

_logger = logging.getLogger("aacc.codex_quota")


def _is_regular_file(path: Path) -> bool:
    return path.is_file()


def find_codex_executable(
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: WhichExecutable = shutil.which,
    is_file: IsRegularFile = _is_regular_file,
) -> Path | None:
    """Locate a Codex executable without launching a task or a shell."""

    resolved_home = home or Path.home()
    environment = os.environ if environ is None else environ
    candidates: list[Path] = []

    override = environment.get("AACC_CODEX_EXECUTABLE")
    if override:
        candidates.append(Path(override).expanduser())

    path_match = which("codex")
    if path_match:
        candidates.append(Path(path_match))

    if platform == "darwin":
        candidates.extend(
            (
                resolved_home / ".local" / "bin" / "codex",
                Path("/opt/homebrew/bin/codex"),
                Path("/usr/local/bin/codex"),
                Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                resolved_home / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex",
            )
        )
    elif platform == "win32":
        for key in ("APPDATA", "LOCALAPPDATA"):
            base = environment.get(key)
            if base:
                candidates.append(Path(base) / "npm" / "codex.cmd")

    for candidate in candidates:
        if is_file(candidate):
            return candidate
    return None


class CodexAppServerReader:
    """Read the Codex account limits through one bounded app-server process."""

    def __init__(
        self,
        executable: Path,
        *,
        timeout_seconds: float = APP_SERVER_TIMEOUT_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        popen: PopenFactory = subprocess.Popen,
        version: str | None = None,
        platform: str = sys.platform,
        command_factory: ProcessCommandFactory | None = None,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = max(0.05, timeout_seconds)
        self._now = now
        self._popen = popen
        self._version = version or public_version()
        self._platform = platform
        self._command_factory = command_factory

    def read_latest(self) -> CodexQuotaSnapshot:
        if self._platform == "win32" and self._command_factory is None:
            return self._unknown()
        process: subprocess.Popen[str] | None = None
        reader_thread: threading.Thread | None = None
        output: queue.Queue[str | None] = queue.Queue(maxsize=APP_SERVER_QUEUE_SIZE)
        deadline = time.monotonic() + self._timeout_seconds
        try:
            popen_options: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            command = self._process_command()
            if command.creationflags:
                popen_options["creationflags"] = command.creationflags
            process = self._popen(
                command.args,
                **popen_options,
            )
            if process.stdin is None or process.stdout is None:
                return self._unknown()
            reader_thread = threading.Thread(
                target=self._read_stdout,
                args=(process.stdout, output),
                name="aacc-codex-app-server-output",
                daemon=True,
            )
            reader_thread.start()
            self._send(
                process,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "aacc", "version": self._version},
                        "capabilities": {},
                    },
                },
            )
            initialized = self._wait_for_response(output, request_id=1, deadline=deadline)
            if initialized is None:
                return self._unknown()
            self._send(process, {"method": "initialized", "params": {}})
            self._send(
                process,
                {
                    "id": 2,
                    "method": "account/rateLimits/read",
                    "params": {},
                },
            )
            result = self._wait_for_response(output, request_id=2, deadline=deadline)
            if result is None:
                return self._unknown()
            return parse_app_server_rate_limits(result, now=self._now())
        except (OSError, ValueError, TypeError) as error:
            detail = redact(str(error) or type(error).__name__)[:160]
            _logger.debug("Codex app-server quota source unavailable: %s", detail)
            return self._unknown()
        finally:
            self._reap(process)
            if reader_thread is not None:
                reader_thread.join(timeout=0.2)

    def _process_command(self) -> BrokerCommand:
        if self._command_factory is not None:
            return self._command_factory(self._executable)
        return BrokerCommand(
            (str(self._executable), "app-server", "--stdio"),
            0,
        )

    @staticmethod
    def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise OSError("Codex app-server stdin is unavailable")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    @staticmethod
    def _wait_for_response(
        output: queue.Queue[str | None],
        *,
        request_id: int,
        deadline: float,
    ) -> object | None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = output.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                return None
            result = message.get("result")
            return result if isinstance(result, dict) else None

    @staticmethod
    def _read_stdout(
        stream: Any,
        output: queue.Queue[str | None],
    ) -> None:
        while True:
            line = stream.readline(MAX_APP_SERVER_LINE_CHARS + 1)
            if line == "":
                CodexAppServerReader._queue_output(output, None)
                return
            if len(line) > MAX_APP_SERVER_LINE_CHARS:
                while line and not line.endswith("\n"):
                    line = stream.readline(MAX_APP_SERVER_LINE_CHARS + 1)
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") not in (1, 2):
                continue
            CodexAppServerReader._queue_output(output, line)

    @staticmethod
    def _queue_output(output: queue.Queue[str | None], value: str | None) -> None:
        try:
            output.put_nowait(value)
        except queue.Full:
            return

    def _reap(self, process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        try:
            is_running = process.poll() is None
        except OSError:
            is_running = False
        if is_running:
            with suppress(OSError):
                process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                process.kill()
            with suppress(OSError, subprocess.TimeoutExpired):
                process.wait(timeout=0.5)
        except OSError:
            pass
        finally:
            if process.stdout is not None:
                with suppress(OSError):
                    process.stdout.close()

    @staticmethod
    def _unknown() -> CodexQuotaSnapshot:
        return CodexQuotaSnapshot(
            weekly=None,
            observed_at=None,
            status=CodexQuotaStatus.UNKNOWN,
        )
