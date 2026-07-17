from pathlib import Path

from aacc.config import default_config
from aacc.models import TaskState, TaskStatus
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def manager(tmp_path: Path) -> TaskManager:
    config = default_config()
    store = StateStore(tmp_path / "state.db")
    store.initialize(config.tasks)
    return TaskManager(config, store)


def test_update_persists_and_notifies_subscriber(tmp_path: Path) -> None:
    service = manager(tmp_path)
    seen: list[TaskStatus] = []
    service.subscribe(lambda state: seen.append(state.status))
    result = service.update(TaskState.new("task-1", "running", source="api"))
    assert result.status is TaskStatus.RUNNING
    assert service.get("task-1").status is TaskStatus.RUNNING
    assert seen == [TaskStatus.RUNNING]
    service.close()


def test_low_confidence_update_is_rejected(tmp_path: Path) -> None:
    service = manager(tmp_path)
    service.update(TaskState.new("task-1", "running", source="api", confidence=0.95))
    result = service.update(TaskState.new("task-1", "unknown", source="log", confidence=0.2))
    assert result.status is TaskStatus.RUNNING
    service.close()


def test_reset_returns_task_to_idle(tmp_path: Path) -> None:
    service = manager(tmp_path)
    service.update(TaskState.new("task-1", "error", source="manual"))
    assert service.reset("task-1").status is TaskStatus.IDLE
    service.close()


def test_unknown_task_is_rejected(tmp_path: Path) -> None:
    service = manager(tmp_path)
    try:
        service.get("task-99")
    except KeyError as error:
        assert "task-99" in str(error)
    else:
        raise AssertionError("unknown task should fail")
    service.close()
