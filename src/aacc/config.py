from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aacc.models import AgentConfig, AppConfig, TaskConfig, TerminalConfig


def _default_tasks() -> list[TaskConfig]:
    return [
        TaskConfig(
            id="task-1",
            slot=1,
            name="Codex 任务",
            agent=AgentConfig(type="codex_cli", display_name="Codex CLI"),
            terminal=TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal"),
        ),
        TaskConfig(
            id="task-2",
            slot=2,
            name="Claude 任务",
            agent=AgentConfig(type="claude_code", display_name="Claude Code"),
            terminal=TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal"),
        ),
        TaskConfig(
            id="task-3",
            slot=3,
            name="Kimi 任务",
            agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
            terminal=TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal"),
        ),
        TaskConfig(
            id="task-4",
            slot=4,
            name="Z Code 任务",
            agent=AgentConfig(
                type="generic_cli", display_name="Z Code", process_patterns=["zcode"]
            ),
            terminal=TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal"),
        ),
    ]


def default_config() -> AppConfig:
    config = AppConfig(tasks=_default_tasks())
    config.app.api.token = secrets.token_urlsafe(32)
    config.hotkeys = {
        "focus_task_1": "F13",
        "focus_task_2": "F14",
        "focus_task_3": "F15",
        "focus_task_4": "F16",
        "send_enter": "F17",
        "send_1": "F18",
        "send_2": "F19",
        "voice": "F20",
    }
    return config


def create_default_config(path: Path) -> AppConfig:
    config = default_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return create_default_config(path)
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AppConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as error:
        if "regular expression" in str(error) or "127.0.0.1" in str(error):
            raise ValueError(str(error)) from error
        raise ValueError(f"Invalid AACC configuration: {error}") from error
