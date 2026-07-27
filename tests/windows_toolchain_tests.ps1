#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\windows_toolchain.ps1")

function Assert-True {
    param([Parameter(Mandatory = $true)][bool]$Condition, [Parameter(Mandatory = $true)][string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Throws {
    param([Parameter(Mandatory = $true)][scriptblock]$Action, [Parameter(Mandatory = $true)][string]$Message)
    try {
        & $Action
    } catch {
        return
    }
    throw $Message
}

$root = Join-Path ([System.IO.Path]::GetTempPath()) ("aacc-toolchain-test-" + [guid]::NewGuid())
try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null

    function New-ToolchainFixture {
        param(
            [Parameter(Mandatory = $true)][string]$Name,
            [Parameter(Mandatory = $true)][string]$Version,
            [bool]$CreateVsDevCmd = $true,
            [string]$MissingTool = ""
        )

        $installationPath = Join-Path $root $Name
        $vcToolsRoot = Join-Path $installationPath "VC\Tools\MSVC\test"
        $vcBin = Join-Path $vcToolsRoot "bin\Hostx64\x64"
        $windowsSdkRoot = Join-Path $installationPath "Windows Kits\10"
        $sdkBin = Join-Path $windowsSdkRoot "bin\x64"
        New-Item -ItemType Directory -Path $vcBin, $sdkBin -Force | Out-Null
        if ($CreateVsDevCmd) {
            $vsDevCmd = Join-Path $installationPath "Common7\Tools\VsDevCmd.bat"
            New-Item -ItemType Directory -Path (Split-Path -Parent $vsDevCmd) -Force | Out-Null
            Set-Content -LiteralPath $vsDevCmd -Value "@echo off" -Encoding ASCII
        }
        foreach ($tool in @("cl.exe", "link.exe", "dumpbin.exe")) {
            if ($tool -ne $MissingTool) {
                Set-Content -LiteralPath (Join-Path $vcBin $tool) -Value "tool" -Encoding ASCII
            }
        }
        if ("rc.exe" -ne $MissingTool) {
            Set-Content -LiteralPath (Join-Path $sdkBin "rc.exe") -Value "tool" -Encoding ASCII
        }
        return [pscustomobject]@{
            Candidate = [pscustomobject]@{
                InstallationPath = $installationPath
                InstallationVersionText = $Version
            }
            Environment = @{
                PATH = "$vcBin;$sdkBin"
                VCToolsInstallDir = $vcToolsRoot
                WindowsSdkDir = $windowsSdkRoot
                VSCMD_ARG_TGT_ARCH = "x64"
                VSCMD_ARG_HOST_ARCH = "x64"
                AACC_TOOLCHAIN_TEST = "candidate"
            }
        }
    }

    Assert-Throws -Message "empty JSON array did not fail closed" -Action {
        ConvertTo-AaccVsCandidates -Json "[]"
    }
    $singleObject = ConvertTo-AaccVsCandidates -Json '{"installationPath":"C:\\VS17","installationVersion":"17.9.0"}'
    Assert-True -Condition ($singleObject.Count -eq 1) -Message "single JSON object was not parsed"
    $singleElementArray = ConvertTo-AaccVsCandidates -Json '[{"installationPath":"C:\\VS17","installationVersion":"17.9.0"}]'
    Assert-True -Condition ($singleElementArray.Count -eq 1) -Message "single JSON array element was not parsed"
    $sorted = ConvertTo-AaccVsCandidates -Json @'
[
  {"installationPath":"C:\\VS17","installationVersion":"17.9.0"},
  {"installationPath":"C:\\VS18","installationVersion":"18.7.0"},
  {"installationPath":"c:\\vs18\\","installationVersion":"18.6.0"}
]
'@
    Assert-True -Condition ($sorted.Count -eq 2) -Message "candidates were not deduplicated"
    Assert-True -Condition ($sorted[0].InstallationVersionText -eq "18.7.0") -Message "candidates were not sorted"
    foreach ($unsafePath in @("relative", "\\\\server\\share", "\\\\?\\C:\\VS", "C:\\bad$([char]1)path")) {
        Assert-Throws -Message "unsafe candidate path was accepted" -Action {
            ConvertTo-AaccLocalPath -Path $unsafePath
        }
    }
    $normalized = ConvertTo-AaccLocalPath -Path "C:\\VS\\1\\..\\2\\"
    Assert-True -Condition ($normalized -eq "C:\\VS\\2") -Message "candidate path was not normalized"

    $prefixRoot = Join-Path $root "VS\1"
    $prefixCollision = Join-Path $root "VS\10\cl.exe"
    New-Item -ItemType Directory -Path $prefixRoot, (Split-Path -Parent $prefixCollision) -Force | Out-Null
    Set-Content -LiteralPath $prefixCollision -Value "tool" -Encoding ASCII
    Assert-True -Condition (-not (Test-AaccPathWithin -Path $prefixCollision -Root $prefixRoot)) -Message (
        "tool provenance accepted a path-prefix collision"
    )

    $newestMissingVsDevCmd = New-ToolchainFixture -Name "vs18-missing-devcmd" -Version "18.7.0" -CreateVsDevCmd $false
    $newerMissingTool = New-ToolchainFixture -Name "vs18-missing-tool" -Version "18.6.0" -MissingTool "dumpbin.exe"
    $olderValid = New-ToolchainFixture -Name "vs17-valid" -Version "17.9.0"
    $borrowedVc = New-ToolchainFixture -Name "vs17-borrowed-vc" -Version "17.8.0"
    $fixtures = @{}
    foreach ($fixture in @($newestMissingVsDevCmd, $newerMissingTool, $olderValid)) {
        $fixtures[$fixture.Candidate.InstallationPath] = $fixture
    }
    $borrowedEnvironment = @{}
    foreach ($key in $olderValid.Environment.Keys) { $borrowedEnvironment[$key] = $olderValid.Environment[$key] }
    $borrowedEnvironment["VCToolsInstallDir"] = $borrowedVc.Environment["VCToolsInstallDir"]
    $borrowedEnvironment["PATH"] = "$($borrowedVc.Environment["PATH"]);$($olderValid.Environment["WindowsSdkDir"])\\bin\\x64"
    Assert-Throws -Message "candidate accepted another Visual Studio VC root" -Action {
        Get-AaccToolPaths -Candidate $olderValid.Candidate -Environment $borrowedEnvironment
    }
    $oldSentinel = [Environment]::GetEnvironmentVariable("AACC_TOOLCHAIN_TEST", "Process")
    [Environment]::SetEnvironmentVariable("AACC_TOOLCHAIN_TEST", "parent", "Process")
    try {
        $selected = Select-AaccMsvcToolchain -Candidates @(
            $newestMissingVsDevCmd.Candidate,
            $newerMissingTool.Candidate,
            $olderValid.Candidate
        ) -CandidateEnvironmentLoader {
            param($candidate)
            $fixture = $fixtures[$candidate.InstallationPath]
            $vsDevCmd = Join-Path $candidate.InstallationPath "Common7\Tools\VsDevCmd.bat"
            if (-not (Test-Path -LiteralPath $vsDevCmd -PathType Leaf)) {
                return [pscustomobject]@{ Success = $false; Reason = "missing-vsdevcmd"; Environment = $null }
            }
            return [pscustomobject]@{ Success = $true; Reason = "ok"; Environment = $fixture.Environment }
        }
        Assert-True -Condition ($selected.InstallationVersion -eq "17.9.0") -Message "did not fall back to VS 17"
        Assert-True -Condition (
            [Environment]::GetEnvironmentVariable("AACC_TOOLCHAIN_TEST", "Process") -eq "parent"
        ) -Message "failed candidate polluted the parent environment"
    } finally {
        [Environment]::SetEnvironmentVariable("AACC_TOOLCHAIN_TEST", $oldSentinel, "Process")
    }

    Assert-Throws -Message "all failing candidates did not fail closed" -Action {
        Select-AaccMsvcToolchain -Candidates @($newerMissingTool.Candidate) -CandidateEnvironmentLoader {
            param($candidate)
            [pscustomobject]@{ Success = $false; Reason = "missing-vsdevcmd"; Environment = $null }
        }
    }
    Assert-Throws -Message "malformed JSON did not fail closed" -Action {
        Get-AaccVsWhereCandidates -VsWherePath "ignored" -ProcessRunner {
            param($path, $timeout)
            [pscustomobject]@{ ExitCode = 0; StdOut = "not json"; TimedOut = $false }
        }
    }
    Assert-Throws -Message "nonzero vswhere did not fail closed" -Action {
        Get-AaccVsWhereCandidates -VsWherePath "ignored" -ProcessRunner {
            param($path, $timeout)
            [pscustomobject]@{ ExitCode = 1; StdOut = "[]"; TimedOut = $false }
        }
    }
    Assert-Throws -Message "timed out vswhere did not fail closed" -Action {
        Get-AaccVsWhereCandidates -VsWherePath "ignored" -ProcessRunner {
            param($path, $timeout)
            [pscustomobject]@{ ExitCode = -1; StdOut = ""; TimedOut = $true }
        }
    }
    $largeArguments = '-NoLogo -NoProfile -Command "& { $text = ''x'' * 131072; [Console]::Out.Write($text); [Console]::Error.Write($text) }"'
    $largeCapture = Invoke-AaccProcessCapture -FilePath "powershell.exe" -Arguments $largeArguments -TimeoutSeconds 5
    Assert-True -Condition ($largeCapture.StdOut.Length -eq 131072) -Message "large stdout was not drained"
    Assert-True -Condition ($largeCapture.StdErr.Length -eq 131072) -Message "large stderr was not drained"
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $timeoutCapture = Invoke-AaccProcessCapture -FilePath "powershell.exe" -Arguments '-NoLogo -NoProfile -Command "Start-Sleep -Seconds 10"' -TimeoutSeconds 1
    $watch.Stop()
    Assert-True -Condition $timeoutCapture.TimedOut -Message "process timeout did not fail closed"
    Assert-True -Condition ($watch.Elapsed.TotalSeconds -lt 7) -Message "process timeout was not bounded"
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
