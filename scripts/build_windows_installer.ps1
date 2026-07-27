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
    if ($VersionInfo.FileMajorPart -ne 6 -or
        $VersionInfo.FileMinorPart -ne 7 -or
        $VersionInfo.FileBuildPart -ne 1 -or
        $VersionInfo.FilePrivatePart -ne 0 -or
        $VersionInfo.ProductMajorPart -ne 6 -or
        $VersionInfo.ProductMinorPart -ne 7 -or
        $VersionInfo.ProductBuildPart -ne 1 -or
        $VersionInfo.ProductPrivatePart -ne 0) {
        throw "ISCC version validation failed"
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

$VersionOutput = @(& uv version --short)
if ($LASTEXITCODE -ne 0) {
    throw "project version query failed"
}
if ($VersionOutput.Count -ne 1) {
    throw "project version query returned unexpected output"
}
$Version = $VersionOutput[0].Trim()
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "project version is not a restricted numeric version"
}

$IsccPath = $null
if (-not [string]::IsNullOrWhiteSpace($env:AACC_ISCC_PATH)) {
    $IsccPath = Resolve-ExistingLeaf `
        -Path $env:AACC_ISCC_PATH `
        -Category "AACC_ISCC_PATH"
    Assert-IsccTrusted -Path $IsccPath
    Write-Host "Using verified Inno Setup $InnoVersion compiler (explicit)"
}
else {
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

    $InnoRoot = Join-Path $Root "build\tools\inno-$InnoVersion"
    [System.IO.Directory]::CreateDirectory($InnoRoot) | Out-Null
    $IsccCandidates = @(
        Get-ChildItem -LiteralPath $InnoRoot -Filter "ISCC.exe" -File -Recurse
    )
    if ($IsccCandidates.Count -eq 0) {
        & $BootstrapPath `
            /PORTABLE=1 `
            /VERYSILENT `
            /CURRENTUSER `
            /NOICONS `
            /NORESTART `
            /SP- `
            "/DIR=$InnoRoot"
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup bootstrap failed"
        }
        $IsccCandidates = @(
            Get-ChildItem -LiteralPath $InnoRoot -Filter "ISCC.exe" -File -Recurse
        )
    }
    Write-Host "AACC_INNO_LAYOUT candidate_count=$($IsccCandidates.Count)"
    if ($IsccCandidates.Count -eq 0) {
        $DesktopRoot = [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::DesktopDirectory
        )
        $DefaultDesktopIscc = Join-Path $DesktopRoot "Inno Setup 6\ISCC.exe"
        $DefaultDesktopCandidates = @(
            Get-Item -LiteralPath $DefaultDesktopIscc -Force -ErrorAction SilentlyContinue |
                Where-Object {
                    -not $_.PSIsContainer -and
                    (($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0)
                }
        )
        Write-Host (
            "AACC_INNO_DEFAULT_DESKTOP candidate_count=" +
            $DefaultDesktopCandidates.Count
        )
    }
    if ($IsccCandidates.Count -ne 1) {
        throw "bootstrapped Inno Setup compiler layout is invalid"
    }
    $IsccPath = Resolve-ExistingLeaf `
        -Path $IsccCandidates[0].FullName `
        -Category "bootstrapped ISCC"
    Assert-IsccTrusted -Path $IsccPath
    Write-Host "Using verified Inno Setup $InnoVersion compiler (bootstrapped)"
}

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

& $IsccPath "/DMyAppVersion=$Version" $IssPath
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
