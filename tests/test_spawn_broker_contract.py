import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_windows_build_compiles_static_spawn_broker() -> None:
    script = (ROOT / "scripts" / "build_spawn_broker.ps1").read_text(encoding="utf-8")

    for compiler_flag in ("/std:c++17", "/O2", "/MT", "/GS", "/guard:cf", "/W4", "/WX"):
        assert compiler_flag in script
    for linker_flag in ("/DYNAMICBASE", "/NXCOMPAT", "/HIGHENTROPYVA"):
        assert linker_flag in script
    assert "vswhere.exe" in script
    assert "-prerelease -latest -products *" in script
    assert "-requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64" in script
    assert "-property installationPath" in script
    assert '-version "[17.0,18.0)"' not in script
    assert "Visual Studio 2022" not in script
    assert "uv version --short" in script
    assert "dumpbin" in script.lower()
    for forbidden_dependency in ("VCRUNTIME", "MSVCP", "ucrtbase", "Python", "Qt"):
        assert forbidden_dependency in script


def test_broker_build_logs_safe_vswhere_diagnostics_on_discovery_failure() -> None:
    script = (ROOT / "scripts" / "build_spawn_broker.ps1").read_text(encoding="utf-8")

    assert 'Write-Host "AACC_VS_DISCOVERY_DIAGNOSTICS"' in script
    assert '@("installationPath", "installationVersion", "productId")' in script
    assert "& $VsWhere -all -prerelease -products * -property $Property" in script
    assert '$VisualStudioRoot = "C:\\Program Files\\Microsoft Visual Studio"' in script
    assert "Get-ChildItem -LiteralPath $VisualStudioRoot -Directory" in script
    assert "Get-ChildItem -LiteralPath $Directory.FullName -Directory" in script
    failure_start = script.index(
        "if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($InstallationPath))"
    )
    failure_end = script.index("$VsDevCmd", failure_start)
    assert "Write-VisualStudioDiscoveryDiagnostics" in script[failure_start:failure_end]


def test_broker_dependency_check_is_an_explicit_allowlist() -> None:
    script = (ROOT / "scripts" / "build_spawn_broker.ps1").read_text(encoding="utf-8")

    match = re.search(
        r"\$AllowedDependencies\s*=\s*@\((?P<dependencies>[^)]*)\)",
        script,
        flags=re.MULTILINE,
    )
    assert match is not None
    assert re.findall(r'["\']([^"\']+\.dll)["\']', match.group("dependencies")) == ["KERNEL32.dll"]
    assert "unexpected broker dependency" in script
    assert "dependency section was not found" in script
    assert "Image has the following (?:delay load )?dependencies:" in script
    assert "$ParsedDependencies += $Candidate" in script
    assert "A-Za-z0-9._-" not in script


def test_broker_source_is_fixed_to_codex_app_server() -> None:
    source = (ROOT / "native" / "aacc_spawn" / "aacc_spawn.cpp").read_text(encoding="utf-8")

    assert 'L"app-server"' in source
    assert 'L"--stdio"' in source
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in source
    assert "CREATE_SUSPENDED" in source
    assert "PROC_THREAD_ATTRIBUTE_HANDLE_LIST" in source
    assert "AssignProcessToJobObject" in source
    assert "SetDllDirectoryW(nullptr)" in source
    assert "GetSystemDirectoryW" in source
    assert "AACC_BROKER_CODEX_TARGET" in source
    assert 'L" /D /V:OFF /S /C' in source
    assert "taskkill" not in source.lower()


def test_broker_rejects_unsafe_windows_path_syntax_before_filesystem_io() -> None:
    source = (ROOT / "native" / "aacc_spawn" / "aacc_spawn.cpp").read_text(encoding="utf-8")

    assert "HasSafeBrokerPathSyntax" in source
    assert "IsExtendedOrDevicePath" in source
    assert "character >= 0x0001 && character <= 0x001F" in source
    assert "index != 1" in source
    assert "!HasSafeBrokerPathSyntax(options.bundle_dir)" in source
    assert "!HasSafeBrokerPathSyntax(options.codex_path)" in source

    validation = source.index("bool ValidateOptions")
    syntax_check = source.index("HasSafeBrokerPathSyntax", validation)
    filesystem_check = source.index("GetFileAttributesW", validation)
    assert syntax_check < filesystem_check


def test_broker_source_has_fixed_sanitized_diagnostics_and_stages() -> None:
    source = (ROOT / "native" / "aacc_spawn" / "aacc_spawn.cpp").read_text(encoding="utf-8")

    assert 'L"AACC_BROKER_ERROR stage=%d win32=%lu\\n"' in source
    assert "AACC_BROKER_PROTOCOL=1" not in source
    for stage in (10, 11, 12, 20, 21, 22, 23, 24, 25):
        assert f"Fail({stage}," in source


def test_broker_resource_template_tracks_product_and_protocol() -> None:
    resource = (ROOT / "native" / "aacc_spawn" / "aacc_spawn.rc.in").read_text(encoding="utf-8")

    assert "@VERSION_COMMA@" in resource
    assert "@VERSION@" in resource
    assert '"ProtocolVersion", "1"' in resource
    assert '"OriginalFilename", "aacc-spawn.exe"' in resource


def test_windows_integration_covers_real_script_executable_and_job_cleanup() -> None:
    script = (ROOT / "scripts" / "build_spawn_broker.ps1").read_text(encoding="utf-8")

    for fixture in (
        "fake_codex.cmd",
        "fake_codex_server.py",
        "fake_codex_server.cpp",
        "spawn_descendant.py",
    ):
        assert fixture in script
    assert "[char]0x4E34" in script
    assert "[char]0x65F6" in script
    assert '" AACC &(broker)"' in script
    assert "'%AACC_UNSET%!literal!'" in script
    assert '"AACC_UNSET"] = "SHOULD_NOT_EXPAND"' in script
    assert re.search(r"1\.\.20|1\.\.`?20", script)
    assert "Stop-Process" in script
    assert "descendant" in script.lower()
    assert "ReadToEndAsync" in script


def test_windows_integration_rejects_c0_ads_and_extended_paths_without_side_effects() -> None:
    script = (ROOT / "scripts" / "build_spawn_broker.ps1").read_text(encoding="utf-8")
    fixture = (ROOT / "tests" / "native" / "fake_codex_server.cpp").read_text(encoding="utf-8")

    assert "--prepare-unsafe-path-fixtures" in script
    assert "--prepare-unsafe-path-fixtures" in fixture
    assert "foreach ($ControlCode in 1..31)" in script
    assert "${UnsafeCarrier}:payload.cmd" in script
    assert '"\\\\?\\$UnsafeCarrier"' in script
    assert "unsafe-path-sentinel.txt" in script
    assert "Assert-BrokerRejectsUnsafeTarget" in script
    assert "Test-Path -LiteralPath $SentinelPath" in script
    assert "CreateFileW" in fixture


@pytest.mark.skipif(sys.platform != "win32", reason="requires MSVC and Windows Job Objects")
def test_spawn_broker_windows_integration() -> None:
    import subprocess

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "build_spawn_broker.ps1"),
        ],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0
