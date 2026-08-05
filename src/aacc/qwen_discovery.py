"""Discover Qwen Code CLI sessions from ``~/.qwen``.

Qwen Code (the aliyun gemini-cli fork) stores one JSONL transcript per chat
under ``~/.qwen/projects/<encoded-path>/chats/<uuid>.jsonl`` and, while a
session runs, a sibling ``<uuid>.runtime.json`` carrying the process id and
working directory. The CLI process itself is ``node`` (its command line
contains ``qwen-code``), so liveness is verified against the runtime pid —
never by process-name scans — and the runtime file is treated as stale once
that pid is gone, because it outlives process exit.

Transcripts contain sensitive content: only file mtimes, the runtime metadata
and the first line's ``cwd`` field are read. Message bodies are never stored,
displayed, logged, or uploaded.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from aacc.codex_discovery import DiscoveredTask
from aacc.kimi_metrics import decode_speed
from aacc.kimi_wire_usage import SessionUsage
from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig

Clock = Callable[[], datetime]
FileModifiedAt = Callable[[Path], datetime]
SessionProcessAlive = Callable[[int], bool]

_NAME_MAX_LENGTH = 20
_FIRST_LINE_BUDGET_BYTES = 16_384
# Same bound as the other discovery tails: oversized records are noise.
_USAGE_MAX_LINE_BYTES = 65_536


class QwenDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class QwenSession:
    session_id: str
    title: str
    updated_at: datetime
    work_dir: str | None = None


@dataclass(frozen=True)
class QwenSessionStatus:
    """Status evaluation for a single Qwen Code chat transcript."""

    status: TaskStatus
    message: str
    confidence: float
    activity_at: datetime | None


def _default_terminal_config(work_dir: str | None) -> TerminalConfig:
    """Terminal targeting for discovered Qwen Code tasks, per platform."""
    if sys.platform == "win32":
        title = Path(work_dir).name if work_dir else None
        return TerminalConfig(type="windows_terminal", window_title=title or None)
    return TerminalConfig(type="terminal_app", app_bundle_id="com.apple.Terminal")


def _default_session_process_alive(pid: int) -> bool:
    """Verify a runtime pid still belongs to a Qwen Code CLI process.

    The executable is ``node``; only the command line identifies the CLI.
    Unreadable processes fail closed (reported stopped) rather than guessing.
    """

    try:
        process = psutil.Process(pid)
        cmdline = process.cmdline()
    except (psutil.Error, OSError):
        return False
    joined = " ".join(cmdline)
    return "qwen-code" in joined or "qwen_code" in joined


def evaluate_qwen_session_status(
    *,
    runtime_pid: int | None,
    process_alive: bool,
    activity_at: datetime | None,
    now: datetime,
    activity_window_seconds: float,
) -> QwenSessionStatus:
    """Apply the Qwen Code status decision tree to one chat transcript.

    A live runtime pid is authoritative: fresh file activity means the turn
    is working (RUNNING), stale activity means the session waits at the
    prompt (IDLE). A dead or missing pid means the session is stopped, and a
    transcript with no runtime record at all is judged by file freshness.
    """

    fresh = activity_at is not None and (now - activity_at).total_seconds() <= (
        activity_window_seconds
    )
    if runtime_pid is not None:
        if process_alive:
            if fresh:
                return QwenSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, activity_at)
            return QwenSessionStatus(TaskStatus.IDLE, "空闲", 0.7, activity_at)
        return QwenSessionStatus(TaskStatus.STOPPED, "已停止", 0.9, activity_at)
    if fresh:
        return QwenSessionStatus(TaskStatus.RUNNING, "正在运行", 0.6, activity_at)
    return QwenSessionStatus(TaskStatus.STOPPED, "已停止", 0.8, activity_at)


class QwenUsageTracker:
    """Incremental per-session token usage from ``usage_record.jsonl``.

    Qwen Code appends one record per turn to a single global file, keyed by
    ``sessionId`` with per-model token counters. The file is tailed by byte
    offset exactly like the Kimi wire tracker; only ``sessionId`` and the
    numeric model counters are read, never tool/file/message details.
    """

    def __init__(self) -> None:
        self._offsets: dict[Path, int] = {}
        self._sessions: dict[str, SessionUsage] = {}

    def refresh(self, record_path: Path) -> None:
        try:
            size = record_path.stat().st_size
        except OSError:
            return
        offset = self._offsets.get(record_path, 0)
        if size < offset:
            offset = 0
        if size == offset:
            self._offsets[record_path] = size
            return
        try:
            with record_path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(size - offset)
        except OSError:
            return
        segments = data.split(b"\n")
        if data.endswith(b"\n"):
            complete_lines = segments[:-1]
            consumed = size
        else:
            complete_lines = segments[:-1]
            consumed = size - len(segments[-1])
        self._offsets[record_path] = consumed
        for line in complete_lines:
            self._consume(line)

    def usage_for(self, session_id: str) -> SessionUsage | None:
        return self._sessions.get(session_id)

    def _consume(self, line: bytes) -> None:
        if not line or len(line) > _USAGE_MAX_LINE_BYTES or b'"sessionId"' not in line:
            return
        try:
            item: Any = json.loads(line.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return
        if not isinstance(item, dict):
            return
        session_id = item.get("sessionId")
        models = item.get("models")
        if not isinstance(session_id, str) or not session_id or not isinstance(models, dict):
            return
        session = self._sessions.setdefault(session_id, SessionUsage())
        record_output = 0
        for counters in models.values():
            if not isinstance(counters, dict):
                continue
            session.input_tokens += _as_count(counters.get("inputTokens"))
            output = _as_count(counters.get("outputTokens"))
            session.output_tokens += output
            record_output += output
            session.cache_read_tokens += _as_count(counters.get("cachedTokens"))
        duration = item.get("totalLatencyMs")
        if duration is None:
            duration = item.get("durationMs")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            session.last_duration_ms = float(duration)
        session.speed.append(decode_speed(record_output, duration))


def _as_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


class QwenLocalDiscovery:
    """Reads only safe Qwen Code task metadata from local chat transcripts."""

    def __init__(
        self,
        qwen_home: Path | None = None,
        *,
        now: Clock = lambda: datetime.now(UTC),
        file_modified_at: FileModifiedAt | None = None,
        session_process_alive: SessionProcessAlive | None = None,
        activity_window_seconds: float = 90.0,
        max_tasks: int = 20,
        usage_tracker: QwenUsageTracker | None = None,
    ) -> None:
        self.qwen_home = qwen_home or Path.home() / ".qwen"
        self.projects_root = self.qwen_home / "projects"
        self.now = now
        self.file_modified_at = file_modified_at or self._file_modified_at
        self.session_process_alive = session_process_alive or _default_session_process_alive
        self.activity_window_seconds = max(10.0, activity_window_seconds)
        self.max_tasks = max(1, min(max_tasks, 20))
        self.usage_tracker = usage_tracker or QwenUsageTracker()

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        sessions = self._sessions()
        if selected_ids is not None:
            sessions = [session for session in sessions if session["id"] in selected_ids]
        self.usage_tracker.refresh(self.qwen_home / "usage_record.jsonl")
        now = self.now()
        discovered: list[DiscoveredTask] = []
        for session in sessions:
            runtime_pid = session["runtime_pid"]
            alive = self.session_process_alive(runtime_pid) if runtime_pid is not None else False
            evaluation = evaluate_qwen_session_status(
                runtime_pid=runtime_pid,
                process_alive=alive,
                activity_at=session["updated_at"],
                now=now,
                activity_window_seconds=self.activity_window_seconds,
            )
            status = evaluation.status
            updated_at = session["updated_at"]
            session_id = session["id"]
            task_id = f"qwen:{session_id}"
            work_dir = session["work_dir"]
            usage = self.usage_tracker.usage_for(session_id)
            discovered.append(
                DiscoveredTask(
                    config=TaskConfig(
                        id=task_id,
                        slot=1,
                        name=session["title"][:_NAME_MAX_LENGTH],
                        agent=AgentConfig(type="qwen_cli", display_name="Qwen Code"),
                        terminal=_default_terminal_config(work_dir),
                    ),
                    state=TaskState(
                        task_id=task_id,
                        status=status,
                        message=evaluation.message,
                        source="qwen_local",
                        confidence=evaluation.confidence,
                        started_at=updated_at if status is TaskStatus.RUNNING else None,
                        updated_at=updated_at,
                        finished_at=updated_at if status is TaskStatus.STOPPED else None,
                        pid=runtime_pid if runtime_pid is not None and alive else None,
                        session_id=session_id,
                        metadata={
                            "discovered": True,
                            **({"work_dir": work_dir} if work_dir else {}),
                            **(
                                {"usage": usage.to_metadata()}
                                if usage is not None
                                and (usage.total_input_tokens or usage.output_tokens)
                                else {}
                            ),
                        },
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

    def catalog(self) -> list[QwenSession]:
        return [
            QwenSession(
                session_id=session["id"],
                title=session["title"],
                updated_at=session["updated_at"],
                work_dir=session["work_dir"],
            )
            for session in sorted(
                self._sessions(), key=lambda item: item["updated_at"], reverse=True
            )
        ]

    def active_session_ids(self, *, limit: int = 4) -> set[str]:
        """Return a small set of running sessions for auto-monitoring."""
        active: set[str] = set()
        for task in self.discover():
            if task.state.status is TaskStatus.RUNNING and task.state.session_id is not None:
                active.add(task.state.session_id)
                if len(active) >= max(1, limit):
                    break
        return active

    def _sessions(self) -> list[dict[str, Any]]:
        if not self.projects_root.exists():
            return []
        sessions: list[dict[str, Any]] = []
        try:
            project_dirs = list(self.projects_root.iterdir())
        except OSError as error:
            raise QwenDiscoveryError("Qwen projects directory is unreadable") from error
        for project_dir in project_dirs:
            chats_dir = project_dir / "chats"
            if not chats_dir.is_dir():
                continue
            try:
                chat_files = list(chats_dir.glob("*.jsonl"))
            except OSError:
                continue
            for chat_file in chat_files:
                session = self._read_session(chat_file)
                if session is not None:
                    sessions.append(session)
        return sessions

    def _read_session(self, chat_file: Path) -> dict[str, Any] | None:
        session_id = chat_file.stem
        runtime = self._read_runtime(chat_file.with_name(f"{session_id}.runtime.json"))
        try:
            updated_at = self.file_modified_at(chat_file)
        except OSError:
            return None
        work_dir = runtime.get("work_dir") if runtime else None
        if not isinstance(work_dir, str) or not work_dir:
            work_dir = self._first_line_cwd(chat_file)
        title = Path(work_dir).name if work_dir else f"Qwen 任务 {session_id[:8]}"
        pid = runtime.get("pid") if runtime else None
        return {
            "id": session_id,
            "runtime_pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
            "work_dir": work_dir,
            "title": title[:120],
            "updated_at": updated_at,
        }

    def _read_runtime(self, runtime_path: Path) -> dict[str, Any] | None:
        try:
            raw = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _first_line_cwd(self, chat_file: Path) -> str | None:
        """Read only the first line's ``cwd`` field; content is never kept."""
        try:
            with chat_file.open("rb") as handle:
                line = handle.readline(_FIRST_LINE_BUDGET_BYTES)
        except OSError:
            return None
        try:
            item = json.loads(line.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return None
        if not isinstance(item, dict):
            return None
        cwd = item.get("cwd")
        return cwd if isinstance(cwd, str) and cwd else None

    @staticmethod
    def _file_modified_at(path: Path) -> datetime:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
