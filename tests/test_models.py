import pytest
from pydantic import ValidationError

from aacc.models import AppConfig, TaskState, TaskStatus


def test_status_accepts_cli_spelling() -> None:
    assert TaskStatus.parse("waiting-approval") is TaskStatus.WAITING_APPROVAL
    assert TaskStatus.parse("RUNNING") is TaskStatus.RUNNING


def test_new_state_records_normalized_status() -> None:
    state = TaskState.new("task-1", "running", message="working", source="manual")
    assert state.status is TaskStatus.RUNNING
    assert state.task_id == "task-1"
    assert state.message == "working"
    assert state.confidence == 1.0


def test_config_schema_version_defaults_to_one() -> None:
    assert AppConfig().config_version == 1


@pytest.mark.parametrize("timeout", [1.9, 15.1])
def test_automation_timeout_must_stay_within_safe_bounds(timeout: float) -> None:
    with pytest.raises(ValidationError):
        AppConfig(app={"automation_timeout_seconds": timeout})
