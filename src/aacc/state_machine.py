from datetime import UTC, datetime

from aacc.models import TaskState, TaskStatus


class StateMachine:
    STALE_SECONDS = 300
    TERMINAL = {
        TaskStatus.COMPLETED,
        TaskStatus.ERROR,
        TaskStatus.CANCELLED,
        TaskStatus.STOPPED,
    }
    RESTART = {TaskStatus.STARTING, TaskStatus.THINKING, TaskStatus.RUNNING}

    @classmethod
    def accept(cls, current: TaskState | None, candidate: TaskState) -> bool:
        if current is None:
            return True
        if candidate.source == "manual":
            return True
        if current.source == "manual" and candidate.source != "manual":
            age = (datetime.now(UTC) - current.updated_at).total_seconds()
            if age <= cls.STALE_SECONDS:
                return False
        if current.status in cls.TERMINAL and candidate.status in cls.RESTART:
            return True
        age = (datetime.now(UTC) - current.updated_at).total_seconds()
        if candidate.confidence < current.confidence and age <= cls.STALE_SECONDS:
            return False
        return candidate.updated_at >= current.updated_at
