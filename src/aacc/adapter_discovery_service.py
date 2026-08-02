from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

import psutil

from aacc.adapters import AdapterRegistry, GenericCLIAdapter
from aacc.models import AppConfig, TaskConfig, TaskStatus
from aacc.task_manager import TaskManager

_logger = logging.getLogger("aacc.adapter-discovery")

# Native stores have dedicated discovery services with stronger evidence than a
# process-name adapter.  They must never be updated by this fallback service.
_NATIVE_AGENT_TYPES = frozenset(
    {"codex_cli", "codex_app", "kimi_code", "kimi_desktop", "opencode_cli"}
)
_ACTIVE = frozenset(
    {
        TaskStatus.STARTING,
        TaskStatus.THINKING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING_INPUT,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.WARNING,
        TaskStatus.PAUSED,
    }
)
AdapterFactory = Callable[[TaskConfig], GenericCLIAdapter]


class AdapterDiscoveryService:
    """Poll configured non-native adapters using conservative process evidence.

    Adapters are intentionally limited to process-level state here.  No output
    text is read and a process exit is reported as STOPPED, never as COMPLETED.
    Native Codex/Kimi/OpenCode tasks use their dedicated metadata services.
    """

    def __init__(
        self,
        manager: TaskManager,
        *,
        config: AppConfig,
        interval_seconds: float = 5.0,
        adapter_factory: AdapterFactory = AdapterRegistry.create,
    ) -> None:
        self.manager = manager
        self.interval_seconds = max(0.5, interval_seconds)
        self._adapters = tuple(
            (task, adapter_factory(task))
            for task in config.tasks
            if task.agent.type not in _NATIVE_AGENT_TYPES
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="aacc-adapter-discovery",
            daemon=True,
        )

    def poll_once(self) -> int:
        changed = 0
        try:
            processes: list[psutil.Process] | None = list(psutil.process_iter(["name", "cmdline"]))
        except (psutil.Error, OSError):
            processes = None
        for task, adapter in self._adapters:
            try:
                candidate = asyncio.run(adapter.get_status(processes))
            except Exception:  # noqa: BLE001 - one failing adapter must not stop the round
                _logger.warning(
                    "Adapter poll failed for task %s (%s)",
                    task.id,
                    adapter.display_name,
                    exc_info=True,
                )
                continue
            current = self.manager.get(task.id)
            if candidate.status is TaskStatus.STOPPED and current.status not in _ACTIVE:
                continue
            updated = self.manager.register(task, candidate)
            if updated.status is not current.status or updated.updated_at > current.updated_at:
                changed += 1
        return changed

    def start(self) -> None:
        self.poll_safely()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds + 1)

    def poll_safely(self) -> int:
        try:
            return self.poll_once()
        except Exception:  # noqa: BLE001 - a third-party process must not stop AACC
            _logger.warning("Configured adapter discovery poll failed", exc_info=True)
            return 0

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.poll_safely()
