"""Thin ctypes wrappers over the Win32 APIs used by the Windows port.

All Windows-specific calls live behind this module so tests can substitute a
fake (``win32_module`` parameter / ``sys.modules`` stub) without a Windows
machine. The module itself imports everywhere (``user32`` is None off
Windows); every public function raises OSError via ``_require_user32`` when
no Win32 user32 is available, so pure logic stays testable on any platform.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, cast

# Assigned only inside the win32 branch so the module imports everywhere and
# mypy (targeting this host platform) never sees ctypes.windll.
user32: Any = None
if sys.platform == "win32":  # pragma: no cover - requires Windows
    user32 = ctypes.windll.user32

SW_RESTORE = 9
VK_CONTROL = 0x11
VK_LWIN = 0x5B
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


# WINFUNCTYPE (stdcall callbacks) exists only on Windows; CFUNCTYPE is a
# stand-in elsewhere so the module stays importable for off-Windows tests.
_WNDENUMPROC = cast(
    Callable[[Callable[[int, int], bool]], Any],
    getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    ),
)


def _require_user32() -> Any:
    if user32 is None:
        raise OSError("aacc.win32 requires Windows")
    return user32


def find_window_by_title(substring: str) -> int | None:
    """First visible top-level window whose title contains the substring."""
    u32 = _require_user32()
    needle = substring.casefold()
    found: list[int] = []

    @_WNDENUMPROC
    def _callback(hwnd: int, _lparam: int) -> bool:
        if u32.IsWindowVisible(hwnd):
            length = u32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                u32.GetWindowTextW(hwnd, buffer, length + 1)
                if needle in buffer.value.casefold():
                    found.append(hwnd)
        return True

    u32.EnumWindows(_callback, 0)
    return found[0] if found else None


def focus_window(hwnd: int) -> bool:
    """Restore a minimized window and bring it to the foreground."""
    u32 = _require_user32()
    u32.ShowWindow(hwnd, SW_RESTORE)
    return bool(u32.SetForegroundWindow(hwnd))


def _vk_input(vk: int, *, key_up: bool = False) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if key_up else 0, 0, 0)
    return event


def _text_to_utf16_units(text: str) -> list[int]:
    """Split text into UTF-16 code units (surrogate pairs stay two units)."""
    data = text.encode("utf-16-le")
    return [
        int.from_bytes(data[offset : offset + 2], "little")
        for offset in range(0, len(data), 2)
    ]


def _char_input(unit: int, *, key_up: bool = False) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    event.union.ki = KEYBDINPUT(0, unit, flags, 0, 0)
    return event


def _send_input(events: list[INPUT]) -> None:
    u32 = _require_user32()
    array = (INPUT * len(events))(*events)
    sent = u32.SendInput(len(array), array, ctypes.sizeof(INPUT))
    if sent != len(array):
        raise OSError("SendInput failed")


def send_key_combo(vk: int, modifiers: tuple[int, ...] = ()) -> None:
    """Press modifiers+vk and release them in reverse order."""
    events = [_vk_input(modifier) for modifier in modifiers]
    events += [_vk_input(vk), _vk_input(vk, key_up=True)]
    events += [_vk_input(modifier, key_up=True) for modifier in reversed(modifiers)]
    _send_input(events)


def send_unicode_text(text: str) -> None:
    """Type text as Unicode keystrokes (layout-independent)."""
    events: list[INPUT] = []
    for unit in _text_to_utf16_units(text):
        events.append(_char_input(unit))
        events.append(_char_input(unit, key_up=True))
    _send_input(events)


def register_hotkey(hwnd: int, hotkey_id: int, vk: int) -> bool:
    u32 = _require_user32()
    return bool(u32.RegisterHotKey(hwnd, hotkey_id, MOD_NOREPEAT, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    u32 = _require_user32()
    u32.UnregisterHotKey(hwnd, hotkey_id)
