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
    assert "MinVersion=10.0.17763" in setup
    assert "CloseApplications=no" in setup
    assert "RestartApplications=no" in setup
    assert "UninstallFilesDir={app}\\uninstall" in setup


def test_inno_setup_packages_only_the_reviewed_onedir_roots() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    files = _section(text, "Files")

    assert 'Source: "..\\dist\\AACC\\AACC.exe"; DestDir: "{app}"' in files
    assert 'Source: "..\\dist\\AACC\\aacc-spawn.exe"; DestDir: "{app}"' in files
    assert 'Source: "..\\dist\\AACC\\_internal\\*"; DestDir: "{app}\\_internal"' in files
    assert 'Source: "..\\build\\installer\\internal-manifest-v1.txt"' in files
    assert 'Source: "shutdown-v1.capability"' in files
    assert len(re.findall(r"(?m)^Source:", files)) == 5
    internal = files.index('Source: "..\\dist\\AACC\\_internal\\*"')
    aacc = files.index('Source: "..\\dist\\AACC\\AACC.exe"')
    broker = files.index('Source: "..\\dist\\AACC\\aacc-spawn.exe"')
    assert internal < aacc < broker
    assert "[InstallDelete]" not in text


def test_inno_setup_uses_only_the_graceful_aacc_shutdown_command() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")
    lowered = text.lower()

    assert "--shutdown-for-update" in code
    assert "PrepareToInstall" in code
    assert "InitializeUninstall" in code
    assert "SuppressibleMsgBox(" in code
    assert re.search(r"(?<!Suppressible)MsgBox\(", code) is None
    assert code.count("FindWindowByWindowName(AACCWindowTitle)") >= 2
    assert "if AACCWindow = 0 then" in code
    assert "(FindWindowByWindowName(AACCWindowTitle) <> 0) then" in code
    assert "ShutdownCapabilityName = 'shutdown-v1.capability'" in code
    assert "if not FileExists(CapabilityPath) then" in code
    assert "ShutdownControlTimeoutMilliseconds = 25000" in code
    assert "CreateProcessW@kernel32.dll" in code
    assert "WaitForSingleObject(" in code
    assert "WaitResult = WAIT_TIMEOUT" in code
    assert "TerminateProcess(ProcessInfo.hProcess, 124)" in code
    assert "if not TerminateProcess(ProcessInfo.hProcess, 124) then" in code
    assert "WaitForSingleObject(ProcessInfo.hProcess, 5000) <> WAIT_OBJECT_0" in code
    assert "newly created control invocation" in code
    assert "never a handle to the existing main AACC process" in code
    assert "OpenProcess(" not in code
    assert "ewWaitUntilTerminated" not in code
    assert "Exec(" not in code
    assert "ShellExec(" not in code
    assert "ResultCode <> 0" in code
    assert "FileExists(AACCPath)" in code
    assert "taskkill" not in lowered
    assert "stop-process" not in lowered
    assert "wm_close" not in lowered
    assert "forcecloseapplications" not in lowered
    assert "bInheritHandles: BOOL" in code
    assert "pinned Inno Setup 6.7.1" in code


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


def test_inno_setup_cleans_only_manifest_extras_after_commit() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")

    assert "internal-manifest-v1.txt" in code
    assert "LoadStringsFromFile" in code
    assert "CleanupInternalExtras" in code
    assert "CurStep = ssPostInstall" in code
    assert "DeleteFile(" in code
    assert "RemoveDir(" in code
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in code
    reparse_branch = code.split(
        "if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then", 1
    )[1].split("else if (FindRec.Attributes and faDirectory) <> 0 then", 1)[0]
    assert "RemoveDir(FullPath)" in reparse_branch
    assert "DeleteFile(FullPath)" in reparse_branch
    assert "DelTree(" not in reparse_branch
    assert "CleanupInternalExtras(" not in reparse_branch
    assert "Log(" in code
    assert "RaiseException" not in code


def test_windows_smoke_closes_uninstaller_clone_capture_race() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    function = text.split("function Wait-UninstallerTreeGone", 1)[1].split(
        "function Invoke-Uninstaller", 1
    )[0]

    initial = function.index("$InitialSnapshot = @(Get-OwnedProcessTree")
    polling = function.index("while (-not $Process.HasExited")
    final = function.index("$FinalSnapshot = @(Get-OwnedProcessTree")
    waiting = function.index("Assert-ProcessExitedByDeadline")
    assert initial < polling < final < waiting
    assert "ParentProcessId" in function
    assert "Register-OwnedIdentity -Identity $Identity" in function


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
    assert 'Get-ChildItem -LiteralPath $InnoRoot -Filter "ISCC.exe" -File -Recurse' in text
    assert "$IsccCandidates.Count -ne 1" in text
    assert 'Join-Path $InnoRoot "ISCC.exe"' not in text
    assert "AACC_INNO_LAYOUT candidate_count=" in text
    assert "AACC_INNO_DEFAULT_DESKTOP candidate_count=" in text
    assert "$IsccCandidates = $DefaultDesktopCandidates" not in text


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
    assert "internal-manifest-v1.txt" in text
    assert '"D $Relative/"' in text
    assert '"F $Relative"' in text
    assert "[System.Text.UTF8Encoding]::new($false)" in text
    assert "WriteAllText" in text
    assert "ReparsePoint" in text
    assert "Start-Process" in text
    assert "WaitForExit" in text
    assert "Inno Setup bootstrap timed out" in text


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
