#requires -Version 5
# Build the per-user AACC Setup package with hash-pinned Inno Setup 6.7.1.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3

$InnoVersion = "6.7.1"
$InnoBootstrapName = "innosetup-6.7.1.exe"
$InnoBootstrapUrl = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_1/innosetup-6.7.1.exe"
$InnoBootstrapSha256 = "4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0"
$MinimumSetupBytes = 1048576

function Resolve-ExistingLeaf {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Category
    )

    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($Item.PSIsContainer -or
        (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "$Category must be a regular file"
    }
    return $Item.FullName
}

function Assert-AuthenticodeValid {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Category
    )

    $Signature = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction Stop
    if ($Signature.Status.ToString() -ne "Valid") {
        throw "$Category Authenticode validation failed"
    }
}

function Assert-IsccVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($Item.Name -ine "ISCC.exe") {
        throw "ISCC override must name ISCC.exe"
    }
    if ($Item.PSIsContainer -or
        (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "ISCC must be a regular file"
    }

    $VersionInfo = $Item.VersionInfo
    Write-Host (
        "AACC_ISCC_FIXED_VERSION file={0}.{1}.{2}.{3} product={4}.{5}.{6}.{7}" -f
        $VersionInfo.FileMajorPart,
        $VersionInfo.FileMinorPart,
        $VersionInfo.FileBuildPart,
        $VersionInfo.FilePrivatePart,
        $VersionInfo.ProductMajorPart,
        $VersionInfo.ProductMinorPart,
        $VersionInfo.ProductBuildPart,
        $VersionInfo.ProductPrivatePart
    )
    # Official ISCC.exe intentionally carries zeroed fixed PE version fields.
    # Validate the real compiler engine below instead of inventing metadata.
    if ($VersionInfo.FileMajorPart -ne 0 -or
        $VersionInfo.FileMinorPart -ne 0 -or
        $VersionInfo.FileBuildPart -ne 0 -or
        $VersionInfo.FilePrivatePart -ne 0 -or
        $VersionInfo.ProductMajorPart -ne 0 -or
        $VersionInfo.ProductMinorPart -ne 0 -or
        $VersionInfo.ProductBuildPart -ne 0 -or
        $VersionInfo.ProductPrivatePart -ne 0) {
        throw "ISCC version validation failed"
    }

    $ProbeRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
        "aacc-iscc-version-probe-" + [Guid]::NewGuid().ToString("N")
    )
    $ProbePath = Join-Path $ProbeRoot "probe.iss"
    $ProbeText = @"
[Setup]
AppName=AACC Inno Version Probe
AppVersion=0
DefaultDirName={tmp}\AACCInnoVersionProbe
Uninstallable=no
"@
    $ProbeOutput = @()
    $ProbeExitCode = $null
    $ProbeRootOwned = $false
    try {
        if (Test-Path -LiteralPath $ProbeRoot) {
            throw "ISCC version probe directory already exists"
        }
        [System.IO.Directory]::CreateDirectory($ProbeRoot) | Out-Null
        $ProbeRootOwned = $true
        $ProbeRootItem = Get-Item -LiteralPath $ProbeRoot -Force -ErrorAction Stop
        if (-not $ProbeRootItem.PSIsContainer -or
            (($ProbeRootItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw "ISCC version probe directory is invalid"
        }
        [System.IO.File]::WriteAllText(
            $ProbePath,
            $ProbeText,
            [System.Text.UTF8Encoding]::new($false, $true)
        )
        Resolve-ExistingLeaf -Path $ProbePath -Category "ISCC version probe" |
            Out-Null
        $ProbeOutput = @(
            & $Path "/O-" $ProbePath 2>&1 |
                ForEach-Object { [string]$_ }
        )
        $ProbeExitCode = $LASTEXITCODE
    }
    finally {
        if ($ProbeRootOwned -and (Test-Path -LiteralPath $ProbeRoot)) {
            $CleanupItem = Get-Item `
                -LiteralPath $ProbeRoot `
                -Force `
                -ErrorAction SilentlyContinue
            if ($null -ne $CleanupItem -and
                $CleanupItem.PSIsContainer -and
                (($CleanupItem.Attributes -band
                    [System.IO.FileAttributes]::ReparsePoint) -eq 0)) {
                Remove-Item `
                    -LiteralPath $ProbeRoot `
                    -Recurse `
                    -Force `
                    -ErrorAction SilentlyContinue
            }
            elseif ($null -ne $CleanupItem) {
                Remove-Item `
                    -LiteralPath $ProbeRoot `
                    -Force `
                    -ErrorAction SilentlyContinue
            }
        }
    }
    $ExpectedEngineLine = "Compiler engine version: Inno Setup $InnoVersion"
    $EngineMatches = @(
        $ProbeOutput | Where-Object { $_.Trim() -ceq $ExpectedEngineLine }
    )
    if ($ProbeExitCode -ne 0 -or $EngineMatches.Count -ne 1) {
        throw "ISCC version probe failed"
    }
}

function Assert-IsccTrusted {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Assert-AuthenticodeValid -Path $Path -Category "ISCC"
    Assert-IsccVersion -Path $Path
}

function Assert-BootstrapTrusted {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $Digest = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Digest -cne $InnoBootstrapSha256) {
        throw "Inno Setup bootstrap checksum validation failed"
    }
    Assert-AuthenticodeValid -Path $Path -Category "Inno Setup bootstrap"
}

$Root = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
Set-Location -LiteralPath $Root

$IssPath = Resolve-ExistingLeaf `
    -Path (Join-Path $Root "installer\AACC.iss") `
    -Category "Inno source"
$OnedirRoot = (Resolve-Path -LiteralPath (Join-Path $Root "dist\AACC")).Path
$OnedirItem = Get-Item -LiteralPath $OnedirRoot -Force
if (-not $OnedirItem.PSIsContainer -or
    (($OnedirItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "Windows package root must be a regular directory"
}

$ExpectedRootEntries = @("_internal", "AACC.exe", "aacc-spawn.exe")
$RootItems = @(Get-ChildItem -LiteralPath $OnedirRoot -Force)
$RootNames = @($RootItems | ForEach-Object { $_.Name })
$RootDifference = @(
    Compare-Object -ReferenceObject $ExpectedRootEntries -DifferenceObject $RootNames
)
if ($RootDifference.Count -ne 0) {
    throw "unexpected Windows package root"
}
foreach ($RootItem in $RootItems) {
    if (($RootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Windows package root contains a reparse point"
    }
    if ($RootItem.Name -eq "_internal") {
        if (-not $RootItem.PSIsContainer) {
            throw "Windows package internal payload is not a directory"
        }
    }
    elseif ($RootItem.PSIsContainer) {
        throw "Windows package executable payload is not a file"
    }
}

$InternalRoot = Join-Path $OnedirRoot "_internal"
$ManifestBuildRoot = Join-Path $Root "build\installer"
$InternalManifestPath = Join-Path $ManifestBuildRoot "internal-manifest-v1.txt"
[System.IO.Directory]::CreateDirectory($ManifestBuildRoot) | Out-Null
$ManifestLines = New-Object System.Collections.Generic.List[string]
$ManifestPathKeys = New-Object 'System.Collections.Generic.HashSet[string]' `
    -ArgumentList ([System.StringComparer]::OrdinalIgnoreCase)
foreach ($InternalItem in @(Get-ChildItem -LiteralPath $InternalRoot -Force -Recurse)) {
    if (($InternalItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Windows internal payload contains a reparse point"
    }
    $Relative = $InternalItem.FullName.Substring($InternalRoot.Length + 1).Replace("\", "/")
    if (
        [string]::IsNullOrWhiteSpace($Relative) -or
        $Relative.Contains("`r") -or
        $Relative.Contains("`n")
    ) {
        throw "Windows internal payload contains an invalid manifest path"
    }
    if (-not $ManifestPathKeys.Add($Relative)) {
        throw "Windows internal payload contains a case-insensitive path collision"
    }
    if ($InternalItem.PSIsContainer) {
        $ManifestLines.Add("D $Relative/")
    }
    else {
        $ManifestLines.Add("F $Relative")
    }
}
if ($ManifestLines.Count -eq 0) {
    throw "Windows internal payload manifest is empty"
}
[string[]]$SortedManifestLines = $ManifestLines.ToArray()
[Array]::Sort($SortedManifestLines, [System.StringComparer]::Ordinal)
$ManifestEncoding = [System.Text.UTF8Encoding]::new($false, $true)
$ManifestText = ($SortedManifestLines -join "`n") + "`n"
[System.IO.File]::WriteAllText($InternalManifestPath, $ManifestText, $ManifestEncoding)
$ManifestBytes = [System.IO.File]::ReadAllBytes($InternalManifestPath)
if (
    $ManifestBytes.Length -eq 0 -or
    (
        $ManifestBytes.Length -ge 3 -and
        $ManifestBytes[0] -eq 0xEF -and
        $ManifestBytes[1] -eq 0xBB -and
        $ManifestBytes[2] -eq 0xBF
    ) -or
    $ManifestText.Contains("`r")
) {
    throw "Windows internal payload manifest encoding is invalid"
}

$VersionOutput = @(& uv version --short)
if ($LASTEXITCODE -ne 0) {
    throw "project version query failed"
}
if ($VersionOutput.Count -ne 1) {
    throw "project version query returned unexpected output"
}
$Version = $VersionOutput[0].Trim()
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)((?:a|b|rc)(0|[1-9][0-9]*))?$') {
    throw "project version is not a valid release version"
}
# Windows VERSIONINFO fields are numeric-only; prerelease suffixes stay in
# display names (AppVersion, artifact filenames) via a separate define.
$VersionInfo = "$($Matches[1]).$($Matches[2]).$($Matches[3])"

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "LOCALAPPDATA is unavailable"
}
$CacheRoot = Join-Path $env:LOCALAPPDATA "AACC\build-cache\inno-$InnoVersion"
[System.IO.Directory]::CreateDirectory($CacheRoot) | Out-Null
$BootstrapPath = Join-Path $CacheRoot $InnoBootstrapName
$DownloadPath = "$BootstrapPath.download"

$BootstrapReady = $false
if (Test-Path -LiteralPath $BootstrapPath -PathType Leaf) {
    try {
        Assert-BootstrapTrusted -Path $BootstrapPath
        $BootstrapReady = $true
    }
    catch {
        Remove-Item -LiteralPath $BootstrapPath -Force
    }
}
if (-not $BootstrapReady) {
    if (Test-Path -LiteralPath $DownloadPath) {
        Remove-Item -LiteralPath $DownloadPath -Force
    }
    Invoke-WebRequest `
        -UseBasicParsing `
        -Uri $InnoBootstrapUrl `
        -OutFile $DownloadPath
    Assert-BootstrapTrusted -Path $DownloadPath
    Move-Item -LiteralPath $DownloadPath -Destination $BootstrapPath
}
Assert-BootstrapTrusted -Path $BootstrapPath

$InnoRoot = Join-Path $Root (
    "build\tools\inno-$InnoVersion-" + [Guid]::NewGuid().ToString("N")
)
if (Test-Path -LiteralPath $InnoRoot) {
    throw "fresh Inno Setup extraction directory already exists"
}
$InnoRootOwned = $false
try {
    [System.IO.Directory]::CreateDirectory($InnoRoot) | Out-Null
    $InnoRootOwned = $true
    $InnoRootItem = Get-Item -LiteralPath $InnoRoot -Force -ErrorAction Stop
    if (-not $InnoRootItem.PSIsContainer -or
        (($InnoRootItem.Attributes -band
            [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Inno Setup extraction directory is invalid"
    }
    $BootstrapArguments = @(
        "/PORTABLE=1"
        "/VERYSILENT"
        "/CURRENTUSER"
        "/NOICONS"
        "/NORESTART"
        "/SP-"
        ('/DIR="' + $InnoRoot + '"')
    )
    $BootstrapProcess = Start-Process -FilePath $BootstrapPath `
        -ArgumentList $BootstrapArguments -PassThru
    try {
        if (-not $BootstrapProcess.WaitForExit(120000)) {
            if (-not $BootstrapProcess.HasExited) {
                $BootstrapProcess.Kill()
                if (-not $BootstrapProcess.WaitForExit(5000)) {
                    throw "Inno Setup bootstrap cleanup timed out"
                }
            }
            throw "Inno Setup bootstrap timed out"
        }
        if ($BootstrapProcess.ExitCode -ne 0) {
            throw "Inno Setup bootstrap failed"
        }
    }
    finally {
        $BootstrapProcess.Dispose()
    }
    if (-not (Test-Path -LiteralPath $InnoRoot -PathType Container)) {
        throw "Inno Setup bootstrap failed"
    }
    $IsccCandidates = @(
        Get-ChildItem -LiteralPath $InnoRoot -Filter "ISCC.exe" -File -Recurse
    )
    Write-Host "AACC_INNO_LAYOUT candidate_count=$($IsccCandidates.Count)"
    if ($IsccCandidates.Count -ne 1) {
        throw "bootstrapped Inno Setup compiler layout is invalid"
    }
    $IsccPath = Resolve-ExistingLeaf `
        -Path $IsccCandidates[0].FullName `
        -Category "bootstrapped ISCC"
    Assert-IsccTrusted -Path $IsccPath
    Write-Host (
        "Using verified Inno Setup $InnoVersion compiler (hash-pinned bootstrap)"
    )

    $OutputDir = Join-Path $Root "dist\installer"
    [System.IO.Directory]::CreateDirectory($OutputDir) | Out-Null
    $SetupLeaf = "AACC-$Version-Setup.exe"
    $ExpectedSetupPath = Join-Path $OutputDir $SetupLeaf
    $ChecksumPath = "$ExpectedSetupPath.sha256"
    if (Test-Path -LiteralPath $ExpectedSetupPath) {
        Remove-Item -LiteralPath $ExpectedSetupPath -Force
    }
    if (Test-Path -LiteralPath $ChecksumPath) {
        Remove-Item -LiteralPath $ChecksumPath -Force
    }

    & $IsccPath "/DMyAppVersion=$Version" "/DMyAppVersionInfo=$VersionInfo" $IssPath
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed"
    }

    $SetupCandidates = @(
        Get-ChildItem -LiteralPath $OutputDir -Filter $SetupLeaf -File -Force
    )
    if ($SetupCandidates.Count -ne 1 -or
        $SetupCandidates[0].Name -cne $SetupLeaf -or
        $SetupCandidates[0].Length -lt $MinimumSetupBytes) {
        throw "expected fresh Windows Setup was not produced"
    }
    $ExpectedSetupPath = $SetupCandidates[0].FullName

    $Digest = (
        Get-FileHash -LiteralPath $ExpectedSetupPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "Windows Setup checksum format is invalid"
    }
    $Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText(
        $ChecksumPath,
        "$Digest  $SetupLeaf`n",
        $Utf8NoBom
    )

    $ChecksumBytes = [System.IO.File]::ReadAllBytes($ChecksumPath)
    if ($ChecksumBytes.Length -ge 3 -and
        $ChecksumBytes[0] -eq 0xEF -and
        $ChecksumBytes[1] -eq 0xBB -and
        $ChecksumBytes[2] -eq 0xBF) {
        throw "Windows Setup checksum unexpectedly contains a BOM"
    }
    $StrictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $ChecksumText = $StrictUtf8.GetString($ChecksumBytes)
    if ($ChecksumText -cne "$Digest  $SetupLeaf`n" -or
        $ChecksumText -notmatch '^[0-9a-f]{64}  AACC-\d+\.\d+\.\d+-Setup\.exe\n$') {
        throw "Windows Setup checksum file is malformed"
    }
    $VerifiedDigest = (
        Get-FileHash -LiteralPath $ExpectedSetupPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($VerifiedDigest -cne $Digest) {
        throw "Windows Setup checksum verification failed"
    }

    Write-Host "Built dist/installer/$SetupLeaf"
    Write-Host "SHA-256 $Digest"
}
finally {
    if ($InnoRootOwned -and (Test-Path -LiteralPath $InnoRoot)) {
        $InnoCleanupItem = Get-Item `
            -LiteralPath $InnoRoot `
            -Force `
            -ErrorAction SilentlyContinue
        $InnoCleanupKind = $null
        if ($null -ne $InnoCleanupItem -and
            $InnoCleanupItem.PSIsContainer -and
            (($InnoCleanupItem.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -eq 0)) {
            $InnoCleanupKind = "private_toolchain"
            Remove-Item `
                -LiteralPath $InnoRoot `
                -Recurse `
                -Force `
                -ErrorAction SilentlyContinue
        }
        elseif ($null -ne $InnoCleanupItem) {
            $InnoCleanupKind = "replacement"
            Remove-Item `
                -LiteralPath $InnoRoot `
                -Force `
                -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $InnoRoot) {
            Write-Warning "AACC_INNO_CLEANUP cleanup_failed=true"
        }
        elseif ($null -ne $InnoCleanupKind) {
            Write-Host "AACC_INNO_CLEANUP removed_$InnoCleanupKind=true"
        }
        else {
            Write-Host "AACC_INNO_CLEANUP path_disappeared=true"
        }
    }
}
