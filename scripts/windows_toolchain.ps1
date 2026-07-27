#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-AaccLocalPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { throw "path is empty" }
    foreach ($character in $Path.ToCharArray()) {
        if ([int][char]$character -ge 0 -and [int][char]$character -le 31) {
            throw "path contains a control character"
        }
    }
    if ($Path.StartsWith("\\") -or $Path.StartsWith("\\?\") -or $Path.StartsWith("\\.\")) {
        throw "path is not a local drive path"
    }
    if ($Path -notmatch '^[A-Za-z]:[\\/]') { throw "path is not drive rooted" }
    try { $fullPath = [System.IO.Path]::GetFullPath($Path) } catch { throw "path cannot be normalized" }
    if ($fullPath -notmatch '^[A-Za-z]:\\') { throw "path is not a local drive path" }
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if (-not $fullPath.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        $fullPath = $fullPath.TrimEnd([char[]]@([char]'\', [char]'/'))
    }
    return $fullPath
}

function ConvertTo-AaccVsCandidates {
    param([Parameter(Mandatory = $true)][string]$Json)

    try {
        $baseResult = ConvertFrom-Json -InputObject $Json -ErrorAction Stop
    } catch {
        throw "vswhere returned invalid JSON"
    }

    $instances = New-Object System.Collections.ArrayList
    if ($null -ne $baseResult) {
        if ($baseResult -is [System.Array]) {
            foreach ($instance in $baseResult) { [void]$instances.Add($instance) }
        } else {
            [void]$instances.Add($baseResult)
        }
    }

    $parsed = @()
    foreach ($instance in $instances) {
        try { $installationPath = ConvertTo-AaccLocalPath -Path ([string]$instance.installationPath) } catch { continue }
        $versionText = ([string]$instance.installationVersion).Trim()
        [version]$installationVersion = $null
        if (
            [string]::IsNullOrWhiteSpace($installationPath) -or
            -not [version]::TryParse($versionText, [ref]$installationVersion)
        ) {
            continue
        }
        $parsed += [pscustomobject]@{
            InstallationPath = $installationPath
            InstallationVersion = $installationVersion
            InstallationVersionText = $versionText
        }
    }

    $seenPaths = New-Object 'System.Collections.Generic.HashSet[string]' -ArgumentList ([System.StringComparer]::OrdinalIgnoreCase)
    $candidates = @()
    foreach (
        $candidate in @(
            $parsed | Sort-Object -Property @{ Expression = { $_.InstallationVersion }; Descending = $true }
        )
    ) {
        if ($seenPaths.Add($candidate.InstallationPath)) {
            $candidates += $candidate
        }
    }
    if ($candidates.Count -eq 0) {
        throw "vswhere returned no usable Visual Studio instances"
    }
    return $candidates
}

function Invoke-AaccProcessCapture {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $Arguments
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw "process could not be started" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $timeoutMilliseconds = $TimeoutSeconds * 1000
        if (-not $process.WaitForExit($timeoutMilliseconds)) {
            try { if (-not $process.HasExited) { $process.Kill() } } catch {}
            $null = $process.WaitForExit(5000)
            $null = $stdoutTask.Wait(5000)
            $null = $stderrTask.Wait(5000)
            return [pscustomobject]@{ ExitCode = -1; StdOut = ""; StdErr = ""; TimedOut = $true }
        }
        $readTasks = [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask)
        if (-not [System.Threading.Tasks.Task]::WaitAll($readTasks, $timeoutMilliseconds)) {
            throw "process output read timed out"
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            StdOut = $stdoutTask.Result
            StdErr = $stderrTask.Result
            TimedOut = $false
        }
    } finally {
        $process.Dispose()
    }
}

function Invoke-AaccVsWhereProcess {
    param(
        [Parameter(Mandatory = $true)][string]$VsWherePath,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    return Invoke-AaccProcessCapture -FilePath $VsWherePath `
        -Arguments "-all -prerelease -products * -format json -utf8" -TimeoutSeconds $TimeoutSeconds
}

function Get-AaccVsWhereCandidates {
    param(
        [Parameter(Mandatory = $true)][string]$VsWherePath,
        [int]$TimeoutSeconds = 30,
        [scriptblock]$ProcessRunner = $null
    )

    if ($TimeoutSeconds -le 0) {
        throw "vswhere timeout must be positive"
    }
    if ($null -eq $ProcessRunner) {
        $result = Invoke-AaccVsWhereProcess -VsWherePath $VsWherePath -TimeoutSeconds $TimeoutSeconds
    } else {
        $result = & $ProcessRunner $VsWherePath $TimeoutSeconds
    }
    if ($result.TimedOut) {
        throw "vswhere timed out"
    }
    if ($result.ExitCode -ne 0) {
        throw "vswhere exited unsuccessfully"
    }
    return ConvertTo-AaccVsCandidates -Json ([string]$result.StdOut)
}

function Get-AaccCandidateEnvironment {
    param([Parameter(Mandatory = $true)]$Candidate)

    $vsDevCmd = Join-Path $Candidate.InstallationPath "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $vsDevCmd -PathType Leaf)) {
        return [pscustomobject]@{ Success = $false; Reason = "missing-vsdevcmd"; Environment = $null }
    }

    $loader = Join-Path ([System.IO.Path]::GetTempPath()) ("aacc-vs-env-" + [guid]::NewGuid() + ".cmd")
    @(
        "@echo off"
        "call `"$vsDevCmd`" -no_logo -arch=x64 -host_arch=x64 >nul"
        "if errorlevel 1 exit /b %errorlevel%"
        "set"
    ) | Set-Content -LiteralPath $loader -Encoding ASCII
    try {
        $output = & $loader
        $exitCode = $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $loader -Force -ErrorAction SilentlyContinue
    }
    if ($exitCode -ne 0) {
        return [pscustomobject]@{ Success = $false; Reason = "vsdevcmd-failed"; Environment = $null }
    }

    $environment = @{}
    foreach ($line in $output) {
        $separator = $line.IndexOf("=")
        if ($separator -gt 0) {
            $environment[$line.Substring(0, $separator)] = $line.Substring($separator + 1)
        }
    }
    return [pscustomobject]@{ Success = $true; Reason = "ok"; Environment = $environment }
}

function Test-AaccPathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    [char[]]$trimCharacters = @([char]'\', [char]'/')
    $resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path.TrimEnd($trimCharacters)
    $resolvedRoot = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path.TrimEnd($trimCharacters)
    return $resolvedPath.StartsWith(
        ($resolvedRoot + '\'), [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Find-AaccToolPath {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string]$ToolName
    )

    $pathValue = [string]$Environment["PATH"]
    if ([string]::IsNullOrWhiteSpace($pathValue)) {
        throw "candidate environment has no PATH"
    }
    foreach ($directory in $pathValue -split ";") {
        $trimmedDirectory = $directory.Trim().Trim('"')
        if ([string]::IsNullOrWhiteSpace($trimmedDirectory)) {
            continue
        }
        $candidatePath = Join-Path $trimmedDirectory $ToolName
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidatePath -ErrorAction Stop).Path
        }
    }
    throw "candidate environment lacks required tool"
}

function Get-AaccToolPaths {
    param(
        [Parameter(Mandatory = $true)]$Candidate,
        [Parameter(Mandatory = $true)][hashtable]$Environment
    )

    if (
        $Environment["VSCMD_ARG_TGT_ARCH"] -ine "x64" -or
        $Environment["VSCMD_ARG_HOST_ARCH"] -ine "x64"
    ) {
        throw "candidate environment is not x64"
    }
    try {
        $candidateRoot = ConvertTo-AaccLocalPath -Path ([string]$Candidate.InstallationPath)
        $vcToolsRoot = ConvertTo-AaccLocalPath -Path ([string]$Environment["VCToolsInstallDir"])
        $windowsSdkRoot = ConvertTo-AaccLocalPath -Path ([string]$Environment["WindowsSdkDir"])
    } catch {
        throw "candidate environment lacks SDK roots"
    }
    if (-not (Test-AaccPathWithin -Path $vcToolsRoot -Root $candidateRoot)) {
        throw "VCToolsInstallDir is outside the candidate installation"
    }

    $tools = [ordered]@{}
    foreach ($toolName in @("cl.exe", "link.exe", "dumpbin.exe")) {
        $toolPath = Find-AaccToolPath -Environment $Environment -ToolName $toolName
        if (-not (Test-AaccPathWithin -Path $toolPath -Root $vcToolsRoot)) {
            throw "candidate compiler tool is outside VCToolsInstallDir"
        }
        $tools[$toolName] = $toolPath
    }
    $rcPath = Find-AaccToolPath -Environment $Environment -ToolName "rc.exe"
    if (-not (Test-AaccPathWithin -Path $rcPath -Root $windowsSdkRoot)) {
        throw "candidate resource compiler is outside WindowsSdkDir"
    }
    $tools["rc.exe"] = $rcPath
    return $tools
}

function Select-AaccMsvcToolchain {
    param(
        [Parameter(Mandatory = $true)][object[]]$Candidates,
        [scriptblock]$CandidateEnvironmentLoader = $null
    )

    if ($null -eq $CandidateEnvironmentLoader) {
        $CandidateEnvironmentLoader = { param($candidate) Get-AaccCandidateEnvironment -Candidate $candidate }
    }
    foreach ($candidate in $Candidates) {
        try {
            $environmentResult = & $CandidateEnvironmentLoader $candidate
            if (-not $environmentResult.Success) {
                Write-Host "AACC_MSVC_CANDIDATE version=$($candidate.InstallationVersionText) reason=environment-unavailable"
                continue
            }
        } catch {
            Write-Host "AACC_MSVC_CANDIDATE version=$($candidate.InstallationVersionText) reason=environment-load-failed"
            continue
        }
        try {
            $tools = Get-AaccToolPaths -Candidate $candidate -Environment $environmentResult.Environment
        } catch {
            Write-Host "AACC_MSVC_CANDIDATE version=$($candidate.InstallationVersionText) reason=tool-validation-failed"
            continue
        }
        Write-Host "AACC_MSVC_SELECTED version=$($candidate.InstallationVersionText)"
        return [pscustomobject]@{
            Environment = $environmentResult.Environment
            Tools = $tools
            InstallationVersion = $candidate.InstallationVersionText
        }
    }
    throw "no Visual Studio instance provides the required x64 MSVC tools"
}

function Set-AaccToolchainEnvironment {
    param([Parameter(Mandatory = $true)]$Toolchain)

    foreach ($name in $Toolchain.Environment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $Toolchain.Environment[$name], "Process")
    }
}
