"""Process liveness checks with PID caching.

Full process-tree scans every poll are wasteful; once a matching process is
found, its PID is revalidated cheaply and the tree is only rescanned after
the cached PID dies or stops matching (including PID reuse).
"""

from __future__ import annotations

from collections.abc import Callable

import psutil


class CachedProcessAlive:
    """Callable liveness probe caching the last matching PID."""

    def __init__(self, attr: str, matches: Callable[[str], bool]) -> None:
        self._attr = attr
        self._matches = matches
        self._pid: int | None = None

    def __call__(self) -> bool:
        if self._pid is not None:
            try:
                value = getattr(psutil.Process(self._pid), self._attr)()
                if isinstance(value, str) and self._matches(value):
                    return True
            except (psutil.Error, OSError):
                pass
            self._pid = None
        try:
            for process in psutil.process_iter([self._attr]):
                value = process.info.get(self._attr)
                if isinstance(value, str) and self._matches(value):
                    self._pid = process.pid
                    return True
        except (psutil.Error, OSError):
            return False
        return False
