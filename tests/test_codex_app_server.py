from __future__ import annotations

import io
import json
import logging
import queue
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aacc.codex_app_server import (
    WINDOWS_PROCESS_CREATION_FLAGS,
    CodexAppServerReader,
    find_codex_executable,
    find_running_desktop_codex,
)
from aacc.codex_quota import (
    CodexQuotaStatus,
    parse_app_server_rate_limits,
)
from aacc.windows_broker import BrokerCommand

NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)


def test_find_codex_executable_prefers_explicit_override(tmp_path: Path) -> None:
    override = tmp_path / "override-codex"
    path_codex = tmp_path / "path-codex"
    existing = {override, path_codex}

    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={"AACC_CODEX_EXECUTABLE": str(override)},
        which=lambda _name: str(path_codex),
        is_file=existing.__contains__,
    )

    assert result == override


def test_find_codex_executable_uses_path_before_known_locations(tmp_path: Path) -> None:
    path_codex = tmp_path / "bin" / "codex"
    local_codex = tmp_path / ".local" / "bin" / "codex"
    existing = {path_codex, local_codex}

    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={},
        which=lambda _name: str(path_codex),
        is_file=existing.__contains__,
    )

    assert result == path_codex


@pytest.mark.parametrize(
    ("existing_suffix", "expected_suffix"),
    [
        (".local/bin/codex", ".local/bin/codex"),
        ("Applications/ChatGPT.app/Contents/Resources/codex", ".local/bin/codex"),
    ],
)
def test_find_codex_executable_checks_macos_home_candidates_in_order(
    tmp_path: Path,
    existing_suffix: str,
    expected_suffix: str,
) -> None:
    local = tmp_path / ".local" / "bin" / "codex"
    bundled = tmp_path / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex"
    existing = {local, bundled}
    if existing_suffix.startswith("Applications"):
        existing.remove(local)

    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={},
        which=lambda _name: None,
        is_file=existing.__contains__,
    )

    expected = local if expected_suffix.startswith(".local") and local in existing else bundled
    assert result == expected


def test_find_codex_executable_checks_windows_npm_locations(tmp_path: Path) -> None:
    roaming = tmp_path / "Roaming"
    local = tmp_path / "Local"
    roaming_codex = roaming / "npm" / "codex.cmd"
    local_codex = local / "npm" / "codex.cmd"

    result = find_codex_executable(
        platform="win32",
        home=tmp_path,
        environ={"APPDATA": str(roaming), "LOCALAPPDATA": str(local)},
        which=lambda _name: None,
        is_file={roaming_codex, local_codex}.__contains__,
    )

    assert result == roaming_codex


def test_find_running_desktop_codex_accepts_trusted_chatgpt_resource(
    tmp_path: Path,
) -> None:
    codex = tmp_path / "OpenAI" / "ChatGPT" / "resources" / "codex.exe"
    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"MZ")

    class Process:
        info = {"name": "codex.exe", "exe": str(codex)}

    result = find_running_desktop_codex(
        process_iter=lambda _attrs: [Process()],
    )

    assert result == codex


def test_find_running_desktop_codex_rejects_untrusted_same_named_process(
    tmp_path: Path,
) -> None:
    codex = tmp_path / "Temp" / "codex.exe"
    codex.parent.mkdir()
    codex.write_bytes(b"MZ")

    class Process:
        info = {"name": "codex.exe", "exe": str(codex)}

    assert find_running_desktop_codex(process_iter=lambda _attrs: [Process()]) is None


def test_find_codex_executable_checks_windows_chatgpt_resources_before_processes(
    tmp_path: Path,
) -> None:
    local = tmp_path / "Local"
    installed = local / "Programs" / "ChatGPT" / "resources" / "codex.exe"
    running = local / "OpenAI" / "ChatGPT" / "resources" / "codex.exe"

    result = find_codex_executable(
        platform="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": str(local)},
        which=lambda _name: None,
        is_file={installed, running}.__contains__,
        running_desktop_locator=lambda: running,
    )

    assert result == installed


def test_find_codex_executable_rejects_non_files(tmp_path: Path) -> None:
    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={"AACC_CODEX_EXECUTABLE": str(tmp_path / "directory")},
        which=lambda _name: str(tmp_path / "missing"),
        is_file=lambda _path: False,
    )

    assert result is None


def _window(
    *,
    minutes: int = 10_080,
    used: object = 9,
    reset_delta: timedelta = timedelta(hours=1),
) -> dict[str, object]:
    return {
        "usedPercent": used,
        "windowDurationMins": minutes,
        "resetsAt": int((NOW + reset_delta).timestamp()),
    }


def test_parse_app_server_prefers_named_codex_limit() -> None:
    snapshot = parse_app_server_rate_limits(
        {
            "rateLimits": {"primary": _window(minutes=300, used=1)},
            "rateLimitsByLimitId": {
                "other": {"primary": _window(used=95)},
                "codex": {
                    "planType": "prolite",
                    "primary": _window(used=9),
                },
            },
        },
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 9
    assert snapshot.plan_type == "prolite"
    assert snapshot.observed_at == NOW


def test_parse_app_server_falls_back_to_legacy_rate_limits() -> None:
    snapshot = parse_app_server_rate_limits(
        {
            "rateLimits": {
                "planType": "team",
                "primary": _window(minutes=300),
                "secondary": _window(used=64),
            }
        },
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 64
    assert snapshot.plan_type == "team"


@pytest.mark.parametrize(
    "window",
    [
        _window(minutes=300),
        _window(used=-1),
        _window(used=101),
        _window(used=True),
        _window(reset_delta=timedelta(seconds=-1)),
        {"usedPercent": 9, "windowDurationMins": 10_080, "resetsAt": "invalid"},
    ],
)
def test_parse_app_server_rejects_invalid_weekly_windows(window: object) -> None:
    snapshot = parse_app_server_rate_limits(
        {"rateLimits": {"primary": window}},
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert snapshot.weekly is None


def test_parse_app_server_truncates_plan_type() -> None:
    snapshot = parse_app_server_rate_limits(
        {
            "rateLimits": {
                "planType": "p" * 80,
                "primary": _window(),
            }
        },
        now=NOW,
    )

    assert snapshot.plan_type == "p" * 32


def _fake_server(tmp_path: Path, body: str) -> Path:
    source = (
        f"#!/usr/bin/env python3\nimport json\nimport sys\nimport time\n{textwrap.dedent(body)}"
    )
    if sys.platform == "win32":
        python_server = tmp_path / "fake-codex.py"
        python_server.write_text(source, encoding="utf-8")
        server = tmp_path / "fake-codex.cmd"
        server.write_text(
            f'@echo off\r\n"{sys.executable}" "{python_server}" %*\r\n',
            encoding="utf-8",
        )
        return server
    server = tmp_path / "fake-codex"
    server.write_text(source, encoding="utf-8")
    server.chmod(0o755)
    return server


def _capturing_popen(
    calls: list[tuple[str, ...]],
) -> Any:
    def start(args: tuple[str, ...], **kwargs: object) -> subprocess.Popen[str]:
        calls.append(args)
        return subprocess.Popen(args, **kwargs)

    return start


def test_app_server_reader_performs_handshake_and_matches_quota_response(
    tmp_path: Path,
) -> None:
    server = _fake_server(
        tmp_path,
        """
initialize = json.loads(sys.stdin.readline())
print(json.dumps({"id": initialize["id"], "result": {}}), flush=True)
initialized = json.loads(sys.stdin.readline())
assert initialized["method"] == "initialized"
request = json.loads(sys.stdin.readline())
assert request["method"] == "account/rateLimits/read"
print(json.dumps({"method": "account/updated", "params": {}}), flush=True)
print(json.dumps({"id": 999, "result": {"rateLimits": {}}}), flush=True)
print(json.dumps({
    "id": request["id"],
    "result": {
        "rateLimitsByLimitId": {
            "codex": {
                "planType": "prolite",
                "primary": {
                    "usedPercent": 9,
                    "windowDurationMins": 10080,
                    "resetsAt": 1785747600
                }
            }
        }
    }
}), flush=True)
""",
    )
    calls: list[tuple[str, ...]] = []
    reader = CodexAppServerReader(
        server,
        timeout_seconds=2,
        now=lambda: NOW,
        popen=_capturing_popen(calls),
        platform="darwin",
    )

    snapshot = reader.read_latest()

    assert calls == [(str(server), "app-server", "--stdio")]
    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 9
    assert snapshot.plan_type == "prolite"


def test_stdout_notifications_cannot_evict_a_matching_response() -> None:
    response = json.dumps({"id": 2, "result": {"rateLimits": {}}})
    notifications = [
        json.dumps({"method": "account/updated", "params": {"index": index}}) for index in range(64)
    ]
    stream = io.StringIO("\n".join([response, *notifications]) + "\n")
    output: queue.Queue[str | None] = queue.Queue(maxsize=32)

    CodexAppServerReader._read_stdout(stream, output)

    assert CodexAppServerReader._wait_for_response(
        output,
        request_id=2,
        deadline=time.monotonic() + 1,
    ) == {"rateLimits": {}}


def test_windows_reader_uses_broker_command_and_reaps_only_broker(tmp_path: Path) -> None:
    class FakeStream:
        close_calls = 0

        def write(self, _value: str) -> int:
            return 0

        def flush(self) -> None:
            pass

        def readline(self, _limit: int) -> str:
            return ""

        def close(self) -> None:
            self.close_calls += 1

    class FakeProcess:
        pid = 4321
        stdin = FakeStream()
        stdout = FakeStream()
        terminate_calls = 0
        kill_calls = 0
        wait_calls = 0

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            self.wait_calls += 1
            return 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1

    process = FakeProcess()
    popen_args: tuple[str, ...] | None = None
    popen_kwargs: dict[str, object] = {}

    def popen(args: tuple[str, ...], **kwargs: object) -> FakeProcess:
        nonlocal popen_args
        popen_args = args
        popen_kwargs.update(kwargs)
        return process

    reader = CodexAppServerReader(
        tmp_path / "codex.cmd",
        timeout_seconds=0.05,
        popen=popen,
        platform="win32",
        command_factory=lambda _codex: BrokerCommand(
            ("C:\\AACC\\aacc-spawn.exe", "--protocol", "1"),
            WINDOWS_PROCESS_CREATION_FLAGS,
        ),
    )
    reader.read_latest()

    assert popen_args == ("C:\\AACC\\aacc-spawn.exe", "--protocol", "1")
    assert popen_kwargs["creationflags"] == WINDOWS_PROCESS_CREATION_FLAGS
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert process.wait_calls == 1
    assert process.stdin.close_calls == 1
    assert process.stdout.close_calls == 1


def test_windows_reader_without_broker_factory_fails_closed_before_popen(
    tmp_path: Path,
) -> None:
    popen_calls = 0

    def popen(*_args: object, **_kwargs: object) -> None:
        nonlocal popen_calls
        popen_calls += 1

    snapshot = CodexAppServerReader(
        tmp_path / "codex.cmd",
        platform="win32",
        popen=popen,
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert popen_calls == 0


@pytest.mark.parametrize(
    "error",
    (
        ValueError(r"invalid C:\Users\private\codex.cmd"),
        RuntimeError(r"failed C:\Users\private\aacc-spawn.exe"),
    ),
)
def test_windows_reader_sanitizes_command_factory_failures(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    popen_calls = 0

    def command_factory(_codex: Path) -> BrokerCommand:
        raise error

    def popen(*_args: object, **_kwargs: object) -> None:
        nonlocal popen_calls
        popen_calls += 1

    caplog.set_level(logging.DEBUG, logger="aacc.codex_quota")
    snapshot = CodexAppServerReader(
        tmp_path / "codex.cmd",
        platform="win32",
        command_factory=command_factory,
        popen=popen,
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert popen_calls == 0
    assert "Codex app-server command unavailable" in caplog.text
    assert "private" not in caplog.text
    assert "codex.cmd" not in caplog.text
    assert "aacc-spawn.exe" not in caplog.text


def test_reader_sanitizes_process_start_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_path = r"C:\Users\private\aacc-spawn.exe"

    def popen(*_args: object, **_kwargs: object) -> None:
        raise OSError(f"cannot start {private_path}")

    caplog.set_level(logging.DEBUG, logger="aacc.codex_quota")
    snapshot = CodexAppServerReader(
        tmp_path / "codex.cmd",
        platform="win32",
        command_factory=lambda _codex: BrokerCommand((private_path,), 0),
        popen=popen,
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert "Codex app-server quota source unavailable" in caplog.text
    assert "private" not in caplog.text
    assert "aacc-spawn.exe" not in caplog.text


def test_reader_reap_kills_broker_only_after_wait_timeout(tmp_path: Path) -> None:
    class FakeStream:
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class FakeProcess:
        stdin = FakeStream()
        stdout = FakeStream()
        terminate_calls = 0
        kill_calls = 0
        wait_calls = 0

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminate_calls += 1

        def wait(self, timeout: float) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("broker", timeout)
            return 0

        def kill(self) -> None:
            self.kill_calls += 1

    process = FakeProcess()
    reader = CodexAppServerReader(
        tmp_path / "codex.cmd",
        platform="win32",
        command_factory=lambda _codex: BrokerCommand(("broker",), 0),
    )

    reader._reap(process)  # type: ignore[arg-type]

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.wait_calls == 2
    assert process.stdin.close_calls == 1
    assert process.stdout.close_calls == 1


def test_reader_reap_waits_for_already_exited_process(tmp_path: Path) -> None:
    class FakeProcess:
        stdin = None
        stdout = None
        wait_calls = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float) -> int:
            self.wait_calls += 1
            return 0

        def terminate(self) -> None:
            raise AssertionError("already-exited process must not be terminated")

        def kill(self) -> None:
            raise AssertionError("already-exited process must not be killed")

    process = FakeProcess()
    reader = CodexAppServerReader(tmp_path / "codex", platform="darwin")

    reader._reap(process)  # type: ignore[arg-type]

    assert process.wait_calls == 1


def test_reader_reap_suppresses_process_os_errors_and_closes_streams(
    tmp_path: Path,
) -> None:
    class FakeStream:
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class FakeProcess:
        stdin = FakeStream()
        stdout = FakeStream()

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise OSError("process disappeared")

        def wait(self, timeout: float) -> int:
            raise OSError("invalid process handle")

        def kill(self) -> None:
            raise AssertionError("OSError is not a timeout")

    process = FakeProcess()
    reader = CodexAppServerReader(tmp_path / "codex", platform="darwin")

    reader._reap(process)  # type: ignore[arg-type]

    assert process.stdin.close_calls == 1
    assert process.stdout.close_calls == 1


def test_reader_reap_suppresses_value_error_from_already_closed_streams(
    tmp_path: Path,
) -> None:
    class ClosedStream:
        def close(self) -> None:
            raise ValueError("I/O operation on closed file")

    class FakeProcess:
        stdin = ClosedStream()
        stdout = ClosedStream()

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("already-exited process must not be terminated")

        def kill(self) -> None:
            raise AssertionError("already-exited process must not be killed")

    reader = CodexAppServerReader(tmp_path / "codex", platform="darwin")

    reader._reap(FakeProcess())  # type: ignore[arg-type]


def test_python_sources_contain_no_taskkill() -> None:
    source_root = Path(__file__).parents[1] / "src"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))

    assert "taskkill" not in sources.lower()


def test_app_server_reader_times_out_and_reaps_child(tmp_path: Path) -> None:
    server = _fake_server(
        tmp_path,
        """
json.loads(sys.stdin.readline())
time.sleep(10)
""",
    )
    children: list[subprocess.Popen[str]] = []

    def start(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
        process = subprocess.Popen(args, **kwargs)
        children.append(process)
        return process

    snapshot = CodexAppServerReader(
        server,
        timeout_seconds=0.1,
        now=lambda: NOW,
        popen=start,
        platform="darwin",
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert children
    assert children[0].poll() is not None


@pytest.mark.parametrize(
    "response_line",
    [
        "not-json",
        '{"id": 1, "error": {"code": -32601, "message": "method missing"}}',
    ],
)
def test_app_server_reader_returns_unknown_for_invalid_or_incompatible_server(
    tmp_path: Path,
    response_line: str,
) -> None:
    server = _fake_server(
        tmp_path,
        f"""
initialize = json.loads(sys.stdin.readline())
print({response_line!r}, flush=True)
""",
    )

    snapshot = CodexAppServerReader(
        server,
        timeout_seconds=1,
        now=lambda: NOW,
        platform="darwin",
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN


def test_app_server_reader_ignores_oversized_output(tmp_path: Path) -> None:
    server = _fake_server(
        tmp_path,
        """
initialize = json.loads(sys.stdin.readline())
print("x" * 70000, flush=True)
""",
    )

    snapshot = CodexAppServerReader(
        server,
        timeout_seconds=1,
        now=lambda: NOW,
        platform="darwin",
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
