# RAM-only Phone (1) U-Boot -> EFI loader -> iOS launcher.
[CmdletBinding()]
param(
    [string]$PlatformTools = (Split-Path -Parent $PSScriptRoot),
    [string]$BootImage = (Join-Path $PSScriptRoot 'spacewar-uboot-loader.img'),
    [string]$PayloadFile = (Join-Path $PSScriptRoot 'spacewar-uboot-gpu550-touch-quiet.bin')
)
$ErrorActionPreference = 'Stop'
$PlatformTools = [System.IO.Path]::GetFullPath($PlatformTools)
$fastboot = Join-Path $PlatformTools 'fastboot.exe'
$runner = Join-Path $PSScriptRoot 'spacewar-userland-touch.ps1'
$logFile = Join-Path $PSScriptRoot ('uboot-start-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))

function Invoke-Fastboot {
    param(
        [string[]]$Arguments,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 300
    )

    # ProcessStartInfo.ArgumentList is unavailable in Windows PowerShell 5.1.
    # Quote each argument using the Windows C-runtime command-line rules.
    $quotedArguments = foreach ($argument in $Arguments) {
        $quoted = New-Object System.Text.StringBuilder
        [void]$quoted.Append('"')
        $slashes = 0
        foreach ($character in $argument.ToCharArray()) {
            if ($character -eq '\') {
                $slashes++
                continue
            }
            if ($character -eq '"') {
                [void]$quoted.Append(('\' * (2 * $slashes + 1)))
            } else {
                [void]$quoted.Append(('\' * $slashes))
            }
            [void]$quoted.Append($character)
            $slashes = 0
        }
        [void]$quoted.Append(('\' * (2 * $slashes)))
        [void]$quoted.Append('"')
        $quoted.ToString()
    }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $fastboot
    $startInfo.Arguments = $quotedArguments -join ' '
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $timedOut = $false
    try {
        if (-not $process.Start()) { throw "Could not start $fastboot" }
        # Drain both pipes concurrently so a full stderr/stdout pipe cannot
        # deadlock stage/get_staged while the other pipe is being read.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $timedOut = $true
            $process.Kill()
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    } finally {
        $process.Dispose()
    }
    $output = $stdout + $stderr
    $transcript = "[{0}] fastboot {1}`r`nexit={2}; timeout={3}`r`nstdout:`r`n{4}`r`nstderr:`r`n{5}`r`n" -f (
        Get-Date -Format 'o'), ($quotedArguments -join ' '), $exitCode, $timedOut, $stdout, $stderr
    Add-Content -LiteralPath ($logFile + '.fastboot.txt') -Value $transcript -Encoding UTF8
    if ($timedOut) {
        throw "fastboot $($Arguments -join ' ') timed out after $TimeoutSeconds seconds. Host transcript: $logFile.fastboot.txt"
    }
    if ($exitCode -ne 0) {
        throw "fastboot $($Arguments -join ' ') exited with code ${exitCode}: $output"
    }
    return $output
}


foreach ($file in @($fastboot, $BootImage, $PayloadFile, $runner, (Join-Path $PlatformTools 'system.apfs'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing $file" }
}
$devices = Invoke-Fastboot -Arguments @('devices') -TimeoutSeconds 10
if ($devices -notmatch '\bfastboot\b') { throw 'Put the Phone (1) into stock Android fastboot first.' }
$product = Invoke-Fastboot -Arguments @('getvar','product') -TimeoutSeconds 10
if ($product -notmatch '(?im)^product:\s*Spacewar\s*$') { throw "Expected stock Phone (1) fastboot: $product" }
Write-Host 'Starting the Phone (1) U-Boot RAM loader...'
Invoke-Fastboot -Arguments @('boot', $BootImage) -TimeoutSeconds 60 | Write-Host
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$ready = $false
while ($timer.Elapsed.TotalSeconds -lt 45) {
    Start-Sleep -Seconds 2
    try {
        $devices = Invoke-Fastboot -Arguments @('devices') -TimeoutSeconds 5
        if ($devices -match '\bfastboot\b') {
            $product = Invoke-Fastboot -Arguments @('getvar','product') -TimeoutSeconds 5
            if ($product -match 'product:\s*xnuloader') { $ready = $true; break }
        }
    } catch { Write-Verbose $_ }
}
if (-not $ready) { throw "Loader USB did not appear. Host transcript: $logFile.fastboot.txt" }
Write-Host 'Loader USB connected; uploading the system and selected payload to RAM.'
& $runner -PlatformTools $PlatformTools -PayloadFile $PayloadFile -LiveLog -Gpu -Touch -Quiet -GpuMHz 550
