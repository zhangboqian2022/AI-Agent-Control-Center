from __future__ import annotations

import builtins
import logging
import threading
from collections.abc import Callable
from datetime import datetime

from aacc.models import AppConfig, TaskConfig, TaskState, TaskStatus
from aacc.persistence import StateStore
from aacc.state_machine import StateMachine

Subscriber = Callable[[TaskState], None]

_logger = logging.getLogger("aacc.tasks")

DISCOVERED_RUN_STATE_TTL_SECONDS = 3600.0


def _notify(subscribers: tuple[Subscriber, ...], state: TaskState) -> None:
    for callback in subscribers:
        try:
            callback(state)
        except Exception as error:
            _logger.warning("Task state subscriber failed: %s", error)


class TaskManager:
    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self._tasks = {task.id: task for task in config.tasks}
        self._subscribers: list[Subscriber] = []
        self._lock = threading.RLock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def task_config(self, task_id: str) -> TaskConfig:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as error:
                raise KeyError(f"Unknown task: {task_id}") from error

    def task_configs(self) -> list[TaskConfig]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda item: item.slot)

    def get(self, task_id: str) -> TaskState:
        self.task_config(task_id)
        return self.store.get(task_id)

    def list(self) -> list[TaskState]:
        states = {state.task_id: state for state in self.store.list()}
        return [states[task.id] for task in self.task_configs()]

    def update(self, candidate: TaskState) -> TaskState:
        self.task_config(candidate.task_id)
        with self._lock:
            current = self.store.get(candidate.task_id)
            transitioned = StateMachine.transition(current, candidate)
            if transitioned is None:
                if StateMachine.heartbeat_due(current, candidate):
                    heartbeat = current.model_copy(update={"updated_at": candidate.updated_at})
                    return self.store.heartbeat(heartbeat)
                return current
            saved = self.store.update(transitioned)
            subscribers = tuple(self._subscribers)
        _notify(subscribers, saved)
        return saved

    def register(self, task: TaskConfig, state: TaskState | None = None) -> TaskState:
        """Register a local runtime task without rewriting the YAML configuration."""
        with self._lock:
            is_new = task.id not in self._tasks
            self._tasks[task.id] = task
            if is_new:
                self.store.register(task)
        if state is None:
            return self.store.get(task.id)
        if is_new:
            with self._lock:
                transitioned = StateMachine.transition(None, state)
                if transitioned is None:
                    raise ValueError("Initial task state was rejected")
                saved = self.store.update(transitioned)
                subscribers = tuple(self._subscribers)
            _notify(subscribers, saved)
            return saved
        return self.update(state)

    def reset(self, task_id: str) -> TaskState:
        return self.update(
            TaskState.new(task_id, TaskStatus.IDLE, message="已重置", source="manual")
        )

    def expire_stale_discovered(
        self,
        *,
        source: str,
        seen_session_ids: set[str],
        now: datetime,
        ttl_seconds: float = DISCOVERED_RUN_STATE_TTL_SECONDS,
    ) -> int:
        """Expire discovered run-states that no poll round has refreshed.

        Sessions outside the discovery window (unselected or beyond the result
        limit) never receive fresh candidates, so a stale RUNNING/WAITING state
        would persist forever. Anything genuinely active heartbeats at least
        once a minute, so a run-state older than the TTL is a lie and is
        normalized to UNKNOWN through the regular state machine.

        States are read from the store rather than the in-memory task table:
        after an app restart the zombie's task config is gone from memory but
        its persisted run-state is exactly what must expire.
        """
        expired = 0
        for state in self.store.list():
            if state.source != source:
                continue
            if state.session_id is None or state.session_id in seen_session_ids:
                continue
            if state.status not in StateMachine.RUN_STATES:
                continue
            if (now - state.updated_at).total_seconds() <= ttl_seconds:
                continue
            candidate = state.model_copy(
                update={
                    "status": TaskStatus.UNKNOWN,
                    "message": "长时间未更新",
                    "confidence": 0.55,
                    "updated_at": now,
                    "started_at": None,
                    "finished_at": None,
                    "pid": None,
                }
            )
            transitioned = StateMachine.transition(state, candidate)
            if transitioned is None:
                continue
            saved = self.store.update(transitioned)
            with self._lock:
                subscribers = tuple(self._subscribers)
            _notify(subscribers, saved)
            expired += 1
        return expired

    def history(self, task_id: str, limit: int = 100) -> builtins.list[TaskState]:
        self.task_config(task_id)
        return self.store.history(task_id, limit)

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._subscribers.clear()
            self.store.close()
