import pytest

from aacc.processes import CachedProcessAlive


class _StubProcess:
    def __init__(self, pid: int, value: str) -> None:
        self.pid = pid
        self.info = {"exe": value, "name": value}


class _StubPsutil:
    class Error(Exception):
        pass

    def __init__(self, alive: dict[int, str]) -> None:
        self.alive = alive
        self.scan_calls = 0

    def process_iter(self, attrs: list[str]) -> list[_StubProcess]:
        self.scan_calls += 1
        return [_StubProcess(pid, value) for pid, value in self.alive.items()]

    def Process(self, pid: int) -> object:
        if pid not in self.alive:
            raise self.Error(f"no such process: {pid}")
        value = self.alive[pid]
        return type("P", (), {"exe": lambda self: value, "name": lambda self: value})()


def test_cached_pid_avoids_rescan_and_recovers_on_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubPsutil({4242: "/Applications/Kimi.app/Contents/MacOS/Kimi"})
    monkeypatch.setattr("aacc.processes.psutil", stub)
    alive = CachedProcessAlive("exe", lambda value: "/Kimi.app/" in value)

    assert alive() is True
    assert stub.scan_calls == 1
    assert alive() is True
    assert stub.scan_calls == 1

    del stub.alive[4242]
    assert alive() is False
    assert stub.scan_calls == 2


def test_no_matching_process_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubPsutil({1: "/usr/bin/true"})
    monkeypatch.setattr("aacc.processes.psutil", stub)
    alive = CachedProcessAlive("exe", lambda value: "/Kimi.app/" in value)

    assert alive() is False
    assert alive() is False
    assert stub.scan_calls == 2


def test_pid_reuse_with_different_binary_is_not_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubPsutil({4242: "/Applications/Kimi.app/Contents/MacOS/Kimi"})
    monkeypatch.setattr("aacc.processes.psutil", stub)
    alive = CachedProcessAlive("exe", lambda value: "/Kimi.app/" in value)

    assert alive() is True
    stub.alive[4242] = "/usr/bin/malicious-lookalike"
    assert alive() is False
