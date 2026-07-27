from __future__ import annotations

import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aacc.codex_app_server import CodexAppServerReader, find_codex_executable
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
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "import time\n"
        f"{textwrap.dedent(body)}",
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
