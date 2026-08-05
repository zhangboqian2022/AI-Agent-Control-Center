from datetime import UTC, datetime, timedelta
from pathlib import Path

from aacc.codex_discovery import CodexDiscoveryError, DiscoveredTask
from aacc.config import default_config
from aacc.discovery_service import (
    CodexDiscoveryService,
    DiscoveryHealth,
    KimiDesktopDiscoveryService,
    KimiDiscoveryService,
    OpenCodeDiscoveryService,
    QwenDiscoveryService,
)
from aacc.kimi_desktop_discovery import KimiDesktopDiscoveryError
from aacc.models import AgentConfig, TaskConfig, TaskState
from aacc.opencode_discovery import OpenCodeDiscoveryError
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


class StubDiscovery:
    def __init__(self, tasks: list[DiscoveredTask], active_ids: set[str] | None = None) -> None:
        self.tasks = tasks
        self.selected_ids: set[str] | None = None
        self.active_ids = active_ids or set()

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        self.selected_ids = selected_ids
        if selected_ids is None:
            return self.tasks
        return [
            task for task in self.tasks if task.config.id.removeprefix("codex:") in selected_ids
        ]

    def active_session_ids(self) -> set[str]:
        return set(self.active_ids)


class FailingDiscovery(StubDiscovery):
    def __init__(self) -> None:
        super().__init__([])
        self.error: Exception | None = None

    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        if self.error is not None:
            raise self.error
        return super().discover(selected_ids)


class StubKimiDiscovery(StubDiscovery):
    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        self.selected_ids = selected_ids
        if selected_ids is None:
            return self.tasks
        return [task for task in self.tasks if task.config.id.removeprefix("kimi:") in selected_ids]


class StubKimiDesktopDiscovery(StubDiscovery):
    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        self.selected_ids = selected_ids
        if selected_ids is None:
            return self.tasks
        return [
            task
            for task in self.tasks
            if task.config.id.removeprefix("kimi_desktop:") in selected_ids
        ]


class StubQwenDiscovery(StubDiscovery):
    def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
        self.selected_ids = selected_ids
        if selected_ids is None:
            return self.tasks
        return [task for task in self.tasks if task.config.id.removeprefix("qwen:") in selected_ids]


def _manager_for(tmp_path: Path) -> TaskManager:
    config = default_config()
    store = StateStore(tmp_path / "disc.db")
    store.initialize(config.tasks)
    return TaskManager(config, store)


def test_default_poll_interval_is_five_seconds(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    service = CodexDiscoveryService(manager, discovery=StubDiscovery([]))  # type: ignore[arg-type]

    assert service.interval_seconds == 5.0
    manager.close()


def test_poll_registers_discovered_codex_task(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    task = TaskConfig(
        id="codex:task-1234",
        slot=1,
        name="自动任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    discovery = StubDiscovery([DiscoveredTask(task, TaskState.new(task.id, "running"))])
    service = CodexDiscoveryService(
        manager,
        discovery=discovery,  # type: ignore[arg-type]
    )
    service.set_selected_ids({"task-1234"})

    count = service.poll_once()

    assert count == 1
    assert discovery.selected_ids == {"task-1234"}
    assert manager.get(task.id).status.value == "RUNNING"
    manager.close()


def test_poll_once_expires_unseen_stale_run_states(tmp_path: Path) -> None:
    # 发现窗口之外的会话永远等不到新候选，陈旧的 RUNNING 必须由轮询过期。
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    stale_task = TaskConfig(
        id="codex:stale-session",
        slot=2,
        name="陈旧任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    old_at = datetime.now(UTC) - timedelta(hours=2)
    manager.register(
        stale_task,
        TaskState.new(stale_task.id, "running", source="codex_local", confidence=0.9).model_copy(
            update={"updated_at": old_at, "session_id": "stale-session"}
        ),
    )
    service = CodexDiscoveryService(manager, discovery=StubDiscovery([]))  # type: ignore[arg-type]

    service.poll_once()

    state = manager.get(stale_task.id)
    assert state.status.value == "UNKNOWN"
    assert state.message == "长时间未更新"
    manager.close()


def test_poll_auto_monitors_active_tasks_and_honors_inactive_muted_ids(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    task = TaskConfig(
        id="codex:auto-running",
        slot=1,
        name="自动运行任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    discovery = StubDiscovery(
        [DiscoveredTask(task, TaskState.new(task.id, "running"))], active_ids={"auto-running"}
    )
    service = CodexDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]

    service.set_monitoring_preferences(set(), set(), set())
    service.poll_once()

    assert discovery.selected_ids == {"auto-running"}
    assert service.auto_active_ids() == {"auto-running"}
    assert manager.get(task.id).status.value == "RUNNING"

    discovery.active_ids = set()
    service.set_monitoring_preferences(set(), set(), {"auto-running"})
    service.poll_once()

    assert discovery.selected_ids == set()
    manager.close()


def test_active_task_is_retained_and_reappears_after_removal(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    task = TaskConfig(
        id="codex:auto-retained",
        slot=1,
        name="自动保留任务",
        agent=AgentConfig(type="codex_cli", display_name="Codex"),
    )
    discovery = StubDiscovery(
        [DiscoveredTask(task, TaskState.new(task.id, "running"))], active_ids={"auto-retained"}
    )
    service = CodexDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]

    service.poll_once()
    discovery.active_ids = set()
    service.poll_once()

    assert service.retained_ids() == {"auto-retained"}
    assert discovery.selected_ids == {"auto-retained"}

    service.remove_task("auto-retained")
    service.poll_once()

    assert discovery.selected_ids == set()

    discovery.active_ids = {"auto-retained"}
    service.poll_once()

    assert discovery.selected_ids == {"auto-retained"}
    manager.close()


def test_kimi_service_poll_registers_auto_active_task_and_remove_task_mutes(
    tmp_path: Path,
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    task = TaskConfig(
        id="kimi:session-1234",
        slot=1,
        name="Kimi 任务",
        agent=AgentConfig(type="kimi_code", display_name="Kimi Code"),
    )
    discovery = StubKimiDiscovery(
        [DiscoveredTask(task, TaskState.new(task.id, "running"))], active_ids={"session-1234"}
    )
    service = KimiDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]

    service.set_monitoring_preferences(set(), set(), set())
    count = service.poll_once()

    assert count == 1
    assert discovery.selected_ids == {"session-1234"}
    assert service.auto_active_ids() == {"session-1234"}
    assert manager.get(task.id).status.value == "RUNNING"

    discovery.active_ids = set()
    service.remove_task("session-1234")
    service.poll_once()

    assert discovery.selected_ids == set()
    manager.close()


def test_qwen_service_poll_registers_auto_active_task_and_remove_task_mutes(
    tmp_path: Path,
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    task = TaskConfig(
        id="qwen:session-1234",
        slot=1,
        name="Qwen 任务",
        agent=AgentConfig(type="qwen_cli", display_name="Qwen Code"),
    )
    discovery = StubQwenDiscovery(
        [DiscoveredTask(task, TaskState.new(task.id, "running"))], active_ids={"session-1234"}
    )
    service = QwenDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]

    service.set_monitoring_preferences(set(), set(), set())
    count = service.poll_once()

    assert count == 1
    assert discovery.selected_ids == {"session-1234"}
    assert service.auto_active_ids() == {"session-1234"}
    assert manager.get(task.id).status.value == "RUNNING"

    discovery.active_ids = set()
    service.remove_task("session-1234")
    service.poll_once()

    assert discovery.selected_ids == set()
    manager.close()


def test_health_degrades_after_three_failures_and_recovers_after_two_successes(
    tmp_path: Path,
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    discovery = FailingDiscovery()
    service = CodexDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]
    seen: list[DiscoveryHealth] = []
    service.subscribe_health(seen.append)
    discovery.error = OSError("broken index")

    for _ in range(3):
        service.poll_safely()

    assert service.health().degraded
    assert service.health().consecutive_failures == 3
    assert service.health().diagnostic_id
    discovery.error = None
    service.poll_safely()
    assert service.health().degraded
    service.poll_safely()
    assert not service.health().degraded
    assert seen[-1].consecutive_successes == 2
    manager.close()


def test_existing_unreadable_index_degrades_immediately_and_preserves_state(
    tmp_path: Path,
) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    manager.update(TaskState.new("task-1", "running", message="known", source="manual"))
    discovery = FailingDiscovery()
    discovery.error = CodexDiscoveryError("session index unreadable")
    service = CodexDiscoveryService(manager, discovery=discovery)  # type: ignore[arg-type]

    assert service.poll_safely() == 0

    assert service.health().degraded
    assert service.health().consecutive_failures == 1
    assert manager.get("task-1").message == "known"
    assert len(service.health().summary) <= 80
    manager.close()


def test_kimi_desktop_service_poll_registers_task(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)
    task = TaskConfig(
        id="kimi_desktop:conv-1",
        slot=1,
        name="Kimi Desktop 任务",
        agent=AgentConfig(type="kimi_desktop", display_name="Kimi Desktop"),
    )
    discovery = StubKimiDesktopDiscovery([DiscoveredTask(task, TaskState.new(task.id, "running"))])
    service = KimiDesktopDiscoveryService(
        manager,
        discovery=discovery,  # type: ignore[arg-type]
    )
    service.set_selected_ids({"conv-1"})

    count = service.poll_once()

    assert count == 1
    assert discovery.selected_ids == {"conv-1"}
    assert manager.get(task.id).status.value == "RUNNING"
    manager.close()


def test_kimi_desktop_health_degrades_on_discovery_error(tmp_path: Path) -> None:
    config = default_config()
    store = StateStore(tmp_path / "states.db")
    store.initialize(config.tasks)
    manager = TaskManager(config, store)

    class FailingKimiDesktopDiscovery(StubKimiDesktopDiscovery):
        def discover(self, selected_ids: set[str] | None = None) -> list[DiscoveredTask]:
            raise KimiDesktopDiscoveryError("index unreadable")

        def active_session_ids(self) -> set[str]:
            return set()

    service = KimiDesktopDiscoveryService(
        manager,
        discovery=FailingKimiDesktopDiscovery([]),  # type: ignore[arg-type]
    )

    assert service.poll_safely() == 0

    health = service.health()
    assert health.degraded
    assert health.brand == "Kimi Desktop"
    manager.close()


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


def test_opencode_service_polls_and_forwards_selection(tmp_path: Path) -> None:
    discovery = _FakeOpenCodeDiscovery()
    manager = _manager_for(tmp_path)
    service = OpenCodeDiscoveryService(
        manager,
        discovery=discovery,  # type: ignore[arg-type]
        interval_seconds=0.5,
    )
    service.set_selected_ids({"ses_1"})
    service.poll_once()
    assert discovery.calls == 1
    assert discovery.selected == {"ses_1"}
    assert service.health().brand == "OpenCode"
    service.stop()
    manager.close()


def test_opencode_service_tolerates_discovery_errors(tmp_path: Path, caplog) -> None:
    class _BoomDiscovery(_FakeOpenCodeDiscovery):
        def discover(self, selected_ids=None):
            raise OpenCodeDiscoveryError("boom")

    manager = _manager_for(tmp_path)
    service = OpenCodeDiscoveryService(
        manager,
        discovery=_BoomDiscovery(),  # type: ignore[arg-type]
        interval_seconds=0.5,
    )
    assert service.poll_safely() == 0
    health = service.health()
    assert health.degraded
    service.stop()
    manager.close()
