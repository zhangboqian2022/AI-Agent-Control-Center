from __future__ import annotations

import threading
import types

import pytest

from aacc.automation import AutomationError
from aacc.automation_windows import WIN_KEY_CODES, WindowsAutomation
from aacc.models import AppConfig, TaskConfig, TerminalConfig


class FakeWin32:
    VK_CONTROL = 0x11
    VK_LWIN = 0x5B

    def __init__(self) -> None:
        self.windows: dict[str, int] = {}
        self.focused: list[int] = []
        self.combos: list[tuple[int, tuple[int, ...]]] = []
        self.texts: list[str] = []

    def find_window_by_title(self, substring: str) -> int | None:
        for title, hwnd in self.windows.items():
            if substring.lower() in title.lower():
                return hwnd
        return None

    def focus_window(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return True

    def send_key_combo(self, vk: int, modifiers: tuple[int, ...] = ()) -> None:
        self.combos.append((vk, modifiers))

    def send_unicode_text(self, text: str) -> None:
        self.texts.append(text)


def _task(title: str | None = "myproject") -> TaskConfig:
    return TaskConfig(
        id="t1",
        slot=1,
        name="myproject",
        terminal=TerminalConfig(type="windows_terminal", window_title=title),
    )


def _automation(win32: FakeWin32) -> WindowsAutomation:
    return WindowsAutomation(AppConfig(), win32_module=win32, sleeper=lambda _: None)


def test_focus_uses_window_title() -> None:
    win32 = FakeWin32()
    win32.windows["user@host: C:\\dev\\myproject"] = 7
    assert _automation(win32).focus(_task()) == "已聚焦 myproject"
    assert win32.focused == [7]


def test_focus_falls_back_to_task_name() -> None:
    win32 = FakeWin32()
    win32.windows["myproject - Windows Terminal"] = 9
    assert _automation(win32).focus(_task(title=None)) == "已聚焦 myproject"
    assert win32.focused == [9]


def test_focus_missing_window_raises() -> None:
    win32 = FakeWin32()
    with pytest.raises(AutomationError):
        _automation(win32).focus(_task())


def test_send_key_enter_focuses_then_sends() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    assert _automation(win32).send_key(_task(), "ENTER") == "已发送 ENTER"
    assert win32.focused == [3]
    assert win32.combos == [(WIN_KEY_CODES["ENTER"], ())]


def test_send_key_ctrl_c_uses_control_modifier() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    _automation(win32).send_key(_task(), "CTRL_C")
    assert win32.combos == [(0x43, (0x11,))]


def test_send_key_rejects_unknown_key() -> None:
    win32 = FakeWin32()
    with pytest.raises(AutomationError):
        _automation(win32).send_key(_task(), "F24")


def test_send_text_unicode() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    assert _automation(win32).send_text(_task(), "你好 world") == "文本已发送"
    assert win32.texts == ["你好 world"]


def test_send_text_validates_length_and_nul() -> None:
    win32 = FakeWin32()
    automation = _automation(win32)
    with pytest.raises(AutomationError):
        automation.send_text(_task(), "")
    with pytest.raises(AutomationError):
        automation.send_text(_task(), "x" * 2001)
    with pytest.raises(AutomationError):
        automation.send_text(_task(), "a\0b")


def test_injection_disabled_blocks_send() -> None:
    win32 = FakeWin32()
    config = AppConfig()
    config.app.keyboard_injection = False
    automation = WindowsAutomation(config, win32_module=win32, sleeper=lambda _: None)
    with pytest.raises(AutomationError):
        automation.send_key(_task(), "ENTER")


def test_start_voice_triggers_win_h() -> None:
    win32 = FakeWin32()
    win32.windows["myproject"] = 3
    assert _automation(win32).start_voice(_task()) == "已触发语音输入"
    assert win32.combos == [(0x48, (0x5B,))]


def test_cancel_event_aborts_before_focus() -> None:
    win32 = FakeWin32()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AutomationError):
        _automation(win32).focus(_task(), cancel_event=cancel)
    assert win32.focused == []


def test_factory_returns_windows_automation_on_win32(monkeypatch) -> None:
    import sys

    import aacc.automation as automation_mod

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "aacc.win32", types.SimpleNamespace())
    controller = automation_mod.create_automation(AppConfig())
    assert isinstance(controller, WindowsAutomation)


def test_factory_returns_mac_automation_on_darwin(monkeypatch) -> None:
    import sys

    import aacc.automation as automation_mod

    monkeypatch.setattr(sys, "platform", "darwin")
    controller = automation_mod.create_automation(AppConfig())
    assert isinstance(controller, automation_mod.MacAutomation)
