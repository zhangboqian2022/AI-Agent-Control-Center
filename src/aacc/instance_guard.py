from __future__ import annotations

import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import IO, Protocol, cast


class PosixFileLock(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, file: IO[str] | int, operation: int) -> None: ...


class WindowsFileLock(Protocol):
    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, nbytes: int) -> None: ...


def _posix_file_lock() -> PosixFileLock:
    return cast(PosixFileLock, import_module("fcntl"))


def _windows_file_lock() -> WindowsFileLock:
    return cast(WindowsFileLock, import_module("msvcrt"))


class InstanceGuard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        if sys.platform == "win32":
            if not self._lock_windows(handle):
                handle.close()
                return False
        else:
            if not self._lock_posix(handle):
                handle.close()
                return False
        self._handle = handle
        return True

    @staticmethod
    def _lock_posix(handle: IO[str]) -> bool:
        fcntl = _posix_file_lock()

        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    @staticmethod
    def _lock_windows(handle: IO[str]) -> bool:
        msvcrt = _windows_file_lock()

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                msvcrt = _windows_file_lock()
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = _posix_file_lock()
                fcntl.flock(self._handle, fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def activate_existing_instance() -> None:
    if sys.platform == "win32":
        try:
            from aacc import win32

            hwnd = win32.find_window_by_title("AACC")
            if hwnd is not None:
                win32.focus_window(hwnd)
        except Exception:
            return
        return
    try:
        subprocess.run(
            ["/usr/bin/open", "-b", "com.aacc.controlcenter"],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
