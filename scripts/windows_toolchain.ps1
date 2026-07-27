#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-AaccVsCandidates {
    param([Parameter(Mandatory = $true)][string]$Json)

    try {
        $instances = @(ConvertFrom-Json -InputObject $Json -ErrorAction Stop)
    } catch {
        throw "vswhere returned invalid JSON"
    }

    $parsed = @()
    foreach ($instance in $instances) {
        $installationPath = ([string]$instance.installationPath).Trim()
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

    $seenPaths = @{}
    $candidates = @()
    foreach (
        $candidate in @(
            $parsed | Sort-Object -Property @{ Expression = { $_.InstallationVersion }; Descending = $true }
        )
    ) {
        if (-not $seenPaths.ContainsKey($candidate.InstallationPath)) {
            $seenPaths[$candidate.InstallationPath] = $true
            $candidates += $candidate
        }
    }
    if ($candidates.Count -eq 0) {
        throw "vswhere returned no usable Visual Studio instances"
    }
    return $candidates
}

function Invoke-AaccVsWhereProcess {
    param(
        [Parameter(Mandatory = $true)][string]$VsWherePath,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $VsWherePath
    $startInfo.Arguments = "-all -prerelease -products * -format json -utf8"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "vswhere could not be started"
    }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        $process.WaitForExit()
        return [pscustomobject]@{ ExitCode = -1; StdOut = ""; TimedOut = $true }
    }
    $stdout = $process.StandardOutput.ReadToEnd()
    $null = $process.StandardError.ReadToEnd()
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        StdOut = $stdout
        TimedOut = $false
    }
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
    param([Parameter(Mandatory = $true)][hashtable]$Environment)

    if (
        $Environment["VSCMD_ARG_TGT_ARCH"] -ine "x64" -or
        $Environment["VSCMD_ARG_HOST_ARCH"] -ine "x64"
    ) {
        throw "candidate environment is not x64"
    }
    $vcToolsRoot = [string]$Environment["VCToolsInstallDir"]
    $windowsSdkRoot = [string]$Environment["WindowsSdkDir"]
    if ([string]::IsNullOrWhiteSpace($vcToolsRoot) -or [string]::IsNullOrWhiteSpace($windowsSdkRoot)) {
        throw "candidate environment lacks SDK roots"
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
            $tools = Get-AaccToolPaths -Environment $environmentResult.Environment
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
