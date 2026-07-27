from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

WINDOWS_PROCESS_CREATION_FLAGS = 0x08000000 | 0x00000200
_SUPPORTED_CODEX_SUFFIXES = frozenset((".exe", ".cmd", ".bat"))


@dataclass(frozen=True)
class BrokerCommand:
    args: tuple[str, ...]
    creationflags: int = WINDOWS_PROCESS_CREATION_FLAGS


def packaged_broker_path(
    *,
    platform: str | None = None,
    frozen: bool | None = None,
    executable: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the only broker path allowed for the current Windows runtime."""

    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform != "win32":
        return None

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    application_executable = Path(sys.executable) if executable is None else executable
    if is_frozen:
        if not application_executable.is_absolute():
            return None
        return application_executable.with_name("aacc-spawn.exe")

    environment = os.environ if environ is None else environ
    raw_override = environment.get("AACC_SPAWN_BROKER_PATH")
    if not raw_override:
        return None
    override = Path(raw_override)
    return override if override.is_absolute() else None


def build_broker_command(
    broker: Path,
    codex: Path,
    *,
    parent_pid: int,
    bundle_dir: Path,
) -> BrokerCommand:
    """Build protocol 1 without resolving or searching for any path."""

    _require_absolute_regular_file(broker, "broker")
    _require_absolute_regular_file(codex, "Codex executable")
    if codex.suffix.lower() not in _SUPPORTED_CODEX_SUFFIXES:
        raise ValueError("Codex executable has an unsupported suffix")
    if not bundle_dir.is_absolute():
        raise ValueError("bundle directory must be absolute")
    if not bundle_dir.is_dir():
        raise ValueError("bundle directory must be an existing directory")
    if isinstance(parent_pid, bool) or not isinstance(parent_pid, int) or parent_pid <= 0:
        raise ValueError("parent PID must be a positive integer")

    return BrokerCommand(
        (
            str(broker),
            "--protocol",
            "1",
            "--parent-pid",
            str(parent_pid),
            "--bundle-dir",
            str(bundle_dir),
            "--codex",
            str(codex),
        )
    )


def _require_absolute_regular_file(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    if not path.is_file():
        raise ValueError(f"{label} path must be an existing regular file")
