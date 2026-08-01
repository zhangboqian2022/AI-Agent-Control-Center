"""Discover opencode CLI tasks from its local SQLite database, read-only.

opencode persists session metadata and message-part snapshots in
``~/.local/share/opencode/opencode.db``. Official session status (idle/busy)
is a runtime SSE event and is not stored, so status is inferred from the
latest ``part`` snapshot: a ``tool`` part with ``state.status == "pending"``
means the session is waiting for the user to approve a permission request.
Only ``type`` / ``state.status`` / ``time_updated`` are read from part data;
prompts, replies, tool commands and reasoning content are never touched.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aacc.codex_discovery import DiscoveredTask
from aacc.kimi_discovery import Clock, ProcessAlive
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig
from aacc.processes import CachedProcessAlive

_NAME_MAX_LENGTH = 20
_DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
_SESSION_QUERY = """
SELECT id, title, directory, agent, model, time_created, time_updated
FROM session
WHERE time_archived IS NULL AND parent_id IS NULL
ORDER BY time_updated DESC
LIMIT 50
"""
_LATEST_PART_QUERY = """
SELECT data, time_updated FROM part
WHERE session_id = ?
ORDER BY time_updated DESC, id DESC
LIMIT 1
"""
_STREAMING_PART_TYPES = {"text", "reasoning", "patch", "step-start"}
_STEP_END_PART_TYPES = {"step-finish", "tool"}

_logger = logging.getLogger("aacc.opencode_discovery")

ConnectFactory = Callable[[Path], sqlite3.Connection]


def _default_process_match(value: str) -> bool:
    if sys.platform == "win32":
        return re.match(r"^opencode(?:\.exe)?$", value, re.IGNORECASE) is not None
    return value == "opencode"


def _default_connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)


def _default_terminal_config() -> TerminalConfig:
    if sys.platform == "win32":
        return TerminalConfig(type="windows_terminal", window_title="opencode")
    return TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal")


def _epoch_ms_to_datetime(value: object) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / 1000, UTC)
    return datetime.min.replace(tzinfo=UTC)


class OpenCodeDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenCodeSession:
    session_id: str
    title: str
    work_dir: str | None
    agent: str | None
    model: str | None
    updated_at: datetime


@dataclass(frozen=True)
class OpenCodePartSnapshot:
    part_type: str | None
    state_status: str | None
    time_updated: datetime | None


@dataclass(frozen=True)
class OpenCodeSessionStatus:
    status: TaskStatus
    message: str
    confidence: float
    activity_at: datetime | None


def evaluate_opencode_session_status(
    snapshot: OpenCodePartSnapshot | None,
    *,
    now: datetime,
    process_alive: ProcessAlive,
    activity_window_seconds: float,
) -> OpenCodeSessionStatus:
    """Apply the opencode status decision tree to one part snapshot."""
    if snapshot is None or snapshot.part_type is None or snapshot.time_updated is None:
        if process_alive():
            return OpenCodeSessionStatus(TaskStatus.IDLE, "空闲", 0.7, None)
        return OpenCodeSessionStatus(TaskStatus.UNKNOWN, "未检测到运行进程", 0.55, None)
    if snapshot.part_type == "tool" and snapshot.state_status == "pending":
        fresh = (now - snapshot.time_updated).total_seconds() <= activity_window_seconds
        if fresh:
            return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, snapshot.time_updated)
        return OpenCodeSessionStatus(
            TaskStatus.WAITING_APPROVAL, "等待同意", 0.97, snapshot.time_updated
        )
    if snapshot.part_type == "tool" and snapshot.state_status == "running":
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.95, snapshot.time_updated)
    active = (now - snapshot.time_updated).total_seconds() <= activity_window_seconds
    if snapshot.part_type in _STREAMING_PART_TYPES and active:
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, snapshot.time_updated)
    if snapshot.part_type in _STEP_END_PART_TYPES:
        return OpenCodeSessionStatus(TaskStatus.COMPLETED, "回合已完成", 0.9, snapshot.time_updated)
    if process_alive():
        return OpenCodeSessionStatus(
            TaskStatus.WAITING_INPUT, "等待输入", 0.85, snapshot.time_updated
        )
    return OpenCodeSessionStatus(TaskStatus.COMPLETED, "已完成", 0.92, snapshot.time_updated)


class OpenCodeLocalDiscovery:
    """Reads only safe opencode task metadata from its local SQLite database.

    The database is opened read-only (never immutable — WAL replay keeps the
    snapshot fresh) with short-lived connections. Part data is parsed for
    ``type`` / ``state.status`` / ``time_updated`` only; prompts, replies,
    tool commands and reasoning content are never stored or displayed.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        now: Clock = lambda: datetime.now(UTC),
        process_alive: ProcessAlive | None = None,
        activity_window_seconds: float = 90.0,
        max_tasks: int = 20,
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.now = now
        self.process_alive = process_alive or CachedProcessAlive("name", _default_process_match)
        self.activity_window_seconds = max(10.0, activity_window_seconds)
        self.max_tasks = max(1, min(max_tasks, 20))
        self._connect = connect_factory or _default_connect

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        sessions = self._sessions()
        if selected_ids is not None:
            sessions = [session for session in sessions if session.session_id in selected_ids]
        now = self.now()
        process_alive: bool | None = None

        def is_alive() -> bool:
            nonlocal process_alive
            if process_alive is None:
                process_alive = self.process_alive()
            return process_alive

        discovered: list[DiscoveredTask] = []
        snapshots = self._latest_part_snapshots([session.session_id for session in sessions])
        for session in sessions:
            evaluation = evaluate_opencode_session_status(
                snapshots.get(session.session_id),
                now=now,
                process_alive=is_alive,
                activity_window_seconds=self.activity_window_seconds,
            )
            activity_at = evaluation.activity_at
            updated_at = activity_at if activity_at is not None else session.updated_at
            status = evaluation.status
            task_id = f"opencode:{session.session_id}"
            metadata: dict[str, Any] = {"discovered": True}
            if session.work_dir:
                metadata["work_dir"] = session.work_dir
            if session.agent:
                metadata["agent"] = session.agent
            discovered.append(
                DiscoveredTask(
                    config=TaskConfig(
                        id=task_id,
                        slot=1,
                        name=(session.title or f"OpenCode 任务 {session.session_id[:8]}")[
                            :_NAME_MAX_LENGTH
                        ],
                        agent=AgentConfig(type="opencode_cli", display_name="OpenCode"),
                        terminal=_default_terminal_config(),
                    ),
                    state=TaskState(
                        task_id=task_id,
                        status=status,
                        message=evaluation.message,
                        source="opencode_local",
                        confidence=evaluation.confidence,
                        started_at=(activity_at if status is TaskStatus.RUNNING else None),
                        updated_at=updated_at,
                        finished_at=(updated_at if status is TaskStatus.COMPLETED else None),
                        pid=None,
                        session_id=session.session_id,
                        metadata=metadata,
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

    def catalog(self) -> list[OpenCodeSession]:
        return sorted(self._sessions(), key=lambda item: item.updated_at, reverse=True)

    def active_session_ids(self, *, limit: int = 4) -> set[str]:
        active: set[str] = set()
        for task in self.discover():
            if task.state.status is TaskStatus.RUNNING and task.state.session_id is not None:
                active.add(task.state.session_id)
                if len(active) >= max(1, limit):
                    break
        return active

    def _sessions(self) -> list[OpenCodeSession]:
        if not self.db_path.exists():
            return []
        try:
            connection = self._connect(self.db_path)
            try:
                rows = connection.execute(_SESSION_QUERY).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise OpenCodeDiscoveryError("opencode database is unreadable") from error
        sessions: list[OpenCodeSession] = []
        for session_id, title, directory, agent, model, _, updated_ms in rows:
            if not isinstance(session_id, str) or not session_id:
                continue
            sessions.append(
                OpenCodeSession(
                    session_id=session_id,
                    title=title if isinstance(title, str) else "",
                    work_dir=directory if isinstance(directory, str) and directory else None,
                    agent=agent if isinstance(agent, str) and agent else None,
                    model=model if isinstance(model, str) and model else None,
                    updated_at=_epoch_ms_to_datetime(updated_ms),
                )
            )
        return sessions

    def _latest_part_snapshots(self, session_ids: list[str]) -> dict[str, OpenCodePartSnapshot]:
        if not session_ids or not self.db_path.exists():
            return {}
        snapshots: dict[str, OpenCodePartSnapshot] = {}
        try:
            connection = self._connect(self.db_path)
            try:
                for session_id in session_ids:
                    row = connection.execute(_LATEST_PART_QUERY, (session_id,)).fetchone()
                    if row is None:
                        continue
                    parsed = self._parse_part_snapshot(row[0])
                    if parsed is not None:
                        snapshots[session_id] = OpenCodePartSnapshot(
                            part_type=parsed.part_type,
                            state_status=parsed.state_status,
                            time_updated=_epoch_ms_to_datetime(row[1]),
                        )
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise OpenCodeDiscoveryError("opencode database is unreadable") from error
        return snapshots

    @staticmethod
    def _parse_part_snapshot(data: object) -> OpenCodePartSnapshot | None:
        if not isinstance(data, str) or not data:
            return None
        try:
            import json

            parsed = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        part_type = parsed.get("type")
        if not isinstance(part_type, str):
            return None
        state = parsed.get("state")
        state_status: str | None = None
        if isinstance(state, dict):
            candidate = state.get("status")
            if isinstance(candidate, str):
                state_status = candidate
        return OpenCodePartSnapshot(
            part_type=part_type,
            state_status=state_status,
            time_updated=None,
        )
