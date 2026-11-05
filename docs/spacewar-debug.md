# Phone (1) fastboot debugging

Boot the debug XNU loader once, then stage XNUP RAM payloads for each test.
The loader keeps CPU0 for UEFI, USB and installed-storage I/O; XNU uses seven
cores with the existing coordinated touch/GPU ownership policy.

| File | Purpose |
|---|---|
| `loader-debug.img` | Small RAM boot image; waits in the debug loader without starting XNU |
| `baseline-debug.bin` | Existing userland/services profile, with verbose/debug output |
| `brightness-debug.bin` | Same profile plus a five-minute brightness/IORegistry capture |
| `recovery-readonly.bin` | Static PID1 exports the latest saved audio/debug log with read-only HFS |
| `recovery-replay.bin` | Separate recovery variant: replay the HFS journal, then remount read-only |

These files use the already installed matching HakiOS system/data layout.
Building them does not contact a phone. Booting and staging do not flash boot
images or change slots. Normal userland runs use the existing writable data
volume; the brightness probe saves a mode-0600 log capped at 2 MiB there.
The replay recovery variant can write journal/mount metadata on that volume.

Debug bundles enable `-v`, `hakios_debug=1` and loader startup tracing. They
retain the existing Wi-Fi, battery and continuous low-output speaker policy.
They do not add raw partition log writes or change the normal kernel/image.
`fastboot getvar debug` reports `start-trace=1 raw-log=0` in the supplied loader.

The focused native brightness profile now sustains live USB logging through
iOS startup, UI testing, orderly stop and another RAM payload in the same
resident U-Boot loader. It keeps touch/GPU/storage/WLAN enabled and leaves
ADSP, battery probing and sound idle during brightness debugging. Use this
profile for repeated brightness work; stock fastboot is needed only to enter
the resident loader initially or recover an unreachable session.

The earlier full services profile retained four early kernel/userland
snapshots before USB disappeared. A fresh loader plus staged journal-replay recovery exported
the exact saved brightness log with CRC/SHA-256 validation and returned to the
idle loader. The recovery workflow therefore remains usable when a normal
userland run loses USB.

## Fast iteration on the USB host

Use native paths and tools on the computer that owns USB. On Windows, copy the
bundle into `C:\platform-tools\hakios-debug`, open PowerShell in that directory,
and use `..\fastboot.exe`. Start with the phone in **stock bootloader fastboot**:

```powershell
..\fastboot.exe boot .\loader-debug.img
```

Select **HakiOS** in the RAM menu. It enters an idle XNU loader. Its USB serial
is `xnuloader`, which differs from the stock bootloader's serial. Then:

```powershell
..\fastboot.exe -s xnuloader getvar debug
..\fastboot.exe -s xnuloader stage .\brightness-debug.bin
..\fastboot.exe -s xnuloader oem run
..\fastboot.exe -s xnuloader getvar result
..\fastboot.exe -s xnuloader oem log
..\fastboot.exe -s xnuloader get_staged .\brightness-live.private.log
```

`oem log` prepares a snapshot; `get_staged` downloads it. Neither command alone
does both. Start collecting immediately after `oem run`, before launchd starts.
Use Control Center and Settings while capturing the brightness run.

The included Python runner automates identity checks, staging and repeated
captures. Run it with Windows Python on a Windows USB host:

```powershell
py -3 .\spacewar-debug.py --fastboot ..\fastboot.exe run .\brightness-debug.bin --out .\logs\brightness-001
py -3 .\spacewar-debug.py --fastboot ..\fastboot.exe stop
py -3 .\spacewar-debug.py --fastboot ..\fastboot.exe run .\baseline-debug.bin --out .\logs\baseline-001
```

Each output directory must be new. Before starting a new run, the runner saves
`loader-before-run.private.log`, preserving the startup trace that `oem run`
otherwise resets. If the previous kernel returned and its full log is still
staged, the runner first retains `kernel-returned.private.log`, including the
kernel message buffer; an idle `oem log` would replace that evidence.
The runner keeps the latest snapshot and
the last 32 complete snapshots, plus transport errors and session metadata.
It does not stop the kernel when capture ends. `stop` requests orderly return
and waits for confirmation; stage the next payload only after that succeeds.
An existing run can be captured separately with `capture --out NEW_DIRECTORY`.
For a bounded test, `--until TEXT` finishes capture on that log marker and
records it in `session.json`; the kernel remains running. The marker must
come from the test being captured, such as the full Metal probe's final
`probe: 0 failures` line.

If USB disappears, the runner retains earlier snapshots and retries until its
deadline. Reboot into stock fastboot and boot `loader-debug.img` again when a
fresh firmware/kernel session is needed. A RAM payload cannot replace an
unreachable loader through stock fastboot.

## Recover a saved capture

The brightness probe announces its exact `hakios-debug-XXXXXX` filename in the
console and saves brightness/API/registry output plus periodic filtered kernel
snapshots. Each sample is flushed and synced. The probe changes no brightness
controls; this package is a diagnostic, not a brightness fix.

After stopping the kernel, or after a fresh idle-loader boot, run:

```powershell
py -3 .\spacewar-debug.py --fastboot ..\fastboot.exe run .\recovery-readonly.bin --out .\logs\recovery-001
```

Recovery payloads start a small static exporter instead of launchd. The runner
automatically validates LR1 frame sequence, length and CRC, then saves the
decoded capture and SHA-256 manifest. Without an exact name in the built
recovery helper, it selects the newest eligible audio/debug file. The
`--name` runner option can restrict which exported name is accepted; it does
not change which file the phone's helper selects.

The runner stops capture when the exporter reports a final unavailable result,
retaining the exact error in `recovery-errors.json`. An initial read-only
refusal is expected in the replay variant and does not terminate collection.
If read-only HFS mounting refuses a dirty journal, stop that run, then use
`recovery-replay.bin` in a new output directory. Both recovery variants are
separate from normal userland and never launch an application installer.

## Rebuild the bundle

From the repository, supply the audited local inputs explicitly:

```sh
source xnu-env.sh
python3 tools/spacewar-debug-package.py \
  --kernel-dir /private/audited-kernel-build \
  --baseline-root /private/clean-normal-ram-root \
  --install-map /private/install-persist.map \
  --uboot /private/u-boot.bin --dtb /private/spacewar.dtb \
  --gpu-fw /private/fw-gpu --output build/debug-new
```

`--recovery-name hakios-debug-Ab12cD` builds the two recovery payloads for one
exact capture instead of choosing the latest. The builder checks the kernel
manifest, driver profile, service-policy header, full loader argument budget,
APFS images, XNUP entries, bootstrap container and boot image. `bundle.json`,
per-payload audits and `SHA256SUMS` record the resulting local artifacts.
