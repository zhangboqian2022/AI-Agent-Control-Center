from __future__ import annotations

import builtins
import sqlite3
import threading
from pathlib import Path

from aacc.models import TaskConfig, TaskState, TaskStatus


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row

    def initialize(self, tasks: list[TaskConfig]) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS current_states ("
                "task_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS state_history ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
                "payload TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            for task in tasks:
                initial = TaskState.new(
                    task.id,
                    TaskStatus.IDLE if task.enabled else TaskStatus.UNCONFIGURED,
                    source="system",
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO current_states(task_id, payload) VALUES (?, ?)",
                    (task.id, initial.model_dump_json()),
                )

    def get(self, task_id: str) -> TaskState:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM current_states WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return TaskState.model_validate_json(row["payload"])

    def list(self) -> list[TaskState]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM current_states ORDER BY task_id"
            ).fetchall()
        return [TaskState.model_validate_json(row["payload"]) for row in rows]

    def update(self, state: TaskState) -> TaskState:
        payload = state.model_dump_json()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE current_states SET payload = ? WHERE task_id = ?",
                (payload, state.task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown task: {state.task_id}")
            self._connection.execute(
                "INSERT INTO state_history(task_id, payload, created_at) VALUES (?, ?, ?)",
                (state.task_id, payload, state.updated_at.isoformat()),
            )
        return state

    def history(self, task_id: str, limit: int = 100) -> builtins.list[TaskState]:
        safe_limit = min(max(limit, 1), 1000)
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM state_history WHERE task_id = ? ORDER BY id ASC LIMIT ?",
                (task_id, safe_limit),
            ).fetchall()
        return [TaskState.model_validate_json(row["payload"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
