import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\s*$\n(.*?)(?=^\[[^\]]+\]\s*$|\Z)",
        text,
    )
    assert match is not None, name
    return match.group(1)


def test_inno_setup_is_per_user_and_upgrade_stable() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    setup = _section(text, "Setup")
    setup_lines = set(setup.splitlines())

    assert "AppId={{C174E242-E193-5863-8A46-F16152875173}" in setup
    assert "PrivilegesRequired=lowest" in setup_lines
    assert "PrivilegesRequiredOverridesAllowed=" in setup_lines
    assert "VersionInfoVersion={#MyAppVersion}" in setup
    assert "VersionInfoProductVersion={#MyAppVersion}" in setup
    assert "DefaultDirName={localappdata}\\Programs\\AACC" in setup
    assert "UsePreviousAppDir=yes" in setup
    assert "UninstallLogMode=append" in setup
    assert "ArchitecturesAllowed=x64compatible" in setup
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in setup
    assert "CloseApplications=no" in setup
    assert "RestartApplications=no" in setup
    assert "UninstallFilesDir={app}\\uninstall" in setup


def test_inno_setup_packages_only_the_reviewed_onedir_roots() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    files = _section(text, "Files")
    install_delete = _section(text, "InstallDelete")

    assert 'Source: "..\\dist\\AACC\\AACC.exe"; DestDir: "{app}"' in files
    assert 'Source: "..\\dist\\AACC\\aacc-spawn.exe"; DestDir: "{app}"' in files
    assert 'Source: "..\\dist\\AACC\\_internal\\*"; DestDir: "{app}\\_internal"' in files
    assert len(re.findall(r"(?m)^Source:", files)) == 3
    assert 'Name: "{app}\\_internal"' in install_delete
    assert 'Name: "{app}\\*"' not in install_delete
    assert 'Name: "{app}"' not in install_delete


def test_inno_setup_uses_only_the_graceful_aacc_shutdown_command() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")
    lowered = text.lower()

    assert "--shutdown-for-update" in code
    assert "PrepareToInstall" in code
    assert "InitializeUninstall" in code
    assert "SuppressibleMsgBox(" in code
    assert re.search(r"(?<!Suppressible)MsgBox\(", code) is None
    assert "ewWaitUntilTerminated" in code
    assert code.count("Exec(") == 1
    assert "ShellExec(" not in code
    assert "ResultCode <> 0" in code
    assert "FileExists(ExpandConstant('{app}\\AACC.exe'))" in code
    assert "taskkill" not in lowered
    assert "terminateprocess" not in lowered
    assert "stop-process" not in lowered
    assert "wm_close" not in lowered
    assert "forcecloseapplications" not in lowered


def test_inno_setup_preserves_user_data_and_offers_expected_shortcuts() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    lowered = text.lower()
    tasks = _section(text, "Tasks")
    icons = _section(text, "Icons")
    run = _section(text, "Run")

    for sensitive_name in (
        "{userappdata}\\aacc",
        "config.yaml",
        "aacc.db",
        "kimi-credentials.json",
    ):
        assert sensitive_name not in lowered
    assert "[UninstallDelete]" not in text
    assert 'Name: "desktopicon"' in tasks
    assert "unchecked" in tasks
    assert "{autoprograms}\\AACC" in icons
    assert "{autodesktop}\\AACC" in icons
    assert "postinstall" in run
    assert "nowait" in run
    assert "skipifsilent" in run


def test_windows_installer_build_pins_and_authenticates_iscc() -> None:
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")

    assert "6.7.1" in text
    assert "4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0" in text
    assert "AACC_ISCC_PATH" in text
    assert "innosetup-6.7.1.exe" in text
    assert (
        "https://github.com/jrsoftware/issrc/releases/download/is-6_7_1/innosetup-6.7.1.exe"
    ) in text
    assert "Get-AuthenticodeSignature -LiteralPath $Path" in text
    assert "Get-AuthenticodeSignature -FilePath" not in text
    for exact_version_part in (
        "$VersionInfo.FileMajorPart -ne 6",
        "$VersionInfo.FileMinorPart -ne 7",
        "$VersionInfo.FileBuildPart -ne 1",
        "$VersionInfo.FilePrivatePart -ne 0",
        "$VersionInfo.ProductMajorPart -ne 6",
        "$VersionInfo.ProductMinorPart -ne 7",
        "$VersionInfo.ProductBuildPart -ne 1",
        "$VersionInfo.ProductPrivatePart -ne 0",
    ):
        assert exact_version_part in text
    assert "Get-Command ISCC.exe" not in text
    assert "/PORTABLE=1" in text
    assert "/CURRENTUSER" in text


def test_windows_installer_build_validates_inputs_and_fresh_output() -> None:
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")

    assert "uv version --short" in text
    assert "'^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'" in text
    assert "Compare-Object" in text
    assert "AACC.exe" in text
    assert "aacc-spawn.exe" in text
    assert "_internal" in text
    assert "Remove-Item -LiteralPath $ExpectedSetupPath" in text
    assert "Remove-Item -LiteralPath $ChecksumPath" in text
    assert "Resolve-Path -LiteralPath" in text
    assert "Get-Item -LiteralPath" in text
    assert "Invoke-Expression" not in text
    assert "$LASTEXITCODE" in text
    assert '"/DMyAppVersion=$Version"' in text
    assert "$IssPath" in text
    assert "Length -lt" in text


def test_windows_installer_checksum_is_strict_and_bom_free() -> None:
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")

    assert "Get-FileHash" in text
    assert "ToLowerInvariant()" in text
    assert "[System.Text.UTF8Encoding]::new($false)" in text
    assert '"$Digest  $SetupLeaf`n"' in text
    assert "ReadAllBytes" in text
    assert "0xEF" in text
    assert "0xBB" in text
    assert "0xBF" in text
    assert "[0-9a-f]{64}" in text
