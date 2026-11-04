# GPU acceleration with physical touch

For the quieter and smoother CPU performance build, see
[spacewar-quiet-performance.md](spacewar-quiet-performance.md).

Phone-verified on November4: the user confirmed "Touch and buttons work".
Logs captured real FT12 down/up events, zero rejected reports, and over1400
accelerated compositor frames with zero reported GL errors. Frozen evidence
and exact counters are in `build/spacewar-gpu-touch/manifest.json`. This is an
initial functional test; extended stability and multitouch remain untested.

The combined payload retains the tested seven-core GPU profile, Mesa/OpenGL
ES libraries,315MHz GPU setting, physical buttons and live USB logs. It adds
the Spacewar FT12 physical-touch worker and its per-transaction USB ownership
protocol. Both `loader_return_after=0` and `touch_seconds=0` leave the run
without a scheduled cutoff.

Use the new loader: the old touch loader rejects GPU arguments. From stock
Android fastboot in `C:\platform-tools`:

```powershell
.\fastboot boot .\xnu-spacewar-gpu-touch-debug.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -Touch
```

To attach logging to an already running session:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -Touch -CollectLog
```

The launcher saves snapshots to `spacewar-userland-<timestamp>.live.log`.
Ctrl+C stops the collector and leaves iOS running. Firmware upload may take
about a minute after startup. Wait for `TOUCH WINDOW: verified runtime`, then
try a short Settings tap followed by release, a swipe, volume and power.
`TOUCH READY` confirms the first valid report after touching. `fastboot oem stop` requests
return; collect the final log before rebooting.

## Coordination

The kernel accepts exactly the previous eight-driver touch configuration or
the same configuration plus Adreno. The base315MHz profile uses `gpu=1` and
`adreno_speed=1`. The [GPU clock build](spacewar-gpu-clocks.md) adds the
driver's450/550MHz settings with a matched loader; other clocks and driver
sets are refused. CPU7/SMP7,
buttons, board identity, lease ABI, nonce and CPU-online checks remain in
place. The USB loader independently enforces its argument whitelist, requires
the GPU/speed pair together, and advertises `getvar touch-gpu` capability.
Diagnostic GPU overrides remain outside the live-touch whitelist.

For the combined profile, synchronous GPU initialization finishes before the
touch worker starts. This prevents the workers from configuring shared clock
and power state concurrently. Legacy startup order is retained for other
profiles. Touch still obtains exclusive ownership from the USB loader before
each hardware transaction and releases it between firmware packets and reads.
The60-second CPU-online wait includes the existing28-second slow-core delay.

`build/spacewar-gpu-touch/` contains the kernel, exact configuration, command
line, payload, loader boot image, source snapshots and manifest. The root is
the same `build/spacewar-gpu-accel/root-gpu.apfs` verified for the GPU-only run.
The original GPU-only and touch-only payloads remain available.

Validation:94 production kernel-helper cases and the actual loader policy/
lease tests under ASAN/UBSAN;18 actual startup-order cases; generated-board
guards; Windows PowerShell mock startup, logging and old-loader refusal tests;
ARM64 kernel and UEFI loader builds; boot-image inspection matching the
existing vendor firmware and DTB. Phone evidence and remaining limits are
recorded in `build/spacewar-gpu-touch/manifest.json`.
