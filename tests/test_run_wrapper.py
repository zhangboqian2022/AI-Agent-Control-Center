import subprocess
from pathlib import Path

import aacc.run_wrapper as wrapper_module
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


def test_main_reports_success_and_restores_signal_handlers(monkeypatch: object) -> None:
    statuses: list[tuple[str, int | None]] = []
    restored: list[int] = []

    class CompletedProcess:
        pid = 4321

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(  # type: ignore[attr-defined]
        wrapper_module,
        "_status",
        lambda _path, _task, state, _message, pid=None: statuses.append((state, pid)),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        wrapper_module.subprocess, "Popen", lambda *_args, **_kwargs: CompletedProcess()
    )

    def signal_handler(signum: int, handler: object) -> object:
        if handler == "previous":
            restored.append(signum)
        return "previous"

    monkeypatch.setattr(wrapper_module.signal, "signal", signal_handler)  # type: ignore[attr-defined]

    result = wrapper_module.main(
        ["--task", "task-1", "--config", str(Path("config.yaml")), "--", "true"]
    )

    assert result == 0
    assert statuses == [("starting", None), ("running", 4321), ("stopped", None)]
    assert restored == [wrapper_module.signal.SIGINT, wrapper_module.signal.SIGTERM]


def test_main_reports_process_start_failure(monkeypatch: object, capsys: object) -> None:
    statuses: list[str] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        wrapper_module,
        "_status",
        lambda _path, _task, state, _message, pid=None: statuses.append(state),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        wrapper_module.signal, "signal", lambda _signum, _handler: None
    )

    def fail_start(*_args: object, **_kwargs: object) -> object:
        raise OSError("missing executable")

    monkeypatch.setattr(wrapper_module.subprocess, "Popen", fail_start)  # type: ignore[attr-defined]

    assert wrapper_module.main(["--task", "task-1", "missing-command"]) == 127
    assert statuses == ["starting", "error"]
    assert "missing executable" in capsys.readouterr().err  # type: ignore[attr-defined]
