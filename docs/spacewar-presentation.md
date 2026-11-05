# Spacewar presentation investigation

The [combined fix record](spacewar-metal-fixes-20261105.md) supersedes the early
touch, constant-buffer and source-reuse findings below and records the
user-confirmed smooth, correctly positioned scale-2 canvas. Native scanout
experiments remain disabled; that record includes the failed hardware test.

The user reported continuing jitter with v15 Metal and smoother behavior on
Redwood. The ownership candidate passed startup checks but did not resolve
that complaint. The subsequent `fb_beat=0` test was confirmed noticeably smoother
by the user and removed the measured presentation wait. See the
[full stack audit](spacewar-metal-audit-20261105.md) for corrected timing,
remaining GPU/input costs, skipped draws and the separate compiler-cache fix.
The following records the earlier ownership investigation and test sequence.

The reference device tree was inspected at
[`04fbfff933f52faf203a55aca9dd5680092e6ec5`](https://github.com/oosexgang/hakios-sm7325/tree/04fbfff933f52faf203a55aca9dd5680092e6ec5).
That repository supplies Redwood's board configuration, SoC description and
UEFI platform. Rendering and presentation live in the common kernel/userland
repository. Its device status text still says Metal drawing is unavailable, so
that text cannot establish the state of the user's smooth Redwood build.

| Layer | Spacewar evidence and comparison | Consequence |
| --- | --- | --- |
| Boot display | Spacewar retains ABL's 1080x2400 buffer through the frozen U-Boot/resident-loader handoff. Redwood's device description uses UEFI's framebuffer. | The buffer geometry, memory attributes and active DPU mode must be established on Spacewar itself. |
| CPU | Seven XNU CPUs; CPU0 remains with the resident loader. v15 logs request 1900/1862 MHz through Spacewar's bounded EPSS policy. The common Redwood runner selects eight CPUs and its own boost mode. | Copying Redwood's CPU flags would break the established ownership profile. |
| Touch | Spacewar's live lease and firmware ownership remain required. Its FT12 loop hard-codes 8 ms sleeps and a 100 ms fallback; it ignores the generic `touch_poll_us` setting. | Actual report cadence and release recognition require separate measurement from userland injection. |
| GPU | The earlier v15 full stage9 probe passed 282 checks. All nine CA alpha/overlap cases passed serial, batched and fresh-source modes in `phone/metal-debug-015-feedback/latest.private.log`. | The reproduced within-draw feedback failure is corrected in that run; that is not proof that all compositor frames or scanout are correct. |
| GPU cost | The same UI log records some sysmem passes at 18–25 ms and commit gaps past 50 ms. | Some work exceeds a 60 Hz frame budget independently of scanout. The plug-in's zero `presentDrawable` count is not zero IOMFB display updates. |
| CA workload | Spacewar draws 1125x2436; the common Redwood runner requests `fbscale=2`, approximately 44% as many source pixels, plus deferred recording and frozen wallpaper. | These are materially different workloads. The current matrix holds resolution, shaders, GMEM threshold and recording policy fixed. |
| Surface ownership | `MiniIOSurfaceCopy()` increments `holders` only. It does not publish `shared()->useCount`. IOMFB's `SwapWait` returns immediately, and its queue slot is released before the copy finishes. | Object lifetime alone does not advertise the display reader to `IOSurfaceIsInUse()`. There is a possible producer-reuse window during pixel reads. |
| Scaling | `fb_filter=2` alternates nearest-neighbor animation frames with filtered slow/settle frames. A settle reacquires live surfaces by ID and checks their submission fence. | `fb_filter=1` isolates resampling changes and removes the delayed settle path. |
| Timing | IOMFB notifications are timer driven; presentation has a separate adaptive timer beat. Spacewar disables the inherited beam probe by default. | A completed framebuffer copy is not a measured physical panel refresh. |
| Panel mode | The private ROM DTBO inventory's NT37701A and RM692E5 Visionox panel candidates both declare `dsi_cmd_mode`. | This does not identify the active panel or bootloader mode. Do not assume Redwood's video timing/line-counter method works, or force its register address. |

With `fb_surface_use=1`, `MiniIOSurfaceCopyForDisplay()` publishes a category-0
use count under the surface lock together with its lifetime hold. Every queued
swap layer retains that count through its GPU wait, presentation wait, all copy
workers and recording. `MiniIOSurfaceDropForDisplay()` removes it after those
reads. Client-owned counts remain independently accounted. Duplicate surfaces
in different layers/swaps take independently balanced holds. Settle references
use the same pair. The default is zero for a direct behavioral control.

This advertises a display reader to cooperating producers; it does not enforce
GPU write exclusion, implement `SwapWait`, or eliminate physical scanout tearing.
It can change surface-pool pressure and latency. Those effects require the same
gesture on both ownership profiles before selecting a default.

`tools/spacewar-metal-package.py --graphics-matrix` adds six Metal/GLES pairs:

| Suffix | Sole presentation change from the traced baseline |
| --- | --- |
| `baseline` | Existing presentation defaults |
| `ownership` | `fb_surface_use=1` |
| `filtered` | `fb_filter=1` |
| `ca120` | `fb_ca_hz=120`, changing CA's advertised interval while keeping the timer at 60 Hz |
| `copy6` | `fb_threads=6` |
| `unpaced` | `fb_beat=0`, bypassing the adaptive presentation delay |

All twelve use the identical kernel and APFS root and enable `touch_lat=1`.
Each GLES variant removes only `metal`, `metal_fps` and `ca_metallib` from its
Metal partner. The shared root retains the Metal preflight commands; their
Metal-selection failures under GLES do not establish GLES UI failure. Battery
transport remains disabled in these diagnostic pairs to retain USB; the
separate `metal-services` profile enables measured battery reporting.

The rebuilt loader accepts only bounded presentation controls. Its policy tests
reject duplicates, malformed values, zero/out-of-range thread counts, display
register addresses and physical refresh/beam overrides. This loader must be
RAM-booted before using the new profiles; the previous resident loader refuses
their new arguments. The frozen U-Boot and Spacewar DTB remain the bootstrap
inputs. No installed boot image was changed.

Build evidence is under `build/spacewar-presentation-20261105/`: the new
`kernel/manifest.json`, `kernel-build-final.log`, `loader/loader.efi`, `package-final.log`
and `ram-bundle-final/bundle.json`. Packaging checks APFS contents, payload entries,
the 607-byte boot-argument limit, loader policy, HKUB and Android boot wrapping.
The source snapshots and compiled XNU files have matching hashes. The kernel
contains the new ownership functions and read-only tunable. Existing v15 Metal
binaries and compositor shaders are reused with manifest hash checks.

The ownership regression executes the production hold/release functions under
ASan/UBSan. It covers duplicate queued surfaces, independent client counts,
missing surfaces, ordinary object holds, default-off behavior and matching
renderer arguments. Loader, live-touch ownership, services, CPU, backlight,
startup-order, capture and boot-argument catalogue regressions pass. Kernel
build accepts only the existing kext symbol-set failures through its normal
build judge. These checks do not establish visual success.

Begin physical testing with the GPU probe, then compare `metal-baseline` with
`metal-ownership`, followed by the equivalent GLES pair. Use the same app and
gesture after preflight and cache warmup. Capture `tl: p/s/v/y/f` and run
`tools/frames.py` on all snapshots from that run. The analyzer separates
SwapEnd-to-ready queue/GPU time, ready-to-copy pacing, and copy duration. Metal
CPU rendering/idle time cannot be assigned from these display records. The
updated analyzer summarizes Metal `tl: M` records separately without treating
them as hardware GPU time or assigning them to arbitrary display frames.

If the same split bands remain in both renderers and ownership modes, establish
the retained display's active command/video mode and refresh counters next.
If only timing changes help, compare the cadence and copy controls individually.
If Metal alone remains slow, use its timed pass and commit logs to choose a
separate GMEM/deferred-recording experiment. Do not reduce the feedback-ordering
guarantee to regain speed. Physical brightness, Wi-Fi, touch and measured battery
must still be checked together before promoting a build.

Direct Windows executable invocation fails in the current environment, but the
explicit WSL `/init` interpreter works with an extra program-name argument and
vsock access. `build/spacewar-presentation-20261105/fastboot-wsl` adapts the audited
runner to that interface and translates workspace paths to Windows UNC paths.
The existing resident loader can test the rebuilt kernel with default presentation
settings. Its full stage9 GPU probe passed all 282 checks with zero failures;
the log is `phone/render-probe/latest.private.log` under the build directory.
The traced `metal-baseline`, with ownership off, also passes seven pipeline
cases, 18 scheduling cases and three alpha-test modes. Its 120-second capture
contains 32 gestures and 1672 panel copies. Across 1115 sampled gesture frame
intervals, 43% exceed 18 ms and 13% exceed 34.5 ms; idle gaps of 100 ms or more
are excluded by the analyzer. The exact per-gesture results are in
`phone/metal-baseline/frames.txt`. These are copy timestamps, not measured
physical refreshes, and gesture/app differences prevent treating them as one
repeatable benchmark.

The baseline was stopped cleanly (`returned 1446547 0`) and its final kernel log
retained without requesting another loader snapshot. The ownership and other
new controls still require a fresh RAM loader; their physical results remain
pending. Enter stock fastboot, RAM-boot `ram-bundle-final/loader-debug.img`,
select HakiOS in its menu, then use the audited runner for `metal-ownership`
and the matched baseline/GLES profiles. No partition flash is needed.

The subsequent stock-fastboot RAM boot of the new loader succeeded. Its resident
interface identified itself as `xnuloader`, with the Spacewar live-touch lease,
debug logging enabled, raw partition logs disabled and an idle kernel state.
The `metal-ownership` payload was accepted, and the live kernel command line
contains `fb_surface_use=1`. Seven pipeline cases, 18 scheduling cases and all
three alpha-test modes passed. The UI is drawing without a captured panic.
Evidence is in `phone/metal-ownership/` and `phone/validation.json` under the
presentation build directory. A matched gesture comparison and the user's
visual assessment were still pending at that point; startup success did not
establish that the ownership change fixed jitter. The subsequent full audit
above records the user's continued jitter and the smoother unpaced test.
