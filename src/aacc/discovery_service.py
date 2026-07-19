from __future__ import annotations

import threading

from aacc.codex_discovery import CodexLocalDiscovery, CodexSession
from aacc.task_manager import TaskManager


class CodexDiscoveryService:
    """Polls local Codex metadata outside the Qt event loop."""

    def __init__(
        self,
        manager: TaskManager,
        *,
        discovery: CodexLocalDiscovery | None = None,
        interval_seconds: float = 2.0,
    ) -> None:
        self.manager = manager
        self.discovery = discovery or CodexLocalDiscovery()
        self.interval_seconds = max(0.5, interval_seconds)
        self._manual_ids: set[str] = set()
        self._muted_ids: set[str] = set()
        self._auto_active_ids: set[str] = set()
        self._selection_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="aacc-codex-discovery", daemon=True)

    def poll_once(self) -> int:
        auto_active_ids = self.discovery.active_session_ids()
        with self._selection_lock:
            self._auto_active_ids = set(auto_active_ids)
            selected_ids = (self._manual_ids | self._auto_active_ids) - self._muted_ids
        tasks = self.discovery.discover(selected_ids)
        for task in tasks:
            self.manager.register(task.config, task.state)
        return len(tasks)

    def set_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_monitoring_preferences(selected_ids, set())

    def set_monitoring_preferences(self, manual_ids: set[str], muted_ids: set[str]) -> None:
        with self._selection_lock:
            self._manual_ids = set(manual_ids)
            self._muted_ids = set(muted_ids) - self._manual_ids

    def auto_active_ids(self) -> set[str]:
        with self._selection_lock:
            return set(self._auto_active_ids)

    def catalog(self) -> list[CodexSession]:
        return self.discovery.catalog()

    def start(self) -> None:
        self.poll_once()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds + 1)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.poll_once()
            except Exception:
                continue
