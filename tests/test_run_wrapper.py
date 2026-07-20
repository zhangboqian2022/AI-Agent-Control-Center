import subprocess

from aacc.run_wrapper import terminate_process


class FakeProcess:
    def __init__(self, *, timeout: bool = False) -> None:
        self.timeout = timeout
        self.terminated = False
        self.killed = False
        self.waits: list[float | None] = []

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.timeout and len(self.waits) == 1:
            raise subprocess.TimeoutExpired("agent", timeout)
        return -15


def test_terminate_process_waits_for_cooperative_exit() -> None:
    process = FakeProcess()
    terminate_process(process, timeout=3.0)  # type: ignore[arg-type]
    assert process.terminated
    assert not process.killed
    assert process.waits == [3.0]


def test_terminate_process_kills_and_reaps_after_timeout() -> None:
    process = FakeProcess(timeout=True)
    terminate_process(process, timeout=3.0)  # type: ignore[arg-type]
    assert process.terminated
    assert process.killed
    assert process.waits == [3.0, None]
