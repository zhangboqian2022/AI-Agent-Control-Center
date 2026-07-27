#requires -Version 5.1
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BuildDir = Join-Path $Root "build\native"
$BrokerPath = Join-Path $BuildDir "aacc-spawn.exe"
$ObjectPath = Join-Path $BuildDir "aacc_spawn.obj"
$ResourcePath = Join-Path $BuildDir "aacc_spawn.rc"
$ResourceObjectPath = Join-Path $BuildDir "aacc_spawn.res"
$VersionHeaderPath = Join-Path $BuildDir "aacc_spawn_version.h"
$SourcePath = Join-Path $Root "native\aacc_spawn\aacc_spawn.cpp"
$ResourceTemplatePath = Join-Path $Root "native\aacc_spawn\aacc_spawn.rc.in"

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$Version = ((& uv version --short | Select-Object -First 1) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    throw "uv version --short did not return a three-part product version"
}
$VersionComma = "$($Matches[1]),$($Matches[2]),$($Matches[3]),0"

$ProgramFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
if ([string]::IsNullOrWhiteSpace($ProgramFilesX86)) {
    $ProgramFilesX86 = $env:ProgramFiles
}
$VsWhere = Join-Path $ProgramFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $VsWhere -PathType Leaf)) {
    throw "vswhere.exe is required to locate an installed Visual Studio MSVC toolchain"
}

$InstallationPath = ((
    & $VsWhere -prerelease -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath |
        Select-Object -First 1
) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($InstallationPath)) {
    throw "Visual Studio with the x64 MSVC toolchain is required"
}

$VsDevCmd = Join-Path $InstallationPath "Common7\Tools\VsDevCmd.bat"
if (-not (Test-Path -LiteralPath $VsDevCmd -PathType Leaf)) {
    throw "Visual Studio developer environment is missing"
}

$EnvironmentLoader = Join-Path $BuildDir "load-vs-environment.cmd"
@(
    "@echo off"
    "call `"$VsDevCmd`" -no_logo -arch=x64 -host_arch=x64 >nul"
    "if errorlevel 1 exit /b %errorlevel%"
    "set"
) | Set-Content -LiteralPath $EnvironmentLoader -Encoding ASCII
$DeveloperEnvironment = & $EnvironmentLoader
if ($LASTEXITCODE -ne 0) {
    throw "failed to initialize the Visual Studio x64 environment"
}
foreach ($Line in $DeveloperEnvironment) {
    $Separator = $Line.IndexOf("=")
    if ($Separator -gt 0) {
        $Name = $Line.Substring(0, $Separator)
        $Value = $Line.Substring($Separator + 1)
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

$Cl = (Get-Command cl.exe -ErrorAction Stop).Source
$Link = (Get-Command link.exe -ErrorAction Stop).Source
$Rc = (Get-Command rc.exe -ErrorAction Stop).Source
$Dumpbin = (Get-Command dumpbin.exe -ErrorAction Stop).Source

"#pragma once`r`n#define AACC_PRODUCT_VERSION L`"$Version`"`r`n" |
    Set-Content -LiteralPath $VersionHeaderPath -Encoding ASCII
$RenderedResource = Get-Content -LiteralPath $ResourceTemplatePath -Raw
$RenderedResource = $RenderedResource.Replace("@VERSION_COMMA@", $VersionComma)
$RenderedResource = $RenderedResource.Replace("@VERSION@", $Version)
$RenderedResource | Set-Content -LiteralPath $ResourcePath -Encoding ASCII

Remove-Item -LiteralPath $BrokerPath, $ObjectPath, $ResourceObjectPath `
    -Force -ErrorAction SilentlyContinue

$CompilerArguments = @(
    "/nologo"
    "/std:c++17"
    "/O2"
    "/MT"
    "/GS"
    "/guard:cf"
    "/W4"
    "/WX"
    "/EHsc"
    "/DUNICODE"
    "/D_UNICODE"
    "/DWINVER=0x0A00"
    "/D_WIN32_WINNT=0x0A00"
    "/c"
    "/I$BuildDir"
    "/Fo$ObjectPath"
    $SourcePath
)
& $Cl @CompilerArguments
if ($LASTEXITCODE -ne 0) {
    throw "aacc-spawn C++ compilation failed"
}

& $Rc /nologo "/fo$ResourceObjectPath" $ResourcePath
if ($LASTEXITCODE -ne 0) {
    throw "aacc-spawn version resource compilation failed"
}

$LinkerArguments = @(
    "/NOLOGO"
    "/OUT:$BrokerPath"
    "/SUBSYSTEM:CONSOLE"
    "/MACHINE:X64"
    "/DYNAMICBASE"
    "/NXCOMPAT"
    "/HIGHENTROPYVA"
    "/GUARD:CF"
    "/INCREMENTAL:NO"
    $ObjectPath
    $ResourceObjectPath
    "kernel32.lib"
)
& $Link @LinkerArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $BrokerPath -PathType Leaf)) {
    throw "aacc-spawn link failed"
}

$Headers = (& $Dumpbin /HEADERS $BrokerPath | Out-String)
if ($LASTEXITCODE -ne 0 -or $Headers -notmatch '(?im)^\s*8664 machine \(x64\)\s*$') {
    throw "aacc-spawn is not an x64 PE image"
}

$DependencyOutput = (& $Dumpbin /DEPENDENTS $BrokerPath | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "dumpbin /DEPENDENTS failed"
}
$DependencySectionFound = $false
$InDependencySection = $false
$DependencySeenInSection = $false
$ParsedDependencies = @()
foreach ($Line in ($DependencyOutput -split "\r?\n")) {
    if (
        $Line -match
        '^\s*Image has the following (?:delay load )?dependencies:\s*$'
    ) {
        $DependencySectionFound = $true
        $InDependencySection = $true
        $DependencySeenInSection = $false
        continue
    }
    if (-not $InDependencySection) {
        continue
    }

    $Candidate = $Line.Trim()
    if ([string]::IsNullOrEmpty($Candidate)) {
        if ($DependencySeenInSection) {
            $InDependencySection = $false
        }
        continue
    }

    # Import descriptor module names do not have to end in ".dll". Treat
    # every non-empty dependency-section line as a module and compare it
    # against the exact allowlist below.
    $ParsedDependencies += $Candidate
    $DependencySeenInSection = $true
}
if (-not $DependencySectionFound) {
    throw "dumpbin dependency section was not found"
}
$Dependencies = @($ParsedDependencies | Sort-Object -Unique)
$AllowedDependencies = @("KERNEL32.dll")
$ForbiddenDependencyMarkers = @("VCRUNTIME", "MSVCP", "ucrtbase", "Python", "Qt")
if (@($Dependencies).Count -eq 0) {
    throw "aacc-spawn dependency list is empty"
}
$UnexpectedDependencies = @(
    $Dependencies | Where-Object { $AllowedDependencies -notcontains $_ }
)
if ($UnexpectedDependencies.Count -ne 0) {
    throw "unexpected broker dependency: $($UnexpectedDependencies -join ', ')"
}
$MissingDependencies = @(
    $AllowedDependencies | Where-Object { $Dependencies -notcontains $_ }
)
if ($MissingDependencies.Count -ne 0) {
    throw "required broker dependency is missing: $($MissingDependencies -join ', ')"
}
foreach ($Marker in $ForbiddenDependencyMarkers) {
    if (($Dependencies -join "`n") -match [regex]::Escape($Marker)) {
        throw "forbidden broker dependency marker: $Marker"
    }
}

$VersionOutput = (& $BrokerPath --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $VersionOutput -ne "protocol=1 product=$Version") {
    throw "aacc-spawn protocol/product version mismatch"
}
$VersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($BrokerPath)
if (
    $VersionInfo.FileVersion -ne $Version -or
    $VersionInfo.ProductVersion -ne $Version -or
    $VersionInfo.OriginalFilename -ne "aacc-spawn.exe"
) {
    throw "aacc-spawn version resource mismatch"
}

function New-BrokerTestProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CodexPath,
        [Parameter(Mandatory = $true)]
        [string]$BundleDir
    )

    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $BrokerPath
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.EnvironmentVariables["AACC_BROKER_CODEX_TARGET"] = (
        "C:\malicious inherited target\not-codex.cmd"
    )
    $StartInfo.EnvironmentVariables["AACC_UNSET"] = "SHOULD_NOT_EXPAND"
    $StartInfo.EnvironmentVariables["AACC_TEST_EXPECTED_CODEX_TARGET"] = $CodexPath
    $StartInfo.Arguments = (
        "--protocol 1 --parent-pid $PID " +
        "--bundle-dir `"$BundleDir`" --codex `"$CodexPath`""
    )
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = $StartInfo
    return $Process
}

function Assert-ProcessExited {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Id
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($null -eq (Get-Process -Id $Id -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "broker integration left a child process alive"
}

function Assert-BrokerRejectsArguments {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Arguments
    )

    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $BrokerPath
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.Arguments = $Arguments
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = $StartInfo
    $Started = $false
    try {
        if (-not $Process.Start()) {
            throw "failed to start invalid-argument broker probe"
        }
        $Started = $true
        $OutputTask = $Process.StandardOutput.ReadToEndAsync()
        $ErrorTask = $Process.StandardError.ReadToEndAsync()
        if (-not $Process.WaitForExit(10000)) {
            throw "invalid-argument broker probe timed out"
        }
        $Process.WaitForExit()
        $Output = $OutputTask.GetAwaiter().GetResult().Trim()
        $ErrorOutput = $ErrorTask.GetAwaiter().GetResult().Trim()
        if (
            $Process.ExitCode -ne 10 -or
            -not [string]::IsNullOrEmpty($Output) -or
            $ErrorOutput -notmatch '^AACC_BROKER_ERROR stage=10 win32=\d+$'
        ) {
            throw "aacc-spawn argument diagnostics are not fixed and sanitized"
        }
    }
    finally {
        if ($Started -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        $Process.Dispose()
    }
}

function Invoke-BrokerProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CodexPath,
        [Parameter(Mandatory = $true)]
        [string]$BundleDir,
        [Parameter(Mandatory = $true)]
        [int]$RequestId,
        [Parameter(Mandatory = $true)]
        [string]$Payload,
        [int]$ExpectedExitCode = 0
    )

    $Process = New-BrokerTestProcess -CodexPath $CodexPath -BundleDir $BundleDir
    $Started = $false
    try {
        if (-not $Process.Start()) {
            throw "failed to start aacc-spawn integration probe"
        }
        $Started = $true
        $OutputTask = $Process.StandardOutput.ReadToEndAsync()
        $ErrorTask = $Process.StandardError.ReadToEndAsync()
        $Request = @{
            jsonrpc = "2.0"
            id = $RequestId
            method = "account/rateLimits/read"
            payload = $Payload
        } | ConvertTo-Json -Compress
        $Process.StandardInput.WriteLine($Request)
        $Process.StandardInput.Close()

        if (-not $Process.WaitForExit(15000)) {
            throw "aacc-spawn integration probe timed out"
        }
        $Process.WaitForExit()
        $Output = $OutputTask.GetAwaiter().GetResult().Trim()
        $ErrorOutput = $ErrorTask.GetAwaiter().GetResult().Trim()
        if ($Process.ExitCode -ne $ExpectedExitCode) {
            throw "aacc-spawn did not propagate the target exit code"
        }
        if (-not [string]::IsNullOrEmpty($ErrorOutput)) {
            throw "aacc-spawn emitted unexpected diagnostics on a successful launch"
        }

        $Response = $Output | ConvertFrom-Json
        if (
            $Response.args.Count -ne 2 -or
            $Response.args[0] -ne "app-server" -or
            $Response.args[1] -ne "--stdio"
        ) {
            throw "aacc-spawn changed the fixed Codex arguments"
        }
        if (
            $Response.request.id -ne $RequestId -or
            $Response.request.method -ne "account/rateLimits/read" -or
            $Response.request.payload -ne $Payload
        ) {
            throw "aacc-spawn did not preserve JSON stdio"
        }
        if ($Response.bundle_in_path -ne $false) {
            throw "aacc-spawn did not remove bundle-rooted PATH entries"
        }
        if ($Response.preserved_path_present -ne $true) {
            throw "aacc-spawn removed a PATH entry outside the bundle"
        }
        if ($Response.broker_target_matches_expected -ne $true) {
            throw "aacc-spawn did not replace its private cmd target variable"
        }
        Assert-ProcessExited -Id ([int]$Response.pid)
    }
    finally {
        if ($Started -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        $Process.Dispose()
    }
}

$IntegrationParent = Join-Path $BuildDir "broker-integration"
# Required Unicode/metacharacter fixture name uses U+4E34 U+65F6 plus ASCII.
# Windows PowerShell 5.1 treats BOM-less scripts as the active ANSI code page,
# so construct the two non-ASCII characters rather than trusting source decoding.
$SpecialDirectoryName = -join @(
    [char]0x4E34
    [char]0x65F6
    " AACC &(broker)"
)
$IntegrationRoot = Join-Path $IntegrationParent $SpecialDirectoryName
$TargetRoot = Join-Path $IntegrationRoot '%AACC_UNSET%!literal!'
$BundleDir = Join-Path $IntegrationRoot "_internal"
if (Test-Path -LiteralPath $IntegrationParent) {
    Remove-Item -LiteralPath $IntegrationParent -Recurse -Force
}
New-Item -ItemType Directory -Path $BundleDir -Force | Out-Null
New-Item -ItemType Directory -Path $TargetRoot -Force | Out-Null

$FakeCppSource = Join-Path $Root "tests\native\fake_codex_server.cpp"
$FakeExe = Join-Path $TargetRoot "fake-codex-server.exe"
$FakeObject = Join-Path $BuildDir "fake_codex_server.obj"
& $Cl /nologo /std:c++17 /O2 /MT /W4 /WX /EHsc /DUNICODE /D_UNICODE `
    /c "/Fo$FakeObject" $FakeCppSource
if ($LASTEXITCODE -ne 0) {
    throw "fake .exe broker target compilation failed"
}
& $Link /NOLOGO "/OUT:$FakeExe" /SUBSYSTEM:CONSOLE /MACHINE:X64 `
    /INCREMENTAL:NO $FakeObject kernel32.lib
if ($LASTEXITCODE -ne 0) {
    throw "fake .exe broker target link failed"
}

$NativeFixtures = Join-Path $Root "tests\native"
foreach ($Fixture in @(
    "fake_codex.cmd",
    "fake_codex_server.py",
    "spawn_descendant.py"
)) {
    Copy-Item -LiteralPath (Join-Path $NativeFixtures $Fixture) `
        -Destination (Join-Path $TargetRoot $Fixture) -Force
}
$FakeCmd = Join-Path $TargetRoot "fake_codex.cmd"
$FakeBat = Join-Path $TargetRoot "fake_codex.bat"
Copy-Item -LiteralPath $FakeCmd -Destination $FakeBat -Force
$PythonExecutable = ((
    & uv run python -c "import sys; print(sys.executable)" |
        Select-Object -First 1
) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "failed to locate the integration-test Python executable"
}

$OldFakePython = $env:FAKE_CODEX_PYTHON
$OldTestExitCode = $env:AACC_TEST_EXIT_CODE
$OldDescendantPidFile = $env:AACC_TEST_DESCENDANT_PID_FILE
$OldTestBundleDir = $env:AACC_TEST_BUNDLE_DIR
$OldPreservedPathEntry = $env:AACC_TEST_PRESERVED_PATH_ENTRY
$OldPath = $env:PATH
$DescendantBroker = $null
$DescendantBrokerStarted = $false
try {
    Remove-Item Env:AACC_TEST_EXIT_CODE -ErrorAction SilentlyContinue
    Remove-Item Env:AACC_TEST_DESCENDANT_PID_FILE -ErrorAction SilentlyContinue
    $env:FAKE_CODEX_PYTHON = $PythonExecutable
    $env:AACC_TEST_BUNDLE_DIR = $BundleDir
    $PreservedPathEntry = "${BundleDir}-sibling"
    $env:AACC_TEST_PRESERVED_PATH_ENTRY = $PreservedPathEntry
    $env:PATH = "$BundleDir;$BundleDir\poison;$PreservedPathEntry;$OldPath"

    $BrokerCountBefore = @(
        Get-Process -Name "aacc-spawn" -ErrorAction SilentlyContinue
    ).Count
    $ChildCountBefore = @(
        Get-Process -Name "fake-codex-server" -ErrorAction SilentlyContinue
    ).Count

    foreach ($Iteration in 1..20) {
        $Target = if ($Iteration % 2 -eq 0) { $FakeExe } else { $FakeCmd }
        $Payload = if ($Iteration -eq 1) {
            ("x" * 70000) -join ""
        }
        else {
            "probe-$Iteration"
        }
        Invoke-BrokerProbe -CodexPath $Target -BundleDir $BundleDir `
            -RequestId $Iteration -Payload $Payload
    }

    foreach ($ExitCase in @(
        @{ Target = $FakeCmd; Code = 7 },
        @{ Target = $FakeExe; Code = 9 }
    )) {
        $env:AACC_TEST_EXIT_CODE = [string]$ExitCase.Code
        Invoke-BrokerProbe -CodexPath $ExitCase.Target -BundleDir $BundleDir `
            -RequestId (100 + $ExitCase.Code) -Payload "exit-code" `
            -ExpectedExitCode $ExitCase.Code
    }
    Remove-Item Env:AACC_TEST_EXIT_CODE -ErrorAction SilentlyContinue

    Invoke-BrokerProbe -CodexPath $FakeBat -BundleDir $BundleDir `
        -RequestId 120 -Payload "batch-extension"

    $DirectoryTarget = Join-Path $IntegrationRoot "directory.exe"
    New-Item -ItemType Directory -Path $DirectoryTarget -Force | Out-Null
    $WrongExtensionTarget = Join-Path $IntegrationRoot "not-codex.txt"
    "not an executable" |
        Set-Content -LiteralPath $WrongExtensionTarget -Encoding ASCII
    $ValidArguments = (
        "--protocol 1 --parent-pid $PID " +
        "--bundle-dir `"$BundleDir`" --codex `"$FakeExe`""
    )
    foreach ($InvalidArguments in @(
        "--protocol 2 --parent-pid $PID --bundle-dir `"$BundleDir`" --codex `"$FakeExe`"",
        "--protocol 1 --protocol 1 --bundle-dir `"$BundleDir`" --codex `"$FakeExe`"",
        "--protocol 1 --parent-pid $PID --bundle-dir `"$BundleDir`" --target `"$FakeExe`"",
        "--protocol 1 --parent-pid nope --bundle-dir `"$BundleDir`" --codex `"$FakeExe`"",
        "--protocol 1 --parent-pid 0 --bundle-dir `"$BundleDir`" --codex `"$FakeExe`"",
        "--protocol 1 --parent-pid $PID --bundle-dir relative --codex `"$FakeExe`"",
        "--protocol 1 --parent-pid $PID --bundle-dir `"$BundleDir`" --codex relative.exe",
        "--protocol 1 --parent-pid $PID --bundle-dir `"$BundleDir`" --codex `"$DirectoryTarget`"",
        "--protocol 1 --parent-pid $PID --bundle-dir `"$BundleDir`" --codex `"$WrongExtensionTarget`"",
        "$ValidArguments arbitrary-command"
    )) {
        Assert-BrokerRejectsArguments -Arguments $InvalidArguments
    }

    $PidFile = Join-Path $IntegrationRoot "descendant-pids.txt"
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    $env:AACC_TEST_DESCENDANT_PID_FILE = $PidFile
    $DescendantBroker = New-BrokerTestProcess -CodexPath $FakeCmd -BundleDir $BundleDir
    if (-not $DescendantBroker.Start()) {
        throw "failed to start descendant-tree broker probe"
    }
    $DescendantBrokerStarted = $true
    $DescendantBroker.StandardInput.Close()

    $Deadline = [DateTime]::UtcNow.AddSeconds(15)
    $DescendantPids = @()
    while ([DateTime]::UtcNow -lt $Deadline) {
        if (Test-Path -LiteralPath $PidFile) {
            $RecordedPids = @(
                Get-Content -LiteralPath $PidFile |
                    Where-Object { $_ -match '^\d+$' } |
                    ForEach-Object { [int]$_ }
            )
            $DescendantPids = @($RecordedPids | Sort-Object -Unique)
            if ($DescendantPids.Count -ge 3) {
                break
            }
        }
        Start-Sleep -Milliseconds 100
    }
    if ($DescendantPids.Count -lt 3) {
        Stop-Process -Id $DescendantBroker.Id -Force -ErrorAction SilentlyContinue
        throw "descendant-tree fixture did not record root, child, and grandchild"
    }

    Stop-Process -Id $DescendantBroker.Id -Force
    $DescendantBroker.WaitForExit(10000) | Out-Null
    foreach ($DescendantPid in $DescendantPids) {
        Assert-ProcessExited -Id $DescendantPid
    }

    $BrokerCountAfter = @(
        Get-Process -Name "aacc-spawn" -ErrorAction SilentlyContinue
    ).Count
    $ChildCountAfter = @(
        Get-Process -Name "fake-codex-server" -ErrorAction SilentlyContinue
    ).Count
    if (
        $BrokerCountAfter -ne $BrokerCountBefore -or
        $ChildCountAfter -ne $ChildCountBefore
    ) {
        throw "broker integration changed broker/child process counts"
    }
}
finally {
    if (
        $null -ne $DescendantBroker -and
        $DescendantBrokerStarted -and
        -not $DescendantBroker.HasExited
    ) {
        Stop-Process -Id $DescendantBroker.Id -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $DescendantBroker) {
        $DescendantBroker.Dispose()
    }
    $env:PATH = $OldPath
    if ($null -eq $OldFakePython) {
        Remove-Item Env:FAKE_CODEX_PYTHON -ErrorAction SilentlyContinue
    }
    else {
        $env:FAKE_CODEX_PYTHON = $OldFakePython
    }
    if ($null -eq $OldTestExitCode) {
        Remove-Item Env:AACC_TEST_EXIT_CODE -ErrorAction SilentlyContinue
    }
    else {
        $env:AACC_TEST_EXIT_CODE = $OldTestExitCode
    }
    if ($null -eq $OldDescendantPidFile) {
        Remove-Item Env:AACC_TEST_DESCENDANT_PID_FILE -ErrorAction SilentlyContinue
    }
    else {
        $env:AACC_TEST_DESCENDANT_PID_FILE = $OldDescendantPidFile
    }
    if ($null -eq $OldTestBundleDir) {
        Remove-Item Env:AACC_TEST_BUNDLE_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:AACC_TEST_BUNDLE_DIR = $OldTestBundleDir
    }
    if ($null -eq $OldPreservedPathEntry) {
        Remove-Item Env:AACC_TEST_PRESERVED_PATH_ENTRY -ErrorAction SilentlyContinue
    }
    else {
        $env:AACC_TEST_PRESERVED_PATH_ENTRY = $OldPreservedPathEntry
    }
}

Write-Host "Built and verified build/native/aacc-spawn.exe"
