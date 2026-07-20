from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path
from typing import IO


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
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def activate_existing_instance() -> None:
    try:
        subprocess.run(
            ["/usr/bin/open", "-b", "com.aacc.controlcenter"],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
