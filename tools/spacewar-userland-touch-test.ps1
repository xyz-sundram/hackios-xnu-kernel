# Host-only regression test. This never invokes the real fastboot or a phone.
# Run with Windows PowerShell 5.1 or later:
# powershell -NoProfile -ExecutionPolicy Bypass -File tools\spacewar-userland-touch-test.ps1
$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot 'spacewar-userland-touch.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$definition = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Fastboot'
}, $true)
if ($null -eq $definition) { throw 'Invoke-Fastboot helper missing' }
# Load just the production helper; do not execute the launcher's main body.
Invoke-Expression $definition.Extent.Text

$build = Join-Path (Split-Path -Parent $PSScriptRoot) 'build'
$temp = Join-Path $build ('spacewar-fastboot-test-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $temp)
try {
    $fastboot = Join-Path $temp 'mock fastboot.exe'
    $logFile = Join-Path $temp 'kernel.log'
    Add-Type -OutputAssembly $fastboot -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Threading;
public class MockFastboot {
    private static int RunFlow(string[] args, string folder, string scenario) {
        string calls = Path.Combine(folder, "mock-calls.txt");
        string countPath = Path.Combine(folder, "result-count.txt");
        string stagedPath = Path.Combine(folder, "staged-content.txt");
        File.AppendAllText(calls, String.Join("|", args) + Environment.NewLine);
        int count = File.Exists(countPath) ? Int32.Parse(File.ReadAllText(countPath)) : 0;
        bool touchBoot = scenario.StartsWith("touchboot");
        if (args[0] == "devices") {
            Console.Out.WriteLine("mockserial\tfastboot");
        } else if (args[0] == "getvar" && args[1] == "product") {
            Console.Error.WriteLine("(bootloader) product: xnuloader");
        } else if (args[0] == "getvar" && args[1] == "result") {
            if (touchBoot && !File.Exists(Path.Combine(folder, "run-started"))) {
                Console.Error.WriteLine("result: idle");
                return 0;
            }
            count++;
            File.WriteAllText(countPath, count.ToString());
            bool returned = count >= (scenario == "retry" ? 4 : 2);
            if (returned) File.WriteAllText(stagedPath, "mock final kernel evidence");
            Console.Error.WriteLine("(bootloader) result: " + (returned ? "returned" : "running"));
        } else if (touchBoot && args[0] == "getvar" && args[1] == "identity") {
            Console.Error.WriteLine("identity: ipsw");
        } else if (touchBoot && args[0] == "getvar" && args[1] == "hardware-target") {
            Console.Error.WriteLine("hardware-target: D22AP");
        } else if (touchBoot && args[0] == "getvar" && args[1] == "touch-lease") {
            Console.Error.WriteLine("touch-lease: " + (scenario == "touchboot-old" ? "unsupported" : "spacewar-v1"));
        } else if (touchBoot && args[0] == "getvar" && args[1] == "touch-gpu") {
            Console.Error.WriteLine("touch-gpu: " + (scenario == "touchboot-gpu-old" ? "unsupported" : "spacewar-v1"));
        } else if (touchBoot && args[0] == "getvar" && args[1] == "quiet-ui") {
            Console.Error.WriteLine("quiet-ui: " + (scenario == "touchboot-quiet-old" ? "unsupported" : "spacewar-v1"));
        } else if (touchBoot && args[0] == "getvar" && args[1] == "cpu-perf") {
            Console.Error.WriteLine("cpu-perf: " + (scenario == "touchboot-quiet-cpu-old" ? "unsupported" : "spacewar-v1"));
        } else if (touchBoot && args[0] == "getvar" && args[1] == "gpu-clocks") {
            Console.Error.WriteLine("gpu-clocks: " + (scenario == "touchboot-clocks-old" ? "315" : "315,450,550"));
        } else if (touchBoot && args[0] == "stage") {
            if (!File.Exists(args[1])) return 98;
        } else if (touchBoot && args[0] == "oem" && args[1] == "ramdisk") {
        } else if (touchBoot && args[0] == "oem" && args[1] == "run") {
            File.WriteAllText(Path.Combine(folder, "run-started"), "yes");
        } else if (args[0] == "oem" && args[1] == "log") {
            File.WriteAllText(stagedPath, "mock live kernel evidence " + count);
        } else if (args[0] == "get_staged") {
            if (scenario == "retry" && count == 2) {
                string[] live = Directory.GetFiles(folder, "*.live.log");
                bool preserved = live.Length == 1 && File.ReadAllText(live[0]) == "mock live kernel evidence 1";
                File.WriteAllText(args[1], "partial transfer");
                preserved = preserved && File.ReadAllText(live[0]) == "mock live kernel evidence 1";
                File.WriteAllText(Path.Combine(folder, "preserved-after-failure.txt"), preserved.ToString());
                Console.Error.WriteLine("FAILED (remote: mock transient transfer failure)");
                return 7;
            }
            File.WriteAllText(args[1], File.ReadAllText(stagedPath));
        } else {
            Console.Error.WriteLine("Unexpected mock command: " + String.Join(" ", args));
            return 99;
        }
        Console.Error.WriteLine("OKAY [  0.000s]");
        return 0;
    }
    public static int Main(string[] args) {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string scenarioPath = Path.Combine(folder, "scenario.txt");
        if (File.Exists(scenarioPath)) return RunFlow(args, folder, File.ReadAllText(scenarioPath));
        switch (args[0]) {
            case "ok":
                Console.Out.WriteLine("normal stdout");
                Console.Error.WriteLine("OKAY [  0.000s]");
                Console.Error.WriteLine("Finished. Total time: 0.000s");
                return 0;
            case "fail":
                Console.Error.WriteLine("FAILED (remote: mock failure)");
                return 7;
            case "echo":
                for (int i = 1; i < args.Length; i++)
                    Console.Out.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(args[i])));
                return 0;
            case "flood":
                Console.Out.Write(new string('O', 131072));
                Console.Error.Write(new string('E', 131072));
                return 0;
            case "wait":
                Console.Error.WriteLine("mock waiting");
                Thread.Sleep(10000);
                return 0;
            default: return 99;
        }
    }
}
'@

    $output = Invoke-Fastboot -Arguments @('ok')
    if ($output -notmatch 'normal stdout' -or $output -notmatch 'OKAY \[') { throw 'Successful stderr/stdout was lost' }
    Write-Host 'PASS normal fastboot OKAY on stderr remains text with ErrorActionPreference=Stop'

    try {
        Invoke-Fastboot -Arguments @('fail') | Out-Null
        throw 'A nonzero exit was accepted'
    } catch {
        if ($_.Exception.Message -notmatch 'exited with code 7:.*FAILED') { throw }
    }
    Write-Host 'PASS a real nonzero exit raises an error containing native stderr'

    $argumentsToEcho = @('a path with spaces', '', 'C:\path with spaces\', 'before"after', 'backslash\"quote', 'plain')
    $actual = (Invoke-Fastboot -Arguments (@('echo') + $argumentsToEcho)).TrimEnd("`r", "`n") -split '\r?\n'
    $expected = @($argumentsToEcho | ForEach-Object { [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($_)) })
    if (($actual -join '|') -cne ($expected -join '|')) { throw 'Native argument boundaries changed' }
    Write-Host 'PASS spaces, empty arguments, embedded quotes and trailing backslashes survive Windows argument parsing'

    $output = Invoke-Fastboot -Arguments @('flood')
    if ($output.Length -ne 262144) { throw 'Concurrent pipe drain lost output' }
    Write-Host 'PASS both native pipes drain beyond their buffer sizes without deadlock'

    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        Invoke-Fastboot -Arguments @('wait') -TimeoutSeconds 1 | Out-Null
        throw 'A timed-out process was accepted'
    } catch {
        if ($_.Exception.Message -notmatch 'timed out after 1 seconds') { throw }
    }
    if ($timer.Elapsed.TotalSeconds -ge 5) { throw 'Native command timeout did not bound the wait' }
    $hostLog = Get-Content -LiteralPath ($logFile + '.fastboot.txt') -Raw
    if ($hostLog -notmatch 'exit=7' -or $hostLog -notmatch 'timeout=True' -or $hostLog -notmatch 'mock waiting') {
        throw 'Host command transcript is incomplete'
    }
    if (Test-Path -LiteralPath $logFile) { throw 'Host transcript was confused with a kernel log' }
    Write-Host 'PASS bounded timeout preserves a separate host transcript without creating a fake kernel log'

    foreach ($scenario in @('normal', 'retry')) {
        $flow = Join-Path $temp ("flow with spaces $scenario")
        [void](New-Item -ItemType Directory -Path $flow)
        Copy-Item -LiteralPath $fastboot -Destination (Join-Path $flow 'fastboot.exe')
        Set-Content -LiteralPath (Join-Path $flow 'scenario.txt') -Value $scenario -NoNewline

        # Execute the complete production collector, including real sleeps and
        # filesystem moves; only its native executable is replaced by our mock.
        & $sourcePath -PlatformTools $flow -LiveLog -CollectLog -LogIntervalSeconds 2
        $liveFiles = @(Get-ChildItem -LiteralPath $flow -Filter '*.live.log')
        $finalFiles = @(Get-ChildItem -LiteralPath $flow -Filter 'spacewar-userland-touch-*.log')
        $hostFiles = @(Get-ChildItem -LiteralPath $flow -Filter '*.fastboot.txt')
        if ($liveFiles.Count -ne 1 -or $finalFiles.Count -ne 1 -or $hostFiles.Count -ne 1) {
            throw "Collector did not save distinct live/final/host files for $scenario"
        }
        $lastLive = if ($scenario -eq 'retry') { 'mock live kernel evidence 3' } else { 'mock live kernel evidence 1' }
        if ((Get-Content -LiteralPath $liveFiles[0].FullName -Raw) -cne $lastLive) { throw "Wrong live snapshot for $scenario" }
        if ((Get-Content -LiteralPath $finalFiles[0].FullName -Raw) -cne 'mock final kernel evidence') { throw 'Final staged evidence was replaced' }
        $calls = @(Get-Content -LiteralPath (Join-Path $flow 'mock-calls.txt'))
        $expectedLiveCount = if ($scenario -eq 'retry') { 3 } else { 1 }
        if (@($calls | Where-Object { $_ -eq 'oem|log' }).Count -ne $expectedLiveCount) {
            throw 'Collector issued oem log after a final result or missed a live result'
        }
        if ($calls[-1] -notmatch '^get_staged\|.*spacewar-userland-touch-.*\.log$' -or
            $calls[-2] -ne 'getvar|result') { throw 'Final evidence was not fetched directly after its result' }
        if ($scenario -eq 'retry') {
            if ((Get-Content -LiteralPath (Join-Path $flow 'preserved-after-failure.txt') -Raw) -cne 'True') {
                throw 'A partial transfer replaced the last successful live log'
            }
            $hostLog = Get-Content -LiteralPath $hostFiles[0].FullName -Raw
            if ($hostLog -notmatch 'mock transient transfer failure') { throw 'Transient failure was absent from host transcript' }
            Write-Host 'PASS full live collector preserves the last snapshot through a partial transfer, retries and saves final evidence'
        } else {
            Write-Host 'PASS full live collector saves running evidence before return and fetches the final staged log without replacing it'
        }
    }
    foreach ($scenario in @('touchboot-new', 'touchboot-old', 'touchboot-gpu', 'touchboot-gpu-old', 'touchboot-quiet', 'touchboot-quiet-old', 'touchboot-quiet-cpu-old', 'touchboot-clocks450', 'touchboot-clocks550', 'touchboot-clocks-old')) {
        $flow = Join-Path $temp $scenario
        [void](New-Item -ItemType Directory -Path $flow)
        Copy-Item -LiteralPath $fastboot -Destination (Join-Path $flow 'fastboot.exe')
        Set-Content -LiteralPath (Join-Path $flow 'scenario.txt') -Value $scenario -NoNewline
        Set-Content -LiteralPath (Join-Path $flow 'system.apfs') -Value 'mock root' -NoNewline
        Set-Content -LiteralPath (Join-Path $flow 'spacewar-userland-touch-live-payload.bin') -Value 'mock payload' -NoNewline
        Set-Content -LiteralPath (Join-Path $flow 'spacewar-userland-gpu-touch-payload.bin') -Value 'mock GPU touch payload' -NoNewline
        Set-Content -LiteralPath (Join-Path $flow 'spacewar-userland-gpu-touch-quiet-payload.bin') -Value 'mock quiet GPU touch payload' -NoNewline
        Set-Content -LiteralPath (Join-Path $flow 'spacewar-userland-gpu450-touch-quiet-payload.bin') -Value 'mock450 payload' -NoNewline
        Set-Content -LiteralPath (Join-Path $flow 'spacewar-userland-gpu550-touch-quiet-payload.bin') -Value 'mock550 payload' -NoNewline
        $gpuClock = if ($scenario -eq 'touchboot-clocks550') { 550 } elseif ($scenario -like 'touchboot-clocks*') { 450 } else { 315 }
        $quietTouch = $scenario -like 'touchboot-quiet*' -or $gpuClock -ne 315
        $gpuTouch = $scenario -like 'touchboot-gpu*' -or $quietTouch
        $expectedLoader = if ($gpuClock -ne 315) { 'requires xnu-spacewar-gpu-clocks.img' } elseif ($scenario -eq 'touchboot-quiet-cpu-old') { 'requires the updated xnu-spacewar-gpu-touch-quiet.img' } elseif ($quietTouch) { 'requires xnu-spacewar-gpu-touch-quiet.img' } elseif ($gpuTouch) { 'requires xnu-spacewar-gpu-touch-debug.img' } else { 'requires xnu-spacewar-touch-live-debug.img' }
        $expectedPayload = if ($gpuClock -ne 315) { "spacewar-userland-gpu${gpuClock}-touch-quiet-payload.bin" } elseif ($quietTouch) { 'spacewar-userland-gpu-touch-quiet-payload.bin' } elseif ($gpuTouch) { 'spacewar-userland-gpu-touch-payload.bin' } else { 'spacewar-userland-touch-live-payload.bin' }
        if ($scenario -like '*-old') {
            try {
                & $sourcePath -PlatformTools $flow -LiveLog -Touch -Gpu:$gpuTouch -Quiet:$quietTouch -GpuMHz $gpuClock -LogIntervalSeconds 2
                throw 'An incompatible loader was accepted'
            } catch {
                if ($_.Exception.Message -notmatch [regex]::Escape($expectedLoader)) { throw }
            }
            $calls = @(Get-Content -LiteralPath (Join-Path $flow 'mock-calls.txt'))
            if (@($calls | Where-Object { $_ -match '^(stage|oem)\|' }).Count) { throw 'Incompatible loader received a staging or run command' }
            Write-Host 'PASS live touch refuses an incompatible loader before staging or running'
        } else {
            & $sourcePath -PlatformTools $flow -LiveLog -Touch -Gpu:$gpuTouch -Quiet:$quietTouch -GpuMHz $gpuClock -LogIntervalSeconds 2
            $calls = @(Get-Content -LiteralPath (Join-Path $flow 'mock-calls.txt'))
            if (@($calls | Where-Object { $_ -match ('^stage\|.*' + [regex]::Escape($expectedPayload) + '$') }).Count -ne 1 -or
                @($calls | Where-Object { $_ -eq 'oem|run' }).Count -ne 1 -or
                @(Get-ChildItem -LiteralPath $flow -Filter '*.live.log').Count -ne 1) { throw 'Live-touch startup or logging failed' }
            Write-Host 'PASS compatible loader stages the live-touch payload, starts XNU and saves live evidence'
        }
    }
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force
}
