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


def test_adapter_service_start_and_stop_manage_polling_thread(tmp_path: Path) -> None:
    manager, task = _manager(tmp_path)
    service = AdapterDiscoveryService(
        manager,
        config=manager.config,
        interval_seconds=0.5,
        adapter_factory=lambda _task: FakeAdapter(task.id, TaskStatus.RUNNING),
    )

    service.start()
    assert service._thread.is_alive()
    service.stop()
    assert not service._thread.is_alive()
    manager.close()


def test_adapter_service_swallows_adapter_poll_failure(tmp_path: Path, monkeypatch: object) -> None:
    manager, _task = _manager(tmp_path)
    service = AdapterDiscoveryService(manager, config=manager.config)
    monkeypatch.setattr(service, "poll_once", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert service.poll_safely() == 0
    manager.close()


def test_adapter_service_thread_polls_until_stop_signal(
    tmp_path: Path, monkeypatch: object
) -> None:
    manager, _task = _manager(tmp_path)
    service = AdapterDiscoveryService(manager, config=manager.config)
    calls: list[str] = []

    class StopAfterOnePoll:
        def __init__(self) -> None:
            self.waits = iter((False, True))

        def wait(self, _timeout: float) -> bool:
            return next(self.waits)

    monkeypatch.setattr(service, "_stop", StopAfterOnePoll())
    monkeypatch.setattr(service, "poll_safely", lambda: calls.append("poll"))

    service._run()

    assert calls == ["poll"]
    manager.close()
