from pathlib import Path

from aacc.codex_discovery import CodexDiscoveryError, DiscoveredTask
from aacc.config import default_config
from aacc.discovery_service import CodexDiscoveryService, DiscoveryHealth
from aacc.models import AgentConfig, TaskConfig, TaskState
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
            task
            for task in self.tasks
            if task.config.id.removeprefix("codex:") in selected_ids
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
