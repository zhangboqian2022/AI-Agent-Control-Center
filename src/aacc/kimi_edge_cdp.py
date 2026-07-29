"""Isolated Microsoft Edge session primitives for Kimi membership quota."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Protocol, cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from aacc.file_security import protect_directory
from aacc.kimi_membership_query import membership_fetch_expression
from aacc.kimi_web_error import KimiWebErrorCategory

KIMI_MEMBERSHIP_URL = "https://www.kimi.com/membership/subscription"
_EDGE_RELATIVE_PATH = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
_REPARSE_POINT_ATTRIBUTE = 0x400
MAX_CDP_MESSAGE_BYTES = 4 * 1024 * 1024
EDGE_STARTUP_TIMEOUT_SECONDS = 15.0
EDGE_LOGIN_TIMEOUT_SECONDS = 15.0 * 60.0
EDGE_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class EdgeSessionError(RuntimeError):
    """Sanitized failure raised by the managed Edge boundary."""

    def __init__(self, category: KimiWebErrorCategory) -> None:
        self.category = category
        super().__init__(category.value)


class EdgeUnauthorizedError(RuntimeError):
    """Internal signal that the dedicated Kimi browser session expired."""


class EdgeCancelledError(RuntimeError):
    """Internal signal that the owning Qt session cancelled this operation."""


@dataclass(frozen=True)
class EdgeLaunchSpec:
    executable: Path
    arguments: tuple[str, ...]
    profile: Path


@dataclass(frozen=True)
class DevToolsEndpoint:
    http_origin: str
    browser_websocket: str


@dataclass(frozen=True)
class EdgeQuotaResult:
    stats: object
    subscription: object


class _WebSocketLike(Protocol):
    def send(self, payload: str) -> object: ...

    def recv(self) -> object: ...

    def close(self) -> object: ...


class _ProcessLike(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...


def edge_profile_path(local_app_data: Path) -> Path:
    """Return the only Edge profile AACC is allowed to manage."""

    return local_app_data / "AACC" / "kimi-edge-profile"


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def validate_owned_profile(profile: Path, local_app_data: Path) -> None:
    """Reject a profile that is not the exact AACC-owned path."""

    expected = edge_profile_path(local_app_data)
    if profile != expected or _is_reparse_point(profile):
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    aacc_root = expected.parent
    if _is_reparse_point(aacc_root) or _is_reparse_point(local_app_data):
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    if profile.exists() and not profile.is_dir():
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)


def clear_owned_profile(profile: Path, local_app_data: Path) -> None:
    """Remove only AACC's exact Edge profile, never a user browser profile."""

    validate_owned_profile(profile, local_app_data)
    if not profile.exists():
        return
    quarantine = profile.parent / f".kimi-edge-profile.logout-{uuid4().hex}"
    try:
        os.replace(profile, quarantine)
        shutil.rmtree(quarantine)
    except OSError as error:
        raise EdgeSessionError(KimiWebErrorCategory.LOGOUT_PARTIAL) from error


def _default_registry_reader(key: str, name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        registry = import_module("winreg")
        with registry.OpenKey(registry.HKEY_LOCAL_MACHINE, key) as handle:
            value, _kind = registry.QueryValueEx(handle, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def find_edge_executable(
    *,
    environ: Mapping[str, str] = os.environ,
    registry_reader: Callable[[str, str], str | None] = _default_registry_reader,
) -> Path:
    """Locate an installed Edge binary without invoking a shell or PATH."""

    candidates: list[Path] = []
    registry_value = registry_reader(
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        "",
    )
    if registry_value:
        candidates.append(Path(registry_value))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = environ.get(variable)
        if root:
            candidates.append(Path(root) / _EDGE_RELATIVE_PATH)

    for candidate in candidates:
        if (
            candidate.name.casefold() == "msedge.exe"
            and candidate.is_file()
            and not _is_reparse_point(candidate)
        ):
            return candidate
    raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)


def build_edge_launch(executable: Path, profile: Path, *, visible: bool) -> EdgeLaunchSpec:
    """Build a shell-free launch specification for a managed Edge process."""

    mode_arguments: tuple[str, ...] = () if visible else ("--headless=new", "--disable-gpu")
    return EdgeLaunchSpec(
        executable=executable,
        profile=profile,
        arguments=(
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            *mode_arguments,
            f"--app={KIMI_MEMBERSHIP_URL}",
        ),
    )


def read_devtools_endpoint(profile: Path) -> DevToolsEndpoint:
    """Parse Edge's random loopback debugging endpoint without trusting URLs."""

    active_port = profile / "DevToolsActivePort"
    try:
        if active_port.stat().st_size > 4096:
            raise ValueError
        lines = active_port.read_text(encoding="ascii").splitlines()
        port = int(lines[0])
        browser_path = lines[1]
    except (OSError, UnicodeError, ValueError, IndexError) as error:
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED) from error
    if (
        port < 1
        or port > 65535
        or not browser_path.startswith("/devtools/browser/")
        or "://" in browser_path
        or any(character in browser_path for character in ("?", "#", "\\"))
    ):
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    return DevToolsEndpoint(
        http_origin=f"http://127.0.0.1:{port}",
        browser_websocket=f"ws://127.0.0.1:{port}{browser_path}",
    )


def select_kimi_target(targets: object, *, expected_port: int) -> str:
    """Select the Kimi page while rejecting externally supplied CDP URLs."""

    if not isinstance(targets, list):
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    for candidate in targets:
        if not isinstance(candidate, dict):
            continue
        page_url = candidate.get("url")
        websocket_url = candidate.get("webSocketDebuggerUrl")
        if (
            candidate.get("type") != "page"
            or not isinstance(page_url, str)
            or not page_url.startswith("https://www.kimi.com/")
            or not isinstance(websocket_url, str)
        ):
            continue
        parsed = urlparse(websocket_url)
        if (
            parsed.scheme == "ws"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == expected_port
            and parsed.path.startswith("/devtools/page/")
            and not parsed.query
            and not parsed.fragment
        ):
            return websocket_url
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)


def parse_quota_payload(payload: object) -> EdgeQuotaResult:
    """Reduce an untrusted page result to the two membership documents."""

    if not isinstance(payload, dict):
        raise EdgeSessionError(KimiWebErrorCategory.REFRESH_FAILED)
    kind = payload.get("kind")
    if kind == "unauthorized":
        raise EdgeUnauthorizedError
    if kind != "quota" or "stats" not in payload or "subscription" not in payload:
        raise EdgeSessionError(KimiWebErrorCategory.REFRESH_FAILED)
    return EdgeQuotaResult(payload["stats"], payload["subscription"])


class CdpConnection:
    """Small synchronous CDP client used only from the Edge worker thread."""

    def __init__(self, socket: _WebSocketLike) -> None:
        self._socket = socket
        self._next_id = 1

    def evaluate(self, expression: str) -> object:
        response = self._request(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        try:
            result = cast(dict[str, object], response["result"])
            if "exceptionDetails" in result:
                raise ValueError
            remote = cast(dict[str, object], result["result"])
            return remote.get("value")
        except (KeyError, TypeError, ValueError) as error:
            raise EdgeSessionError(KimiWebErrorCategory.REFRESH_FAILED) from error

    def close_browser(self) -> None:
        self._request("Browser.close", {})

    def close(self) -> None:
        with suppress(Exception):
            self._socket.close()

    def _request(self, method: str, params: Mapping[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps(
            {"id": request_id, "method": method, "params": dict(params)},
            separators=(",", ":"),
        )
        try:
            self._socket.send(payload)
            while True:
                raw = self._socket.recv()
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CDP_MESSAGE_BYTES:
                    raise ValueError
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise ValueError
                return cast(dict[str, object], message)
        except EdgeSessionError:
            raise
        except Exception as error:
            raise EdgeSessionError(KimiWebErrorCategory.REFRESH_FAILED) from error


def _protect_profile(profile: Path) -> None:
    protect_directory(profile, platform="win32")


def _start_process(command: list[str]) -> _ProcessLike:
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(  # noqa: S603 - executable is resolved from trusted locations
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )


def _load_targets(origin: str) -> object:
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        response = client.get(f"{origin}/json/list")
        response.raise_for_status()
        return response.json()


def _open_socket(url: str) -> _WebSocketLike:
    websocket = import_module("websocket")
    create_connection = websocket.create_connection
    return cast(
        _WebSocketLike,
        create_connection(url, timeout=5.0, suppress_origin=True),
    )


class ManagedEdgeOperation:
    """Run one visible login or headless quota refresh against the owned profile."""

    def __init__(
        self,
        *,
        local_app_data: Path,
        executable: Path | None = None,
        protector: Callable[[Path], None] = _protect_profile,
        process_factory: Callable[[list[str]], _ProcessLike] = _start_process,
        target_loader: Callable[[str], object] = _load_targets,
        socket_factory: Callable[[str], _WebSocketLike] = _open_socket,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.local_app_data = local_app_data
        self.profile = edge_profile_path(local_app_data)
        self.executable = executable
        self._protector = protector
        self._process_factory = process_factory
        self._target_loader = target_loader
        self._socket_factory = socket_factory
        self._sleep = sleep
        self._monotonic = monotonic

    def run(self, *, visible: bool, cancel: Event) -> EdgeQuotaResult:
        if cancel.is_set():
            raise EdgeCancelledError
        validate_owned_profile(self.profile, self.local_app_data)
        try:
            self._protector(self.profile)
            validate_owned_profile(self.profile, self.local_app_data)
        except EdgeSessionError:
            raise
        except Exception as error:
            raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED) from error
        self._remove_stale_active_port()
        executable = self.executable or find_edge_executable()
        spec = build_edge_launch(executable, self.profile, visible=visible)
        try:
            process = self._process_factory([str(spec.executable), *spec.arguments])
        except Exception as error:
            raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED) from error

        browser: CdpConnection | None = None
        try:
            endpoint = self._wait_for_endpoint(process, cancel)
            browser = CdpConnection(self._socket_factory(endpoint.browser_websocket))
            port = urlparse(endpoint.http_origin).port
            if port is None:
                raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
            login_deadline = self._monotonic() + EDGE_LOGIN_TIMEOUT_SECONDS
            while True:
                if cancel.is_set():
                    raise EdgeCancelledError
                if process.poll() is not None:
                    raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
                try:
                    targets = self._target_loader(endpoint.http_origin)
                    page_url = select_kimi_target(targets, expected_port=port)
                    page = CdpConnection(self._socket_factory(page_url))
                    try:
                        payload = page.evaluate(membership_fetch_expression())
                    finally:
                        page.close()
                    return parse_quota_payload(payload)
                except EdgeUnauthorizedError:
                    if not visible:
                        raise
                    if self._monotonic() >= login_deadline:
                        raise EdgeSessionError(KimiWebErrorCategory.REFRESH_TIMEOUT) from None
                    self._sleep(2.0)
                except EdgeCancelledError:
                    raise
                except EdgeSessionError:
                    if not visible or self._monotonic() >= login_deadline:
                        raise
                    self._sleep(1.0)
        finally:
            if browser is not None:
                with suppress(EdgeSessionError):
                    browser.close_browser()
                browser.close()
            self._shutdown_process(process)

    def _wait_for_endpoint(
        self,
        process: _ProcessLike,
        cancel: Event,
    ) -> DevToolsEndpoint:
        deadline = self._monotonic() + EDGE_STARTUP_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            if cancel.is_set():
                raise EdgeCancelledError
            if process.poll() is not None:
                raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
            try:
                return read_devtools_endpoint(self.profile)
            except EdgeSessionError:
                self._sleep(0.1)
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)

    def _remove_stale_active_port(self) -> None:
        active_port = self.profile / "DevToolsActivePort"
        if not active_port.exists():
            return
        if _is_reparse_point(active_port) or not active_port.is_file():
            raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
        try:
            active_port.unlink()
        except OSError as error:
            raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED) from error

    @staticmethod
    def _shutdown_process(process: _ProcessLike) -> None:
        try:
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
            return
        except Exception:
            pass
        with suppress(Exception):
            process.terminate()
        with suppress(Exception):
            process.wait(timeout=EDGE_SHUTDOWN_TIMEOUT_SECONDS)
