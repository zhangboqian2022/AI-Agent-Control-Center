"""Protected permission to reuse Kimi's native WebView session."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from aacc.file_security import protect_directory, protect_file

_STATE_FILE_NAME = "kimi-web-session-state.json"
_ALLOWED_STATE_FILE_NAMES = frozenset({_STATE_FILE_NAME, "opencode-web-session-state.json"})
_STATE_VERSION = 1


class KimiWebLoginStateStore:
    """Persist the user's permission to reuse the OS-owned Kimi web session."""

    def __init__(
        self,
        config_dir: Path,
        *,
        state_file_name: str = _STATE_FILE_NAME,
    ) -> None:
        if state_file_name not in _ALLOWED_STATE_FILE_NAMES:
            raise ValueError("unsupported web session state file")
        self._config_dir = config_dir
        self._state_file_name = state_file_name

    def may_reuse(self) -> bool:
        """Return whether automatic access to the native session is permitted."""

        path = self._path()
        try:
            self._reject_unsafe_path(path)
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(raw, dict)
            and type(raw.get("version")) is int
            and raw.get("version") == _STATE_VERSION
            and isinstance(raw.get("reuse_native_session"), bool)
        ) and raw["reuse_native_session"]

    def set_may_reuse(self, value: bool) -> None:
        """Atomically persist a reuse decision using AACC's file protections."""

        if not isinstance(value, bool):
            raise ValueError("Kimi web session reuse value must be boolean")
        path = self._path()
        self._reject_unsafe_path(path)
        if self._config_dir.is_symlink():
            raise ValueError("Kimi web session state directory must not be a symbolic link")
        protect_directory(self._config_dir, platform=sys.platform)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{_STATE_FILE_NAME}.",
            dir=self._config_dir,
        )
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
                json.dump(
                    {
                        "version": _STATE_VERSION,
                        "reuse_native_session": value,
                    },
                    handle,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            protect_file(temporary, platform=sys.platform)
            os.replace(temporary, path)
            protect_file(path, platform=sys.platform)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self) -> Path:
        return self._config_dir / self._state_file_name

    @staticmethod
    def _reject_unsafe_path(path: Path) -> None:
        if path.parent.is_symlink():
            raise ValueError("Kimi web session state directory must not be a symbolic link")
        if path.is_symlink():
            raise ValueError("Kimi web session state path must not be a symbolic link")
        if path.exists() and not path.is_file():
            raise ValueError("Kimi web session state path must be a regular file")
