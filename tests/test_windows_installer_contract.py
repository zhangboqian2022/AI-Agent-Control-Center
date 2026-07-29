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
    assert "MicrosoftEdgeWebview2Setup.exe" not in files
    assert len(re.findall(r"(?m)^Source:", files)) == 6
    internal = files.index('Source: "..\\dist\\AACC\\_internal\\*"')
    aacc = files.index('Source: "..\\dist\\AACC\\AACC.exe"')
    broker = files.index('Source: "..\\dist\\AACC\\aacc-spawn.exe"')
    manifest_probe = files.index('DestName: "aacc-preflight-manifest-v1.txt"; Flags: dontcopy')
    manifest_install = files.rindex('Source: "..\\build\\installer\\internal-manifest-v1.txt"')
    assert manifest_probe < aacc < broker < internal < manifest_install
    assert "[InstallDelete]" not in text


def test_inno_setup_uses_only_the_graceful_aacc_shutdown_command() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")
    shutdown_control = code.split("function RunManagedShutdownControl", 1)[1].split(
        "function ManifestPath", 1
    )[0]
    shutdown_existing = code.split("function ShutdownExistingAACC", 1)[1].split(
        "function ValidateInternalRootForInstall", 1
    )[0]
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
    assert "WaitResult = AACC_WAIT_TIMEOUT" in code
    assert "TerminateProcess(ProcessInfo.hProcess, 124)" in code
    assert "if not TerminateProcess(ProcessInfo.hProcess, 124) then" in code
    assert "WaitForSingleObject(ProcessInfo.hProcess, 5000) <> AACC_WAIT_OBJECT_0" in code
    assert "newly created control invocation" in code
    assert "never a handle to the existing main AACC process" in code
    assert "OpenProcess(" not in code
    assert "ewWaitUntilTerminated" not in shutdown_control
    assert "Exec(" not in shutdown_control
    assert "ShellExec(" not in shutdown_control
    assert "Exec(" not in shutdown_existing
    assert "ShellExec(" not in shutdown_existing
    assert "ResultCode <> 0" in code
    assert "FileExists(AACCPath)" in code
    assert "taskkill" not in lowered
    assert "stop-process" not in lowered
    assert "wm_close" not in lowered
    assert "forcecloseapplications" not in lowered
    assert "bInheritHandles: BOOL" in code
    assert "pinned Inno Setup 6.7.1" in code


def test_inno_setup_rejects_internal_root_reparse_before_install() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")
    prepare = code.split("function PrepareToInstall", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]

    assert "GetFileAttributesW@kernel32.dll" in code
    assert "INVALID_FILE_ATTRIBUTES" in code
    assert "ValidateInternalRootForInstall" in code
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in code
    assert "FILE_ATTRIBUTE_DIRECTORY =" not in code
    assert "FILE_ATTRIBUTE_REPARSE_POINT =" not in code
    assert "faDirectory" not in code
    assert "internal payload root is unsafe" in code
    assert prepare.index("ValidateInternalRootForInstall") < prepare.index("ShutdownExistingAACC")


def test_inno_setup_preflights_every_packaged_replacement_before_writing() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")
    prepare = code.split("function PrepareToInstall", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]

    for required in (
        "CreateFileW@kernel32.dll",
        "GENERIC_READ",
        "GENERIC_WRITE",
        "DELETE_ACCESS",
        "OPEN_EXISTING",
        "FILE_FLAG_BACKUP_SEMANTICS",
        "ValidatePackagedTargetsForInstall",
        "ValidateInternalManifestTargets",
        "ValidateExistingTargetDirectory",
        "ValidateExistingUninstallerTargets",
        "for Index := 0 to 999 do",
        "Format('unins%.3d', [Index])",
        "BaseName + '.exe'",
        "BaseName + '.dat'",
        "ExpandConstant('{autoprograms}\\AACC.lnk')",
        "ExpandConstant('{autodesktop}\\AACC.lnk')",
        "ExtractTemporaryFile('aacc-preflight-manifest-v1.txt')",
        "ValidateInternalManifest",
        "installed-manifest-unavailable",
        "installed-manifest-invalid",
        "AACC_PREFLIGHT result=completed",
        "AACC_PREFLIGHT result=target-unavailable",
    ):
        assert required in code
    for inno_or_win32_name in (
        "WAIT_OBJECT_0",
        "WAIT_TIMEOUT",
        "STARTF_USESHOWWINDOW",
        "INVALID_FILE_ATTRIBUTES",
        "INVALID_HANDLE_VALUE",
        "ERROR_FILE_NOT_FOUND",
        "ERROR_PATH_NOT_FOUND",
        "GENERIC_READ",
        "GENERIC_WRITE",
        "DELETE_ACCESS",
        "OPEN_EXISTING",
        "FILE_ATTRIBUTE_NORMAL",
        "FILE_FLAG_BACKUP_SEMANTICS",
    ):
        assert not re.search(rf"(?m)^\s{{2}}{inno_or_win32_name}\s*=", code), (
            f"custom constant must be AACC-prefixed: {inno_or_win32_name}"
        )
    assert "ShutdownExistingAACC(ErrorMessage)" in prepare
    assert "ValidatePackagedTargetsForInstall(ErrorMessage)" in prepare
    assert prepare.index("ShutdownExistingAACC(ErrorMessage)") < prepare.index(
        "ValidatePackagedTargetsForInstall(ErrorMessage)"
    )
    target_preflight = code.split("function ValidateInternalTargetParents", 1)[1].split(
        "function DeleteFileWithRetries", 1
    )[0]
    assert "FindFirst(" not in target_preflight
    assert "FindNext(" not in target_preflight
    assert not re.search(r"(?m)^\s*#\d", code)


def test_windows_smoke_accepts_empty_process_argument_arrays() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    for function_name in ("New-ProcessStartInfo", "Start-OwnedProcess"):
        function = text.split(f"function {function_name}", 1)[1].split("\n}", 1)[0]
        assert "[AllowEmptyCollection()][string[]]$Arguments" in function


def test_windows_smoke_reads_executable_path_from_the_owned_process_handle() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    function = text.split("function Get-ProcessIdentity", 1)[1].split("\n}", 1)[0]

    assert "Get-ProcessImagePath -Process $Process" in function
    assert "Path = $Process.Path" not in function
    assert "$Process.MainModule.FileName" not in function
    assert "QueryFullProcessImageNameW" in text
    assert "GetTickCount64" in text
    assert "$Process.Handle" in text
    assert "[Runtime.InteropServices.Marshal]::GetLastWin32Error()" in text
    assert "[Environment]::TickCount64" not in text
    assert "[AaccSmokeNativeProcess]::GetTickCount64()" in text


def test_windows_smoke_fixtures_use_only_command_line_unicode_definitions() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    assert "/DUNICODE /D_UNICODE" in script
    for relative_path in (
        "tests/windows/fake_legacy_aacc.cpp",
        "tests/windows/lock_payload.cpp",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "#define UNICODE" not in source
        assert "#define _UNICODE" not in source


def test_windows_smoke_junction_target_avoids_provider_wildcards() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    assert '"product-smoke\\junction-target"' in script
    assert '"product-smoke\\junction target $SpecialLeaf"' not in script


def test_windows_smoke_restores_terminated_fixture_with_a_bounded_retry() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    helper = script.split("function Restore-LiteralFileAfterProcessExit", 1)[1].split("\n}", 1)[0]
    assert "Test-ProcessIdentityAlive -Identity $Identity" in helper
    assert "[System.IO.File]::Copy($Source, $Destination, $true)" in helper
    assert "catch [System.IO.IOException]" in helper
    assert "$Win32Code -notin @(32, 33)" in helper
    assert "[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)" in helper
    assert "Start-Sleep -Milliseconds 100" in helper
    assert "Get-FileHash -LiteralPath $Source -Algorithm SHA256" in helper
    assert "Get-FileHash -LiteralPath $Destination -Algorithm SHA256" in helper
    assert "$DestinationHash -ceq $SourceHash" in helper

    refusal = script.split("function Test-InstalledControlRefusal", 1)[1].split(
        "function Compile-WindowsFixtures", 1
    )[0]
    stopped = refusal.index("Assert-ProcessExitedByDeadline -Identity $Legacy.Identity")
    restore = refusal.index("Restore-LiteralFileAfterProcessExit")
    assert stopped < restore
    assert "-Identity $Legacy.Identity" in refusal[restore:]


def test_windows_legacy_fixture_uses_a_harness_only_graceful_stop_event() -> None:
    script = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    fixture = (ROOT / "tests" / "windows" / "fake_legacy_aacc.cpp").read_text(encoding="utf-8")

    assert "AACC_LEGACY_STOP_EVENT" in fixture
    assert "AACC_LEGACY_LIFECYCLE_FILE" in fixture
    assert "OpenEventW(SYNCHRONIZE, FALSE" in fixture
    assert "MsgWaitForMultipleObjects(" in fixture
    for stage in (
        "entry",
        "stop-event-name-unavailable",
        "stop-env-read",
        "stop-event-open-failed",
        "stop-event-open-ok",
        "ready",
        "wait-failed",
        "stop-signaled",
        "message-quit",
        "exit-zero",
    ):
        assert f'L"{stage}"' in fixture
    assert "GetCurrentThreadId()" in fixture
    assert "GetTickCount64()" in fixture
    assert '\\"image_path\\":\\"' in fixture
    assert '\\"creation_time\\":' in fixture
    assert "WM_CLOSE" in fixture
    assert fixture.index("--shutdown-for-update") < fixture.index("OpenEventW(SYNCHRONIZE, FALSE")

    refusal = script.split("function Test-InstalledControlRefusal", 1)[1].split(
        "function Compile-WindowsFixtures", 1
    )[0]
    assert "[System.Threading.EventWaitHandle]::new(" in refusal
    assert '"Local\\AACC.LegacySmokeStop."' in refusal
    assert "[ref]$CreatedNew" in refusal
    assert "Assert-True $CreatedNew" in refusal
    assert "$env:AACC_LEGACY_STOP_EVENT = $StopEventName" in refusal
    assert "$env:AACC_LEGACY_LIFECYCLE_FILE = $LifecycleEvidence" in refusal
    set_environment = refusal.index("$env:AACC_LEGACY_STOP_EVENT = $StopEventName")
    start = refusal.index("$Legacy = Start-OwnedProcess")
    clear_environment = refusal.index("Remove-Item Env:AACC_LEGACY_STOP_EVENT")
    ready = refusal.index('"legacy fixture did not report its stop-event readiness"')
    invoke_setup = refusal.index("Invoke-Setup")
    assert set_environment < start < clear_environment < ready < invoke_setup
    assert "[DateTime]::FromFileTimeUtc(" in refusal
    assert "Test-ProcessIdentityExactMatch -ExpectedIdentity $Legacy.Identity" in refusal
    assert '"ready-identity-compare.json"' in refusal
    ready_compare = refusal.index('"ready-identity-compare.json"')
    ready_assert = refusal.index(
        '"legacy fixture ready identity differs from its captured process"'
    )
    assert ready_compare < ready_assert
    assert '"post-refusal-identity.json"' in refusal
    post_refusal = refusal.index('"post-refusal-identity.json"')
    refusal_alive = refusal.index('"$Action refusal terminated the legacy main process"')
    assert invoke_setup < post_refusal < refusal_alive
    signal = refusal.index("$StopEvent.Set()")
    exited = refusal.index("Assert-ProcessExitedByDeadline -Identity $Legacy.Identity")
    exit_code = refusal.index("$Legacy.Process.ExitCode -eq 0")
    restore = refusal.index("Restore-LiteralFileAfterProcessExit")
    assert signal < exited < exit_code < restore
    assert "$StopEvent.Dispose()" in refusal
    assert "Stop-OwnedProcessIdentity -Identity $Legacy.Identity" not in refusal


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
    assert "Check: InternalCleanupSucceeded" in run


def test_inno_setup_cleans_only_manifest_extras_after_commit() -> None:
    text = (ROOT / "installer" / "AACC.iss").read_text(encoding="utf-8")
    code = _section(text, "Code")

    assert "internal-manifest-v1.txt" in code
    assert "LoadStringsFromFile" in code
    assert "ManifestFilePath: String" in code
    assert "LoadStringsFromFile(ManifestFilePath, Manifest)" in code
    assert "CleanupInternalExtras" in code
    assert "CurStep = ssPostInstall" in code
    assert "DeleteFile(" in code
    assert "RemoveDir(" in code
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in code
    cleanup = code.split("function CleanupInternalExtras", 1)[1].split(
        "function CleanupCommittedInternalPayload", 1
    )[0]
    reparse_branch = cleanup.split(
        "if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then", 1
    )[1].split("else if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then", 1)[0]
    assert "RemoveDirWithRetries(FullPath)" in reparse_branch
    assert "DeleteFileWithRetries(FullPath)" in reparse_branch
    assert "DelTree(" not in reparse_branch
    assert "CleanupInternalExtras(" not in reparse_branch
    assert "CleanupRetryCount = 3" in code
    assert "CleanupRetryDelayMilliseconds" in code
    assert "DeleteFileWithRetries" in code
    assert "RemoveDirWithRetries" in code
    assert "ValidateInternalManifest" in code
    assert "CleanupInternalExtras" in code
    assert "if not CleanupCommittedInternalPayload then" in code
    assert "InternalCleanupIncomplete := True" in code
    assert "function GetCustomSetupExitCode: Integer" in code
    assert "function InternalCleanupSucceeded: Boolean" in code
    assert "InternalCleanupFailureExitCode = 9" in code
    assert "Result := InternalCleanupFailureExitCode" in code
    assert "RaiseException(" not in code
    assert "result=incomplete" in code


def test_windows_smoke_discovers_only_from_captured_exact_live_parents() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    function = text.split("function Wait-UninstallerTreeGone", 1)[1].split(
        "function Invoke-Uninstaller", 1
    )[0]

    initial = function.index("$InitialSnapshot = @(")
    polling = function.index("while (-not $Process.HasExited")
    waiting = function.index("$TreeDeadline = [DateTime]::UtcNow.AddSeconds(30)")
    assert initial < polling < waiting
    assert "Update-OwnedProcessForest -Identities $Identities" in function
    assert "$FinalSnapshot" not in function
    assert "parent-exit clone race" not in function

    tree = text.split("function Get-OwnedProcessTree", 1)[1].split(
        "function Stop-OwnedProcessTree", 1
    )[0]
    assert "ParentProcessId" in tree
    assert "if (-not (Test-ProcessIdentityExactAlive -Identity $ExpectedRootIdentity))" in tree
    assert tree.count("if (-not (Test-ProcessIdentityExactAlive -Identity $ParentIdentity))") >= 4
    assert "continue" in tree
    assert "break" in tree
    assert "Get-CimProcessRecordById -Id $ChildIdentity.Id" in tree
    assert "Test-CimChildRecordBound" in tree
    assert "-SnapshotRecord $ChildRecord" in tree
    assert "-FreshRecord $FreshChildRecord" in tree
    assert "Test-OwnedProcessEdge -ParentIdentity" in tree
    assert "$Pending.Push($ChildIdentity)" in tree
    assert "CreationTimeUtc -ge $ParentIdentity.CreationTimeUtc" in text
    assert "A verified parent may exit before its descendants" not in tree

    forest = text.split("function Update-OwnedProcessForest", 1)[1].split(
        "function Assert-StaleParentPidEdgeRejected", 1
    )[0]
    assert "$Parents = @($Identities.Values)" in forest
    assert "-RootId $ParentIdentity.Id" in forest
    assert "-ExpectedRootIdentity $ParentIdentity" in forest
    assert "Register-OwnedIdentity -Identity $Identity" in forest


def test_windows_smoke_rejects_later_child_after_parent_pid_reuse_and_exit() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")

    assert "function Assert-ReusedParentExitSequenceRejected" in text
    assert "reused-parent-exit-sequence.json" in text
    assert "p1-exited" in text
    assert "pid-reused-by-p2" in text
    assert "p2-spawned-later-c" in text
    assert "p2-exited" in text
    assert "acceptedIdentities = $AcceptedIdentities" in text
    assert "temporaryClones = $TemporaryClones" in text
    assert "later child of a reused parent PID entered the owned tree" in text


def test_windows_smoke_binds_snapshot_child_to_fresh_cim_identity() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    binding = text.split("function Test-CimChildRecordBound", 1)[1].split(
        "function Get-OwnedProcessTree", 1
    )[0]

    assert "Convert-CimCreationDateToUtcTicks" in text
    assert "Get-CimProcessRecordById" in text
    assert "ProcessId" in binding
    assert "ParentProcessId" in binding
    assert "ExecutablePath" in binding
    assert "CreationDate" in binding
    assert "SnapshotCreationTicks" in binding
    assert "FreshCreationTicks" in binding
    assert "CreationTimeUtc" in binding
    assert "CreationTickTolerance = 10" in text
    assert "function Assert-ChildPidReuseSequenceRejected" in text
    assert "child-pid-reuse-sequence.json" in text
    assert "snapshot-child-a" in text
    assert "pid-reused-by-unrelated-b" in text
    assert "acceptedIdentities = $AcceptedIdentities" in text
    assert "temporaryClones = $TemporaryClones" in text
    assert "reused child PID entered the owned tree" in text


def test_windows_smoke_keeps_temp_clone_filter_structurally_closed() -> None:
    text = (ROOT / "scripts" / "test_windows_package.ps1").read_text(encoding="utf-8")
    function = text.split("function Wait-UninstallerTreeGone", 1)[1].split(
        "function Invoke-Uninstaller", 1
    )[0]

    assert (
        """[System.StringComparison]::OrdinalIgnoreCase
                    )
                }
        )
        Write-SmokeEvidence"""
        in function
    )
    assert function.index("Write-SmokeEvidence") < function.index(
        "function Assert-DiagnosticsTreeHasNoPrimaryArtifacts"
    )


def test_windows_installer_build_pins_and_authenticates_iscc() -> None:
    text = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")

    assert "6.7.1" in text
    assert "4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0" in text
    assert "AACC_ISCC_PATH" not in text
    assert "innosetup-6.7.1.exe" in text
    assert (
        "https://github.com/jrsoftware/issrc/releases/download/is-6_7_1/innosetup-6.7.1.exe"
    ) in text
    assert "Get-AuthenticodeSignature -LiteralPath $Path" in text
    assert "Get-AuthenticodeSignature -FilePath" not in text
    for zero_version_part in (
        "$VersionInfo.FileMajorPart -ne 0",
        "$VersionInfo.FileMinorPart -ne 0",
        "$VersionInfo.FileBuildPart -ne 0",
        "$VersionInfo.FilePrivatePart -ne 0",
        "$VersionInfo.ProductMajorPart -ne 0",
        "$VersionInfo.ProductMinorPart -ne 0",
        "$VersionInfo.ProductBuildPart -ne 0",
        "$VersionInfo.ProductPrivatePart -ne 0",
    ):
        assert zero_version_part in text
    assert "Compiler engine version: Inno Setup $InnoVersion" in text
    assert '"/O-"' in text
    assert "ISCC version probe failed" in text
    assert "[Guid]::NewGuid().ToString(" in text
    assert "iscc-version-probe-" in text
    assert "inno-$InnoVersion-" in text
    assert "$ProbeRootOwned = $false" in text
    assert "$InnoRootOwned = $false" in text
    assert "$InnoRootOwned -and (Test-Path -LiteralPath $InnoRoot)" in text
    assert "-LiteralPath $InnoRoot" in text
    assert "AACC_INNO_CLEANUP" in text
    assert "cleanup_failed=true" in text
    assert "if (Test-Path -LiteralPath $InnoRoot)" in text
    assert "Get-Command ISCC.exe" not in text
    assert "/PORTABLE=1" in text
    assert "/CURRENTUSER" in text
    assert 'Get-ChildItem -LiteralPath $InnoRoot -Filter "ISCC.exe" -File -Recurse' in text
    assert "$IsccCandidates.Count -ne 1" in text
    assert 'Join-Path $InnoRoot "ISCC.exe"' not in text
    assert "AACC_INNO_LAYOUT candidate_count=" in text
    assert "AACC_INNO_DEFAULT_DESKTOP" not in text


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
