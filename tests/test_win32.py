"""Tests for the pure logic in aacc.win32 (importable off-Windows)."""

from __future__ import annotations

import sys

import pytest

from aacc import win32


def test_text_to_utf16_units_bmp() -> None:
    assert win32._text_to_utf16_units("aA中") == [ord("a"), ord("A"), ord("中")]


def test_text_to_utf16_units_surrogate_pair() -> None:
    assert win32._text_to_utf16_units("🙂") == [0xD83D, 0xDE42]


class _FakeUser32:
    def __init__(self) -> None:
        self.calls: list[list[win32.INPUT]] = []

    def SendInput(self, count: int, array: object, size: int) -> int:  # noqa: N802
        assert isinstance(array, list | tuple) or hasattr(array, "__getitem__")
        self.calls.append([array[i] for i in range(count)])
        return count


def test_send_unicode_text_surrogate_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeUser32()
    monkeypatch.setattr(win32, "user32", fake)
    win32.send_unicode_text("🙂")

    assert len(fake.calls) == 1
    events = fake.calls[0]
    assert len(events) == 4  # 2 UTF-16 units x (down + up)

    scans = [event.union.ki.wScan for event in events]
    assert scans == [0xD83D, 0xD83D, 0xDE42, 0xDE42]

    down_flags = win32.KEYEVENTF_UNICODE
    up_flags = win32.KEYEVENTF_UNICODE | win32.KEYEVENTF_KEYUP
    flags = [event.union.ki.dwFlags for event in events]
    assert flags == [down_flags, up_flags, down_flags, up_flags]

    assert all(event.type == win32.INPUT_KEYBOARD for event in events)


def test_send_unicode_text_bmp_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeUser32()
    monkeypatch.setattr(win32, "user32", fake)
    win32.send_unicode_text("ab")

    (events,) = fake.calls
    scans = [event.union.ki.wScan for event in events]
    assert scans == [ord("a"), ord("a"), ord("b"), ord("b")]


def test_find_window_by_title_requires_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win32, "user32", None)
    with pytest.raises(OSError, match="requires Windows"):
        win32.find_window_by_title("x")


class _FakeShutdownUser32:
    def __init__(self) -> None:
        self.titles = {10: "AI Agent Control Center", 11: "AI Agent Control Center", 12: "Other"}
        self.visible = {10: False, 11: True, 12: True}
        self.post_result = 1

    def EnumWindows(self, callback, lparam):  # noqa: N802
        for hwnd in self.titles:
            callback(hwnd, lparam)
        return 1

    def GetWindowTextLengthW(self, hwnd):  # noqa: N802
        return len(self.titles[hwnd])

    def GetWindowTextW(self, hwnd, buffer, _length):  # noqa: N802
        buffer.value = self.titles[hwnd]
        return len(buffer.value)

    def GetWindowThreadProcessId(self, _hwnd, pid_ptr):  # noqa: N802
        pid_ptr._obj.value = 77
        return 1

    def RegisterWindowMessageW(self, _name):  # noqa: N802
        return 0xC042

    def PostMessageW(self, _hwnd, _message, _wparam, _lparam):  # noqa: N802
        return self.post_result


def test_find_exact_windows_includes_hidden_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeShutdownUser32()
    monkeypatch.setattr(win32, "user32", fake)

    assert win32.find_exact_windows("AI Agent Control Center") == (10, 11)


def test_shutdown_user32_wrappers_validate_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeShutdownUser32()
    monkeypatch.setattr(win32, "user32", fake)

    assert win32.register_window_message("AACC.ShutdownForUpdate.v1") == 0xC042
    assert win32.window_process_id(10) == 77
    fake.post_result = 0
    with pytest.raises(OSError, match="PostMessage"):
        win32.post_message(10, 0xC042)


def test_win32_call_error_preserves_machine_readable_error_code() -> None:
    error = win32.Win32CallError("OpenProcess", 5)

    assert error.winerror_code == 5
    assert "OpenProcess failed" in str(error)


class _FakeKernel32:
    def __init__(self, *, wait_result: int = win32.WAIT_OBJECT_0) -> None:
        self.wait_result = wait_result
        self.queried: list[int] = []
        self.waited: list[tuple[int, int]] = []
        self.closed: list[int] = []

    def QueryFullProcessImageNameW(self, handle, _flags, buffer, length_ptr):  # noqa: N802
        self.queried.append(handle)
        buffer.value = r"C:\Program Files\AACC\AACC.exe"
        length_ptr._obj.value = len(buffer.value)
        return 1

    def WaitForSingleObject(self, handle, timeout):  # noqa: N802
        self.waited.append((handle, timeout))
        return self.wait_result

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


def test_verified_process_handle_queries_waits_and_closes_same_handle_once() -> None:
    kernel32 = _FakeKernel32()
    process = win32.VerifiedProcessHandle(1234, kernel32_module=kernel32)

    with process:
        assert process.image_name() == r"C:\Program Files\AACC\AACC.exe"
        assert process.wait_for_exit(20_000)
    process.close()

    assert kernel32.queried == [1234]
    assert kernel32.waited == [(1234, 20_000)]
    assert kernel32.closed == [1234]


def test_verified_process_handle_distinguishes_timeout_and_wait_failure() -> None:
    timed_out = win32.VerifiedProcessHandle(
        1, kernel32_module=_FakeKernel32(wait_result=win32.WAIT_TIMEOUT)
    )
    assert not timed_out.wait_for_exit(5)

    failed = win32.VerifiedProcessHandle(
        2, kernel32_module=_FakeKernel32(wait_result=win32.WAIT_FAILED)
    )
    with pytest.raises(OSError, match="WaitForSingleObject"):
        failed.wait_for_exit(5)


@pytest.mark.skipif(sys.platform != "win32", reason="requires real user32")
def test_real_enum_windows_read_only_path_returns_valid_handle_or_none() -> None:
    handle = win32.find_window_by_title("")

    assert handle is None or handle > 0
