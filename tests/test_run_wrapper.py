import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import aacc.run_wrapper as wrapper_module
from aacc.config import default_config
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


def _noop_group_signal(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    signals: list[int] = []
    monkeypatch.setattr(
        wrapper_module, "_signal_process_group", lambda _process, sig: signals.append(sig)
    )
    return signals


def test_terminate_process_waits_for_cooperative_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    signals = _noop_group_signal(monkeypatch)
    terminate_process(process, timeout=3.0)  # type: ignore[arg-type]
    assert process.waits == [3.0]
    assert signals == [signal.SIGTERM]


def test_terminate_process_kills_and_reaps_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(timeout=True)
    signals = _noop_group_signal(monkeypatch)
    terminate_process(process, timeout=3.0)  # type: ignore[arg-type]
    assert process.waits == [3.0, None]
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_signal_process_group_falls_back_to_direct_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    process.pid = 4242
    monkeypatch.setattr(
        wrapper_module.os, "killpg", lambda _pgid, _sig: (_ for _ in ()).throw(OSError())
    )

    wrapper_module._signal_process_group(process, signal.SIGTERM)

    assert process.terminated
    assert not process.killed


def _pid_alive(pid: int) -> bool:
    try:
        import os

        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
def test_terminate_process_reaps_the_spawned_process_group(tmp_path: Path) -> None:
    spawner = tmp_path / "spawner.py"
    spawner.write_text(
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']\n"
        ")\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(spawner)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    grandchild_pid = int(parent.stdout.readline())

    terminate_process(parent)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _pid_alive(parent.pid) and not _pid_alive(grandchild_pid):
            break
        time.sleep(0.05)
    assert not _pid_alive(parent.pid)
    assert not _pid_alive(grandchild_pid)


def test_status_does_not_use_proxy_environment(monkeypatch: object, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, *_args: object, **_kwargs: object) -> None:
            captured["url"] = url

    monkeypatch.setattr(wrapper_module, "load_config", lambda _path: default_config())  # type: ignore[attr-defined]
    monkeypatch.setattr(wrapper_module.httpx, "Client", FakeClient)  # type: ignore[attr-defined]

    wrapper_module._status(tmp_path / "config.yaml", "task-1", "running", "test")

    assert captured["trust_env"] is False


def test_status_brackets_ipv6_loopback_url(monkeypatch: object, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    config = default_config()
    config.app.api.host = "::1"

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, *_args: object, **_kwargs: object) -> None:
            captured["url"] = url

    monkeypatch.setattr(wrapper_module, "load_config", lambda _path: config)  # type: ignore[attr-defined]
    monkeypatch.setattr(wrapper_module.httpx, "Client", FakeClient)  # type: ignore[attr-defined]

    wrapper_module._status(tmp_path / "config.yaml", "task-1", "running", "test")

    assert captured["url"] == "http://[::1]:17650/api/v1/tasks/task-1/status"


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
