# Run from the directory containing fastboot.exe, system.apfs and the payload.
# A CPU0 touch run pauses the USB loader until XNU returns.
[CmdletBinding()]
param(
    [string]$PlatformTools = (Get-Location).Path,
    [string]$PayloadFile = '',
    [switch]$Timed,
    [switch]$CollectLog,
    [switch]$LiveLog,
    [switch]$Touch,
    [switch]$Gpu,
    [switch]$Quiet,
    [ValidateSet(315, 450, 550)][int]$GpuMHz = 315,
    [ValidateRange(2, 300)][int]$LogIntervalSeconds = 15
)

$ErrorActionPreference = 'Stop'
$fastboot = Join-Path $PlatformTools 'fastboot.exe'
$system = Join-Path $PlatformTools 'system.apfs'
$payloadName = if ($Quiet -and $GpuMHz -ne 315) { "spacewar-userland-gpu${GpuMHz}-touch-quiet-payload.bin" } elseif ($Quiet) { 'spacewar-userland-gpu-touch-quiet-payload.bin' } elseif ($Gpu -and $Touch) { 'spacewar-userland-gpu-touch-payload.bin' } elseif ($Gpu) { 'spacewar-userland-gpu-payload.bin' } elseif ($Touch) { 'spacewar-userland-touch-live-payload.bin' } elseif ($LiveLog) { 'spacewar-userland-live-log-payload.bin' } elseif ($Timed) { 'spacewar-userland-touch-diagnostic-payload.bin' } else { 'spacewar-userland-touch-continuous-payload.bin' }
$payload = if ($PayloadFile) { [System.IO.Path]::GetFullPath($PayloadFile) } else { Join-Path $PlatformTools $payloadName }
$payloadName = Split-Path -Leaf $payload
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logFile = Join-Path $PlatformTools "spacewar-userland-touch-$stamp.log"
$liveLogFile = Join-Path $PlatformTools "spacewar-userland-$stamp.live.log"

if ($LiveLog -and $Timed) { throw 'Choose -LiveLog or -Timed.' }
if ($Touch -and (-not $LiveLog -or $Timed)) { throw 'Use -Touch together with -LiveLog.' }
if ($Gpu -and (-not $LiveLog -or $Timed)) { throw 'Use -Gpu together with -LiveLog.' }
if ($Quiet -and (-not $Gpu -or -not $Touch -or -not $LiveLog -or $Timed)) { throw 'Use -Quiet with -LiveLog -Gpu -Touch.' }
if ($GpuMHz -ne 315 -and -not $Quiet) { throw 'Higher GPU clocks require -Quiet -Gpu -Touch -LiveLog.' }

# Read native stdout/stderr as text. PowerShell 5.1 otherwise turns fastboot's
# normal OKAY/status output on stderr into NativeCommandError records.
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

if (-not (Test-Path -LiteralPath $fastboot -PathType Leaf)) { throw "Missing $fastboot" }

if (-not $CollectLog) {
    foreach ($required in @($system, $payload)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing $required" }
    }
    $product = Invoke-Fastboot -Arguments @('getvar', 'product') -TimeoutSeconds 15
    if ($product -notmatch 'product:\s*xnuloader') { throw "Boot xnu-spacewar-identity-debug.img first: $product" }
    $result = Invoke-Fastboot -Arguments @('getvar', 'result') -TimeoutSeconds 15
    if ($result -match 'result:\s*running') { throw 'XNU is already running; collect its log after it returns.' }
    $identity = Invoke-Fastboot -Arguments @('getvar', 'identity') -TimeoutSeconds 15
    $target = Invoke-Fastboot -Arguments @('getvar', 'hardware-target') -TimeoutSeconds 15
    if ($identity -notmatch 'identity:\s*ipsw' -or $target -notmatch 'hardware-target:\s*D22AP') {
        throw "Expected the verified identity loader: $identity $target"
    }
    if ($Touch) {
        if ($GpuMHz -ne 315) {
            try {
                $clockCapability = Invoke-Fastboot -Arguments @('getvar', 'gpu-clocks') -TimeoutSeconds 15
            } catch {
                throw "This GPU clock requires xnu-spacewar-gpu-clocks.img. Boot that loader first. $($_.Exception.Message)"
            }
            if ($clockCapability -notmatch 'gpu-clocks:\s*315,450,550') {
                throw 'This GPU clock requires xnu-spacewar-gpu-clocks.img. Boot that loader first.'
            }
        }
        if ($Quiet) {
            try {
                $quietCapability = Invoke-Fastboot -Arguments @('getvar', 'quiet-ui') -TimeoutSeconds 15
            } catch {
                throw "Quiet mode requires xnu-spacewar-gpu-touch-quiet.img. Boot that loader first. $($_.Exception.Message)"
            }
            if ($quietCapability -notmatch 'quiet-ui:\s*spacewar-v1') {
                throw 'Quiet mode requires xnu-spacewar-gpu-touch-quiet.img. Boot that loader first.'
            }
            $cpuCapability = Invoke-Fastboot -Arguments @('getvar', 'cpu-perf') -TimeoutSeconds 15
            if ($cpuCapability -notmatch 'cpu-perf:\s*spacewar-v1') {
                throw 'Quiet performance mode requires the updated xnu-spacewar-gpu-touch-quiet.img. Boot that loader first.'
            }
        }
        if ($Gpu) {
            try {
                $gpuTouchCapability = Invoke-Fastboot -Arguments @('getvar', 'touch-gpu') -TimeoutSeconds 15
            } catch {
                throw "GPU with touch requires xnu-spacewar-gpu-touch-debug.img. Boot that loader first. $($_.Exception.Message)"
            }
            if ($gpuTouchCapability -notmatch 'touch-gpu:\s*spacewar-v1') {
                throw 'GPU with touch requires xnu-spacewar-gpu-touch-debug.img. Boot that loader first.'
            }
        }
        try {
            $capability = Invoke-Fastboot -Arguments @('getvar', 'touch-lease') -TimeoutSeconds 15
        } catch {
            throw "Touch with USB logging requires xnu-spacewar-touch-live-debug.img. Boot that loader first. $($_.Exception.Message)"
        }
        if ($capability -notmatch 'touch-lease:\s*spacewar-v1') {
            throw 'Touch with USB logging requires xnu-spacewar-touch-live-debug.img. Boot that loader first.'
        }
    }

    Write-Host "Staging system.apfs and $payloadName..."
    Invoke-Fastboot -Arguments @('oem', 'ramdisk') | Write-Host
    Invoke-Fastboot -Arguments @('stage', $system) -TimeoutSeconds 900 | Write-Host
    Invoke-Fastboot -Arguments @('stage', $payload) | Write-Host
    Invoke-Fastboot -Arguments @('oem', 'run') | Write-Host
    if ($LiveLog) {
        Write-Host 'Live logging: XNU uses seven cores, leaving CPU0 with the USB loader.'
        if ($Touch) {
            Write-Host 'Physical touch enabled with coordinated loader ownership. Buttons remain enabled.'
            if ($Gpu) { Write-Host "Adreno GPU acceleration enabled at $GpuMHz MHz; GPU initialization finishes before touch starts." }
            if ($Quiet) {
                Write-Host 'Quiet mode: screen console, routine traces and per-call GL error checks disabled by hakios_debug=0.'
                Write-Host 'CPU-only performance mode: big/prime firmware LUT requests capped at 1920 MHz; CPU0 and DDR unchanged.'
                Write-Host 'Allow about a minute for touch firmware startup before trying taps; routine touch messages are suppressed.'
            } else {
                Write-Host 'Firmware upload may take about a minute; try taps after TOUCH WINDOW: verified runtime. TOUCH READY confirms the first valid report.'
            }
        } else {
            if ($Gpu) { Write-Host "Adreno GPU acceleration enabled through Mesa/OpenGL ES at $GpuMHz MHz. Buttons remain enabled." }
            Write-Host 'Physical touch is disabled in this profile.'
        }
        Write-Host 'No automatic kernel cutoff. Ctrl+C stops collection; it leaves XNU running.'
        Write-Host 'Use fastboot oem stop from another terminal to request return after collecting logs.'
    } elseif (-not $Timed) {
        Write-Host 'Started with no scheduled touch cutoff or kernel return. This script is finished.'
        Write-Host 'Power/volume input and live USB logs remain unavailable in this CPU0 touch configuration.'
        Write-Host 'Five fingers held for 1.5 seconds request return, but multitouch exit is not phone-verified.'
        Write-Host 'After a successful return, run this script with -CollectLog. A hardware restart loses the RAM log.'
        return
    } else {
        Write-Host 'Diagnostic mode: 300-second touch window; kernel return is requested after 390 seconds.'
    }
}

if ($LiveLog) {
    Write-Host "Saving a live kernel snapshot every $LogIntervalSeconds seconds to $liveLogFile"
} else {
    Write-Host 'Waiting for xnuloader to return and saving its log. Ctrl+C stops this collector only.'
}
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$savedLiveLog = $false
while ($true) {
    Start-Sleep -Seconds $(if ($LiveLog -and $savedLiveLog) { $LogIntervalSeconds } else { 2 })
    try {
        $devices = Invoke-Fastboot -Arguments @('devices') -TimeoutSeconds 15
        if ($devices -match '(?m)^\S+\s+fastboot\s*$') {
            $product = Invoke-Fastboot -Arguments @('getvar', 'product') -TimeoutSeconds 15
            if ($product -notmatch 'product:\s*xnuloader') {
                throw 'The connected device is outside xnuloader; this mode cannot fetch the XNU RAM log.'
            }
            $result = Invoke-Fastboot -Arguments @('getvar', 'result') -TimeoutSeconds 15
            if ($LiveLog -and $result -match 'result:\s*running\b') {
                Invoke-Fastboot -Arguments @('oem', 'log') -TimeoutSeconds 15 | Out-Null
                $temporaryLog = "$liveLogFile.downloading"
                Invoke-Fastboot -Arguments @('get_staged', $temporaryLog) -TimeoutSeconds 30 | Out-Null
                if ((Get-Item -LiteralPath $temporaryLog).Length -eq 0) { throw 'Downloaded snapshot is empty.' }
                Move-Item -LiteralPath $temporaryLog -Destination $liveLogFile -Force
                $savedLiveLog = $true
                Write-Host "Saved live kernel log: $liveLogFile ($([int]$timer.Elapsed.TotalSeconds)s)"
            } elseif ($result -match 'result:\s*(returned|early-exception|checkpoint|hung|not-started|no-such-core)\b') {
                # The loader already staged the final log; oem log would replace it.
                Invoke-Fastboot -Arguments @('get_staged', $logFile) -TimeoutSeconds 30 | Write-Host
                if ((Get-Item -LiteralPath $logFile).Length -eq 0) { throw 'Downloaded final log is empty.' }
                Write-Host "Saved final kernel log: $logFile"
                Write-Host $result
                return
            }
        }
    } catch {
        Write-Warning "Collection attempt failed: $($_.Exception.Message)"
    }
    if ($LiveLog -and -not $savedLiveLog -and $timer.Elapsed.TotalSeconds -ge 60) {
        throw 'No live kernel log was retrieved. USB is unavailable or this is not the CPU7 logging profile; repeating the collector will not restore CPU0 USB access.'
    }
    if (-not $LiveLog -and -not $CollectLog -and $timer.Elapsed.TotalSeconds -ge 450) {
        throw 'No completed XNU return after 450 seconds. The collector cannot read USB while CPU0 runs XNU.'
    }
}
