import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
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
    assert "--hidden-import aacc.kimi_web_session" in script
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
    assert "disable_windowed_traceback=True" in spec
    assert "Quartz" in spec  # 出现在 excludes
    assert "BUNDLE" not in spec
    assert "styles.qss" in spec


def test_windows_native_acl_dependency_and_payload_are_pinned() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "pywin32==312; sys_platform == 'win32'" in project["project"]["dependencies"]

    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    hidden_imports = spec.split("hiddenimports=", 1)[1].split("]", 1)[0]
    for module in (
        "win32api",
        "win32con",
        "win32security",
        "ntsecuritycon",
        "pywintypes",
    ):
        assert repr(module) in hidden_imports
    assert "pywintypes312.dll" not in spec
    assert "f'pywintypes{sys.version_info.major}{sys.version_info.minor}.dll'" in spec
    binaries = spec.split("binaries=", 1)[1].split("]", 1)[0]
    assert "PYWINTYPES_DLL_NAME" in binaries


@pytest.mark.parametrize(
    ("python_version", "dll_name"),
    [
        ((3, 12), "pywintypes312.dll"),
        ((3, 13), "pywintypes313.dll"),
    ],
)
def test_windows_pywintypes_payload_name_tracks_python_abi(
    python_version: tuple[int, int], dll_name: str
) -> None:
    major, minor = python_version

    assert f"pywintypes{major}{minor}.dll" == dll_name


def test_windows_build_script_invokes_pyinstaller() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "AACC-windows.spec" in script
    assert "pyinstaller" in script.lower()
    assert "uv sync --locked --extra dev" in script


def test_windows_build_packages_spawn_broker_at_exact_onedir_root() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    broker_build = script.index("build_spawn_broker.ps1")
    pyinstaller_build = script.index("uv run pyinstaller")
    broker_copy = script.index(
        'Copy-Item "build\\native\\aacc-spawn.exe" "dist\\AACC\\aacc-spawn.exe" -Force'
    )
    assert broker_build < pyinstaller_build < broker_copy
    assert "Compare-Object -ReferenceObject $expectedRootEntries" in script
    assert "unexpected Windows package root" in script
    assert "@($rootFiles | Sort-Object) -join" not in script


def test_windows_build_chains_setup_unless_explicitly_skipped() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    root_validation = script.index("unexpected Windows package root")
    installer_build = script.index("build_windows_installer.ps1")
    assert root_validation < installer_build
    assert "AACC_SKIP_INSTALLER" in script
    assert '$env:AACC_SKIP_INSTALLER -ne "1"' in script


def test_windows_edge_login_package_does_not_bundle_or_require_webview2() -> None:
    script = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")

    assert "MicrosoftEdgeWebview2Setup.exe" not in script
    assert "EnsureWebView2Runtime" not in installer
    assert "PySide6.QtWebView" not in spec
    assert "'websocket'" in spec


def test_windows_installer_keeps_preflight_without_browser_runtime_gate() -> None:
    installer = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    prepare_to_install = installer.split("function PrepareToInstall", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]

    assert "ValidateInternalRootForInstall(ErrorMessage)" in prepare_to_install
    assert "ShutdownExistingAACC(ErrorMessage)" in prepare_to_install
    assert "ValidatePackagedTargetsForInstall(ErrorMessage)" in prepare_to_install
    assert "WebView2" not in prepare_to_install
    assert "Result := '';" in prepare_to_install


def test_windows_webview_smoke_exercises_a_visible_native_controller_after_setup() -> None:
    native_module = ROOT / "src" / "aacc" / "webview_smoke.py"
    smoke = ROOT / "scripts" / "smoke_windows_webview.py"
    assert native_module.exists()
    assert smoke.exists()
    module = native_module.read_text(encoding="utf-8")
    script = smoke.read_text(encoding="utf-8")

    assert 'sys.platform != "win32"' in script
    assert "unsupported-platform" in script
    assert 'import_module("aacc.webview_smoke")' in script
    assert 'import_module("aacc.constants")' in script
    assert (
        "raise SystemExit(webview_smoke.run_native_webview_smoke(constants.APP_SUPPORT_DIR))"
        in script
    )
    assert "class NativeWebViewSmoke" not in script
    assert "QWebView" not in script
    assert "SMOKE_TIMEOUT_MS = 30_000" in module
    assert module.index("initialize_native_webview(data_dir)") < module.index("QApplication(")
    assert "QTimer" in module
    assert "QDialog" in module
    assert "_dialog.show()" in module
    assert "QWidget.createWindowContainer(" in module
    assert "view.loadHtml(" in module
    assert "LoadStatus.Succeeded" in module
    assert "runJavaScript" in module
    assert ".page()" not in module
    assert "unexpected-javascript-result" in module
    assert "timeout" in module
    assert "EXIT_FAILURE = 1" in module
    assert "self._exit_code = EXIT_FAILURE" in module
    assert "if self._finished:\n            return\n        if result !=" in module
    assert "QtWebEngine" not in module


def test_windows_2025_ci_runs_installed_product_without_native_webview_smoke() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package_job = workflow.split("  windows-package-2025:", 1)[1]
    assert "Smoke frozen and installed Windows product" in package_job
    assert "uv run python scripts/smoke_windows_webview.py" not in package_job

    package_smoke = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    fresh_install = package_smoke.index(
        'Assert-InstalledRootPayloadHashes -EvidenceCategory "installed"'
    )
    installed_launch = package_smoke.index(
        '$Installed = Invoke-InstalledLaunch -Category "installed"'
    )
    assert fresh_install < installed_launch
    assert "--smoke-native-webview" not in package_smoke
    assert "AACC_WEBVIEW_SMOKE_RESULT_PATH" not in package_smoke


def test_windows_installed_product_smoke_exercises_managed_edge_cdp_path() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    fresh_install = script.index('Assert-InstalledRootPayloadHashes -EvidenceCategory "installed"')
    edge_smoke = script.rindex("Invoke-InstalledEdgeCdpSmoke")
    installed_launch = script.index('$Installed = Invoke-InstalledLaunch -Category "installed"')

    assert fresh_install < edge_smoke < installed_launch
    assert "AACC_EDGE_CDP_SMOKE_RESULT_PATH" in script
    assert "AACC_EDGE_CDP_SMOKE category=success" in script
    assert '"AACC\\kimi-edge-profile"' in script
    assert "Assert-ExactAcl -Path $EdgeProfile -Directory $true" in script
    assert '-EvidenceCategory "installed" -EvidenceName "kimi-edge-profile"' in script
    assert "Assert-ProductProcessBaseline" in script
    assert "Assert-ManagedEdgeProcessBaseline" in script


def test_windows_edge_docs_explain_persistent_isolated_login() -> None:
    def normalized(name: str) -> str:
        return " ".join((ROOT / name).read_text(encoding="utf-8").split())

    english_docs = (
        "README.md",
        "docs/user-guide.en.md",
        "docs/release-notes-1.4.2.md",
        "CHANGELOG.md",
    )
    for name in english_docs:
        document = normalized(name)
        assert "AACC-owned Edge profile" in document
        assert "until you sign out" in document
        assert "WebView2" not in document

    chinese_docs = (
        "README.zh-CN.md",
        "docs/user-guide.md",
        "docs/release-notes-1.4.2.md",
        "CHANGELOG.zh-CN.md",
    )
    for name in chinese_docs:
        document = normalized(name)
        assert "AACC 专用 Edge 配置目录" in document
        assert "手动退出" in document
        assert "WebView2" not in document

    english_checklist = normalized("docs/windows-verification-checklist.en.md")
    assert "AACC-owned Edge profile" in english_checklist
    assert "%LOCALAPPDATA%\\AACC\\kimi-edge-profile" in english_checklist
    assert "WebView2" not in english_checklist

    chinese_checklist = normalized("docs/windows-verification-checklist.zh-CN.md")
    assert "AACC 专用 Edge 配置目录" in chinese_checklist
    assert "%LOCALAPPDATA%\\AACC\\kimi-edge-profile" in chinese_checklist
    assert "WebView2" not in chinese_checklist


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

    assert "os: [macos-latest, windows-2022, windows-2025-vs2026]" in workflow
    assert "windows-frozen-2022:" in workflow
    assert "windows-package-2025:" in workflow
    assert "scripts/build_app.sh" in workflow
    assert "test -d dist/AACC.app" in workflow
    assert "scripts/build_windows.ps1" in workflow
    assert 'Test-Path -LiteralPath "dist\\AACC\\AACC.exe" -PathType Leaf' in workflow
    assert 'Test-Path -LiteralPath "dist\\AACC\\aacc-spawn.exe" -PathType Leaf' in workflow
    assert "pyi-archive_viewer -r" in workflow
    for module in (
        "aacc.win32",
        "aacc.automation_windows",
        "aacc.hotkeys_windows",
        "aacc.windows_broker",
        "aacc.kimi_edge_cdp",
        "aacc.kimi_edge_session",
        "aacc.kimi_membership_query",
        "websocket",
    ):
        assert module in workflow
    assert (
        workflow.count(
            'Get-ChildItem -LiteralPath "dist\\AACC" -Recurse -File -Filter "win32event*.pyd"'
        )
        == 2
    )
    assert workflow.count("$eventModules.Count -ne 1") == 2
    assert workflow.count("$eventModules[0].Length -le 0") == 2
    assert (
        '$portableBuildPath = Join-Path "build\\candidate-validation" '
        '"AACC-$version-windows-x64-windows-2025-vs2026.zip"' in workflow
    )
    assert (
        'Compress-Archive -LiteralPath "dist\\AACC" '
        "-DestinationPath $portableBuildPath -Force" in workflow
    )
    assert "Copy-Item -LiteralPath $portableBuildPath -Destination $portablePath" in workflow
    assert (
        'Compress-Archive -LiteralPath "dist\\AACC" -DestinationPath $portablePath' not in workflow
    )
    assert "AACC-*-windows-x64-windows-2025-vs2026.zip" in workflow
    assert "Package and strictly verify primary artifacts" in workflow
    assert "build/verified-output" in workflow

    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    hidden_imports = spec.split("hiddenimports=", 1)[1].split("]", 1)[0]
    assert "'win32event'" in hidden_imports
    assert "aacc.win32" not in hidden_imports
    assert "aacc.automation_windows" not in hidden_imports
    assert "aacc.hotkeys_windows" not in hidden_imports


def test_ci_runs_real_windows_product_smoke_before_primary_artifact_publish() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "windows-2022" in workflow
    assert "windows-2025-vs2026" in workflow
    assert "windows-latest" not in workflow
    assert "scripts/test_windows_package.ps1" in workflow
    assert "AACC_SKIP_INSTALLER: 1" in workflow
    assert "windows-smoke-windows-2022" in workflow
    assert "windows-smoke-windows-2025-vs2026" in workflow
    assert "Smoke frozen Windows product" in workflow
    assert "Smoke frozen and installed Windows product" in workflow
    assert "windows-frozen-2022:" in workflow
    assert "windows-package-2025:" in workflow
    final_job = workflow.split("windows-package-2025:", 1)[1]
    assert "needs: [quality, windows-frozen-2022]" in final_job
    assert "scripts/verify_windows_artifacts.py" in workflow
    assert "AACC-*-Setup.exe" in workflow
    assert "AACC-*-Setup.exe.sha256" in workflow
    assert "windows-smoke" in workflow
    frozen_job = workflow.split("windows-frozen-2022:", 1)[1].split("windows-package-2025:", 1)[0]
    assert "AACC-*-Setup.exe" not in frozen_job
    assert "windows-x64" not in frozen_job
    assert "windows-verified" not in workflow
    assert final_job.count("actions/upload-artifact@v4") == 2
    primary_step = final_job.split("Upload primary Windows Setup artifacts", 1)[1].split(
        "Upload Windows smoke diagnostics", 1
    )[0]
    assert "if: always()" not in primary_step
    assert "if: failure()" not in primary_step
    assert "if-no-files-found: error" in primary_step
    assert "Hosted Windows Server evidence only" in workflow


def test_ci_isolates_unverified_primary_artifacts_from_always_uploaded_diagnostics() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    final_job = workflow.split("windows-package-2025:", 1)[1]
    package_step = final_job.split("Package and strictly verify primary artifacts", 1)[1].split(
        "Upload primary Windows Setup artifacts", 1
    )[0]
    diagnostics_step = final_job.split("Upload Windows smoke diagnostics", 1)[1]

    assert "build\\candidate-validation" in package_step
    assert "build\\windows-smoke\\artifact" not in package_step
    assert "build/windows-smoke" in diagnostics_step
    assert "candidate-validation" not in diagnostics_step
    assert "verified-output" not in diagnostics_step
    for primary_glob in ("AACC-*-Setup.exe", "AACC-*-Setup.exe.sha256", "windows-x64"):
        assert primary_glob not in diagnostics_step


def test_hosted_windows_build_and_product_smoke_run_under_windows_powershell_51() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    frozen_job = workflow.split("windows-frozen-2022:", 1)[1].split("windows-package-2025:", 1)[0]
    package_job = workflow.split("windows-package-2025:", 1)[1]

    for job in (frozen_job, package_job):
        build_step = job.split("Build Windows app", 1)[1].split("- name:", 1)[0]
        smoke_step = job.split("Smoke frozen", 1)[1].split("- name:", 1)[0]
        assert "shell: powershell" in build_step
        assert "shell: powershell" in smoke_step
        assert "shell: pwsh" not in build_step
        assert "shell: pwsh" not in smoke_step


def test_windows_product_smoke_uses_native_qt_windows_for_shutdown_protocol() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    frozen_launch = script.split("function Invoke-FrozenSmoke", 1)[1].split(
        "function Invoke-InstalledLaunch", 1
    )[0]
    installed_launch = script.split("function Invoke-InstalledLaunch", 1)[1].split(
        "function Test-InstalledControlRefusal", 1
    )[0]
    for launch in (frozen_launch, installed_launch):
        assert '$env:QT_QPA_PLATFORM = "windows"' in launch
        assert '$env:QT_QPA_PLATFORM = "offscreen"' not in launch


def test_windows_product_smoke_has_bounded_exact_identity_and_state_checks() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    for required in (
        "Wait-ProcessDeadline",
        "Stop-OwnedProcessIdentity",
        "CreationTimeUtc",
        "Path",
        "GetAccessRules",
        "AreAccessRulesProtected",
        "S-1-5-18",
        "S-1-5-32-544",
        "InheritanceFlags",
        "PropagationFlags",
        "Get-TreeManifest",
        "Get-RegistryManifest",
        "Get-ShortcutManifest",
        "Get-AppDataManifest",
        "rollback-sentinel.bin",
        "fake_legacy_aacc.cpp",
        "lock_payload.cpp",
        "--shutdown-for-update",
        "/NOCLOSEAPPLICATIONS",
        "/NOFORCECLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS",
        "AACC_CONFIG_PATH",
        "AACC_DATABASE_PATH",
        "AACC_CODEX_EXECUTABLE",
        "QT_QPA_PLATFORM",
        "20",
        "Server",
    ):
        assert required in script
    assert "pendingfilerenameoperations" in script.lower()
    assert "Get-Process cmd" not in script
    assert "Get-Process python" not in script
    assert "taskkill" not in script.lower()
    assert "Stop-Process -Name" not in script
    for required in (
        "Write-SmokeEvidence",
        "GetSecurityDescriptorSddlForm",
        "before-manifest.json",
        "after-manifest.json",
        "elapsedMilliseconds",
        "OwnedProcessRegistry",
        "Invoke-OwnedCleanup",
        "Wait-UninstallerTreeGone",
        "Get-ProductProcessBaseline",
        "Assert-ProductProcessBaseline",
        "AACC_LEGACY_EVIDENCE_FILE",
        "legacy-control-evidence.jsonl",
        "Get-ProcessIdentity -Process $Process",
        "Assert-NonEmptyLiteralFile",
        "Assert-InstalledRootPayloadHashes",
        "Get-StableAppDataState",
        "Assert-StableAppDataState",
        "aacc_smoke_preservation",
        "temporaryClones",
        "AACC_PREFLIGHT result=target-unavailable",
        "$LockCases = @(",
        'Name = "aacc"',
        'Name = "broker"',
        'Name = "internal"',
        'Name = "directory"',
        'Name = "uninstaller"',
        'Name = "uninstaller-data"',
        'Name = "shortcut"',
        "stale-obsolete.pyd",
        "nested-junction-victim-backup",
        "nested-junction-refusal.log",
        "junction-external-preserve.txt",
        "junction-refusal-external-manifest.json",
        "Assert-DiagnosticsTreeHasNoPrimaryArtifacts",
        "stale-parent-pid-edge.json",
        "reused-parent-exit-sequence.json",
        "child-pid-reuse-sequence.json",
    ):
        assert required in script
    assert "if (-not $Process.WaitForExit(5000))" in script
    assert "$SpecialLeaf\\broker-marker.json" in script
    assert "$SpecialLeaf\\timeout-identities.jsonl" in script
    assert '$SmokeRoot "installed\\$SpecialLeaf\\setup copy' not in script
    assert '$CandidateRoot "product-smoke\\$SpecialLeaf\\setup copy' in script
    assert "$Stopwatch.ElapsedMilliseconds -ge 23000" in script
    assert "$Stopwatch.ElapsedMilliseconds -le 35000" in script
    assert '$SavedAacc = Join-Path $CandidateRoot "product-smoke\\saved-AACC.exe"' in script
    assert 'Join-Path $SmokeRoot "reinstall\\saved-AACC.exe"' not in script
    assert '"fake legacy AACC.exe"' not in script
    assert '"legacy-window-fixture.exe"' in script


def test_windows_product_smoke_fixtures_are_strict_and_bounded() -> None:
    fake_server = (ROOT / "tests" / "windows" / "fake_codex_server.py").read_text(encoding="utf-8")
    timeout_server = (ROOT / "tests" / "windows" / "fake_codex_timeout.py").read_text(
        encoding="utf-8"
    )
    fake_cmd = (ROOT / "tests" / "windows" / "fake-codex.cmd").read_text(encoding="utf-8")
    legacy = (ROOT / "tests" / "windows" / "fake_legacy_aacc.cpp").read_text(encoding="utf-8")
    locker = (ROOT / "tests" / "windows" / "lock_payload.cpp").read_text(encoding="utf-8")

    assert "initialize" in fake_server
    assert "account/rateLimits/read" in fake_server
    assert "AACC_FAKE_CODEX_MARKER" in fake_server
    fake_main = fake_server.split("def main()", 1)[1]
    marker_commit = fake_main.index("os.replace(")
    quota_reply = fake_main.rindex("_reply(")
    assert marker_commit < quota_reply
    assert "CREATE_NEW_PROCESS_GROUP" in timeout_server
    assert "FILE_FLAG_BACKUP_SEMANTICS" in locker
    assert "payload_is_directory ? DELETE : 0" in locker
    assert "creation_time" in timeout_server
    assert "image_path" in timeout_server
    assert "%~dp0" in fake_cmd
    assert "AI Agent Control Center" in legacy
    assert "CreateWindowExW" in legacy
    assert "false-success" in legacy
    assert "return 0;" in legacy
    assert "AACC_LEGACY_EVIDENCE_FILE" in legacy
    assert "creation_time" in legacy
    assert "image_path" in legacy
    lock_open = locker.split("HANDLE payload = CreateFileW(", 1)[1].split(");", 1)[0]
    assert "GENERIC_READ" in lock_open
    assert "payload_is_directory ? DELETE : 0" in lock_open
    assert re.search(r"payload_is_directory \? DELETE : 0\),\s*0,", lock_open)
    assert "LOCK_READY" in locker
    assert "ROLLBACK_PROBE_OBSERVED" in locker
    assert "ReadAllBytes" in locker


def test_windows_fake_codex_commits_marker_before_quota_reply(tmp_path: Path) -> None:
    marker = tmp_path / "测试 marker &() %! [x].json"
    environment = os.environ.copy()
    environment["AACC_FAKE_CODEX_MARKER"] = str(marker)
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tests" / "windows" / "fake_codex_server.py"),
            "app-server",
            "--stdio",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write('{"id":1,"method":"initialize"}\n')
        process.stdin.flush()
        assert json.loads(process.stdout.readline()) == {"id": 1, "result": {}}
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.write('{"id":2,"method":"account/rateLimits/read"}\n')
        process.stdin.flush()
        quota_reply = json.loads(process.stdout.readline())

        marker_bytes = marker.read_bytes()
        marker_record = json.loads(marker_bytes)
        assert marker_bytes
        assert quota_reply["id"] == 2
        # A Windows virtual-environment launcher may spawn the base Python
        # interpreter, so the fixture identity need not equal the Popen wrapper.
        assert isinstance(marker_record["pid"], int)
        assert marker_record["pid"] > 0
        assert marker_record["initialize"] == "initialize"
        assert marker_record["request"] == "account/rateLimits/read"
        assert Path(marker_record["image_path"]).is_absolute()
        assert Path(marker_record["image_path"]).is_file()
        assert isinstance(marker_record["creation_time"], float)
        assert marker_record["creation_time"] > 0

        process.stdin.close()
        assert process.wait(timeout=5) == 0
        assert json.loads(marker.read_bytes()) == marker_record
        assert not marker.with_name(marker.name + ".tmp").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_windows_artifact_verifier_rejects_malformed_checksum_and_zip_layout() -> None:
    script = (ROOT / "scripts" / "verify_windows_artifacts.py").read_text(encoding="utf-8")

    for required in (
        "AACC.exe",
        "aacc-spawn.exe",
        "_internal",
        "sha256",
        "is_absolute",
        "PurePosixPath",
        "setup_path",
        "portable_path",
        "built_root",
    ):
        assert required in script
    assert "utf-8-sig" not in script
    assert ".." in script


def test_windows_artifact_verifier_accepts_exact_tree_and_rejects_case_collision(
    tmp_path: Path,
) -> None:
    built_root = tmp_path / "dist" / "AACC"
    internal = built_root / "_internal"
    internal.mkdir(parents=True)
    (built_root / "AACC.exe").write_bytes(b"gui")
    (built_root / "aacc-spawn.exe").write_bytes(b"broker")
    (internal / "runtime.bin").write_bytes(b"runtime")
    setup = tmp_path / "AACC-1.4.2-Setup.exe"
    setup.write_bytes(b"s" * (1024 * 1024 + 1))
    checksum = tmp_path / f"{setup.name}.sha256"
    checksum.write_bytes(
        f"{hashlib.sha256(setup.read_bytes()).hexdigest()}  {setup.name}\n".encode()
    )
    portable = tmp_path / "portable.zip"

    def write_portable(*, collision: bool, unsafe_root: bool = False) -> None:
        with zipfile.ZipFile(portable, "w") as archive:
            if unsafe_root:
                root = zipfile.ZipInfo("AACC/")
                root.external_attr = (0o120777 << 16) | 0x10
                archive.writestr(root, b"target")
            archive.writestr("AACC/_internal/", b"")
            archive.writestr("AACC/AACC.exe", b"gui")
            archive.writestr("AACC/aacc-spawn.exe", b"broker")
            archive.writestr("AACC/_internal/runtime.bin", b"runtime")
            if collision:
                archive.writestr("AACC/aacc.exe", b"collision")

    command = [
        sys.executable,
        str(ROOT / "scripts" / "verify_windows_artifacts.py"),
        "--setup",
        str(setup),
        "--checksum",
        str(checksum),
        "--portable",
        str(portable),
        "--built-root",
        str(built_root),
    ]
    write_portable(collision=False)
    assert subprocess.run(command, check=False).returncode == 0

    write_portable(collision=True)
    assert subprocess.run(command, check=False, capture_output=True).returncode != 0

    write_portable(collision=False, unsafe_root=True)
    assert subprocess.run(command, check=False, capture_output=True).returncode != 0


def test_windows_spec_includes_broker_python_module_but_not_native_binary() -> None:
    spec = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    hidden_imports = spec.split("hiddenimports=", 1)[1].split("]", 1)[0]
    binaries = spec.split("binaries=", 1)[1].split("]", 1)[0]
    datas = spec.split("datas=", 1)[1].split("]", 1)[0]

    assert "'aacc.windows_broker'" in hidden_imports
    assert "aacc-spawn" not in binaries
    assert "aacc-spawn" not in datas


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
    assert "AACC-${release_version}-Setup.exe" in script
    assert "AACC-${release_version}-Setup.exe.sha256" in script
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
        assert "shasum -a 256 AACC-1.4.2.dmg" in content
        assert "xattr -cr /Applications/AACC.app" in content


def test_windows_spec_bundles_edge_cdp_transport_without_qt_webview() -> None:
    text = (ROOT / "AACC-windows.spec").read_text(encoding="utf-8")
    assert "'websocket'" in text
    assert "PySide6.QtWebView" not in text
    assert "QtWebEngine" not in text


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
        assert "Native DACL" in text or "原生 DACL" in text
        assert "icacls" not in text
        assert "unprivileged" in text or "无特权" in text
