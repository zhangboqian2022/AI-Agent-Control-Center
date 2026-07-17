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


@dataclass(frozen=True)
class DiscoveredTask:
    config: TaskConfig
    state: TaskState


class CodexLocalDiscovery:
    """Reads only safe Codex task metadata from local index files."""

    def __init__(
        self,
        session_index_path: Path | None = None,
        process_manager_path: Path | None = None,
        *,
        pid_exists: PidExists = psutil.pid_exists,
        now: Clock = lambda: datetime.now(UTC),
        max_tasks: int = 20,
    ) -> None:
        codex_home = Path.home() / ".codex"
        self.session_index_path = session_index_path or codex_home / "session_index.jsonl"
        self.process_manager_path = process_manager_path or (
            codex_home / "process_manager" / "chat_processes.json"
        )
        self.pid_exists = pid_exists
        self.now = now
        self.max_tasks = max(1, min(max_tasks, 20))

    def discover(self) -> list[DiscoveredTask]:
        sessions = self._sessions()
        active_pids = self._active_pids()
        discovered: list[DiscoveredTask] = []
        for session in sessions:
            conversation_id = session["id"]
            pid = active_pids.get(conversation_id)
            is_active = pid is not None
            updated_at = session["updated_at"]
            if is_active:
                status = TaskStatus.RUNNING
                message = "检测到 Codex 正在运行"
                confidence = 0.92
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
                        started_at=updated_at if is_active else None,
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

    def _active_pids(self) -> dict[str, int]:
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
            if not isinstance(conversation_id, str) or not isinstance(pid, int) or pid <= 0:
                continue
            try:
                if self.pid_exists(pid):
                    pids[conversation_id] = pid
            except (OSError, psutil.Error):
                continue
        return pids

    def _parse_time(self, raw: object) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
