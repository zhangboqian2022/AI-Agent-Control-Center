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


@dataclass(frozen=True)
class SessionSignal:
    status: TaskStatus
    observed_at: datetime


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
        session_signals = self._session_signals(selected)
        active_pids = self._active_pids(selected)
        discovered: list[DiscoveredTask] = []
        for session in sessions:
            conversation_id = session["id"]
            pid = active_pids.get(conversation_id)
            signal = session_signals.get(conversation_id)
            updated_at = signal.observed_at if signal is not None else session["updated_at"]
            if signal is not None and signal.status is TaskStatus.COMPLETED:
                status = TaskStatus.COMPLETED
                message = "Codex 回合已完成"
                confidence = 0.96
            elif signal is not None and signal.status is TaskStatus.RUNNING:
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
                        finished_at=updated_at if status is TaskStatus.COMPLETED else None,
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
        except OSError:
            return []
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
            conversation_id = item.get("id")
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            updated_at = self._parse_time(item.get("updated_at"))
            if updated_at is None:
                continue
            title = item.get("thread_name")
            session = {
                "id": conversation_id,
                "title": title[:120] if isinstance(title, str) else "",
                "updated_at": updated_at,
            }
            previous = sessions_by_id.get(conversation_id)
            if previous is None or session["updated_at"] >= previous["updated_at"]:
                sessions_by_id[conversation_id] = session
        return list(sessions_by_id.values())

    def _session_signals(self, selected_ids: set[str]) -> dict[str, SessionSignal]:
        signals: dict[str, SessionSignal] = {}
        if not selected_ids:
            return signals
        now = self.now()
        files_by_id = self._session_paths(selected_ids)
        for conversation_id, files in files_by_id.items():
            if not files:
                continue
            try:
                latest_path = max(files, key=self.session_modified_at)
                latest_at = self.session_modified_at(latest_path)
            except OSError:
                continue
            terminal_signal = self._read_session_signal(latest_path, latest_at)
            if terminal_signal is not None and terminal_signal.status is TaskStatus.COMPLETED:
                signals[conversation_id] = terminal_signal
                continue
            if terminal_signal is not None and self._is_recent(now, terminal_signal.observed_at):
                signals[conversation_id] = terminal_signal
                continue
            if self._is_recent(now, latest_at):
                signals[conversation_id] = SessionSignal(TaskStatus.RUNNING, latest_at)
        return signals

    def _session_paths(self, selected_ids: set[str]) -> dict[str, list[Path]]:
        paths_by_id: dict[str, list[Path]] = {
            conversation_id: [] for conversation_id in selected_ids
        }
        if not paths_by_id:
            return paths_by_id
        try:
            paths = self.session_directory.rglob("*.jsonl")
            for path in paths:
                filename = path.name
                for conversation_id, matched_paths in paths_by_id.items():
                    if filename.endswith(f"{conversation_id}.jsonl"):
                        matched_paths.append(path)
        except OSError:
            return {conversation_id: [] for conversation_id in selected_ids}
        return paths_by_id

    def _is_recent(self, now: datetime, observed_at: datetime) -> bool:
        return (now - observed_at).total_seconds() <= self.activity_window_seconds

    def _read_session_signal(self, path: Path, fallback: datetime) -> SessionSignal | None:
        """Read only the tail event metadata, never prompts or response content."""
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 262_144))
                lines = handle.read().decode("utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("type") != "event_msg":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type")
            if event_type not in {"task_started", "task_complete"}:
                continue
            observed_at = self._parse_time(item.get("timestamp")) or fallback
            status = TaskStatus.COMPLETED if event_type == "task_complete" else TaskStatus.RUNNING
            return SessionSignal(status, observed_at)
        return None

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
