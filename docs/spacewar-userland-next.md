# Spacewar phone userland preflight

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The next test enables physical touch while retaining CPU7/SMP7, buttons and
live USB logs. It uses a matched loader/kernel with coordinated hardware
ownership, not the old CPU0 probe. It is built and host-tested; the phone run
is pending. See [touch live instructions](spacewar-touch-live.md).

The CPU7 live-log payload has now passed its first phone run: the user reports
wallpaper and working keys. The saved log has36 bootstrap checks and80
surface checks with zero failures, running SpringBoard/backboardd/mediaserverd,
and five complete volume-key cycles per direction reaching user space. No
kernel panic or restart of these services is recorded in the snapshot.
Physical touch was disabled. Exact images and evidence are frozen in
build/spacewar-live-gui-working-baseline/ (`build/spacewar-live-gui-working-baseline/manifest.json`).
See [result, latency caveats and logging](spacewar-userland-touch.md).

Log collection now takes priority over another CPU0 touch run. The corrected
Windows PowerShell launcher supports `-LiveLog`: a CPU7/SMP7 payload leaves
CPU0 with UEFI and downloads live snapshots every15 seconds, without relying
on final return. This uses the phone-tested home-screen kernel/root with
physical touch disabled, so it can diagnose startup/wallpaper/services but
not the physical-touch fault. Native fastboot stderr handling has been fixed
and tested in Windows PowerShell5.1. See
[logging instructions and limitations](spacewar-userland-touch.md).

The extended touch-window phone run reached iOS, but the user reports a tap
turning into a long press/edit mode, then no touch; missing wallpaper and
volume/power response; and a screen stuck in iOS after the return deadline.
The collector was interrupted and no final phone log was recovered. The
return is failed/unverified, not a successful timed return. A new continuous
payload removes the two scheduled cutoffs; its phone test remains pending.
See [current result and continuous-mode instructions](spacewar-userland-touch.md).

The first CPU0 touch plus graphical userland run reached the home screen after about 2.5 minutes, but the user observed no touch response. That payload set `touch_seconds=60`, the kernel's maximum, and the driver holds the controller in reset when the window ends. This likely expired before the home screen was ready; the phone left fastboot before a log could be saved, so the cause is not yet confirmed by that run. Physical touch had already passed 279 input events with zero rejected reports in the separate kernel test.

The failed extended test used `spacewar-userland-touch-window-payload.bin`: a rebuilt kernel permits up to 600 seconds, with 300 seconds selected for this run, `touch_lat=1` for input-path diagnostics, and a 390-second timed return. It retains the CPU0/buttons-off touch ownership conditions and existing APFS images and identity loader. USB is unavailable while XNU owns CPU0. Historical manifest (`build/spacewar-userland-touch-window/manifest.json`).

The earlier seven-core home-screen baseline below remains preserved and touch-disabled. It is not the command line for the new CPU0 touch test.

The phone has now reached the home screen, as reported by the user after
starting the full SMP7 userland from the identity-verified loader. The saved
`logs/spacewar-userland-smp7-home-live.log`
supports the boot sequence: IPSW identity, APFS system root plus the small
`md1` image, 36 bootstrap checks with zero failures, real launchd, and
SpringBoard/backboardd spawning. No actual panic or hardware-target query
failure appears in that live snapshot. The home-screen observation is the
user's later visual report; a framebuffer capture and final timed-return log
for this run have not yet been collected.

The working combination is **`xnu-spacewar-identity-debug.img`** (SHA-256
`64c252db2608e6da1c4fe8c806c0316c88aeca20125d2514f66cc1c6babb0dfb`),
**`spacewar-userland-smp7-payload.bin`** (SHA-256
`6da2060e56bd81feefab726df05fbcec3f6d581bac247f1f057ef93d5defe306`)
and the existing validated `system.apfs`. The older
`xnu-spacewar-userland-debug.img` with hash `f341ceb4...` is preserved, but is
not the boot image associated with this successful visual report. Physical
touch remains disabled and untested. This milestone does not establish native
GPU acceleration, persistent storage or the other unported physical drivers.

## First full-userland phone result

The first SMP7 full-userland run mounted the APFS root and passed all 36
bootstrap checks, then failed immediately after `exec /sbin/launchd.darwin`.
PID 1 exited with namespace 35 (`OS_REASON_LIBIGNITION`), subcode 6,
`failed to query hardware target: 22`; XNU consequently reported the initproc
panic. SpringBoard was not reached. Canonical evidence:
`logs/spacewar-userland-smp7-failure-hw-target.log`.

The exact failing query is **`hw.target`**, issued by the libignition startup
code built into `/usr/lib/dyld` before launchd's main function. The local iOS
16.5 binaries identify dyld 1066.8 and libignition 51. Disassembly of the
matching shared-cache image loads the `hw.target` string at
`0x1A461FDE0`–`0x1A461FDE4`, performs the sysctl query, and routes its nonzero
error to the observed failure description at `0x1A46201C8`–`0x1A46201CC`.
The primary cache's image-info entry identifies this image as `/usr/lib/dyld`.

The kernel requirement is:

1. `xnu-src/xnu-8796.121.2/bsd/kern/kern_mib.c`, `HW_TARGET`,
   calls `PEGetTargetName()` and returns `EINVAL` (22) when it returns false.
2. `xnu-src/xnu-8796.121.2/iokit/Kernel/IOPlatformExpert.cpp`,
   `PEGetTargetName()`, dispatches to the active platform expert, or returns
   false if none is available.
3. On ARM64, `IODTPlatformExpert::getTargetName()` delegates to `getModelName()`.
4. `getModelName()` reads the root provider's **`compatible`** OSData property
   and returns false when it is absent. With the real iPhone identity,
   `hw.target` should return `D22AP`. The `target-type` property is also needed
   by other identity paths, but is not this sysctl's direct source.

A missing identity property or unavailable platform expert is consistent with
the first failure. The placeholder loader intentionally omits real identity
properties, whereas the real-identity image includes them. The identity that
reached the kernel in that failed run remains unknown; the user reports that
the boot command succeeded, so reusing a placeholder loader remains a
hypothesis.

## Identity diagnostic passed on the phone

The subsequent diagnostic boot used `xnu-spacewar-identity-debug.img`. Its
loader reports `identity: ipsw`, model `iPhone10,3`, compatible `D22AP` and
target type `D22`. The raw static probe then read:

```text
hw.machine=iPhone10,3
hw.model=D22AP
hw.target=D22AP
hw.product=iPhone10,3
```

It ran as PID 1 with uid 0 and returned cleanly after 15 seconds. There was no
current panic, early exception or identity-query error. The kernel is the same
eight-driver `b3cef03d...` build used by the failed userland run; the diagnostic
loader only adds identity reporting and does not alter device-tree population.
This establishes working kernel identity queries in the verified loader
session. The diagnostic alone did not establish graphical startup; the later
home-screen report is recorded above. It does not prove why the earlier
session failed. Saved evidence:
`logs/spacewar-identity-success.log` and its
SHA-256 sidecar.

The diagnostic intentionally omitted SoC and thermal runtime arguments, so
its `soc_name=unknown`, `soc_id=-1` and empty thermal readings are expected.
Those drivers remain verified by their separate hardware tests.

Exact binary/source trace:
`build/spacewar-userland-fix/hardware-target-report.json`
and `build/spacewar-userland-fix/hardware-target-query.disassembly.txt`.

## Payload and hardware baseline

The successful reported home-screen run used
`spacewar-userland-smp7-payload.bin` with the eight-driver kernel,
`hakios-kernel-spacewar-touch-probes`, with the existing validated small APFS
root. Its runtime arguments enable the phone-tested seven-core setup
(`bootcpu=7 cpus=7`), physical buttons, SoC identity and thermal polling. It
allows 240 seconds to initialize and show the screen, with `sb_delay=8` and a
refreshed epoch. CPU0 remains with UEFI and USB; XNU uses physical CPUs 7–1.

Physical touch stays off: the `touchscreen` argument is absent, so the compiled
driver returns before starting its thread or accessing hardware. The kernel's
generated header has `HAKIOS_DEVICE_SPACEWAR=1`, which forces the Spacewar
profile if touch is explicitly requested and refuses mutating stages. GPU,
UFS, CPU-frequency control, modem and audio are also absent from this command
line. The existing root's synthetic unlock gesture remains available.

The 19,184,416-byte payload has SHA-256
`6da2060e56bd81feefab726df05fbcec3f6d581bac247f1f057ef93d5defe306`.
Its exact kernel/root/version bytes, XNUP bounds, 237-character command line,
board guard and example RAM layout passed inspection. The new kernel has
SHA-256 `b3cef03db7c405c72ba063381fa67444b12e7a7132478436258ddbba8519a94e`.
Its first phone boot has now passed the read-only touch-stage-1 test, including
the state reads, raw `hello` and timed return. This verifies booting the new
kernel, not the full graphical userland. All three required files have now
been copied to Windows and their destination hashes verified. The first full
phone run failed before launchd's main function as described above. After the
identity diagnostic, the next run reached the home screen according to the
user, with live service logs saved. The earlier failure and identity evidence
remain preserved. No QEMU run or image rebuild was needed for that later
phone test.

Manifest: `build/spacewar-userland-smp7/manifest.json`.
Inspection: `build/spacewar-userland-smp7/inspect.log`.
Checksums: `build/spacewar-userland-smp7/SHA256SUMS`.

The older `spacewar-userland-240-payload.bin` is preserved as a separate
single-core six-driver fallback. Its manifest and checksums remain under
`build/spacewar-userland-phone/`. The original 120-second payload, APFS images
and Windows bundle are also unchanged.

## RAM and loader checks

The successful phone log claims a kernel memory window at `0xF4000000`, size
`0x180000000`. The loader places the resident system image at window base plus
`0x08000000`, so this example starts at `0xFC000000` and ends at
`0x18F9FB000` for the 2,476,716,032-byte system image. The kernel and small root
end at `0xF85E0000` with the new kernel, below the resident image. The upload limit reserves
`0x30000000` for the kernel and is capped at the protocol's `0xFFFFFFFF` bytes;
the system image fits. The loader claims a fresh window dynamically and checks
the sizes and overlap again, so the payload has no fixed physical-base or
`rdres` override.

The current diagnostic session runs `xnu-spacewar-identity-debug.img`, whose
IPSW-derived identity has now been verified through the kernel. Continue from
that resident loader after the 15-second identity probe returns; no reboot or
additional `fastboot boot` is needed for the next staged payload. If a fresh
session is required later, enter the stock bootloader before booting that
image, then verify `identity=ipsw`, `hardware-target=D22AP` and
`model=iPhone10,3`. The older working RAM-fix debug image contains the
placeholder identity and is suitable for raw `hello`, not the complete
Darwin userland. The diagnostic image retains the working Spacewar UEFI and
current-ROM DTB; inspection is in `logs/spacewar-identity-boot-inspect.log`.

## Repeating the working phone sequence

The main agent has copied `xnu-spacewar-identity-debug.img` and
`userland/system.apfs` (as `system.apfs`) to `C:\platform-tools` with `./copy`.
Both destination SHA-256 hashes match the validated source manifest, including
the full 2,476,716,032-byte system image. This verification is recorded in the
SMP7 manifest. `spacewar-userland-smp7-payload.bin` has also been copied with
`./copy` and its destination hash verified. The existing Windows fastboot
client is left intact; the separately prepared Google platform-tools 37.0.1
client remains in `build/spacewar-userland-windows/platform-tools/`.

From an **identity-verified resident loader after its previous kernel has
returned**, run in PowerShell. If a new session is needed, boot
`xnu-spacewar-identity-debug.img` from stock fastboot first. While the current
graphical run is still active, use only its status/log/screenshot queries.

```powershell
./fastboot getvar product
./fastboot getvar identity
./fastboot getvar hardware-target
./fastboot getvar model
./fastboot oem ramdisk
./fastboot getvar max-download-size
./fastboot stage system.apfs
./fastboot stage spacewar-userland-smp7-payload.bin
./fastboot oem run
Start-Sleep -Seconds 15
$status = ./fastboot getvar result 2>&1 | Out-String
$status
if ($status -match 'result:\s*running') { ./fastboot oem log }
./fastboot get_staged spacewar-userland-smp7-15s.log
Select-String -Path .\spacewar-userland-smp7-15s.log -Pattern 'identity:','stage0:','init:','launchd','SpringBoard','backboardd','failed to query','processors running','EARLY KERNEL EXCEPTION','panic\(cpu'
```

This first check catches another early startup failure after 15 seconds rather
than waiting for the full four-minute budget. If the status is already
`returned`, fetch the staged log directly: it contains the completed kernel
log, and `oem log` must not replace it. Issue `oem log` only while the kernel
is still running; that command composes a live copy. If startup continues,
observe the phone and collect the final result after the budget:

```powershell
Start-Sleep -Seconds 235
./fastboot getvar result
./fastboot get_staged spacewar-userland-smp7.log
Select-String -Path .\spacewar-userland-smp7.log -Pattern 'identity:','launchd','SpringBoard','backboardd','processors running','thermal:','loader_return_after expired','EARLY KERNEL EXCEPTION','panic\(cpu'
```

`oem ramdisk` directs the next download to the large resident region; the
subsequent small payload download returns to the normal XNUP region. Preserve
that order. These operations boot from RAM. The seven-core setup leaves CPU0
with UEFI, so USB status/log/screenshot queries can remain available during the
run. `getvar result` may say `running` until the budget expires. The expected
sequence is APFS root mount, stage0 mounting the small image at `/redwood`, the
port's init handing over to real launchd, and graphical services starting.
The user has reported a visible home screen in this configuration; physical
touch interaction and broader UI behavior remain untested.

After saving the log, a raw framebuffer capture can be collected with
`getvar screen`, `oem screenshot` and `get_staged spacewar-userland-smp7.raw`.
The screenshot replaces the staged log buffer, so retrieve the log first.
For a live snapshot before the timer expires, use `oem log` followed by
`get_staged spacewar-userland-smp7-live.log`; that stages a copy of the running
kernel's log without modifying its buffer. A screenshot can then follow. Keep
the full final log as well, and require the delayed callback to show seven
processors running, followed by the clean timed return.

## Interrupted emulator follow-up

A longer isolated emulator experiment was stopped at the user's request. Its
controller exited 130 after interruption, after the 180-second capture and
before the 240-second capture. This is neither a pass nor a failure. Its
partial captures (90 seconds: Apple logo; 180 seconds: black) are inconclusive.
No further emulator work is planned. No shared source, image or phone payload
was changed by that experiment. Its record is
`build/qemu-host/userland-next/result.txt`; partial log:
`logs/spacewar-qemu-userland-next.log`.
