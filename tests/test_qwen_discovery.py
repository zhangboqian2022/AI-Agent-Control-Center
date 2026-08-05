from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aacc.models import TaskStatus
from aacc.qwen_discovery import (
    QwenDiscoveryError,
    QwenLocalDiscovery,
    QwenUsageTracker,
    evaluate_qwen_session_status,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
SESSION_ID = "3f2a9c1e-1111-2222-3333-444455556666"
ENCODED_PROJECT = "-Users-zhangboqian-Desktop-codelight"
WORK_DIR = "/Users/zhangboqian/Desktop/codelight"


def _chat_dir(tmp_path: Path) -> Path:
    chats = tmp_path / "projects" / ENCODED_PROJECT / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    return chats


def _write_chat(
    tmp_path: Path,
    *,
    session_id: str = SESSION_ID,
    runtime: dict[str, object] | None = None,
    first_line_cwd: str | None = WORK_DIR,
) -> Path:
    chats = _chat_dir(tmp_path)
    chat = chats / f"{session_id}.jsonl"
    if first_line_cwd is None:
        chat.write_text("", encoding="utf-8")
    else:
        line = json.dumps(
            {"sessionId": session_id, "type": "user", "cwd": first_line_cwd},
            ensure_ascii=False,
        )
        chat.write_text(line + "\n", encoding="utf-8")
    if runtime is not None:
        (chats / f"{session_id}.runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    return chat


def _discovery(
    tmp_path: Path,
    *,
    process_alive: dict[int, bool] | None = None,
    mtime: datetime | None = None,
) -> QwenLocalDiscovery:
    alive = process_alive or {}
    modified = mtime if mtime is not None else NOW - timedelta(seconds=10)
    return QwenLocalDiscovery(
        qwen_home=tmp_path,
        now=lambda: NOW,
        file_modified_at=lambda _path: modified,
        session_process_alive=lambda pid: alive.get(pid, False),
    )


def test_live_process_with_recent_activity_is_running(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime={"pid": 1311, "work_dir": WORK_DIR})
    tasks = _discovery(tmp_path, process_alive={1311: True}).discover()

    assert len(tasks) == 1
    state = tasks[0].state
    assert state.task_id == f"qwen:{SESSION_ID}"
    assert state.session_id == SESSION_ID
    assert state.status is TaskStatus.RUNNING
    assert state.source == "qwen_local"
    assert state.metadata["work_dir"] == WORK_DIR
    config = tasks[0].config
    assert config.agent.type == "qwen_cli"
    assert config.agent.display_name == "Qwen Code"
    assert config.name == "codelight"


def test_live_process_with_stale_activity_is_idle(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime={"pid": 1311, "work_dir": WORK_DIR})
    discovery = _discovery(
        tmp_path,
        process_alive={1311: True},
        mtime=NOW - timedelta(seconds=600),
    )
    tasks = discovery.discover()
    assert tasks[0].state.status is TaskStatus.IDLE


def test_dead_runtime_pid_is_stopped_even_with_recent_write(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime={"pid": 58395, "work_dir": WORK_DIR})
    tasks = _discovery(tmp_path, process_alive={}).discover()
    assert tasks[0].state.status is TaskStatus.STOPPED
    assert tasks[0].state.finished_at is not None


def test_missing_runtime_with_recent_write_is_running(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime=None)
    tasks = _discovery(tmp_path).discover()
    assert tasks[0].state.status is TaskStatus.RUNNING


def test_missing_runtime_with_stale_file_is_stopped(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime=None)
    discovery = _discovery(tmp_path, mtime=NOW - timedelta(seconds=3600))
    tasks = discovery.discover()
    assert tasks[0].state.status is TaskStatus.STOPPED


def test_work_dir_falls_back_to_first_line_cwd(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime=None, first_line_cwd="/tmp/other-project")
    tasks = _discovery(tmp_path).discover()
    assert tasks[0].state.metadata["work_dir"] == "/tmp/other-project"
    assert tasks[0].config.name == "other-project"


def test_malformed_runtime_is_treated_as_missing(tmp_path: Path) -> None:
    chats = _chat_dir(tmp_path)
    chat = chats / f"{SESSION_ID}.jsonl"
    chat.write_text(json.dumps({"cwd": WORK_DIR}) + "\n", encoding="utf-8")
    (chats / f"{SESSION_ID}.runtime.json").write_text("{not json", encoding="utf-8")
    tasks = _discovery(tmp_path).discover()
    assert tasks[0].state.status is TaskStatus.RUNNING


def test_discover_filters_selected_ids(tmp_path: Path) -> None:
    _write_chat(tmp_path)
    _write_chat(tmp_path, session_id="aaaa-bbbb", runtime=None)
    tasks = _discovery(tmp_path).discover(selected_ids={"aaaa-bbbb"})
    assert [task.state.session_id for task in tasks] == ["aaaa-bbbb"]


def test_catalog_sorted_by_updated_at_descending(tmp_path: Path) -> None:
    _write_chat(tmp_path, session_id="old-session", runtime=None)
    _write_chat(tmp_path, session_id="new-session", runtime=None)
    stale = NOW - timedelta(hours=3)
    fresh = NOW - timedelta(seconds=5)
    mtimes = {"old-session": stale, "new-session": fresh}
    discovery = QwenLocalDiscovery(
        qwen_home=tmp_path,
        now=lambda: NOW,
        file_modified_at=lambda path: mtimes.get(path.stem, NOW),
        session_process_alive=lambda _pid: False,
    )
    catalog = discovery.catalog()
    assert [item.session_id for item in catalog] == ["new-session", "old-session"]
    assert catalog[0].title == "codelight"
    assert catalog[0].work_dir == WORK_DIR


def test_active_session_ids_only_running(tmp_path: Path) -> None:
    _write_chat(tmp_path, session_id="live", runtime={"pid": 1, "work_dir": WORK_DIR})
    _write_chat(tmp_path, session_id="dead", runtime={"pid": 2, "work_dir": WORK_DIR})
    discovery = _discovery(tmp_path, process_alive={1: True})
    assert discovery.active_session_ids() == {"live"}


def test_message_content_never_reaches_task_state(tmp_path: Path) -> None:
    chats = _chat_dir(tmp_path)
    secret = "SUPER_SECRET_PROMPT_CONTENT"
    chat = chats / f"{SESSION_ID}.jsonl"
    chat.write_text(
        json.dumps({"cwd": WORK_DIR, "message": {"parts": [secret]}}) + "\n",
        encoding="utf-8",
    )
    tasks = _discovery(tmp_path).discover()
    state = tasks[0].state
    serialized = json.dumps(
        {
            "message": state.message,
            "metadata": state.metadata,
            "name": tasks[0].config.name,
        },
        ensure_ascii=False,
    )
    assert secret not in serialized


def test_evaluate_status_uses_injected_clock() -> None:
    status = evaluate_qwen_session_status(
        runtime_pid=None,
        process_alive=False,
        activity_at=NOW - timedelta(seconds=5),
        now=NOW,
        activity_window_seconds=90.0,
    )
    assert status.status is TaskStatus.RUNNING


def test_missing_projects_root_is_empty(tmp_path: Path) -> None:
    discovery = _discovery(tmp_path / "nowhere")
    assert discovery.discover() == []
    assert discovery.catalog() == []


def test_unreadable_projects_root_raises_discovery_error(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.write_text("not a directory", encoding="utf-8")
    discovery = _discovery(tmp_path)
    try:
        discovery.discover()
    except QwenDiscoveryError:
        pass
    else:
        raise AssertionError("expected QwenDiscoveryError")


def test_subagent_files_are_not_enumerated(tmp_path: Path) -> None:
    _write_chat(tmp_path)
    project = tmp_path / "projects" / ENCODED_PROJECT
    subagents = project / "subagents" / SESSION_ID
    subagents.mkdir(parents=True)
    (subagents / "agent-helper-call_1.jsonl").write_text(
        json.dumps({"cwd": WORK_DIR}) + "\n", encoding="utf-8"
    )
    tasks = _discovery(tmp_path).discover()
    assert [task.state.session_id for task in tasks] == [SESSION_ID]


def _write_usage_record(
    tmp_path: Path,
    *,
    session_id: str = SESSION_ID,
    input_tokens: int = 100,
    output_tokens: int = 40,
    cached_tokens: int = 60,
    latency_ms: float = 2000.0,
    append: bool = False,
) -> Path:
    record = {
        "sessionId": session_id,
        "project": WORK_DIR,
        "models": {
            "qwen3-max": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cachedTokens": cached_tokens,
                "totalLatencyMs": latency_ms,
            }
        },
        "totalLatencyMs": latency_ms,
    }
    path = tmp_path / "usage_record.jsonl"
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return path


def test_usage_tracker_accumulates_records_per_session(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    path = _write_usage_record(tmp_path)
    _write_usage_record(tmp_path, input_tokens=50, output_tokens=10, cached_tokens=20, append=True)
    tracker.refresh(path)

    usage = tracker.usage_for(SESSION_ID)
    assert usage is not None
    assert usage.input_tokens == 150
    assert usage.output_tokens == 50
    assert usage.cache_read_tokens == 80
    assert usage.total_input_tokens == 230
    assert usage.cache_read_pct == round(80 / 230 * 100)


def test_usage_tracker_is_incremental_across_refreshes(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    path = _write_usage_record(tmp_path)
    tracker.refresh(path)
    _write_usage_record(tmp_path, input_tokens=10, output_tokens=5, cached_tokens=0, append=True)
    tracker.refresh(path)

    usage = tracker.usage_for(SESSION_ID)
    assert usage is not None
    assert usage.input_tokens == 110
    assert usage.output_tokens == 45


def test_usage_tracker_keeps_sessions_separate(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    path = _write_usage_record(tmp_path)
    _write_usage_record(tmp_path, session_id="other-session", input_tokens=999, append=True)
    tracker.refresh(path)

    assert tracker.usage_for(SESSION_ID).input_tokens == 100
    assert tracker.usage_for("other-session").input_tokens == 999
    assert tracker.usage_for("missing") is None


def test_usage_tracker_skips_malformed_and_oversized_lines(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    path = _write_usage_record(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write("x" * 70_000 + "\n")
    tracker.refresh(path)
    assert tracker.usage_for(SESSION_ID).input_tokens == 100


def test_discover_attaches_usage_metadata(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime={"pid": 1311, "work_dir": WORK_DIR})
    _write_usage_record(tmp_path)
    discovery = _discovery(tmp_path, process_alive={1311: True})
    tasks = discovery.discover()

    usage = tasks[0].state.metadata["usage"]
    assert usage["total_input_tokens"] == 160
    assert usage["output_tokens"] == 40
    assert usage["cache_read_pct"] == round(60 / 160 * 100)


def test_discover_without_usage_records_has_no_usage_key(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime={"pid": 1311, "work_dir": WORK_DIR})
    tasks = _discovery(tmp_path, process_alive={1311: True}).discover()
    assert "usage" not in tasks[0].state.metadata


def test_default_terminal_config_uses_windows_terminal_on_win32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aacc.qwen_discovery import _default_terminal_config

    monkeypatch.setattr(sys, "platform", "win32")
    config = _default_terminal_config(WORK_DIR)
    assert config.type == "windows_terminal"
    assert config.window_title == "codelight"

    empty = _default_terminal_config(None)
    assert empty.type == "windows_terminal"
    assert empty.window_title is None


def test_default_session_process_alive_requires_qwen_cmdline() -> None:
    import os

    from aacc.qwen_discovery import _default_session_process_alive

    # The current test process never carries qwen-code in its command line.
    assert _default_session_process_alive(os.getpid()) is False
    # A pid that cannot exist fails closed instead of guessing.
    assert _default_session_process_alive(2**22 + 12345) is False


def test_usage_tracker_resets_offset_after_truncation(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    record = tmp_path / "usage_record.jsonl"
    first = json.dumps({"sessionId": "s1", "models": {"m": {"outputTokens": 222}}})
    record.write_text(first + "\n", encoding="utf-8")
    tracker.refresh(record)

    shorter = json.dumps({"sessionId": "s1", "models": {"m": {"outputTokens": 1}}})
    record.write_text(shorter + "\n", encoding="utf-8")
    tracker.refresh(record)

    usage = tracker.usage_for("s1")
    assert usage is not None
    assert usage.output_tokens == 223


def test_usage_tracker_no_change_is_a_noop(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    record = tmp_path / "usage_record.jsonl"
    record.write_text(
        json.dumps({"sessionId": "s1", "models": {"m": {"outputTokens": 1}}}) + "\n",
        encoding="utf-8",
    )
    tracker.refresh(record)
    tracker.refresh(record)

    usage = tracker.usage_for("s1")
    assert usage is not None
    assert usage.output_tokens == 1


def test_usage_tracker_buffers_partial_trailing_line(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    record = tmp_path / "usage_record.jsonl"
    complete = json.dumps({"sessionId": "s1", "models": {"m": {"outputTokens": 2}}})
    record.write_text(complete + '\n{"sessionId": "s1", "mod', encoding="utf-8")
    tracker.refresh(record)
    usage = tracker.usage_for("s1")
    assert usage is not None
    assert usage.output_tokens == 2

    finished = json.dumps({"sessionId": "s1", "models": {"m": {"outputTokens": 3}}})
    record.write_text(complete + "\n" + finished + "\n", encoding="utf-8")
    tracker.refresh(record)
    usage = tracker.usage_for("s1")
    assert usage is not None
    assert usage.output_tokens == 5


def test_usage_tracker_rejects_non_object_and_invalid_session_lines(tmp_path: Path) -> None:
    tracker = QwenUsageTracker()
    record = tmp_path / "usage_record.jsonl"
    lines = [
        '{"sessionId": broken json',
        '["sessionId"]',
        '{"sessionId": "", "models": {}}',
        '{"sessionId": "s1", "models": {"m": 5}}',
        '{"sessionId": "s1", "models": {"m": {"outputTokens": 4}}, "durationMs": 1200}',
    ]
    record.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tracker.refresh(record)

    usage = tracker.usage_for("s1")
    assert usage is not None
    assert usage.output_tokens == 4
    assert usage.last_duration_ms == 1200.0


def test_active_session_ids_respects_limit(tmp_path: Path) -> None:
    for index in range(3):
        _write_chat(tmp_path, session_id=f"session-{index}")
    discovery = _discovery(tmp_path)
    active = discovery.active_session_ids(limit=2)
    assert len(active) == 2


def test_sessions_skips_project_dir_without_chats(tmp_path: Path) -> None:
    _write_chat(tmp_path)
    (tmp_path / "projects" / "stray-project").mkdir(parents=True)
    (tmp_path / "projects" / "empty-project" / "chats").mkdir(parents=True)
    tasks = _discovery(tmp_path).discover()
    assert len(tasks) == 1


def test_sessions_skips_unreadable_chats_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_chat(tmp_path)
    broken = tmp_path / "projects" / "broken-project" / "chats"
    broken.mkdir(parents=True)

    real_glob = Path.glob

    def guarded_glob(self: Path, pattern: str, **kwargs: object):
        if "broken-project" in str(self):
            raise OSError("unreadable")
        return real_glob(self, pattern, **kwargs)

    monkeypatch.setattr(Path, "glob", guarded_glob)
    tasks = _discovery(tmp_path).discover()
    assert len(tasks) == 1


def test_read_session_swallows_stat_failure(tmp_path: Path) -> None:
    _write_chat(tmp_path)

    def broken_modified_at(_path: Path) -> datetime:
        raise OSError("stat failed")

    discovery = QwenLocalDiscovery(
        qwen_home=tmp_path,
        now=lambda: NOW,
        file_modified_at=broken_modified_at,
        session_process_alive=lambda _pid: False,
    )
    assert discovery.discover() == []


def test_first_line_cwd_swallows_open_failure(tmp_path: Path) -> None:
    chats = _chat_dir(tmp_path)
    # A directory matching *.jsonl makes open() raise IsADirectoryError.
    (chats / f"{SESSION_ID}.jsonl").mkdir()
    tasks = _discovery(tmp_path).discover()
    assert len(tasks) == 1
    assert tasks[0].config.name.startswith("Qwen 任务")
    assert "work_dir" not in tasks[0].state.metadata


def test_first_line_cwd_rejects_unparsable_first_line(tmp_path: Path) -> None:
    chats = _chat_dir(tmp_path)
    chat = chats / f"{SESSION_ID}.jsonl"
    chat.write_text("{not json\n", encoding="utf-8")
    tasks = _discovery(tmp_path).discover()
    assert "work_dir" not in tasks[0].state.metadata

    chat.write_text("[1, 2]\n", encoding="utf-8")
    tasks = _discovery(tmp_path).discover()
    assert "work_dir" not in tasks[0].state.metadata


def test_default_file_modified_at_reads_mtime(tmp_path: Path) -> None:
    _write_chat(tmp_path, runtime={"pid": 1311, "work_dir": WORK_DIR})
    discovery = QwenLocalDiscovery(qwen_home=tmp_path, now=lambda: NOW)
    tasks = discovery.discover()
    assert len(tasks) == 1
    assert tasks[0].state.updated_at is not None
