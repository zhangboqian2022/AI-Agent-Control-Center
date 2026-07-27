from __future__ import annotations

from dataclasses import dataclass

import pytest

from aacc.shutdown_windows import (
    AACC_WINDOW_TITLE,
    ADMINISTRATORS_SID,
    EVENT_ALL_ACCESS,
    SHUTDOWN_EVENT_PREFIX,
    SYSTEM_SID,
    WindowsShutdownEventApi,
    WindowsShutdownListener,
    _normalized_windows_image,
    request_shutdown_for_update,
    shutdown_event_name,
)
from aacc.win32 import Win32CallError


@dataclass
class FakeProcess:
    api: FakeWin32ShutdownApi
    pid: int
    image: str
    wait_result: bool | BaseException = True

    def __enter__(self) -> FakeProcess:
        self.api.entered.append(self.pid)
        return self

    def __exit__(self, *_args: object) -> None:
        self.api.closed.append(self.pid)

    def image_name(self) -> str:
        self.api.queried.append(self.pid)
        return self.image

    def wait_for_exit(self, timeout_ms: int) -> bool:
        self.api.waited.append((self.pid, timeout_ms))
        if isinstance(self.wait_result, BaseException):
            raise self.wait_result
        return self.wait_result


@dataclass
class FakeEventHandle:
    api: FakeWin32ShutdownApi
    name: str

    def __enter__(self) -> FakeEventHandle:
        self.api.entered_events.append(self.name)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self.name not in self.api.closed_events:
            self.api.closed_events.append(self.name)


class FakeWin32ShutdownApi:
    def __init__(
        self,
        *,
        windows: list[int] | None = None,
        pids: dict[int, int] | None = None,
        images: dict[int, str] | None = None,
    ) -> None:
        self.windows = [] if windows is None else windows
        self.pids = {} if pids is None else pids
        self.images = {} if images is None else images
        self.wait_results: dict[int, bool | BaseException] = {}
        self.pid_errors: dict[int, BaseException] = {}
        self.pid_sequences: dict[int, list[int]] = {}
        self.open_errors: dict[int, BaseException] = {}
        self.event_open_error: BaseException | None = None
        self.event_create_error: BaseException | None = None
        self.event_signal_error: BaseException | None = None
        self.event_wait_error: BaseException | None = None
        self.event_signaled = False
        self.event_preexisting = False
        self.find_titles: list[str] = []
        self.opened: list[int] = []
        self.entered: list[int] = []
        self.queried: list[int] = []
        self.waited: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.pid_queries: list[int] = []
        self.created_events: list[str] = []
        self.opened_events: list[str] = []
        self.entered_events: list[str] = []
        self.signaled_events: list[str] = []
        self.polled_events: list[str] = []
        self.closed_events: list[str] = []

    def find_exact_windows(self, title: str) -> tuple[int, ...]:
        self.find_titles.append(title)
        return tuple(self.windows)

    def window_process_id(self, hwnd: int) -> int:
        self.pid_queries.append(hwnd)
        if hwnd in self.pid_errors:
            raise self.pid_errors[hwnd]
        if hwnd in self.pid_sequences:
            return self.pid_sequences[hwnd].pop(0)
        return self.pids[hwnd]

    def open_verified_process(self, pid: int) -> FakeProcess:
        self.opened.append(pid)
        if pid in self.open_errors:
            raise self.open_errors[pid]
        return FakeProcess(self, pid, self.images[pid], self.wait_results.get(pid, True))

    def create_shutdown_event(self, name: str) -> FakeEventHandle:
        self.created_events.append(name)
        if self.event_create_error is not None:
            raise self.event_create_error
        if self.event_preexisting:
            raise OSError("shutdown event already exists")
        return FakeEventHandle(self, name)

    def open_shutdown_event(self, name: str) -> FakeEventHandle:
        self.opened_events.append(name)
        if self.event_open_error is not None:
            raise self.event_open_error
        return FakeEventHandle(self, name)

    def set_shutdown_event(self, handle: FakeEventHandle) -> None:
        self.signaled_events.append(handle.name)
        if self.event_signal_error is not None:
            raise self.event_signal_error
        self.event_signaled = True

    def is_shutdown_event_signaled(self, handle: FakeEventHandle) -> bool:
        self.polled_events.append(handle.name)
        if self.event_wait_error is not None:
            raise self.event_wait_error
        return self.event_signaled


def test_shutdown_client_returns_zero_when_no_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeWin32ShutdownApi()
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.find_titles == [AACC_WINDOW_TITLE]
    assert api.opened == []


def test_windows_image_normalization_equates_extended_unc_path() -> None:
    assert _normalized_windows_image(r"\\?\UNC\server\share\AACC.exe") == _normalized_windows_image(
        r"\\server\share\aacc.exe"
    )


@pytest.mark.parametrize("pid", [True, False, 0, -1, 0x1_0000_0000])
def test_shutdown_event_name_rejects_invalid_process_ids(pid: int) -> None:
    with pytest.raises(ValueError, match="pid"):
        shutdown_event_name(pid)


def test_shutdown_event_name_is_local_and_deterministic() -> None:
    assert shutdown_event_name(201) == f"{SHUTDOWN_EVENT_PREFIX}.201"


class FakeToken:
    def __init__(self) -> None:
        self.closed = False

    def Close(self) -> None:
        self.closed = True


class FakeSecurityAttributes:
    SECURITY_DESCRIPTOR: object
    bInheritHandle: bool


class FakeDacl:
    def __init__(self, aces: list[tuple[tuple[int, int], int, str]]) -> None:
        self.aces = aces

    def GetAceCount(self) -> int:
        return len(self.aces)

    def GetAce(self, index: int) -> tuple[tuple[int, int], int, str]:
        return self.aces[index]


class FakeSecurityDescriptor:
    def __init__(
        self,
        dacl: FakeDacl,
        *,
        protected: bool = True,
    ) -> None:
        self.dacl = dacl
        self.protected = protected

    def GetSecurityDescriptorControl(self) -> tuple[int, int]:
        return (0x1000 if self.protected else 0, 1)

    def GetSecurityDescriptorDacl(self) -> FakeDacl:
        return self.dacl


class FakeWin32ApiModule:
    def __init__(self) -> None:
        self.last_error = 0
        self.closed_handles: list[object] = []

    def GetCurrentProcess(self) -> str:
        return "process"

    def SetLastError(self, value: int) -> None:
        self.last_error = value

    def GetLastError(self) -> int:
        return self.last_error

    def CloseHandle(self, handle: object) -> None:
        self.closed_handles.append(handle)


class FakeWin32EventModule:
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258

    def __init__(self, api: FakeWin32ApiModule) -> None:
        self.api = api
        self.create_last_error = 0
        self.created: list[tuple[FakeSecurityAttributes, bool, bool, str]] = []

    def CreateEvent(
        self,
        attributes: FakeSecurityAttributes,
        manual_reset: bool,
        initial_state: bool,
        name: str,
    ) -> str:
        self.created.append((attributes, manual_reset, initial_state, name))
        self.api.last_error = self.create_last_error
        return "event-handle"


class FakeWin32SecurityModule:
    TokenUser = 1
    SDDL_REVISION_1 = 1
    SE_KERNEL_OBJECT = 6
    DACL_SECURITY_INFORMATION = 4
    SE_DACL_PROTECTED = 0x1000
    ACCESS_ALLOWED_ACE_TYPE = 0
    INHERITED_ACE = 0x10

    def __init__(self, *, dacl: FakeDacl | None = None, protected: bool = True) -> None:
        self.token = FakeToken()
        self.sddl_values: list[str] = []
        self.dacl = dacl or FakeDacl(
            [
                ((0, 0), EVENT_ALL_ACCESS, SYSTEM_SID),
                ((0, 0), EVENT_ALL_ACCESS, ADMINISTRATORS_SID),
                ((0, 0), EVENT_ALL_ACCESS, "S-1-5-21-1000"),
            ]
        )
        self.protected = protected

    def OpenProcessToken(self, _process: object, _access: int) -> FakeToken:
        return self.token

    def GetTokenInformation(self, _token: FakeToken, _kind: int) -> tuple[str]:
        return ("S-1-5-21-1000",)

    def ConvertSidToStringSid(self, sid: object) -> str:
        return str(sid)

    def ConvertStringSecurityDescriptorToSecurityDescriptor(
        self,
        sddl: str,
        _revision: int,
    ) -> str:
        self.sddl_values.append(sddl)
        return "security-descriptor"

    def SECURITY_ATTRIBUTES(self) -> FakeSecurityAttributes:
        return FakeSecurityAttributes()

    def GetSecurityInfo(
        self,
        _handle: object,
        _object_type: int,
        _information: int,
    ) -> FakeSecurityDescriptor:
        return FakeSecurityDescriptor(self.dacl, protected=self.protected)


def _fake_native_event_api(
    *,
    dacl: FakeDacl | None = None,
    protected: bool = True,
) -> tuple[
    WindowsShutdownEventApi,
    FakeWin32ApiModule,
    FakeWin32EventModule,
    FakeWin32SecurityModule,
]:
    api = object.__new__(WindowsShutdownEventApi)
    win32api = FakeWin32ApiModule()
    win32event = FakeWin32EventModule(win32api)
    win32security = FakeWin32SecurityModule(dacl=dacl, protected=protected)
    api._win32api = win32api
    api._win32con = type("FakeWin32Con", (), {"TOKEN_QUERY": 8})()
    api._win32event = win32event
    api._win32security = win32security
    return api, win32api, win32event, win32security


def test_native_event_api_creates_noninheritable_exact_protected_event() -> None:
    api, win32api, win32event, win32security = _fake_native_event_api()

    handle = api.create_shutdown_event(shutdown_event_name(201))

    attributes, manual_reset, initial_state, name = win32event.created[0]
    assert (manual_reset, initial_state, name) == (True, False, shutdown_event_name(201))
    assert attributes.SECURITY_DESCRIPTOR == "security-descriptor"
    assert attributes.bInheritHandle is False
    assert win32security.sddl_values == [
        "D:P"
        f"(A;;0x{EVENT_ALL_ACCESS:x};;;{SYSTEM_SID})"
        f"(A;;0x{EVENT_ALL_ACCESS:x};;;{ADMINISTRATORS_SID})"
        f"(A;;0x{EVENT_ALL_ACCESS:x};;;S-1-5-21-1000)"
    ]
    assert win32security.token.closed is True
    handle.close()
    assert win32api.closed_handles == ["event-handle"]


def test_native_event_api_rejects_name_squatting_and_closes_handle() -> None:
    api, win32api, win32event, _win32security = _fake_native_event_api()
    win32event.create_last_error = 183

    with pytest.raises(OSError, match="already exists"):
        api.create_shutdown_event(shutdown_event_name(201))

    assert win32api.closed_handles == ["event-handle"]


@pytest.mark.parametrize(
    ("dacl", "protected"),
    [
        (
            FakeDacl(
                [
                    ((0, 0), EVENT_ALL_ACCESS, SYSTEM_SID),
                    ((0, 0), EVENT_ALL_ACCESS, ADMINISTRATORS_SID),
                    ((0, 0), EVENT_ALL_ACCESS, "S-1-5-21-1000"),
                ]
            ),
            False,
        ),
        (
            FakeDacl(
                [
                    ((0, 0), EVENT_ALL_ACCESS, SYSTEM_SID),
                    ((0, 0), EVENT_ALL_ACCESS, ADMINISTRATORS_SID),
                    ((0, 0), EVENT_ALL_ACCESS - 1, "S-1-5-21-1000"),
                ]
            ),
            True,
        ),
    ],
)
def test_native_event_api_rejects_unprotected_or_inexact_dacl_and_closes(
    dacl: FakeDacl,
    protected: bool,
) -> None:
    api, win32api, _win32event, _win32security = _fake_native_event_api(
        dacl=dacl,
        protected=protected,
    )

    with pytest.raises(OSError, match="DACL"):
        api.create_shutdown_event(shutdown_event_name(201))

    assert win32api.closed_handles == ["event-handle"]


def test_shutdown_client_uses_hidden_exact_title_window_and_one_process_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[101],
        pids={101: 201},
        images={201: r"\\?\C:\Program Files\AACC\AACC.exe"},
    )
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"c:\program files\aacc\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.opened == [201]
    assert api.entered == [201]
    assert api.queried == [201]
    assert api.opened_events == [shutdown_event_name(201)]
    assert api.signaled_events == [shutdown_event_name(201)]
    assert api.entered_events == [shutdown_event_name(201)]
    assert api.closed_events == [shutdown_event_name(201)]
    assert api.waited == [(201, 20_000)]
    assert api.closed == [201]
    assert api.pid_queries == [101, 101]


def test_shutdown_client_skips_wrong_image_and_selects_later_hidden_real_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100, 101],
        pids={100: 200, 101: 201},
        images={
            200: r"C:\attacker\AACC.exe",
            201: r"C:\Program Files\AACC\AACC.exe",
        },
    )
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.signaled_events == [shutdown_event_name(201)]
    assert api.closed == [200, 201]


def test_shutdown_client_rejects_same_basename_from_different_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"D:\portable\AACC.exe"},
    )
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) != 0
    assert api.signaled_events == []
    assert api.closed == [200]


def test_shutdown_client_treats_natural_exit_during_signal_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.event_signal_error = ProcessLookupError()
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.waited == [(200, 20_000)]
    assert api.closed == [200]


def test_shutdown_client_signal_failure_waits_full_timeout_then_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.event_signal_error = OSError("signal failed")
    api.wait_results[200] = False
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) != 0
    assert api.waited == [(200, 20_000)]
    assert api.closed == [200]


def test_shutdown_client_rechecks_window_pid_before_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.pid_sequences[100] = [200, 300]
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) != 0
    assert api.pid_queries == [100, 100]
    assert api.signaled_events == []
    assert api.closed == [200]


@pytest.mark.parametrize(
    ("stage", "error_code"),
    [
        ("window", 1400),
        ("open", 87),
        ("open", 1168),
    ],
)
def test_shutdown_client_treats_disappeared_window_or_process_as_natural_exit(
    stage: str,
    error_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    error = Win32CallError("test", error_code)
    if stage == "window":
        api.pid_errors[100] = error
    else:
        api.open_errors[200] = error
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.signaled_events == []


def test_shutdown_client_does_not_hide_access_denied_as_natural_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.open_errors[200] = Win32CallError("OpenProcess", 5)
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) != 0


def test_shutdown_client_fails_closed_when_protected_event_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.event_open_error = PermissionError("access denied")
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) != 0
    assert api.signaled_events == []
    assert api.waited == []
    assert api.closed == [200]


@pytest.mark.parametrize("wait_result", [False, OSError("wait failed")])
def test_shutdown_client_returns_nonzero_for_timeout_or_wait_failure(
    wait_result: bool | BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.wait_results[200] = wait_result
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) != 0
    assert api.closed == [200]


@pytest.mark.parametrize("timeout", [True, False, -1, 120_001])
def test_shutdown_client_rejects_unsafe_timeout_values(timeout: int) -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        request_shutdown_for_update(timeout, win32_module=FakeWin32ShutdownApi())


class FakeWindow:
    def __init__(self) -> None:
        self.quit_calls = 0

    def quit_application(self) -> None:
        self.quit_calls += 1


class FakeQtApplication:
    def __init__(self) -> None:
        self.parented_timers: list[FakeTimer] = []


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)


class FakeTimer:
    def __init__(self, parent: FakeQtApplication) -> None:
        parent.parented_timers.append(self)
        self.timeout = FakeSignal()
        self.interval = 0
        self.started = False
        self.stop_calls = 0

    def setInterval(self, interval: int) -> None:
        self.interval = interval

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.stop_calls += 1

    def fire(self) -> None:
        for callback in self.timeout.callbacks:
            callback()  # type: ignore[operator]


def test_shutdown_event_quits_through_window_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aacc.shutdown_windows.os.getpid", lambda: 201)
    window = FakeWindow()
    api = FakeWin32ShutdownApi()
    app = FakeQtApplication()
    listener = WindowsShutdownListener(win32_module=api, timer_factory=FakeTimer)
    listener.start(app, window)
    api.event_signaled = True

    app.parented_timers[0].fire()
    app.parented_timers[0].fire()

    assert window.quit_calls == 1
    assert app.parented_timers[0].started is False


def test_shutdown_listener_start_and_stop_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aacc.shutdown_windows.os.getpid", lambda: 201)
    app = FakeQtApplication()
    api = FakeWin32ShutdownApi()
    listener = WindowsShutdownListener(win32_module=api, timer_factory=FakeTimer)
    window = FakeWindow()

    listener.start(app, window)
    listener.start(app, window)
    listener.stop()
    listener.stop()

    assert len(app.parented_timers) == 1
    assert app.parented_timers[0].stop_calls == 1
    assert api.created_events == [shutdown_event_name(201)]
    assert api.closed_events == [shutdown_event_name(201)]


def test_shutdown_listener_can_schedule_again_after_stop_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aacc.shutdown_windows.os.getpid", lambda: 201)
    api = FakeWin32ShutdownApi()
    first_window = FakeWindow()
    second_window = FakeWindow()
    app = FakeQtApplication()
    listener = WindowsShutdownListener(win32_module=api, timer_factory=FakeTimer)

    listener.start(app, first_window)
    api.event_signaled = True
    app.parented_timers[0].fire()
    listener.stop()

    api.event_signaled = False
    listener.start(app, second_window)
    api.event_signaled = True
    app.parented_timers[1].fire()

    assert first_window.quit_calls == 1
    assert second_window.quit_calls == 1


def test_shutdown_listener_name_squatting_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aacc.shutdown_windows.os.getpid", lambda: 201)
    app = FakeQtApplication()
    api = FakeWin32ShutdownApi()
    api.event_preexisting = True
    listener = WindowsShutdownListener(win32_module=api, timer_factory=FakeTimer)

    with pytest.raises(OSError, match="already exists"):
        listener.start(app, FakeWindow())

    assert app.parented_timers == []
    assert api.closed_events == []


def test_shutdown_listener_poll_error_stops_and_closes_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aacc.shutdown_windows.os.getpid", lambda: 201)
    app = FakeQtApplication()
    window = FakeWindow()
    api = FakeWin32ShutdownApi()
    listener = WindowsShutdownListener(win32_module=api, timer_factory=FakeTimer)
    listener.start(app, window)
    api.event_wait_error = OSError("wait failed")

    app.parented_timers[0].fire()

    assert app.parented_timers[0].started is False
    assert api.closed_events == [shutdown_event_name(201)]
    assert window.quit_calls == 1
