from datetime import UTC, datetime, timedelta

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
