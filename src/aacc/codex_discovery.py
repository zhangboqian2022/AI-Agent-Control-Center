from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import psutil

from aacc.models import AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig

PidExists = Callable[[int], bool]
Clock = Callable[[], datetime]
SessionModifiedAt = Callable[[Path], datetime]
ProcessStartedAt = Callable[[int], int | None]
CODEX_METADATA_COMPATIBILITY = "2026-07"
MAX_SESSION_METADATA_LINE_BYTES = 65_536
SESSION_SCAN_CHUNK_BYTES = 65_536
SESSION_START_CACHE_LIMIT = 512


class CodexDiscoveryError(RuntimeError):
    pass


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
    message: str
    started_at: datetime | None = None


@dataclass(frozen=True)
class SessionStartCache:
    scanned_size: int
    started_at: datetime | None
    device: int
    inode: int
    boundary_digest: bytes
    start_offset: int | None
    start_digest: bytes | None
    modified_ns: int
    changed_ns: int


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
        self._session_start_cache: dict[Path, SessionStartCache] = {}

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
                message = signal.message
                confidence = 0.96
            elif signal is not None and signal.status in {
                TaskStatus.RUNNING,
                TaskStatus.WAITING_INPUT,
                TaskStatus.WAITING_APPROVAL,
            }:
                status = signal.status
                message = signal.message
                confidence = 0.9
            elif pid is not None:
                status = TaskStatus.RUNNING
                message = "正在分析任务"
                confidence = 0.88
            else:
                status = TaskStatus.UNKNOWN
                message = "最近更新，未检测到运行进程"
                confidence = 0.55
            task_id = f"codex:{conversation_id}"
            timed_statuses = {
                TaskStatus.RUNNING,
                TaskStatus.WAITING_INPUT,
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.COMPLETED,
            }
            started_at = signal.started_at if signal is not None else None
            if started_at is None and status in timed_statuses - {TaskStatus.COMPLETED}:
                started_at = updated_at
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
                        started_at=started_at if status in timed_statuses else None,
                        updated_at=updated_at,
                        finished_at=updated_at if status is TaskStatus.COMPLETED else None,
                        pid=pid,
                        session_id=conversation_id,
                        metadata={
                            "discovered": True,
                            "source_event_at": updated_at.isoformat(),
                        },
                    ),
                )
            )
        discovered.sort(
            key=lambda item: (
                item.state.status
                in {
                    TaskStatus.RUNNING,
                    TaskStatus.WAITING_INPUT,
                    TaskStatus.WAITING_APPROVAL,
                },
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
            if (
                task.state.status
                in {
                    TaskStatus.RUNNING,
                    TaskStatus.WAITING_INPUT,
                    TaskStatus.WAITING_APPROVAL,
                }
                and task.state.session_id is not None
            ):
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
            raise CodexDiscoveryError("Codex session index is unreadable") from error
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
                signals[conversation_id] = SessionSignal(
                    TaskStatus.RUNNING, latest_at, "正在分析任务"
                )
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

    @staticmethod
    def _command_activity(payload: dict[str, Any]) -> str:
        category = payload.get("command_category")
        if category == "test":
            return "正在运行测试"
        if category in {"build", "package"}:
            return "正在构建程序"
        if category in {"inspect", "read"}:
            return "正在检查代码"
        return "正在执行命令"

    @classmethod
    def _activity_message(cls, item: dict[str, Any]) -> str | None:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            return None
        event_type = payload.get("type")
        if event_type in {"patch_apply_begin", "patch_apply_end"}:
            return "正在修改代码"
        if item.get("type") != "response_item" or event_type != "custom_tool_call":
            return None
        raw_name = payload.get("name")
        if not isinstance(raw_name, str):
            return None
        name = raw_name.casefold()
        if any(marker in name for marker in ("patch", "write", "edit")):
            return "正在修改代码"
        if any(marker in name for marker in ("web", "browser", "chrome", "search")):
            return "正在查询资料"
        if any(marker in name for marker in ("read", "view", "open", "list", "find")):
            return "正在检查代码"
        if name in {"exec", "exec_command", "functions.exec"}:
            return cls._command_activity(payload)
        return None

    def _read_session_signal(self, path: Path, fallback: datetime) -> SessionSignal | None:
        """Read bounded tail metadata and return only fixed, privacy-safe labels."""
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 262_144))
                lines = handle.read().decode("utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        latest_activity: tuple[TaskStatus, datetime, str] | None = None
        terminal_at: datetime | None = None
        terminal_started_at: datetime | None = None
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            event_type = payload.get("type")
            observed_at = self._parse_time(item.get("timestamp")) or fallback
            if item.get("type") == "event_msg" and event_type == "task_complete":
                if latest_activity is not None:
                    status, activity_at, message = latest_activity
                    return self._finalize_session_signal(
                        path, size, SessionSignal(status, activity_at, message), fallback
                    )
                terminal_at = observed_at
                terminal_started_at = self._parse_time(payload.get("started_at"))
                if terminal_started_at is not None:
                    return self._finalize_session_signal(
                        path,
                        size,
                        SessionSignal(
                            TaskStatus.COMPLETED,
                            terminal_at,
                            "已完成",
                            terminal_started_at,
                        ),
                        fallback,
                    )
                continue
            if item.get("type") == "event_msg" and event_type == "task_started":
                started_at = self._parse_time(payload.get("started_at")) or observed_at
                if terminal_at is not None:
                    return self._finalize_session_signal(
                        path,
                        size,
                        SessionSignal(TaskStatus.COMPLETED, terminal_at, "已完成", started_at),
                        fallback,
                    )
                if latest_activity is not None:
                    status, activity_at, message = latest_activity
                    return self._finalize_session_signal(
                        path,
                        size,
                        SessionSignal(status, activity_at, message, started_at),
                        fallback,
                    )
                return self._finalize_session_signal(
                    path,
                    size,
                    SessionSignal(TaskStatus.RUNNING, observed_at, "正在分析任务", started_at),
                    fallback,
                )
            if terminal_at is not None or latest_activity is not None:
                continue
            waiting_status = (
                {
                    "request_user_input": TaskStatus.WAITING_INPUT,
                    "waiting_input": TaskStatus.WAITING_INPUT,
                    "input_required": TaskStatus.WAITING_INPUT,
                    "approval_request": TaskStatus.WAITING_APPROVAL,
                    "request_approval": TaskStatus.WAITING_APPROVAL,
                    "waiting_approval": TaskStatus.WAITING_APPROVAL,
                    "exec_approval_request": TaskStatus.WAITING_APPROVAL,
                    "apply_patch_approval_request": TaskStatus.WAITING_APPROVAL,
                    "patch_approval_request": TaskStatus.WAITING_APPROVAL,
                    "request_permissions": TaskStatus.WAITING_APPROVAL,
                }.get(event_type)
                if isinstance(event_type, str)
                else None
            )
            if waiting_status is not None:
                latest_activity = (waiting_status, observed_at, "等待你的确认")
                continue
            activity_message = self._activity_message(item)
            if activity_message is not None:
                latest_activity = (TaskStatus.RUNNING, observed_at, activity_message)
                continue
            if event_type in {
                "agent_reasoning",
                "reasoning",
                "future_activity",
                "custom_tool_call",
            }:
                latest_activity = (TaskStatus.RUNNING, observed_at, "正在分析任务")
        if terminal_at is not None:
            return self._finalize_session_signal(
                path,
                size,
                SessionSignal(TaskStatus.COMPLETED, terminal_at, "已完成", terminal_started_at),
                fallback,
            )
        if latest_activity is not None:
            status, observed_at, message = latest_activity
            return self._finalize_session_signal(
                path,
                size,
                SessionSignal(status, observed_at, message),
                fallback,
            )
        return None

    def _finalize_session_signal(
        self,
        path: Path,
        size: int,
        signal: SessionSignal,
        fallback: datetime,
    ) -> SessionSignal:
        started_at = signal.started_at
        recovered_start = self._latest_task_start(path, size, fallback)
        if started_at is None:
            started_at = recovered_start
        elif recovered_start != started_at:
            self._store_session_start(path, size, started_at)
        return SessionSignal(
            signal.status,
            signal.observed_at,
            signal.message,
            started_at,
        )

    def _latest_task_start(self, path: Path, size: int, fallback: datetime) -> datetime | None:
        try:
            stat = path.stat()
            complete_size = self._complete_line_boundary(path, size)
        except OSError:
            return None
        cached = self._session_start_cache.get(path)
        cache_valid = (
            cached is not None
            and cached.device == stat.st_dev
            and cached.inode == stat.st_ino
            and 0 <= cached.scanned_size <= complete_size
            and cached.boundary_digest == self._boundary_digest(path, cached.scanned_size)
            and self._cached_start_matches(path, cached)
            and not (
                cached.scanned_size == complete_size
                and (
                    cached.modified_ns != stat.st_mtime_ns or cached.changed_ns != stat.st_ctime_ns
                )
            )
        )
        if cache_valid and cached is not None:
            started_at = cached.started_at
            start_offset = cached.start_offset
            start_digest = cached.start_digest
            if cached.scanned_size == complete_size:
                return started_at
            try:
                with path.open("rb") as handle:
                    yield_lines = self._forward_lines(handle, cached.scanned_size, complete_size)
                    for offset, line in yield_lines:
                        candidate = self._task_start_from_line(line, fallback)
                        if candidate is not None:
                            started_at = candidate
                            start_offset = offset
                            start_digest = self._line_digest(line)
            except OSError:
                return started_at
            self._store_session_start(
                path,
                complete_size,
                started_at,
                start_offset=start_offset,
                start_digest=start_digest,
            )
            return started_at

        started_at = None
        start_offset = None
        start_digest = None
        try:
            with path.open("rb") as handle:
                for offset, line in self._reverse_lines(handle, complete_size):
                    started_at = self._task_start_from_line(line, fallback)
                    if started_at is not None:
                        start_offset = offset
                        start_digest = self._line_digest(line)
                        break
        except OSError:
            return None
        self._store_session_start(
            path,
            complete_size,
            started_at,
            start_offset=start_offset,
            start_digest=start_digest,
        )
        return started_at

    @staticmethod
    def _complete_line_boundary(path: Path, size: int) -> int:
        with path.open("rb") as handle:
            position = size
            while position > 0:
                read_size = min(SESSION_SCAN_CHUNK_BYTES, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    return position + newline + 1
        return 0

    @staticmethod
    def _boundary_digest(path: Path, boundary: int) -> bytes:
        start = max(0, boundary - 4096)
        with path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(boundary - start)
        return hashlib.blake2b(raw, digest_size=16).digest()

    def _store_session_start(
        self,
        path: Path,
        scanned_size: int,
        started_at: datetime | None,
        *,
        start_offset: int | None = None,
        start_digest: bytes | None = None,
    ) -> None:
        try:
            stat = path.stat()
            complete_size = self._complete_line_boundary(path, scanned_size)
            digest = self._boundary_digest(path, complete_size)
        except OSError:
            return
        entry = SessionStartCache(
            scanned_size=complete_size,
            started_at=started_at,
            device=stat.st_dev,
            inode=stat.st_ino,
            boundary_digest=digest,
            start_offset=start_offset,
            start_digest=start_digest,
            modified_ns=stat.st_mtime_ns,
            changed_ns=stat.st_ctime_ns,
        )
        self._session_start_cache.pop(path, None)
        self._session_start_cache[path] = entry
        while len(self._session_start_cache) > SESSION_START_CACHE_LIMIT:
            oldest = next(iter(self._session_start_cache))
            self._session_start_cache.pop(oldest, None)

    @staticmethod
    def _line_digest(line: bytes) -> bytes:
        return hashlib.blake2b(line, digest_size=16).digest()

    @classmethod
    def _cached_start_matches(cls, path: Path, cached: SessionStartCache) -> bool:
        if cached.start_offset is None or cached.start_digest is None:
            return True
        try:
            with path.open("rb") as handle:
                handle.seek(cached.start_offset)
                line = handle.readline(MAX_SESSION_METADATA_LINE_BYTES + 1)
        except OSError:
            return False
        line = line.rstrip(b"\r\n")
        return (
            len(line) <= MAX_SESSION_METADATA_LINE_BYTES
            and cls._line_digest(line) == cached.start_digest
        )

    @staticmethod
    def _forward_lines(handle: BinaryIO, start: int, end: int) -> Iterator[tuple[int, bytes]]:
        handle.seek(start)
        while handle.tell() < end:
            line_offset = handle.tell()
            remaining = end - handle.tell()
            line = handle.readline(min(MAX_SESSION_METADATA_LINE_BYTES + 1, remaining))
            if not line:
                break
            oversized = len(line) > MAX_SESSION_METADATA_LINE_BYTES
            line_complete = line.endswith(b"\n")
            if oversized and not line_complete:
                while handle.tell() < end:
                    remaining = end - handle.tell()
                    fragment = handle.readline(min(MAX_SESSION_METADATA_LINE_BYTES + 1, remaining))
                    if not fragment or fragment.endswith(b"\n"):
                        break
                continue
            line = line.rstrip(b"\r\n")
            if line and not oversized:
                yield line_offset, line

    @staticmethod
    def _reverse_lines(handle: BinaryIO, size: int) -> Iterator[tuple[int, bytes]]:
        position = size
        fragments: deque[bytes] = deque()
        line_size = 0
        line_start = size
        dropping_oversized = False
        while position > 0:
            read_size = min(SESSION_SCAN_CHUNK_BYTES, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            segments = chunk.split(b"\n")
            segment_offsets: list[int] = []
            cursor = position
            for segment in segments:
                segment_offsets.append(cursor)
                cursor += len(segment) + 1
            for index in range(len(segments) - 1, -1, -1):
                segment = segments[index]
                line_start = segment_offsets[index]
                if not dropping_oversized:
                    line_size += len(segment)
                    if line_size > MAX_SESSION_METADATA_LINE_BYTES:
                        dropping_oversized = True
                        fragments.clear()
                    else:
                        fragments.appendleft(segment)
                if index > 0:
                    if not dropping_oversized and line_size:
                        yield line_start, b"".join(fragments)
                    fragments.clear()
                    line_size = 0
                    dropping_oversized = False
        if not dropping_oversized and line_size:
            yield line_start, b"".join(fragments)

    def _task_start_from_line(self, raw_line: bytes, fallback: datetime) -> datetime | None:
        if (
            len(raw_line) > MAX_SESSION_METADATA_LINE_BYTES
            or b'"task_started"' not in raw_line
            or b'"event_msg"' not in raw_line
        ):
            return None
        try:
            item = json.loads(raw_line.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return None
        if not isinstance(item, dict) or item.get("type") != "event_msg":
            return None
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "task_started":
            return None
        return (
            self._parse_time(payload.get("started_at"))
            or self._parse_time(item.get("timestamp"))
            or fallback
        )

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
                if isinstance(record_started, int):
                    if process_started is None:
                        continue
                    process_matches = abs(process_started - record_started) <= 60_000
                else:
                    process_matches = True
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
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            try:
                return datetime.fromtimestamp(raw, UTC)
            except (OSError, OverflowError, ValueError):
                return None
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
