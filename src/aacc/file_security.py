from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast


class FileProtectionError(RuntimeError):
    """The operating system could not restrict a sensitive file."""


def protect_file(
    path: Path,
    *,
    descriptor: int | None = None,
    platform: str = sys.platform,
) -> None:
    if platform == "win32":
        from aacc.file_security_windows import protect_windows_path

        protect_windows_path(path)
        return
    if descriptor is not None:
        cast(Any, os).fchmod(descriptor, 0o600)
    else:
        os.chmod(path, 0o600)


def protect_directory(
    path: Path,
    *,
    platform: str = sys.platform,
) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if platform == "win32":
        from aacc.file_security_windows import protect_windows_path

        protect_windows_path(path, directory=True)
    else:
        os.chmod(path, 0o700)


def atomic_replace(
    source: Path,
    target: Path,
    *,
    platform: str = sys.platform,
    source_handle: int | None = None,
) -> None:
    """Publish a protected sibling file without weakening platform guarantees."""
    if platform == "win32" and os.name == "nt":
        from aacc.file_security_windows import replace_windows_file

        replace_windows_file(source, target, source_handle=source_handle)
        return
    os.replace(source, target)
