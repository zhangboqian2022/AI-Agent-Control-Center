from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig

PidExists = Callable[[int], bool]
Clock = Callable[[], datetime]
SessionModifiedAt = Callable[[Path], datetime]
ProcessStartedAt = Callable[[int], int | None]


@dataclass(frozen=True)
class DiscoveredTask:
    config: TaskConfig
    state: TaskState


@dataclass(frozen=True)
class CodexSession:
    conversation_id: str
    title: str
    updated_at: datetime


class CodexLocalDiscovery:
    """Reads only safe Codex task metadata from local index files."""

    def __init__(
        self,
        session_index_path: Path | None = None,
        process_manager_path: Path | None = None,
        session_directory: Path | None = None,
        *,
        pid_exists: PidExists = psutil.pid_exists,
        now: Clock = lambda: datetime.now(UTC),
        session_modified_at: SessionModifiedAt | None = None,
        process_started_at: ProcessStartedAt | None = None,
        activity_window_seconds: float = 90.0,
        max_tasks: int = 20,
    ) -> None:
        codex_home = Path.home() / ".codex"
        self.session_index_path = session_index_path or codex_home / "session_index.jsonl"
        self.process_manager_path = process_manager_path or (
            codex_home / "process_manager" / "chat_processes.json"
        )
        self.session_directory = session_directory or codex_home / "sessions"
        self.pid_exists = pid_exists
        self.now = now
        self.session_modified_at = session_modified_at or self._session_modified_at
        self.process_started_at = process_started_at or self._process_started_at
        self.activity_window_seconds = max(10.0, activity_window_seconds)
        self.max_tasks = max(1, min(max_tasks, 20))

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        sessions = self._sessions()
        if selected_ids is not None:
            sessions = [session for session in sessions if session["id"] in selected_ids]
        selected = {session["id"] for session in sessions}
        active_sessions = self._active_sessions(selected)
        active_pids = self._active_pids(selected)
        discovered: list[DiscoveredTask] = []
        for session in sessions:
            conversation_id = session["id"]
            pid = active_pids.get(conversation_id)
            activity_at = active_sessions.get(conversation_id)
            updated_at = activity_at or session["updated_at"]
            if activity_at is not None:
                status = TaskStatus.RUNNING
                message = "检测到 Codex 会话活动"
                confidence = 0.9
            elif pid is not None:
                status = TaskStatus.RUNNING
                message = "检测到 Codex 正在运行"
                confidence = 0.88
            else:
                status = TaskStatus.UNKNOWN
                message = "最近更新，未检测到运行进程"
                confidence = 0.55
            task_id = f"codex:{conversation_id}"
            discovered.append(
                DiscoveredTask(
                    config=TaskConfig(
                        id=task_id,
                        slot=1,
                        name=session["title"] or f"Codex 任务 {conversation_id[:8]}",
                        agent=AgentConfig(type="codex_cli", display_name="Codex"),
                        terminal=TerminalConfig(type="mac_app", app_bundle_id="com.openai.codex"),
                    ),
                    state=TaskState(
                        task_id=task_id,
                        status=status,
                        message=message,
                        source="codex_local",
                        confidence=confidence,
                        started_at=updated_at if status is TaskStatus.RUNNING else None,
                        updated_at=updated_at,
                        pid=pid,
                        session_id=conversation_id,
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

    def catalog(self) -> list[CodexSession]:
        return [
            CodexSession(
                conversation_id=session["id"],
                title=session["title"] or f"Codex 任务 {session['id'][:8]}",
                updated_at=session["updated_at"],
            )
            for session in sorted(
                self._sessions(), key=lambda item: item["updated_at"], reverse=True
            )
        ]

    def _sessions(self) -> list[dict[str, Any]]:
        try:
            lines = self.session_index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        sessions: list[dict[str, Any]] = []
        for line in lines:
            if len(line) > 16_384:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("id")
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            updated_at = self._parse_time(item.get("updated_at"))
            if updated_at is None:
                continue
            title = item.get("thread_name")
            sessions.append(
                {
                    "id": conversation_id,
                    "title": title[:120] if isinstance(title, str) else "",
                    "updated_at": updated_at,
                }
            )
        return sessions

    def _active_sessions(self, selected_ids: set[str]) -> dict[str, datetime]:
        active: dict[str, datetime] = {}
        if not selected_ids:
            return active
        now = self.now()
        for conversation_id in selected_ids:
            try:
                files = self.session_directory.rglob(f"*{conversation_id}.jsonl")
                latest = max((self.session_modified_at(path) for path in files), default=None)
            except OSError:
                continue
            is_recent = latest is not None and (
                now - latest
            ).total_seconds() <= self.activity_window_seconds
            if is_recent and latest is not None:
                active[conversation_id] = latest
        return active

    def _active_pids(self, selected_ids: set[str]) -> dict[str, int]:
        try:
            raw = json.loads(self.process_manager_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, list):
            return {}
        pids: dict[str, int] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("conversationId")
            pid = item.get("osPid")
            if (
                not isinstance(conversation_id, str)
                or conversation_id not in selected_ids
                or not isinstance(pid, int)
                or pid <= 0
            ):
                continue
            try:
                record_started = item.get("startedAtMs")
                process_started = self.process_started_at(pid)
                process_matches = (
                    not isinstance(record_started, int)
                    or process_started is None
                    or abs(process_started - record_started) <= 60_000
                )
                if self.pid_exists(pid) and process_matches:
                    pids[conversation_id] = pid
            except (OSError, psutil.Error):
                continue
        return pids

    @staticmethod
    def _session_modified_at(path: Path) -> datetime:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)

    @staticmethod
    def _process_started_at(pid: int) -> int | None:
        try:
            return round(psutil.Process(pid).create_time() * 1000)
        except psutil.Error:
            return None

    def _parse_time(self, raw: object) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
