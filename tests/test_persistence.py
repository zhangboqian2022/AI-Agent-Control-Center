import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

from aacc.config import default_config
from aacc.models import TaskState, TaskStatus
from aacc.persistence import StateStore


def test_state_survives_store_reopen_and_history_is_ordered(tmp_path: Path) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    store.initialize(default_config().tasks)
    store.update(TaskState.new("task-1", "running", message="one", source="api"))
    store.update(TaskState.new("task-1", "completed", message="two", source="api"))
    store.close()

    reopened = StateStore(path)
    reopened.initialize(default_config().tasks)
    assert reopened.get("task-1").status is TaskStatus.COMPLETED
    assert [item.message for item in reopened.history("task-1")] == ["one", "two"]
    reopened.close()


def test_initialize_creates_idle_state_for_each_configured_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    assert len(store.list()) == 4
    assert all(item.status is TaskStatus.IDLE for item in store.list())
    store.close()


def test_history_returns_recent_rows_oldest_to_newest(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    for index in range(5):
        store.update(TaskState.new("task-1", "running", message=str(index), source="api"))

    assert [item.message for item in store.history("task-1", 2)] == ["3", "4"]
    store.close()


def test_database_is_private(tmp_path: Path) -> None:
    path = tmp_path / "aacc.db"
    store = StateStore(path)
    store.initialize(default_config().tasks)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    store.close()


def test_heartbeat_updates_current_without_growing_history(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    current = store.update(TaskState.new("task-1", "running", message="working", source="api"))
    heartbeat = current.model_copy(update={"updated_at": current.updated_at + timedelta(minutes=1)})

    store.heartbeat(heartbeat)

    assert store.get("task-1").updated_at == heartbeat.updated_at
    assert len(store.history("task-1")) == 1
    store.close()


def test_history_retains_at_most_one_thousand_rows_per_task(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(default_config().tasks)
    for index in range(1_001):
        store.update(TaskState.new("task-1", "running", message=str(index), source="api"))

    history = store.history("task-1", 1_000)
    assert len(history) == 1_000
    assert history[0].message == "1"
    assert history[-1].message == "1000"
    store.close()


def test_locked_operation_retries_three_times(tmp_path: Path) -> None:
    delays: list[float] = []
    store = StateStore(tmp_path / "aacc.db", sleeper=delays.append)
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise sqlite3.OperationalError("database is locked")
        return "saved"

    assert store._retry_locked(operation) == "saved"
    assert delays == [0.05, 0.1, 0.2]
    store.close()
