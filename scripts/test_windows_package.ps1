#requires -Version 5.1
param([switch]$FrozenOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$OwnedProcessRegistry = New-Object System.Collections.ArrayList

function Assert-True {
    param([Parameter(Mandatory = $true)][bool]$Condition, [string]$Message = "assertion failed")
    if (-not $Condition) { throw $Message }
}

function ConvertTo-ProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $Builder = New-Object System.Text.StringBuilder
    [void]$Builder.Append('"')
    $Backslashes = 0
    foreach ($Character in $Value.ToCharArray()) {
        if ($Character -eq '\') {
            $Backslashes += 1
            continue
        }
        if ($Character -eq '"') {
            [void]$Builder.Append(('\' * (($Backslashes * 2) + 1)))
            [void]$Builder.Append('"')
            $Backslashes = 0
            continue
        }
        if ($Backslashes -gt 0) {
            [void]$Builder.Append(('\' * $Backslashes))
            $Backslashes = 0
        }
        [void]$Builder.Append($Character)
    }
    if ($Backslashes -gt 0) {
        [void]$Builder.Append(('\' * ($Backslashes * 2)))
    }
    [void]$Builder.Append('"')
    return $Builder.ToString()
}

function New-ProcessStartInfo {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    $Info.FileName = $FilePath
    $Info.Arguments = (@($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join " ")
    $Info.WorkingDirectory = $Root
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    return $Info
}

function Write-SmokeEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$Category,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()]$Value
    )
    $Directory = Join-Path $SmokeRoot $Category
    [System.IO.Directory]::CreateDirectory($Directory) | Out-Null
    $Text = if ($Value -is [string]) {
        $Value
    }
    else {
        ConvertTo-Json -InputObject $Value -Depth 8
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $Directory $Name),
        $Text + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Get-ProcessIdentity {
    param(
        [int]$Id = 0,
        [System.Diagnostics.Process]$Process = $null
    )
    $OwnsProcess = $false
    if ($null -eq $Process) {
        $Process = Get-Process -Id $Id -ErrorAction Stop
        $OwnsProcess = $true
    }
    try {
        return [pscustomobject]@{
            Id = $Process.Id
            Path = $Process.Path
            CreationTimeUtc = $Process.StartTime.ToUniversalTime().Ticks
        }
    }
    finally {
        if ($OwnsProcess) {
            $Process.Dispose()
        }
    }
}

function Test-ProcessIdentityAlive {
    param([Parameter(Mandatory = $true)]$Identity)
    try {
        $Current = Get-ProcessIdentity -Id $Identity.Id
        return (
            $Current.Path.Equals($Identity.Path, [System.StringComparison]::OrdinalIgnoreCase) -and
            $Current.CreationTimeUtc -eq $Identity.CreationTimeUtc
        )
    }
    catch {
        return $false
    }
}

function Stop-OwnedProcessIdentity {
    param([Parameter(Mandatory = $true)]$Identity)
    try {
        $Process = Get-Process -Id $Identity.Id -ErrorAction Stop
    }
    catch {
        return
    }
    try {
        $Current = Get-ProcessIdentity -Process $Process
        if (
            -not $Current.Path.Equals(
                $Identity.Path,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            $Current.CreationTimeUtc -ne $Identity.CreationTimeUtc
        ) {
            return
        }
        $Process.Kill()
        if (-not $Process.WaitForExit(5000)) {
            throw "owned process did not exit after handle-scoped termination"
        }
    }
    finally {
        $Process.Dispose()
    }
}

function Register-OwnedIdentity {
    param([Parameter(Mandatory = $true)]$Identity)
    [void]$OwnedProcessRegistry.Add($Identity)
}

function Invoke-OwnedCleanup {
    foreach ($Identity in @($OwnedProcessRegistry)) {
        try { Stop-OwnedProcessIdentity -Identity $Identity } catch {}
    }
}

function Get-OwnedProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootId)
    $ChildrenByParent = @{}
    foreach ($Record in @(Get-CimInstance Win32_Process)) {
        $Parent = [int]$Record.ParentProcessId
        if (-not $ChildrenByParent.ContainsKey($Parent)) {
            $ChildrenByParent[$Parent] = New-Object System.Collections.ArrayList
        }
        [void]$ChildrenByParent[$Parent].Add([int]$Record.ProcessId)
    }
    $Pending = New-Object System.Collections.Stack
    $Pending.Push($RootId)
    $Identities = New-Object System.Collections.ArrayList
    while ($Pending.Count -gt 0) {
        $CurrentId = [int]$Pending.Pop()
        try { [void]$Identities.Add((Get-ProcessIdentity -Id $CurrentId)) } catch {}
        if ($ChildrenByParent.ContainsKey($CurrentId)) {
            foreach ($ChildId in $ChildrenByParent[$CurrentId]) { $Pending.Push($ChildId) }
        }
    }
    return @($Identities)
}

function Stop-OwnedProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootId)
    $Identities = @(Get-OwnedProcessTree -RootId $RootId)
    [array]::Reverse($Identities)
    foreach ($Identity in $Identities) { Stop-OwnedProcessIdentity -Identity $Identity }
}

function Get-ProductProcessBaseline {
    param([Parameter(Mandatory = $true)][string]$ProductRoot)
    $BrokerPath = Join-Path $ProductRoot "aacc-spawn.exe"
    $Identities = @()
    foreach ($Record in @(Get-CimInstance Win32_Process)) {
        $MatchesBroker = (
            -not [string]::IsNullOrWhiteSpace([string]$Record.ExecutablePath) -and
            ([string]$Record.ExecutablePath).Equals(
                $BrokerPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        )
        $MatchesFixture = (
            -not [string]::IsNullOrWhiteSpace([string]$Record.CommandLine) -and
            ([string]$Record.CommandLine).IndexOf(
                $FixtureRoot,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -ge 0
        )
        if ($MatchesBroker -or $MatchesFixture) {
            try { $Identities += Get-ProcessIdentity -Id ([int]$Record.ProcessId) } catch {}
        }
    }
    return @($Identities | Sort-Object Id, CreationTimeUtc)
}

function Assert-ProductProcessBaseline {
    param(
        [Parameter(Mandatory = $true)][string]$ProductRoot,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()]$Expected,
        [Parameter(Mandatory = $true)][string]$Category
    )
    $Actual = @(Get-ProductProcessBaseline -ProductRoot $ProductRoot)
    Write-SmokeEvidence -Category $Category -Name "product-process-baseline.json" `
        -Value ([ordered]@{ expected = @($Expected); actual = $Actual })
    $ExpectedText = ConvertTo-Json -InputObject @($Expected) -Compress
    $ActualText = ConvertTo-Json -InputObject @($Actual) -Compress
    Assert-True ($ActualText -ceq $ExpectedText) "product process baseline changed"
}

function Wait-ProcessDeadline {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Category
    )
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-OwnedProcessTree -RootId $Process.Id
        throw "$Category exceeded its outer harness deadline"
    }
    return $Process.ExitCode
}

function Invoke-ExternalDeadline {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Category
    )
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = New-ProcessStartInfo -FilePath $FilePath -Arguments $Arguments
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        Assert-True $Process.Start() "$Category did not start"
        $Identity = Get-ProcessIdentity -Process $Process
        Register-OwnedIdentity -Identity $Identity
        $ExitCode = Wait-ProcessDeadline -Process $Process -TimeoutSeconds $TimeoutSeconds `
            -Category $Category
        Write-SmokeEvidence -Category "processes" `
            -Name (
                ($Category -replace '[^A-Za-z0-9.-]', '-') +
                "-pid-$($Identity.Id)-start-$($Identity.CreationTimeUtc).json"
            ) `
            -Value ([ordered]@{
                pid = $Identity.Id
                path = $Identity.Path
                creationTimeUtc = $Identity.CreationTimeUtc
                exitCode = $ExitCode
                elapsedMilliseconds = $Stopwatch.ElapsedMilliseconds
            })
        return $ExitCode
    }
    finally {
        $Stopwatch.Stop()
        $Process.Dispose()
    }
}

function Start-OwnedProcess {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string[]]$Arguments = @())
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = New-ProcessStartInfo -FilePath $FilePath -Arguments $Arguments
    $StartedId = 0
    try {
        Assert-True $Process.Start() "owned process did not start"
        $StartedId = $Process.Id
        $Identity = Get-ProcessIdentity -Process $Process
        Register-OwnedIdentity -Identity $Identity
        return [pscustomobject]@{
            Process = $Process
            Identity = $Identity
        }
    }
    catch {
        if ($StartedId -gt 0) {
            try { Stop-OwnedProcessTree -RootId $StartedId } catch {}
        }
        $Process.Dispose()
        throw
    }
}

function Wait-LiteralPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [System.Diagnostics.Process]$Owner = $null
    )
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if (Test-Path -LiteralPath $Path) { return }
        if ($null -ne $Owner -and $Owner.HasExited) {
            throw "product exited before producing a required smoke file"
        }
        Start-Sleep -Milliseconds 100
    }
    throw "required smoke file was not produced before deadline"
}

function Assert-ProcessExitedByDeadline {
    param([Parameter(Mandatory = $true)]$Identity, [int]$TimeoutSeconds = 20)
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if (-not (Test-ProcessIdentityAlive -Identity $Identity)) { return }
        Start-Sleep -Milliseconds 100
    }
    throw "owned process identity did not exit before deadline"
}

function Assert-ExactAcl {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][bool]$Directory,
        [Parameter(Mandatory = $true)][string]$EvidenceCategory,
        [Parameter(Mandatory = $true)][string]$EvidenceName
    )
    $Acl = Get-Acl -LiteralPath $Path
    $Rules = @(
        $Acl.GetAccessRules(
            $true,
            $false,
            [System.Security.Principal.SecurityIdentifier]
        )
    )
    $RuleEvidence = @(
        $Rules | ForEach-Object {
            [ordered]@{
                sid = $_.IdentityReference.Value
                type = $_.AccessControlType.ToString()
                rights = [int64]$_.FileSystemRights
                inherited = $_.IsInherited
                inheritanceFlags = $_.InheritanceFlags.ToString()
                propagationFlags = $_.PropagationFlags.ToString()
            }
        }
    )
    Write-SmokeEvidence -Category $EvidenceCategory -Name "$EvidenceName-acl.json" `
        -Value ([ordered]@{
            path = $Path
            sddl = $Acl.GetSecurityDescriptorSddlForm(
                [System.Security.AccessControl.AccessControlSections]::All
            )
            protected = $Acl.AreAccessRulesProtected
            aces = $RuleEvidence
        })
    Assert-True $Acl.AreAccessRulesProtected "DACL is not protected"
    $CurrentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $ExpectedSids = @($CurrentSid, "S-1-5-18", "S-1-5-32-544")
    Assert-True ($Rules.Count -eq 3) "DACL does not contain exactly three explicit ACEs"
    $Seen = @{}
    foreach ($Rule in $Rules) {
        $Sid = $Rule.IdentityReference.Value
        Assert-True ($ExpectedSids -contains $Sid) "DACL contains an unexpected SID"
        Assert-True (-not $Seen.ContainsKey($Sid)) "DACL contains a duplicate SID"
        $Seen[$Sid] = $true
        Assert-True (-not $Rule.IsInherited) "DACL contains an inherited ACE"
        Assert-True (
            $Rule.AccessControlType -eq
            [System.Security.AccessControl.AccessControlType]::Allow
        ) "DACL contains a deny ACE"
        Assert-True (
            [int64]$Rule.FileSystemRights -eq
            [int64][System.Security.AccessControl.FileSystemRights]::FullControl
        ) "DACL ACE is not exact full control"
        if ($Directory) {
            $ExpectedInheritance = (
                [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
            )
            Assert-True (
                $Rule.InheritanceFlags -eq $ExpectedInheritance
            ) "directory ACE inheritance flags are not exact"
        }
        else {
            Assert-True (
                $Rule.InheritanceFlags -eq
                [System.Security.AccessControl.InheritanceFlags]::None
            ) "file ACE unexpectedly inherits"
        }
        Assert-True (
            $Rule.PropagationFlags -eq
            [System.Security.AccessControl.PropagationFlags]::None
        ) "ACE propagation flags are not exact"
    }
    Assert-True ($Seen.Count -eq 3) "DACL expected SID set is incomplete"
}

function Get-TreeManifest {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "missing" }
    $RootPath = (Resolve-Path -LiteralPath $Path).Path
    $Entries = @(
        Get-ChildItem -LiteralPath $RootPath -Force -Recurse |
            Sort-Object FullName |
            ForEach-Object {
                $Relative = $_.FullName.Substring($RootPath.Length).TrimStart('\')
                if ($_.PSIsContainer) {
                    [ordered]@{ path = $Relative; type = "directory" }
                }
                else {
                    [ordered]@{
                        path = $Relative
                        type = "file"
                        size = $_.Length
                        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                    }
                }
            }
    )
    return ($Entries | ConvertTo-Json -Depth 4 -Compress)
}

function Get-RegistryManifest {
    if (-not (Test-Path -LiteralPath $UninstallRegistryPath)) { return "missing" }
    $Properties = Get-ItemProperty -LiteralPath $UninstallRegistryPath
    $Values = [ordered]@{}
    foreach ($Property in @($Properties.PSObject.Properties | Sort-Object Name)) {
        if ($Property.Name -notmatch '^PS') { $Values[$Property.Name] = [string]$Property.Value }
    }
    return ($Values | ConvertTo-Json -Compress)
}

function Get-ShortcutManifest {
    $Entries = @()
    foreach ($Path in @($StartMenuShortcut, $DesktopShortcut)) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $Entries += [ordered]@{
                leaf = [System.IO.Path]::GetFileName($Path)
                location = [System.IO.Path]::GetDirectoryName($Path)
                sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
            }
        }
    }
    return ($Entries | ConvertTo-Json -Depth 3 -Compress)
}

function Get-AppDataManifest {
    return Get-TreeManifest -Path $AppDataRoot
}

function Get-FullStateManifest {
    return [ordered]@{
        tree = Get-TreeManifest -Path $InstallRoot
        registry = Get-RegistryManifest
        shortcuts = Get-ShortcutManifest
        appdata = Get-AppDataManifest
    } | ConvertTo-Json -Depth 5 -Compress
}

function Get-PendingFileRenameOperations {
    $SessionManager = (
        "Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager"
    )
    try {
        $Value = (Get-ItemProperty -LiteralPath $SessionManager `
            -Name PendingFileRenameOperations -ErrorAction Stop).PendingFileRenameOperations
        return (@($Value) -join "`n")
    }
    catch {
        return "missing"
    }
}

function Get-SpecialSmokePath {
    param([Parameter(Mandatory = $true)][string]$RequestedPath)
    $Directory = Join-Path (Split-Path -Parent $RequestedPath) $SpecialLeaf
    [System.IO.Directory]::CreateDirectory($Directory) | Out-Null
    return Join-Path $Directory ([System.IO.Path]::GetFileName($RequestedPath))
}

function Invoke-Setup {
    param([Parameter(Mandatory = $true)][string]$LogPath, [bool]$ExpectSuccess)
    $LogPath = Get-SpecialSmokePath -RequestedPath $LogPath
    $Arguments = @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
        "/NOCLOSEAPPLICATIONS", "/NOFORCECLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS", "/LOG=$LogPath"
    )
    $ExitCode = Invoke-ExternalDeadline -FilePath $SetupPath -Arguments $Arguments `
        -TimeoutSeconds 180 -Category "Windows Setup"
    if ($ExpectSuccess) {
        Assert-True ($ExitCode -eq 0) "Windows Setup failed"
    }
    else {
        Assert-True ($ExitCode -ne 0) "Windows Setup unexpectedly accepted a refusal"
    }
}

function Wait-UninstallerTreeGone {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]$RootIdentity,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Category
    )
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $Identities = @{}
    try {
        $RootKey = "$($RootIdentity.Id):$($RootIdentity.CreationTimeUtc)"
        $Identities[$RootKey] = $RootIdentity
        Register-OwnedIdentity -Identity $RootIdentity

        # Capture immediately after Start so a short-lived original uninstaller
        # cannot hand off to its temporary clone before the first tree snapshot.
        $InitialSnapshot = @(Get-OwnedProcessTree -RootId $Process.Id)
        foreach ($Identity in $InitialSnapshot) {
            $Key = "$($Identity.Id):$($Identity.CreationTimeUtc)"
            $Identities[$Key] = $Identity
            Register-OwnedIdentity -Identity $Identity
        }

        $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        while (-not $Process.HasExited -and [DateTime]::UtcNow -lt $Deadline) {
            foreach ($Identity in @(Get-OwnedProcessTree -RootId $Process.Id)) {
                $Key = "$($Identity.Id):$($Identity.CreationTimeUtc)"
                $Identities[$Key] = $Identity
                Register-OwnedIdentity -Identity $Identity
            }
            Start-Sleep -Milliseconds 25
        }
        if (-not $Process.HasExited) {
            Stop-OwnedProcessTree -RootId $Process.Id
            throw "$Category exceeded its outer harness deadline"
        }

        # Win32_Process retains ParentProcessId on a live clone even after its
        # parent exits. One final root snapshot closes the parent-exit clone race.
        $FinalSnapshot = @(Get-OwnedProcessTree -RootId $Process.Id)
        foreach ($Identity in $FinalSnapshot) {
            $Key = "$($Identity.Id):$($Identity.CreationTimeUtc)"
            $Identities[$Key] = $Identity
            Register-OwnedIdentity -Identity $Identity
        }
        foreach ($Identity in @($Identities.Values)) {
            Assert-ProcessExitedByDeadline -Identity $Identity -TimeoutSeconds 30
        }
        Write-SmokeEvidence -Category "uninstall" `
            -Name (
                "uninstaller-process-tree-pid-$($Process.Id)-" +
                "created-$($RootIdentity.CreationTimeUtc).json"
            ) `
            -Value ([ordered]@{
                identities = @($Identities.Values)
                exitCode = $Process.ExitCode
                elapsedMilliseconds = $Stopwatch.ElapsedMilliseconds
            })
        return $Process.ExitCode
    }
    finally {
        $Stopwatch.Stop()
    }
}

function Invoke-Uninstaller {
    param([Parameter(Mandatory = $true)][string]$LogPath, [bool]$ExpectSuccess)
    $LogPath = Get-SpecialSmokePath -RequestedPath $LogPath
    $Arguments = @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/NOCLOSEAPPLICATIONS", "/NOFORCECLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS", "/LOG=$LogPath"
    )
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = New-ProcessStartInfo -FilePath $UninstallerPath -Arguments $Arguments
    try {
        Assert-True $Process.Start() "Windows uninstaller did not start"
        $Identity = Get-ProcessIdentity -Process $Process
        Register-OwnedIdentity -Identity $Identity
        $ExitCode = Wait-UninstallerTreeGone -Process $Process -RootIdentity $Identity `
            -TimeoutSeconds 180 -Category "Windows uninstaller"
    }
    finally {
        $Process.Dispose()
    }
    if ($ExpectSuccess) {
        Assert-True ($ExitCode -eq 0) "Windows uninstaller failed"
    }
    else {
        Assert-True ($ExitCode -ne 0) "Windows uninstaller unexpectedly accepted a refusal"
    }
}

function Assert-InstalledInternalMatchesManifest {
    param([Parameter(Mandatory = $true)][string]$EvidenceCategory)
    $ManifestPath = Join-Path $InstallRoot "uninstall\internal-manifest-v1.txt"
    Assert-True (Test-Path -LiteralPath $ManifestPath -PathType Leaf) `
        "installed internal manifest is missing"
    $Expected = @(
        [System.IO.File]::ReadAllLines(
            $ManifestPath,
            [System.Text.UTF8Encoding]::new($false, $true)
        ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $ActualList = @(
        Get-ChildItem -LiteralPath (Join-Path $InstallRoot "_internal") -Force -Recurse |
            ForEach-Object {
                $Relative = $_.FullName.Substring(
                    (Join-Path $InstallRoot "_internal").Length + 1
                ).Replace("\", "/")
                if ($_.PSIsContainer) { "D $Relative/" } else { "F $Relative" }
            }
    )
    [string[]]$Actual = $ActualList
    [string[]]$ExpectedSorted = $Expected
    [Array]::Sort($Actual, [System.StringComparer]::Ordinal)
    [Array]::Sort($ExpectedSorted, [System.StringComparer]::Ordinal)
    $HashMismatches = @()
    foreach ($Line in $Actual) {
        if ($Line.StartsWith("F ")) {
            $Relative = $Line.Substring(2).Replace("/", "\")
            $InstalledFile = Join-Path (Join-Path $InstallRoot "_internal") $Relative
            $BuiltFile = Join-Path (Join-Path $DistRoot "_internal") $Relative
            if (
                -not (Test-Path -LiteralPath $BuiltFile -PathType Leaf) -or
                (Get-FileHash -LiteralPath $InstalledFile -Algorithm SHA256).Hash -cne
                (Get-FileHash -LiteralPath $BuiltFile -Algorithm SHA256).Hash
            ) {
                $HashMismatches += $Line
            }
        }
    }
    Write-SmokeEvidence -Category $EvidenceCategory -Name "internal-manifest-compare.json" `
        -Value ([ordered]@{
            expected = $ExpectedSorted
            actual = $Actual
            hashMismatches = $HashMismatches
        })
    Assert-True (
        (ConvertTo-Json -InputObject $Actual -Compress) -ceq
        (ConvertTo-Json -InputObject $ExpectedSorted -Compress)
    ) "installed _internal does not match the committed build manifest"
    Assert-True ($HashMismatches.Count -eq 0) `
        "installed _internal hashes do not match the built payload"
}

function Write-CredentialsFixture {
    param([Parameter(Mandatory = $true)][string]$ConfigDirectory)
    $env:AACC_SMOKE_CREDENTIAL_DIR = $ConfigDirectory
    $Code = (
        "import os; from pathlib import Path; " +
        "from aacc.kimi_oauth import save_credentials; " +
        "save_credentials(Path(os.environ['AACC_SMOKE_CREDENTIAL_DIR']), " +
        "{'auth_method':'oauth','token':{'access_token':'smoke-only'}})"
    )
    $ExitCode = Invoke-ExternalDeadline -FilePath $UvPath `
        -Arguments @("run", "python", "-c", $Code) -TimeoutSeconds 30 `
        -Category "credentials fixture"
    Assert-True ($ExitCode -eq 0) "credentials fixture failed"
}

function Invoke-ProductBrokerProbes {
    param([Parameter(Mandatory = $true)][string]$ProductRoot, [string]$Category)
    $Baseline = @(Get-ProductProcessBaseline -ProductRoot $ProductRoot)
    $Marker = Join-Path $SmokeRoot "$Category\$SpecialLeaf\broker-marker.json"
    $TimeoutIdentities = Join-Path $SmokeRoot `
        "$Category\$SpecialLeaf\timeout-identities.jsonl"
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $Marker)) | Out-Null
    $Arguments = @(
        "run", "python", "tests/windows/run_product_broker_probes.py",
        "--broker", (Join-Path $ProductRoot "aacc-spawn.exe"),
        "--codex", $FakeCodexCmd,
        "--bundle-dir", (Join-Path $ProductRoot "_internal"),
        "--marker", $Marker,
        "--timeout-identities", $TimeoutIdentities
    )
    $ExitCode = Invoke-ExternalDeadline -FilePath $UvPath -Arguments $Arguments `
        -TimeoutSeconds 180 -Category "20 normal and timeout broker probes"
    Assert-True ($ExitCode -eq 0) "product broker probes failed"
    Assert-ProductProcessBaseline -ProductRoot $ProductRoot -Expected $Baseline `
        -Category $Category
}

function Start-And-VerifyProduct {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$DatabasePath,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$MarkerPath,
        [Parameter(Mandatory = $true)][string]$Category
    )
    Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
    $env:AACC_FAKE_CODEX_MODE = ""
    $env:AACC_FAKE_CODEX_MARKER = $MarkerPath
    $ProductRoot = Split-Path -Parent $Executable
    $Baseline = @(Get-ProductProcessBaseline -ProductRoot $ProductRoot)
    Write-SmokeEvidence -Category $Category -Name "before-product-process-baseline.json" `
        -Value $Baseline
    $Owned = Start-OwnedProcess -FilePath $Executable
    Write-SmokeEvidence -Category $Category -Name "product-main-identity.json" `
        -Value $Owned.Identity
    try {
        foreach ($Required in @($ConfigPath, $DatabasePath, $LogPath, $MarkerPath)) {
            Wait-LiteralPath -Path $Required -TimeoutSeconds 30 -Owner $Owned.Process
        }
        $SurvivalDeadline = [DateTime]::UtcNow.AddSeconds(20)
        while ([DateTime]::UtcNow -lt $SurvivalDeadline) {
            Assert-True (-not $Owned.Process.HasExited) "$Category did not survive first launch"
            Start-Sleep -Milliseconds 250
        }
        return [pscustomobject]@{
            Process = $Owned.Process
            Identity = $Owned.Identity
            ProductRoot = $ProductRoot
            ProductBaseline = $Baseline
            EvidenceCategory = $Category
        }
    }
    catch {
        Stop-OwnedProcessIdentity -Identity $Owned.Identity
        $Owned.Process.Dispose()
        throw
    }
}

function Invoke-GracefulShutdown {
    param([Parameter(Mandatory = $true)][string]$Executable, [Parameter(Mandatory = $true)]$Owned)
    $ExitCode = Invoke-ExternalDeadline -FilePath $Executable `
        -Arguments @("--shutdown-for-update") -TimeoutSeconds 30 `
        -Category "AACC graceful shutdown control"
    Assert-True ($ExitCode -eq 0) "AACC graceful shutdown control returned non-zero"
    Assert-ProcessExitedByDeadline -Identity $Owned.Identity -TimeoutSeconds 20
    $Owned.Process.Dispose()
    Assert-ProductProcessBaseline -ProductRoot $Owned.ProductRoot `
        -Expected $Owned.ProductBaseline -Category $Owned.EvidenceCategory
}

function Assert-ProductAcl {
    param(
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$EvidenceCategory
    )
    Assert-ExactAcl -Path $DataRoot -Directory $true `
        -EvidenceCategory $EvidenceCategory -EvidenceName "data-directory"
    foreach ($Leaf in @("config.yaml", "aacc.db", "aacc.db-wal", "aacc.db-shm", "kimi-credentials.json")) {
        $Path = Join-Path $DataRoot $Leaf
        Assert-True (Test-Path -LiteralPath $Path -PathType Leaf) `
            "required protected product file is missing"
        Assert-ExactAcl -Path $Path -Directory $false `
            -EvidenceCategory $EvidenceCategory -EvidenceName $Leaf
    }
}

function Invoke-FrozenSmoke {
    $FrozenRoot = Join-Path $SmokeRoot "frozen\$SpecialLeaf\AACC"
    [System.IO.Directory]::CreateDirectory($FrozenRoot) | Out-Null
    $ConfigPath = Join-Path $FrozenRoot "config.yaml"
    $DatabasePath = Join-Path $FrozenRoot "aacc.db"
    $LogPath = Join-Path $FrozenRoot "logs\app.log"
    $MarkerPath = Join-Path $SmokeRoot "frozen\$SpecialLeaf\fake-codex-marker.json"
    $env:AACC_CONFIG_PATH = $ConfigPath
    $env:AACC_DATABASE_PATH = $DatabasePath
    $env:AACC_CODEX_EXECUTABLE = $FakeCodexCmd
    $env:AACC_FAKE_CODEX_PYTHON = $PythonPath
    $env:QT_QPA_PLATFORM = "offscreen"
    $Owned = Start-And-VerifyProduct -Executable $FrozenAacc `
        -ConfigPath $ConfigPath -DatabasePath $DatabasePath -LogPath $LogPath `
        -MarkerPath $MarkerPath -Category "frozen AACC"
    Write-CredentialsFixture -ConfigDirectory $FrozenRoot
    foreach ($Leaf in @("aacc.db-wal", "aacc.db-shm", "kimi-credentials.json")) {
        Wait-LiteralPath -Path (Join-Path $FrozenRoot $Leaf) -TimeoutSeconds 10 `
            -Owner $Owned.Process
    }
    Assert-ProductAcl -DataRoot $FrozenRoot -EvidenceCategory "frozen"
    Invoke-ProductBrokerProbes -ProductRoot $DistRoot -Category "frozen"
    Invoke-GracefulShutdown -Executable $FrozenAacc -Owned $Owned
    "hosted Windows Server frozen product smoke complete" |
        Set-Content -LiteralPath (Join-Path $SmokeRoot "frozen\result.log") -Encoding ASCII
}

function Invoke-InstalledLaunch {
    param([Parameter(Mandatory = $true)][string]$Category)
    Remove-Item Env:AACC_CONFIG_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:AACC_DATABASE_PATH -ErrorAction SilentlyContinue
    $env:APPDATA = $IsolatedRoaming
    $env:AACC_CODEX_EXECUTABLE = $FakeCodexCmd
    $env:AACC_FAKE_CODEX_PYTHON = $PythonPath
    $env:QT_QPA_PLATFORM = "offscreen"
    $ConfigPath = Join-Path $AppDataRoot "config.yaml"
    $DatabasePath = Join-Path $AppDataRoot "aacc.db"
    $LogPath = Join-Path $AppDataRoot "logs\app.log"
    $MarkerPath = Join-Path $SmokeRoot "$Category\$SpecialLeaf\fake-codex-marker.json"
    return Start-And-VerifyProduct -Executable $InstalledAacc `
        -ConfigPath $ConfigPath -DatabasePath $DatabasePath -LogPath $LogPath `
        -MarkerPath $MarkerPath -Category "installed AACC"
}

function Test-InstalledControlRefusal {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("setup", "uninstall")][string]$Action,
        [Parameter(Mandatory = $true)][ValidateSet("nonzero", "timeout", "false-success")]
        [string]$Mode,
        [bool]$Capability = $true
    )
    $SavedAacc = Join-Path $SmokeRoot "reinstall\saved-AACC.exe"
    Copy-Item -LiteralPath $InstalledAacc -Destination $SavedAacc -Force
    Copy-Item -LiteralPath $LegacyFixture -Destination $InstalledAacc -Force
    if ($Capability) {
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $CapabilityPath)) | Out-Null
        [System.IO.File]::WriteAllText($CapabilityPath, "AACC shutdown protocol v1`n")
    }
    else {
        Remove-Item -LiteralPath $CapabilityPath -Force -ErrorAction SilentlyContinue
    }
    $env:AACC_LEGACY_CONTROL_MODE = $Mode
    $Scenario = "$Action-$Mode-capability-$Capability"
    $LegacyEvidence = Join-Path $SmokeRoot `
        "$Action\$Scenario\$SpecialLeaf\legacy-control-evidence.jsonl"
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $LegacyEvidence)) | Out-Null
    Remove-Item -LiteralPath $LegacyEvidence -Force -ErrorAction SilentlyContinue
    $env:AACC_LEGACY_EVIDENCE_FILE = $LegacyEvidence
    $Legacy = Start-OwnedProcess -FilePath $InstalledAacc
    try {
        Start-Sleep -Seconds 1
        Assert-True (-not $Legacy.Process.HasExited) "legacy fixture did not own its window"
        $Before = Get-FullStateManifest
        Write-SmokeEvidence -Category "$Action\$Scenario" -Name "before-manifest.json" `
            -Value $Before
        $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        if ($Action -eq "setup") {
            Invoke-Setup -LogPath (Join-Path $SmokeRoot "reinstall\$Mode-setup.log") `
                -ExpectSuccess $false
        }
        else {
            Invoke-Uninstaller `
                -LogPath (Join-Path $SmokeRoot "uninstall\$Mode-uninstall.log") `
                -ExpectSuccess $false
        }
        $Stopwatch.Stop()
        $After = Get-FullStateManifest
        Write-SmokeEvidence -Category "$Action\$Scenario" -Name "after-manifest.json" `
            -Value $After
        Assert-True ($After -ceq $Before) "$Action refusal mutated installed state"
        Assert-True (Test-ProcessIdentityAlive -Identity $Legacy.Identity) `
            "$Action refusal terminated the legacy main process"
        if ($Capability) {
            Wait-LiteralPath -Path $LegacyEvidence -TimeoutSeconds 5
            $ControlRecords = @(
                Get-Content -LiteralPath $LegacyEvidence -Encoding UTF8 |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                    ForEach-Object { $_ | ConvertFrom-Json }
            )
            Assert-True ($ControlRecords.Count -eq 1) `
                "legacy control invocation evidence count is not exact"
            $Control = $ControlRecords[0]
            Assert-True ($Control.mode -ceq $Mode) "legacy control mode was not exercised"
            Assert-True (
                ([string]$Control.image_path).Equals(
                    $InstalledAacc,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            ) "legacy control child image path is wrong"
            $ControlIdentity = [pscustomobject]@{
                Id = [int]$Control.pid
                Path = [string]$Control.image_path
                CreationTimeUtc = [DateTime]::FromFileTimeUtc(
                    [int64]$Control.creation_time
                ).Ticks
            }
            Assert-ProcessExitedByDeadline -Identity $ControlIdentity -TimeoutSeconds 5
            if ($Mode -eq "timeout") {
                Assert-True (
                    $Stopwatch.ElapsedMilliseconds -ge 20000 -and
                    $Stopwatch.ElapsedMilliseconds -le 40000
                ) "legacy timeout did not exercise the bounded 25-second control path"
            }
            else {
                Assert-True ($Stopwatch.ElapsedMilliseconds -lt 20000) `
                    "non-timeout legacy control path was unexpectedly slow"
            }
            Write-SmokeEvidence -Category "$Action\$Scenario" `
                -Name "legacy-control-result.json" -Value ([ordered]@{
                    control = $Control
                    main = $Legacy.Identity
                    elapsedMilliseconds = $Stopwatch.ElapsedMilliseconds
                    childExited = -not (Test-ProcessIdentityAlive -Identity $ControlIdentity)
                })
        }
        else {
            Assert-True (-not (Test-Path -LiteralPath $LegacyEvidence)) `
                "legacy control was invoked without a capability marker"
        }
    }
    finally {
        Stop-OwnedProcessIdentity -Identity $Legacy.Identity
        $Legacy.Process.Dispose()
        Copy-Item -LiteralPath $SavedAacc -Destination $InstalledAacc -Force
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $CapabilityPath)) | Out-Null
        [System.IO.File]::WriteAllText($CapabilityPath, "AACC shutdown protocol v1`n")
        Remove-Item Env:AACC_LEGACY_CONTROL_MODE -ErrorAction SilentlyContinue
        Remove-Item Env:AACC_LEGACY_EVIDENCE_FILE -ErrorAction SilentlyContinue
    }
}

function Compile-WindowsFixtures {
    . "$PSScriptRoot\windows_toolchain.ps1"
    $ProgramFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ([string]::IsNullOrWhiteSpace($ProgramFilesX86)) { $ProgramFilesX86 = $env:ProgramFiles }
    $VsWhere = Join-Path $ProgramFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
    $Toolchain = Select-AaccMsvcToolchain `
        -Candidates (Get-AaccVsWhereCandidates -VsWherePath $VsWhere)
    Set-AaccToolchainEnvironment -Toolchain $Toolchain
    $Cl = $Toolchain.Tools["cl.exe"]
    $LegacyObject = Join-Path $FixtureRoot "fake_legacy_aacc.obj"
    $LockerObject = Join-Path $FixtureRoot "lock_payload.obj"
    & $Cl /nologo /std:c++17 /O2 /MT /GS /W4 /WX /EHsc /DUNICODE /D_UNICODE `
        /c "/Fo$LegacyObject" (Join-Path $Root "tests\windows\fake_legacy_aacc.cpp")
    Assert-True ($LASTEXITCODE -eq 0) "legacy fixture compilation failed"
    & $Cl /nologo "/Fe$LegacyFixture" $LegacyObject /link /SUBSYSTEM:WINDOWS user32.lib
    Assert-True ($LASTEXITCODE -eq 0) "legacy fixture link failed"
    & $Cl /nologo /std:c++17 /O2 /MT /GS /W4 /WX /EHsc /DUNICODE /D_UNICODE `
        /c "/Fo$LockerObject" (Join-Path $Root "tests\windows\lock_payload.cpp")
    Assert-True ($LASTEXITCODE -eq 0) "locker fixture compilation failed"
    & $Cl /nologo "/Fe$LockerFixture" $LockerObject /link /SUBSYSTEM:CONSOLE
    Assert-True ($LASTEXITCODE -eq 0) "locker fixture link failed"
}

function Invoke-SmokeMain {
if ($env:OS -ne "Windows_NT") { throw "Windows product smoke requires Windows" }
$Root = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
Set-Location -LiteralPath $Root
$UvPath = (Get-Command uv -ErrorAction Stop).Source
$PythonPath = (Resolve-Path -LiteralPath (Join-Path $Root ".venv\Scripts\python.exe")).Path
$DistRoot = (Resolve-Path -LiteralPath (Join-Path $Root "dist\AACC")).Path
$FrozenAacc = Join-Path $DistRoot "AACC.exe"
Assert-True (Test-Path -LiteralPath $FrozenAacc -PathType Leaf) "frozen AACC is missing"

$SpecialLeaf = (
    [string][char]0x6D4B + [char]0x8BD5 + " AACC &() %! [Server]"
)
$SmokeRoot = Join-Path $Root "build\windows-smoke"
if (Test-Path -LiteralPath $SmokeRoot) {
    Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
}
[System.IO.Directory]::CreateDirectory($SmokeRoot) | Out-Null
foreach ($Category in @("frozen", "installed", "reinstall", "uninstall")) {
    [System.IO.Directory]::CreateDirectory((Join-Path $SmokeRoot $Category)) | Out-Null
}
$FixtureRoot = Join-Path $SmokeRoot "fixtures\$SpecialLeaf\native &() %! [x]"
[System.IO.Directory]::CreateDirectory($FixtureRoot) | Out-Null
foreach ($Fixture in @("fake-codex.cmd", "fake_codex_server.py", "fake_codex_timeout.py")) {
    Copy-Item -LiteralPath (Join-Path $Root "tests\windows\$Fixture") `
        -Destination (Join-Path $FixtureRoot $Fixture)
}
$FakeCodexCmd = Join-Path $FixtureRoot "fake-codex.cmd"
$LegacyFixture = Join-Path $FixtureRoot "fake legacy AACC.exe"
$LockerFixture = Join-Path $FixtureRoot "lock payload.exe"

Write-Host "AACC_WINDOWS_SMOKE evidence=hosted-Windows-Server"
Invoke-FrozenSmoke

if ($FrozenOnly) {
    Write-Host "Hosted Windows Server evidence only; consumer Windows 10/11 not claimed"
    return
}

$Version = ((& uv version --short | Select-Object -First 1) | Out-String).Trim()
$SetupSource = Join-Path $Root "dist\installer\AACC-$Version-Setup.exe"
$ChecksumSource = "$SetupSource.sha256"
Assert-True (Test-Path -LiteralPath $SetupSource -PathType Leaf) "Setup is missing"
Assert-True (Test-Path -LiteralPath $ChecksumSource -PathType Leaf) "Setup checksum is missing"
$SpecialSetupRoot = Join-Path $SmokeRoot "installed\$SpecialLeaf\setup copy &() %! [x]"
[System.IO.Directory]::CreateDirectory($SpecialSetupRoot) | Out-Null
$SetupPath = Join-Path $SpecialSetupRoot "AACC-$Version-Setup.exe"
$ChecksumCopy = "$SetupPath.sha256"
Copy-Item -LiteralPath $SetupSource -Destination $SetupPath
Copy-Item -LiteralPath $ChecksumSource -Destination $ChecksumCopy
Assert-True (
    (Get-FileHash -LiteralPath $SetupSource -Algorithm SHA256).Hash -ceq
    (Get-FileHash -LiteralPath $SetupPath -Algorithm SHA256).Hash
) "special-path Setup copy hash mismatch"
$ChecksumBytes = [System.IO.File]::ReadAllBytes($ChecksumCopy)
Assert-True (
    -not (
        $ChecksumBytes.Length -ge 3 -and
        $ChecksumBytes[0] -eq 0xEF -and
        $ChecksumBytes[1] -eq 0xBB -and
        $ChecksumBytes[2] -eq 0xBF
    )
) "special-path checksum copy contains a BOM"
$StrictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
$ExpectedChecksum = (
    (Get-FileHash -LiteralPath $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant() +
    "  " + [System.IO.Path]::GetFileName($SetupPath) + "`n"
)
Assert-True (
    $StrictUtf8.GetString($ChecksumBytes) -ceq $ExpectedChecksum
) "special-path checksum copy is malformed"
Compile-WindowsFixtures

$InstallRoot = Join-Path (
    [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
) "Programs\AACC"
$InstalledAacc = Join-Path $InstallRoot "AACC.exe"
$UninstallerPath = Join-Path $InstallRoot "uninstall\unins000.exe"
$CapabilityPath = Join-Path $InstallRoot "uninstall\shutdown-v1.capability"
$UninstallRegistryPath = (
    "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Uninstall\" +
    "{C174E242-E193-5863-8A46-F16152875173}_is1"
)
$StartMenuShortcut = Join-Path (
    [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
) "AACC.lnk"
$DesktopShortcut = Join-Path (
    [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
) "AACC.lnk"
$IsolatedRoaming = Join-Path $SmokeRoot "installed\profile &() %! [x]\AppData\Roaming"
$AppDataRoot = Join-Path $IsolatedRoaming "AACC"
[System.IO.Directory]::CreateDirectory($IsolatedRoaming) | Out-Null

Assert-True (-not (Test-Path -LiteralPath $InstallRoot)) "pre-install payload is not clean"
Assert-True (-not (Test-Path -LiteralPath $UninstallRegistryPath)) `
    "pre-install HKCU registration is not clean"
Assert-True (-not (Test-Path -LiteralPath $StartMenuShortcut)) `
    "pre-install shortcut state is not clean"
Invoke-Setup -LogPath (Join-Path $SmokeRoot "installed\fresh-install.log") -ExpectSuccess $true
foreach ($Required in @(
    $InstalledAacc,
    (Join-Path $InstallRoot "aacc-spawn.exe"),
    (Join-Path $InstallRoot "_internal"),
    $UninstallerPath,
    $CapabilityPath,
    $StartMenuShortcut
)) {
    Assert-True (Test-Path -LiteralPath $Required) "installed product tree is incomplete"
}
Assert-InstalledInternalMatchesManifest -EvidenceCategory "installed"
Assert-True (-not (Test-Path -LiteralPath $DesktopShortcut)) `
    "silent install unexpectedly created a desktop shortcut"
Assert-True (Test-Path -LiteralPath $UninstallRegistryPath) `
    "HKCU uninstall registration is missing"
$HklmUninstall = (
    "Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall\" +
    "{C174E242-E193-5863-8A46-F16152875173}_is1"
)
Assert-True (-not (Test-Path -LiteralPath $HklmUninstall)) "HKLM registration is forbidden"
foreach ($ProgramFilesRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if (-not [string]::IsNullOrWhiteSpace($ProgramFilesRoot)) {
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $ProgramFilesRoot "AACC"))) `
            "per-user Setup wrote under Program Files"
    }
}

$Installed = Invoke-InstalledLaunch -Category "installed"
Write-CredentialsFixture -ConfigDirectory $AppDataRoot
foreach ($Leaf in @("aacc.db-wal", "aacc.db-shm", "kimi-credentials.json")) {
    Wait-LiteralPath -Path (Join-Path $AppDataRoot $Leaf) -TimeoutSeconds 10 `
        -Owner $Installed.Process
}
Assert-ProductAcl -DataRoot $AppDataRoot -EvidenceCategory "installed"
Invoke-ProductBrokerProbes -ProductRoot $InstallRoot -Category "installed"
Invoke-GracefulShutdown -Executable $InstalledAacc -Owned $Installed
[System.IO.File]::WriteAllText((Join-Path $AppDataRoot "preserve-me.txt"), "preserve")

Test-InstalledControlRefusal -Action setup -Mode nonzero -Capability $false
foreach ($Mode in @("nonzero", "false-success", "timeout")) {
    Test-InstalledControlRefusal -Action setup -Mode $Mode -Capability $true
    Test-InstalledControlRefusal -Action uninstall -Mode $Mode -Capability $true
}

$RunningForReinstall = Invoke-InstalledLaunch -Category "reinstall"
Invoke-Setup -LogPath (Join-Path $SmokeRoot "reinstall\running-reinstall.log") `
    -ExpectSuccess $true
Assert-ProcessExitedByDeadline -Identity $RunningForReinstall.Identity
$RunningForReinstall.Process.Dispose()
Assert-ProductProcessBaseline -ProductRoot $RunningForReinstall.ProductRoot `
    -Expected $RunningForReinstall.ProductBaseline -Category "reinstall"
Assert-True (Test-Path -LiteralPath (Join-Path $AppDataRoot "preserve-me.txt")) `
    "reinstall did not preserve AppData"

$RollbackSentinel = Join-Path $InstallRoot "_internal\rollback-sentinel.bin"
[System.IO.File]::WriteAllBytes($RollbackSentinel, [byte[]](1, 4, 2))
$RollbackProbe = @(
    Get-ChildItem -LiteralPath (Join-Path $InstallRoot "_internal") `
        -Filter "METADATA" -File -Recurse |
        Where-Object { $_.DirectoryName -like "*.dist-info" } |
        Select-Object -First 1
)
Assert-True ($RollbackProbe.Count -eq 1) `
    "no non-runtime metadata file is available for rollback proof"
$RollbackProbePath = $RollbackProbe[0].FullName
$RollbackProbeBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
    "AACC_OLD_INTERNAL_ROLLBACK_PROBE`n"
)
[System.IO.File]::WriteAllBytes($RollbackProbePath, $RollbackProbeBytes)
$BeforeFault = Get-FullStateManifest
Write-SmokeEvidence -Category "reinstall\lock-fault" -Name "before-manifest.json" `
    -Value $BeforeFault
$PendingBefore = Get-PendingFileRenameOperations
$LockedPayload = $InstalledAacc
$LockReady = Join-Path $SmokeRoot "reinstall\lock-ready.txt"
$Locker = Start-OwnedProcess -FilePath $LockerFixture -Arguments @($LockedPayload, $LockReady)
try {
    Wait-LiteralPath -Path $LockReady -TimeoutSeconds 10 -Owner $Locker.Process
    Invoke-Setup -LogPath (Join-Path $SmokeRoot "reinstall\locked-failure.log") `
        -ExpectSuccess $false
}
finally {
    Stop-OwnedProcessIdentity -Identity $Locker.Identity
    $Locker.Process.Dispose()
}
$AfterFault = Get-FullStateManifest
Write-SmokeEvidence -Category "reinstall\lock-fault" -Name "after-manifest.json" `
    -Value $AfterFault
Assert-True ($AfterFault -ceq $BeforeFault) `
    "native lock fault did not restore the complete install manifest"
Assert-True ((Get-PendingFileRenameOperations) -ceq $PendingBefore) `
    "failed reinstall scheduled a pending-reboot replacement"
Assert-True (Test-Path -LiteralPath $RollbackSentinel -PathType Leaf) `
    "failed reinstall removed rollback-sentinel.bin"
Assert-True (
    [Convert]::ToBase64String($RollbackProbeBytes) -ceq
    [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($RollbackProbePath))
) "failed reinstall did not roll back the already replaced internal metadata"
Assert-True (
    @(Get-ChildItem -LiteralPath (Split-Path -Parent $InstallRoot) -Force |
        Where-Object { $_.Name -like "AACC.aacc-*" }).Count -eq 0
) "failed reinstall left staging or backup residue"

$OldPayload = Invoke-InstalledLaunch -Category "reinstall\old-payload"
Invoke-ProductBrokerProbes -ProductRoot $InstallRoot -Category "reinstall\old-payload"
Invoke-GracefulShutdown -Executable $InstalledAacc -Owned $OldPayload
Invoke-Setup -LogPath (Join-Path $SmokeRoot "reinstall\after-lock-success.log") `
    -ExpectSuccess $true
Assert-True (-not (Test-Path -LiteralPath $RollbackSentinel)) `
    "successful reinstall left a stale sentinel"
Assert-True (Test-Path -LiteralPath (Join-Path $AppDataRoot "preserve-me.txt")) `
    "successful reinstall changed AppData"
Assert-InstalledInternalMatchesManifest -EvidenceCategory "reinstall"

$RunningForUninstall = Invoke-InstalledLaunch -Category "uninstall"
Invoke-Uninstaller -LogPath (Join-Path $SmokeRoot "uninstall\running-uninstall.log") `
    -ExpectSuccess $true
Assert-ProcessExitedByDeadline -Identity $RunningForUninstall.Identity
$RunningForUninstall.Process.Dispose()
Assert-ProductProcessBaseline -ProductRoot $RunningForUninstall.ProductRoot `
    -Expected $RunningForUninstall.ProductBaseline -Category "uninstall"
$UninstallDeadline = [DateTime]::UtcNow.AddSeconds(30)
while (
    [DateTime]::UtcNow -lt $UninstallDeadline -and
    (
        (Test-Path -LiteralPath $InstallRoot) -or
        (Test-Path -LiteralPath $UninstallRegistryPath) -or
        (Test-Path -LiteralPath $StartMenuShortcut)
    )
) {
    Start-Sleep -Milliseconds 200
}
Assert-True (-not (Test-Path -LiteralPath $InstallRoot)) `
    "uninstaller clone did not remove itself and the program directory"
Assert-True (-not (Test-Path -LiteralPath $UninstallRegistryPath)) `
    "uninstaller left HKCU registration"
Assert-True (-not (Test-Path -LiteralPath $StartMenuShortcut)) `
    "uninstaller left the Start Menu shortcut"
Assert-True (Test-Path -LiteralPath (Join-Path $AppDataRoot "preserve-me.txt")) `
    "uninstaller removed preserved AppData"

[System.IO.Directory]::CreateDirectory($InstallRoot) | Out-Null
Copy-Item -LiteralPath $LegacyFixture -Destination $InstalledAacc
Invoke-Setup -LogPath (Join-Path $SmokeRoot "installed\stopped-legacy-install.log") `
    -ExpectSuccess $true
Assert-True (Test-Path -LiteralPath $CapabilityPath) `
    "stopped legacy install did not become managed"
Assert-InstalledInternalMatchesManifest -EvidenceCategory "installed\stopped-legacy"
Invoke-Uninstaller -LogPath (Join-Path $SmokeRoot "uninstall\final-cleanup.log") `
    -ExpectSuccess $true

Write-Host "Hosted Windows Server evidence only; consumer Windows 10/11 not claimed"
}

try {
    Invoke-SmokeMain
}
finally {
    Invoke-OwnedCleanup
}
