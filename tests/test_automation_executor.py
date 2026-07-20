import threading
from concurrent.futures import Future

import pytest

import aacc.automation_executor as executor_module
from aacc.automation import AutomationError
from aacc.config import default_config
from aacc.models import TaskConfig


class RecordingController:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def focus(self, task: TaskConfig) -> str:
        self.calls.append(task.name)
        return task.name

    def send_key(self, task: TaskConfig, key: str) -> str:
        self.calls.append(f"{task.name}:{key}")
        return key

    def send_text(self, task: TaskConfig, value: str) -> str:
        self.calls.append(f"{task.name}:text")
        return value

    def start_voice(self, task: TaskConfig) -> str:
        self.calls.append(f"{task.name}:voice")
        return "voice"


def test_executor_preserves_submission_order() -> None:
    controller = RecordingController()
    executor = executor_module.AutomationExecutor(controller)
    tasks = []
    for index in range(10):
        task = default_config().tasks[0].model_copy(update={"name": str(index)})
        tasks.append(task)

    futures: list[Future[str]] = [executor.submit("focus", task) for task in tasks]

    assert [future.result(timeout=1) for future in futures] == [str(index) for index in range(10)]
    assert controller.calls == [str(index) for index in range(10)]
    executor.close()


def test_executor_rejects_overflow() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingController(RecordingController):
        def focus(self, task: TaskConfig) -> str:
            started.set()
            release.wait(timeout=1)
            return task.name

    executor = executor_module.AutomationExecutor(BlockingController(), capacity=2)
    task = default_config().tasks[0]
    first = executor.submit("focus", task)
    assert started.wait(timeout=1)
    second = executor.submit("focus", task)

    with pytest.raises(executor_module.AutomationBusyError, match="queue is full"):
        executor.submit("focus", task)

    release.set()
    assert first.result(timeout=1)
    assert second.result(timeout=1)
    executor.close()


def test_synchronous_adapter_times_out() -> None:
    release = threading.Event()

    class BlockingController(RecordingController):
        def focus(self, task: TaskConfig) -> str:
            release.wait(timeout=1)
            return task.name

    executor = executor_module.AutomationExecutor(BlockingController(), total_timeout=0.01)
    with pytest.raises(AutomationError, match="timed out"):
        executor.focus(default_config().tasks[0])
    release.set()
    executor.close()


def test_executor_routes_all_controller_methods() -> None:
    controller = RecordingController()
    executor = executor_module.AutomationExecutor(controller)
    task = default_config().tasks[0]

    assert executor.send_key(task, "ENTER") == "ENTER"
    assert executor.send_text(task, "hello") == "hello"
    assert executor.start_voice(task) == "voice"
    executor.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"capacity": 0}, "capacity"), ({"total_timeout": 0}, "timeout")],
)
def test_executor_rejects_invalid_limits(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        executor_module.AutomationExecutor(RecordingController(), **kwargs)  # type: ignore[arg-type]


def test_executor_rejects_unknown_operation_and_submission_after_close() -> None:
    executor = executor_module.AutomationExecutor(RecordingController())
    with pytest.raises(AutomationError, match="Unsupported"):
        executor.submit("delete_everything").result(timeout=1)

    executor.close()
    executor.close()
    with pytest.raises(executor_module.AutomationBusyError, match="closed"):
        executor.submit("focus", default_config().tasks[0])
