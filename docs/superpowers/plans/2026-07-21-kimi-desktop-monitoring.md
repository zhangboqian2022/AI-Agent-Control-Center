# Kimi Desktop Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kimi Desktop (Kimi.app, daimon runtime) as a third monitored agent brand in AACC, covering both Agent tasks (full status parity via the embedded kimi-code session) and chat conversations (simplified running/idle status).

**Architecture:** New `kimi_desktop_discovery.py` reads the daimon `conversations.sqlite` catalog (read-only/immutable) and delegates Agent-conversation status to a reusable session-status evaluator extracted from `kimi_discovery.py` (behavior-preserving refactor). A thin `KimiDesktopDiscoveryService(LocalDiscoveryService)` plugs into the existing generic polling service. GUI gains a third brand by mirroring the codex/kimi pairing pattern; card focus uses the existing `mac_app` mechanism with bundle id `com.moonshot.kimichat`.

**Tech Stack:** Python 3.12+, PySide6, sqlite3 (stdlib), psutil, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-07-21-kimi-desktop-monitoring-design.md`

## Global Constraints

- Task id prefix: `kimi_desktop:`; `AgentConfig.type` = `"kimi_desktop"`; display name `"Kimi Desktop"`.
- `TerminalConfig(type="mac_app", app_bundle_id="com.moonshot.kimichat")` for every discovered task.
- daimon root: `~/Library/Application Support/kimi-desktop/daimon-share/daimon/`; conversations DB at `agents/main/sessions/hosted-logical/conversations.sqlite`, opened `mode=ro&immutable=1`. Never select content columns (prompts/responses) — only `conversation_id, title, updated_at_ms, kernel_session_dir, workspace_path`.
- Activity freshness window 90 s; bounded active-turn window 1800 s (same defaults as `KimiLocalDiscovery`).
- A conversation with a non-empty `kernel_session_dir` is an Agent task; anything else is a chat. Chats: fresh `updated_at_ms` → RUNNING (`正在生成回复`); stale → IDLE (`空闲`) if Kimi.app alive else UNKNOWN (`未检测到运行进程`).
- Missing daimon root / sqlite → empty catalog, no error. Corrupt/unreadable sqlite → raise `KimiDesktopDiscoveryError` (service health degrades).
- The `kimi_discovery.py` refactor must keep all existing tests passing **unchanged** (except no test edits at all in Task 1).
- QSettings keys: `kimi_desktop_manual_tasks`, `kimi_desktop_retained_tasks`, `kimi_desktop_muted_tasks` (no legacy migration).
- Gate after every task: `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`, `.venv/bin/mypy src/aacc` all green.
- Commit message format: `feat: ...` / `fix: ...` / `docs: ...` / `refactor: ...`, English.

---

### Task 1: Extract reusable session-status evaluation from kimi_discovery.py

Pure refactor. `KimiLocalDiscovery.discover()` currently inlines a status decision tree; extract it (plus the two session-scan helpers) to module level so the Kimi Desktop discovery can reuse them. Behavior must be identical, including the lazy single-resolution of `agent_process_alive` per `discover()` call.

**Files:**
- Modify: `src/aacc/kimi_discovery.py`
- Test: `tests/test_kimi_discovery.py` (no edits — regression only)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 2):
  - `KimiSessionStatus` dataclass: `status: TaskStatus`, `message: str`, `confidence: float`, `activity_at: datetime | None`
  - `kimi_session_activity_at(session_dir: Path, file_modified_at: FileModifiedAt) -> datetime | None`
  - `kimi_session_turn_completed(session_dir: Path) -> bool | None`
  - `evaluate_kimi_session_status(session_dir: Path, *, now: datetime, file_modified_at: FileModifiedAt, process_alive: ProcessAlive, activity_window_seconds: float, active_turn_window_seconds: float) -> KimiSessionStatus`

- [ ] **Step 1: Baseline — run the existing suite**

Run: `.venv/bin/python -m pytest tests/test_kimi_discovery.py -q`
Expected: all PASS (records the pre-refactor green state)

- [ ] **Step 2: Add module-level helpers in `src/aacc/kimi_discovery.py`**

Insert after the `KimiSession` dataclass (and keep everything else in place):

```python
@dataclass(frozen=True)
class KimiSessionStatus:
    """Status evaluation for a single Kimi Code session directory."""

    status: TaskStatus
    message: str
    confidence: float
    activity_at: datetime | None


def kimi_session_activity_at(
    session_dir: Path, file_modified_at: FileModifiedAt
) -> datetime | None:
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
            modified_at = file_modified_at(path)
        except OSError:
            continue
        if latest is None or modified_at > latest:
            latest = modified_at
    return latest


def kimi_session_turn_completed(session_dir: Path) -> bool | None:
    """Detect a finished turn from the wire, reading event types only.

    A completed turn ends with a `usage.record` event scoped to the turn;
    any later turn-boundary event (prompt, loop activity, llm request)
    means the session is working again. Returns True for completed,
    False for active, and None when the scan budget is exhausted before
    either signal — an undetermined scan must never fabricate completion.
    """
    wire_path = session_dir / "agents" / "main" / "wire.jsonl"
    truncated = [False]
    try:
        with wire_path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            for line in _reverse_complete_lines(
                handle, size, _WIRE_SCAN_BUDGET_BYTES, truncated
            ):
                event_type, usage_scope = _wire_event(line)
                if event_type in _TURN_ACTIVE_TYPES:
                    return False
                if event_type == "usage.record" and usage_scope == "turn":
                    return True
    except OSError:
        return None
    return None if truncated[0] else False


def evaluate_kimi_session_status(
    session_dir: Path,
    *,
    now: datetime,
    file_modified_at: FileModifiedAt,
    process_alive: ProcessAlive,
    activity_window_seconds: float,
    active_turn_window_seconds: float,
) -> KimiSessionStatus:
    """Apply the Kimi Code status decision tree to one session directory."""
    activity_at = kimi_session_activity_at(session_dir, file_modified_at)
    turn_completed = kimi_session_turn_completed(session_dir)
    if turn_completed is True:
        return KimiSessionStatus(TaskStatus.COMPLETED, "回合已完成", 0.96, activity_at)
    if (
        activity_at is not None
        and (now - activity_at).total_seconds() <= activity_window_seconds
    ):
        return KimiSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, activity_at)
    if (
        turn_completed is False
        and activity_at is not None
        and (now - activity_at).total_seconds() <= active_turn_window_seconds
    ):
        return KimiSessionStatus(TaskStatus.RUNNING, "正在运行", 0.8, activity_at)
    if process_alive():
        return KimiSessionStatus(TaskStatus.IDLE, "空闲", 0.7, activity_at)
    return KimiSessionStatus(
        TaskStatus.UNKNOWN, "未检测到运行进程", 0.55, activity_at
    )
```

- [ ] **Step 3: Rewire `KimiLocalDiscovery` to the helpers**

Replace the body of `discover()`'s per-session decision (current lines 146–179, from `process_alive: bool | None = None` through the final `else:` branch) with:

```python
        process_alive: bool | None = None

        def is_agent_alive() -> bool:
            nonlocal process_alive
            if process_alive is None:
                process_alive = self.agent_process_alive()
            return process_alive

        discovered: list[DiscoveredTask] = []
        for session in sessions:
            session_id = session["id"]
            evaluation = evaluate_kimi_session_status(
                session["session_dir"],
                now=now,
                file_modified_at=self.file_modified_at,
                process_alive=is_agent_alive,
                activity_window_seconds=self.activity_window_seconds,
                active_turn_window_seconds=self.active_turn_window_seconds,
            )
            activity_at = evaluation.activity_at
            updated_at = (
                activity_at if activity_at is not None else session["updated_at"]
            )
            status = evaluation.status
            message = evaluation.message
            confidence = evaluation.confidence
            task_id = f"kimi:{session_id}"
```

(The `DiscoveredTask(...)` append, sort, and slot re-numbering below stay untouched.)

Replace `_activity_at` and `_turn_completed` methods with delegating wrappers (`_turn_completed` must stay — `tests/test_kimi_discovery.py:404` calls it):

```python
    def _activity_at(self, session_dir: Path) -> datetime | None:
        return kimi_session_activity_at(session_dir, self.file_modified_at)

    def _turn_completed(self, session_dir: Path) -> bool | None:
        return kimi_session_turn_completed(session_dir)
```

Delete the now-unused `_is_recent` and `_is_within_active_turn_window` methods (no test references them).

- [ ] **Step 4: Verify regression**

Run: `.venv/bin/python -m pytest tests/test_kimi_discovery.py tests/test_discovery_service.py -q`
Expected: all PASS, zero test-file edits

- [ ] **Step 5: Lint, type check, commit**

Run: `.venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: clean

```bash
git add src/aacc/kimi_discovery.py
git commit -m "refactor: extract reusable kimi session status evaluation"
```

---

### Task 2: KimiDesktopLocalDiscovery module

**Files:**
- Create: `src/aacc/kimi_desktop_discovery.py`
- Test: `tests/test_kimi_desktop_discovery.py` (create)

**Interfaces:**
- Consumes (from Task 1): `evaluate_kimi_session_status`, `Clock`, `FileModifiedAt`, `ProcessAlive` from `aacc.kimi_discovery`; `DiscoveredTask` from `aacc.codex_discovery`.
- Produces (used by Tasks 3/5): `KimiDesktopDiscoveryError(RuntimeError)`; `KimiDesktopSession` dataclass (`session_id: str, title: str, updated_at: datetime`); `KimiDesktopLocalDiscovery` with `discover(selected_ids: set[str] | None = None) -> list[DiscoveredTask]`, `catalog() -> list[KimiDesktopSession]`, `active_session_ids(*, limit: int = 4) -> set[str]` — satisfying the `_LocalDiscovery` protocol.

- [ ] **Step 1: Write the failing test file `tests/test_kimi_desktop_discovery.py`**

```python
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aacc.kimi_desktop_discovery import (
    KimiDesktopDiscoveryError,
    KimiDesktopLocalDiscovery,
)
from aacc.models import TaskStatus

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    return NOW


def _db_path(root: Path) -> Path:
    return (
        root
        / "agents"
        / "main"
        / "sessions"
        / "hosted-logical"
        / "conversations.sqlite"
    )


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _write_conversations(db_path: Path, rows: list[tuple]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                kernel_session_dir TEXT,
                workspace_path TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)", rows
        )
        connection.commit()
    finally:
        connection.close()


def _write_wire(session_dir: Path, *event_types: str) -> Path:
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '{"type":"usage.record","usageScope":"turn"}'
        if event_type == "usage.record"
        else f'{{"type":"{event_type}"}}'
        for event_type in event_types
    ]
    wire.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return wire


def _build(
    root: Path,
    *,
    mtimes: dict[Path, datetime] | None = None,
    app_alive: bool = True,
) -> KimiDesktopLocalDiscovery:
    mtime_map = mtimes or {}

    def file_modified_at(path: Path) -> datetime:
        if path in mtime_map:
            return mtime_map[path]
        raise OSError(f"no such file: {path}")

    return KimiDesktopLocalDiscovery(
        root,
        now=_now,
        file_modified_at=file_modified_at,
        app_process_alive=lambda: app_alive,
    )


def test_missing_daimon_root_discovers_nothing(tmp_path: Path) -> None:
    discovery = _build(tmp_path / "absent")
    assert discovery.discover() == []
    assert discovery.catalog() == []
    assert discovery.active_session_ids() == set()


def test_fresh_chat_conversation_is_running(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    _write_conversations(
        _db_path(root),
        [("conv-1", "闲聊", _ms(NOW - timedelta(seconds=30)), None, None)],
    )
    tasks = _build(root).discover()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.config.id == "kimi_desktop:conv-1"
    assert task.state.status is TaskStatus.RUNNING
    assert task.state.message == "正在生成回复"
    assert task.state.session_id == "conv-1"
    assert task.config.agent.type == "kimi_desktop"
    assert task.config.agent.display_name == "Kimi Desktop"
    assert task.config.terminal.type == "mac_app"
    assert task.config.terminal.app_bundle_id == "com.moonshot.kimichat"


def test_stale_chat_conversation_is_idle_when_app_alive(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    _write_conversations(
        _db_path(root),
        [("conv-1", "闲聊", _ms(NOW - timedelta(minutes=10)), None, None)],
    )
    task = _build(root, app_alive=True).discover()[0]
    assert task.state.status is TaskStatus.IDLE
    assert task.state.message == "空闲"


def test_stale_chat_conversation_is_unknown_when_app_dead(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    _write_conversations(
        _db_path(root),
        [("conv-1", "闲聊", _ms(NOW - timedelta(minutes=10)), None, None)],
    )
    task = _build(root, app_alive=False).discover()[0]
    assert task.state.status is TaskStatus.UNKNOWN
    assert task.state.message == "未检测到运行进程"


def test_agent_conversation_completed_turn(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    kernel = tmp_path / "kernel-session"
    _write_wire(kernel, "turn.prompt", "usage.record")
    _write_conversations(
        _db_path(root),
        [("conv-2", "重构代码", _ms(NOW - timedelta(hours=1)), str(kernel), None)],
    )
    task = _build(root).discover()[0]
    assert task.state.status is TaskStatus.COMPLETED
    assert task.state.message == "回合已完成"


def test_agent_conversation_with_recent_wire_is_running(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    kernel = tmp_path / "kernel-session"
    wire = _write_wire(kernel, "turn.prompt")
    _write_conversations(
        _db_path(root),
        [("conv-2", "重构代码", _ms(NOW - timedelta(hours=1)), str(kernel), None)],
    )
    task = _build(root, mtimes={wire: NOW - timedelta(seconds=30)}).discover()[0]
    assert task.state.status is TaskStatus.RUNNING
    assert task.state.confidence == pytest.approx(0.9)
    assert task.state.started_at == NOW - timedelta(seconds=30)


def test_agent_conversation_with_missing_kernel_dir_falls_back(
    tmp_path: Path,
) -> None:
    root = tmp_path / "daimon"
    _write_conversations(
        _db_path(root),
        [
            (
                "conv-2",
                "重构代码",
                _ms(NOW - timedelta(hours=1)),
                str(tmp_path / "gone"),
                None,
            )
        ],
    )
    task = _build(root, app_alive=True).discover()[0]
    assert task.state.status is TaskStatus.IDLE


def test_corrupt_sqlite_raises_discovery_error(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    db_path = _db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"this is not a sqlite database")
    discovery = _build(root)
    with pytest.raises(KimiDesktopDiscoveryError):
        discovery.discover()


def test_selected_ids_filter_and_catalog_order(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    _write_conversations(
        _db_path(root),
        [
            ("conv-old", "旧会话", _ms(NOW - timedelta(days=1)), None, None),
            ("conv-new", "新会话", _ms(NOW - timedelta(minutes=5)), None, None),
        ],
    )
    discovery = _build(root)
    selected = discovery.discover(selected_ids={"conv-old"})
    assert [task.config.id for task in selected] == ["kimi_desktop:conv-old"]
    catalog = discovery.catalog()
    assert [session.session_id for session in catalog] == ["conv-new", "conv-old"]
    assert catalog[0].title == "新会话"


def test_active_session_ids_collects_running(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    _write_conversations(
        _db_path(root),
        [
            ("conv-1", "活跃", _ms(NOW - timedelta(seconds=30)), None, None),
            ("conv-2", "安静", _ms(NOW - timedelta(days=1)), None, None),
        ],
    )
    assert _build(root).active_session_ids() == {"conv-1"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_kimi_desktop_discovery.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aacc.kimi_desktop_discovery'`

- [ ] **Step 3: Implement `src/aacc/kimi_desktop_discovery.py`**

```python
from __future__ import annotations

import re
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
_KIMI_DESKTOP_PROCESS_PATTERN = re.compile(r"^kimi$", re.IGNORECASE)
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

    The conversations sqlite is opened read-only/immutable and only metadata
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
                f"file:{self.conversations_path}?mode=ro&immutable=1", uri=True
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
            for process in psutil.process_iter(["name", "exe"]):
                exe = process.info.get("exe")
                if isinstance(exe, str) and "/Kimi.app/" in exe:
                    return True
                name = process.info.get("name")
                if isinstance(name, str) and _KIMI_DESKTOP_PROCESS_PATTERN.search(name):
                    return True
        except (psutil.Error, OSError):
            return False
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kimi_desktop_discovery.py -q`
Expected: 10 PASS

- [ ] **Step 5: Lint, type check, commit**

Run: `.venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: clean

```bash
git add src/aacc/kimi_desktop_discovery.py tests/test_kimi_desktop_discovery.py
git commit -m "feat: add Kimi Desktop local discovery"
```

---

### Task 3: KimiDesktopDiscoveryService

**Files:**
- Modify: `src/aacc/discovery_service.py`
- Test: `tests/test_discovery_service.py`

**Interfaces:**
- Consumes: `KimiDesktopLocalDiscovery`, `KimiDesktopDiscoveryError`, `KimiDesktopSession` (Task 2).
- Produces: `KimiDesktopDiscoveryService(manager, *, discovery=None, interval_seconds=5.0)` — brand `"Kimi Desktop"`, thread `aacc-kimi-desktop-discovery`, error type `KimiDesktopDiscoveryError`. Used by Task 7 (`app.py`).

- [ ] **Step 1: Write the failing tests (append to `tests/test_discovery_service.py`)**

```python
class StubKimiDesktopDiscovery(StubDiscovery):
    pass


def test_kimi_desktop_service_poll_registers_task(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "state.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    discovery = StubKimiDesktopDiscovery([_discovered_task("kimi_desktop:conv-1")])
    service = KimiDesktopDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]
    assert service.poll_once() == 1
    assert manager.get("kimi_desktop:conv-1").status is TaskStatus.RUNNING
    manager.close()


def test_kimi_desktop_health_degrades_on_discovery_error(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "state.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)

    class FailingKimiDesktopDiscovery(StubKimiDesktopDiscovery):
        def discover(self, selected_ids: set[str] | None = None) -> list:
            raise KimiDesktopDiscoveryError("index unreadable")

        def active_session_ids(self) -> set[str]:
            return set()

    service = KimiDesktopDiscoveryService(
        manager, discovery=FailingKimiDesktopDiscovery([])  # type: ignore[arg-type]
    )
    assert service.poll_safely() == 0
    health = service.health()
    assert health.degraded
    assert health.brand == "Kimi Desktop"
    manager.close()
```

Check the top of `tests/test_discovery_service.py` first: mirror exactly how existing tests build `_discovered_task`-like fixtures (the existing `StubDiscovery` at line 11 and the Codex task-registration test at line 61). If the existing file builds `DiscoveredTask` inline instead of a helper named `_discovered_task`, reuse that existing construction verbatim and adapt the two new tests to match. Also extend the import line 5 to include `KimiDesktopService`→ `KimiDesktopDiscoveryService`, and import `KimiDesktopDiscoveryError` from `aacc.kimi_desktop_discovery`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'KimiDesktopDiscoveryService'`

- [ ] **Step 3: Implement the service in `src/aacc/discovery_service.py`**

Extend the kimi import at line 18 area:

```python
from aacc.kimi_desktop_discovery import (
    KimiDesktopDiscoveryError,
    KimiDesktopLocalDiscovery,
    KimiDesktopSession,
)
```

Append at end of file:

```python
class KimiDesktopDiscoveryService(LocalDiscoveryService[KimiDesktopSession]):
    """Polls local Kimi Desktop metadata outside the Qt event loop."""

    def __init__(
        self,
        manager: TaskManager,
        *,
        discovery: KimiDesktopLocalDiscovery | None = None,
        interval_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            manager,
            discovery=discovery or KimiDesktopLocalDiscovery(),
            interval_seconds=interval_seconds,
            thread_name="aacc-kimi-desktop-discovery",
            error_type=KimiDesktopDiscoveryError,
            brand="Kimi Desktop",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_service.py -q`
Expected: all PASS

- [ ] **Step 5: Lint, type check, commit**

Run: `.venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: clean

```bash
git add src/aacc/discovery_service.py tests/test_discovery_service.py
git commit -m "feat: add Kimi Desktop discovery service"
```

---

### Task 4: models.py default visible agent type

**Files:**
- Modify: `src/aacc/models.py:58`
- Test: `tests/test_gui.py` (assertion added in Task 5 covers window behavior; this task adds a pure-model test)

**Interfaces:**
- Consumes: nothing.
- Produces: default `visible_agent_types` = `["codex_cli", "kimi_code", "kimi_desktop"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py` if it exists and covers `default_config()` (check first; otherwise append to `tests/test_app.py`):

```python
def test_default_visible_agent_types_include_kimi_desktop() -> None:
    from aacc.config import default_config

    assert "kimi_desktop" in default_config().app.visible_agent_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_default_visible_agent_types_include_kimi_desktop -q` (adjust path per Step 1)
Expected: FAIL — `assert 'kimi_desktop' not in [...]` (AssertionError)

- [ ] **Step 3: Edit `src/aacc/models.py:58`**

```python
    visible_agent_types: list[str] = Field(
        default_factory=lambda: ["codex_cli", "kimi_code", "kimi_desktop"]
    )
```

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `.venv/bin/python -m pytest -q -k visible_agent_types && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: PASS, clean

```bash
git add src/aacc/models.py tests/test_config.py  # or tests/test_app.py per Step 1
git commit -m "feat: default-show Kimi Desktop tasks"
```

---

### Task 5: GUI core third-brand wiring

Mirror the kimi pairing pattern for `kimi_desktop` in `MainWindow` and `TaskCard`: callback group, QSettings persistence, preference methods, visibility filtering, remove/clear handling.

**Files:**
- Modify: `src/aacc/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `KimiDesktopSession` from `aacc.kimi_desktop_discovery` (Task 2).
- Produces (used by Tasks 6/7): `MainWindow` kwargs `kimi_desktop_sessions`, `kimi_desktop_auto_active_ids`, `kimi_desktop_retained_ids`, `kimi_desktop_muted_ids`, `set_kimi_desktop_monitoring_preferences`; attributes `kimi_desktop_manual_ids` / `kimi_desktop_retained_ids` / `kimi_desktop_muted_ids`; property `kimi_desktop_selected_ids`; methods `kimi_desktop_auto_active_ids()`, `set_kimi_desktop_monitoring_preferences(manual, retained, muted)`, `remove_kimi_desktop_task(task_id)`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_gui.py`)**

```python
def build_kimi_desktop_window(
    tmp_path: Path, qtbot: object
) -> tuple[MainWindow, TaskManager, list[tuple[set[str], set[str], set[str]]]]:
    config = default_config()
    store = StateStore(tmp_path / "gui-kd.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    settings = QSettings(str(tmp_path / "gui-kd-settings.ini"), QSettings.Format.IniFormat)
    applied: list[tuple[set[str], set[str], set[str]]] = []
    window = MainWindow(
        manager,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=settings,
        kimi_desktop_sessions=lambda: [],
        kimi_desktop_auto_active_ids=lambda: set(),
        kimi_desktop_retained_ids=lambda: set(),
        kimi_desktop_muted_ids=lambda: set(),
        set_kimi_desktop_monitoring_preferences=lambda manual, retained, muted: applied.append(
            (set(manual), set(retained), set(muted))
        ),
    )
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    return window, manager, applied


def test_kimi_desktop_preferences_persist_and_apply(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager, applied = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, {"conv-2"}, {"conv-3"})
    assert applied[-1] == ({"conv-1"}, {"conv-2"}, {"conv-3"})
    assert window.kimi_desktop_selected_ids == {"conv-1", "conv-2"}
    # Note: QSettings may round-trip a single-element list as a plain string,
    # so assert the parsed in-memory sets here; raw persistence is covered by
    # the reload test below (whose loader tolerates both forms).
    assert window.kimi_desktop_manual_ids == {"conv-1"}
    assert window.kimi_desktop_retained_ids == {"conv-2"}
    assert window.kimi_desktop_muted_ids == {"conv-3"}
    manager.close()


def test_kimi_desktop_preferences_reload_from_settings(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager, _ = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, {"conv-2"}, set())
    manager.close()
    reloaded_settings = QSettings(
        str(tmp_path / "gui-kd-settings.ini"), QSettings.Format.IniFormat
    )
    config = default_config()
    store = StateStore(tmp_path / "gui-kd2.db")
    store.initialize(config.tasks)
    manager2 = TaskManager(config, store)
    reloaded = MainWindow(
        manager2,
        AutomationExecutor(MacAutomation(config)),
        enable_tray=False,
        settings=reloaded_settings,
    )
    qtbot.addWidget(reloaded)  # type: ignore[attr-defined]
    assert reloaded.kimi_desktop_manual_ids == {"conv-1"}
    assert reloaded.kimi_desktop_retained_ids == {"conv-2"}
    assert reloaded.kimi_desktop_selected_ids == {"conv-1", "conv-2"}
    manager2.close()


def test_remove_kimi_desktop_task_mutes_and_hides(tmp_path: Path, qtbot: object) -> None:
    window, manager, applied = build_kimi_desktop_window(tmp_path, qtbot)
    window.set_kimi_desktop_monitoring_preferences({"conv-1"}, set(), set())
    manager.register(
        TaskConfig(
            id="kimi_desktop:conv-1",
            slot=1,
            name="桌面任务",
            agent=AgentConfig(type="kimi_desktop", display_name="Kimi Desktop"),
            terminal=TerminalConfig(type="mac_app", app_bundle_id="com.moonshot.kimichat"),
        ),
        TaskState.new("kimi_desktop:conv-1", "RUNNING"),
    )
    window.sync_cards()
    assert "kimi_desktop:conv-1" in window.cards
    window.remove_kimi_desktop_task("kimi_desktop:conv-1")
    assert applied[-1] == (set(), set(), {"conv-1"})
    assert "kimi_desktop:conv-1" not in window.cards
    manager.close()


def test_kimi_desktop_task_hidden_until_selected(tmp_path: Path, qtbot: object) -> None:
    window, manager, _ = build_kimi_desktop_window(tmp_path, qtbot)
    manager.register(
        TaskConfig(
            id="kimi_desktop:conv-9",
            slot=1,
            name="未选任务",
            agent=AgentConfig(type="kimi_desktop", display_name="Kimi Desktop"),
            terminal=TerminalConfig(type="mac_app", app_bundle_id="com.moonshot.kimichat"),
        ),
        TaskState.new("kimi_desktop:conv-9", "RUNNING"),
    )
    window.sync_cards()
    assert "kimi_desktop:conv-9" not in window.cards
    window.set_kimi_desktop_monitoring_preferences({"conv-9"}, set(), set())
    assert "kimi_desktop:conv-9" in window.cards
    manager.close()


def test_kimi_desktop_visible_by_default_in_fresh_window(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert "kimi_desktop" in window.visible_agent_types
    manager.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui.py -q -k kimi_desktop`
Expected: FAIL — `TypeError: MainWindow.__init__() got an unexpected keyword argument 'kimi_desktop_sessions'`

- [ ] **Step 3: Implement the GUI wiring in `src/aacc/gui.py`**

a) Import (after the `KimiSession` import, line 49):

```python
from aacc.kimi_desktop_discovery import KimiDesktopSession
```

b) `TaskCard.__init__` (line 220) and `create_context_menu` (line 311): change both `("codex:", "kimi:")` tuples to `("codex:", "kimi:", "kimi_desktop:")`.

c) `MainWindow.__init__` signature — add after `set_kimi_monitoring_preferences` (line 536-537):

```python
        kimi_desktop_sessions: Callable[[], list[KimiDesktopSession]] | None = None,
        kimi_desktop_auto_active_ids: Callable[[], set[str]] | None = None,
        kimi_desktop_retained_ids: Callable[[], set[str]] | None = None,
        kimi_desktop_muted_ids: Callable[[], set[str]] | None = None,
        set_kimi_desktop_monitoring_preferences: Callable[[set[str], set[str], set[str]], None]
        | None = None,
```

d) Storage — add after the `self._set_kimi_monitoring_preferences = ...` block (line 574-576):

```python
        self._kimi_desktop_sessions = kimi_desktop_sessions or (lambda: [])
        self._kimi_desktop_auto_active_ids = kimi_desktop_auto_active_ids or (lambda: set())
        self._kimi_desktop_retained_ids = kimi_desktop_retained_ids or (lambda: set())
        self._kimi_desktop_muted_ids = kimi_desktop_muted_ids or (lambda: set())
        self._set_kimi_desktop_monitoring_preferences = (
            set_kimi_desktop_monitoring_preferences
            or (lambda _manual_ids, _retained_ids, _muted_ids: None)
        )
```

e) QSettings load — add after `self._apply_kimi_monitoring_preferences()` (line 640), before the `custom_task_names` block:

```python
        saved_kimi_desktop_tasks = self._settings.value("kimi_desktop_manual_tasks")
        if isinstance(saved_kimi_desktop_tasks, str):
            self.kimi_desktop_manual_ids = {saved_kimi_desktop_tasks}
        elif isinstance(saved_kimi_desktop_tasks, list):
            self.kimi_desktop_manual_ids = {str(value) for value in saved_kimi_desktop_tasks}
        else:
            self.kimi_desktop_manual_ids = set()
        saved_kimi_desktop_retained = self._settings.value("kimi_desktop_retained_tasks")
        if isinstance(saved_kimi_desktop_retained, str):
            self.kimi_desktop_retained_ids = {saved_kimi_desktop_retained}
        elif isinstance(saved_kimi_desktop_retained, list):
            self.kimi_desktop_retained_ids = {
                str(value) for value in saved_kimi_desktop_retained
            }
        else:
            self.kimi_desktop_retained_ids = set()
        saved_kimi_desktop_muted = self._settings.value("kimi_desktop_muted_tasks")
        if isinstance(saved_kimi_desktop_muted, str):
            self.kimi_desktop_muted_ids = {saved_kimi_desktop_muted}
        elif isinstance(saved_kimi_desktop_muted, list):
            self.kimi_desktop_muted_ids = {str(value) for value in saved_kimi_desktop_muted}
        else:
            self.kimi_desktop_muted_ids = set()
        self._apply_kimi_desktop_monitoring_preferences()
```

f) Default visibility — line 659, after `self.visible_agent_types.add("kimi_code")` add:

```python
        self.visible_agent_types.add("kimi_desktop")
```

g) `refresh()` (lines 835-838) — add two calls after `self._sync_kimi_muted_ids()`:

```python
        self._sync_kimi_desktop_retained_ids()
        self._sync_kimi_desktop_muted_ids()
```

h) `_visible_tasks()` — add a third filter block after the kimi one (lines 858-866):

```python
        tasks = [
            task
            for task in tasks
            if task.agent.type != "kimi_desktop"
            or (
                task.id.startswith("kimi_desktop:")
                and task.id.removeprefix("kimi_desktop:")
                in self.kimi_desktop_selected_ids
            )
        ]
```

i) Selected-ids accessors — add after `kimi_auto_active_ids()` (line 884-885):

```python
    @property
    def kimi_desktop_selected_ids(self) -> set[str]:
        return (
            self.kimi_desktop_manual_ids
            | self.kimi_desktop_retained_ids
            | self.kimi_desktop_auto_active_ids()
        ) - self.kimi_desktop_muted_ids

    def kimi_desktop_auto_active_ids(self) -> set[str]:
        return set(self._kimi_desktop_auto_active_ids())
```

j) `sync_cards()` — add a third remove connection after line 905 (`new_card.remove_requested.connect(self.remove_kimi_task)`):

```python
                new_card.remove_requested.connect(self.remove_kimi_desktop_task)
```

and update `clear_retained_button` visibility (line 924-926) tuple to `("codex:", "kimi:", "kimi_desktop:")`.

k) Preference methods — add after `_sync_kimi_muted_ids` (line 1080), before `remove_kimi_task`:

```python
    def set_kimi_desktop_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_kimi_desktop_monitoring_preferences(selected_ids, set(), set())

    def set_kimi_desktop_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        self.kimi_desktop_manual_ids = set(manual_ids)
        self.kimi_desktop_retained_ids = set(retained_ids) - self.kimi_desktop_manual_ids
        self.kimi_desktop_muted_ids = set(muted_ids) - self.kimi_desktop_manual_ids
        self._settings.setValue(
            "kimi_desktop_manual_tasks", sorted(self.kimi_desktop_manual_ids)
        )
        self._settings.setValue(
            "kimi_desktop_retained_tasks", sorted(self.kimi_desktop_retained_ids)
        )
        self._settings.setValue(
            "kimi_desktop_muted_tasks", sorted(self.kimi_desktop_muted_ids)
        )
        self._apply_kimi_desktop_monitoring_preferences()
        self.sync_cards()

    def _apply_kimi_desktop_monitoring_preferences(self) -> None:
        self._set_kimi_desktop_monitoring_preferences(
            self.kimi_desktop_manual_ids,
            self.kimi_desktop_retained_ids,
            self.kimi_desktop_muted_ids,
        )

    def _sync_kimi_desktop_retained_ids(self) -> None:
        retained_ids = self._kimi_desktop_retained_ids()
        if retained_ids != self.kimi_desktop_retained_ids:
            self.kimi_desktop_retained_ids = set(retained_ids)
            self._settings.setValue(
                "kimi_desktop_retained_tasks", sorted(self.kimi_desktop_retained_ids)
            )

    def _sync_kimi_desktop_muted_ids(self) -> None:
        muted_ids = self._kimi_desktop_muted_ids()
        if muted_ids != self.kimi_desktop_muted_ids:
            self.kimi_desktop_muted_ids = set(muted_ids)
            self._settings.setValue(
                "kimi_desktop_muted_tasks", sorted(self.kimi_desktop_muted_ids)
            )

    def remove_kimi_desktop_task(self, task_id: str) -> None:
        if not task_id.startswith("kimi_desktop:"):
            return
        session_id = task_id.removeprefix("kimi_desktop:")
        self.kimi_desktop_manual_ids.discard(session_id)
        self.kimi_desktop_retained_ids.discard(session_id)
        self.kimi_desktop_muted_ids.add(session_id)
        self._settings.setValue(
            "kimi_desktop_manual_tasks", sorted(self.kimi_desktop_manual_ids)
        )
        self._settings.setValue(
            "kimi_desktop_retained_tasks", sorted(self.kimi_desktop_retained_ids)
        )
        self._settings.setValue(
            "kimi_desktop_muted_tasks", sorted(self.kimi_desktop_muted_ids)
        )
        self._apply_kimi_desktop_monitoring_preferences()
        self.sync_cards()
```

l) `rename_task` (line 1096) and `clear_retained_tasks` (line 1134): change both `("codex:", "kimi:")` tuples to `("codex:", "kimi:", "kimi_desktop:")`. In `clear_retained_tasks`' dispatch loop (lines 1148-1152) change to:

```python
        for task_id in task_ids:
            if task_id.startswith("codex:"):
                self.remove_codex_task(task_id)
            elif task_id.startswith("kimi_desktop:"):
                self.remove_kimi_desktop_task(task_id)
            else:
                self.remove_kimi_task(task_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui.py -q`
Expected: all PASS (new + existing)

- [ ] **Step 5: Lint, type check, commit**

Run: `.venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: clean

```bash
git add src/aacc/gui.py tests/test_gui.py
git commit -m "feat: wire Kimi Desktop brand into main window"
```

---

### Task 6: GUI dialogs, settings panel, empty-state copy, health merge

**Files:**
- Modify: `src/aacc/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: Task 5's `kimi_desktop_*` members; `KimiDesktopSession` (Task 2).
- Produces (used by Task 7): `MainWindow` signal `kimi_desktop_discovery_health_received`; kwargs `kimi_desktop_discovery_health`, `subscribe_kimi_desktop_discovery_health`; method `open_kimi_desktop_task_selector()`; class `KimiDesktopTaskSelectionDialog`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_gui.py`)**

```python
def test_kimi_desktop_task_selection_dialog_applies_preferences(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager, applied = build_kimi_desktop_window(tmp_path, qtbot)
    sessions = [
        KimiDesktopSession(
            session_id="conv-1",
            title="桌面会话",
            updated_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
        )
    ]
    dialog = KimiDesktopTaskSelectionDialog(sessions, set(), set(), window)
    assert dialog.tasks.count() == 1
    dialog.tasks.item(0).setCheckState(Qt.CheckState.Checked)
    selected = dialog.selected_ids()
    assert selected == {"conv-1"}
    manager.close()


def test_kimi_desktop_health_warning_merges_all_brands(
    tmp_path: Path, qtbot: object
) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert not window.discovery_warning.isVisible()
    window.kimi_desktop_discovery_health_received.emit(
        DiscoveryHealth(degraded=True, summary="index unreadable", brand="Kimi Desktop")
    )
    assert window.discovery_warning.isVisible()
    assert "Kimi Desktop" in window.discovery_warning_label.text()
    window.kimi_desktop_discovery_health_received.emit(
        DiscoveryHealth(brand="Kimi Desktop")
    )
    assert not window.discovery_warning.isVisible()
    manager.close()


def test_empty_tasks_label_mentions_kimi_desktop(tmp_path: Path, qtbot: object) -> None:
    window, manager = build_window(tmp_path, qtbot)
    assert "Kimi Desktop" in window.empty_tasks_label.text()
    manager.close()
```

Update the import block of `tests/test_gui.py` (lines 21-29): add `KimiDesktopTaskSelectionDialog` to the `aacc.gui` imports and add `from aacc.kimi_desktop_discovery import KimiDesktopSession`.

Also update the existing test at `tests/test_gui.py:56`: change the asserted substring from `"未选择 Codex / Kimi Code 任务"` to `"未选择 Codex / Kimi Code / Kimi Desktop 任务"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui.py -q -k "kimi_desktop or empty_tasks"`
Expected: FAIL — `ImportError: cannot import name 'KimiDesktopTaskSelectionDialog'`; old empty-label assertion fails after copy change

- [ ] **Step 3: Implement in `src/aacc/gui.py`**

a) Signal — add after `kimi_discovery_health_received = Signal(object)` (line 517):

```python
    kimi_desktop_discovery_health_received = Signal(object)
```

b) `MainWindow.__init__` kwargs — add after `subscribe_kimi_discovery_health` (lines 543-546):

```python
        kimi_desktop_discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_kimi_desktop_discovery_health: (
            Callable[[Callable[[DiscoveryHealth], None]], Callable[[], None]] | None
        ) = None,
```

c) Health storage — replace lines 578-581 (`self._discovery_health = ...` / `self._kimi_discovery_health = ...`) with a brand-keyed dict:

```python
        self._discovery_healths: dict[str, DiscoveryHealth] = {}
        for health in (
            (discovery_health or DiscoveryHealth)(),
            (kimi_discovery_health or (lambda: DiscoveryHealth(brand="Kimi")))(),
            (
                kimi_desktop_discovery_health
                or (lambda: DiscoveryHealth(brand="Kimi Desktop"))
            )(),
        ):
            self._discovery_healths[health.brand] = health
```

d) Subscription — add after the `self._unsubscribe_kimi_discovery_health = ...` block (lines 590-594):

```python
        self._unsubscribe_kimi_desktop_discovery_health = (
            subscribe_kimi_desktop_discovery_health(
                self.kimi_desktop_discovery_health_received.emit
            )
            if subscribe_kimi_desktop_discovery_health is not None
            else lambda: None
        )
```

e) Signal connection — after line 665 (`self.kimi_discovery_health_received.connect(self._apply_kimi_discovery_health)`):

```python
        self.kimi_desktop_discovery_health_received.connect(self._apply_discovery_health)
```

Also change the two existing connections (lines 664-665) to both point at the unified `_apply_discovery_health`, and delete the now-redundant `_apply_kimi_discovery_health` method. Replace `_apply_discovery_health` (lines 1281-1285) with:

```python
    def _apply_discovery_health(self, value: object) -> None:
        if not isinstance(value, DiscoveryHealth):
            return
        self._discovery_healths[value.brand] = value
        self._refresh_discovery_warning()
```

f) Initial warning call — line 729: replace `self._apply_discovery_health(self._discovery_health)` with `self._refresh_discovery_warning()`.

g) `_refresh_discovery_warning` (lines 1293-1304) — iterate the dict:

```python
    def _refresh_discovery_warning(self) -> None:
        degraded = [
            health for health in self._discovery_healths.values() if health.degraded
        ]
        if not degraded:
            self.discovery_warning.setVisible(False)
            return
        summary = "；".join(f"{health.brand}: {health.summary}" for health in degraded)
        self.discovery_warning_label.setText(summary[:80])
        self.discovery_warning.setVisible(True)
```

h) `copy_discovery_diagnostics` (lines 1306-1312):

```python
    def copy_discovery_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(
            "\n\n".join(
                health.diagnostics(self._discovery_log_path)
                for health in self._discovery_healths.values()
            )
        )
```

i) `closeEvent` (line 1387) — add after `self._unsubscribe_kimi_discovery_health()`:

```python
        self._unsubscribe_kimi_desktop_discovery_health()
```

j) `KimiDesktopTaskSelectionDialog` — add after `KimiTaskSelectionDialog` (ends line 509):

```python
class KimiDesktopTaskSelectionDialog(TaskSelectionDialog):
    def __init__(
        self,
        sessions: list[KimiDesktopSession],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(
            [
                (session.session_id, session.title, session.updated_at)
                for session in sessions
            ],
            selected_ids,
            auto_active_ids,
            parent,
            window_title="选择监控的 Kimi Desktop 任务",
        )
```

k) `open_kimi_desktop_task_selector` — add after `open_kimi_task_selector` (ends line 1184):

```python
    def open_kimi_desktop_task_selector(self) -> None:
        auto_active_ids = self.kimi_desktop_auto_active_ids()
        dialog = KimiDesktopTaskSelectionDialog(
            self._kimi_desktop_sessions(),
            self.kimi_desktop_selected_ids,
            auto_active_ids,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dialog.selected_ids()
            retained_ids = self.kimi_desktop_retained_ids & selected_ids
            manual_ids = (self.kimi_desktop_manual_ids & selected_ids) | (
                selected_ids - auto_active_ids - retained_ids
            )
            muted_ids = (
                self.kimi_desktop_muted_ids | (auto_active_ids - selected_ids)
            ) - selected_ids
            if dialog.restore_auto_requested():
                muted_ids -= auto_active_ids
            self.set_kimi_desktop_monitoring_preferences(
                manual_ids, retained_ids, muted_ids
            )
```

l) `SettingsDialog` — add a third button after the kimi one (lines 368-374):

```python
        kimi_desktop_tasks = QPushButton(
            "选择监控的 Kimi Desktop 任务"
            f"（{len(window.kimi_desktop_selected_ids)} 已选 · "
            f"{len(window.kimi_desktop_auto_active_ids())} 自动运行）"
        )
        kimi_desktop_tasks.clicked.connect(window.open_kimi_desktop_task_selector)
        layout.addWidget(kimi_desktop_tasks)
```

and add to the `labels` dict (line 379-384): `"kimi_desktop": "Kimi Desktop",` after the `kimi_code` entry.

m) Empty-state copy — line 739: change to

```python
        self.empty_tasks_label = QLabel(
            "未选择 Codex / Kimi Code / Kimi Desktop 任务 · 点击 ⚙ 选择监控任务"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui.py -q`
Expected: all PASS

- [ ] **Step 5: Lint, type check, commit**

Run: `.venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: clean

```bash
git add src/aacc/gui.py tests/test_gui.py
git commit -m "feat: add Kimi Desktop selection dialog and health reporting"
```

---

### Task 7: app.py runtime wiring

**Files:**
- Modify: `src/aacc/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `KimiDesktopDiscoveryService` (Task 3); `MainWindow` kwargs from Tasks 5-6.
- Produces: `Runtime.kimi_desktop_discovery: KimiDesktopDiscoveryService`, constructed in `build_runtime()`, started/stopped with the other services.

- [ ] **Step 1: Write the failing test (append to `tests/test_app.py`)**

First read `tests/test_app.py` to mirror its existing `build_runtime` usage (config/db path fixtures). Then add:

```python
def test_runtime_includes_kimi_desktop_discovery(tmp_path: Path) -> None:
    from aacc.app import build_runtime
    from aacc.discovery_service import KimiDesktopDiscoveryService

    runtime = build_runtime(tmp_path / "config.yaml", tmp_path / "state.db")
    assert isinstance(runtime.kimi_desktop_discovery, KimiDesktopDiscoveryService)
    runtime.close()
```

If the existing `test_app.py` builds runtimes differently (e.g. with a written config file), copy that exact setup instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -q -k kimi_desktop`
Expected: FAIL — `AttributeError: 'Runtime' object has no attribute 'kimi_desktop_discovery'`

- [ ] **Step 3: Wire `src/aacc/app.py`**

a) Import (line 20):

```python
from aacc.discovery_service import (
    CodexDiscoveryService,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
)
```

b) `Runtime` dataclass — add field after `kimi_discovery` (line 38):

```python
    kimi_desktop_discovery: KimiDesktopDiscoveryService
```

and stop it first in `close()`:

```python
    def close(self) -> None:
        self.kimi_desktop_discovery.stop()
        self.kimi_discovery.stop()
        self.discovery.stop()
        self.automation_executor.close()
        self.manager.close()
```

c) `build_runtime()` — add to the `Runtime(...)` call:

```python
        kimi_desktop_discovery=KimiDesktopDiscoveryService(manager),
```

d) `_run_application()` `MainWindow(...)` kwargs — add after the `set_kimi_monitoring_preferences` line:

```python
        kimi_desktop_sessions=runtime.kimi_desktop_discovery.catalog,
        kimi_desktop_auto_active_ids=runtime.kimi_desktop_discovery.auto_active_ids,
        kimi_desktop_retained_ids=runtime.kimi_desktop_discovery.retained_ids,
        kimi_desktop_muted_ids=runtime.kimi_desktop_discovery.muted_ids,
        set_kimi_desktop_monitoring_preferences=runtime.kimi_desktop_discovery.set_monitoring_preferences,
```

and after the `subscribe_kimi_discovery_health` line:

```python
        kimi_desktop_discovery_health=runtime.kimi_desktop_discovery.health,
        subscribe_kimi_desktop_discovery_health=runtime.kimi_desktop_discovery.subscribe_health,
```

e) Start the service — after `runtime.kimi_discovery.start()` (line 148):

```python
    runtime.kimi_desktop_discovery.start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -q`
Expected: all PASS

- [ ] **Step 5: Full gate, commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: all green

```bash
git add src/aacc/app.py tests/test_app.py
git commit -m "feat: start Kimi Desktop discovery service in runtime"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/adapter-development.md`, `docs/adapter-development.en.md`, `AGENTS.md`
- Test: none (docs only)

- [ ] **Step 1: `docs/adapter-development.md`（中文）**

在介绍本地会话发现的段落（Codex / Kimi Code 部分）后追加小节：

```markdown
### Kimi Desktop（本地发现）

AACC 同时监控 Kimi 桌面版（Kimi.app，`com.moonshot.kimichat`）的任务，
数据源为 `~/Library/Application Support/kimi-desktop/daimon-share/daimon/`：

- `agents/main/sessions/hosted-logical/conversations.sqlite` 提供会话目录
  （只读打开，仅读取元数据列）。带 `kernel_session_dir` 的会话视为 Agent
  任务，状态判定复用 Kimi Code 的 mtime + wire.jsonl 回合边界分析；
  其余视为聊天会话，仅区分「正在生成回复 / 空闲」。
- 任务 id 前缀 `kimi_desktop:`，卡片聚焦走 `mac_app` 机制（`open -b`），
  与 Codex 卡片聚焦 Codex.app 相同。
```

- [ ] **Step 2: `docs/adapter-development.en.md`**

Add the English mirror of the same section (same structure, English prose).

- [ ] **Step 3: `AGENTS.md` 架构要点**

Update the discovery bullet list: add a line for `src/aacc/kimi_desktop_discovery.py`（daimon sqlite 目录 + 复用 kimi 回合判定的第三发现源），and mention `KimiDesktopDiscoveryService` alongside the existing two services in the `discovery_service.py` bullet.

- [ ] **Step 4: Commit**

```bash
git add docs/adapter-development.md docs/adapter-development.en.md AGENTS.md
git commit -m "docs: document Kimi Desktop discovery source"
```

---

### Task 9: Real-data calibration and manual verification

Not a code task — the closing validation gate from the spec.

- [ ] **Step 1: Generate real data**

In Kimi.app: run one Agent task (K3 / OK Computer) and send one ordinary chat. Confirm `conversations.sqlite` now has rows and the embedded kimi-code home has session dirs:

```bash
sqlite3 "file:$HOME/Library/Application Support/kimi-desktop/daimon-share/daimon/agents/main/sessions/hosted-logical/conversations.sqlite?mode=ro&immutable=1" \
  "SELECT conversation_id, title, origin, kernel_type, kernel_session_dir, updated_at_ms FROM conversations;"
```

- [ ] **Step 2: Calibrate**

Compare `origin` / `kernel_session_dir` values against the assumptions in
`kimi_desktop_discovery.py`. If a better chat/agent discriminator than
"kernel_session_dir present" exists (e.g. a stable `origin` value), refine
`_conversations()` classification with a follow-up test first (TDD).

- [ ] **Step 3: Manual smoke test**

Run the app (`.venv/bin/python -m aacc` or the installed AACC.app built via
`scripts/build_app.sh` + `scripts/install.sh`) and verify: both
conversations appear as cards with correct titles; status transitions match
observation (running while generating, completed after); clicking a card
focuses Kimi.app; selection dialog lists Kimi Desktop sessions; removal and
re-adding persist across restart.

- [ ] **Step 4: Full gate**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/mypy src/aacc`
Expected: all green
