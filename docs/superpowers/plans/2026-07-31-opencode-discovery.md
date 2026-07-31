# OpenCode 任务状态检测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AACC 新增 opencode CLI 任务监控：只读轮询 `~/.local/share/opencode/opencode.db`，从 `part` 表快照推断状态（等待同意/进行中/等待输入/已完成/空闲），复用 TaskCard 圆形状态灯显示。

**Architecture:** 仿 Kimi Desktop 链路：`opencode_discovery.py`（只读 SQLite 发现 + 状态决策树纯函数）→ `OpenCodeDiscoveryService`（`LocalDiscoveryService` 泛型子类，5s 后台轮询，manual/retained/muted 语义沿用）→ TaskManager → 面板卡片。GUI 仅加 agent 类型 `opencode_cli` 与设置入口，状态灯颜色全部复用现有 `STATUS_COLORS`。

**Tech Stack:** Python 3.12+ / sqlite3（URI 只读模式）/ psutil（进程检测，复用 `CachedProcessAlive`）/ pytest / ruff / mypy。

## Global Constraints

- 数据源：`~/.local/share/opencode/opencode.db`（可注入路径；WAL 模式，只读连接短开短关）。
- 只读访问：`sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)`，绝不 `mode=immutable`（WAL 需回放）。
- **内容边界**：`part.data` 只提取 `type` / `state.status` / `time_updated` 三字段；绝不读取/存储/展示 prompt、回复文本、工具命令、reasoning 内容；标题仅取 `session.title`。
- 状态决策树（spec 定稿）：tool+pending→等待同意；tool+running→进行中；text/reasoning/patch/step-start 且 age≤90s→进行中；其余 part（step-finish/tool completed|error/超窗任意 part）→进程在?等待输入:已完成；无 part→进程在?空闲:未检测到。
- 活动窗口 90s（`activity_window_seconds=90.0`，注入可调）；完成判定 = 进程退出 + 90s 缓冲。
- 会话发现：`WHERE time_archived IS NULL AND parent_id IS NULL`（子会话不显示），按 time_updated 倒序 LIMIT 50，卡片最多 20（运行中优先，与 Kimi 一致）。
- task_id = `opencode:<session_id>`；agent type `opencode_cli`，display_name "OpenCode"；标题截断 20 字符。
- 进程检测：`CachedProcessAlive("name", ...)`，macOS 匹配 `value == "opencode"`，win32 匹配 `^opencode(\.exe)?$`（忽略大小写）；每轮轮询只查一次。
- db 缺失/损坏/不可读 → 空列表/健康标记，不阻塞面板；SQLite 错误抛 `OpenCodeDiscoveryError`（service 捕获降级）。
- `models.py` `AppSettings.visible_agent_types` 默认加 `"opencode_cli"`。
- 时间造假相对当前时刻回拨（`now()` 注入 + `timedelta` 相对断言）；epoch ms 转 UTC datetime。
- TDD：每任务先失败测试；CI diff-cover 改动行覆盖率 ≥90%。
- i18n 双语成对：新增 `settings.select_opencode`；状态文案复用现有 `status.*` 键；卡片 message 硬编码中文（跟随 Kimi）。
- 提交信息英文 `feat:` / `fix:` / `docs:`。
- 本机全绿：`.venv/bin/python -m pytest -q`、`.venv/bin/ruff check src tests`、`.venv/bin/ruff format --check src tests`、`.venv/bin/mypy src/aacc`。

## File Structure

| 文件 | 责任 |
|---|---|
| Create `src/aacc/opencode_discovery.py` | 只读 SQLite 发现 + 状态决策树（`OpenCodeLocalDiscovery` / `evaluate_opencode_session_status` / `OpenCodePartSnapshot`） |
| Modify `src/aacc/discovery_service.py` | `OpenCodeDiscoveryService`（LocalDiscoveryService 子类，仿 KimiDesktopDiscoveryService） |
| Modify `src/aacc/models.py` | `visible_agent_types` 默认加 `opencode_cli` |
| Modify `src/aacc/i18n.py` | `settings.select_opencode` 双语键 |
| Modify `src/aacc/gui.py` | 设置对话框按钮、visible 标签、MainWindow 参数/信号/接线、`opencode:` 前缀判断（L929/L1083） |
| Modify `src/aacc/app.py` | Runtime 字段 + close + 装配 + 启动 |
| Create `tests/test_opencode_discovery.py` | 决策树 + 发现 + 只读性 + 内容边界测试 |
| Modify `tests/test_discovery_service.py` | opencode service 轮询/集合/健康测试 |
| Modify `tests/test_gui.py` / `tests/test_gui_quota_wiring.py` | visible 默认、设置按钮、接线 |
| Modify `tests/test_app.py` | Runtime 装配测试 |

设计文档：`docs/superpowers/specs/2026-07-31-opencode-discovery-design.md`（已提交 3b635cb）。

---

## Task 1: 纯发现与状态决策 `opencode_discovery.py`

**Files:**
- Create: `src/aacc/opencode_discovery.py`
- Test: `tests/test_opencode_discovery.py`

**Interfaces:**
- Consumes: `aacc.codex_discovery.DiscoveredTask`、`aacc.models.{AgentConfig, TaskConfig, TaskState, TaskStatus, TerminalConfig}`、`aacc.processes.CachedProcessAlive`、`aacc.kimi_discovery.{Clock, ProcessAlive}`
- Produces:
  - `OpenCodeDiscoveryError(RuntimeError)`
  - `OpenCodeSession` frozen dataclass：`session_id: str`、`title: str`、`work_dir: str | None`、`agent: str | None`、`model: str | None`、`updated_at: datetime`
  - `OpenCodePartSnapshot` frozen dataclass：`part_type: str | None`、`state_status: str | None`、`time_updated: datetime | None`
  - `OpenCodeSessionStatus` frozen dataclass：`status: TaskStatus`、`message: str`、`confidence: float`、`activity_at: datetime | None`
  - `evaluate_opencode_session_status(snapshot, *, now, process_alive, activity_window_seconds) -> OpenCodeSessionStatus`
  - `OpenCodeLocalDiscovery(db_path=None, *, now=..., process_alive=..., activity_window_seconds=90.0, max_tasks=20, connect_factory=None)`：`discover(selected_ids=None) -> list[DiscoveredTask]`、`catalog() -> list[OpenCodeSession]`、`active_session_ids(*, limit=4) -> set[str]`
  - 默认 `db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"`；`connect_factory` 默认 `lambda path: sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)`（注入用）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opencode_discovery.py
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aacc.models import TaskStatus
from aacc.opencode_discovery import (
    OpenCodePartSnapshot,
    OpenCodeSessionStatus,
    evaluate_opencode_session_status,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _snapshot(part_type: str | None, state: str | None, age_seconds: float | None) -> OpenCodePartSnapshot:
    now = _now()
    updated = now - timedelta(seconds=age_seconds) if age_seconds is not None else None
    return OpenCodePartSnapshot(part_type, state, updated)


def _evaluate(snapshot, *, alive: bool, window: float = 90.0) -> OpenCodeSessionStatus:
    return evaluate_opencode_session_status(
        snapshot,
        now=_now(),
        process_alive=lambda: alive,
        activity_window_seconds=window,
    )


def test_pending_tool_is_waiting_approval() -> None:
    result = _evaluate(_snapshot("tool", "pending", 5), alive=True)
    assert result.status is TaskStatus.WAITING_APPROVAL
    assert result.confidence == 0.97


def test_running_tool_is_running() -> None:
    result = _evaluate(_snapshot("tool", "running", 5), alive=True)
    assert result.status is TaskStatus.RUNNING


def test_streaming_parts_within_window_are_running() -> None:
    for part_type in ("text", "reasoning", "patch", "step-start"):
        result = _evaluate(_snapshot(part_type, None, 30), alive=True)
        assert result.status is TaskStatus.RUNNING, part_type


def test_stale_streaming_part_with_process_is_waiting_input() -> None:
    result = _evaluate(_snapshot("text", None, 300), alive=True)
    assert result.status is TaskStatus.WAITING_INPUT


def test_stale_streaming_part_without_process_is_completed() -> None:
    result = _evaluate(_snapshot("text", None, 300), alive=False)
    assert result.status is TaskStatus.COMPLETED
    assert result.confidence == 0.92


def test_step_finish_with_process_is_waiting_input() -> None:
    result = _evaluate(_snapshot("step-finish", None, 10), alive=True)
    assert result.status is TaskStatus.WAITING_INPUT


def test_tool_completed_without_process_is_completed() -> None:
    result = _evaluate(_snapshot("tool", "completed", 10), alive=False)
    assert result.status is TaskStatus.COMPLETED


def test_no_parts_with_process_is_idle() -> None:
    result = _evaluate(None, alive=True)
    assert result.status is TaskStatus.IDLE


def test_no_parts_without_process_is_unknown() -> None:
    result = _evaluate(None, alive=False)
    assert result.status is TaskStatus.UNKNOWN


def test_activity_at_uses_part_timestamp() -> None:
    snapshot = _snapshot("text", None, 42)
    result = _evaluate(snapshot, alive=True)
    assert result.activity_at == snapshot.time_updated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_opencode_discovery.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'aacc.opencode_discovery'`）

- [ ] **Step 3: Write minimal implementation（决策树部分）**

```python
# src/aacc/opencode_discovery.py
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
        return OpenCodeSessionStatus(TaskStatus.WAITING_APPROVAL, "等待同意", 0.97, snapshot.time_updated)
    if snapshot.part_type == "tool" and snapshot.state_status == "running":
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.95, snapshot.time_updated)
    active = (now - snapshot.time_updated).total_seconds() <= activity_window_seconds
    if snapshot.part_type in _STREAMING_PART_TYPES and active:
        return OpenCodeSessionStatus(TaskStatus.RUNNING, "正在运行", 0.9, snapshot.time_updated)
    if process_alive():
        return OpenCodeSessionStatus(TaskStatus.WAITING_INPUT, "等待输入", 0.85, snapshot.time_updated)
    return OpenCodeSessionStatus(TaskStatus.COMPLETED, "已完成", 0.92, snapshot.time_updated)
```

- [ ] **Step 4: Run tests to verify they pass（决策树部分）**

Run: `.venv/bin/python -m pytest tests/test_opencode_discovery.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: Write the failing discovery tests**

```python
# tests/test_opencode_discovery.py（追加；文件头已有 datetime/UTC/timedelta/pytest/Path imports，勿重复）
import sqlite3


def _make_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    path = tmp_path / "opencode.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "title TEXT NOT NULL, directory TEXT, agent TEXT, model TEXT, "
        "parent_id TEXT, time_archived INTEGER, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    return path, connection


def _add_session(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    title: str = "测试会话",
    directory: str | None = None,
    agent: str = "build",
    parent_id: str | None = None,
    archived: bool = False,
    updated: datetime | None = None,
) -> None:
    updated = updated or datetime.now(UTC)
    connection.execute(
        "INSERT INTO session (id, project_id, title, directory, agent, model, "
        "parent_id, time_archived, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            "p1",
            title,
            directory,
            agent,
            None,
            parent_id,
            1 if archived else None,
            int(updated.timestamp() * 1000),
            int(updated.timestamp() * 1000),
        ),
    )


def _add_part(
    connection: sqlite3.Connection,
    session_id: str,
    part_id: str,
    data: dict[str, Any],
    updated: datetime | None = None,
) -> None:
    import json

    updated = updated or datetime.now(UTC)
    connection.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
        "VALUES (?,?,?,?,?,?)",
        (
            part_id,
            f"msg_{part_id}",
            session_id,
            int(updated.timestamp() * 1000),
            int(updated.timestamp() * 1000),
            json.dumps(data),
        ),
    )


def test_discover_reports_waiting_approval(tmp_path: Path, monkeypatch) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    _add_session(connection, "ses_1", title="任务一", directory="/work/a")
    _add_part(connection, "ses_1", "prt_1", {"type": "tool", "state": {"status": "pending"}}, updated=datetime.now(UTC))
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    tasks = discovery.discover()
    assert len(tasks) == 1
    assert tasks[0].config.id == "opencode:ses_1"
    assert tasks[0].state.status is TaskStatus.WAITING_APPROVAL
    assert tasks[0].config.agent.type == "opencode_cli"
    assert tasks[0].config.agent.display_name == "OpenCode"
    assert tasks[0].state.session_id == "ses_1"


def test_discover_filters_children_and_archived(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_1", title="主会话", updated=now)
    _add_session(connection, "ses_2", title="子会话", parent_id="ses_1", updated=now - timedelta(seconds=10))
    _add_session(connection, "ses_3", title="归档会话", archived=True, updated=now - timedelta(seconds=20))
    _add_part(connection, "ses_1", "prt_1", {"type": "text"}, updated=now)
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    tasks = discovery.discover()
    assert [task.config.id for task in tasks] == ["opencode:ses_1"]


def test_discover_orders_by_recency_with_running_first(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_old", title="旧会话", updated=now - timedelta(minutes=5))
    _add_session(connection, "ses_new", title="新会话", updated=now - timedelta(minutes=1))
    _add_part(connection, "ses_old", "prt_1", {"type": "tool", "state": {"status": "running"}}, updated=now - timedelta(minutes=5))
    _add_part(connection, "ses_new", "prt_2", {"type": "step-finish"}, updated=now - timedelta(minutes=1))
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    tasks = discovery.discover()
    assert [task.config.id for task in tasks] == ["opencode:ses_old", "opencode:ses_new"]
    assert tasks[0].state.status is TaskStatus.RUNNING


def test_discover_selects_only_requested_ids(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_1", title="一", updated=now)
    _add_session(connection, "ses_2", title="二", updated=now)
    _add_part(connection, "ses_1", "prt_1", {"type": "text"}, updated=now)
    _add_part(connection, "ses_2", "prt_2", {"type": "text"}, updated=now)
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    tasks = discovery.discover(selected_ids={"ses_2"})
    assert [task.config.id for task in tasks] == ["opencode:ses_2"]


def test_discover_missing_db_returns_empty(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    discovery = OpenCodeLocalDiscovery(db_path=tmp_path / "missing.db", process_alive=lambda: True)
    assert discovery.discover() == []


def test_discover_corrupt_db_raises_error(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeDiscoveryError, OpenCodeLocalDiscovery

    path = tmp_path / "bad.db"
    path.write_bytes(b"not a sqlite database")
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    with pytest.raises(OpenCodeDiscoveryError):
        discovery.discover()


def test_connect_opens_read_only(tmp_path: Path, monkeypatch) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    _add_session(connection, "ses_1", title="一")
    _add_part(connection, "ses_1", "prt_1", {"type": "text"})
    connection.commit()
    connection.close()

    opened: list[tuple[str, bool]] = []

    def spy_connect(db_path: Path) -> sqlite3.Connection:
        real = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        opened.append((str(db_path), True))
        return real

    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True, connect_factory=spy_connect)
    assert discovery.discover() != []
    assert opened and opened[0][1] is True


def test_content_boundary_never_reads_text_content(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_1", title="会话", directory="/work")
    _add_part(
        connection,
        "ses_1",
        "prt_1",
        {
            "type": "text",
            "text": "SECRET-PROMPT-CONTENT-不应被读取",
        },
        updated=now - timedelta(seconds=10),
    )
    _add_part(
        connection,
        "ses_1",
        "prt_0",
        {
            "type": "tool",
            "state": {"status": "running", "input": {"command": "cat ~/.ssh/id_rsa"}},
        },
        updated=now,
    )
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    tasks = discovery.discover()
    assert len(tasks) == 1
    assert tasks[0].state.status is TaskStatus.RUNNING
    assert "SECRET" not in str(tasks[0].state.metadata)


def test_catalog_returns_sessions_sorted(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_1", title="旧", updated=now - timedelta(minutes=3))
    _add_session(connection, "ses_2", title="新", updated=now - timedelta(minutes=1))
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    catalog = discovery.catalog()
    assert [item.session_id for item in catalog] == ["ses_2", "ses_1"]


def test_active_session_ids_returns_running_only(tmp_path: Path) -> None:
    from aacc.opencode_discovery import OpenCodeLocalDiscovery

    path, connection = _make_db(tmp_path)
    now = datetime.now(UTC)
    _add_session(connection, "ses_run", title="运行", updated=now)
    _add_session(connection, "ses_wait", title="等待", updated=now)
    _add_part(connection, "ses_run", "prt_1", {"type": "tool", "state": {"status": "running"}}, updated=now)
    _add_part(connection, "ses_wait", "prt_2", {"type": "tool", "state": {"status": "pending"}}, updated=now)
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    assert discovery.active_session_ids() == {"ses_run"}
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_opencode_discovery.py -q`
Expected: FAIL（`OpenCodeLocalDiscovery` 不存在 / `discover` 不存在）

- [ ] **Step 7: Write minimal implementation（发现部分，追加到 opencode_discovery.py）**

```python
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

    def _latest_part_snapshots(
        self, session_ids: list[str]
    ) -> dict[str, OpenCodePartSnapshot]:
        if not session_ids or not self.db_path.exists():
            return {}
        snapshots: dict[str, OpenCodePartSnapshot] = {}
        try:
            connection = self._connect(self.db_path)
            try:
                for session_id in session_ids:
                    row = connection.execute(
                        _LATEST_PART_QUERY, (session_id,)
                    ).fetchone()
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
```

注意：`_parse_part_snapshot` 只提取 `type`/`state.status`（`time_updated` 由 SQL 的 `time_updated` 列提供，见上方两列查询）。

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_opencode_discovery.py -q`
Expected: PASS（全部通过）

- [ ] **Step 9: Full gate + Commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/mypy src/aacc`
Expected: 全绿

```bash
git add src/aacc/opencode_discovery.py tests/test_opencode_discovery.py
git commit -m "feat: add opencode task discovery from local sqlite"
```

---

## Task 2: 轮询服务 `OpenCodeDiscoveryService`

**Files:**
- Modify: `src/aacc/discovery_service.py`（末尾，`KimiDesktopDiscoveryService` 之后）
- Test: `tests/test_discovery_service.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `OpenCodeLocalDiscovery` / `OpenCodeDiscoveryError` / `OpenCodeSession`；`LocalDiscoveryService[SessionT]` 泛型基类（现有）
- Produces: `OpenCodeDiscoveryService(LocalDiscoveryService[OpenCodeSession])`：`__init__(manager, *, discovery: OpenCodeLocalDiscovery | None = None, interval_seconds: float = 5.0)`，brand `"OpenCode"`，thread_name `"aacc-opencode-discovery"`，error_type `OpenCodeDiscoveryError`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discovery_service.py（追加；沿用文件现有 imports 与 helper，缺什么补什么）
from aacc.discovery_service import OpenCodeDiscoveryService
from aacc.opencode_discovery import OpenCodeDiscoveryError, OpenCodeLocalDiscovery


class _FakeOpenCodeDiscovery:
    def __init__(self) -> None:
        self.calls = 0
        self.selected: set[str] | None = None

    def discover(self, selected_ids: set[str] | None = None):
        self.calls += 1
        self.selected = selected_ids
        return []

    def active_session_ids(self) -> set[str]:
        return set()

    def catalog(self) -> list[object]:
        return []


def test_opencode_service_polls_and_forwards_selection(tmp_path) -> None:
    discovery = _FakeOpenCodeDiscovery()
    service = OpenCodeDiscoveryService(
        _manager_for(tmp_path),
        discovery=discovery,  # type: ignore[arg-type]
        interval_seconds=0.5,
    )
    service.set_selected_ids({"ses_1"})
    service.poll_once()
    assert discovery.calls == 1
    assert discovery.selected == {"ses_1"}
    assert service.brand == "OpenCode"
    service.stop()


def test_opencode_service_tolerates_discovery_errors(tmp_path, caplog) -> None:
    class _BoomDiscovery(_FakeOpenCodeDiscovery):
        def discover(self, selected_ids=None):
            raise OpenCodeDiscoveryError("boom")

    service = OpenCodeDiscoveryService(
        _manager_for(tmp_path),
        discovery=_BoomDiscovery(),  # type: ignore[arg-type]
        interval_seconds=0.5,
    )
    assert service.poll_safely() == 0
    health = service.health()
    assert health.ok is False
    service.stop()
```

`_manager_for(tmp_path)` 用现有测试文件的 manager 构造 helper（若 test_discovery_service.py 已有，直接复用；否则：

```python
def _manager_for(tmp_path):
    from aacc.config import default_config
    from aacc.persistence import StateStore
    from aacc.task_manager import TaskManager

    config = default_config()
    store = StateStore(tmp_path / "disc.db")
    store.initialize(config.tasks)
    return TaskManager(config, store)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_service.py -q`
Expected: FAIL（`ImportError: cannot import name 'OpenCodeDiscoveryService'`）

- [ ] **Step 3: Write minimal implementation**

```python
# src/aacc/discovery_service.py 末尾追加
from aacc.opencode_discovery import OpenCodeDiscoveryError, OpenCodeLocalDiscovery, OpenCodeSession


class OpenCodeDiscoveryService(LocalDiscoveryService[OpenCodeSession]):
    """Polls local opencode metadata outside the Qt event loop."""

    def __init__(
        self,
        manager: TaskManager,
        *,
        discovery: OpenCodeLocalDiscovery | None = None,
        interval_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            manager,
            discovery=discovery or OpenCodeLocalDiscovery(),
            interval_seconds=interval_seconds,
            thread_name="aacc-opencode-discovery",
            error_type=OpenCodeDiscoveryError,
            brand="OpenCode",
        )
```

（import 追加到文件顶部 import 区，与其他 discovery import 并列。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_service.py -q`
Expected: PASS

- [ ] **Step 5: Full gate + Commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/mypy src/aacc`
Expected: 全绿

```bash
git add src/aacc/discovery_service.py tests/test_discovery_service.py
git commit -m "feat: add opencode discovery polling service"
```

---

## Task 3: GUI 与装配（agent 类型 + 设置入口 + app.py）

**Files:**
- Modify: `src/aacc/models.py`（`AppSettings.visible_agent_types` 默认 L58-60）
- Modify: `src/aacc/i18n.py`（`settings.select_opencode` 双语键）
- Modify: `src/aacc/gui.py`（L929/L1083 前缀、L1200 labels、L1162 附近设置按钮、MainWindow 参数/信号/属性/接线、open_opencode_task_selector）
- Modify: `src/aacc/app.py`（Runtime 字段 + close + 装配 + MainWindow 传参 + 启动）
- Test: `tests/test_gui.py`（追加）、`tests/test_app.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `OpenCodeDiscoveryService`；Task 1 的 `OpenCodeSession`
- Produces:
  - `AppSettings.visible_agent_types` 默认 = `["codex_cli", "kimi_code", "kimi_desktop", "opencode_cli"]`
  - MainWindow 新参数：`opencode_sessions` / `opencode_auto_active_ids` / `opencode_retained_ids` / `opencode_muted_ids` / `set_opencode_monitoring_preferences` / `opencode_discovery_health` / `subscribe_opencode_discovery_health`（全部可空，仿 kimi_desktop 参数块 L1380-1396）
  - 新信号 `opencode_discovery_health_received`；属性 `self.opencode_selected_ids`（仿 kimi_desktop）
  - 方法 `open_opencode_task_selector()`（仿 `open_kimi_desktop_task_selector`，window_title_key="settings.select_opencode"）
  - i18n 键：`settings.select_opencode` zh `"选择监控的 OpenCode 任务"` / en `"Select OpenCode tasks to monitor"`
  - app.py：`Runtime.opencode_discovery: OpenCodeDiscoveryService`；close 阶段 `"opencode-discovery"`；build_runtime 装配 `opencode_discovery=OpenCodeDiscoveryService(manager)`；MainWindow 传参；启动序列加 `runtime.opencode_discovery.start()`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui.py（追加）
def test_opencode_agent_visible_by_default() -> None:
    from aacc.config import default_config

    config = default_config()
    assert "opencode_cli" in config.app.visible_agent_types


def test_settings_dialog_shows_opencode_selector(qtbot, tmp_path) -> None:
    from aacc.gui import SettingsDialog
    from tests.test_gui import build_window

    window, manager = build_window(tmp_path, qtbot)
    window.opencode_sessions = lambda: []  # type: ignore[method-assign]
    dialog = SettingsDialog(window)
    texts = [button.text() for button in dialog.findChildren(QPushButton)]
    assert any("OpenCode" in text for text in texts)
    manager.close()
```

```python
# tests/test_app.py（追加）
def test_runtime_wires_opencode_discovery(tmp_path) -> None:
    from aacc.app import build_runtime
    from aacc.config import create_default_config

    config_path = tmp_path / "config.yaml"
    create_default_config(config_path)
    runtime = build_runtime(
        config_path,
        tmp_path / "aacc.db",
        quota_service_factory=lambda config_dir: None,
        kimi_web_quota_service_factory=lambda config_dir: None,
        codex_quota_service_factory=lambda: None,
    )
    assert runtime.opencode_discovery is not None
    runtime.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui.py -k opencode tests/test_app.py -k opencode -q`
Expected: FAIL（visible 默认缺 opencode_cli / SettingsDialog 无 OpenCode 按钮 / Runtime 无 opencode_discovery 属性）

- [ ] **Step 3: Write minimal implementation（models.py + i18n.py + gui.py + app.py）**

3a. `src/aacc/models.py` L58-60：

```python
    visible_agent_types: list[str] = Field(
        default_factory=lambda: ["codex_cli", "kimi_code", "kimi_desktop", "opencode_cli"]
    )
```

3b. `src/aacc/i18n.py`（zh catalog `"settings.select_kimi_desktop"` 之后）：

```python
        "settings.select_opencode": "选择监控的 OpenCode 任务",
```

en catalog 对应：

```python
        "settings.select_opencode": "Select OpenCode tasks to monitor",
```

3c. `src/aacc/gui.py`：
- import 区加 `from aacc.opencode_discovery import OpenCodeSession`（与 `KimiDesktopSession` import 并列）
- L929 与 L1083 的元组加 `"opencode:"`：

```python
        if task.id.startswith(("codex:", "kimi:", "kimi_desktop:", "opencode:")):
```

（两处均改）
- L1200 labels dict 加：

```python
            "opencode_cli": "OpenCode",
```

- `KimiDesktopTaskSelectionDialog`（L1329）之前加 `OpenCodeTaskSelectionDialog`：

```python
class OpenCodeTaskSelectionDialog(TaskSelectionDialog):
    def __init__(
        self,
        sessions: list[OpenCodeSession],
        selected_ids: set[str],
        auto_active_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(
            [(session.session_id, session.title, session.updated_at) for session in sessions],
            selected_ids,
            auto_active_ids,
            parent,
            window_title_key="settings.select_opencode",
        )
```

- 设置对话框（L1171 `kimi_desktop_tasks` 块之后）加：

```python
        opencode_tasks = QPushButton(
            language.text("settings.select_opencode")
            + language.text(
                "settings.selected_counts",
                selected=len(window.opencode_selected_ids),
                automatic=len(window.opencode_auto_active_ids()),
            )
        )
        opencode_tasks.clicked.connect(window.open_opencode_task_selector)
        layout.addWidget(opencode_tasks)
```

- MainWindow 信号（L1352 `kimi_desktop_discovery_health_received` 之后）加：

```python
    opencode_discovery_health_received = Signal(object)
```

- MainWindow `__init__` 参数（kimi_desktop 参数块之后）加：

```python
        opencode_sessions: Callable[[], list[OpenCodeSession]] | None = None,
        opencode_auto_active_ids: Callable[[], set[str]] | None = None,
        opencode_retained_ids: Callable[[], set[str]] | None = None,
        opencode_muted_ids: Callable[[], set[str]] | None = None,
        set_opencode_monitoring_preferences: (
            Callable[[set[str], set[str], set[str]], None] | None
        ) = None,
        opencode_discovery_health: Callable[[], DiscoveryHealth] | None = None,
        subscribe_opencode_discovery_health: (
            Callable[[HealthSubscriber], Callable[[], None]] | None
        ) = None,
```

- 属性区（kimi_desktop 属性之后）加：

```python
        self._opencode_sessions = opencode_sessions or (lambda: [])
        self._opencode_auto_active_ids = opencode_auto_active_ids or (lambda: set())
        self._opencode_retained_ids = opencode_retained_ids or (lambda: set())
        self._opencode_muted_ids = opencode_muted_ids or (lambda: set())
        self._set_opencode_monitoring_preferences = (
            set_opencode_monitoring_preferences or (lambda _m, _r, _u: None)
        )
        self._unsubscribe_opencode_discovery_health = (
            subscribe_opencode_discovery_health(
                self.opencode_discovery_health_received.emit
            )
            if subscribe_opencode_discovery_health is not None
            else lambda: None
        )
```

- QSettings 恢复块（kimi_desktop 恢复块 L1528-1549 之后，`self._apply_kimi_desktop_monitoring_preferences()` 之后）加：

```python
        saved_opencode_tasks = self._settings.value("opencode_manual_tasks")
        if isinstance(saved_opencode_tasks, str):
            self.opencode_manual_ids = {saved_opencode_tasks}
        elif isinstance(saved_opencode_tasks, list):
            self.opencode_manual_ids = {str(value) for value in saved_opencode_tasks}
        else:
            self.opencode_manual_ids = set()
        saved_opencode_retained = self._settings.value("opencode_retained_tasks")
        if isinstance(saved_opencode_retained, str):
            self.opencode_retained_ids = {saved_opencode_retained}
        elif isinstance(saved_opencode_retained, list):
            self.opencode_retained_ids = {str(value) for value in saved_opencode_retained}
        else:
            self.opencode_retained_ids = set()
        saved_opencode_muted = self._settings.value("opencode_muted_tasks")
        if isinstance(saved_opencode_muted, str):
            self.opencode_muted_ids = {saved_opencode_muted}
        elif isinstance(saved_opencode_muted, list):
            self.opencode_muted_ids = {str(value) for value in saved_opencode_muted}
        else:
            self.opencode_muted_ids = set()
        self.opencode_selected_ids = set(self.opencode_manual_ids)
        self._apply_opencode_monitoring_preferences()
```

- 偏好应用/同步方法（`_sync_kimi_desktop_muted_ids` 之后）加：

```python
    def set_opencode_selected_ids(self, selected_ids: set[str]) -> None:
        self.set_opencode_monitoring_preferences(selected_ids, set(), set())

    def set_opencode_monitoring_preferences(
        self, manual_ids: set[str], retained_ids: set[str], muted_ids: set[str]
    ) -> None:
        self.opencode_manual_ids = set(manual_ids)
        self.opencode_retained_ids = set(retained_ids) - self.opencode_manual_ids
        self.opencode_muted_ids = set(muted_ids) - self.opencode_manual_ids
        self.opencode_selected_ids = set(self.opencode_manual_ids)
        self._settings.setValue("opencode_manual_tasks", sorted(self.opencode_manual_ids))
        self._settings.setValue(
            "opencode_retained_tasks", sorted(self.opencode_retained_ids)
        )
        self._settings.setValue("opencode_muted_tasks", sorted(self.opencode_muted_ids))
        self._apply_opencode_monitoring_preferences()
        self.sync_cards()

    def _apply_opencode_monitoring_preferences(self) -> None:
        self._set_opencode_monitoring_preferences(
            self.opencode_manual_ids,
            self.opencode_retained_ids,
            self.opencode_muted_ids,
        )

    def _sync_opencode_retained_ids(self) -> None:
        retained_ids = self._opencode_retained_ids()
        if retained_ids != self.opencode_retained_ids:
            self.opencode_retained_ids = set(retained_ids)
            self._settings.setValue(
                "opencode_retained_tasks", sorted(self.opencode_retained_ids)
            )

    def _sync_opencode_muted_ids(self) -> None:
        muted_ids = self._opencode_muted_ids()
        if muted_ids != self.opencode_muted_ids:
            self.opencode_muted_ids = set(muted_ids)
            self._settings.setValue(
                "opencode_muted_tasks", sorted(self.opencode_muted_ids)
            )
```

- refresh 流程（L1902-1903 `_sync_kimi_desktop_*` 之后）加：

```python
        self._sync_opencode_retained_ids()
        self._sync_opencode_muted_ids()
```

- 打开选择器方法（`open_kimi_desktop_task_selector` 之后）加：

```python
    def open_opencode_task_selector(self) -> None:
        auto_active_ids = self._opencode_auto_active_ids()
        dialog = OpenCodeTaskSelectionDialog(
            self._opencode_sessions(),
            self.opencode_selected_ids,
            auto_active_ids,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dialog.selected_ids()
            retained_ids = self.opencode_retained_ids & selected_ids
            manual_ids = (self.opencode_manual_ids & selected_ids) | (
                selected_ids - auto_active_ids - retained_ids
            )
            muted_ids = (
                self.opencode_muted_ids | (auto_active_ids - selected_ids)
            ) - selected_ids
            if dialog.restore_auto_requested():
                muted_ids -= auto_active_ids
            self.set_opencode_monitoring_preferences(manual_ids, retained_ids, muted_ids)
```

- `_request_quota_refresh_on_restore` 或 discovery 相关无需改（discovery 由 app 层启动）。

3d. `src/aacc/app.py`：
- import 加 `from aacc.opencode_discovery import OpenCodeLocalDiscovery`（若 service 构造用默认）与 `from aacc.discovery_service import OpenCodeDiscoveryService`
- Runtime dataclass（L66 `kimi_desktop_discovery` 之后）加：

```python
    opencode_discovery: OpenCodeDiscoveryService
```

- close() 元组（`kimi-desktop-discovery` 之前）加：

```python
            ("opencode-discovery", self.opencode_discovery.stop),
```

- build_runtime 装配（L247 `kimi_desktop_discovery=...` 之后）加：

```python
        opencode_discovery=OpenCodeDiscoveryService(manager),
```

- MainWindow 调用（L419-420 kimi_desktop health 之后）加：

```python
        opencode_sessions=runtime.opencode_discovery.catalog,
        opencode_auto_active_ids=runtime.opencode_discovery.auto_active_ids,
        opencode_retained_ids=runtime.opencode_discovery.retained_ids,
        opencode_muted_ids=runtime.opencode_discovery.muted_ids,
        set_opencode_monitoring_preferences=runtime.opencode_discovery.set_monitoring_preferences,
        opencode_discovery_health=runtime.opencode_discovery.health,
        subscribe_opencode_discovery_health=runtime.opencode_discovery.subscribe_health,
```

- 启动序列（L516 `runtime.kimi_desktop_discovery.start()` 附近）加：

```python
            runtime.opencode_discovery.start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui.py -k opencode tests/test_app.py -k opencode -q`
Expected: PASS

- [ ] **Step 5: Full gate + Commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/mypy src/aacc`
Expected: 全绿

```bash
git add src/aacc/models.py src/aacc/i18n.py src/aacc/gui.py src/aacc/app.py tests/test_gui.py tests/test_app.py
git commit -m "feat: surface opencode tasks in panel with settings entry"
```

---

## Task 4: 手动验收（无法自动化）

**Files:**
- Modify: `docs/KNOWN_LIMITATIONS.md`（双语条目）

- [ ] **Step 1: 本机验证**

1. 确认本机 opencode db 存在（`~/.local/share/opencode/opencode.db`）。
2. 运行 AACC（源码模式：`.venv/bin/python -m aacc` 或已安装 app）。
3. 面板出现 OpenCode 任务卡片（agent 标 "OPENCODE"）：
   - 当前正在运行的会话 → 蓝色"进行中"（或黄色"等待输入"——取决于此刻是否有活跃 part）。
4. 触发等待同意：在 opencode 里发起一个需要权限确认的工具调用（或观察既有 pending part 的会话）→ AACC 卡片变黄色"等待同意"。
5. 关闭 opencode（退出 TUI）→ 等 90s 缓冲 → 卡片变绿色"已完成"。
6. 重启 AACC → 设置对话框 →「选择监控的 OpenCode 任务」列出最近会话，可勾选/取消（自动监控运行中会话）。

- [ ] **Step 2: 更新 KNOWN_LIMITATIONS（双语）**

追加：OpenCode 任务状态基于本地 `opencode.db` 的 `part` 表快照推断（官方 idle/busy 为运行时事件不落库）；
90s 活动窗口与进程退出判定为近似；opencode 网页版（opencode.ai）会话不在本地 db，无法监控。

- [ ] **Step 3: Commit**

```bash
git add docs/KNOWN_LIMITATIONS.md
git commit -m "docs: note opencode discovery inference limits"
```

---

## Self-Review

**1. Spec coverage（对照设计文档逐节）：**
- 架构/数据流（发现模块 + service + GUI + app 装配）→ Task 1/2/3。✓
- 会话发现（archived/parent_id 过滤、排序、LIMIT 50/max 20）→ Task 1 SQL + discover。✓
- 状态决策树（7 行表全部状态）→ Task 1 `evaluate_opencode_session_status` + 10 个决策测试。✓
- 安全边界（mode=ro、内容三字段、静默降级/健康）→ Task 1 `_default_connect`/`_parse_part_snapshot` + 测试（read-only spy、content boundary、corrupt db）。✓
- 测试策略（discovery/service/gui/app 四类）→ Task 1-3。✓
- 实施顺序 4 步 → Task 1-4。✓

**2. Placeholder scan：** 无 TBD/TODO；Task 3 的 `open_opencode_task_selector` 注明"以现有 open_task_selector 形态为准"——这是对现有代码形态的引用而非占位符（实现者必须读现有实现对齐）。✓

**3. Type consistency：**
- `OpenCodeSession`（Task 1 定义）在 Task 2 泛型 `LocalDiscoveryService[OpenCodeSession]` 与 Task 3 `opencode_sessions: Callable[[], list[OpenCodeSession]]` 一致。✓
- `OpenCodeDiscoveryError`（Task 1）在 Task 2 error_type 使用一致。✓
- `evaluate_opencode_session_status` 签名（snapshot/now/process_alive/activity_window_seconds）在 Task 1 定义与使用一致。✓
- `OpenCodeDiscoveryService(manager, *, discovery=None, interval_seconds=5.0)` 在 Task 2 定义、Task 3 app.py 装配（无参构造）一致。✓
- `visible_agent_types` 默认加 opencode_cli 在 Task 3 models.py 定义、test_gui 断言一致。✓
