from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from aacc.codex_discovery import DiscoveredTask
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig

Clock = Callable[[], datetime]
FileModifiedAt = Callable[[Path], datetime]
ProcessAlive = Callable[[], bool]

_KIMI_PROCESS_PATTERN = re.compile(r"(?:^|/)kimi(?:\s|$)", re.IGNORECASE)
_NAME_MAX_LENGTH = 20
_WIRE_TAIL_BYTES = 65_536
# Wire event types that bound a turn; all other types (config, permission, …)
# are ignored when scanning for turn state. Only event *types* are inspected,
# never prompt or response content.
_TURN_ACTIVE_TYPES = {"turn.prompt", "llm.request", "context.append_loop_event"}


class KimiDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class KimiSession:
    session_id: str
    title: str
    updated_at: datetime


class KimiLocalDiscovery:
    """Reads only safe Kimi Code task metadata from local index files.

    Session wire/log files contain sensitive content: log/state files are only
    stat'ed for mtimes, and the wire tail is scanned for event *types* alone
    (turn boundary detection) — prompt and response content is never read.
    """

    def __init__(
        self,
        kimi_home: Path | None = None,
        session_index_path: Path | None = None,
        sessions_root: Path | None = None,
        *,
        now: Clock = lambda: datetime.now(UTC),
        file_modified_at: FileModifiedAt | None = None,
        agent_process_alive: ProcessAlive | None = None,
        activity_window_seconds: float = 90.0,
        max_tasks: int = 20,
    ) -> None:
        home = kimi_home or Path.home() / ".kimi-code"
        self.session_index_path = session_index_path or home / "session_index.jsonl"
        self.sessions_root = sessions_root or home / "sessions"
        self.now = now
        self.file_modified_at = file_modified_at or self._file_modified_at
        self.agent_process_alive = agent_process_alive or self._agent_process_alive
        self.activity_window_seconds = max(10.0, activity_window_seconds)
        self.max_tasks = max(1, min(max_tasks, 20))

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        sessions = self._sessions()
        if selected_ids is not None:
            sessions = [session for session in sessions if session["id"] in selected_ids]
        now = self.now()
        process_alive: bool | None = None
        discovered: list[DiscoveredTask] = []
        for session in sessions:
            session_id = session["id"]
            activity_at = self._activity_at(session["session_dir"])
            updated_at = activity_at if activity_at is not None else session["updated_at"]
            if self._turn_completed(session["session_dir"]):
                status = TaskStatus.COMPLETED
                message = "回合已完成"
                confidence = 0.96
            elif activity_at is not None and self._is_recent(now, activity_at):
                status = TaskStatus.RUNNING
                message = "正在运行"
                confidence = 0.9
            else:
                if process_alive is None:
                    process_alive = self.agent_process_alive()
                if process_alive:
                    status = TaskStatus.IDLE
                    message = "空闲"
                    confidence = 0.7
                else:
                    status = TaskStatus.UNKNOWN
                    message = "未检测到运行进程"
                    confidence = 0.55
            task_id = f"kimi:{session_id}"
            discovered.append(
                DiscoveredTask(
                    config=TaskConfig(
                        id=task_id,
                        slot=1,
                        name=(session["title"] or f"Kimi 任务 {session_id[:8]}")[
                            :_NAME_MAX_LENGTH
                        ],
                        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
                        terminal=TerminalConfig(
                            type="terminal_app", app_bundle_id="com.apple.Terminal"
                        ),
                    ),
                    state=TaskState(
                        task_id=task_id,
                        status=status,
                        message=message,
                        source="kimi_local",
                        confidence=confidence,
                        started_at=activity_at if status is TaskStatus.RUNNING else None,
                        updated_at=updated_at,
                        finished_at=updated_at if status is TaskStatus.COMPLETED else None,
                        pid=None,
                        session_id=session_id,
                        metadata={"discovered": True},
                    ),
                )
            )
        discovered.sort(
            key=lambda item: (item.state.status is TaskStatus.RUNNING, item.state.updated_at),
            reverse=True,
        )
        return [
            DiscoveredTask(
                config=item.config.model_copy(update={"slot": slot}),
                state=item.state,
            )
            for slot, item in enumerate(discovered[: self.max_tasks], start=1)
        ]

    def catalog(self) -> list[KimiSession]:
        return [
            KimiSession(
                session_id=session["id"],
                title=session["title"] or f"Kimi 任务 {session['id'][:8]}",
                updated_at=session["updated_at"],
            )
            for session in sorted(
                self._sessions(), key=lambda item: item["updated_at"], reverse=True
            )
        ]

    def active_session_ids(self, *, limit: int = 4) -> set[str]:
        """Return a small set of recently verified active sessions for auto-monitoring."""
        active: set[str] = set()
        for task in self.discover():
            if task.state.status is TaskStatus.RUNNING and task.state.session_id is not None:
                active.add(task.state.session_id)
                if len(active) >= max(1, limit):
                    break
        return active

    def _sessions(self) -> list[dict[str, Any]]:
        try:
            lines = self.session_index_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as error:
            raise KimiDiscoveryError("Kimi session index is unreadable") from error
        sessions_by_id: dict[str, dict[str, Any]] = {}
        for line in lines:
            if len(line) > 16_384:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            session_id = item.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                continue
            session_dir = item.get("sessionDir")
            if isinstance(session_dir, str) and session_dir:
                session_path = Path(session_dir)
            else:
                session_path = self.sessions_root / session_id
            title, updated_at = self._read_state(session_path)
            if updated_at is None:
                updated_at = self._fallback_updated_at(session_path)
            session = {
                "id": session_id,
                "session_dir": session_path,
                "title": title[:120],
                "updated_at": updated_at,
            }
            previous = sessions_by_id.get(session_id)
            if previous is None or session["updated_at"] >= previous["updated_at"]:
                sessions_by_id[session_id] = session
        return list(sessions_by_id.values())

    def _read_state(self, session_dir: Path) -> tuple[str, datetime | None]:
        """Read only title/updatedAt from state.json; tolerate missing or malformed files."""
        try:
            raw = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "", None
        if not isinstance(raw, dict):
            return "", None
        title = raw.get("title")
        updated_at = self._parse_time(raw.get("updatedAt"))
        return title if isinstance(title, str) else "", updated_at

    def _fallback_updated_at(self, session_dir: Path) -> datetime:
        for path in (session_dir / "state.json", session_dir):
            try:
                return self.file_modified_at(path)
            except OSError:
                continue
        return datetime.min.replace(tzinfo=UTC)

    def _activity_at(self, session_dir: Path) -> datetime | None:
        """Latest mtime among known activity files; contents are never read."""
        candidates = (
            session_dir / "agents" / "main" / "wire.jsonl",
            session_dir / "logs" / "kimi-code.log",
            session_dir / "state.json",
        )
        latest: datetime | None = None
        for path in candidates:
            if not path.exists():
                continue
            try:
                modified_at = self.file_modified_at(path)
            except OSError:
                continue
            if latest is None or modified_at > latest:
                latest = modified_at
        return latest

    def _is_recent(self, now: datetime, observed_at: datetime) -> bool:
        return (now - observed_at).total_seconds() <= self.activity_window_seconds

    def _turn_completed(self, session_dir: Path) -> bool:
        """Detect a finished turn from the wire tail, reading event types only.

        A completed turn ends with a `usage.record` event scoped to the turn;
        any later turn-boundary event (prompt, loop activity, llm request)
        means the session is working again.
        """
        wire_path = session_dir / "agents" / "main" / "wire.jsonl"
        try:
            with wire_path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - _WIRE_TAIL_BYTES))
                lines = handle.read().decode("utf-8", errors="ignore").splitlines()
        except OSError:
            return False
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            event_type = item.get("type")
            if event_type in _TURN_ACTIVE_TYPES:
                return False
            if event_type == "usage.record" and item.get("usageScope") == "turn":
                return True
        return False

    @staticmethod
    def _file_modified_at(path: Path) -> datetime:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)

    @staticmethod
    def _agent_process_alive() -> bool:
        try:
            for process in psutil.process_iter(["name"]):
                name = process.info.get("name")
                if isinstance(name, str) and _KIMI_PROCESS_PATTERN.search(name):
                    return True
        except (psutil.Error, OSError):
            return False
        return False

    def _parse_time(self, raw: object) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
