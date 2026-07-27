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
$rootFiles = Get-ChildItem "dist\AACC" | Select-Object -ExpandProperty Name
if (@($rootFiles | Sort-Object) -join "," -ne "_internal,AACC.exe,aacc-spawn.exe") {
    throw "unexpected Windows package root"
}
Write-Host "Built dist/AACC/AACC.exe"
