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

# Assigned only inside the win32 branch so the module imports everywhere.
# WinDLL(use_last_error=True) gives every wrapper a reliable GetLastError value.
user32: Any = None
kernel32: Any = None
if sys.platform == "win32":  # pragma: no cover - requires Windows
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_RESTORE = 9
VK_CONTROL = 0x11
VK_LWIN = 0x5B
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
MAX_PROCESS_IMAGE_PATH = 32_768


class Win32CallError(OSError):
    """Win32 wrapper failure with a stable numeric code for safe decisions."""

    def __init__(self, operation: str, winerror_code: int) -> None:
        super().__init__(winerror_code, f"{operation} failed")
        self.winerror_code = winerror_code


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
    getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
)


def _require_user32() -> Any:
    if user32 is None:
        raise OSError("aacc.win32 requires Windows")
    return user32


def _require_kernel32() -> Any:
    if kernel32 is None:
        raise OSError("aacc.win32 requires Windows")
    return kernel32


def _raise_winerror(operation: str) -> None:
    error_code = getattr(ctypes, "get_last_error", lambda: 0)()
    raise Win32CallError(operation, error_code)


if user32 is not None:  # pragma: no cover - requires Windows
    user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL

if kernel32 is not None:  # pragma: no cover - requires Windows
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


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


def find_exact_windows(title: str) -> tuple[int, ...]:
    """Enumerate every top-level window with an exact title, including hidden ones."""
    u32 = _require_user32()
    found: list[int] = []

    @_WNDENUMPROC
    def _callback(hwnd: int, _lparam: int) -> bool:
        length = u32.GetWindowTextLengthW(hwnd)
        if length == len(title):
            buffer = ctypes.create_unicode_buffer(length + 1)
            u32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                found.append(int(hwnd))
        return True

    if not u32.EnumWindows(_callback, 0):
        _raise_winerror("EnumWindows")
    return tuple(found)


def register_window_message(name: str) -> int:
    u32 = _require_user32()
    message = int(u32.RegisterWindowMessageW(name))
    if message == 0:
        _raise_winerror("RegisterWindowMessageW")
    return message


def window_process_id(hwnd: int) -> int:
    u32 = _require_user32()
    pid = wintypes.DWORD()
    thread_id = u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not thread_id or not pid.value:
        _raise_winerror("GetWindowThreadProcessId")
    return int(pid.value)


def post_message(hwnd: int, message: int) -> None:
    u32 = _require_user32()
    if not u32.PostMessageW(hwnd, message, 0, 0):
        _raise_winerror("PostMessageW")


class VerifiedProcessHandle:
    """One stable process object used for image verification and exit waiting."""

    def __init__(self, handle: int, *, kernel32_module: Any | None = None) -> None:
        self._kernel32 = _require_kernel32() if kernel32_module is None else kernel32_module
        self._handle = handle
        self._closed = False

    def __enter__(self) -> VerifiedProcessHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def image_name(self) -> str:
        buffer = ctypes.create_unicode_buffer(MAX_PROCESS_IMAGE_PATH)
        length = wintypes.DWORD(len(buffer))
        if not self._kernel32.QueryFullProcessImageNameW(
            self._handle, 0, buffer, ctypes.byref(length)
        ):
            _raise_winerror("QueryFullProcessImageNameW")
        return buffer.value[: length.value]

    def wait_for_exit(self, timeout_ms: int) -> bool:
        result = int(self._kernel32.WaitForSingleObject(self._handle, timeout_ms))
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            _raise_winerror("WaitForSingleObject")
        raise OSError(f"WaitForSingleObject returned unexpected status {result}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._kernel32.CloseHandle(self._handle):
            _raise_winerror("CloseHandle")


def open_verified_process(pid: int) -> VerifiedProcessHandle:
    k32 = _require_kernel32()
    access = PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    handle = k32.OpenProcess(access, False, pid)
    if not handle:
        _raise_winerror("OpenProcess")
    return VerifiedProcessHandle(int(handle), kernel32_module=k32)


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
        int.from_bytes(data[offset : offset + 2], "little") for offset in range(0, len(data), 2)
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
