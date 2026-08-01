from __future__ import annotations

import asyncio
from pathlib import Path

from aacc.adapter_discovery_service import AdapterDiscoveryService
from aacc.models import AgentConfig, AppConfig, TaskConfig, TaskState, TaskStatus
from aacc.persistence import StateStore
from aacc.task_manager import TaskManager


class FakeAdapter:
    def __init__(self, task_id: str, status: TaskStatus) -> None:
        self.task_id = task_id
        self.status = status

    async def get_status(self) -> TaskState:
        await asyncio.sleep(0)
        return TaskState.new(self.task_id, self.status, source="process", confidence=0.55)


def _manager(tmp_path: Path) -> tuple[TaskManager, TaskConfig]:
    task = TaskConfig(
        id="claude-1",
        slot=1,
        name="Claude",
        agent=AgentConfig(type="claude_code"),
    )
    config = AppConfig(tasks=[task])
    store = StateStore(tmp_path / "aacc.db")
    store.initialize(config.tasks)
    return TaskManager(config, store), task


def test_adapter_service_updates_running_process_without_fabricating_idle_state(
    tmp_path: Path,
) -> None:
    manager, task = _manager(tmp_path)
    adapter = FakeAdapter(task.id, TaskStatus.RUNNING)
    service = AdapterDiscoveryService(
        manager,
        config=manager.config,
        adapter_factory=lambda _task: adapter,
    )

    assert service.poll_once() == 1
    assert manager.get(task.id).status is TaskStatus.RUNNING

    adapter.status = TaskStatus.STOPPED
    assert service.poll_once() == 1
    assert manager.get(task.id).status is TaskStatus.STOPPED

    assert service.poll_once() == 0
    manager.close()


def test_adapter_service_does_not_overwrite_terminal_state_when_process_is_absent(
    tmp_path: Path,
) -> None:
    manager, task = _manager(tmp_path)
    manager.update(TaskState.new(task.id, TaskStatus.COMPLETED, source="manual"))
    service = AdapterDiscoveryService(
        manager,
        config=manager.config,
        adapter_factory=lambda _task: FakeAdapter(task.id, TaskStatus.STOPPED),
    )

    assert service.poll_once() == 0
    assert manager.get(task.id).status is TaskStatus.COMPLETED
    manager.close()
