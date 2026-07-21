from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from aacc.codex_discovery import DiscoveredTask
from aacc.kimi_discovery import (
    Clock,
    FileModifiedAt,
    ProcessAlive,
    evaluate_kimi_session_status,
)
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig

_DAIMON_ROOT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "kimi-desktop"
    / "daimon-share"
    / "daimon"
)
_CONVERSATIONS_QUERY = """
SELECT conversation_id, title, updated_at_ms, kernel_session_dir, workspace_path
FROM conversations
"""
_NAME_MAX_LENGTH = 20


class KimiDesktopDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class KimiDesktopSession:
    session_id: str
    title: str
    updated_at: datetime


class KimiDesktopLocalDiscovery:
    """Reads only safe Kimi Desktop (daimon) conversation metadata.

    The conversations sqlite is opened read-only and only metadata
    columns are selected — prompts, responses and other content columns are
    never read. Agent conversations reuse the Kimi Code session-status
    evaluation against their kernel session directory; chat conversations
    use simplified updated_at freshness semantics.
    """

    def __init__(
        self,
        daimon_root: Path | None = None,
        *,
        now: Clock = lambda: datetime.now(UTC),
        file_modified_at: FileModifiedAt | None = None,
        app_process_alive: ProcessAlive | None = None,
        activity_window_seconds: float = 90.0,
        active_turn_window_seconds: float = 1800.0,
        max_tasks: int = 20,
    ) -> None:
        self.daimon_root = daimon_root or _DAIMON_ROOT
        self.conversations_path = (
            self.daimon_root
            / "agents"
            / "main"
            / "sessions"
            / "hosted-logical"
            / "conversations.sqlite"
        )
        self.now = now
        self.file_modified_at = file_modified_at or self._file_modified_at
        self.app_process_alive = app_process_alive or self._app_process_alive
        self.activity_window_seconds = max(10.0, activity_window_seconds)
        self.active_turn_window_seconds = max(
            self.activity_window_seconds, active_turn_window_seconds
        )
        self.max_tasks = max(1, min(max_tasks, 20))

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        conversations = self._conversations()
        if selected_ids is not None:
            conversations = [
                conversation
                for conversation in conversations
                if conversation["id"] in selected_ids
            ]
        now = self.now()
        process_alive: bool | None = None

        def is_app_alive() -> bool:
            nonlocal process_alive
            if process_alive is None:
                process_alive = self.app_process_alive()
            return process_alive

        discovered: list[DiscoveredTask] = []
        for conversation in conversations:
            kernel_dir = conversation["kernel_session_dir"]
            if kernel_dir is not None:
                evaluation = evaluate_kimi_session_status(
                    kernel_dir,
                    now=now,
                    file_modified_at=self.file_modified_at,
                    process_alive=is_app_alive,
                    activity_window_seconds=self.activity_window_seconds,
                    active_turn_window_seconds=self.active_turn_window_seconds,
                )
                status = evaluation.status
                message = evaluation.message
                confidence = evaluation.confidence
                activity_at = evaluation.activity_at
            else:
                activity_at = None
                if self._is_recent(now, conversation["updated_at"]):
                    status = TaskStatus.RUNNING
                    message = "正在生成回复"
                    confidence = 0.9
                elif is_app_alive():
                    status = TaskStatus.IDLE
                    message = "空闲"
                    confidence = 0.7
                else:
                    status = TaskStatus.UNKNOWN
                    message = "未检测到运行进程"
                    confidence = 0.55
            updated_at = (
                activity_at
                if activity_at is not None
                else conversation["updated_at"]
            )
            task_id = f"kimi_desktop:{conversation['id']}"
            discovered.append(
                DiscoveredTask(
                    config=TaskConfig(
                        id=task_id,
                        slot=1,
                        name=(
                            conversation["title"]
                            or f"Kimi Desktop 任务 {conversation['id'][:8]}"
                        )[:_NAME_MAX_LENGTH],
                        agent=AgentConfig(
                            type="kimi_desktop", display_name="Kimi Desktop"
                        ),
                        terminal=TerminalConfig(
                            type="mac_app",
                            app_bundle_id="com.moonshot.kimichat",
                        ),
                    ),
                    state=TaskState(
                        task_id=task_id,
                        status=status,
                        message=message,
                        source="kimi_desktop_local",
                        confidence=confidence,
                        started_at=(
                            activity_at if status is TaskStatus.RUNNING else None
                        ),
                        updated_at=updated_at,
                        finished_at=(
                            updated_at if status is TaskStatus.COMPLETED else None
                        ),
                        pid=None,
                        session_id=conversation["id"],
                        metadata={"discovered": True},
                    ),
                )
            )
        discovered.sort(
            key=lambda item: (
                item.state.status is TaskStatus.RUNNING,
                item.state.updated_at,
            ),
            reverse=True,
        )
        return [
            DiscoveredTask(
                config=item.config.model_copy(update={"slot": slot}),
                state=item.state,
            )
            for slot, item in enumerate(discovered[: self.max_tasks], start=1)
        ]

    def catalog(self) -> list[KimiDesktopSession]:
        return [
            KimiDesktopSession(
                session_id=conversation["id"],
                title=(
                    conversation["title"]
                    or f"Kimi Desktop 任务 {conversation['id'][:8]}"
                ),
                updated_at=conversation["updated_at"],
            )
            for conversation in sorted(
                self._conversations(),
                key=lambda item: item["updated_at"],
                reverse=True,
            )
        ]

    def active_session_ids(self, *, limit: int = 4) -> set[str]:
        """Return a small set of recently verified active conversations."""
        active: set[str] = set()
        for task in self.discover():
            if (
                task.state.status is TaskStatus.RUNNING
                and task.state.session_id is not None
            ):
                active.add(task.state.session_id)
                if len(active) >= max(1, limit):
                    break
        return active

    def _conversations(self) -> list[dict[str, Any]]:
        if not self.conversations_path.exists():
            return []
        try:
            connection = sqlite3.connect(
                f"file:{self.conversations_path}?mode=ro", uri=True
            )
            try:
                rows = connection.execute(_CONVERSATIONS_QUERY).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise KimiDesktopDiscoveryError(
                "Kimi Desktop conversations index is unreadable"
            ) from error
        conversations: list[dict[str, Any]] = []
        for conversation_id, title, updated_at_ms, kernel_session_dir, _ in rows:
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            kernel_dir = (
                Path(kernel_session_dir)
                if isinstance(kernel_session_dir, str) and kernel_session_dir
                else None
            )
            conversations.append(
                {
                    "id": conversation_id,
                    "title": title if isinstance(title, str) else "",
                    "updated_at": self._updated_at(updated_at_ms),
                    "kernel_session_dir": kernel_dir,
                }
            )
        return conversations

    def _updated_at(self, updated_at_ms: object) -> datetime:
        if isinstance(updated_at_ms, (int, float)) and updated_at_ms > 0:
            return datetime.fromtimestamp(updated_at_ms / 1000, UTC)
        return datetime.min.replace(tzinfo=UTC)

    def _is_recent(self, now: datetime, observed_at: datetime) -> bool:
        return (now - observed_at).total_seconds() <= self.activity_window_seconds

    @staticmethod
    def _file_modified_at(path: Path) -> datetime:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)

    @staticmethod
    def _app_process_alive() -> bool:
        try:
            for process in psutil.process_iter(["exe"]):
                exe = process.info.get("exe")
                if isinstance(exe, str) and "/Kimi.app/" in exe:
                    return True
        except (psutil.Error, OSError):
            return False
        return False
