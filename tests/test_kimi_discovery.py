import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aacc.kimi_discovery import KimiDiscoveryError, KimiLocalDiscovery
from aacc.models import TaskStatus

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
STALE = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
RECENT = datetime(2026, 7, 18, 11, 59, tzinfo=UTC)


def _session_dir(home: Path, name: str, session_id: str) -> Path:
    return home / "sessions" / f"wd_{name}_abcdef" / session_id


def _write_state(session_dir: Path, *, title: str | None, updated_at: str | None) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, object] = {}
    if title is not None:
        state["title"] = title
    if updated_at is not None:
        state["updatedAt"] = updated_at
    (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _write_wire(session_dir: Path) -> Path:
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text("sensitive content must not be parsed", encoding="utf-8")
    return wire


def _write_log(session_dir: Path) -> Path:
    log = session_dir / "logs" / "kimi-code.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("log content", encoding="utf-8")
    return log


def _mtime_map(
    mapping: dict[str, datetime], default: datetime = STALE
) -> Callable[[Path], datetime]:
    def modified(path: Path) -> datetime:
        if path.name in mapping:
            return mapping[path.name]
        if path.exists():
            return default
        raise OSError(path)

    return modified


def _write_index(home: Path, lines: list[str]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "session_index.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _index_line(home: Path, session_id: str, name: str = "proj") -> str:
    return json.dumps(
        {
            "sessionId": session_id,
            "sessionDir": str(_session_dir(home, name, session_id)),
            "workDir": "/path/to/project",
        }
    )


def test_existing_unreadable_session_index_raises_discovery_error(tmp_path: Path) -> None:
    index_directory = tmp_path / "session_index.jsonl"
    index_directory.mkdir()
    discovery = KimiLocalDiscovery(tmp_path / ".kimi-code", session_index_path=index_directory)

    with pytest.raises(KimiDiscoveryError, match="session index"):
        discovery.catalog()


def test_discover_returns_kimi_task_shape(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    active_id = "session_active-1234"
    idle_id = "session_idle-5678"
    _write_index(
        home,
        [
            _index_line(home, active_id, "one"),
            "not json at all",
            _index_line(home, idle_id, "two"),
        ],
    )
    active_dir = _session_dir(home, "one", active_id)
    _write_state(active_dir, title="修复菜单栏", updated_at="2026-07-18T11:00:00Z")
    _write_wire(active_dir)
    idle_dir = _session_dir(home, "two", idle_id)
    _write_state(idle_dir, title="整理任务", updated_at="2026-07-18T09:00:00Z")

    discovery = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": RECENT}),
        agent_process_alive=lambda: False,
    )
    tasks = discovery.discover()

    assert [task.config.id for task in tasks] == [f"kimi:{active_id}", f"kimi:{idle_id}"]
    assert tasks[0].config.name == "修复菜单栏"
    assert tasks[0].config.agent.type == "kimi_code"
    assert tasks[0].config.agent.display_name == "Kimi Code"
    assert tasks[0].config.terminal.app_bundle_id == "com.apple.Terminal"
    assert tasks[0].state.source == "kimi_local"
    assert tasks[0].state.metadata == {"discovered": True}
    assert tasks[0].state.session_id == active_id
    assert tasks[0].state.pid is None
    assert tasks[0].state.finished_at is None


def test_recent_activity_is_reported_as_running(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_busy-0001"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="进行中", updated_at="2026-07-18T11:00:00Z")
    _write_wire(session_dir)
    _write_log(session_dir)

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map(
            {"wire.jsonl": RECENT, "kimi-code.log": STALE, "state.json": STALE}
        ),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].state.status is TaskStatus.RUNNING
    assert tasks[0].state.message == "正在运行"
    assert tasks[0].state.confidence == 0.9
    assert tasks[0].state.started_at == RECENT
    assert tasks[0].state.updated_at == RECENT


def test_stale_activity_with_live_process_is_idle(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_idle-0002"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="空闲", updated_at="2026-07-18T09:00:00Z")
    _write_wire(session_dir)

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": STALE, "state.json": STALE}),
        agent_process_alive=lambda: True,
    ).discover()

    assert tasks[0].state.status is TaskStatus.IDLE
    assert tasks[0].state.message == "空闲"
    assert tasks[0].state.confidence == 0.7
    assert tasks[0].state.started_at is None


def test_stale_activity_without_process_is_unknown(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_gone-0003"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="已结束", updated_at="2026-07-18T09:00:00Z")

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"state.json": STALE}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].state.status is TaskStatus.UNKNOWN
    assert tasks[0].state.message == "未检测到运行进程"
    assert tasks[0].state.confidence == 0.55
    assert tasks[0].state.updated_at == STALE


def test_process_check_is_lazy_and_called_at_most_once(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    lines = []
    for index in range(3):
        session_id = f"session_lazy-{index:04d}"
        lines.append(_index_line(home, session_id, f"p{index}"))
        _write_state(
            _session_dir(home, f"p{index}", session_id),
            title=f"任务 {index}",
            updated_at="2026-07-18T09:00:00Z",
        )
    _write_index(home, lines)
    calls = 0

    def process_alive() -> bool:
        nonlocal calls
        calls += 1
        return True

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"state.json": STALE}),
        agent_process_alive=process_alive,
    ).discover()

    assert len(tasks) == 3
    assert calls == 1

    calls = 0
    KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"state.json": RECENT}),
        agent_process_alive=process_alive,
    ).discover()
    assert calls == 0


def test_long_titles_are_truncated_for_the_panel(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_long-0004"
    _write_index(home, [_index_line(home, session_id)])
    _write_state(
        _session_dir(home, "proj", session_id),
        title="长" * 200,
        updated_at="2026-07-18T09:00:00Z",
    )

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].config.name == "长" * 20


def _write_wire_events(session_dir: Path, events: list[dict[str, object]]) -> Path:
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )
    return wire


def test_completed_turn_is_reported_as_completed(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_done-0006"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="已完成", updated_at="2026-07-18T11:00:00Z")
    _write_wire_events(
        session_dir,
        [
            {"type": "turn.prompt"},
            {"type": "context.append_loop_event"},
            {"type": "usage.record", "usageScope": "step"},
            {"type": "usage.record", "usageScope": "turn"},
        ],
    )

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": RECENT}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].state.status is TaskStatus.COMPLETED
    assert tasks[0].state.message == "回合已完成"
    assert tasks[0].state.confidence == 0.96
    assert tasks[0].state.finished_at == RECENT
    assert tasks[0].state.started_at is None


def test_new_turn_activity_after_completion_is_running(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_again-0007"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="继续", updated_at="2026-07-18T11:00:00Z")
    _write_wire_events(
        session_dir,
        [
            {"type": "usage.record", "usageScope": "turn"},
            {"type": "turn.prompt"},
            {"type": "llm.request"},
        ],
    )

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": RECENT}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].state.status is TaskStatus.RUNNING
    assert tasks[0].state.message == "正在运行"


def test_active_turn_with_quiet_wire_is_still_running(tmp_path: Path) -> None:
    # A turn in progress can leave the wire untouched for minutes (a slow LLM
    # response or a long tool call); it must not fall back to idle.
    home = tmp_path / ".kimi-code"
    session_id = "session_quiet-0008"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="长工具调用", updated_at="2026-07-18T11:00:00Z")
    _write_wire_events(
        session_dir,
        [
            {"type": "usage.record", "usageScope": "turn"},
            {"type": "turn.prompt"},
            {"type": "llm.request"},
        ],
    )
    quiet = datetime(2026, 7, 18, 11, 50, tzinfo=UTC)  # 10 min ago: past the 90s window

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": quiet, "state.json": quiet}),
        agent_process_alive=lambda: True,
    ).discover()

    assert tasks[0].state.status is TaskStatus.RUNNING
    assert tasks[0].state.message == "正在运行"


def test_active_turn_window_is_bounded(tmp_path: Path) -> None:
    # A crashed session can leave the wire mid-turn forever; once activity is
    # older than the active-turn window it must fall back to idle.
    home = tmp_path / ".kimi-code"
    session_id = "session_crashed-0009"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="崩溃", updated_at="2026-07-18T09:00:00Z")
    _write_wire_events(session_dir, [{"type": "turn.prompt"}, {"type": "llm.request"}])

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": STALE, "state.json": STALE}),
        agent_process_alive=lambda: True,
    ).discover()

    assert tasks[0].state.status is TaskStatus.IDLE
    assert tasks[0].state.message == "空闲"


def test_completed_turn_detected_beyond_oversized_irrelevant_event(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_oversize-0001"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="长事件", updated_at="2026-07-18T11:00:00Z")
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text(
        "\n".join(
            [
                json.dumps({"type": "turn.prompt"}),
                json.dumps({"type": "usage.record", "usageScope": "turn"}),
                json.dumps({"type": "noise", "payload": "x" * 70_000}),
            ]
        ),
        encoding="utf-8",
    )

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": RECENT}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].state.status is TaskStatus.COMPLETED


def test_wire_scan_budget_exhaustion_returns_undetermined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_budget-0002"
    session_dir = _session_dir(home, "proj", session_id)
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text(
        "\n".join(json.dumps({"type": "noise", "i": i}) for i in range(200)),
        encoding="utf-8",
    )

    monkeypatch.setattr("aacc.kimi_discovery._WIRE_SCAN_BUDGET_BYTES", 256)
    discovery = KimiLocalDiscovery(home)

    assert discovery._turn_completed(session_dir) is None


def test_undetermined_wire_scan_is_not_fabricated_as_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_unknown-0003"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="不确定", updated_at="2026-07-18T11:00:00Z")
    events = [json.dumps({"type": "usage.record", "usageScope": "turn"})]
    events += [json.dumps({"type": "noise", "i": i}) for i in range(200)]
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text("\n".join(events), encoding="utf-8")

    monkeypatch.setattr("aacc.kimi_discovery._WIRE_SCAN_BUDGET_BYTES", 256)
    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": RECENT}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].state.status is TaskStatus.RUNNING
    assert tasks[0].state.status is not TaskStatus.COMPLETED


def test_wire_secrets_never_reach_task_state(tmp_path: Path) -> None:
    secret = "sk-live-secret-token-9f8e7d"
    prompt_text = f"请删除生产数据库 {secret}"
    home = tmp_path / ".kimi-code"
    session_id = "session_privacy-0004"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    _write_state(session_dir, title="隐私", updated_at="2026-07-18T11:00:00Z")
    _write_wire_events(
        session_dir,
        [
            {"type": "turn.prompt", "content": prompt_text},
            {"type": "usage.record", "usageScope": "turn", "detail": secret},
        ],
    )

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"wire.jsonl": RECENT}),
        agent_process_alive=lambda: False,
    ).discover()

    state_dump = json.dumps(tasks[0].state.model_dump(mode="json"), ensure_ascii=False)
    config_dump = json.dumps(tasks[0].config.model_dump(mode="json"), ensure_ascii=False)
    assert tasks[0].state.status is TaskStatus.COMPLETED
    assert secret not in state_dump
    assert secret not in config_dump
    assert "删除生产数据库" not in state_dump


def test_missing_state_file_falls_back_to_safe_title(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_12345678-abcd"
    _write_index(home, [_index_line(home, session_id)])

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({}),
        agent_process_alive=lambda: False,
    ).discover()

    assert tasks[0].config.name == "Kimi 任务 session_"
    assert tasks[0].state.status is TaskStatus.UNKNOWN


def test_malformed_state_file_uses_index_entry_anyway(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_id = "session_broken-0005"
    _write_index(home, [_index_line(home, session_id)])
    session_dir = _session_dir(home, "proj", session_id)
    session_dir.mkdir(parents=True)
    (session_dir / "state.json").write_text("{not valid json", encoding="utf-8")

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({"state.json": STALE}),
        agent_process_alive=lambda: True,
    ).discover()

    assert tasks[0].config.name == f"Kimi 任务 {session_id[:8]}"
    assert tasks[0].state.status is TaskStatus.IDLE
    assert tasks[0].state.updated_at == STALE


def test_discovery_only_returns_explicitly_selected_tasks(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    chosen_dir = _session_dir(home, "one", "chosen")
    ignored_dir = _session_dir(home, "two", "ignored")
    _write_index(home, [_index_line(home, "chosen", "one"), _index_line(home, "ignored", "two")])
    _write_state(chosen_dir, title="已选择", updated_at="2026-07-18T09:00:00Z")
    _write_state(ignored_dir, title="未选择", updated_at="2026-07-18T09:00:00Z")

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=_mtime_map({}),
        agent_process_alive=lambda: False,
    ).discover({"chosen"})

    assert [task.state.session_id for task in tasks] == ["chosen"]


def test_catalog_is_sorted_desc_and_deduplicates_by_session_id(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    old_dir = _session_dir(home, "old", "same-session")
    new_dir = _session_dir(home, "new", "same-session")
    other_dir = _session_dir(home, "other", "other-session")
    _write_index(
        home,
        [
            json.dumps(
                {
                    "sessionId": "same-session",
                    "sessionDir": str(old_dir),
                    "workDir": "/path/to/project",
                }
            ),
            json.dumps(
                {
                    "sessionId": "other-session",
                    "sessionDir": str(other_dir),
                    "workDir": "/path/to/project",
                }
            ),
            json.dumps(
                {
                    "sessionId": "same-session",
                    "sessionDir": str(new_dir),
                    "workDir": "/path/to/project",
                }
            ),
        ],
    )
    _write_state(old_dir, title="旧标题", updated_at="2026-07-18T08:00:00Z")
    _write_state(new_dir, title="新标题", updated_at="2026-07-18T10:00:00Z")
    _write_state(other_dir, title="中间任务", updated_at="2026-07-18T09:00:00Z")

    catalog = KimiLocalDiscovery(
        home,
        file_modified_at=_mtime_map({}),
        agent_process_alive=lambda: False,
    ).catalog()

    assert [(session.session_id, session.title) for session in catalog] == [
        ("same-session", "新标题"),
        ("other-session", "中间任务"),
    ]
    assert catalog[0].updated_at == datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def test_active_session_ids_only_includes_running_sessions_up_to_limit(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    lines = []
    for index in range(3):
        session_id = f"session_run-{index:04d}"
        lines.append(_index_line(home, session_id, f"r{index}"))
        session_dir = _session_dir(home, f"r{index}", session_id)
        _write_state(session_dir, title=f"运行 {index}", updated_at="2026-07-18T11:00:00Z")
        _write_wire(session_dir)
    idle_id = "session_idle-9999"
    lines.append(_index_line(home, idle_id, "idle"))
    _write_state(
        _session_dir(home, "idle", idle_id), title="空闲", updated_at="2026-07-18T09:00:00Z"
    )
    _write_index(home, lines)

    # Each session has its own wire.jsonl; distinguish by parent dir instead.
    def modified(path: Path) -> datetime:
        if path.name == "wire.jsonl" and "session_run-" in path.parent.parent.parent.name:
            return RECENT
        if path.exists():
            return STALE
        raise OSError(path)

    discovery = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=modified,
        agent_process_alive=lambda: True,
    )

    assert discovery.active_session_ids() == {
        "session_run-0000",
        "session_run-0001",
        "session_run-0002",
    }
    assert len(discovery.active_session_ids(limit=2)) == 2


def test_slots_are_reassigned_and_capped_by_max_tasks(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    lines = []
    for index in range(4):
        session_id = f"session_slot-{index:04d}"
        lines.append(_index_line(home, session_id, f"s{index}"))
        _write_state(
            _session_dir(home, f"s{index}", session_id),
            title=f"任务 {index}",
            updated_at=f"2026-07-18T0{index}:00:00Z",
        )
    _write_index(home, lines)

    def modified(path: Path) -> datetime:
        for index in range(4):
            if f"session_slot-{index:04d}" in str(path):
                return datetime(2026, 7, 18, index, tzinfo=UTC)
        raise OSError(path)

    tasks = KimiLocalDiscovery(
        home,
        now=lambda: NOW,
        file_modified_at=modified,
        agent_process_alive=lambda: False,
        max_tasks=3,
    ).discover()

    assert len(tasks) == 3
    assert [task.config.slot for task in tasks] == [1, 2, 3]
    assert [task.state.session_id for task in tasks] == [
        "session_slot-0003",
        "session_slot-0002",
        "session_slot-0001",
    ]
