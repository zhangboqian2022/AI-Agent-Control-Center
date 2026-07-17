from __future__ import annotations

import secrets
from typing import Annotated, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from aacc import __version__
from aacc.models import AppConfig, TaskConfig, TaskState, TaskStatus
from aacc.task_manager import TaskManager

AllowedKey = Literal["ENTER", "ESC", "UP", "DOWN", "LEFT", "RIGHT", "CTRL_C", "1", "2"]


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


def create_api(
    config: AppConfig, manager: TaskManager, controller: Controller | None = None
) -> FastAPI:
    app = FastAPI(title="AACC Local API", version=__version__, docs_url=None, redoc_url=None)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
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
    def send_text(task_id: str, request: TextRequest) -> dict[str, str]:
        task = task_or_404(task_id)
        return {"result": require_controller().send_text(task, request.text)}

    @app.post("/api/v1/tasks/{task_id}/voice", dependencies=[authorized])
    def voice(task_id: str) -> dict[str, str]:
        task = task_or_404(task_id)
        return {"result": require_controller().start_voice(task)}

    @app.get("/api/v1/adapters", dependencies=[authorized])
    def adapters() -> list[dict[str, str]]:
        return [
            {"task_id": task.id, "type": task.agent.type, "name": task.agent.display_name or task.agent.type}
            for task in config.tasks
        ]

    @app.post("/api/v1/reload-config", dependencies=[authorized])
    def reload_config() -> dict[str, str]:
        return {"result": "Restart AACC to load configuration changes"}

    return app

