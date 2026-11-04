# Quiet GPU/touch build and CPU performance

The separate [GPU clock build](spacewar-gpu-clocks.md) adds450/550MHz GPU
selection to this quiet CPU performance profile;315MHz remains available.

Phone-verified on November4: the user confirmed "No log text; noticeably
smoother; touch/buttons work". EPSS request readback confirmed1900MHz for the
big cores and1862MHz for the prime core. A captured framebuffer shows the
home screen without console text. This is a functional improvement confirmed
by the user; a numerical quiet-mode FPS comparison was not collected.

The previous profile ran the correct seven-core assignment, physical7..1,
with CPU0 retained by the USB loader. The scheduler preferred the faster cores.
Hardware GPU bring-up measured315MHz on Adreno642L, and Mesa used the hardware
renderer. The important missing setting was CPU performance: firmware EPSS
requests were1516MHz for the little cluster,691MHz for the big cluster and
806MHz for the prime core. Their LUT limits were1804/2400/2515MHz. These are
requested rates from EPSS, not measurements of clocks under thermal limits.

`tools/frames.py` on the previous physical-touch run found556 frame steps:
82% exceeded its nominal16.7ms threshold and40% exceeded its33ms threshold.
Rendering samples were overwhelmingly on big cores (992 versus23 little-core
samples). The low big/prime clock requests and console/tracing overhead were
therefore relevant, rather than a wrong CPU assignment.

## Run

From stock Android fastboot in `C:\platform-tools`:

```powershell
.\fastboot boot .\xnu-spacewar-gpu-touch-quiet.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -Touch -Quiet
```

The launcher verifies `quiet-ui` and `cpu-perf` capabilities before staging.
It selects `spacewar-userland-gpu-touch-quiet-payload.bin`. Allow about a
minute for touch firmware initialization. There is no scheduled cutoff.
`-CollectLog` attaches to an existing run; Ctrl+C stops collection and leaves
iOS running. The host can still request a snapshot or return using
`fastboot oem log` / `fastboot oem stop`.

## Flags and preserved debugging

`hakios_debug=0` suppresses the framebuffer text console, drains daemon console
output without per-character logging/drawing, suppresses routine HakiOS dylib
messages and touch diagnostics, and disables the GL bridge's per-call error
checks. `touch_lat=0` disables detailed touch/frame latency tracing. The loader
uses `debug=0` and marks the identity tree's `debug-enabled` property false.
Essential startup/clock messages and kernel message/panic buffers remain
available for diagnosis. The kernel remains a DEVELOPMENT build with its
debugging code compiled in.

`hakios_debug=1`, or omitting it, retains the previous diagnostics. A second
payload, `spacewar-userland-gpu-touch-runtime-debug-payload.bin`, contains the
same kernel and root with `hakios_debug=1`, `touch_lat=1` and `-v`. With the
matched loader and system image already staged, stop the quiet run, stage
that payload and issue `fastboot oem run` to use it. The original working
touch/GPU bundle is also preserved under `build/spacewar-gpu-touch/`.

## CPU-only performance mode

`cpufreq=2` on Spacewar opts into firmware-LUT requests capped at1920MHz for
domains1 and2. Domain0, including the loader's CPU0, is never accessed by
this mode. DDR/bandwidth votes and GPU clocks remain unchanged. A domain
already requesting a higher rate is left alone. The exact ten-driver profile,
CPU7/SMP7/buttons, an approved GPU speed and touch lease settings are required. Other
Spacewar cpufreq modes do not enter the reference board's clock/DDR path.

The code checks LUT bounds and request readback, rolls back a partial failure,
and restores requests on return only if the register still holds this run's
request. Normal eight-driver touch and nine-driver GPU/touch profiles retain
their own strict checks. This is a bounded fixed request, not a DVFS governor.
Thermal monitoring and the existing stop guard remain active.

`cpu_probe=1` independently reads the three firmware requests and LUT limits,
without writes, after validating Spacewar and SoC475. Phone before/after values,
test results, exact artifacts and limitations are recorded in
`build/spacewar-gpu-touch-performance/manifest.json`.

Validation includes18 production EPSS cases (bounds, rollback, readback,
ownership-aware restoration and excluding domain0),98 actual touch-lease
cases, the actual loader policy,18 startup-order cases, actual console/flag/
read-only-probe tests, Windows mock startup/capability tests, kernel/loader
builds, APFS readback and boot-image inspection. Extended stability and a
measured quiet-mode FPS comparison remain separate work.
