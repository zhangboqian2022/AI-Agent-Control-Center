#requires -Version 5
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("windows-10", "windows-11")]
    [string]$Target
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Windows compatibility contract must run on Windows"
}

$os = Get-CimInstance -ClassName Win32_OperatingSystem
if ($os.Caption -notmatch "Windows Server") {
    throw "Unexpected runner evidence boundary: $($os.Caption)"
}

Write-Host "AACC_WINDOWS_COMPAT_TARGET=$Target"
Write-Host "AACC_WINDOWS_RUNNER_CAPTION=$($os.Caption)"
Write-Host "Hosted Windows Server compatibility evidence only; consumer Windows 10/11 hardware not claimed"
