from __future__ import annotations

import ctypes
from dataclasses import dataclass

import pytest
from PySide6.QtCore import QCoreApplication

from aacc.hotkeys_windows import WindowsGlobalHotkeys
from aacc.shutdown_windows import (
    _MSG,
    AACC_WINDOW_TITLE,
    SHUTDOWN_MESSAGE_NAME,
    WindowsShutdownListener,
    _normalized_windows_image,
    request_shutdown_for_update,
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


class FakeWin32ShutdownApi:
    def __init__(
        self,
        *,
        windows: list[int] | None = None,
        pids: dict[int, int] | None = None,
        images: dict[int, str] | None = None,
    ) -> None:
        self.shutdown_message = 0xC042
        self.windows = [] if windows is None else windows
        self.pids = {} if pids is None else pids
        self.images = {} if images is None else images
        self.wait_results: dict[int, bool | BaseException] = {}
        self.pid_errors: dict[int, BaseException] = {}
        self.pid_sequences: dict[int, list[int]] = {}
        self.open_errors: dict[int, BaseException] = {}
        self.post_error: BaseException | None = None
        self.find_titles: list[str] = []
        self.opened: list[int] = []
        self.entered: list[int] = []
        self.queried: list[int] = []
        self.posted: list[tuple[int, int]] = []
        self.waited: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.pid_queries: list[int] = []

    def register_window_message(self, name: str) -> int:
        assert name == SHUTDOWN_MESSAGE_NAME
        return self.shutdown_message

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

    def post_message(self, hwnd: int, message: int) -> None:
        self.posted.append((hwnd, message))
        if self.post_error is not None:
            raise self.post_error


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
    assert api.posted == [(101, api.shutdown_message)]
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
    assert api.posted == [(101, api.shutdown_message)]
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
    assert api.posted == []
    assert api.closed == [200]


def test_shutdown_client_treats_natural_exit_during_post_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.post_error = ProcessLookupError()
    monkeypatch.setattr("aacc.shutdown_windows.sys.executable", r"C:\Program Files\AACC\AACC.exe")

    assert request_shutdown_for_update(win32_module=api) == 0
    assert api.waited == [(200, 20_000)]
    assert api.closed == [200]


def test_shutdown_client_post_failure_waits_full_timeout_then_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeWin32ShutdownApi(
        windows=[100],
        pids={100: 200},
        images={200: r"C:\Program Files\AACC\AACC.exe"},
    )
    api.post_error = OSError("post failed")
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
    assert api.posted == []
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
    assert api.posted == []


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
    def __init__(self, hwnd: int = 991) -> None:
        self.hwnd = hwnd
        self.quit_calls = 0

    def winId(self) -> int:
        return self.hwnd

    def quit_application(self) -> None:
        self.quit_calls += 1


class FakeQtApplication:
    def __init__(self) -> None:
        self.installed: list[object] = []
        self.removed: list[object] = []

    def installNativeEventFilter(self, event_filter: object) -> None:
        self.installed.append(event_filter)

    def removeNativeEventFilter(self, event_filter: object) -> None:
        self.removed.append(event_filter)


def test_shutdown_message_quits_through_window_once(qapp) -> None:
    window = FakeWindow()
    api = FakeWin32ShutdownApi()
    listener = WindowsShutdownListener(win32_module=api)
    listener.start(qapp, window)

    listener.dispatch_message(
        event_type=b"windows_generic_MSG",
        hwnd=window.hwnd,
        message=api.shutdown_message,
        w_param=0,
        l_param=0,
    )
    listener.dispatch_message(
        event_type=b"windows_generic_MSG",
        hwnd=window.hwnd,
        message=api.shutdown_message,
        w_param=0,
        l_param=0,
    )
    QCoreApplication.processEvents()

    assert window.quit_calls == 1


@pytest.mark.parametrize(
    ("event_type", "hwnd", "message", "w_param", "l_param"),
    [
        (b"other", 991, 0xC042, 0, 0),
        (b"windows_generic_MSG", 992, 0xC042, 0, 0),
        (b"windows_generic_MSG", 991, 0xC043, 0, 0),
        (b"windows_generic_MSG", 991, 0xC042, 1, 0),
        (b"windows_generic_MSG", 991, 0xC042, 0, 1),
    ],
)
def test_shutdown_listener_rejects_wrong_native_messages(
    qapp,
    event_type: bytes,
    hwnd: int,
    message: int,
    w_param: int,
    l_param: int,
) -> None:
    window = FakeWindow()
    listener = WindowsShutdownListener(win32_module=FakeWin32ShutdownApi())
    listener.start(qapp, window)

    assert not listener.dispatch_message(
        event_type=event_type,
        hwnd=hwnd,
        message=message,
        w_param=w_param,
        l_param=l_param,
    )
    QCoreApplication.processEvents()
    assert window.quit_calls == 0


def test_shutdown_listener_start_and_stop_are_idempotent() -> None:
    app = FakeQtApplication()
    listener = WindowsShutdownListener(win32_module=FakeWin32ShutdownApi())
    window = FakeWindow()

    listener.start(app, window)
    listener.start(app, window)
    listener.stop()
    listener.stop()

    assert len(app.installed) == 1
    assert app.removed == app.installed


def test_shutdown_listener_can_schedule_again_after_stop_and_restart(qapp) -> None:
    api = FakeWin32ShutdownApi()
    first_window = FakeWindow(991)
    second_window = FakeWindow(992)
    listener = WindowsShutdownListener(win32_module=api)

    listener.start(qapp, first_window)
    assert listener.dispatch_message(
        event_type=b"windows_generic_MSG",
        hwnd=991,
        message=api.shutdown_message,
        w_param=0,
        l_param=0,
    )
    QCoreApplication.processEvents()
    listener.stop()

    listener.start(qapp, second_window)
    assert listener.dispatch_message(
        event_type=b"windows_generic_MSG",
        hwnd=992,
        message=api.shutdown_message,
        w_param=0,
        l_param=0,
    )
    QCoreApplication.processEvents()

    assert first_window.quit_calls == 1
    assert second_window.quit_calls == 1


def test_shutdown_listener_rolls_back_partial_native_filter_install() -> None:
    class PartiallyFailingApplication(FakeQtApplication):
        def installNativeEventFilter(self, event_filter: object) -> None:
            super().installNativeEventFilter(event_filter)
            raise RuntimeError("install failed")

    app = PartiallyFailingApplication()
    listener = WindowsShutdownListener(win32_module=FakeWin32ShutdownApi())

    with pytest.raises(RuntimeError, match="install failed"):
        listener.start(app, FakeWindow())

    assert app.removed == app.installed
    assert listener._filter is None
    assert listener._qt_app is None
    assert listener._window is None
    assert listener._hwnd == 0
    assert listener._message == 0


def test_shutdown_listener_win_id_failure_does_not_install_filter() -> None:
    class FailingWindow(FakeWindow):
        def winId(self) -> int:
            raise RuntimeError("winId failed")

    app = FakeQtApplication()
    listener = WindowsShutdownListener(win32_module=FakeWin32ShutdownApi())

    with pytest.raises(RuntimeError, match="winId failed"):
        listener.start(app, FailingWindow())

    assert app.installed == []
    assert listener._filter is None


def test_shutdown_listener_rejects_zero_registered_message() -> None:
    api = FakeWin32ShutdownApi()
    api.shutdown_message = 0

    with pytest.raises(OSError, match="RegisterWindowMessage"):
        WindowsShutdownListener(win32_module=api).start(FakeQtApplication(), FakeWindow())


def test_native_filter_rejects_wrong_event_type_and_null_message_pointer(qapp) -> None:
    listener = WindowsShutdownListener(win32_module=FakeWin32ShutdownApi())
    listener.start(qapp, FakeWindow())
    assert listener._filter is not None

    assert listener._filter.nativeEventFilter(b"other", 1) is False
    assert listener._filter.nativeEventFilter(b"windows_generic_MSG", 0) is False


def test_native_filter_dispatches_registered_windows_message_and_returns_bool(qapp) -> None:
    window = FakeWindow()
    listener = WindowsShutdownListener(win32_module=FakeWin32ShutdownApi())
    listener.start(qapp, window)
    assert listener._filter is not None
    message = _MSG(
        hwnd=window.hwnd,
        message=0xC042,
        wParam=0,
        lParam=0,
    )

    result = listener._filter.nativeEventFilter(
        b"windows_generic_MSG",
        ctypes.addressof(message),
    )
    QCoreApplication.processEvents()

    assert result is False
    assert window.quit_calls == 1


def test_later_hotkey_filter_allows_shutdown_message_to_reach_shutdown_filter(qapp) -> None:
    class CombinedWin32(FakeWin32ShutdownApi):
        WM_HOTKEY = 0x0312

        def register_hotkey(self, _hwnd: int, _hotkey_id: int, _vk: int) -> bool:
            return True

        def unregister_hotkey(self, _hwnd: int, _hotkey_id: int) -> None:
            return None

    api = CombinedWin32()
    app = FakeQtApplication()
    window = FakeWindow()
    listener = WindowsShutdownListener(win32_module=api)
    listener.start(app, window)
    hotkeys = WindowsGlobalHotkeys({}, {}, hwnd=window.hwnd, win32_module=api, qt_app=app)
    assert hotkeys.start() is True
    message = _MSG(hwnd=window.hwnd, message=api.shutdown_message, wParam=0, lParam=0)

    for event_filter in reversed(app.installed):
        handled = event_filter.nativeEventFilter(
            b"windows_generic_MSG",
            ctypes.addressof(message),
        )
        assert handled is False
    QCoreApplication.processEvents()

    assert window.quit_calls == 1
