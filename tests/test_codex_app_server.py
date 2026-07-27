from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil
import pytest

from aacc.codex_app_server import (
    WINDOWS_PROCESS_CREATION_FLAGS,
    CodexAppServerReader,
    find_codex_executable,
)
from aacc.codex_quota import (
    CodexQuotaStatus,
    parse_app_server_rate_limits,
)

NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)


def test_find_codex_executable_prefers_explicit_override(tmp_path: Path) -> None:
    override = tmp_path / "override-codex"
    path_codex = tmp_path / "path-codex"
    existing = {override, path_codex}

    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={"AACC_CODEX_EXECUTABLE": str(override)},
        which=lambda _name: str(path_codex),
        is_file=existing.__contains__,
    )

    assert result == override


def test_find_codex_executable_uses_path_before_known_locations(tmp_path: Path) -> None:
    path_codex = tmp_path / "bin" / "codex"
    local_codex = tmp_path / ".local" / "bin" / "codex"
    existing = {path_codex, local_codex}

    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={},
        which=lambda _name: str(path_codex),
        is_file=existing.__contains__,
    )

    assert result == path_codex


@pytest.mark.parametrize(
    ("existing_suffix", "expected_suffix"),
    [
        (".local/bin/codex", ".local/bin/codex"),
        ("Applications/ChatGPT.app/Contents/Resources/codex", ".local/bin/codex"),
    ],
)
def test_find_codex_executable_checks_macos_home_candidates_in_order(
    tmp_path: Path,
    existing_suffix: str,
    expected_suffix: str,
) -> None:
    local = tmp_path / ".local" / "bin" / "codex"
    bundled = tmp_path / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex"
    existing = {local, bundled}
    if existing_suffix.startswith("Applications"):
        existing.remove(local)

    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={},
        which=lambda _name: None,
        is_file=existing.__contains__,
    )

    expected = local if expected_suffix.startswith(".local") and local in existing else bundled
    assert result == expected


def test_find_codex_executable_checks_windows_npm_locations(tmp_path: Path) -> None:
    roaming = tmp_path / "Roaming"
    local = tmp_path / "Local"
    roaming_codex = roaming / "npm" / "codex.cmd"
    local_codex = local / "npm" / "codex.cmd"

    result = find_codex_executable(
        platform="win32",
        home=tmp_path,
        environ={"APPDATA": str(roaming), "LOCALAPPDATA": str(local)},
        which=lambda _name: None,
        is_file={roaming_codex, local_codex}.__contains__,
    )

    assert result == roaming_codex


def test_find_codex_executable_rejects_non_files(tmp_path: Path) -> None:
    result = find_codex_executable(
        platform="darwin",
        home=tmp_path,
        environ={"AACC_CODEX_EXECUTABLE": str(tmp_path / "directory")},
        which=lambda _name: str(tmp_path / "missing"),
        is_file=lambda _path: False,
    )

    assert result is None


def _window(
    *,
    minutes: int = 10_080,
    used: object = 9,
    reset_delta: timedelta = timedelta(hours=1),
) -> dict[str, object]:
    return {
        "usedPercent": used,
        "windowDurationMins": minutes,
        "resetsAt": int((NOW + reset_delta).timestamp()),
    }


def test_parse_app_server_prefers_named_codex_limit() -> None:
    snapshot = parse_app_server_rate_limits(
        {
            "rateLimits": {"primary": _window(minutes=300, used=1)},
            "rateLimitsByLimitId": {
                "other": {"primary": _window(used=95)},
                "codex": {
                    "planType": "prolite",
                    "primary": _window(used=9),
                },
            },
        },
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 9
    assert snapshot.plan_type == "prolite"
    assert snapshot.observed_at == NOW


def test_parse_app_server_falls_back_to_legacy_rate_limits() -> None:
    snapshot = parse_app_server_rate_limits(
        {
            "rateLimits": {
                "planType": "team",
                "primary": _window(minutes=300),
                "secondary": _window(used=64),
            }
        },
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 64
    assert snapshot.plan_type == "team"


@pytest.mark.parametrize(
    "window",
    [
        _window(minutes=300),
        _window(used=-1),
        _window(used=101),
        _window(used=True),
        _window(reset_delta=timedelta(seconds=-1)),
        {"usedPercent": 9, "windowDurationMins": 10_080, "resetsAt": "invalid"},
    ],
)
def test_parse_app_server_rejects_invalid_weekly_windows(window: object) -> None:
    snapshot = parse_app_server_rate_limits(
        {"rateLimits": {"primary": window}},
        now=NOW,
    )

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert snapshot.weekly is None


def test_parse_app_server_truncates_plan_type() -> None:
    snapshot = parse_app_server_rate_limits(
        {
            "rateLimits": {
                "planType": "p" * 80,
                "primary": _window(),
            }
        },
        now=NOW,
    )

    assert snapshot.plan_type == "p" * 32


def _fake_server(tmp_path: Path, body: str) -> Path:
    server = tmp_path / "fake-codex"
    server.write_text(
        f"#!/usr/bin/env python3\nimport json\nimport sys\nimport time\n{textwrap.dedent(body)}",
        encoding="utf-8",
    )
    server.chmod(0o755)
    return server


def _capturing_popen(
    calls: list[list[str]],
) -> Any:
    def start(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
        calls.append(args)
        return subprocess.Popen(args, **kwargs)

    return start


def test_app_server_reader_performs_handshake_and_matches_quota_response(
    tmp_path: Path,
) -> None:
    server = _fake_server(
        tmp_path,
        """
initialize = json.loads(sys.stdin.readline())
print(json.dumps({"id": initialize["id"], "result": {}}), flush=True)
initialized = json.loads(sys.stdin.readline())
assert initialized["method"] == "initialized"
request = json.loads(sys.stdin.readline())
assert request["method"] == "account/rateLimits/read"
print(json.dumps({"method": "account/updated", "params": {}}), flush=True)
print(json.dumps({"id": 999, "result": {"rateLimits": {}}}), flush=True)
print(json.dumps({
    "id": request["id"],
    "result": {
        "rateLimitsByLimitId": {
            "codex": {
                "planType": "prolite",
                "primary": {
                    "usedPercent": 9,
                    "windowDurationMins": 10080,
                    "resetsAt": 1785747600
                }
            }
        }
    }
}), flush=True)
""",
    )
    calls: list[list[str]] = []
    reader = CodexAppServerReader(
        server,
        timeout_seconds=2,
        now=lambda: NOW,
        popen=_capturing_popen(calls),
    )

    snapshot = reader.read_latest()

    assert calls == [[str(server), "app-server", "--stdio"]]
    assert snapshot.status is CodexQuotaStatus.OK
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 9
    assert snapshot.plan_type == "prolite"


def test_stdout_notifications_cannot_evict_a_matching_response() -> None:
    response = json.dumps({"id": 2, "result": {"rateLimits": {}}})
    notifications = [
        json.dumps({"method": "account/updated", "params": {"index": index}}) for index in range(64)
    ]
    stream = io.StringIO("\n".join([response, *notifications]) + "\n")
    output: queue.Queue[str | None] = queue.Queue(maxsize=32)

    CodexAppServerReader._read_stdout(stream, output)

    assert CodexAppServerReader._wait_for_response(
        output,
        request_id=2,
        deadline=time.monotonic() + 1,
    ) == {"rateLimits": {}}


def test_windows_reader_hides_process_and_kills_the_process_tree(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeStream:
        def write(self, _value: str) -> int:
            return 0

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeProcess:
        pid = 4321
        stdin = FakeStream()
        stdout = io.StringIO("")

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            return 0

        def terminate(self) -> None:
            raise AssertionError("taskkill should handle the Windows process tree")

        def kill(self) -> None:
            raise AssertionError("taskkill should handle the Windows process tree")

    popen_kwargs: dict[str, object] = {}

    def popen(_args: list[str], **kwargs: object) -> FakeProcess:
        popen_kwargs.update(kwargs)
        return FakeProcess()

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    reader = CodexAppServerReader(
        tmp_path / "codex.cmd",
        timeout_seconds=0.05,
        popen=popen,
        platform="win32",
        run=run,
    )
    reader.read_latest()

    assert popen_kwargs["creationflags"] == WINDOWS_PROCESS_CREATION_FLAGS
    assert calls == [
        (
            ["taskkill", "/PID", "4321", "/T", "/F"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 2.0,
                "check": False,
                "shell": False,
            },
        )
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows cmd.exe and taskkill")
def test_windows_cmd_timeout_does_not_leave_a_descendant(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text(
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "fake-codex.cmd"
    wrapper.write_text(
        f'@echo off\r\n"{sys.executable}" "{child}" "{pid_file}"\r\n',
        encoding="utf-8",
    )

    snapshot = CodexAppServerReader(
        wrapper,
        timeout_seconds=2.0,
        platform="win32",
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    deadline = time.monotonic() + 2
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while _pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _pid_exists(pid)


def _pid_exists(pid: int) -> bool:
    return psutil.pid_exists(pid)


def test_app_server_reader_times_out_and_reaps_child(tmp_path: Path) -> None:
    server = _fake_server(
        tmp_path,
        """
json.loads(sys.stdin.readline())
time.sleep(10)
""",
    )
    children: list[subprocess.Popen[str]] = []

    def start(args: list[str], **kwargs: object) -> subprocess.Popen[str]:
        process = subprocess.Popen(args, **kwargs)
        children.append(process)
        return process

    snapshot = CodexAppServerReader(
        server,
        timeout_seconds=0.1,
        now=lambda: NOW,
        popen=start,
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
    assert children
    assert children[0].poll() is not None


@pytest.mark.parametrize(
    "response_line",
    [
        "not-json",
        '{"id": 1, "error": {"code": -32601, "message": "method missing"}}',
    ],
)
def test_app_server_reader_returns_unknown_for_invalid_or_incompatible_server(
    tmp_path: Path,
    response_line: str,
) -> None:
    server = _fake_server(
        tmp_path,
        f"""
initialize = json.loads(sys.stdin.readline())
print({response_line!r}, flush=True)
""",
    )

    snapshot = CodexAppServerReader(
        server,
        timeout_seconds=1,
        now=lambda: NOW,
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN


def test_app_server_reader_ignores_oversized_output(tmp_path: Path) -> None:
    server = _fake_server(
        tmp_path,
        """
initialize = json.loads(sys.stdin.readline())
print("x" * 70000, flush=True)
""",
    )

    snapshot = CodexAppServerReader(
        server,
        timeout_seconds=1,
        now=lambda: NOW,
    ).read_latest()

    assert snapshot.status is CodexQuotaStatus.UNKNOWN
