from pathlib import Path

from aacc.codex_discovery import DiscoveredTask
from aacc.config import default_config
from aacc.discovery_service import CodexDiscoveryService
from aacc.models import AgentConfig, TaskConfig, TaskState
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


class StubDiscovery:
    def __init__(self, tasks: list[DiscoveredTask]) -> None:
        self.tasks = tasks

    def discover(self) -> list[DiscoveredTask]:
        return self.tasks


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
    service = CodexDiscoveryService(
        manager,
        discovery=StubDiscovery([DiscoveredTask(task, TaskState.new(task.id, "running"))]),
    )

    count = service.poll_once()

    assert count == 1
    assert manager.get(task.id).status.value == "RUNNING"
    manager.close()
