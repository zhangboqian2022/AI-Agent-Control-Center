"""Authenticated, cooperative shutdown used by the Windows Setup package."""

from __future__ import annotations

import ctypes
import logging
import ntpath
import sys
from ctypes import wintypes
from typing import Any, Protocol, cast

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QTimer

AACC_WINDOW_TITLE = "AI Agent Control Center"
SHUTDOWN_MESSAGE_NAME = "AACC.ShutdownForUpdate.v1"
MAX_SHUTDOWN_TIMEOUT_MS = 120_000
_NATURAL_EXIT_WINERRORS = {87, 1168, 1400}

_logger = logging.getLogger("aacc.shutdown")
_WINDOWS_EVENT_TYPES = {b"windows_generic_MSG", b"windows_dispatcher_MSG"}


class _ProcessHandle(Protocol):
    def __enter__(self) -> _ProcessHandle: ...

    def __exit__(self, *args: object) -> None: ...

    def image_name(self) -> str: ...

    def wait_for_exit(self, timeout_ms: int) -> bool: ...


class _Win32ShutdownApi(Protocol):
    def register_window_message(self, name: str) -> int: ...

    def find_exact_windows(self, title: str) -> tuple[int, ...]: ...

    def window_process_id(self, hwnd: int) -> int: ...

    def open_verified_process(self, pid: int) -> _ProcessHandle: ...

    def post_message(self, hwnd: int, message: int) -> None: ...


def _normalized_windows_image(path: str) -> str:
    normalized = path.replace("/", "\\")
    if normalized.casefold().startswith("\\\\?\\unc\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.casefold().startswith("\\\\?\\"):
        normalized = normalized[4:]
    return ntpath.normcase(ntpath.normpath(normalized))


def _valid_timeout(timeout_ms: object) -> bool:
    return (
        isinstance(timeout_ms, int)
        and not isinstance(timeout_ms, bool)
        and 0 <= timeout_ms <= MAX_SHUTDOWN_TIMEOUT_MS
    )


def _failed(stage: str) -> int:
    _logger.error("Shutdown-for-update failed stage=%s", stage)
    return 1


def _is_natural_exit_error(error: OSError) -> bool:
    code = getattr(error, "winerror_code", None)
    if code is None:
        code = getattr(error, "winerror", None)
    if code is None:
        code = error.errno
    return code in _NATURAL_EXIT_WINERRORS


def request_shutdown_for_update(
    timeout_ms: int = 20_000,
    win32_module: object | None = None,
) -> int:
    """Ask the installed AACC instance to quit, then wait on its verified handle."""
    if not _valid_timeout(timeout_ms):
        raise ValueError("timeout_ms must be an integer from 0 to 120000")
    if win32_module is None:
        from aacc import win32

        api = cast(_Win32ShutdownApi, win32)
    else:
        api = cast(_Win32ShutdownApi, win32_module)

    expected_image = _normalized_windows_image(sys.executable)
    try:
        message = api.register_window_message(SHUTDOWN_MESSAGE_NAME)
        windows = api.find_exact_windows(AACC_WINDOW_TITLE)
    except OSError:
        return _failed("discover")
    if not windows:
        return 0

    saw_candidate_failure = False
    for hwnd in windows:
        try:
            pid = api.window_process_id(hwnd)
        except OSError as error:
            if _is_natural_exit_error(error):
                continue
            saw_candidate_failure = True
            continue
        except KeyError:
            saw_candidate_failure = True
            continue
        try:
            process_context = api.open_verified_process(pid)
        except OSError as error:
            if _is_natural_exit_error(error):
                continue
            saw_candidate_failure = True
            continue
        try:
            with process_context as process:
                try:
                    image = _normalized_windows_image(process.image_name())
                except OSError:
                    saw_candidate_failure = True
                    continue
                if image != expected_image:
                    saw_candidate_failure = True
                    continue
                try:
                    current_pid = api.window_process_id(hwnd)
                except OSError as error:
                    if _is_natural_exit_error(error):
                        continue
                    saw_candidate_failure = True
                    continue
                except KeyError:
                    saw_candidate_failure = True
                    continue
                if current_pid != pid:
                    saw_candidate_failure = True
                    continue
                try:
                    api.post_message(hwnd, message)
                except (OSError, ProcessLookupError):
                    try:
                        if process.wait_for_exit(timeout_ms):
                            return 0
                    except OSError:
                        pass
                    return _failed("post")
                try:
                    if process.wait_for_exit(timeout_ms):
                        return 0
                except OSError:
                    return _failed("wait")
                return _failed("timeout")
        except OSError:
            return _failed("close")
    return _failed("candidate") if saw_candidate_failure else 0


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class _ShutdownEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: WindowsShutdownListener) -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(
        self,
        event_type: QByteArray | bytes | bytearray | memoryview[int],
        message: int,
    ) -> tuple[bool, int]:
        raw_event_type = (
            bytes(event_type.data()) if isinstance(event_type, QByteArray) else bytes(event_type)
        )
        if raw_event_type not in _WINDOWS_EVENT_TYPES or not message:
            return False, 0
        msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        self._owner.dispatch_message(
            event_type=raw_event_type,
            hwnd=int(msg.hwnd or 0),
            message=int(msg.message),
            w_param=int(msg.wParam),
            l_param=int(msg.lParam),
        )
        return False, 0


class WindowsShutdownListener:
    """Independent Qt native filter for the Setup shutdown message."""

    def __init__(self, *, win32_module: Any | None = None) -> None:
        if win32_module is None:
            from aacc import win32

            self._win32: Any = win32
        else:
            self._win32 = win32_module

        self._qt_app: Any | None = None
        self._window: Any | None = None
        self._hwnd = 0
        self._message = 0
        self._filter: _ShutdownEventFilter | None = None
        self._quit_scheduled = False

    def start(self, qt_app: Any, window: Any) -> None:
        if self._filter is not None:
            return
        message = self._win32.register_window_message(SHUTDOWN_MESSAGE_NAME)
        if not message:
            raise OSError("RegisterWindowMessageW failed")
        hwnd = int(window.winId())
        if not hwnd:
            raise OSError("MainWindow HWND is unavailable")
        event_filter = _ShutdownEventFilter(self)
        self._qt_app = qt_app
        self._window = window
        self._hwnd = hwnd
        self._message = int(message)
        self._filter = event_filter
        self._quit_scheduled = False
        try:
            qt_app.installNativeEventFilter(event_filter)
        except Exception:
            try:
                qt_app.removeNativeEventFilter(event_filter)
            except Exception:  # noqa: BLE001 - preserve original install failure
                _logger.error("Shutdown listener rollback failed stage=remove-filter")
            self._clear_state()
            raise

    def dispatch_message(
        self,
        *,
        event_type: bytes,
        hwnd: int,
        message: int,
        w_param: int,
        l_param: int,
    ) -> bool:
        if (
            self._filter is None
            or event_type not in _WINDOWS_EVENT_TYPES
            or hwnd != self._hwnd
            or message != self._message
            or w_param != 0
            or l_param != 0
            or self._quit_scheduled
        ):
            return False
        self._quit_scheduled = True
        window = self._window
        if window is not None:
            QTimer.singleShot(0, window.quit_application)
        return True

    def stop(self) -> None:
        event_filter = self._filter
        qt_app = self._qt_app
        self._clear_state()
        if event_filter is not None and qt_app is not None:
            qt_app.removeNativeEventFilter(event_filter)

    def _clear_state(self) -> None:
        self._filter = None
        self._qt_app = None
        self._window = None
        self._hwnd = 0
        self._message = 0
        self._quit_scheduled = False
