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
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise KeyError(f"Unknown task: {task_id}") from error

    def get(self, task_id: str) -> TaskState:
        self.task_config(task_id)
        return self.store.get(task_id)

    def list(self) -> list[TaskState]:
        states = {state.task_id: state for state in self.store.list()}
        return [
            states[task.id] for task in sorted(self._tasks.values(), key=lambda item: item.slot)
        ]

    def update(self, candidate: TaskState) -> TaskState:
        self.task_config(candidate.task_id)
        with self._lock:
            current = self.store.get(candidate.task_id)
            if not StateMachine.accept(current, candidate):
                return current
            saved = self.store.update(candidate)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            try:
                callback(saved)
            except Exception:
                continue
        return saved

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
