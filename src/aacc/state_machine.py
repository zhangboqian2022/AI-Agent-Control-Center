import json
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
    RESTART = {
        TaskStatus.STARTING,
        TaskStatus.THINKING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING_INPUT,
        TaskStatus.WAITING_APPROVAL,
    }
    RUN_STATES = {
        TaskStatus.STARTING,
        TaskStatus.THINKING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING_INPUT,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.WARNING,
        TaskStatus.PAUSED,
    }

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

    @staticmethod
    def _semantic_key(state: TaskState) -> tuple[object, ...]:
        metadata = json.dumps(
            state.model_dump(mode="json")["metadata"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            state.status,
            state.message,
            state.source,
            state.confidence,
            state.pid,
            state.session_id,
            metadata,
        )

    @classmethod
    def _apply_lifecycle(cls, current: TaskState | None, candidate: TaskState) -> TaskState:
        if candidate.status in cls.TERMINAL:
            started_at = candidate.started_at
            if current is not None and current.status not in cls.TERMINAL:
                started_at = current.started_at or started_at
            return candidate.model_copy(
                update={
                    "started_at": started_at,
                    "finished_at": candidate.finished_at or candidate.updated_at,
                }
            )
        if candidate.status in cls.RUN_STATES:
            started_at = candidate.started_at
            if current is not None and current.status in cls.RUN_STATES:
                started_at = current.started_at
            return candidate.model_copy(
                update={
                    "started_at": started_at or candidate.updated_at,
                    "finished_at": None,
                }
            )
        return candidate.model_copy(update={"started_at": None, "finished_at": None})

    @classmethod
    def transition(cls, current: TaskState | None, candidate: TaskState) -> TaskState | None:
        if not cls.accept(current, candidate):
            return None
        normalized = cls._apply_lifecycle(current, candidate)
        if current is not None and cls._semantic_key(current) == cls._semantic_key(normalized):
            return None
        return normalized

    @classmethod
    def heartbeat_due(cls, current: TaskState, candidate: TaskState) -> bool:
        if cls._semantic_key(current) != cls._semantic_key(candidate):
            return False
        return (candidate.updated_at - current.updated_at).total_seconds() >= 60
