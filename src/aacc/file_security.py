from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

RunCommand = Callable[..., subprocess.CompletedProcess[str]]

_SID_PATTERN = re.compile(r"\bS-\d+(?:-\d+)+\b")
_COMMAND_TIMEOUT_SECONDS = 5.0
_LOCAL_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_OWNER_RIGHTS_SID = "S-1-3-4"


class FileProtectionError(RuntimeError):
    """The operating system could not restrict a sensitive file."""


def protect_file(
    path: Path,
    *,
    descriptor: int | None = None,
    platform: str = sys.platform,
    run: RunCommand = subprocess.run,
) -> None:
    if platform != "win32":
        if descriptor is not None:
            cast(Any, os).fchmod(descriptor, 0o600)
        else:
            os.chmod(path, 0o600)
        return
    user_sid = _current_windows_user_sid(run)
    try:
        result = run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/remove:g",
                f"*{_OWNER_RIGHTS_SID}",
                "/grant:r",
                f"*{user_sid}:(F)",
                f"*{_LOCAL_SYSTEM_SID}:(F)",
                f"*{_ADMINISTRATORS_SID}:(F)",
            ],
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise FileProtectionError("Windows file permission update timed out") from None
    except OSError:
        raise FileProtectionError("Unable to start Windows file permission update") from None
    if result.returncode != 0:
        raise FileProtectionError("Unable to restrict sensitive file permissions")


def protect_directory(
    path: Path,
    *,
    platform: str = sys.platform,
) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if platform != "win32":
        os.chmod(path, 0o700)


def _current_windows_user_sid(run: RunCommand) -> str:
    try:
        result = run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise FileProtectionError("Windows user SID lookup timed out") from None
    except OSError:
        raise FileProtectionError("Unable to start Windows user SID lookup") from None
    if result.returncode != 0:
        raise FileProtectionError("Unable to identify the current Windows user")
    match = _SID_PATTERN.search(result.stdout)
    if match is None:
        raise FileProtectionError("Unable to identify the current Windows user SID")
    return match.group(0)
