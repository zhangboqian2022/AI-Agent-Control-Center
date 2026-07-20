import os
import subprocess
from pathlib import Path

import yaml

from aacc import __version__
from aacc.gui import load_stylesheet
from aacc.models import AppConfig

ROOT = Path(__file__).parents[1]


def test_required_scripts_exist_are_executable_and_parse() -> None:
    for name in ("install.sh", "uninstall.sh", "build_app.sh", "build_dmg.sh", "start.sh"):
        path = ROOT / "scripts" / name
        assert path.exists(), name
        assert os.access(path, os.X_OK), name
        assert subprocess.run(["/bin/bash", "-n", str(path)], check=False).returncode == 0


def test_example_configuration_validates() -> None:
    raw = yaml.safe_load((ROOT / "examples" / "config.example.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(raw)
    assert len(config.tasks) == 4
    assert config.app.api.host == "127.0.0.1"


def test_required_documentation_exists_without_placeholders() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "LICENSE",
        ROOT / "AI-Agent-Control-Center-Specification.md",
        ROOT / "docs" / "user-guide.md",
        ROOT / "docs" / "adapter-development.md",
        ROOT / "docs" / "troubleshooting.md",
        ROOT / "docs" / "test-report.md",
    ]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert len(content) > 100, path.name
        assert "T" + "ODO" not in content
        assert "T" + "BD" not in content


def test_console_entry_points_are_registered() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'aacc = "aacc.cli:main"' in pyproject
    assert 'aacc-run = "aacc.run_wrapper:main"' in pyproject
    assert 'aacc-gui = "aacc.app:main"' in pyproject


def test_app_build_sets_release_version_and_excludes_development_tools() -> None:
    script = (ROOT / "scripts" / "build_app.sh").read_text(encoding="utf-8")
    assert "CFBundleShortVersionString" in script
    assert "CFBundleVersion" in script
    assert "--exclude-module mypy" in script
    assert "--hidden-import Quartz" in script


def test_dmg_build_targets_desktop_and_contains_app_bundle() -> None:
    script = (ROOT / "scripts" / "build_dmg.sh").read_text(encoding="utf-8")
    assert "path to desktop folder" in script
    assert "AACC-1.3.0-rc.1.dmg" in script
    assert "dist/AACC.app" in script
    assert "hdiutil create" in script
    assert "SKIP_BUILD" in script
    assert "AACC_NOTARY_PROFILE" in script


def test_release_version_is_consistent_across_project_and_build_scripts() -> None:
    assert __version__ == "1.3.0rc1"
    assert 'version = "1.3.0rc1"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'AACC_VERSION="${AACC_VERSION:-1.3.0-rc.1}"' in (
        ROOT / "scripts" / "build_app.sh"
    ).read_text(encoding="utf-8")


def test_installer_quits_running_copy_before_replacement() -> None:
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'tell application id "com.aacc.controlcenter" to quit' in script


def test_installer_links_runtime_not_repository_virtualenv() -> None:
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "Application Support/AACC/runtime" in script
    assert '"$project_root/.venv/bin/aacc"' not in script
    assert "uv sync --extra dev" in script
    assert "uv pip install" in script


def test_stylesheet_is_packaged_resource() -> None:
    assert "#panel" in load_stylesheet()
    assert "#discoveryWarning" in load_stylesheet()


def test_build_scripts_support_explicit_signing_and_notarization() -> None:
    app_script = (ROOT / "scripts" / "build_app.sh").read_text(encoding="utf-8")
    dmg_script = (ROOT / "scripts" / "build_dmg.sh").read_text(encoding="utf-8")
    assert "AACC_CODESIGN_IDENTITY" in app_script
    assert "--options runtime" in app_script
    assert "notarytool submit" in dmg_script
    assert "stapler staple" in dmg_script
