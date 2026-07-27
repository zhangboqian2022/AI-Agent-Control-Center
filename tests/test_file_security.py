from __future__ import annotations

import os
from pathlib import Path

import pytest

import aacc.file_security_windows as windows_security
from aacc.file_security import protect_directory, protect_file


def test_windows_file_protection_routes_to_native_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        windows_security,
        "protect_windows_path",
        lambda path, *, directory=False: protected.append((path, directory)),
    )

    protect_file(tmp_path / "secret", platform="win32")

    assert protected == [(tmp_path / "secret", False)]


def test_windows_directory_protection_routes_to_native_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private"
    protected: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        windows_security,
        "protect_windows_path",
        lambda target, *, directory=False: protected.append((target, directory)),
    )

    protect_directory(path, platform="win32")

    assert path.is_dir()
    assert protected == [(path, True)]


def test_posix_file_protection_uses_descriptor_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        os,
        "fchmod",
        lambda descriptor, mode: calls.append((descriptor, mode)),
        raising=False,
    )

    protect_file(tmp_path / "secret", descriptor=42, platform="darwin")

    assert calls == [(42, 0o600)]


def test_posix_directory_protection_creates_and_chmods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "private"
    calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(os, "chmod", lambda target, mode: calls.append((target, mode)))

    protect_directory(path, platform="darwin")

    assert path.is_dir()
    assert calls == [(path, 0o700)]
