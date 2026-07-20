from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from aacc.codex_discovery import CodexDiscoveryError, CodexLocalDiscovery, CodexSession
from aacc.security import redact
from aacc.task_manager import TaskManager


@dataclass(frozen=True)
class DiscoveryHealth:
    degraded: bool = False
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    diagnostic_id: str = ""
    summary: str = ""
    exception_class: str = ""
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None

    def diagnostics(self, log_path: str) -> str:
        failure = self.last_failure_at.isoformat() if self.last_failure_at else "never"
        success = self.last_success_at.isoformat() if self.last_success_at else "never"
        return "\n".join(
            (
                "AACC Codex discovery diagnostics",
                f"Diagnostic ID: {self.diagnostic_id or 'none'}",
                f"Degraded: {self.degraded}",
                f"Consecutive failures: {self.consecutive_failures}",
                f"Consecutive successes: {self.consecutive_successes}",
                f"Last failure: {failure}",
                f"Last success: {success}",
                f"Error: {self.exception_class}: {self.summary}".rstrip(": "),
                f"Log: {log_path}",
            )
        )


HealthSubscriber = Callable[[DiscoveryHealth], None]


class CodexDiscoveryService:
    """Polls local Codex metadata outside the Qt event loop."""

    def __init__(
        self,
        manager: TaskManager,
        *,
        discovery: CodexLocalDiscovery | None = None,
        interval_seconds: float = 5.0,
    ) -> None:
        self.manager = manager
        self.discovery = discovery or CodexLocalDiscovery()
        self.interval_seconds = max(0.5, interval_seconds)
        self._manual_ids: set[str] = set()
        self._retained_ids: set[str] = set()
        self._muted_ids: set[str] = set()
        self._auto_active_ids: set[str] = set()
        self._selection_lock = threading.RLock()
        self._health_lock = threading.RLock()
        self._health = DiscoveryHealth()
        self._health_subscribers: list[HealthSubscriber] = []
        self._last_logged_errors: dict[str, float] = {}
        self._logger = logging.getLogger("aacc.discovery")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="aacc-codex-discovery", daemon=True)

    def poll_once(self) -> int:
        auto_active_ids = self.discovery.active_session_ids()
        with self._selection_lock:
            self._auto_active_ids = set(auto_active_ids)
            self._muted_ids -= auto_active_ids
            self._retained_ids |= auto_active_ids
            selected_ids = (
                self._manual_ids | self._retained_ids | self._auto_active_ids
            ) - self._muted_ids
        tasks = self.discovery.discover(selected_ids)
        for task in tasks:
            self.manager.register(task.config, task.state)
        return len(tasks)

    def set_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_monitoring_preferences(selected_ids, set(), set())

    def set_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        with self._selection_lock:
            self._manual_ids = set(manual_ids)
            self._retained_ids = set(retained_ids) - self._manual_ids
            self._muted_ids = set(muted_ids) - self._manual_ids

    def retained_ids(self) -> set[str]:
        with self._selection_lock:
            return set(self._retained_ids)

    def remove_task(self, session_id: str) -> None:
        with self._selection_lock:
            self._manual_ids.discard(session_id)
            self._retained_ids.discard(session_id)
            self._muted_ids.add(session_id)

    def auto_active_ids(self) -> set[str]:
        with self._selection_lock:
            return set(self._auto_active_ids)

    def catalog(self) -> list[CodexSession]:
        return self.discovery.catalog()

    def health(self) -> DiscoveryHealth:
        with self._health_lock:
            return self._health

    def subscribe_health(self, callback: HealthSubscriber) -> Callable[[], None]:
        with self._health_lock:
            self._health_subscribers.append(callback)

        def unsubscribe() -> None:
            with self._health_lock:
                if callback in self._health_subscribers:
                    self._health_subscribers.remove(callback)

        return unsubscribe

    def _publish_health(self, health: DiscoveryHealth) -> None:
        with self._health_lock:
            self._health = health
            subscribers = tuple(self._health_subscribers)
        for callback in subscribers:
            try:
                callback(health)
            except Exception as error:
                self._logger.warning("Discovery health subscriber failed: %s", error)

    def _record_failure(self, error: Exception) -> None:
        now = datetime.now(UTC)
        exception_class = type(error).__name__
        summary = redact(str(error) or exception_class).replace("\n", " ")[:80]
        fingerprint = f"{exception_class}:{summary}"
        diagnostic_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
        monotonic_now = time.monotonic()
        last_logged = self._last_logged_errors.get(fingerprint, 0.0)
        if monotonic_now - last_logged >= 60:
            self._logger.error(
                "Codex discovery poll failed diagnostic_id=%s error=%s: %s",
                diagnostic_id,
                exception_class,
                summary,
                exc_info=True,
            )
            self._last_logged_errors[fingerprint] = monotonic_now
        previous = self.health()
        failures = previous.consecutive_failures + 1
        degraded = isinstance(error, CodexDiscoveryError) or failures >= 3
        self._publish_health(
            DiscoveryHealth(
                degraded=degraded or previous.degraded,
                consecutive_failures=failures,
                consecutive_successes=0,
                diagnostic_id=diagnostic_id,
                summary=summary,
                exception_class=exception_class,
                last_failure_at=now,
                last_success_at=previous.last_success_at,
            )
        )

    def _record_success(self) -> None:
        previous = self.health()
        successes = previous.consecutive_successes + 1
        recovered = previous.degraded and successes >= 2
        self._publish_health(
            DiscoveryHealth(
                degraded=previous.degraded and not recovered,
                consecutive_failures=0,
                consecutive_successes=successes,
                diagnostic_id="" if recovered else previous.diagnostic_id,
                summary="" if recovered else previous.summary,
                exception_class="" if recovered else previous.exception_class,
                last_failure_at=previous.last_failure_at,
                last_success_at=datetime.now(UTC),
            )
        )

    def poll_safely(self) -> int:
        try:
            count = self.poll_once()
        except Exception as error:
            self._record_failure(error)
            return 0
        self._record_success()
        return count

    def start(self) -> None:
        self.poll_safely()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds + 1)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.poll_safely()
