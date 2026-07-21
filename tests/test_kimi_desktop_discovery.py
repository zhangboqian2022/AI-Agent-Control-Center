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


def test_conversations_written_through_wal_are_visible(tmp_path: Path) -> None:
    root = tmp_path / "daimon"
    db_path = _db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
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
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
            ("conv-wal", "WAL 会话", _ms(NOW - timedelta(minutes=10)), None, None),
        )
        connection.commit()
        # Keep the connection open so the WAL is not checkpointed away:
        # discovery must see rows that only exist in the WAL.
        tasks = _build(root).discover()
        assert [task.config.id for task in tasks] == ["kimi_desktop:conv-wal"]
    finally:
        connection.close()
