from datetime import timedelta
from pathlib import Path

import pytest

from aacc.config import default_config
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


def test_failing_subscriber_is_logged_and_does_not_break_others(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = manager(tmp_path)
    seen: list[str] = []

    def bad(_state: TaskState) -> None:
        raise RuntimeError("boom")

    service.subscribe(bad)
    service.subscribe(lambda state: seen.append(state.task_id))
    with caplog.at_level("WARNING", logger="aacc.tasks"):
        service.update(TaskState.new("task-1", "running", source="api"))

    assert seen == ["task-1"]
    assert "boom" in caplog.text
    service.close()


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


def test_runtime_task_registration_persists_state_and_notifies(tmp_path: Path) -> None:
    service = manager(tmp_path)
    seen: list[str] = []
    service.subscribe(lambda state: seen.append(state.task_id))
    task = TaskConfig(
        id="codex:abc",
        slot=5,
        name="自动发现的 Codex 任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )

    saved = service.register(task, TaskState.new(task.id, "running", source="codex_local"))

    assert service.task_config(task.id).name == task.name
    assert service.get(task.id).status is TaskStatus.RUNNING
    assert saved.task_id == task.id
    assert seen == [task.id]
    service.close()


def test_duplicate_update_does_not_grow_history_or_notify(tmp_path: Path) -> None:
    service = manager(tmp_path)
    seen: list[TaskStatus] = []
    service.subscribe(lambda state: seen.append(state.status))
    current = service.update(
        TaskState.new("task-1", "running", message="working", source="codex_local")
    )
    seen.clear()
    duplicate = current.model_copy(
        update={"updated_at": current.updated_at + timedelta(seconds=10)}
    )

    result = service.update(duplicate)

    assert result.updated_at == current.updated_at
    assert len(service.history("task-1")) == 1
    assert seen == []
    service.close()


def test_due_heartbeat_updates_observation_without_history_or_notification(
    tmp_path: Path,
) -> None:
    service = manager(tmp_path)
    current = service.update(
        TaskState.new("task-1", "running", message="working", source="codex_local")
    )
    seen: list[TaskStatus] = []
    service.subscribe(lambda state: seen.append(state.status))
    heartbeat = current.model_copy(
        update={"updated_at": current.updated_at + timedelta(seconds=60)}
    )

    result = service.update(heartbeat)

    assert result.updated_at == heartbeat.updated_at
    assert service.get("task-1").started_at == current.started_at
    assert len(service.history("task-1")) == 1
    assert seen == []
    service.close()


def test_business_change_preserves_started_at(tmp_path: Path) -> None:
    service = manager(tmp_path)
    running = service.update(TaskState.new("task-1", "running", source="codex_local"))
    waiting = service.update(
        TaskState.new("task-1", "waiting_input", message="reply", source="codex_local")
    )
    completed = service.update(
        TaskState.new("task-1", "completed", message="done", source="codex_local")
    )

    assert waiting.started_at == running.started_at
    assert completed.started_at == running.started_at
    assert completed.finished_at is not None
    assert len(service.history("task-1")) == 3
    service.close()
