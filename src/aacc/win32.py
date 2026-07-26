"""Thin ctypes wrappers over the Win32 APIs used by the Windows port.

All Windows-specific calls live behind this module so tests can substitute a
fake (``win32_module`` parameter / ``sys.modules`` stub) without a Windows
machine. Importing on non-Windows raises ImportError; consumers import it
lazily so the rest of the package stays importable everywhere.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - import guard
    raise ImportError("aacc.win32 is only importable on Windows")

user32 = ctypes.windll.user32

SW_RESTORE = 9
VK_CONTROL = 0x11
VK_LWIN = 0x5B
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


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


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window_by_title(substring: str) -> int | None:
    """First visible top-level window whose title contains the substring."""
    needle = substring.casefold()
    found: list[int] = []

    @_WNDENUMPROC
    def _callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if needle in buffer.value.casefold():
                    found.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return found[0] if found else None


def focus_window(hwnd: int) -> bool:
    """Restore a minimized window and bring it to the foreground."""
    user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


def _vk_input(vk: int, *, key_up: bool = False) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if key_up else 0, 0, 0)
    return event


def _char_input(char: str, *, key_up: bool = False) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    event.union.ki = KEYBDINPUT(0, ord(char), flags, 0, 0)
    return event


def _send_input(events: list[INPUT]) -> None:
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(array), array, ctypes.sizeof(INPUT))
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
    for char in text:
        events.append(_char_input(char))
        events.append(_char_input(char, key_up=True))
    _send_input(events)
