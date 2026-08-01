"""Windows desktop automation: window focus, key/text injection, voice typing.

Mirrors :class:`aacc.automation.MacAutomation` through the shared
``AutomationController`` protocol. All Win32 calls go through ``aacc.win32``
(injectable as ``win32_module`` for tests). Window focus matches window
titles, since Windows has no bundle-identifier concept.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from aacc.automation import AutomationError
from aacc.models import AppConfig, TaskConfig

WIN_KEY_CODES = {
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "1": 0x31,
    "2": 0x32,
}


class WindowsAutomation:
    def __init__(
        self,
        config: AppConfig,
        *,
        win32_module: Any | None = None,
        sleeper: Callable[[float], Any] = time.sleep,
        accessibility_trusted: Callable[[], bool] = lambda: True,
    ) -> None:
        self.config = config
        if win32_module is not None:
            self._win32 = win32_module
        else:
            from aacc import win32

            self._win32 = win32
        self._sleep = sleeper
        self._accessibility_trusted = accessibility_trusted
        self._lock = threading.RLock()

    @staticmethod
    def _check_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AutomationError(
                "Desktop automation was cancelled",
                category="cancelled",
            )

    def _focus_target_unlocked(self, task: TaskConfig) -> int:
        title = task.terminal.window_title or task.terminal.tab_title or task.name
        hwnd = self._win32.find_window_by_title(title)
        if hwnd is None:
            raise AutomationError(
                f"未找到唯一标题包含 {title!r} 的窗口",
                category="window_not_found",
            )
        if not self._win32.focus_window(hwnd):
            raise AutomationError(
                "无法将目标窗口置前",
                category="window_focus_failed",
            )
        return int(hwnd)

    def _focus_unlocked(self, task: TaskConfig) -> str:
        self._focus_target_unlocked(task)
        return f"已聚焦 {task.name}"

    def _verify_foreground_unlocked(self, hwnd: int) -> None:
        if self._win32.foreground_window() != hwnd:
            raise AutomationError(
                "目标窗口焦点在注入前已改变",
                category="window_focus_failed",
            )

    def focus(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            return self._focus_unlocked(task)

    def _ensure_injection(self) -> None:
        if not self.config.app.keyboard_injection:
            raise AutomationError(
                "Keyboard injection is disabled in AACC settings",
                category="injection_disabled",
            )
        if not self._accessibility_trusted():
            raise AutomationError(
                "Accessibility permission is required",
                category="accessibility_required",
            )

    def send_key(
        self,
        task: TaskConfig,
        key: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            normalized = key.upper()
            if normalized not in {*WIN_KEY_CODES, "CTRL_C"}:
                raise AutomationError(
                    f"Key {normalized} is not allowed",
                    category="key_not_allowed",
                )
            hwnd = self._focus_target_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._verify_foreground_unlocked(hwnd)
            if normalized == "CTRL_C":
                self._win32.send_key_combo(0x43, (self._win32.VK_CONTROL,))
            else:
                self._win32.send_key_combo(WIN_KEY_CODES[normalized])
            return f"已发送 {normalized}"

    def send_text(
        self,
        task: TaskConfig,
        text: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            if not text or len(text) > 2000:
                raise AutomationError(
                    "Text must contain 1 to 2000 characters",
                    category="text_invalid",
                )
            if "\0" in text:
                raise AutomationError(
                    "Text must not contain NUL",
                    category="text_nul",
                )
            hwnd = self._focus_target_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._verify_foreground_unlocked(hwnd)
            self._win32.send_unicode_text(text)
            return "文本已发送"

    def start_voice(self, task: TaskConfig, *, cancel_event: threading.Event | None = None) -> str:
        with self._lock:
            self._check_cancelled(cancel_event)
            self._ensure_injection()
            hwnd = self._focus_target_unlocked(task)
            self._sleep(self.config.voice.focus_delay_ms / 1000)
            self._sleep(self.config.voice.voice_delay_ms / 1000)
            self._check_cancelled(cancel_event)
            self._verify_foreground_unlocked(hwnd)
            # Windows 语音输入（Win+H），对应 macOS 的系统听写（双击 Fn）。
            self._win32.send_key_combo(0x48, (self._win32.VK_LWIN,))
            return "已触发语音输入"
