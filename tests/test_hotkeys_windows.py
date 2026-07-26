from __future__ import annotations

import pytest

from aacc.hotkeys_windows import WINDOWS_HOTKEY_VK, WindowsGlobalHotkeys, windows_hotkey_vk


class FakeWin32:
    WM_HOTKEY = 0x0312

    def __init__(self) -> None:
        self.registered: list[tuple[int, int, int]] = []
        self.unregistered: list[tuple[int, int]] = []

    def register_hotkey(self, hwnd: int, hotkey_id: int, vk: int) -> bool:
        self.registered.append((hwnd, hotkey_id, vk))
        return True

    def unregister_hotkey(self, hwnd: int, hotkey_id: int) -> None:
        self.unregistered.append((hwnd, hotkey_id))


def test_vk_table_matches_function_keys() -> None:
    assert WINDOWS_HOTKEY_VK["F13"] == 0x7C
    assert windows_hotkey_vk("F13") == 0x7C
    assert windows_hotkey_vk(" f20 ") == 0x83
    with pytest.raises(ValueError):
        windows_hotkey_vk("F1")


def test_start_registers_hotkeys_and_dispatches(qapp) -> None:
    win32 = FakeWin32()
    fired: list[str] = []
    hotkeys = WindowsGlobalHotkeys(
        {"toggle": "F13"},
        {"toggle": lambda: fired.append("toggle")},
        hwnd=99,
        win32_module=win32,
        qt_app=qapp,
    )
    assert hotkeys.start() is True
    assert hotkeys.available
    assert win32.registered == [(99, 1, 0x7C)]

    # 模拟一条 WM_HOTKEY 消息送达过滤器
    msg = hotkeys.make_hotkey_message(hwnd=99, hotkey_id=1)
    hotkeys.dispatch_message(msg)
    assert fired == ["toggle"]

    hotkeys.stop()
    assert win32.unregistered == [(99, 1)]
    assert not hotkeys.available


def test_start_failure_sets_error(qapp) -> None:
    win32 = FakeWin32()
    win32.register_hotkey = lambda hwnd, hotkey_id, vk: False  # type: ignore[method-assign]
    hotkeys = WindowsGlobalHotkeys(
        {"toggle": "F13"}, {"toggle": lambda: None}, win32_module=win32, qt_app=qapp
    )
    assert hotkeys.start() is False
    assert hotkeys.error is not None
    assert not hotkeys.available


def test_start_rolls_back_registered_hotkeys_on_failure(qapp) -> None:
    win32 = FakeWin32()
    win32.register_hotkey = lambda hwnd, hotkey_id, vk: vk != 0x7D  # type: ignore[method-assign]
    hotkeys = WindowsGlobalHotkeys(
        {"a": "F13", "b": "F14"},
        {"a": lambda: None, "b": lambda: None},
        hwnd=99,
        win32_module=win32,
        qt_app=qapp,
    )
    assert hotkeys.start() is False
    assert hotkeys.error is not None
    assert not hotkeys.available
    # 0x7C（F13，id 1）注册成功后必须被回滚注销，不能泄漏系统级热键占用
    assert (99, 1) in win32.unregistered


def test_start_is_idempotent_while_running(qapp) -> None:
    win32 = FakeWin32()
    hotkeys = WindowsGlobalHotkeys(
        {"toggle": "F13"},
        {"toggle": lambda: None},
        hwnd=99,
        win32_module=win32,
        qt_app=qapp,
    )
    assert hotkeys.start() is True
    assert hotkeys.start() is True
    assert len(win32.registered) == 1
    hotkeys.stop()
