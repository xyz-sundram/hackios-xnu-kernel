# Run from C:\platform-tools after booting the identity or touch-live USB loader.
[CmdletBinding()]
param(
    [string]$PlatformTools = (Get-Location).Path,
    [switch]$CollectLog,
    [switch]$Touch,
    [switch]$Quiet,
    [ValidateSet(315, 450, 550)][int]$GpuMHz = 315,
    [ValidateRange(2, 300)][int]$LogIntervalSeconds = 15
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'spacewar-userland-touch.ps1') -PlatformTools $PlatformTools -LiveLog -Gpu -Touch:$Touch -Quiet:$Quiet -GpuMHz $GpuMHz -CollectLog:$CollectLog -LogIntervalSeconds $LogIntervalSeconds
