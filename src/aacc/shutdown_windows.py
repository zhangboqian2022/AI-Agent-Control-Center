"""Authenticated, cooperative shutdown used by the Windows Setup package."""

from __future__ import annotations

import importlib
import logging
import ntpath
import os
import sys
from typing import Any, Protocol, cast

from PySide6.QtCore import QTimer

AACC_WINDOW_TITLE = "AI Agent Control Center"
SHUTDOWN_EVENT_PREFIX = r"Local\AACC.ShutdownForUpdate.v2"
MAX_SHUTDOWN_TIMEOUT_MS = 120_000
EVENT_ALL_ACCESS = 0x001F0003
EVENT_MODIFY_STATE = 0x0002
ERROR_ALREADY_EXISTS = 183
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
_NATURAL_EXIT_WINERRORS = {87, 1168, 1400}
_MISSING_EVENT_WINERRORS = {2}

_logger = logging.getLogger("aacc.shutdown")


class _ProcessHandle(Protocol):
    def __enter__(self) -> _ProcessHandle: ...

    def __exit__(self, *args: object) -> None: ...

    def image_name(self) -> str: ...

    def wait_for_exit(self, timeout_ms: int) -> bool: ...


class _ShutdownEventHandle(Protocol):
    def __enter__(self) -> _ShutdownEventHandle: ...

    def __exit__(self, *args: object) -> None: ...

    def close(self) -> None: ...


class _Win32ControlApi(Protocol):
    def find_exact_windows(self, title: str) -> tuple[int, ...]: ...

    def window_process_id(self, hwnd: int) -> int: ...

    def open_verified_process(self, pid: int) -> _ProcessHandle: ...


class _ShutdownEventApi(Protocol):
    def create_shutdown_event(self, name: str) -> _ShutdownEventHandle: ...

    def open_shutdown_event(self, name: str) -> _ShutdownEventHandle: ...

    def set_shutdown_event(self, handle: _ShutdownEventHandle) -> None: ...

    def is_shutdown_event_signaled(self, handle: _ShutdownEventHandle) -> bool: ...


class _PyWin32EventHandle:
    def __init__(self, handle: Any, close_handle: Any) -> None:
        self.raw = handle
        self._close_handle = close_handle
        self._closed = False

    def __enter__(self) -> _PyWin32EventHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_handle(self.raw)


class WindowsShutdownEventApi:
    """Small lazily loaded adapter for a protected per-process kernel event."""

    def __init__(self) -> None:
        self._win32api: Any = importlib.import_module("win32api")
        self._win32con: Any = importlib.import_module("win32con")
        self._win32event: Any = importlib.import_module("win32event")
        self._win32security: Any = importlib.import_module("win32security")

    def _current_user_sid_string(self) -> str:
        token = self._win32security.OpenProcessToken(
            self._win32api.GetCurrentProcess(),
            self._win32con.TOKEN_QUERY,
        )
        try:
            sid = self._win32security.GetTokenInformation(
                token,
                self._win32security.TokenUser,
            )[0]
            return str(self._win32security.ConvertSidToStringSid(sid))
        finally:
            close = getattr(token, "Close", None)
            if close is not None:
                close()

    def _security_attributes(self) -> Any:
        current_user_sid = self._current_user_sid_string()
        sddl = (
            f"O:{current_user_sid}D:P"
            f"(A;;0x{EVENT_ALL_ACCESS:x};;;{SYSTEM_SID})"
            f"(A;;0x{EVENT_ALL_ACCESS:x};;;{ADMINISTRATORS_SID})"
            f"(A;;0x{EVENT_ALL_ACCESS:x};;;{current_user_sid})"
        )
        descriptor = self._win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            sddl,
            self._win32security.SDDL_REVISION_1,
        )
        attributes = self._win32security.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        attributes.bInheritHandle = False
        return attributes

    def _wrap(self, handle: Any) -> _PyWin32EventHandle:
        return _PyWin32EventHandle(handle, self._win32api.CloseHandle)

    def create_shutdown_event(self, name: str) -> _PyWin32EventHandle:
        attributes = self._security_attributes()
        self._win32api.SetLastError(0)
        raw_handle = self._win32event.CreateEvent(attributes, True, False, name)
        handle = self._wrap(raw_handle)
        if self._win32api.GetLastError() == ERROR_ALREADY_EXISTS:
            handle.close()
            raise OSError("shutdown event already exists")
        try:
            self._verify_event_dacl(raw_handle)
        except Exception:
            handle.close()
            raise
        return handle

    def open_shutdown_event(self, name: str) -> _PyWin32EventHandle:
        raw_handle = self._win32event.OpenEvent(EVENT_MODIFY_STATE, False, name)
        return self._wrap(raw_handle)

    def set_shutdown_event(self, handle: _ShutdownEventHandle) -> None:
        self._win32event.SetEvent(cast(_PyWin32EventHandle, handle).raw)

    def is_shutdown_event_signaled(self, handle: _ShutdownEventHandle) -> bool:
        result = int(
            self._win32event.WaitForSingleObject(
                cast(_PyWin32EventHandle, handle).raw,
                0,
            )
        )
        if result == int(self._win32event.WAIT_OBJECT_0):
            return True
        if result == int(self._win32event.WAIT_TIMEOUT):
            return False
        raise OSError("unexpected shutdown event wait result")

    def _verify_event_dacl(self, raw_handle: Any) -> None:
        descriptor = self._win32security.GetSecurityInfo(
            raw_handle,
            self._win32security.SE_KERNEL_OBJECT,
            self._win32security.DACL_SECURITY_INFORMATION
            | self._win32security.OWNER_SECURITY_INFORMATION,
        )
        current_user_sid = self._current_user_sid_string()
        owner = descriptor.GetSecurityDescriptorOwner()
        owner_sid = str(self._win32security.ConvertSidToStringSid(owner))
        if owner_sid != current_user_sid:
            raise OSError("shutdown event owner verification failed")
        control, _revision = descriptor.GetSecurityDescriptorControl()
        if not control & self._win32security.SE_DACL_PROTECTED:
            raise OSError("shutdown event DACL is not protected")
        dacl = descriptor.GetSecurityDescriptorDacl()
        if dacl is None:
            raise OSError("shutdown event DACL is unavailable")

        expected = {
            SYSTEM_SID,
            ADMINISTRATORS_SID,
            current_user_sid,
        }
        seen: set[str] = set()
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            if len(ace) != 3:
                raise OSError("shutdown event DACL contains an unsupported ACE")
            (ace_type, flags), mask, sid = ace
            sid_string = str(self._win32security.ConvertSidToStringSid(sid))
            if (
                ace_type != self._win32security.ACCESS_ALLOWED_ACE_TYPE
                or flags & self._win32security.INHERITED_ACE
                or flags != 0
                or mask != EVENT_ALL_ACCESS
                or sid_string not in expected
                or sid_string in seen
            ):
                raise OSError("shutdown event DACL verification failed")
            seen.add(sid_string)
        if seen != expected:
            raise OSError("shutdown event DACL verification failed")


def shutdown_event_name(pid: int) -> str:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or pid > 0xFFFF_FFFF:
        raise ValueError("pid must be an integer from 1 to 4294967295")
    return f"{SHUTDOWN_EVENT_PREFIX}.{pid}"


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
    return _winerror_code(error) in _NATURAL_EXIT_WINERRORS


def _winerror_code(error: OSError) -> int | None:
    code = getattr(error, "winerror_code", None)
    if code is None:
        code = getattr(error, "winerror", None)
    if code is None:
        code = error.errno
    return code


def request_shutdown_for_update(
    timeout_ms: int = 20_000,
    win32_module: object | None = None,
) -> int:
    """Ask the installed AACC instance to quit, then wait on its verified handle."""
    if not _valid_timeout(timeout_ms):
        raise ValueError("timeout_ms must be an integer from 0 to 120000")
    if win32_module is None:
        from aacc import win32

        control_api = cast(_Win32ControlApi, win32)
        event_api = cast(_ShutdownEventApi, WindowsShutdownEventApi())
    else:
        control_api = cast(_Win32ControlApi, win32_module)
        event_api = cast(_ShutdownEventApi, win32_module)

    expected_image = _normalized_windows_image(sys.executable)
    try:
        windows = control_api.find_exact_windows(AACC_WINDOW_TITLE)
    except OSError:
        return _failed("discover")
    if not windows:
        return 0

    saw_candidate_failure = False
    for hwnd in windows:
        try:
            pid = control_api.window_process_id(hwnd)
        except OSError as error:
            if _is_natural_exit_error(error):
                continue
            saw_candidate_failure = True
            continue
        except KeyError:
            saw_candidate_failure = True
            continue
        try:
            process_context = control_api.open_verified_process(pid)
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
                    current_pid = control_api.window_process_id(hwnd)
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
                    event_context = event_api.open_shutdown_event(shutdown_event_name(pid))
                except OSError as error:
                    if _winerror_code(error) in _MISSING_EVENT_WINERRORS:
                        try:
                            if process.wait_for_exit(timeout_ms):
                                return 0
                        except OSError:
                            return _failed("open-event-wait")
                    return _failed("open-event")
                try:
                    with event_context as event:
                        event_api.set_shutdown_event(event)
                except (OSError, ProcessLookupError):
                    try:
                        if process.wait_for_exit(timeout_ms):
                            return 0
                    except OSError:
                        pass
                    return _failed("signal")
                try:
                    if process.wait_for_exit(timeout_ms):
                        return 0
                except OSError:
                    return _failed("wait")
                return _failed("timeout")
        except OSError:
            return _failed("close")
    return _failed("candidate") if saw_candidate_failure else 0


class WindowsShutdownListener:
    """Poll a protected per-process event from the Qt main thread."""

    def __init__(
        self,
        *,
        win32_module: Any | None = None,
        timer_factory: Any = QTimer,
    ) -> None:
        self._event_api: _ShutdownEventApi = cast(
            _ShutdownEventApi,
            WindowsShutdownEventApi() if win32_module is None else win32_module,
        )
        self._timer_factory = timer_factory
        self._qt_app: Any | None = None
        self._window: Any | None = None
        self._timer: Any | None = None
        self._event: _ShutdownEventHandle | None = None
        self._quit_scheduled = False
        self._poll_observed = False

    def start(self, qt_app: Any, window: Any) -> None:
        if self._event is not None or self._timer is not None:
            return
        event = self._event_api.create_shutdown_event(shutdown_event_name(os.getpid()))
        self._qt_app = qt_app
        self._window = window
        self._event = event
        self._quit_scheduled = False
        self._poll_observed = False
        try:
            timer = self._timer_factory(qt_app)
            self._timer = timer
            timer.setInterval(100)
            timer.timeout.connect(self._poll)
            timer.start()
        except Exception:
            self._release_resources()
            self._clear_state()
            raise
        _logger.info("Shutdown listener ready pid=%d", os.getpid())

    def _poll(self) -> None:
        event = self._event
        if event is None or self._quit_scheduled:
            return
        if not self._poll_observed:
            self._poll_observed = True
            _logger.info("Shutdown listener polling active")
        try:
            signaled = self._event_api.is_shutdown_event_signaled(event)
        except Exception:  # noqa: BLE001 - a broken listener must fail safe
            _logger.error("Shutdown listener failed stage=poll")
            self._request_quit()
            return
        if signaled:
            _logger.info("Shutdown listener received update signal")
            self._request_quit()

    def _request_quit(self) -> None:
        if self._quit_scheduled:
            return
        self._quit_scheduled = True
        window = self._window
        self._qt_app = None
        self._window = None
        self._release_timer()
        if window is not None:
            window.quit_application()

    def stop(self) -> None:
        self._release_resources()
        self._clear_state()

    def _release_resources(self) -> None:
        self._release_timer()
        self._release_event()

    def _release_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            operations = (
                ("timer-stop", timer.stop),
                ("timer-disconnect", lambda: timer.timeout.disconnect(self._poll)),
                ("timer-delete", timer.deleteLater),
            )
            for stage, operation in operations:
                try:
                    operation()
                except Exception:  # noqa: BLE001 - all cleanup paths must run
                    _logger.error("Shutdown listener cleanup failed stage=%s", stage)

    def _release_event(self) -> None:
        event = self._event
        self._event = None
        if event is not None:
            try:
                event.close()
            except Exception:  # noqa: BLE001 - timer cleanup and quit must still run
                _logger.error("Shutdown listener cleanup failed stage=event-close")

    def _clear_state(self) -> None:
        self._qt_app = None
        self._window = None
        self._timer = None
        self._event = None
        self._quit_scheduled = False
        self._poll_observed = False
