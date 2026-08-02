from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import Annotated, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from aacc import __version__
from aacc.automation import AutomationError
from aacc.models import AppConfig, TaskConfig, TaskState, TaskStatus
from aacc.task_manager import TaskManager

AllowedKey = Literal["ENTER", "ESC", "UP", "DOWN", "LEFT", "RIGHT", "CTRL_C", "1", "2"]

_METADATA_MAX_KEYS = 20
_METADATA_KEY_MAX_LENGTH = 64
_METADATA_MAX_SERIALIZED_BYTES = 8 * 1024
_KNOWN_SOURCES = {
    "api",
    "manual",
    "wrapper",
    "hook",
    "process",
    "log",
    "codex_local",
    "kimi_local",
    "kimi_desktop_local",
    "opencode_local",
    "automation",
}

_logger = logging.getLogger("aacc.api")


class _SlidingWindowCounter:
    """Minimal thread-safe sliding-window rate limiter keyed by client."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(0.1, window_seconds)
        self._clock = clock or time.monotonic
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = [hit for hit in self._hits.get(key, []) if now - hit < self.window_seconds]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


class Controller(Protocol):
    def focus(self, task: TaskConfig) -> str: ...
    def send_key(self, task: TaskConfig, key: str) -> str: ...
    def send_text(self, task: TaskConfig, text: str) -> str: ...
    def start_voice(self, task: TaskConfig) -> str: ...


class StatusRequest(BaseModel):
    status: TaskStatus
    message: str = Field(default="", max_length=2000)
    source: str = Field(default="api", min_length=1, max_length=80)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        if value not in _KNOWN_SOURCES:
            _logger.warning("Unknown status source %r downgraded to %r", value[:80], "api")
            return "api"
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata_bounds(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > _METADATA_MAX_KEYS:
            raise ValueError(f"metadata must have at most {_METADATA_MAX_KEYS} keys")
        for key in value:
            if len(key) > _METADATA_KEY_MAX_LENGTH:
                raise ValueError(
                    f"metadata keys must be at most {_METADATA_KEY_MAX_LENGTH} characters"
                )
        if len(json.dumps(value)) > _METADATA_MAX_SERIALIZED_BYTES:
            raise ValueError("metadata serialized size must not exceed 8 KiB")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> TaskStatus:
        if not isinstance(value, (str, TaskStatus)):
            raise ValueError("status must be a string")
        try:
            return TaskStatus.parse(value)
        except ValueError as error:
            raise ValueError("unknown task status") from error


class KeyRequest(BaseModel):
    key: AllowedKey


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    """Return the raw Bearer token value for rate-limit keying."""
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required"
        )
    return authorization[len(prefix) :]


def create_api(
    config: AppConfig,
    manager: TaskManager,
    controller: Controller | None = None,
    *,
    send_text_rate_limit: int = 10,
    send_text_rate_window_seconds: float = 10.0,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    app = FastAPI(title="AACC Local API", version=__version__, docs_url=None, redoc_url=None)
    send_text_limiter = _SlidingWindowCounter(
        send_text_rate_limit, send_text_rate_window_seconds, clock
    )

    @app.exception_handler(AutomationError)
    async def automation_error(_request: Request, error: AutomationError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required"
            )
        supplied = authorization[len(prefix) :]
        if not secrets.compare_digest(supplied, config.app.api.token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    authorized = Depends(require_token)

    def task_or_404(task_id: str) -> TaskConfig:
        try:
            return manager.task_config(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def require_controller() -> Controller:
        if controller is None:
            raise HTTPException(status_code=503, detail="Desktop automation is not available")
        return controller

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/tasks", dependencies=[authorized])
    def list_tasks() -> list[TaskState]:
        return manager.list()

    @app.get("/api/v1/tasks/{task_id}", dependencies=[authorized])
    def get_task(task_id: str) -> TaskState:
        task_or_404(task_id)
        return manager.get(task_id)

    @app.post("/api/v1/tasks/{task_id}/status", dependencies=[authorized])
    def set_status(task_id: str, request: StatusRequest) -> TaskState:
        task_or_404(task_id)
        return manager.update(
            TaskState.new(
                task_id,
                request.status,
                message=request.message,
                source=request.source,
                confidence=request.confidence,
                metadata=request.metadata,
            )
        )

    @app.post("/api/v1/tasks/{task_id}/reset", dependencies=[authorized])
    def reset(task_id: str) -> TaskState:
        task_or_404(task_id)
        return manager.reset(task_id)

    @app.get("/api/v1/tasks/{task_id}/events", dependencies=[authorized])
    def events(task_id: str, limit: int = 100) -> list[TaskState]:
        task_or_404(task_id)
        return manager.history(task_id, limit)

    @app.post("/api/v1/tasks/{task_id}/focus", dependencies=[authorized])
    def focus(task_id: str) -> dict[str, str]:
        task = task_or_404(task_id)
        return {"result": require_controller().focus(task)}

    @app.post("/api/v1/tasks/{task_id}/send-key", dependencies=[authorized])
    def send_key(task_id: str, request: KeyRequest) -> dict[str, str]:
        task = task_or_404(task_id)
        return {"result": require_controller().send_key(task, request.key)}

    @app.post("/api/v1/tasks/{task_id}/send-text", dependencies=[authorized])
    def send_text(
        task_id: str,
        request: TextRequest,
        token: Annotated[str, Depends(bearer_token)],
    ) -> dict[str, str]:
        task = task_or_404(task_id)
        if not send_text_limiter.allow(token):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
            )
        return {"result": require_controller().send_text(task, request.text)}

    @app.post("/api/v1/tasks/{task_id}/voice", dependencies=[authorized])
    def voice(task_id: str) -> dict[str, str]:
        task = task_or_404(task_id)
        return {"result": require_controller().start_voice(task)}

    @app.get("/api/v1/adapters", dependencies=[authorized])
    def adapters() -> list[dict[str, str]]:
        return [
            {
                "task_id": task.id,
                "type": task.agent.type,
                "name": task.agent.display_name or task.agent.type,
            }
            for task in config.tasks
        ]

    @app.post("/api/v1/reload-config", dependencies=[authorized])
    def reload_config() -> dict[str, str]:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Config reload is not implemented; restart AACC to load configuration changes",
        )

    return app
