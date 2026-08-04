"""Discover opencode CLI tasks from its local SQLite database, read-only.

opencode persists session metadata and message-part snapshots in
``~/.local/share/opencode/opencode.db``. Official session status (idle/busy)
is a runtime SSE event and is not stored, so status is inferred from the
latest ``part`` snapshot. A ``tool`` part with ``state.status == "pending"``
only means the call was created but its arguments are still streaming;
permission requests are not persisted, so pending parts are never reported
as waiting for approval, and a live process with an unfinished turn always
reports running (never a false waiting-input state). Only ``type`` /
``state.status`` / ``time_updated`` are read from part data; prompts,
replies, tool commands and reasoning content are never touched.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

import psutil

from aacc.codex_discovery import DiscoveredTask
from aacc.kimi_discovery import Clock, ProcessAlive
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig

_NAME_MAX_LENGTH = 20
_OPENCODE_DB_NAMES = (
    "opencode.db",
    "opencode-stable.db",
    "opencode-beta.db",
    "opencode-nightly.db",
)
_SESSION_QUERY = """
SELECT id, title, directory, agent, model, time_created, time_updated
FROM session
WHERE time_archived IS NULL AND parent_id IS NULL
ORDER BY time_updated DESC
LIMIT 50
"""
_LATEST_PART_QUERY = """
SELECT
    CASE WHEN json_valid(data) THEN json_extract(data, '$.type') END,
    CASE WHEN json_valid(data) THEN json_extract(data, '$.state.status') END,
    time_updated
FROM part
WHERE session_id = ?
ORDER BY time_updated DESC, id DESC
LIMIT ?
"""
_PART_HISTORY_LIMIT = 64
_STREAMING_PART_TYPES = {"text", "reasoning", "patch", "step-start"}
_ERROR_TOOL_STATES = {"error", "failed", "rejected"}
_CANCELLED_TOOL_STATES = {"cancelled", "canceled"}

_logger = logging.getLogger("aacc.opencode_discovery")

ConnectFactory = Callable[[Path], sqlite3.Connection]


def _default_process_match(value: str) -> bool:
    if sys.platform == "win32":
        return re.match(r"^opencode(?:\.exe)?$", value, re.IGNORECASE) is not None
    return value == "opencode"


def _default_connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)


def default_opencode_db_paths(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, ...]:
    """Return the documented OpenCode data locations for the current platform."""
    current_platform = platform or sys.platform
    current_home = home or Path.home()
    current_environ = os.environ if environ is None else environ
    if current_platform == "win32":
        roots: list[Path] = []
        local_app_data = current_environ.get("LOCALAPPDATA")
        if local_app_data:
            roots.append(Path(local_app_data) / "opencode")
        # CLI installs may keep the XDG-compatible database under the profile.
        roots.append(current_home / ".local" / "share" / "opencode")
    else:
        data_root = current_environ.get("XDG_DATA_HOME")
        roots = [
            Path(data_root) / "opencode"
            if data_root
            else current_home / ".local" / "share" / "opencode"
        ]
    return tuple(root / name for root in roots for name in _OPENCODE_DB_NAMES)


def _default_db_path() -> Path:
    candidates = default_opencode_db_paths()
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _default_terminal_config(work_dir: str | None = None) -> TerminalConfig:
    if sys.platform == "win32":
        title = PureWindowsPath(work_dir).name if work_dir else "opencode"
        return TerminalConfig(type="windows_terminal", window_title=title or "opencode")
    return TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal")


def _normalize_process_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _matching_process_cwds() -> tuple[set[str], bool]:
    """Return matching OpenCode process working directories.

    The boolean is true when a matching process exists but its cwd could not be
    read. Such a process is only used as a conservative fallback for sessions
    without a known working directory; it must not keep every session alive.
    """
    working_dirs: set[str] = set()
    unreadable_cwd = False
    try:
        processes = psutil.process_iter(["name", "cwd"])
        for process in processes:
            name = process.info.get("name")
            if not isinstance(name, str) or not _default_process_match(name):
                continue
            cwd = process.info.get("cwd")
            if isinstance(cwd, str) and cwd:
                working_dirs.add(_normalize_process_path(cwd))
            else:
                unreadable_cwd = True
    except (psutil.Error, OSError):
        return set(), True
    return working_dirs, unreadable_cwd


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


SessionProcessAlive = Callable[[OpenCodeSession], bool]


@dataclass(frozen=True)
class OpenCodePartSnapshot:
    part_type: str | None
    state_status: str | None
    time_updated: datetime | None
    completed_at: datetime | None = None
    step_started_at: datetime | None = None
    running_at: datetime | None = None
    pending_at: datetime | None = None


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
        return OpenCodeSessionStatus(TaskStatus.STOPPED, "已停止", 0.8, None)
    state_status = (
        snapshot.state_status.casefold() if isinstance(snapshot.state_status, str) else None
    )
    if snapshot.part_type == "tool" and state_status == "pending":
        # "pending" only means the tool call was created but its arguments
        # have not finished streaming yet; execution starts (status flips to
        # "running") once streaming completes. opencode does not persist
        # permission requests, so pending never means waiting for approval.
        # A stale pending part with a live process is a slow or stuck stream
        # — the turn is still in flight, so it stays running.
        if not process_alive():
            return OpenCodeSessionStatus(TaskStatus.STOPPED, "已停止", 0.8, snapshot.time_updated)
        fresh = (now - snapshot.time_updated).total_seconds() <= activity_window_seconds
        confidence = 0.9 if fresh else 0.8
        return OpenCodeSessionStatus(
            TaskStatus.RUNNING, "正在运行", confidence, snapshot.time_updated
        )
    if snapshot.part_type == "tool" and state_status in _ERROR_TOOL_STATES:
        return OpenCodeSessionStatus(TaskStatus.ERROR, "执行失败", 0.96, snapshot.time_updated)
    if snapshot.part_type == "tool" and state_status in _CANCELLED_TOOL_STATES:
        return OpenCodeSessionStatus(TaskStatus.CANCELLED, "已取消", 0.96, snapshot.time_updated)
    if snapshot.part_type == "tool" and state_status == "running":
        if process_alive():
            return OpenCodeSessionStatus(
                TaskStatus.RUNNING, "正在运行", 0.95, snapshot.time_updated
            )
        return OpenCodeSessionStatus(TaskStatus.STOPPED, "已停止", 0.8, snapshot.time_updated)
    step_ended = snapshot.part_type == "step-finish" or (
        snapshot.part_type == "tool" and state_status == "completed"
    )
    if step_ended:
        stale = (now - snapshot.time_updated).total_seconds() > activity_window_seconds
        if stale or not process_alive():
            return OpenCodeSessionStatus(
                TaskStatus.COMPLETED, "回合已完成", 0.9, snapshot.time_updated
            )
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, snapshot.time_updated)
    if (
        snapshot.completed_at is not None
        and snapshot.step_started_at is not None
        and snapshot.completed_at >= snapshot.step_started_at
        and (now - snapshot.completed_at).total_seconds() > activity_window_seconds
    ):
        return OpenCodeSessionStatus(TaskStatus.COMPLETED, "回合已完成", 0.9, snapshot.time_updated)
    if not process_alive():
        return OpenCodeSessionStatus(TaskStatus.STOPPED, "已停止", 0.8, snapshot.time_updated)
    active = (now - snapshot.time_updated).total_seconds() <= activity_window_seconds
    if snapshot.part_type in _STREAMING_PART_TYPES and active:
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, snapshot.time_updated)
    if (
        snapshot.running_at is not None
        and (snapshot.completed_at is None or snapshot.running_at > snapshot.completed_at)
        and process_alive()
    ):
        # A tool that is still running inside the current step must keep the
        # session running even when a slightly newer text part shadows the
        # tool part in the "latest part" ordering.
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, snapshot.time_updated)
    if process_alive():
        # The turn is still in flight (long generation, slow or stuck stream)
        # and opencode persists no waiting-for-user signals — permission
        # prompts never reach the database — so a live process with an
        # unfinished turn always reports running, never a false yellow.
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.85, snapshot.time_updated)
    return OpenCodeSessionStatus(TaskStatus.STOPPED, "已停止", 0.8, snapshot.time_updated)


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
        process_alive_for_session: SessionProcessAlive | None = None,
        activity_window_seconds: float = 90.0,
        max_tasks: int = 20,
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        self.db_path = db_path or _default_db_path()
        self.now = now
        self.process_alive = process_alive
        self.process_alive_for_session = process_alive_for_session
        self.activity_window_seconds = max(10.0, activity_window_seconds)
        self.max_tasks = max(1, min(max_tasks, 20))
        self._connect = connect_factory or _default_connect

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        sessions = self._sessions()
        if selected_ids is not None:
            sessions = [session for session in sessions if session.session_id in selected_ids]
        now = self.now()
        process_alive: bool | None = None
        process_cwds: set[str] = set()
        unreadable_process_cwd = False
        if self.process_alive is None and self.process_alive_for_session is None:
            process_cwds, unreadable_process_cwd = _matching_process_cwds()

        def is_alive(session: OpenCodeSession) -> bool:
            nonlocal process_alive
            if self.process_alive_for_session is not None:
                return self.process_alive_for_session(session)
            if self.process_alive is not None:
                if process_alive is None:
                    process_alive = self.process_alive()
                return process_alive
            if session.work_dir:
                return _normalize_process_path(session.work_dir) in process_cwds
            return bool(process_cwds) or unreadable_process_cwd

        discovered: list[DiscoveredTask] = []
        snapshots = self._latest_part_snapshots([session.session_id for session in sessions])
        for session in sessions:

            def session_process_alive(current: OpenCodeSession = session) -> bool:
                return is_alive(current)

            evaluation = evaluate_opencode_session_status(
                snapshots.get(session.session_id),
                now=now,
                process_alive=session_process_alive,
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
                        terminal=_default_terminal_config(session.work_dir),
                    ),
                    state=TaskState(
                        task_id=task_id,
                        status=status,
                        message=evaluation.message,
                        source="opencode_local",
                        confidence=evaluation.confidence,
                        started_at=(activity_at if status is TaskStatus.RUNNING else None),
                        updated_at=updated_at,
                        finished_at=(
                            updated_at
                            if status
                            in {
                                TaskStatus.COMPLETED,
                                TaskStatus.ERROR,
                                TaskStatus.CANCELLED,
                                TaskStatus.STOPPED,
                            }
                            else None
                        ),
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
            if (
                task.state.status
                in {TaskStatus.RUNNING, TaskStatus.WAITING_INPUT, TaskStatus.WAITING_APPROVAL}
                and task.state.session_id is not None
            ):
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
                    rows = connection.execute(
                        _LATEST_PART_QUERY,
                        (session_id, _PART_HISTORY_LIMIT),
                    ).fetchall()
                    if not rows:
                        continue
                    part_type, state_status, latest_updated = rows[0]
                    if not isinstance(part_type, str):
                        continue
                    step_started_at: datetime | None = None
                    completed_at: datetime | None = None
                    running_at: datetime | None = None
                    pending_at: datetime | None = None
                    in_current_step = True
                    for candidate_type, candidate_status, raw_updated in rows:
                        if not isinstance(candidate_type, str):
                            continue
                        updated_at = _epoch_ms_to_datetime(raw_updated)
                        if candidate_type == "step-start":
                            step_started_at = max(
                                (step_started_at or datetime.min.replace(tzinfo=UTC)),
                                updated_at,
                            )
                        is_step_end = candidate_type == "step-finish" or (
                            candidate_type == "tool" and candidate_status == "completed"
                        )
                        if is_step_end:
                            completed_at = max(
                                (completed_at or datetime.min.replace(tzinfo=UTC)),
                                updated_at,
                            )
                            in_current_step = False
                            continue
                        if not in_current_step:
                            continue
                        candidate_state = (
                            candidate_status.casefold()
                            if isinstance(candidate_status, str)
                            else None
                        )
                        if candidate_type == "tool" and candidate_state == "running":
                            running_at = max(
                                (running_at or datetime.min.replace(tzinfo=UTC)),
                                updated_at,
                            )
                        if candidate_type == "tool" and candidate_state == "pending":
                            pending_at = max(
                                (pending_at or datetime.min.replace(tzinfo=UTC)),
                                updated_at,
                            )
                    snapshots[session_id] = OpenCodePartSnapshot(
                        part_type=part_type,
                        state_status=state_status if isinstance(state_status, str) else None,
                        time_updated=_epoch_ms_to_datetime(latest_updated),
                        completed_at=completed_at,
                        step_started_at=step_started_at,
                        running_at=running_at,
                        pending_at=pending_at,
                    )
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise OpenCodeDiscoveryError("opencode database is unreadable") from error
        return snapshots
