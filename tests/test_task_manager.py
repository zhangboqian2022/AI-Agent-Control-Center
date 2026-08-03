from datetime import UTC, datetime, timedelta
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


def _run_state(task_id: str, session_id: str, updated_at: datetime, source: str) -> TaskState:
    return TaskState.new(task_id, "running", source=source, confidence=0.9).model_copy(
        update={"updated_at": updated_at, "session_id": session_id}
    )


def test_expire_stale_discovered_run_states(tmp_path: Path) -> None:
    service = manager(tmp_path)
    old_at = datetime.now(UTC) - timedelta(hours=2)
    fresh_at = datetime.now(UTC) - timedelta(minutes=10)
    configs = {
        suffix: TaskConfig(
            id=f"codex:{suffix}",
            slot=slot,
            name=f"任务{suffix}",
            agent=AgentConfig(type="codex_cli"),
        )
        for slot, suffix in enumerate(("stale", "fresh", "seen", "done", "manual"), start=5)
    }
    service.register(
        configs["stale"], _run_state("codex:stale", "stale-session", old_at, "codex_local")
    )
    service.register(
        configs["fresh"], _run_state("codex:fresh", "fresh-session", fresh_at, "codex_local")
    )
    service.register(
        configs["seen"], _run_state("codex:seen", "seen-session", old_at, "codex_local")
    )
    service.register(
        configs["done"],
        TaskState.new("codex:done", "completed", source="codex_local", confidence=0.96).model_copy(
            update={"updated_at": old_at, "session_id": "done-session"}
        ),
    )
    service.register(configs["manual"], _run_state("codex:manual", "manual-session", old_at, "api"))

    expired = service.expire_stale_discovered(
        source="codex_local",
        seen_session_ids={"seen-session"},
        now=datetime.now(UTC),
    )

    assert expired == 1
    stale_state = service.get("codex:stale")
    assert stale_state.status is TaskStatus.UNKNOWN
    assert stale_state.message == "长时间未更新"
    assert service.get("codex:fresh").status is TaskStatus.RUNNING
    assert service.get("codex:seen").status is TaskStatus.RUNNING
    assert service.get("codex:done").status is TaskStatus.COMPLETED
    assert service.get("codex:manual").status is TaskStatus.RUNNING
    service.close()


def test_expire_stale_discovered_survives_restart(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "state.db")
    store.initialize(config.tasks)
    first = TaskManager(config, store)
    old_at = datetime.now(UTC) - timedelta(hours=2)
    task = TaskConfig(
        id="codex:zombie",
        slot=9,
        name="残留的 Codex 会话",
        agent=AgentConfig(type="codex_cli"),
    )
    first.register(task, _run_state("codex:zombie", "zombie-session", old_at, "codex_local"))
    first.close()

    # 应用重启后内存任务表只剩 YAML 配置，僵尸状态只存在于 SQLite
    restarted_store = StateStore(tmp_path / "state.db")
    restarted_store.initialize(config.tasks)
    restarted = TaskManager(config, restarted_store)

    expired = restarted.expire_stale_discovered(
        source="codex_local",
        seen_session_ids=set(),
        now=datetime.now(UTC),
    )

    assert expired == 1
    zombie = restarted_store.get("codex:zombie")
    assert zombie.status is TaskStatus.UNKNOWN
    assert zombie.message == "长时间未更新"
    restarted.close()


def test_repeated_runtime_registration_does_not_reinitialize_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = manager(tmp_path)
    task = TaskConfig(
        id="codex:repeat",
        slot=5,
        name="重复注册任务",
        agent=AgentConfig(type="codex_cli"),
    )
    initialize_calls = 0
    original_initialize = service.store.initialize

    def record_initialize(tasks: list[TaskConfig]) -> None:
        nonlocal initialize_calls
        initialize_calls += 1
        original_initialize(tasks)

    monkeypatch.setattr(service.store, "initialize", record_initialize)
    service.register(task, TaskState.new(task.id, "running", source="codex_local"))
    service.register(task, TaskState.new(task.id, "waiting_input", source="codex_local"))

    assert initialize_calls == 0
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
