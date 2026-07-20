from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    IDLE = "IDLE"
    STARTING = "STARTING"
    THINKING = "THINKING"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    WARNING = "WARNING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | TaskStatus) -> TaskStatus:
        if isinstance(value, cls):
            return value
        return cls(value.strip().replace("-", "_").upper())


class APIConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=17650, ge=1024, le=65535)
    token: str = ""

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value != "127.0.0.1":
            raise ValueError("AACC V1.0 API host must be 127.0.0.1")
        return value


class AppSettings(BaseModel):
    language: str = "zh-CN"
    theme: str = "system"
    always_on_top: bool = True
    opacity: float = Field(default=0.94, ge=0.35, le=1.0)
    compact_mode: bool = False
    start_at_login: bool = False
    blink_attention: bool = True
    keyboard_injection: bool = True
    automation_timeout_seconds: float = Field(default=5.0, ge=2.0, le=15.0)
    visible_agent_types: list[str] = Field(default_factory=lambda: ["codex_cli", "kimi_code"])
    api: APIConfig = Field(default_factory=APIConfig)


class VoiceConfig(BaseModel):
    hotkey: str = "FN_FN"
    focus_delay_ms: int = Field(default=250, ge=0, le=5000)
    voice_delay_ms: int = Field(default=200, ge=0, le=5000)


class AgentConfig(BaseModel):
    type: str = "generic_cli"
    display_name: str | None = None
    executable_patterns: list[str] = Field(default_factory=list)
    process_patterns: list[str] = Field(default_factory=list)
    running_patterns: list[str] = Field(default_factory=list)
    waiting_input_patterns: list[str] = Field(default_factory=list)
    waiting_approval_patterns: list[str] = Field(default_factory=list)
    completed_patterns: list[str] = Field(default_factory=list)
    error_patterns: list[str] = Field(default_factory=list)

    @field_validator(
        "executable_patterns",
        "process_patterns",
        "running_patterns",
        "waiting_input_patterns",
        "waiting_approval_patterns",
        "completed_patterns",
        "error_patterns",
    )
    @classmethod
    def valid_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            if len(pattern) > 256:
                raise ValueError("regular expression exceeds 256 characters")
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"invalid regular expression: {error}") from error
        return patterns


class TerminalConfig(BaseModel):
    type: str = "terminal_app"
    app_bundle_id: str | None = None
    window_title: str | None = None
    tab_title: str | None = None
    working_directory: str | None = None


class TaskConfig(BaseModel):
    id: str
    slot: int = Field(ge=1, le=20)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    agent: AgentConfig = Field(default_factory=AgentConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)


class AppConfig(BaseModel):
    config_version: int = Field(default=1, ge=1)
    app: AppSettings = Field(default_factory=AppSettings)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    hotkeys: dict[str, str] = Field(default_factory=dict)
    tasks: list[TaskConfig] = Field(default_factory=list)


class TaskState(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.IDLE
    message: str = ""
    source: str = "system"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    pid: int | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def new(
        cls,
        task_id: str,
        status: str | TaskStatus,
        *,
        message: str = "",
        source: str = "manual",
        confidence: float | None = None,
        pid: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskState:
        normalized = TaskStatus.parse(status)
        now = datetime.now(UTC)
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.ERROR,
            TaskStatus.CANCELLED,
            TaskStatus.STOPPED,
        }
        active = {TaskStatus.STARTING, TaskStatus.THINKING, TaskStatus.RUNNING}
        return cls(
            task_id=task_id,
            status=normalized,
            message=message[:2000],
            source=source,
            confidence=confidence
            if confidence is not None
            else (1.0 if source == "manual" else 0.8),
            started_at=now if normalized in active else None,
            updated_at=now,
            finished_at=now if normalized in terminal else None,
            pid=pid,
            metadata=metadata or {},
        )
