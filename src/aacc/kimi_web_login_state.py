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
_ALLOWED_STATE_FILE_NAMES = frozenset(
    {_STATE_FILE_NAME, "opencode-web-session-state.json", "qwen-web-session-state.json"}
)
_STATE_VERSION = 1


class KimiWebLoginStateStore:
    """Persist the user's permission to reuse the OS-owned Kimi web session."""

    SESSION_ORIGIN_DAILY_RECOPY = "daily_recopy"
    SESSION_ORIGIN_MANUAL_LOGIN = "manual_login"
    _KNOWN_SESSION_ORIGINS = frozenset({SESSION_ORIGIN_DAILY_RECOPY, SESSION_ORIGIN_MANUAL_LOGIN})

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

    def logged_out_by_user(self) -> bool:
        """Return whether the last logout was an explicit user action.

        Expiry also persists ``reuse_native_session=false``; recovery
        heuristics must not treat that the same as a deliberate logout.
        Missing or unreadable state fails closed to ``False``.
        """

        path = self._path()
        try:
            self._reject_unsafe_path(path)
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(raw, dict) and raw.get("logged_out_by_user") is True

    def session_origin(self) -> str:
        """Return how the most recent successful session was established.

        Missing or unreadable state fails closed to the manual-login origin:
        unknown provenance must take the protected (no immediate recopy)
        path, never the destructive one.
        """

        raw = self._read_state()
        if raw is None:
            return self.SESSION_ORIGIN_MANUAL_LOGIN
        origin = raw.get("session_origin")
        if isinstance(origin, str) and origin in self._KNOWN_SESSION_ORIGINS:
            return origin
        return self.SESSION_ORIGIN_MANUAL_LOGIN

    def last_success_epoch(self) -> int | None:
        """Return the local epoch seconds of the most recent success."""

        raw = self._read_state()
        if raw is None:
            return None
        value = raw.get("last_success_epoch")
        return value if type(value) is int else None

    def _read_state(self) -> dict[str, Any] | None:
        path = self._path()
        try:
            self._reject_unsafe_path(path)
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def set_may_reuse(
        self,
        value: bool,
        *,
        logged_out_by_user: bool | None = None,
        session_origin: str | None = None,
        last_success_epoch: int | None = None,
    ) -> None:
        """Atomically persist a reuse decision using AACC's file protections.

        ``logged_out_by_user`` records whether the logged-out state is an
        explicit user logout; ``session_origin`` and ``last_success_epoch``
        record how and when the newest successful session was established.
        Omitted fields preserve the previously persisted values.
        """

        if not isinstance(value, bool):
            raise ValueError("Kimi web session reuse value must be boolean")
        marker = self.logged_out_by_user() if logged_out_by_user is None else logged_out_by_user
        origin = self.session_origin() if session_origin is None else session_origin
        if origin not in self._KNOWN_SESSION_ORIGINS:
            raise ValueError("unknown web session origin")
        success_epoch = (
            self.last_success_epoch() if last_success_epoch is None else last_success_epoch
        )
        if success_epoch is not None and type(success_epoch) is not int:
            raise ValueError("last success epoch must be an integer")
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
                        "logged_out_by_user": marker,
                        "session_origin": origin,
                        "last_success_epoch": success_epoch,
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
