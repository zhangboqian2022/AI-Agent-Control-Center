#requires -Version 5
# Build the windowed, one-directory AACC package on Windows.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
uv sync --locked --extra dev
if ($LASTEXITCODE -ne 0) {
    throw "locked dependency sync failed"
}
& "$PSScriptRoot\build_spawn_broker.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "aacc-spawn build failed"
}
uv run pyinstaller --noconfirm --clean AACC-windows.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}
Copy-Item "build\native\aacc-spawn.exe" "dist\AACC\aacc-spawn.exe" -Force
$expectedRootEntries = @("_internal", "AACC.exe", "aacc-spawn.exe")
$rootFiles = @(
    Get-ChildItem -LiteralPath "dist\AACC" |
        Select-Object -ExpandProperty Name
)
$rootDifference = @(
    Compare-Object -ReferenceObject $expectedRootEntries -DifferenceObject $rootFiles
)
if ($rootDifference.Count -ne 0) {
    throw "unexpected Windows package root"
}
if ($env:AACC_SKIP_INSTALLER -ne "1") {
    & "$PSScriptRoot\build_windows_installer.ps1"
    if ($LASTEXITCODE -ne 0) {
        throw "Windows Setup build failed"
    }
    Write-Host "Built dist/AACC/AACC.exe and per-user Setup"
}
else {
    Write-Host "Built dist/AACC/AACC.exe (Setup explicitly skipped)"
}
