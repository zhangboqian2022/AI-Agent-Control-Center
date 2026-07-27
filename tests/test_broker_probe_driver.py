from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
NATIVE = ROOT / "tests" / "native"
DRIVER_PATH = NATIVE / "run_broker_probe.py"


def load_driver() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_broker_probe", DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(NATIVE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(NATIVE))
    return module


class FakeProcess:
    def __init__(self, command: list[str], mode: str, kwargs: dict[str, Any]) -> None:
        self.command = command
        self.mode = mode
        self.kwargs = kwargs
        self.returncode = 0
        self.request_bytes = b""
        self.killed = False
        self.communicate_calls = 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self.communicate_calls += 1
        if input is not None:
            self.request_bytes = input
        if self.mode == "timeout" and self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if self.mode == "timeout":
            return b"", b""
        if self.mode.startswith("communicate-error") and self.communicate_calls == 1:
            raise OSError("AACC_SECRET_MARKER_4ce1")
        if self.mode == "communicate-error-reap-timeout":
            raise subprocess.TimeoutExpired(self.command, timeout)
        if self.mode == "communicate-error":
            return b"", b""

        request = json.loads(self.request_bytes)
        if self.mode == "mismatch":
            request["payload"] = "wrong"
        response = {
            "pid": 321,
            "args": ["app-server", "--stdio"],
            "request": request,
            "bundle_in_path": False,
            "preserved_path_present": True,
            "broker_target_matches_expected": True,
        }
        return json.dumps(response, separators=(",", ":")).encode(), b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakePopenFactory:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, self.mode, kwargs)
        self.processes.append(process)
        return process


def driver_args(tmp_path: Path, payload: str) -> tuple[list[str], Path, Path, Path, Path]:
    broker = tmp_path / "临时 &%! aacc-spawn.exe"
    codex = tmp_path / "临时 &%! codex.exe"
    bundle = tmp_path / "临时 &%! _internal"
    payload_path = tmp_path / "payload.txt"
    pid_path = tmp_path / "child.pid"
    payload_path.write_text(payload, encoding="utf-8")
    return (
        [
            "--broker",
            str(broker),
            "--codex",
            str(codex),
            "--bundle-dir",
            str(bundle),
            "--payload",
            str(payload_path),
            "--request-id",
            "7",
            "--expected-exit-code",
            "0",
            "--pid-file",
            str(pid_path),
        ],
        broker,
        codex,
        bundle,
        pid_path,
    )


def test_driver_sends_70k_as_bomless_utf8_with_a_fixed_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = load_driver()
    payload = "x" * 70_000
    args, broker, codex, bundle, pid_path = driver_args(tmp_path, payload)
    factory = FakePopenFactory()
    monkeypatch.setenv("AACC_TEST_BUNDLE_DIR", "inherited-fixture-value")

    result = driver.main(args, popen_factory=factory)

    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert pid_path.read_text(encoding="ascii") == "321"
    process = factory.processes[0]
    assert process.command == [
        str(broker),
        "--protocol",
        "1",
        "--parent-pid",
        str(os.getpid()),
        "--bundle-dir",
        str(bundle),
        "--codex",
        str(codex),
    ]
    assert process.kwargs["shell"] is False
    assert process.kwargs["stdin"] is subprocess.PIPE
    assert process.kwargs["stdout"] is subprocess.PIPE
    assert process.kwargs["stderr"] is subprocess.PIPE
    assert process.kwargs["env"]["AACC_TEST_BUNDLE_DIR"] == "inherited-fixture-value"
    assert process.kwargs["env"]["AACC_TEST_EXPECTED_CODEX_TARGET"] == str(codex)
    assert process.request_bytes.startswith(b"{")
    assert not process.request_bytes.startswith(b"\xef\xbb\xbf")
    assert process.request_bytes.endswith(b"\n")
    assert json.loads(process.request_bytes)["payload"] == payload


def test_driver_timeout_is_reaped_and_never_echoes_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = load_driver()
    marker = "AACC_SECRET_MARKER_4ce1"
    args, _, _, _, pid_path = driver_args(tmp_path, marker * 10)
    args.extend(("--timeout-seconds", "0.01"))
    factory = FakePopenFactory("timeout")

    result = driver.main(args, popen_factory=factory)

    captured = capsys.readouterr()
    assert result == 5
    assert captured.out == ""
    assert captured.err == "AACC_BROKER_PROBE code=5 reason=timeout\n"
    assert marker not in captured.err
    assert factory.processes[0].killed is True
    assert factory.processes[0].communicate_calls == 2
    assert not pid_path.exists()


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("communicate-error", "communicate"),
        ("communicate-error-reap-timeout", "communicate-reap"),
    ],
)
def test_driver_communication_errors_are_reaped_with_fixed_diagnostics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    reason: str,
) -> None:
    driver = load_driver()
    marker = "AACC_SECRET_MARKER_4ce1"
    args, _, _, _, pid_path = driver_args(tmp_path, marker * 10)
    factory = FakePopenFactory(mode)

    result = driver.main(args, popen_factory=factory)

    captured = capsys.readouterr()
    assert result == 5
    assert captured.out == ""
    assert captured.err == f"AACC_BROKER_PROBE code=5 reason={reason}\n"
    assert marker not in captured.err
    assert factory.processes[0].killed is True
    assert factory.processes[0].communicate_calls == 2
    assert not pid_path.exists()


def test_driver_validation_failure_has_safe_diagnostics_and_no_pid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = load_driver()
    marker = "AACC_SECRET_MARKER_4ce1"
    args, _, _, _, pid_path = driver_args(tmp_path, marker * 10)
    factory = FakePopenFactory("mismatch")

    result = driver.main(args, popen_factory=factory)

    captured = capsys.readouterr()
    assert result == 4
    assert captured.out == ""
    assert "reason=request-payload" in captured.err
    assert marker not in captured.err
    assert not pid_path.exists()
