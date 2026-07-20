from __future__ import annotations

import builtins
import threading
from collections.abc import Callable

from aacc.models import AppConfig, TaskConfig, TaskState, TaskStatus
from aacc.persistence import StateStore
from aacc.state_machine import StateMachine

Subscriber = Callable[[TaskState], None]


class TaskManager:
    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self._tasks = {task.id: task for task in config.tasks}
        self._subscribers: list[Subscriber] = []
        self._lock = threading.RLock()

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
        for callback in subscribers:
            try:
                callback(saved)
            except Exception:
                continue
        return saved

    def register(self, task: TaskConfig, state: TaskState | None = None) -> TaskState:
        """Register a local runtime task without rewriting the YAML configuration."""
        with self._lock:
            is_new = task.id not in self._tasks
            self._tasks[task.id] = task
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
            for callback in subscribers:
                try:
                    callback(saved)
                except Exception:
                    continue
            return saved
        return self.update(state)

    def reset(self, task_id: str) -> TaskState:
        return self.update(
            TaskState.new(task_id, TaskStatus.IDLE, message="已重置", source="manual")
        )

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
        self.store.close()
