from __future__ import annotations

import os
from pathlib import Path

import pytest

from aacc.codex_app_server import WINDOWS_PROCESS_CREATION_FLAGS
from aacc.windows_broker import (
    BrokerCommand,
    build_broker_command,
    packaged_broker_path,
)


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def test_frozen_windows_uses_executable_sibling_and_ignores_override(
    tmp_path: Path,
) -> None:
    executable = _file(tmp_path / "AACC" / "AACC.exe")
    expected = _file(executable.with_name("aacc-spawn.exe"))
    override = _file(tmp_path / "untrusted" / "aacc-spawn.exe")

    result = packaged_broker_path(
        platform="win32",
        frozen=True,
        executable=executable,
        environ={"AACC_SPAWN_BROKER_PATH": str(override)},
    )

    assert result == expected


def test_source_windows_accepts_only_an_originally_absolute_override(
    tmp_path: Path,
) -> None:
    broker = _file(tmp_path / "native" / "aacc-spawn.exe")

    assert (
        packaged_broker_path(
            platform="win32",
            frozen=False,
            executable=tmp_path / "python.exe",
            environ={"AACC_SPAWN_BROKER_PATH": str(broker)},
        )
        == broker
    )
    assert (
        packaged_broker_path(
            platform="win32",
            frozen=False,
            executable=tmp_path / "python.exe",
            environ={"AACC_SPAWN_BROKER_PATH": "native/aacc-spawn.exe"},
        )
        is None
    )
    assert (
        packaged_broker_path(
            platform="win32",
            frozen=False,
            executable=tmp_path / "python.exe",
            environ={"AACC_SPAWN_BROKER_PATH": "~/native/aacc-spawn.exe"},
        )
        is None
    )


def test_non_windows_never_enables_broker_override(tmp_path: Path) -> None:
    broker = _file(tmp_path / "aacc-spawn.exe")

    assert (
        packaged_broker_path(
            platform="darwin",
            frozen=False,
            executable=tmp_path / "python",
            environ={"AACC_SPAWN_BROKER_PATH": str(broker)},
        )
        is None
    )


def test_build_broker_command_uses_fixed_protocol_and_arguments(tmp_path: Path) -> None:
    broker = _file(tmp_path / "AACC" / "aacc-spawn.exe")
    codex = _file(tmp_path / "用户 & tools" / "codex.cmd")
    bundle = tmp_path / "AACC" / "_internal"
    bundle.mkdir()

    command = build_broker_command(
        broker,
        codex,
        parent_pid=42,
        bundle_dir=bundle,
    )

    assert command == BrokerCommand(
        (
            str(broker),
            "--protocol",
            "1",
            "--parent-pid",
            "42",
            "--bundle-dir",
            str(bundle),
            "--codex",
            str(codex),
        ),
        WINDOWS_PROCESS_CREATION_FLAGS,
    )


@pytest.mark.parametrize("suffix", ("", ".py", ".com"))
def test_build_broker_command_rejects_unsupported_codex_target(
    tmp_path: Path,
    suffix: str,
) -> None:
    broker = _file(tmp_path / "aacc-spawn.exe")
    codex = _file(tmp_path / f"codex{suffix}")
    bundle = tmp_path / "_internal"
    bundle.mkdir()

    with pytest.raises(ValueError, match="Codex executable"):
        build_broker_command(broker, codex, parent_pid=1, bundle_dir=bundle)


def test_build_broker_command_rejects_relative_paths_without_resolving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _file(tmp_path / "aacc-spawn.exe")
    _file(tmp_path / "codex.cmd")
    (tmp_path / "_internal").mkdir()

    for broker, codex, bundle in (
        (Path("aacc-spawn.exe"), tmp_path / "codex.cmd", tmp_path / "_internal"),
        (tmp_path / "aacc-spawn.exe", Path("codex.cmd"), tmp_path / "_internal"),
        (tmp_path / "aacc-spawn.exe", tmp_path / "codex.cmd", Path("_internal")),
    ):
        with pytest.raises(ValueError, match="absolute"):
            build_broker_command(
                broker,
                codex,
                parent_pid=os.getpid(),
                bundle_dir=bundle,
            )


def test_build_broker_command_requires_existing_regular_files_and_directory(
    tmp_path: Path,
) -> None:
    broker = _file(tmp_path / "aacc-spawn.exe")
    codex = _file(tmp_path / "codex.exe")
    bundle = tmp_path / "_internal"
    bundle.mkdir()

    with pytest.raises(ValueError, match="broker"):
        build_broker_command(
            tmp_path / "missing.exe",
            codex,
            parent_pid=1,
            bundle_dir=bundle,
        )
    with pytest.raises(ValueError, match="Codex"):
        build_broker_command(
            broker,
            tmp_path / "missing.cmd",
            parent_pid=1,
            bundle_dir=bundle,
        )
    with pytest.raises(ValueError, match="bundle"):
        build_broker_command(
            broker,
            codex,
            parent_pid=1,
            bundle_dir=tmp_path / "missing",
        )


@pytest.mark.parametrize("parent_pid", (0, -1, True))
def test_build_broker_command_requires_positive_integer_parent_pid(
    tmp_path: Path,
    parent_pid: int,
) -> None:
    broker = _file(tmp_path / "aacc-spawn.exe")
    codex = _file(tmp_path / "codex.bat")
    bundle = tmp_path / "_internal"
    bundle.mkdir()

    with pytest.raises(ValueError, match="parent PID"):
        build_broker_command(
            broker,
            codex,
            parent_pid=parent_pid,
            bundle_dir=bundle,
        )
