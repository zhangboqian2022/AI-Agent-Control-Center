from __future__ import annotations

import builtins
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from aacc.models import TaskConfig, TaskState, TaskStatus

T = TypeVar("T")
RETRY_DELAYS = (0.05, 0.1, 0.2)
MAX_HISTORY_PER_TASK = 1_000
HISTORY_DAYS = 30


class StateStore:
    def __init__(self, path: Path, *, sleeper: Callable[[float], None] = time.sleep) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._sleep = sleeper
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=3.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=3000")
        self._secure_database_files()

    def _secure_database_files(self) -> None:
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if candidate.exists():
                os.chmod(candidate, 0o600)

    def _retry_locked(self, operation: Callable[[], T]) -> T:
        delays: tuple[float | None, ...] = (*RETRY_DELAYS, None)
        for delay in delays:
            try:
                return operation()
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or delay is None:
                    raise
                self._sleep(delay)
        raise AssertionError("unreachable")

    def initialize(self, tasks: list[TaskConfig]) -> None:
        def operation() -> None:
            with self._connection:
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
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_state_history_task_id_id "
                    "ON state_history(task_id, id DESC)"
                )
                self._delete_expired_history()
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

        with self._lock:
            self._retry_locked(operation)
            self._secure_database_files()

    def register(self, task: TaskConfig) -> None:
        self.initialize([task])

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

    def _delete_expired_history(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=HISTORY_DAYS)).isoformat()
        self._connection.execute("DELETE FROM state_history WHERE created_at < ?", (cutoff,))

    def _bound_task_history(self, task_id: str) -> None:
        self._connection.execute(
            "DELETE FROM state_history WHERE task_id = ? AND id NOT IN ("
            "SELECT id FROM state_history WHERE task_id = ? ORDER BY id DESC LIMIT ?)",
            (task_id, task_id, MAX_HISTORY_PER_TASK),
        )

    def update(self, state: TaskState, *, append_history: bool = True) -> TaskState:
        payload = state.model_dump_json()

        def operation() -> None:
            with self._connection:
                cursor = self._connection.execute(
                    "UPDATE current_states SET payload = ? WHERE task_id = ?",
                    (payload, state.task_id),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Unknown task: {state.task_id}")
                if append_history:
                    self._connection.execute(
                        "INSERT INTO state_history(task_id, payload, created_at) VALUES (?, ?, ?)",
                        (state.task_id, payload, state.updated_at.isoformat()),
                    )
                    self._delete_expired_history()
                    self._bound_task_history(state.task_id)

        with self._lock:
            self._retry_locked(operation)
            self._secure_database_files()
        return state

    def heartbeat(self, state: TaskState) -> TaskState:
        return self.update(state, append_history=False)

    def history(self, task_id: str, limit: int = 100) -> builtins.list[TaskState]:
        safe_limit = min(max(limit, 1), 1000)
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM ("
                "SELECT id, payload FROM state_history WHERE task_id = ? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (task_id, safe_limit),
            ).fetchall()
        return [TaskState.model_validate_json(row["payload"]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
