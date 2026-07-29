from __future__ import annotations

from pathlib import Path

import pytest

from aacc.kimi_web_error import KimiWebErrorCategory


def test_edge_profile_is_isolated_under_local_app_data(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import edge_profile_path

    assert edge_profile_path(tmp_path) == tmp_path / "AACC" / "kimi-edge-profile"


def test_edge_discovery_accepts_existing_explicit_program_files_path(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import find_edge_executable

    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")

    found = find_edge_executable(
        environ={"PROGRAMFILES(X86)": str(tmp_path)},
        registry_reader=lambda _key, _name: None,
    )

    assert found == edge


def test_edge_discovery_rejects_reparse_point(tmp_path: Path, monkeypatch) -> None:
    import aacc.kimi_edge_cdp as edge_cdp

    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"MZ")
    monkeypatch.setattr(edge_cdp, "_is_reparse_point", lambda candidate: candidate == edge)

    with pytest.raises(edge_cdp.EdgeSessionError) as raised:
        edge_cdp.find_edge_executable(
            environ={"PROGRAMFILES(X86)": str(tmp_path)},
            registry_reader=lambda _key, _name: None,
        )

    assert raised.value.category is KimiWebErrorCategory.LOAD_FAILED
    assert str(edge) not in str(raised.value)


def test_profile_validation_rejects_reparse_point(tmp_path: Path, monkeypatch) -> None:
    import aacc.kimi_edge_cdp as edge_cdp

    profile = edge_cdp.edge_profile_path(tmp_path)
    profile.mkdir(parents=True)
    monkeypatch.setattr(edge_cdp, "_is_reparse_point", lambda candidate: candidate == profile)

    with pytest.raises(edge_cdp.EdgeSessionError) as raised:
        edge_cdp.validate_owned_profile(profile, tmp_path)

    assert raised.value.category is KimiWebErrorCategory.LOAD_FAILED


def test_background_launch_uses_random_loopback_cdp_and_dedicated_profile(
    tmp_path: Path,
) -> None:
    from aacc.kimi_edge_cdp import KIMI_MEMBERSHIP_URL, build_edge_launch

    spec = build_edge_launch(Path("C:/Edge/msedge.exe"), tmp_path, visible=False)

    assert "--remote-debugging-address=127.0.0.1" in spec.arguments
    assert "--remote-debugging-port=0" in spec.arguments
    assert "--headless=new" in spec.arguments
    assert all("Default" not in argument for argument in spec.arguments)
    assert spec.arguments[-1] == f"--app={KIMI_MEMBERSHIP_URL}"


def test_visible_launch_does_not_request_headless_mode(tmp_path: Path) -> None:
    from aacc.kimi_edge_cdp import build_edge_launch

    spec = build_edge_launch(Path("C:/Edge/msedge.exe"), tmp_path, visible=True)

    assert "--headless=new" not in spec.arguments
    assert "--disable-gpu" not in spec.arguments
