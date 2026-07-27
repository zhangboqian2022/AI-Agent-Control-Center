"""Windows global hotkeys via RegisterHotKey + a Qt native event filter.

Public surface mirrors :class:`aacc.hotkeys.GlobalHotkeys`
(``start``/``stop``/``available``/``error``) so the existing
``AccessibilityHotkeySync`` drives it unchanged. Win32 calls are injectable
for tests; the Qt message filter needs a running QApplication.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray

WINDOWS_HOTKEY_VK = {
    "F13": 0x7C,
    "F14": 0x7D,
    "F15": 0x7E,
    "F16": 0x7F,
    "F17": 0x80,
    "F18": 0x81,
    "F19": 0x82,
    "F20": 0x83,
}


def windows_hotkey_vk(name: str) -> int:
    normalized = name.strip().upper()
    try:
        return WINDOWS_HOTKEY_VK[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported global hotkey: {name}") from error


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: WindowsGlobalHotkeys) -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(
        self,
        _event_type: QByteArray | bytes | bytearray | memoryview[int],
        message: int,
    ) -> bool:
        if not message:
            return False
        msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        if msg.message == self._owner._win32.WM_HOTKEY:
            self._owner.dispatch_message(msg)
        return False


class WindowsGlobalHotkeys:
    def __init__(
        self,
        bindings: dict[str, str],
        actions: dict[str, Callable[[], None]],
        *,
        hwnd: int = 0,
        win32_module: Any | None = None,
        qt_app: Any | None = None,
    ) -> None:
        if win32_module is not None:
            self._win32 = win32_module
        else:
            from aacc import win32

            self._win32 = win32
        self._hwnd = hwnd
        self._qt_app = qt_app
        self._actions_by_id = {
            index: actions[action]
            for index, (action, hotkey) in enumerate(
                ((a, h) for a, h in bindings.items() if a in actions), start=1
            )
        }
        self._vk_by_id = {
            index: windows_hotkey_vk(hotkey)
            for index, (action, hotkey) in enumerate(
                ((a, h) for a, h in bindings.items() if a in actions), start=1
            )
        }
        self._filter: _HotkeyEventFilter | None = None
        self.error: str | None = None
        self._running = False

    @property
    def available(self) -> bool:
        return self.error is None and self._running

    def start(self) -> bool:
        if self._running:
            return self.error is None
        self.error = None
        app = self._qt_app
        if app is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
        if app is None:
            self.error = "QApplication is required for global hotkeys"
            return False
        registered: list[int] = []
        for hotkey_id, vk in self._vk_by_id.items():
            if not self._win32.register_hotkey(self._hwnd, hotkey_id, vk):
                for registered_id in registered:
                    self._win32.unregister_hotkey(self._hwnd, registered_id)
                self.error = f"RegisterHotKey failed for hotkey id {hotkey_id}"
                logging.getLogger("aacc.hotkeys").warning(
                    "Global hotkeys unavailable: %s", self.error
                )
                return False
            registered.append(hotkey_id)
        self._filter = _HotkeyEventFilter(self)
        app.installNativeEventFilter(self._filter)
        self._running = True
        return True

    def make_hotkey_message(self, *, hwnd: int, hotkey_id: int) -> _MSG:
        """Build the MSG a WM_HOTKEY delivery would carry (test hook)."""
        return _MSG(hwnd, self._win32.WM_HOTKEY, hotkey_id, 0, 0, wintypes.POINT(0, 0))

    def dispatch_message(self, msg: _MSG) -> None:
        action = self._actions_by_id.get(msg.wParam)
        if action is not None:
            action()

    def stop(self) -> None:
        app = self._qt_app
        if app is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
        if self._filter is not None and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None
        for hotkey_id in self._vk_by_id:
            self._win32.unregister_hotkey(self._hwnd, hotkey_id)
        self._running = False
