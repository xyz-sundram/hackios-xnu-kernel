# Spacewar graphical touch: current result and continuous mode

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The next phone test adds physical touch to this seven-core/buttons-on live-log
profile using a matched kernel and loader. Host checks pass; phone behavior
is pending. Follow [touch with seven cores and USB logs](spacewar-touch-live.md).
The older CPU0-only touch path and its limitations are retained below for
comparison.

## Live-log phone result, 2026-11-04

The user reports visible wallpaper and properly working physical keys in the
CPU7 logging run. The saved snapshot passes all 36 bootstrap checks and all
80 surface checks. SpringBoard PID8, backboardd PID12 and mediaserverd PID36
reach running state, with no recorded exit/restart or kernel panic in this
snapshot. Five complete volume-up and five complete volume-down cycles reach
backboardd and its SpringBoard destination. Power-key behavior and visible
wallpaper are user observations; this frozen snapshot contains no power-key
cycle or explicit wallpaper decode-success message.

One dropped SPMI read recovered: the later summary still reports only one
failure in 7,506 reads, followed by working volume events. Some input delivery
was delayed during startup: two short volume-down pairs reached backboardd
roughly one second after the driver's press/release log. This does not locate
the delay within logging, posting or user-space dispatch, and does not prove
the earlier physical-touch failure's cause. No memory-pressure status summary
was captured; absence of an OOM marker is not a measurement of free memory.

The host transcript records successful snapshots through 09:18:49 local,
about 124 seconds after `oem run` was acknowledged. The older kernel wall
clock comes from the payload's packaging epoch, so the clock difference does
not establish stale logs. This is a short successful graphical baseline;
longer stability and final return remain unverified. Physical touch was
disabled; the recorded complete touch gesture is synthetic.

Exact images, source launcher, original phone log and host transcript are
frozen with checksums under
build/spacewar-live-gui-working-baseline/ (`build/spacewar-live-gui-working-baseline/manifest.json`).
The log contains one NUL in an unrelated CoreData error string; scan it with
`rg -a` or a binary-safe reader to include the complete snapshot.

## Collecting live logs

The continuous CPU0 touch run cannot provide live USB logs, and its final
return already failed in the reported GUI test. Repeating that run does not
establish a recoverable logging path. Use the CPU7 logging profile first:

```powershell
# From stock fastboot; the first command starts the existing identity loader.
.\fastboot boot .\xnu-spacewar-identity-debug.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-touch.ps1 -LiveLog
```

`spacewar-userland-live-log-payload.bin` reuses the exact kernel, root image
and version from the phone-tested SMP7 home-screen baseline. Only the epoch
and `loader_return_after=0` differ. XNU runs on physical CPUs 7–1; CPU0 stays
with UEFI and USB. Physical buttons are enabled, physical touch is disabled.
This profile can supply startup, wallpaper and service logs, but cannot
reproduce the physical-touch bug. This payload has now supplied live logs and
the successful visual/key result above on the phone.

The launcher captures `oem log` followed by `get_staged` every 15 seconds,
starting early in boot. The latest successful snapshot is
`C:\platform-tools\spacewar-userland-<timestamp>.live.log`. Downloads use a
temporary file, so a failed transfer preserves the previous snapshot. If
XNU returns, the final log is downloaded directly without replacing it with
`oem log`. There is no scheduled kernel cutoff. Ctrl+C stops the collector;
`fastboot oem stop` in another terminal requests return. Already downloaded
snapshots remain available even if the kernel fails to return.

Windows PowerShell 5.1 normally wraps native stderr in `NativeCommandError`;
fastboot writes successful `OKAY` status lines there too. The launcher now
uses `.NET Process` with separate output streams, actual exit-code checks
and command timeouts. Its `.fastboot.txt` transcript records host commands
and results; it is not a kernel log. The production helper was tested in
actual Windows PowerShell 5.1 against a local mock executable, without
contacting the phone.

An attempted read-only status query after the reported script error saw a
fastboot USB entry, but `getvar product` timed out after 10 seconds. No XNU
kernel log was recovered and no reboot was issued. The previous CPU0 run's
log remains unavailable.

Packaging and verification:
logging repair manifest (`build/spacewar-logging-repair/manifest.json`).

## Reported GUI failure

The user reports that `spacewar-userland-touch-window-test.ps1` reached iOS,
but tapping Settings became a long press, entered home-screen edit mode, then
touch stopped responding. Wallpaper and volume/power input were unavailable.
The phone remained stuck in iOS after the configured return time. The user
interrupted the PC collector with Ctrl+C; no final phone log was recovered.
This is a failed graphical interaction/return test, despite the earlier
successful kernel-only touch test. The cause of the long press, missing
wallpaper and failed return is not established.

That payload set `touch_seconds=300`: after this interval following firmware
startup, the driver releases its contacts and resets the controller. It also
set `loader_return_after=390`, independently requesting return to UEFI after
390 seconds of kernel startup. These are diagnostic limits, not iOS limits.
Ctrl+C stops the PC collector, not XNU on the phone.

## Continuous payload

`spacewar-userland-touch-continuous-payload.bin` uses a rebuilt kernel with
`touch_seconds=0` and `loader_return_after=0`. Neither touch nor the kernel has
a scheduled cutoff. In older kernels, `touch_seconds=0` falls back to **20
seconds**; use the new payload rather than changing arguments on an old one.
Omitting the argument still gives a 20-second probe, and finite requests from
1 through 600 seconds retain their previous behavior.

The driver logs progress every five seconds, including its active contact
mask, to help distinguish a retained finger from stopped polling. Firmware,
packet decoder, contact-release policy and hardware ownership checks are
unchanged. Existing error conditions can still stop touch. This change has
not been tested on the phone and does not establish a fix for the long press,
wallpaper or failed return.

After booting the existing `xnu-spacewar-identity-debug.img` into xnuloader,
run from `C:\platform-tools`:

```powershell
.\spacewar-userland-touch.ps1
```

The script stages the existing `system.apfs` and the new payload, starts XNU,
and finishes immediately. It does not wait for or impose a session deadline.
The boot remains in RAM.

## Remaining input, wallpaper and logging limitations

The current touch power/ownership guard requires CPU0 alone and `buttons=0`.
Power and volume input therefore remain disabled. Enabling buttons in this
payload rejects touch initialization. The earlier home-screen baseline used
seven cores and enabled buttons; it did not enable physical touch. Combining
these hardware paths requires further work.

The system/APFS images and wallpaper configuration are unchanged. This touch
profile runs on one little core and uses the existing software rendering and
media decoding paths. Slower wallpaper startup is plausible but unconfirmed;
no log from this run establishes its failure.

CPU0 is occupied by XNU, so UEFI cannot answer USB or supply live logs. Logs
can be downloaded only after XNU successfully returns. A five-finger hold for
1.5 seconds requests return, but multitouch and this exit gesture are not yet
phone-verified. Continuous mode currently has no proven graceful manual exit
on this phone. A hardware restart loses the RAM log.

After a successful return, collect the staged log with:

```powershell
.\spacewar-userland-touch.ps1 -CollectLog
```

For a bounded diagnostic attempt, `-Timed` selects the separate
`spacewar-userland-touch-diagnostic-payload.bin` (300-second touch window,
390-second requested return). Its collector gives up after 450 seconds. The
previous failed return has not been fixed or verified by this packaging.

Build inputs, checksums and host verification:
`build/spacewar-userland-touch-continuous/manifest.json`.
The production GPIO-wire model passes 37 cases under ASAN/UBSAN, including
continuous polling beyond 600 simulated seconds and cleanup after a real
fault. A simulated hold across idle packets remains held until a valid UP.
Device initialization guard checks and the ARM64 kernel compile/link pass.
