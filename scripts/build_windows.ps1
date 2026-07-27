#requires -Version 5
# 在 Windows 上构建 AACC（PyInstaller，windowed 单目录产物 dist/AACC/AACC.exe）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
uv sync --locked --extra dev
uv run pyinstaller --noconfirm --clean AACC-windows.spec
Write-Host "Built dist/AACC/AACC.exe"
