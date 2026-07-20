from datetime import UTC, datetime, timedelta

import pytest

from aacc.models import TaskState, TaskStatus
from aacc.state_machine import StateMachine


def state(status: TaskStatus, source: str, confidence: float, age: int = 0) -> TaskState:
    return TaskState(
        task_id="task-1",
        status=status,
        source=source,
        confidence=confidence,
        updated_at=datetime.now(UTC) - timedelta(seconds=age),
    )


def test_manual_update_overrides_higher_confidence_adapter() -> None:
    current = state(TaskStatus.RUNNING, "hook", 0.99)
    candidate = state(TaskStatus.PAUSED, "manual", 0.5)
    assert StateMachine.accept(current, candidate)


def test_fresh_lower_confidence_inference_does_not_override() -> None:
    current = state(TaskStatus.RUNNING, "api", 0.95)
    candidate = state(TaskStatus.UNKNOWN, "log", 0.4)
    assert not StateMachine.accept(current, candidate)


def test_stale_state_can_be_replaced_by_lower_confidence_warning() -> None:
    current = state(TaskStatus.RUNNING, "log", 0.8, age=301)
    candidate = state(TaskStatus.WARNING, "process", 0.5)
    assert StateMachine.accept(current, candidate)


def test_terminal_state_restarts_on_explicit_starting_update() -> None:
    current = state(TaskStatus.COMPLETED, "api", 1.0)
    candidate = state(TaskStatus.STARTING, "wrapper", 0.9)
    assert StateMachine.accept(current, candidate)


def test_transition_preserves_run_start_and_finishes() -> None:
    started = TaskState.new("task-1", "RUNNING", source="codex_local")
    waiting_candidate = TaskState.new("task-1", "WAITING_INPUT", source="codex_local")

    waiting = StateMachine.transition(started, waiting_candidate)

    assert waiting is not None
    assert waiting.started_at == started.started_at
    completed = StateMachine.transition(
        waiting, TaskState.new("task-1", "COMPLETED", source="codex_local")
    )
    assert completed is not None
    assert completed.started_at == started.started_at
    assert completed.finished_at is not None


def test_transition_starts_fresh_run_after_terminal_state() -> None:
    completed = TaskState.new("task-1", "COMPLETED", source="codex_local")
    candidate = TaskState.new("task-1", "RUNNING", source="codex_local")

    restarted = StateMachine.transition(completed, candidate)

    assert restarted is not None
    assert restarted.started_at == candidate.started_at
    assert restarted.finished_at is None


@pytest.mark.parametrize("waiting_status", [TaskStatus.WAITING_INPUT, TaskStatus.WAITING_APPROVAL])
def test_terminal_state_restarts_when_new_run_is_first_seen_waiting(
    waiting_status: TaskStatus,
) -> None:
    finished_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    completed = TaskState(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
        source="codex_local",
        confidence=0.96,
        started_at=finished_at - timedelta(minutes=4),
        updated_at=finished_at,
        finished_at=finished_at,
    )
    candidate = TaskState(
        task_id="task-1",
        status=waiting_status,
        source="codex_local",
        confidence=0.9,
        started_at=finished_at + timedelta(seconds=10),
        updated_at=finished_at + timedelta(seconds=15),
    )

    restarted = StateMachine.transition(completed, candidate)

    assert restarted is not None
    assert restarted.status is waiting_status
    assert restarted.started_at == candidate.started_at
    assert restarted.finished_at is None


def test_active_messages_and_waiting_states_keep_one_run_start() -> None:
    started_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    running = TaskState(
        task_id="task-1",
        status=TaskStatus.RUNNING,
        message="正在分析任务",
        source="codex_local",
        started_at=started_at,
        updated_at=started_at,
    )
    changed_message = running.model_copy(
        update={
            "message": "正在修改代码",
            "started_at": started_at + timedelta(seconds=15),
            "updated_at": started_at + timedelta(seconds=15),
        }
    )
    changed = StateMachine.transition(running, changed_message)
    assert changed is not None
    assert changed.started_at == started_at

    waiting_candidate = changed.model_copy(
        update={
            "status": TaskStatus.WAITING_INPUT,
            "started_at": None,
            "updated_at": started_at + timedelta(seconds=30),
        }
    )
    waiting = StateMachine.transition(changed, waiting_candidate)
    assert waiting is not None
    assert waiting.started_at == started_at

    resumed_candidate = waiting.model_copy(
        update={
            "status": TaskStatus.RUNNING,
            "started_at": started_at + timedelta(seconds=45),
            "updated_at": started_at + timedelta(seconds=45),
        }
    )
    resumed = StateMachine.transition(waiting, resumed_candidate)
    assert resumed is not None
    assert resumed.started_at == started_at


def test_semantic_duplicate_returns_none() -> None:
    current = TaskState.new("task-1", "RUNNING", message="working", source="codex_local")
    candidate = current.model_copy(update={"updated_at": current.updated_at})

    assert StateMachine.transition(current, candidate) is None


def test_duplicate_becomes_heartbeat_only_after_one_minute() -> None:
    current = TaskState.new("task-1", "RUNNING", message="working", source="codex_local")
    early = current.model_copy(update={"updated_at": current.updated_at + timedelta(seconds=59)})
    due = current.model_copy(update={"updated_at": current.updated_at + timedelta(seconds=60)})

    assert not StateMachine.heartbeat_due(current, early)
    assert StateMachine.heartbeat_due(current, due)
