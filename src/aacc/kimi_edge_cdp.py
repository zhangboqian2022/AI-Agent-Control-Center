"""Isolated Microsoft Edge session primitives for Kimi membership quota."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from aacc.kimi_web_error import KimiWebErrorCategory

KIMI_MEMBERSHIP_URL = "https://www.kimi.com/membership/subscription"
_EDGE_RELATIVE_PATH = Path("Microsoft") / "Edge" / "Application" / "msedge.exe"
_REPARSE_POINT_ATTRIBUTE = 0x400


class EdgeSessionError(RuntimeError):
    """Sanitized failure raised by the managed Edge boundary."""

    def __init__(self, category: KimiWebErrorCategory) -> None:
        self.category = category
        super().__init__(category.value)


@dataclass(frozen=True)
class EdgeLaunchSpec:
    executable: Path
    arguments: tuple[str, ...]
    profile: Path


def edge_profile_path(local_app_data: Path) -> Path:
    """Return the only Edge profile AACC is allowed to manage."""

    return local_app_data / "AACC" / "kimi-edge-profile"


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def validate_owned_profile(profile: Path, local_app_data: Path) -> None:
    """Reject a profile that is not the exact AACC-owned path."""

    expected = edge_profile_path(local_app_data)
    if profile != expected or _is_reparse_point(profile):
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    aacc_root = expected.parent
    if _is_reparse_point(aacc_root) or _is_reparse_point(local_app_data):
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)
    if profile.exists() and not profile.is_dir():
        raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)


def _default_registry_reader(key: str, name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def find_edge_executable(
    *,
    environ: Mapping[str, str] = os.environ,
    registry_reader: Callable[[str, str], str | None] = _default_registry_reader,
) -> Path:
    """Locate an installed Edge binary without invoking a shell or PATH."""

    candidates: list[Path] = []
    registry_value = registry_reader(
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        "",
    )
    if registry_value:
        candidates.append(Path(registry_value))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = environ.get(variable)
        if root:
            candidates.append(Path(root) / _EDGE_RELATIVE_PATH)

    for candidate in candidates:
        if (
            candidate.name.casefold() == "msedge.exe"
            and candidate.is_file()
            and not _is_reparse_point(candidate)
        ):
            return candidate
    raise EdgeSessionError(KimiWebErrorCategory.LOAD_FAILED)


def build_edge_launch(executable: Path, profile: Path, *, visible: bool) -> EdgeLaunchSpec:
    """Build a shell-free launch specification for a managed Edge process."""

    mode_arguments: tuple[str, ...] = () if visible else ("--headless=new", "--disable-gpu")
    return EdgeLaunchSpec(
        executable=executable,
        profile=profile,
        arguments=(
            f"--user-data-dir={profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            *mode_arguments,
            f"--app={KIMI_MEMBERSHIP_URL}",
        ),
    )
