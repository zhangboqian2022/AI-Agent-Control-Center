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
