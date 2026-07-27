#requires -Version 5.1
param([switch]$FrozenOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$OwnedProcessRegistry = New-Object System.Collections.ArrayList
# CIM_DATETIME stores six fractional digits; one microsecond is ten .NET ticks.
$CreationTickTolerance = 10

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
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments
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
            Path = $Process.MainModule.FileName
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

function Restore-LiteralFileAfterProcessExit {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)]$Identity,
        [int]$TimeoutSeconds = 5
    )
    Assert-True (-not (Test-ProcessIdentityAlive -Identity $Identity)) `
        "refusing to restore a fixture while its exact process identity is alive"
    $SourceHash = (
        Get-FileHash -LiteralPath $Source -Algorithm SHA256
    ).Hash
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ($true) {
        try {
            [System.IO.File]::Copy($Source, $Destination, $true)
            $DestinationHash = (
                Get-FileHash -LiteralPath $Destination -Algorithm SHA256
            ).Hash
            Assert-True ($DestinationHash -ceq $SourceHash) `
                "restored fixture hash does not match its saved source"
            return
        }
        catch [System.IO.IOException] {
            $Win32Code = $_.Exception.HResult -band 0xFFFF
            if ($Win32Code -notin @(32, 33) -or [DateTime]::UtcNow -ge $Deadline) {
                throw
            }
            # Windows can retain a terminated image mapping briefly while
            # Defender or another filter driver closes its final handle.
            Start-Sleep -Milliseconds 100
        }
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

function Test-ProcessIdentityExactMatch {
    param(
        [Parameter(Mandatory = $true)]$ExpectedIdentity,
        [Parameter(Mandatory = $true)][AllowNull()]$CurrentIdentity
    )
    if ($null -eq $CurrentIdentity) {
        return $false
    }
    return (
        $CurrentIdentity.Id -eq $ExpectedIdentity.Id -and
        $CurrentIdentity.Path.Equals(
            $ExpectedIdentity.Path,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        $CurrentIdentity.CreationTimeUtc -eq $ExpectedIdentity.CreationTimeUtc
    )
}

function Test-ProcessIdentityExactAlive {
    param([Parameter(Mandatory = $true)]$Identity)
    try {
        $CurrentIdentity = Get-ProcessIdentity -Id $Identity.Id
    }
    catch {
        return $false
    }
    return (Test-ProcessIdentityExactMatch -ExpectedIdentity $Identity `
        -CurrentIdentity $CurrentIdentity)
}

function Test-OwnedProcessEdge {
    param(
        [Parameter(Mandatory = $true)]$ParentIdentity,
        [Parameter(Mandatory = $true)]$ChildIdentity
    )
    return (
        $ChildIdentity.CreationTimeUtc -ge $ParentIdentity.CreationTimeUtc
    )
}

function Convert-CimCreationDateToUtcTicks {
    param([Parameter(Mandatory = $true)][AllowNull()]$CreationDate)
    if ($null -eq $CreationDate) {
        return $null
    }
    try {
        if ($CreationDate -is [DateTime]) {
            return ([DateTime]$CreationDate).ToUniversalTime().Ticks
        }
        if ($CreationDate -is [string] -and
            -not [string]::IsNullOrWhiteSpace([string]$CreationDate)) {
            return (
                [System.Management.ManagementDateTimeConverter]::ToDateTime(
                    [string]$CreationDate
                ).ToUniversalTime().Ticks
            )
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-CimProcessRecordById {
    param([Parameter(Mandatory = $true)][int]$Id)
    try {
        $Records = @(
            Get-CimInstance -ClassName Win32_Process `
                -Filter ("ProcessId = {0}" -f $Id) -ErrorAction Stop
        )
    }
    catch {
        return $null
    }
    if ($Records.Count -ne 1) {
        return $null
    }
    return $Records[0]
}

function Test-CimChildRecordBound {
    param(
        [Parameter(Mandatory = $true)]$SnapshotRecord,
        [Parameter(Mandatory = $true)]$FreshRecord,
        [Parameter(Mandatory = $true)]$ParentIdentity,
        [Parameter(Mandatory = $true)]$ChildIdentity
    )
    try {
        if (
            $null -eq $SnapshotRecord.ProcessId -or
            $null -eq $SnapshotRecord.ParentProcessId -or
            $null -eq $FreshRecord.ProcessId -or
            $null -eq $FreshRecord.ParentProcessId -or
            [string]::IsNullOrWhiteSpace([string]$SnapshotRecord.ExecutablePath) -or
            [string]::IsNullOrWhiteSpace([string]$FreshRecord.ExecutablePath) -or
            $null -eq $SnapshotRecord.CreationDate -or
            $null -eq $FreshRecord.CreationDate
        ) {
            return $false
        }
        if (
            [int]$SnapshotRecord.ProcessId -ne $ChildIdentity.Id -or
            [int]$FreshRecord.ProcessId -ne $ChildIdentity.Id -or
            [int]$SnapshotRecord.ParentProcessId -ne $ParentIdentity.Id -or
            [int]$FreshRecord.ParentProcessId -ne $ParentIdentity.Id -or
            -not ([string]$SnapshotRecord.ExecutablePath).Equals(
                [string]$FreshRecord.ExecutablePath,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not ([string]$FreshRecord.ExecutablePath).Equals(
                $ChildIdentity.Path,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        $SnapshotCreationTicks = Convert-CimCreationDateToUtcTicks `
            -CreationDate $SnapshotRecord.CreationDate
        $FreshCreationTicks = Convert-CimCreationDateToUtcTicks `
            -CreationDate $FreshRecord.CreationDate
        if (
            $null -eq $SnapshotCreationTicks -or
            $null -eq $FreshCreationTicks -or
            $SnapshotCreationTicks -ne $FreshCreationTicks
        ) {
            return $false
        }
        $CreationDelta = if ($FreshCreationTicks -ge $ChildIdentity.CreationTimeUtc) {
            $FreshCreationTicks - $ChildIdentity.CreationTimeUtc
        }
        else {
            $ChildIdentity.CreationTimeUtc - $FreshCreationTicks
        }
        return ($CreationDelta -le $CreationTickTolerance)
    }
    catch {
        return $false
    }
}

function Get-OwnedProcessTree {
    param(
        [Parameter(Mandatory = $true)][int]$RootId,
        $ExpectedRootIdentity = $null
    )
    if ($null -ne $ExpectedRootIdentity) {
        $RootIdentity = $ExpectedRootIdentity
        if (-not (Test-ProcessIdentityExactAlive -Identity $ExpectedRootIdentity)) {
            # Once an exact parent exits or its PID is reused, never discover
            # another process through the old numeric ParentProcessId.
            return @()
        }
    }
    else {
        try {
            $RootIdentity = Get-ProcessIdentity -Id $RootId
        }
        catch {
            return @()
        }
    }
    $ChildrenByParent = @{}
    foreach ($Record in @(Get-CimInstance Win32_Process)) {
        $Parent = [int]$Record.ParentProcessId
        if (-not $ChildrenByParent.ContainsKey($Parent)) {
            $ChildrenByParent[$Parent] = New-Object System.Collections.ArrayList
        }
        [void]$ChildrenByParent[$Parent].Add($Record)
    }
    $Pending = New-Object System.Collections.Stack
    $Pending.Push($RootIdentity)
    $Identities = New-Object System.Collections.ArrayList
    $Seen = @{}
    $RootKey = "$($RootIdentity.Id):$($RootIdentity.CreationTimeUtc)"
    $Seen[$RootKey] = $true
    [void]$Identities.Add($RootIdentity)
    while ($Pending.Count -gt 0) {
        $ParentIdentity = $Pending.Pop()
        if (-not (Test-ProcessIdentityExactAlive -Identity $ParentIdentity)) {
            continue
        }
        if ($ChildrenByParent.ContainsKey($ParentIdentity.Id)) {
            foreach ($ChildRecord in $ChildrenByParent[$ParentIdentity.Id]) {
                if (-not (Test-ProcessIdentityExactAlive -Identity $ParentIdentity)) {
                    break
                }
                try {
                    $ChildIdentity = Get-ProcessIdentity `
                        -Id ([int]$ChildRecord.ProcessId)
                }
                catch {
                    continue
                }
                if (-not (Test-ProcessIdentityExactAlive -Identity $ParentIdentity)) {
                    break
                }
                $FreshChildRecord = Get-CimProcessRecordById -Id $ChildIdentity.Id
                if ($null -eq $FreshChildRecord) {
                    continue
                }
                if (-not (Test-ProcessIdentityExactAlive -Identity $ParentIdentity)) {
                    break
                }
                if (-not (Test-CimChildRecordBound -SnapshotRecord $ChildRecord `
                    -FreshRecord $FreshChildRecord -ParentIdentity $ParentIdentity `
                    -ChildIdentity $ChildIdentity)) {
                    continue
                }
                if (-not (Test-OwnedProcessEdge -ParentIdentity $ParentIdentity `
                    -ChildIdentity $ChildIdentity)) {
                    continue
                }
                $ChildKey = "$($ChildIdentity.Id):$($ChildIdentity.CreationTimeUtc)"
                if ($Seen.ContainsKey($ChildKey)) {
                    continue
                }
                $Seen[$ChildKey] = $true
                [void]$Identities.Add($ChildIdentity)
                $Pending.Push($ChildIdentity)
            }
        }
    }
    return @($Identities)
}

function Update-OwnedProcessForest {
    param([Parameter(Mandatory = $true)][hashtable]$Identities)
    $Parents = @($Identities.Values)
    $Discovered = New-Object System.Collections.ArrayList
    foreach ($ParentIdentity in $Parents) {
        foreach ($Identity in @(
            Get-OwnedProcessTree -RootId $ParentIdentity.Id `
                -ExpectedRootIdentity $ParentIdentity
        )) {
            $Key = "$($Identity.Id):$($Identity.CreationTimeUtc)"
            if ($Identities.ContainsKey($Key)) {
                continue
            }
            $Identities[$Key] = $Identity
            Register-OwnedIdentity -Identity $Identity
            [void]$Discovered.Add($Identity)
        }
    }
    return @($Discovered)
}

function Assert-StaleParentPidEdgeRejected {
    $ReusedRoot = [pscustomobject]@{
        Id = 4242
        Path = "C:\fixture\new-root.exe"
        CreationTimeUtc = 200
    }
    $StaleChild = [pscustomobject]@{
        Id = 4343
        Path = (Join-Path ([System.IO.Path]::GetTempPath()) "stale-child.exe")
        CreationTimeUtc = 199
    }
    $Accepted = Test-OwnedProcessEdge -ParentIdentity $ReusedRoot `
        -ChildIdentity $StaleChild
    $AcceptedIdentities = @()
    if ($Accepted) {
        $AcceptedIdentities += $StaleChild
    }
    $TemporaryClones = @($AcceptedIdentities)
    Write-SmokeEvidence -Category "process-tree" -Name "stale-parent-pid-edge.json" `
        -Value ([ordered]@{
            verifiedParent = $ReusedRoot
            staleChild = $StaleChild
            accepted = $Accepted
            identities = $AcceptedIdentities
            temporaryClones = $TemporaryClones
        })
    Assert-True (
        -not $Accepted -and
        $AcceptedIdentities.Count -eq 0 -and
        $TemporaryClones.Count -eq 0
    ) `
        "a stale child created before a reused parent PID entered the owned tree"
}

function Assert-ReusedParentExitSequenceRejected {
    $P1 = [pscustomobject]@{
        Id = 5151
        Path = "C:\fixture\p1.exe"
        CreationTimeUtc = 100
    }
    $P2 = [pscustomobject]@{
        Id = 5151
        Path = "C:\fixture\p2.exe"
        CreationTimeUtc = 200
    }
    $C = [pscustomobject]@{
        Id = 5252
        Path = (Join-Path ([System.IO.Path]::GetTempPath()) "later-child-c.exe")
        CreationTimeUtc = 201
    }
    $Sequence = @(
        [pscustomobject]@{ event = "p1-exited"; currentParent = $null; child = $null },
        [pscustomobject]@{
            event = "pid-reused-by-p2"
            currentParent = $P2
            child = $null
        },
        [pscustomobject]@{
            event = "p2-spawned-later-c"
            currentParent = $P2
            child = $C
        },
        [pscustomobject]@{ event = "p2-exited"; currentParent = $null; child = $C }
    )
    $Decisions = @()
    $AcceptedIdentities = @()
    foreach ($Step in $Sequence) {
        $ParentExact = Test-ProcessIdentityExactMatch -ExpectedIdentity $P1 `
            -CurrentIdentity $Step.currentParent
        $CreationOrdered = (
            $null -ne $Step.child -and
            (Test-OwnedProcessEdge -ParentIdentity $P1 -ChildIdentity $Step.child)
        )
        $Accepted = $ParentExact -and $CreationOrdered
        if ($Accepted) {
            $AcceptedIdentities += $Step.child
        }
        $Decisions += [pscustomobject]@{
            event = $Step.event
            parentExactAlive = $ParentExact
            childCreationOrdered = $CreationOrdered
            accepted = $Accepted
        }
    }
    $TempRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::GetTempPath()
    ).TrimEnd("\") + "\"
    $TemporaryClones = @(
        $AcceptedIdentities |
            Where-Object {
                [System.IO.Path]::GetFullPath($_.Path).StartsWith(
                    $TempRoot,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    Write-SmokeEvidence -Category "process-tree" `
        -Name "reused-parent-exit-sequence.json" `
        -Value ([ordered]@{
            verifiedP1 = $P1
            reusedP2 = $P2
            laterChildC = $C
            decisions = $Decisions
            acceptedIdentities = $AcceptedIdentities
            temporaryClones = $TemporaryClones
        })
    Assert-True (
        $AcceptedIdentities.Count -eq 0 -and
        $TemporaryClones.Count -eq 0
    ) "a later child of a reused parent PID entered the owned tree"
}

function Assert-ChildPidReuseSequenceRejected {
    $Parent = [pscustomobject]@{
        Id = 6161
        Path = "C:\fixture\exact-parent.exe"
        CreationTimeUtc = 1000
    }
    $AStarted = [DateTime]::new(
        2026, 7, 28, 10, 0, 0, [DateTimeKind]::Utc
    )
    $BStarted = $AStarted.AddSeconds(1)
    $SnapshotA = [pscustomobject]@{
        ProcessId = 6262
        ParentProcessId = $Parent.Id
        ExecutablePath = "C:\fixture\snapshot-child-a.exe"
        CreationDate = $AStarted
    }
    $ReusedB = [pscustomobject]@{
        Id = 6262
        Path = (Join-Path ([System.IO.Path]::GetTempPath()) "unrelated-b.exe")
        CreationTimeUtc = $BStarted.Ticks
    }
    $FreshB = [pscustomobject]@{
        ProcessId = $ReusedB.Id
        ParentProcessId = 9999
        ExecutablePath = $ReusedB.Path
        CreationDate = $BStarted
    }
    $ParentExact = Test-ProcessIdentityExactMatch -ExpectedIdentity $Parent `
        -CurrentIdentity $Parent
    $ChildBound = Test-CimChildRecordBound -SnapshotRecord $SnapshotA `
        -FreshRecord $FreshB -ParentIdentity $Parent -ChildIdentity $ReusedB
    $Accepted = $ParentExact -and $ChildBound
    $AcceptedIdentities = @()
    if ($Accepted) {
        $AcceptedIdentities += $ReusedB
    }
    $TemporaryClones = @($AcceptedIdentities)
    Write-SmokeEvidence -Category "process-tree" `
        -Name "child-pid-reuse-sequence.json" `
        -Value ([ordered]@{
            sequence = @(
                "snapshot-child-a",
                "child-a-exited",
                "pid-reused-by-unrelated-b"
            )
            exactParent = $Parent
            snapshotA = $SnapshotA
            freshB = $FreshB
            currentIdentityB = $ReusedB
            parentExactAlive = $ParentExact
            childRecordBound = $ChildBound
            acceptedIdentities = $AcceptedIdentities
            temporaryClones = $TemporaryClones
        })
    Assert-True (
        -not $Accepted -and
        $AcceptedIdentities.Count -eq 0 -and
        $TemporaryClones.Count -eq 0
    ) "a reused child PID entered the owned tree"
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
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [AllowEmptyCollection()][string[]]$Arguments = @()
    )
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

function Assert-NonEmptyLiteralFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Category
    )
    Assert-True (Test-Path -LiteralPath $Path -PathType Leaf) `
        "$Category did not create its exact special-character log path"
    $Item = Get-Item -LiteralPath $Path -Force
    Assert-True ($Item.Length -gt 0) "$Category created an empty log"
    Write-SmokeEvidence -Category "installer-logs" `
        -Name (
            ([System.IO.Path]::GetFileName($Path) -replace '[^A-Za-z0-9.-]', '-') +
            "-sha256-$((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash).json"
        ) `
        -Value ([ordered]@{
            path = $Item.FullName
            length = $Item.Length
            sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        })
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
    Assert-NonEmptyLiteralFile -Path $LogPath -Category "Windows Setup"
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
        $InitialSnapshot = @(
            Update-OwnedProcessForest -Identities $Identities
        )

        $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        while (-not $Process.HasExited -and [DateTime]::UtcNow -lt $Deadline) {
            $null = Update-OwnedProcessForest -Identities $Identities
            Start-Sleep -Milliseconds 25
        }
        if (-not $Process.HasExited) {
            Stop-OwnedProcessTree -RootId $Process.Id
            throw "$Category exceeded its outer harness deadline"
        }

        # Only already-captured identities may discover another generation.
        # An exited or PID-reused parent fails closed in Get-OwnedProcessTree.
        $null = Update-OwnedProcessForest -Identities $Identities
        $TreeDeadline = [DateTime]::UtcNow.AddSeconds(30)
        while ($true) {
            $null = Update-OwnedProcessForest -Identities $Identities
            $LiveIdentities = @(
                $Identities.Values |
                    Where-Object { Test-ProcessIdentityAlive -Identity $_ }
            )
            if ($LiveIdentities.Count -eq 0) {
                break
            }
            if ([DateTime]::UtcNow -ge $TreeDeadline) {
                foreach ($Identity in $LiveIdentities) {
                    Stop-OwnedProcessIdentity -Identity $Identity
                }
                throw "$Category left a captured uninstaller process tree alive"
            }
            Start-Sleep -Milliseconds 25
        }
        $TempRoot = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::GetTempPath()
        ).TrimEnd("\") + "\"
        $TemporaryClones = @(
            $Identities.Values |
                Where-Object {
                    $_.Id -ne $RootIdentity.Id -and
                    -not $_.Path.Equals(
                        $RootIdentity.Path,
                        [System.StringComparison]::OrdinalIgnoreCase
                    ) -and
                    [System.IO.Path]::GetFullPath($_.Path).StartsWith(
                        $TempRoot,
                        [System.StringComparison]::OrdinalIgnoreCase
                    )
                }
        )
        Write-SmokeEvidence -Category "uninstall" `
            -Name (
                "uninstaller-process-tree-pid-$($Process.Id)-" +
                "created-$($RootIdentity.CreationTimeUtc).json"
            ) `
            -Value ([ordered]@{
                identities = @($Identities.Values)
                temporaryClones = $TemporaryClones
                exitCode = $Process.ExitCode
                elapsedMilliseconds = $Stopwatch.ElapsedMilliseconds
            })
        return [pscustomobject]@{
            ExitCode = $Process.ExitCode
            Identities = @($Identities.Values)
            TemporaryClones = $TemporaryClones
        }
    }
    finally {
        $Stopwatch.Stop()
    }
}

function Assert-DiagnosticsTreeHasNoPrimaryArtifacts {
    $Forbidden = @(
        Get-ChildItem -LiteralPath $SmokeRoot -File -Recurse -Force |
            Where-Object {
                $_.Name -in @("AACC.exe", "aacc-spawn.exe") -or
                $_.Name -like "*-Setup.exe" -or
                $_.Name -like "*.sha256" -or
                $_.Name -like "*.zip"
            } |
            ForEach-Object { $_.FullName }
    )
    Write-SmokeEvidence -Category "artifact-isolation" `
        -Name "diagnostics-primary-artifacts.json" -Value $Forbidden
    Assert-True ($Forbidden.Count -eq 0) `
        "always-uploaded diagnostics tree contains a primary product artifact"
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
        $TreeResult = Wait-UninstallerTreeGone -Process $Process -RootIdentity $Identity `
            -TimeoutSeconds 180 -Category "Windows uninstaller"
    }
    finally {
        $Process.Dispose()
    }
    Assert-NonEmptyLiteralFile -Path $LogPath -Category "Windows uninstaller"
    $ExitCode = $TreeResult.ExitCode
    if ($ExpectSuccess) {
        Assert-True ($ExitCode -eq 0) "Windows uninstaller failed"
        Assert-True (@($TreeResult.TemporaryClones).Count -gt 0) `
            "successful uninstaller did not expose a verified temporary clone"
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

function Assert-InstalledRootPayloadHashes {
    param([Parameter(Mandatory = $true)][string]$EvidenceCategory)
    $Evidence = @()
    foreach ($Leaf in @("AACC.exe", "aacc-spawn.exe")) {
        $BuiltPath = Join-Path $DistRoot $Leaf
        $InstalledPath = Join-Path $InstallRoot $Leaf
        Assert-True (Test-Path -LiteralPath $InstalledPath -PathType Leaf) `
            "installed root payload is missing $Leaf"
        $BuiltHash = (Get-FileHash -LiteralPath $BuiltPath -Algorithm SHA256).Hash
        $InstalledHash = (Get-FileHash -LiteralPath $InstalledPath -Algorithm SHA256).Hash
        $Evidence += [ordered]@{
            leaf = $Leaf
            builtSha256 = $BuiltHash
            installedSha256 = $InstalledHash
        }
        Assert-True ($InstalledHash -ceq $BuiltHash) `
            "installed root payload hash differs from dist for $Leaf"
    }
    Write-SmokeEvidence -Category $EvidenceCategory -Name "root-payload-hashes.json" `
        -Value $Evidence
}

function Set-SmokeDatabaseRow {
    $env:AACC_SMOKE_DATABASE = Join-Path $AppDataRoot "aacc.db"
    $Code = (
        "import os, sqlite3; " +
        "c=sqlite3.connect(os.environ['AACC_SMOKE_DATABASE']); " +
        "c.execute('CREATE TABLE IF NOT EXISTS aacc_smoke_preservation " +
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)'); " +
        "c.execute('INSERT OR REPLACE INTO aacc_smoke_preservation(key,value) " +
        "VALUES (?,?)', ('review-gate','semantic-row-v1')); c.commit(); c.close()"
    )
    try {
        $ExitCode = Invoke-ExternalDeadline -FilePath $PythonPath -Arguments @("-c", $Code) `
            -TimeoutSeconds 30 -Category "smoke database row write"
        Assert-True ($ExitCode -eq 0) "smoke database row creation failed"
    }
    finally {
        Remove-Item Env:AACC_SMOKE_DATABASE -ErrorAction SilentlyContinue
    }
}

function Assert-SmokeDatabaseRow {
    $env:AACC_SMOKE_DATABASE = Join-Path $AppDataRoot "aacc.db"
    $Code = (
        "import os, sqlite3; " +
        "c=sqlite3.connect(os.environ['AACC_SMOKE_DATABASE']); " +
        "r=c.execute('SELECT value FROM aacc_smoke_preservation WHERE key=?', " +
        "('review-gate',)).fetchone(); c.close(); " +
        "assert r == ('semantic-row-v1',), r"
    )
    try {
        $ExitCode = Invoke-ExternalDeadline -FilePath $PythonPath -Arguments @("-c", $Code) `
            -TimeoutSeconds 30 -Category "smoke database row read"
        Assert-True ($ExitCode -eq 0) "smoke database preservation row changed"
    }
    finally {
        Remove-Item Env:AACC_SMOKE_DATABASE -ErrorAction SilentlyContinue
    }
}

function Get-StableAppDataState {
    $State = [ordered]@{}
    foreach ($Leaf in @("preserve-me.txt", "config.yaml", "kimi-credentials.json")) {
        $Path = Join-Path $AppDataRoot $Leaf
        Assert-True (Test-Path -LiteralPath $Path -PathType Leaf) `
            "stable AppData file is missing: $Leaf"
        $State[$Leaf] = [ordered]@{
            length = (Get-Item -LiteralPath $Path -Force).Length
            sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        }
    }
    return [pscustomobject]$State
}

function Assert-StableAppDataState {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$EvidenceCategory
    )
    $Actual = Get-StableAppDataState
    Write-SmokeEvidence -Category $EvidenceCategory -Name "stable-appdata.json" `
        -Value ([ordered]@{ expected = $Expected; actual = $Actual })
    Assert-True (
        (ConvertTo-Json -InputObject $Actual -Depth 4 -Compress) -ceq
        (ConvertTo-Json -InputObject $Expected -Depth 4 -Compress)
    ) "stable AppData config, credentials, or sentinel content changed"
    Assert-SmokeDatabaseRow
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
        $ExitCode = if ($Owned.Process.HasExited) { $Owned.Process.ExitCode } else { $null }
        Write-SmokeEvidence -Category $Category -Name "product-exit.json" `
            -Value ([ordered]@{
                exited = $Owned.Process.HasExited
                exitCode = $ExitCode
            })
        if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
            try {
                Write-SmokeEvidence -Category $Category -Name "app-log.txt" `
                    -Value ([System.IO.File]::ReadAllText($LogPath))
            }
            catch {
                Write-SmokeEvidence -Category $Category -Name "app-log-copy-error.txt" `
                    -Value $_.Exception.GetType().Name
            }
        }
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
    # The shutdown protocol is delivered to the real Win32 HWND. Qt's offscreen
    # plugin has no native window, so product smoke must use the Windows plugin.
    $env:QT_QPA_PLATFORM = "windows"
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
    # Keep installed-product smoke on a real Win32 HWND for graceful shutdown.
    $env:QT_QPA_PLATFORM = "windows"
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
    $SavedAacc = Join-Path $CandidateRoot "product-smoke\saved-AACC.exe"
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $SavedAacc)) | Out-Null
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
    $LifecycleEvidence = Join-Path $SmokeRoot `
        "$Action\$Scenario\$SpecialLeaf\legacy-lifecycle-evidence.jsonl"
    Remove-Item -LiteralPath $LifecycleEvidence -Force -ErrorAction SilentlyContinue
    $StopEventName = (
        "Local\AACC.LegacySmokeStop." + [guid]::NewGuid().ToString("N")
    )
    $CreatedNew = $false
    $StopEvent = [System.Threading.EventWaitHandle]::new(
        $false,
        [System.Threading.EventResetMode]::ManualReset,
        $StopEventName,
        [ref]$CreatedNew
    )
    $Legacy = $null
    try {
        Assert-True $CreatedNew "legacy fixture stop event name collided"
        $env:AACC_LEGACY_STOP_EVENT = $StopEventName
        $env:AACC_LEGACY_LIFECYCLE_FILE = $LifecycleEvidence
        try {
            $Legacy = Start-OwnedProcess -FilePath $InstalledAacc
        }
        finally {
            # The test-only stop channel belongs only to the already-started
            # main fixture. Setup, Uninstall, and control children must not
            # inherit it.
            Remove-Item Env:AACC_LEGACY_STOP_EVENT -ErrorAction SilentlyContinue
            Remove-Item Env:AACC_LEGACY_LIFECYCLE_FILE -ErrorAction SilentlyContinue
        }
        $ReadyRecord = $null
        $ReadyDeadline = [DateTime]::UtcNow.AddSeconds(5)
        while ($null -eq $ReadyRecord -and [DateTime]::UtcNow -lt $ReadyDeadline) {
            if (Test-Path -LiteralPath $LifecycleEvidence -PathType Leaf) {
                foreach ($LifecycleLine in @(
                    Get-Content -LiteralPath $LifecycleEvidence -Encoding UTF8
                )) {
                    if ([string]::IsNullOrWhiteSpace($LifecycleLine)) {
                        continue
                    }
                    try {
                        $CandidateRecord = $LifecycleLine | ConvertFrom-Json
                    }
                    catch {
                        continue
                    }
                    if ($CandidateRecord.stage -ceq "ready") {
                        $ReadyRecord = $CandidateRecord
                        break
                    }
                }
            }
            if ($null -eq $ReadyRecord) {
                if ($Legacy.Process.HasExited) {
                    break
                }
                Start-Sleep -Milliseconds 50
            }
        }
        Assert-True ($null -ne $ReadyRecord) `
            "legacy fixture did not report its stop-event readiness"
        $ReadyIdentity = [pscustomobject]@{
            Id = [int]$ReadyRecord.pid
            Path = [string]$ReadyRecord.image_path
            CreationTimeUtc = [DateTime]::FromFileTimeUtc(
                [int64]$ReadyRecord.creation_time
            ).Ticks
        }
        Assert-True (
            Test-ProcessIdentityExactMatch -ExpectedIdentity $Legacy.Identity `
                -CurrentIdentity $ReadyIdentity
        ) "legacy fixture ready identity differs from its captured process"
        Assert-True (-not $Legacy.Process.HasExited) "legacy fixture did not own its window"
        $Before = Get-FullStateManifest
        Write-SmokeEvidence -Category "$Action\$Scenario" -Name "before-manifest.json" `
            -Value $Before
        $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        if ($Action -eq "setup") {
            Invoke-Setup -LogPath (
                Join-Path $SmokeRoot "reinstall\$Scenario-setup.log"
            ) `
                -ExpectSuccess $false
        }
        else {
            Invoke-Uninstaller `
                -LogPath (Join-Path $SmokeRoot "uninstall\$Scenario-uninstall.log") `
                -ExpectSuccess $false
        }
        $Stopwatch.Stop()
        $CurrentIdentity = $null
        $IdentityError = $null
        try {
            $CurrentIdentity = Get-ProcessIdentity -Id $Legacy.Identity.Id
        }
        catch {
            $IdentityError = [ordered]@{
                type = $_.Exception.GetType().FullName
                hresult = $_.Exception.HResult
            }
        }
        $ProcessHasExited = $Legacy.Process.HasExited
        $ObservedExitCode = if ($ProcessHasExited) {
            $Legacy.Process.ExitCode
        }
        else {
            $null
        }
        $ExactIdentityAlive = Test-ProcessIdentityAlive -Identity $Legacy.Identity
        Write-SmokeEvidence -Category "$Action\$Scenario" `
            -Name "post-refusal-identity.json" -Value ([ordered]@{
                captured = $Legacy.Identity
                processHasExited = $ProcessHasExited
                exitCode = $ObservedExitCode
                exactIdentityAlive = $ExactIdentityAlive
                current = $CurrentIdentity
                error = $IdentityError
                utc = [DateTime]::UtcNow.ToString("o")
                tick = [Environment]::TickCount64
            })
        $After = Get-FullStateManifest
        Write-SmokeEvidence -Category "$Action\$Scenario" -Name "after-manifest.json" `
            -Value $After
        Assert-True ($After -ceq $Before) "$Action refusal mutated installed state"
        Assert-True $ExactIdentityAlive `
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
                    $Stopwatch.ElapsedMilliseconds -ge 23000 -and
                    $Stopwatch.ElapsedMilliseconds -le 35000
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
        Remove-Item Env:AACC_LEGACY_STOP_EVENT -ErrorAction SilentlyContinue
        Remove-Item Env:AACC_LEGACY_LIFECYCLE_FILE -ErrorAction SilentlyContinue
        try {
            if ($null -ne $Legacy) {
                [void]$StopEvent.Set()
                Assert-ProcessExitedByDeadline -Identity $Legacy.Identity -TimeoutSeconds 10
                Assert-True ($Legacy.Process.ExitCode -eq 0) `
                    "legacy fixture did not exit through its harness stop event"
            }
        }
        finally {
            if ($null -ne $Legacy) {
                $Legacy.Process.Dispose()
            }
            $StopEvent.Dispose()
        }
        if ($null -ne $Legacy) {
            Restore-LiteralFileAfterProcessExit -Source $SavedAacc `
                -Destination $InstalledAacc -Identity $Legacy.Identity
        }
        else {
            [System.IO.File]::Copy($SavedAacc, $InstalledAacc, $true)
        }
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
$CandidateRoot = Join-Path $Root "build\candidate-validation"
if (Test-Path -LiteralPath $CandidateRoot) {
    Remove-Item -LiteralPath $CandidateRoot -Recurse -Force
}
[System.IO.Directory]::CreateDirectory($CandidateRoot) | Out-Null
foreach ($Category in @("frozen", "installed", "reinstall", "uninstall")) {
    [System.IO.Directory]::CreateDirectory((Join-Path $SmokeRoot $Category)) | Out-Null
}
Assert-StaleParentPidEdgeRejected
Assert-ReusedParentExitSequenceRejected
Assert-ChildPidReuseSequenceRejected
$FixtureRoot = Join-Path $SmokeRoot "fixtures\$SpecialLeaf\native &() %! [x]"
[System.IO.Directory]::CreateDirectory($FixtureRoot) | Out-Null
foreach ($Fixture in @("fake-codex.cmd", "fake_codex_server.py", "fake_codex_timeout.py")) {
    Copy-Item -LiteralPath (Join-Path $Root "tests\windows\$Fixture") `
        -Destination (Join-Path $FixtureRoot $Fixture)
}
$FakeCodexCmd = Join-Path $FixtureRoot "fake-codex.cmd"
$LegacyFixture = Join-Path $FixtureRoot "legacy-window-fixture.exe"
$LockerFixture = Join-Path $FixtureRoot "lock payload.exe"

Write-Host "AACC_WINDOWS_SMOKE evidence=hosted-Windows-Server"
Invoke-FrozenSmoke
Assert-DiagnosticsTreeHasNoPrimaryArtifacts

if ($FrozenOnly) {
    Write-Host "Hosted Windows Server evidence only; consumer Windows 10/11 not claimed"
    return
}

$Version = ((& uv version --short | Select-Object -First 1) | Out-String).Trim()
$SetupSource = Join-Path $Root "dist\installer\AACC-$Version-Setup.exe"
$ChecksumSource = "$SetupSource.sha256"
Assert-True (Test-Path -LiteralPath $SetupSource -PathType Leaf) "Setup is missing"
Assert-True (Test-Path -LiteralPath $ChecksumSource -PathType Leaf) "Setup checksum is missing"
$SpecialSetupRoot = Join-Path $CandidateRoot "product-smoke\$SpecialLeaf\setup copy &() %! [x]"
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
Assert-InstalledRootPayloadHashes -EvidenceCategory "installed"
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
[System.IO.File]::WriteAllBytes(
    (Join-Path $AppDataRoot "preserve-me.txt"),
    [System.Text.UTF8Encoding]::new($false).GetBytes("preserve-v1`n")
)
Set-SmokeDatabaseRow
$StableAppData = Get-StableAppDataState
Assert-StableAppDataState -Expected $StableAppData -EvidenceCategory "installed"

$InstalledInternalRoot = Join-Path $InstallRoot "_internal"
$InternalBackupRoot = Join-Path $CandidateRoot "product-smoke\internal-root-backup"
$ExternalJunctionRoot = Join-Path $CandidateRoot "product-smoke\junction-target"
[System.IO.Directory]::CreateDirectory($ExternalJunctionRoot) | Out-Null
$ExternalPreserveMarker = Join-Path `
    $ExternalJunctionRoot "junction-external-preserve.txt"
[System.IO.File]::WriteAllBytes(
    $ExternalPreserveMarker,
    [System.Text.UTF8Encoding]::new($false).GetBytes("external-preserve-v1`n")
)
$ExternalBefore = Get-TreeManifest -Path $ExternalJunctionRoot
Move-Item -LiteralPath $InstalledInternalRoot -Destination $InternalBackupRoot
try {
    New-Item -ItemType Junction -Path $InstalledInternalRoot `
        -Target $ExternalJunctionRoot | Out-Null
    $JunctionItem = Get-Item -LiteralPath $InstalledInternalRoot -Force
    Assert-True (
        ($JunctionItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) "_internal junction fixture is not a reparse point"
    $JunctionRequestedLog = Join-Path $SmokeRoot "reinstall\junction-refusal.log"
    Invoke-Setup -LogPath $JunctionRequestedLog `
        -ExpectSuccess $false
    $JunctionLog = Get-SpecialSmokePath -RequestedPath $JunctionRequestedLog
    Assert-True (
        [System.IO.File]::ReadAllText($JunctionLog).Contains(
            "AACC internal payload root is unsafe"
        )
    ) "junction refusal log did not prove the pre-install safety gate"
    $ExternalAfter = Get-TreeManifest -Path $ExternalJunctionRoot
    Write-SmokeEvidence -Category "reinstall\junction-refusal" `
        -Name "junction-refusal-external-manifest.json" `
        -Value ([ordered]@{ before = $ExternalBefore; after = $ExternalAfter })
    Assert-True ($ExternalAfter -ceq $ExternalBefore) `
        "Setup traversed or mutated the external _internal junction target"
}
finally {
    if (Test-Path -LiteralPath $InstalledInternalRoot) {
        $InternalRootItem = Get-Item -LiteralPath $InstalledInternalRoot -Force
        Assert-True (
            ($InternalRootItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) "unsafe cleanup refused to delete a non-reparse _internal root"
        [System.IO.Directory]::Delete($InstalledInternalRoot)
    }
    Assert-True (Test-Path -LiteralPath $ExternalPreserveMarker -PathType Leaf) `
        "junction cleanup removed the external preserve marker"
    Move-Item -LiteralPath $InternalBackupRoot -Destination $InstalledInternalRoot
}
Assert-InstalledInternalMatchesManifest -EvidenceCategory "reinstall\junction-refusal"
Assert-InstalledRootPayloadHashes -EvidenceCategory "reinstall\junction-refusal"

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
Assert-StableAppDataState -Expected $StableAppData -EvidenceCategory "reinstall\running"
Assert-InstalledInternalMatchesManifest -EvidenceCategory "reinstall\running"
Assert-InstalledRootPayloadHashes -EvidenceCategory "reinstall\running"

$StalePayload = Join-Path $InstallRoot "_internal\stale-obsolete.pyd"
[System.IO.File]::WriteAllBytes($StalePayload, [byte[]](9, 8, 7, 6))
$StaleReady = Join-Path $SmokeRoot "reinstall\stale-lock-ready.txt"
$StaleLocker = Start-OwnedProcess -FilePath $LockerFixture -Arguments @(
    $StalePayload,
    $StaleReady
)
try {
    Wait-LiteralPath -Path $StaleReady -TimeoutSeconds 10 -Owner $StaleLocker.Process
    $StaleFailureRequestedLog = Join-Path `
        $SmokeRoot "reinstall\stale-locked-failure.log"
    Invoke-Setup -LogPath $StaleFailureRequestedLog `
        -ExpectSuccess $false
}
finally {
    Stop-OwnedProcessIdentity -Identity $StaleLocker.Identity
    $StaleLocker.Process.Dispose()
}
Assert-True (Test-Path -LiteralPath $StalePayload -PathType Leaf) `
    "locked stale payload unexpectedly disappeared"
$StaleFailureLog = Get-SpecialSmokePath -RequestedPath $StaleFailureRequestedLog
Assert-True (
    [System.IO.File]::ReadAllText($StaleFailureLog).Contains(
        "AACC_MANIFEST_CLEANUP result=incomplete"
    )
) "stale cleanup failure log did not record an explicit incomplete result"
Assert-StableAppDataState -Expected $StableAppData `
    -EvidenceCategory "reinstall\stale-locked-failure"
$BeforeStoppedSuccess = Get-AppDataManifest
Invoke-Setup -LogPath (Join-Path $SmokeRoot "reinstall\stale-unlocked-success.log") `
    -ExpectSuccess $true
$AfterStoppedSuccess = Get-AppDataManifest
Write-SmokeEvidence -Category "reinstall\stale-unlocked-success" `
    -Name "appdata-manifest.json" `
    -Value ([ordered]@{ before = $BeforeStoppedSuccess; after = $AfterStoppedSuccess })
Assert-True ($AfterStoppedSuccess -ceq $BeforeStoppedSuccess) `
    "stopped successful reinstall changed the complete AppData manifest"
Assert-True (-not (Test-Path -LiteralPath $StalePayload)) `
    "unlocked stale payload survived successful manifest cleanup"
Assert-StableAppDataState -Expected $StableAppData `
    -EvidenceCategory "reinstall\stale-unlocked-success"
Assert-InstalledInternalMatchesManifest -EvidenceCategory "reinstall\stale-unlocked-success"
Assert-InstalledRootPayloadHashes -EvidenceCategory "reinstall\stale-unlocked-success"

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
$InternalInstalledRoot = Join-Path $InstallRoot "_internal"
$RollbackProbeRelative = $RollbackProbePath.Substring($InternalInstalledRoot.Length + 1)
$BuiltRollbackProbePath = Join-Path (Join-Path $DistRoot "_internal") $RollbackProbeRelative
$BuiltRollbackProbeBytes = [System.IO.File]::ReadAllBytes($BuiltRollbackProbePath)
Assert-True (
    [Convert]::ToBase64String($RollbackProbeBytes) -cne
    [Convert]::ToBase64String($BuiltRollbackProbeBytes)
) "rollback probe old bytes unexpectedly equal the packaged bytes"
$BeforeFault = Get-FullStateManifest
Write-SmokeEvidence -Category "reinstall\lock-fault" -Name "before-manifest.json" `
    -Value $BeforeFault
$PendingBefore = Get-PendingFileRenameOperations
$LockedPayload = $InstalledAacc
$LockReady = Join-Path $SmokeRoot "reinstall\lock-ready.txt"
$RollbackObserved = Join-Path $SmokeRoot `
    "reinstall\$SpecialLeaf\rollback-probe-observed.txt"
$Locker = Start-OwnedProcess -FilePath $LockerFixture -Arguments @(
    $LockedPayload,
    $LockReady,
    $RollbackProbePath,
    $BuiltRollbackProbePath,
    $RollbackObserved
)
try {
    Wait-LiteralPath -Path $LockReady -TimeoutSeconds 10 -Owner $Locker.Process
    $RollbackRequestedLog = Join-Path $SmokeRoot "reinstall\locked-failure.log"
    Invoke-Setup -LogPath $RollbackRequestedLog `
        -ExpectSuccess $false
    Wait-LiteralPath -Path $RollbackObserved -TimeoutSeconds 5 -Owner $Locker.Process
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
Assert-NonEmptyLiteralFile -Path $RollbackObserved `
    -Category "independent rollback probe observer"
$RollbackResolvedLog = Get-SpecialSmokePath -RequestedPath $RollbackRequestedLog
$RollbackLogText = [System.IO.File]::ReadAllText($RollbackResolvedLog)
Assert-True ($RollbackLogText -match '(?i)roll(?:ing)? back|rollback') `
    "installer log did not record rollback activity"
Assert-True (
    @(Get-ChildItem -LiteralPath (Split-Path -Parent $InstallRoot) -Force |
        Where-Object { $_.Name -like "AACC.aacc-*" }).Count -eq 0
) "failed reinstall left staging or backup residue"

$OldPayload = Invoke-InstalledLaunch -Category "reinstall\old-payload"
Invoke-ProductBrokerProbes -ProductRoot $InstallRoot -Category "reinstall\old-payload"
Invoke-GracefulShutdown -Executable $InstalledAacc -Owned $OldPayload
$BeforeAfterLockSuccess = Get-AppDataManifest
Invoke-Setup -LogPath (Join-Path $SmokeRoot "reinstall\after-lock-success.log") `
    -ExpectSuccess $true
$AfterAfterLockSuccess = Get-AppDataManifest
Assert-True ($AfterAfterLockSuccess -ceq $BeforeAfterLockSuccess) `
    "stopped successful reinstall after rollback changed AppData"
Assert-True (-not (Test-Path -LiteralPath $RollbackSentinel)) `
    "successful reinstall left a stale sentinel"
Assert-StableAppDataState -Expected $StableAppData `
    -EvidenceCategory "reinstall\after-lock-success"
Assert-InstalledInternalMatchesManifest -EvidenceCategory "reinstall"
Assert-InstalledRootPayloadHashes -EvidenceCategory "reinstall"

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
Assert-StableAppDataState -Expected $StableAppData -EvidenceCategory "uninstall"

[System.IO.Directory]::CreateDirectory($InstallRoot) | Out-Null
Copy-Item -LiteralPath $LegacyFixture -Destination $InstalledAacc
$BeforeStoppedLegacyAppData = Get-AppDataManifest
Invoke-Setup -LogPath (Join-Path $SmokeRoot "installed\stopped-legacy-install.log") `
    -ExpectSuccess $true
$AfterStoppedLegacyAppData = Get-AppDataManifest
Assert-True ($AfterStoppedLegacyAppData -ceq $BeforeStoppedLegacyAppData) `
    "stopped legacy install changed AppData"
Assert-True (Test-Path -LiteralPath $CapabilityPath) `
    "stopped legacy install did not become managed"
Assert-InstalledInternalMatchesManifest -EvidenceCategory "installed\stopped-legacy"
Assert-InstalledRootPayloadHashes -EvidenceCategory "installed\stopped-legacy"
Assert-StableAppDataState -Expected $StableAppData -EvidenceCategory "installed\stopped-legacy"
Invoke-Uninstaller -LogPath (Join-Path $SmokeRoot "uninstall\final-cleanup.log") `
    -ExpectSuccess $true

Assert-DiagnosticsTreeHasNoPrimaryArtifacts
Write-Host "Hosted Windows Server evidence only; consumer Windows 10/11 not claimed"
}

try {
    Invoke-SmokeMain
}
finally {
    Invoke-OwnedCleanup
}
