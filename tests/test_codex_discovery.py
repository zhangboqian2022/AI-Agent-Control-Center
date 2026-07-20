import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aacc.codex_discovery import (
    CODEX_METADATA_COMPATIBILITY,
    CodexDiscoveryError,
    CodexLocalDiscovery,
)
from aacc.models import TaskStatus

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def test_discovers_active_and_recent_codex_tasks_without_reading_session_content(
    tmp_path: Path,
) -> None:
    index = tmp_path / "session_index.jsonl"
    index.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "active-task-1234",
                        "thread_name": "修复桌面面板",
                        "updated_at": "2026-07-17T02:00:00Z",
                        "unrelated_prompt": "must not be exposed",
                    }
                ),
                "not json",
                json.dumps(
                    {
                        "id": "recent-task-5678",
                        "thread_name": "整理任务",
                        "updated_at": "2026-07-17T01:00:00Z",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text(
        json.dumps(
            [
                {"conversationId": "active-task-1234", "osPid": 321},
                {"conversationId": "stale-task", "osPid": 999},
            ]
        ),
        encoding="utf-8",
    )

    discovery = CodexLocalDiscovery(
        index,
        processes,
        pid_exists=lambda pid: pid == 321,
        now=lambda: datetime(2026, 7, 17, 3, tzinfo=UTC),
    )
    tasks = discovery.discover()

    assert [task.config.id for task in tasks] == [
        "codex:active-task-1234",
        "codex:recent-task-5678",
    ]
    assert tasks[0].config.name == "修复桌面面板"
    assert tasks[0].state.status is TaskStatus.RUNNING
    assert tasks[0].state.pid == 321
    assert tasks[1].state.status is TaskStatus.UNKNOWN
    assert tasks[1].state.message == "最近更新，未检测到运行进程"
    assert tasks[0].state.metadata == {
        "discovered": True,
        "source_event_at": "2026-07-17T02:00:00+00:00",
    }


def test_discovers_missing_title_with_safe_short_identifier(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    index.write_text(
        json.dumps({"id": "12345678-abcd", "updated_at": "2026-07-17T02:00:00Z"}),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")

    tasks = CodexLocalDiscovery(index, processes).discover()

    assert tasks[0].config.name == "Codex 任务 12345678"


def test_selected_recent_session_file_is_reported_as_running(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    conversation_id = "active-session-1234"
    index.write_text(
        json.dumps(
            {
                "id": conversation_id,
                "thread_name": "正在思考的任务",
                "updated_at": "2026-07-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    session_file = sessions / f"rollout-2026-07-18T00-00-00-{conversation_id}.jsonl"
    session_file.write_text("metadata only", encoding="utf-8")
    activity_at = datetime(2026, 7, 18, 0, 0, 30, tzinfo=UTC)

    discovery = CodexLocalDiscovery(
        index,
        processes,
        session_directory=sessions,
        now=lambda: activity_at,
        session_modified_at=lambda _path: activity_at,
    )
    tasks = discovery.discover({conversation_id})

    assert tasks[0].state.status is TaskStatus.RUNNING
    assert tasks[0].state.message == "正在分析任务"


@pytest.mark.parametrize(
    ("activity", "expected"),
    [
        ({"type": "event_msg", "payload": {"type": "patch_apply_end"}}, "正在修改代码"),
        (
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "web__run"},
            },
            "正在查询资料",
        ),
        (
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "read_mcp_resource"},
            },
            "正在检查代码",
        ),
        (
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": '{"cmd":"uv run pytest -q","secret":"private-test-sentinel"}',
                },
            },
            "正在运行测试",
        ),
        (
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "input": '{"cmd":"./scripts/build_dmg.sh","path":"private-build-sentinel"}',
                },
            },
            "正在构建程序",
        ),
        ({"type": "event_msg", "payload": {"type": "future_activity"}}, "正在分析任务"),
    ],
)
def test_recent_session_activity_is_reduced_to_a_fixed_private_summary(
    tmp_path: Path, activity: dict[str, object], expected: str
) -> None:
    index = tmp_path / "session_index.jsonl"
    conversation_id = "activity-summary"
    now = datetime(2026, 7, 20, 10, 0, 5, tzinfo=UTC)
    index.write_text(
        json.dumps(
            {
                "id": conversation_id,
                "thread_name": "隐私概括测试",
                "updated_at": "2026-07-20T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    records = [
        {
            "timestamp": "2026-07-20T10:00:00Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "prompt": "private-prompt-sentinel"},
        },
        {
            "timestamp": "2026-07-20T10:00:04Z",
            **activity,
            "private_response": "private-response-sentinel",
        },
    ]
    session_path = sessions / f"rollout-{conversation_id}.jsonl"
    session_path.write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )

    task = CodexLocalDiscovery(
        index,
        tmp_path / "missing-processes.json",
        session_directory=sessions,
        now=lambda: now,
        session_modified_at=lambda _path: now,
    ).discover({conversation_id})[0]

    assert task.state.status is TaskStatus.RUNNING
    assert task.state.message == expected
    assert len(task.state.message) <= 18
    for private_value in (
        "private-prompt-sentinel",
        "private-response-sentinel",
        "private-test-sentinel",
        "private-build-sentinel",
    ):
        assert private_value not in task.state.message


def test_malformed_recent_activity_falls_back_without_exposing_content(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    conversation_id = "malformed-activity"
    now = datetime(2026, 7, 20, 10, 0, 5, tzinfo=UTC)
    index.write_text(
        json.dumps({"id": conversation_id, "updated_at": "2026-07-20T10:00:00Z"}),
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / f"rollout-{conversation_id}.jsonl").write_text(
        "not-json private-malformed-sentinel\n"
        + json.dumps(
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started"},
            }
        ),
        encoding="utf-8",
    )

    task = CodexLocalDiscovery(
        index,
        tmp_path / "missing-processes.json",
        session_directory=sessions,
        now=lambda: now,
        session_modified_at=lambda _path: now,
    ).discover({conversation_id})[0]

    assert task.state.message == "正在分析任务"
    assert "private-malformed-sentinel" not in task.state.message


def test_discovery_only_returns_explicitly_selected_tasks(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    index.write_text(
        "\n".join(
            [
                json.dumps(
                    {"id": "chosen", "thread_name": "已选择", "updated_at": "2026-07-18T00:00:00Z"}
                ),
                json.dumps(
                    {"id": "ignored", "thread_name": "未选择", "updated_at": "2026-07-18T00:00:00Z"}
                ),
            ]
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")

    tasks = CodexLocalDiscovery(index, processes).discover({"chosen"})

    assert [task.state.session_id for task in tasks] == ["chosen"]


def test_catalog_deduplicates_index_rows_by_most_recent_update(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    index.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "same-task",
                        "thread_name": "旧标题",
                        "updated_at": "2026-07-18T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "id": "same-task",
                        "thread_name": "新标题",
                        "updated_at": "2026-07-18T00:02:00Z",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")

    catalog = CodexLocalDiscovery(index, processes).catalog()

    assert [(session.conversation_id, session.title) for session in catalog] == [
        ("same-task", "新标题")
    ]


def test_stale_task_started_event_is_not_reported_as_running(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    conversation_id = "stale-started"
    started_at = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    index.write_text(
        json.dumps(
            {
                "id": conversation_id,
                "thread_name": "过期任务",
                "updated_at": started_at.isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    session_file = sessions / f"rollout-{conversation_id}.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "timestamp": started_at.isoformat().replace("+00:00", "Z"),
                "type": "event_msg",
                "payload": {"type": "task_started"},
            }
        ),
        encoding="utf-8",
    )

    task = CodexLocalDiscovery(
        index,
        processes,
        session_directory=sessions,
        now=lambda: datetime(2026, 7, 18, 0, 10, tzinfo=UTC),
        session_modified_at=lambda _path: started_at,
        activity_window_seconds=90,
    ).discover({conversation_id})[0]

    assert task.state.status is TaskStatus.UNKNOWN


def test_active_session_ids_include_only_recent_verified_activity(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    active_id = "active-now"
    stale_id = "old-start"
    now = datetime(2026, 7, 18, 0, 2, tzinfo=UTC)
    index.write_text(
        "\n".join(
            [
                json.dumps({"id": active_id, "updated_at": now.isoformat().replace("+00:00", "Z")}),
                json.dumps({"id": stale_id, "updated_at": "2026-07-17T00:00:00Z"}),
            ]
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / f"rollout-{active_id}.jsonl").write_text(
        json.dumps(
            {
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "type": "event_msg",
                "payload": {"type": "task_started"},
            }
        ),
        encoding="utf-8",
    )
    (sessions / f"rollout-{stale_id}.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-07-17T00:00:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started"},
            }
        ),
        encoding="utf-8",
    )

    discovery = CodexLocalDiscovery(
        index,
        processes,
        session_directory=sessions,
        now=lambda: now,
        session_modified_at=lambda path: (
            now if active_id in path.name else datetime(2026, 7, 17, tzinfo=UTC)
        ),
    )

    assert discovery.active_session_ids() == {active_id}


def test_completed_session_event_overrides_recent_file_activity(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    conversation_id = "finished-session"
    index.write_text(
        json.dumps(
            {
                "id": conversation_id,
                "thread_name": "已完成任务",
                "updated_at": "2026-07-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text("[]", encoding="utf-8")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    completed_at = datetime(2026, 7, 18, 0, 1, tzinfo=UTC)
    (sessions / f"rollout-2026-07-18T00-00-00-{conversation_id}.jsonl").write_text(
        json.dumps(
            {
                "timestamp": completed_at.isoformat().replace("+00:00", "Z"),
                "type": "event_msg",
                "payload": {"type": "task_complete"},
            }
        ),
        encoding="utf-8",
    )

    tasks = CodexLocalDiscovery(
        index,
        processes,
        session_directory=sessions,
        now=lambda: datetime(2026, 7, 18, 0, 1, 30, tzinfo=UTC),
        session_modified_at=lambda _path: completed_at,
    ).discover({conversation_id})

    assert tasks[0].state.status is TaskStatus.COMPLETED
    assert tasks[0].state.message == "已完成"


def test_pid_with_record_start_is_rejected_when_live_start_unknown(tmp_path: Path) -> None:
    index = tmp_path / "session_index.jsonl"
    index.write_text(
        json.dumps({"id": "conversation-1", "updated_at": "2026-07-18T00:00:00Z"}),
        encoding="utf-8",
    )
    processes = tmp_path / "chat_processes.json"
    processes.write_text(
        json.dumps(
            [
                {
                    "conversationId": "conversation-1",
                    "osPid": 321,
                    "startedAtMs": 1_752_800_000_000,
                }
            ]
        ),
        encoding="utf-8",
    )
    discovery = CodexLocalDiscovery(
        index,
        processes,
        pid_exists=lambda _pid: True,
        process_started_at=lambda _pid: None,
    )

    assert discovery._active_pids({"conversation-1"}) == {}


def test_existing_unreadable_session_index_raises_discovery_error(tmp_path: Path) -> None:
    index_directory = tmp_path / "session_index.jsonl"
    index_directory.mkdir()
    discovery = CodexLocalDiscovery(index_directory, tmp_path / "processes.json")

    with pytest.raises(CodexDiscoveryError, match="session index"):
        discovery.catalog()


def test_missing_session_index_is_empty_first_run(tmp_path: Path) -> None:
    discovery = CodexLocalDiscovery(tmp_path / "missing-index.jsonl", tmp_path / "processes.json")
    assert discovery.catalog() == []
    assert CODEX_METADATA_COMPATIBILITY == "2026-07"


def test_current_codex_metadata_fixture_parses_running_and_completed_sessions(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for fixture_name in (
        "rollout-fixture-running-0001.jsonl",
        "rollout-fixture-complete-0002.jsonl",
    ):
        (sessions / fixture_name).write_bytes((FIXTURES / fixture_name).read_bytes())

    discovery = CodexLocalDiscovery(
        FIXTURES / "session_index.jsonl",
        tmp_path / "missing-processes.json",
        session_directory=sessions,
        now=lambda: datetime(2026, 7, 20, 8, 0, 30, tzinfo=UTC),
        session_modified_at=lambda path: datetime.fromisoformat(
            "2026-07-20T08:00:01+00:00" if "running" in path.name else "2026-07-20T07:55:01+00:00"
        ),
    )

    tasks = discovery.discover()

    assert CODEX_METADATA_COMPATIBILITY == "2026-07"
    assert [(task.state.session_id, task.state.status) for task in tasks] == [
        ("fixture-running-0001", TaskStatus.RUNNING),
        ("fixture-complete-0002", TaskStatus.COMPLETED),
    ]
