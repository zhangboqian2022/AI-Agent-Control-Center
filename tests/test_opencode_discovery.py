from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aacc.models import TaskStatus
from aacc.opencode_discovery import (
    OpenCodePartSnapshot,
    OpenCodeSessionStatus,
    evaluate_opencode_session_status,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _snapshot(
    part_type: str | None, state: str | None, age_seconds: float | None
) -> OpenCodePartSnapshot:
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


def test_step_finish_is_completed() -> None:
    result = _evaluate(_snapshot("step-finish", None, 10), alive=True)
    assert result.status is TaskStatus.COMPLETED


def test_tool_completed_is_completed_with_or_without_process() -> None:
    result = _evaluate(_snapshot("tool", "completed", 10), alive=False)
    assert result.status is TaskStatus.COMPLETED
    result = _evaluate(_snapshot("tool", "completed", 10), alive=True)
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
    _add_part(
        connection,
        "ses_1",
        "prt_1",
        {"type": "tool", "state": {"status": "pending"}},
        updated=datetime.now(UTC),
    )
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
    _add_session(
        connection,
        "ses_2",
        title="子会话",
        parent_id="ses_1",
        updated=now - timedelta(seconds=10),
    )
    _add_session(
        connection,
        "ses_3",
        title="归档会话",
        archived=True,
        updated=now - timedelta(seconds=20),
    )
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
    _add_part(
        connection,
        "ses_old",
        "prt_1",
        {"type": "tool", "state": {"status": "running"}},
        updated=now - timedelta(minutes=5),
    )
    _add_part(
        connection,
        "ses_new",
        "prt_2",
        {"type": "step-finish"},
        updated=now - timedelta(minutes=1),
    )
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

    discovery = OpenCodeLocalDiscovery(
        db_path=path, process_alive=lambda: True, connect_factory=spy_connect
    )
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
    _add_part(
        connection,
        "ses_run",
        "prt_1",
        {"type": "tool", "state": {"status": "running"}},
        updated=now,
    )
    _add_part(
        connection,
        "ses_wait",
        "prt_2",
        {"type": "tool", "state": {"status": "pending"}},
        updated=now,
    )
    connection.commit()
    connection.close()
    discovery = OpenCodeLocalDiscovery(db_path=path, process_alive=lambda: True)
    assert discovery.active_session_ids() == {"ses_run"}
