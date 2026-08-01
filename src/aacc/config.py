from __future__ import annotations

import logging
import os
import secrets
import sys
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aacc.file_security import FileProtectionError, protect_directory, protect_file
from aacc.models import AgentConfig, AppConfig, TaskConfig, TerminalConfig

CURRENT_CONFIG_VERSION = 1
# Placeholder-shaped prefixes are never valid credentials: the shipped example
# config once carried a long placeholder that passed every other check, which
# would have let users run the API with a public constant as the token.
_PLACEHOLDER_PREFIXES = ("change-me", "replace-", "your-token", "placeholder")
Migration = Callable[[dict[str, Any]], dict[str, Any]]
_logger = logging.getLogger("aacc.config")


def _migrate_0_to_1(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(raw)
    migrated["config_version"] = 1
    return migrated


MIGRATIONS: dict[int, Migration] = {0: _migrate_0_to_1}


def _default_terminal_config() -> TerminalConfig:
    if sys.platform == "win32":
        # Windows has no bundle-id concept; windows_terminal focuses by title.
        return TerminalConfig(type="windows_terminal")
    return TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal")


def _default_tasks() -> list[TaskConfig]:
    return [
        TaskConfig(
            id="task-1",
            slot=1,
            name="Codex 任务",
            agent=AgentConfig(type="codex_cli", display_name="Codex CLI"),
            terminal=_default_terminal_config(),
        ),
        TaskConfig(
            id="task-2",
            slot=2,
            name="Claude 任务",
            agent=AgentConfig(type="claude_code", display_name="Claude Code"),
            terminal=_default_terminal_config(),
        ),
        TaskConfig(
            id="task-3",
            slot=3,
            name="Kimi 任务",
            agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
            terminal=_default_terminal_config(),
        ),
        TaskConfig(
            id="task-4",
            slot=4,
            name="Z Code 任务",
            agent=AgentConfig(
                type="generic_cli", display_name="Z Code", process_patterns=["zcode"]
            ),
            terminal=_default_terminal_config(),
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


def is_valid_token(value: str) -> bool:
    return (
        len(value) >= 32
        and value.isprintable()
        and not any(character.isspace() for character in value)
        and not value.startswith(_PLACEHOLDER_PREFIXES)
    )


def _reject_unsafe_path(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("AACC configuration path must not be a symbolic link")
    if path.exists() and not path.is_file():
        raise ValueError("AACC configuration path must be a regular file")


def _prepare_parent(path: Path) -> None:
    if path.parent.is_symlink():
        raise ValueError("AACC configuration directory must not be a symbolic link")
    protect_directory(path.parent, platform=sys.platform)


def _open_posix_temporary(parent_descriptor: int, prefix: str) -> tuple[int, str]:
    """Create an exclusive temporary file relative to an open parent dir."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(10):
        name = f"{prefix}{secrets.token_hex(12)}"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        return descriptor, name
    raise FileExistsError("could not allocate a unique AACC configuration temporary file")


def save_config(path: Path, config: AppConfig) -> None:
    _reject_unsafe_path(path)
    _prepare_parent(path)
    parent_descriptor = -1
    anchored_posix = sys.platform != "win32"
    if anchored_posix:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            descriptor, temporary_name = _open_posix_temporary(parent_descriptor, f".{path.name}.")
        except Exception:
            os.close(parent_descriptor)
            raise
        temporary = path.parent / temporary_name
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
    try:
        if sys.platform == "win32":
            os.close(descriptor)
            descriptor = -1
            protect_file(temporary, platform=sys.platform)
            handle_context = temporary.open("w", encoding="utf-8")
        else:
            try:
                protect_file(temporary, descriptor=descriptor, platform=sys.platform)
            except Exception:
                os.close(descriptor)
                raise
            handle_context = os.fdopen(descriptor, "w", encoding="utf-8")
        with handle_context as handle:
            yaml.safe_dump(
                config.model_dump(mode="json"),
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        if sys.platform == "win32":
            protect_file(temporary, platform=sys.platform)
            os.replace(temporary, path)
        else:
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
    except FileProtectionError:
        _logger.critical("Unable to protect the AACC configuration file")
        raise
    finally:
        if anchored_posix:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
        else:
            temporary.unlink(missing_ok=True)


def _migrate(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    version = raw.get("config_version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("config_version must be an integer")
    if version > CURRENT_CONFIG_VERSION:
        raise ValueError(f"Unsupported AACC config_version: {version}")
    migrated = raw
    changed = False
    while version < CURRENT_CONFIG_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(f"No AACC configuration migration from version {version}")
        migrated = migration(migrated)
        version += 1
        changed = True
    return migrated, changed


def create_default_config(path: Path) -> AppConfig:
    config = default_config()
    save_config(path, config)
    return config


def load_config(path: Path) -> AppConfig:
    _reject_unsafe_path(path)
    _prepare_parent(path)
    if not path.exists():
        return create_default_config(path)
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a mapping")
        migrated, changed = _migrate(raw)
        config = AppConfig.model_validate(migrated)
        if not is_valid_token(config.app.api.token):
            config.app.api.token = secrets.token_urlsafe(32)
            changed = True
        if changed:
            save_config(path, config)
        else:
            protect_file(path, platform=sys.platform)
        return config
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as error:
        if "regular expression" in str(error) or "127.0.0.1" in str(error):
            raise ValueError(str(error)) from error
        raise ValueError(f"Invalid AACC configuration: {error}") from error


def rotate_api_token(path: Path, config: AppConfig) -> str:
    token = secrets.token_urlsafe(32)
    updated = config.model_copy(deep=True)
    updated.app.api.token = token
    save_config(path, updated)
    config.app.api.token = token
    return token
