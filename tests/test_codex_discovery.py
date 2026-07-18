import json
from datetime import UTC, datetime
from pathlib import Path

from aacc.codex_discovery import CodexLocalDiscovery
from aacc.models import TaskStatus


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
    assert tasks[0].state.metadata == {"discovered": True}


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
    assert tasks[0].state.message == "检测到 Codex 会话活动"


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
