from aacc.models import TaskState, TaskStatus


def test_status_accepts_cli_spelling() -> None:
    assert TaskStatus.parse("waiting-approval") is TaskStatus.WAITING_APPROVAL
    assert TaskStatus.parse("RUNNING") is TaskStatus.RUNNING


def test_new_state_records_normalized_status() -> None:
    state = TaskState.new("task-1", "running", message="working", source="manual")
    assert state.status is TaskStatus.RUNNING
    assert state.task_id == "task-1"
    assert state.message == "working"
    assert state.confidence == 1.0

