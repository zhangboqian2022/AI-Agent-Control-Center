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


@pytest.mark.skipif(sys.platform != "win32", reason="requires real user32")
def test_real_enum_windows_read_only_path_returns_valid_handle_or_none() -> None:
    handle = win32.find_window_by_title("")

    assert handle is None or handle > 0
