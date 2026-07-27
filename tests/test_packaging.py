import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from aacc import __version__
from aacc.gui import load_stylesheet
from aacc.models import AppConfig

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX shell scripts")
def test_required_scripts_exist_are_executable_and_parse() -> None:
    for name in (
        "install.sh",
        "uninstall.sh",
        "build_app.sh",
        "build_dmg.sh",
        "start.sh",
        "verify_release.sh",
    ):
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


def test_rc2_documentation_has_one_discovery_cadence_and_current_limit_titles() -> None:
    english_guide = (ROOT / "docs" / "user-guide.en.md").read_text(encoding="utf-8")
    assert "every two seconds" not in english_guide
    assert "Every five seconds" in english_guide
    assert (
        (ROOT / "KNOWN_LIMITATIONS.md")
        .read_text(encoding="utf-8")
        .startswith("# AACC Known Limitations")
    )
    assert (
        (ROOT / "KNOWN_LIMITATIONS.zh-CN.md")
        .read_text(encoding="utf-8")
        .startswith("# AACC 已知限制")
    )


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
    assert "--hidden-import PySide6.QtWebView" in script
    assert '--additional-hooks-dir "$project_root/hooks"' in script
    assert "QtWebEngine" not in script


def test_dmg_build_targets_desktop_and_contains_app_bundle() -> None:
    script = (ROOT / "scripts" / "build_dmg.sh").read_text(encoding="utf-8")
    assert "path to desktop folder" in script
    assert "AACC-${AACC_VERSION}.dmg" in script
    assert "dist/AACC.app" in script
    assert "hdiutil create" in script
    assert "SKIP_BUILD" in script
    assert "AACC_NOTARY_PROFILE" in script


def test_release_version_is_consistent_across_project_and_build_scripts() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == project["project"]["version"]
    design = (
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-07-27-v1.4.2-quota-and-windows-hardening-design.md"
    ).read_text(encoding="utf-8")
    target = re.search(r"\*\*Target release:\*\* (\d+\.\d+\.\d+)", design)
    assert target is not None
    assert __version__ == target.group(1)
    assert (ROOT / "docs" / f"release-notes-{__version__}.md").exists()
    for name in ("build_app.sh", "build_dmg.sh"):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert 'AACC_VERSION="${AACC_VERSION:-$(uv version --short)}"' in script


def test_installer_quits_running_copy_before_replacement() -> None:
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'tell application id "com.aacc.controlcenter" to quit' in script


def test_installer_links_runtime_not_repository_virtualenv() -> None:
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "Application Support/AACC/runtime" in script
    assert '"$project_root/.venv/bin/aacc"' not in script
    assert "uv sync --locked --extra dev" in script
    assert "uv export --locked --no-dev --no-emit-project" in script
    assert '"${wheels[0]}" --no-deps' in script
    assert "SKIP_BUILD" in script


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


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX shell scripts")
def test_partial_release_credentials_fail_before_build() -> None:
    cases = [
        {"AACC_CODESIGN_IDENTITY": "Developer ID Application: Example"},
        {"AACC_NOTARY_PROFILE": "example-notary-profile"},
    ]
    for extra_environment in cases:
        environment = os.environ.copy()
        environment.pop("AACC_CODESIGN_IDENTITY", None)
        environment.pop("AACC_NOTARY_PROFILE", None)
        environment.update(extra_environment)
        completed = subprocess.run(
            [str(ROOT / "scripts" / "build_app.sh")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert "AACC_CODESIGN_IDENTITY" in completed.stderr
        assert "AACC_NOTARY_PROFILE" in completed.stderr


def test_documentation_download_links_match_latest_published_release() -> None:
    readme_paths = [ROOT / "README.md", ROOT / "README.zh-CN.md"]
    published_versions: set[str] = set()
    for path in readme_paths:
        content = path.read_text(encoding="utf-8")
        download_links = re.findall(r"releases/download/\S+", content)
        assert download_links, path.name
        for link in download_links:
            match = re.search(r"/v(\d+\.\d+\.\d+(?:-rc\.\d+)?)/", link)
            assert match is not None, path.name
            published_versions.add(match.group(1))
    assert len(published_versions) == 1
    (published_version,) = published_versions
    dmg_name = f"AACC-{published_version}.dmg"

    documentation_paths = readme_paths + [
        ROOT / "docs" / "user-guide.md",
        ROOT / "docs" / "user-guide.en.md",
    ]
    for path in documentation_paths:
        content = path.read_text(encoding="utf-8")
        for referenced in re.findall(r"AACC-\d+\.\d+\.\d+(?:-rc\.\d+)?\.dmg", content):
            assert referenced == dmg_name, path.name


def test_windows_spec_exists_and_excludes_quartz() -> None:
    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    assert "console=False" in spec
    assert "Quartz" in spec  # 出现在 excludes
    assert "BUNDLE" not in spec
    assert "styles.qss" in spec


def test_windows_build_script_invokes_pyinstaller() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "AACC-windows.spec" in script
    assert "pyinstaller" in script.lower()
    assert "uv sync --locked --extra dev" in script


def test_ci_enforces_locked_sync_audit_report_and_diff_coverage() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow
    assert "uv sync --locked --extra dev" in workflow
    assert "continue-on-error: true" not in workflow
    assert "ruff format --check src tests" in workflow
    assert "QT_QPA_PLATFORM: offscreen" in workflow
    assert "--cov=src/aacc --cov-report=xml" in workflow
    assert "diff-cover coverage.xml" in workflow
    assert "--fail-under=90" in workflow
    assert "uv export --locked --extra dev --no-emit-project" in workflow
    assert "--requirement pip-audit-requirements.txt" in workflow
    assert "--no-deps --disable-pip" in workflow
    assert "--format=json --output=pip-audit-${{ matrix.os }}.json" in workflow
    assert "if: always()" in workflow
    assert "pip-audit-${{ matrix.os }}" in workflow

    type_check = workflow.split("- name: Type check", 1)[1].split("- name: Test", 1)[0]
    assert "if:" not in type_check
    audit = workflow.split("- name: Dependency vulnerability scan", 1)[1].split(
        "- name: Upload vulnerability report", 1
    )[0]
    assert "if:" not in audit


def test_ci_builds_native_packages_and_checks_windows_module_archive() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "scripts/build_app.sh" in workflow
    assert "test -d dist/AACC.app" in workflow
    assert "scripts/build_windows.ps1" in workflow
    assert 'Test-Path "dist\\AACC\\AACC.exe"' in workflow
    assert "pyi-archive_viewer -r" in workflow
    for module in ("aacc.win32", "aacc.automation_windows", "aacc.hotkeys_windows"):
        assert module in workflow
    assert 'Compress-Archive -Path "dist\\AACC"' in workflow
    assert "AACC-*-windows-x64.zip" in workflow
    assert "Upload Windows portable package" in workflow

    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    hidden_imports = spec.split("hiddenimports=", 1)[1].split("]", 1)[0]
    assert "aacc.win32" not in hidden_imports
    assert "aacc.automation_windows" not in hidden_imports
    assert "aacc.hotkeys_windows" not in hidden_imports


def test_build_uses_locked_development_environment() -> None:
    script = (ROOT / "scripts" / "build_app.sh").read_text(encoding="utf-8")
    assert "uv sync --locked --extra dev" in script


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX shell scripts")
def test_release_verifier_rejects_incomplete_or_broken_assets() -> None:
    path = ROOT / "scripts" / "verify_release.sh"
    assert path.exists()
    assert os.access(path, os.X_OK)
    assert subprocess.run(["/bin/bash", "-n", str(path)], check=False).returncode == 0

    script = path.read_text(encoding="utf-8")
    assert "draft" in script
    assert "prerelease" in script
    assert "AACC-${release_version}.dmg" in script
    assert "AACC-${release_version}.dmg.sha256" in script
    assert "browser_download_url" in script
    assert "asset_size" in script
    assert 'curl --fail --silent --show-error --location --head --output /dev/null "$url"' in script
    assert "github-repository" not in script


def test_release_docs_explain_codex_weekly_privacy_and_safe_gatekeeper_flow() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert "10080" in content
        assert "300-minute" not in content
        assert "300 分钟" not in content
        assert "shasum -a 256 AACC-1.4.1.dmg" in content
        assert "xattr -cr /Applications/AACC.app" in content


def test_specs_bundle_native_qt_webview_for_kimi_membership_login() -> None:
    for name in ("AACC.spec", "AACC-windows.spec"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "PySide6.QtWebView" in text
        assert "QtWebEngine" not in text
        assert "hooks" in text

    hook = (ROOT / "hooks" / "hook-PySide6.QtWebView.py").read_text(encoding="utf-8")
    assert "collect_module" in hook
    assert "webengine" in hook.lower()


def test_readme_first_screen_and_windows_checklists_are_cross_platform() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        first_screen = "\n".join(text.splitlines()[:30])
        assert "macOS 13+" in first_screen
        assert "Windows 10+" in first_screen

    for name in (
        "windows-verification-checklist.en.md",
        "windows-verification-checklist.zh-CN.md",
    ):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "WEEK" in text
        assert "5H" in text
        assert "MONTH" in text
        assert "config.yaml" in text
        assert "kimi-credentials.json" in text
        assert "icacls" in text
        assert "unprivileged" in text or "无特权" in text
