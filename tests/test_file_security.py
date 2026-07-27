from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from aacc.file_security import FileProtectionError, protect_file


def test_windows_acl_uses_numeric_sids_without_shell(tmp_path: Path) -> None:
    path = tmp_path / "secret file"
    path.write_text("token", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        if args == ["whoami", "/user", "/fo", "csv", "/nh"]:
            return subprocess.CompletedProcess(
                args,
                0,
                '"DESKTOP\\\\user","S-1-5-21-1-2-3-1001"\n',
                "",
            )
        return subprocess.CompletedProcess(args, 0, "processed file", "")

    protect_file(path, platform="win32", run=run)

    assert [call[0] for call in calls] == [
        ["whoami", "/user", "/fo", "csv", "/nh"],
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/remove:g",
            "*S-1-3-4",
            "/grant:r",
            "*S-1-5-21-1-2-3-1001:(F)",
            "*S-1-5-18:(F)",
            "*S-1-5-32-544:(F)",
        ],
    ]
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[1]["capture_output"] is True for call in calls)
    assert all(call[1]["timeout"] == 5.0 for call in calls)


@pytest.mark.parametrize(
    ("whoami_result", "message"),
    [
        (
            subprocess.CompletedProcess(["whoami"], 1, "private-output", "denied"),
            "identify",
        ),
        (
            subprocess.CompletedProcess(["whoami"], 0, '"name","not-a-sid"', ""),
            "identify",
        ),
    ],
)
def test_windows_acl_rejects_failed_or_malformed_sid_lookup(
    tmp_path: Path,
    whoami_result: subprocess.CompletedProcess[str],
    message: str,
) -> None:
    def run(_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return whoami_result

    with pytest.raises(FileProtectionError, match=message) as exc_info:
        protect_file(tmp_path / "secret", platform="win32", run=run)

    assert "private-output" not in str(exc_info.value)
    assert "denied" not in str(exc_info.value)


def test_windows_acl_failure_raises_sanitized_error(tmp_path: Path) -> None:
    calls = 0

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(args, 0, '"user","S-1-5-21-1001"', "")
        return subprocess.CompletedProcess(args, 5, "token=private-token", "access denied")

    with pytest.raises(FileProtectionError, match="restrict") as exc_info:
        protect_file(tmp_path / "secret", platform="win32", run=run)

    assert "private-token" not in str(exc_info.value)
    assert "access denied" not in str(exc_info.value)


def test_windows_acl_timeout_is_a_protection_error(tmp_path: Path) -> None:
    def run(args: list[str], **_kwargs: object) -> Any:
        raise subprocess.TimeoutExpired(args, 5.0, output="private-output")

    with pytest.raises(FileProtectionError, match="timed out") as exc_info:
        protect_file(tmp_path / "secret", platform="win32", run=run)

    assert "private-output" not in str(exc_info.value)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows icacls")
def test_real_windows_acl_has_no_inherited_grants(tmp_path: Path) -> None:
    path = tmp_path / "secret.txt"
    path.write_text("secret", encoding="utf-8")

    protect_file(path)
    result = subprocess.run(
        ["icacls", str(path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )

    assert "(I)" not in result.stdout
    assert "S-1-5-18" in result.stdout or "SYSTEM" in result.stdout.upper()
