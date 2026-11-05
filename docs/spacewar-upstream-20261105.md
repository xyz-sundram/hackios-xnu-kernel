The later [combined fix and canvas record](spacewar-metal-fixes-20261105.md)
documents the user-confirmed smooth scale-2 layout, shader/constant/input fixes
and the remaining physical scanout work. The following preserves migration
history and its earlier validation states.

The Phone (1) source was rebased onto upstream
[`2631f5d`](https://github.com/oosexgang/hakios-xnu-kernel/commit/2631f5d7d4f9d7322aca60991b34ba465dec343b)
on 5 November 2026. This brings in the common native Metal engine under
`hakios-metal/`, runtime shader selection changes, framebuffer changes and
the upstream device tooling. Source integration does not establish that the
rebuilt image fixes the moving rectangular patches reported during Metal tests.

No directory move is needed. Keep this kernel workspace and its existing
`devices/spacewar/` repository, including its `soc/` layer, private firmware,
installation maps and generated artifacts. The in-tree `hakios-metal/` is the
common engine; `build/hakios-metal-20261105/` remains the pinned checkout used
for earlier evidence. Do not substitute an old archive for a newly rebuilt
core or plug-in merely because their filenames match.

The base `spacewar_defconfig` intentionally leaves service drivers out. The
full 19-driver build uses this fragment chain:

```text
spacewar_defconfig
  common_defconfig + sc7280.config + spacewar.config
  spacewar-services-audio
    spacewar-services
      spacewar-storage
        spacewar-performance
```

The corresponding configuration command is:

```sh
make spacewar_defconfig FRAGMENTS=spacewar-services-audio
```

This selects build capabilities. Runtime permissions and the matched loader
still control which services start. The resulting drivers are `adsp speaker
sound virtio_sound socinfo adreno haptic virtio_input goodix buttons qmi
virtio_net battery cpufreq thermal pmic ufs wifi virtio_blk`. Selecting only
`spacewar-services` omits `sound` and produces a different driver set. The
audited brightness and Metal packagers require the recorded 19-driver profile.

[`devices/spacewar/metal.args`](../devices/spacewar/metal.args) supplies the
board defaults consumed by the upstream runner: `bootcpu=7 cpus=7` leaves
physical CPU0 with the USB loader, `cpufreq=2` retains the bounded Spacewar
clock requests, and `fb_hz=60 fb_threads=3 touch_poll_us=8000` retains the
previous timing defaults. It supplies no guessed display-interface address.
The [CPU profile](spacewar-quiet-performance.md) and
[live touch ownership](spacewar-touch-live.md) remain part of a complete payload.

The common `tools/metal-run.sh` still adds defaults beyond that file:
deferred recording, a reduced framebuffer scale, wallpaper freezing, GPU
recovery and, with its default `PERSIST=1`, writes through `var_store` to the
reference installation's `darwin` partition. Its `xl` entry path is not the
audited Nothing U-Boot/resident-loader handoff. The board file does not replace
Spacewar's touch lease, service policy, private partition identities or
607-byte argument audit. Do not use the common runner unchanged on this
installed phone.

Continue preparing isolated RAM payloads with
[`tools/spacewar-metal-package.py`](../tools/spacewar-metal-package.py) and
staging/capturing them with
[`tools/spacewar-debug.py`](../tools/spacewar-debug.py). They retain the matched
loader, brightness bridge and private `install-persist.map`, with normal
`var_hfs=ufs_darwin_data` storage rather than the common runner's `var_store`.
Preparation itself performs no phone I/O. Diagnostic profiles keep ADSP and
battery management off for live USB; they therefore do not establish measured
battery percentage. The services profile restores `adsp=1 battmgr=1` and must
be verified separately. Its DSP startup has previously disconnected USB.

The changes relevant to a future Metal correctness test are:

| Change | Meaning for Phone (1) |
| --- | --- |
| [`3b9db78`](https://github.com/oosexgang/hakios-xnu-kernel/commit/3b9db780ff960204c0e25dc05ca8d6285773b3c3) | Chooses a nearby compatible CA shader variant when a runtime descriptor has no exact precompiled variant. Upstream removes the wallpaper rewrite and white status-bar gradient workaround. |
| Local descriptor selection | Retains bare runtime-label normalization and gives a known descriptor pair priority over contradictory function labels. The nearest fallback complements this fix. |
| [`539d35e`](https://github.com/oosexgang/hakios-xnu-kernel/commit/539d35e02482e4586670137c08c922ef889894ae) | Filters slow frames immediately and skips a delayed settle redraw when the surface's submission fence has changed. Local physical-brightness support must remain alongside it. |
| Local command-buffer scheduling | Retains submission draining, event-held waits, ordered scheduled/completed handlers and waits for handler execution. Upstream's no-op scheduling wait does not replace these checks. |
| Bundled native engine | Rendering, command submission, texture handling and shader compilation match the previously pinned core. The import adds build integration and chip validation; it is not a new framebuffer-fetch overlap fix. |

The converted canonical QuartzCore library and its `/redwood/ca.metallib`
alias remain required. Rebuild the SpringBoard injection library from the
integrated source so an older packaged gradient workaround does not hide the
new shader behavior. Removing the wallpaper build script does not restore
wallpaper scenes already rewritten in an existing system image.

Validation should first check device metadata and expand the full fragment
chain without replacing the active configuration:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/hakios-device.py check spacewar sc7280
PYTHONDONTWRITEBYTECODE=1 python3 tools/hakios-config.py defconfig \
  spacewar spacewar-services-audio -o /dev/null
```

Then rebuild the intended core, plug-in, probes and SB library into new output
directories, record their hashes, and audit the payload and APFS contents.
Retain the constexpr compiler compatibility patch when required by the host
compiler. Request the diagnostic probe target explicitly: the bundled
Makefile can omit it from `make ios` when its host fixture compiler is absent.
Rerun the scheduling, native draw/alpha/MRT and missing-variant regressions
before comparing the same physical gesture on the phone. Include status-bar
gradients, wallpaper and translucent materials, idle frames, physical
brightness, Wi-Fi, measured battery percentage and touch in the final hardware
check. Host tests and upstream measurements are not that check.

The frozen GLES brightness/battery fallback, installed menu inputs and phone
backups remain unchanged by this source migration. The stable installation
checkpoint is `build/spacewar-stable-20261105/RESUME.md`; its package and verified
backups exist, but flashing had not completed at that checkpoint. Follow that
record and detect USB mode afresh before any separately authorized deployment.
Do not promote an untested rebased image over the stable fallback.

The integration build passed on this workspace: the 19-driver kernel, bundled
native Metal library/probe, CA plug-in, fresh SpringBoard injection library and
the scheduling/alpha diagnostics all compiled. The native core reported 261
host tests with zero failed checks. Twenty configuration/service/ownership
regression suites passed, along with driver inventory, documentation links and
boot-argument checks. Four new RAM profiles passed APFS content readback, XNUP,
HKUB and boot-wrapper audits. Their measured-battery services profile retains
`adsp=1 battmgr=1`; deferred recording remains off by default.

The bundled host build needed one additional scoped compatibility patch:
`host-chip-check.patch` marks the Apple-only `chipKnown` helper unused on
non-Apple hosts, preserving its phone behavior while allowing GCC `-Werror`.
The builder checks the exact bundled source plus these reviewed patches and
records source hashes; `--dry-run` validates without modifying the tree.

Build evidence is under `build/upstream-rebase-20261105/`: `validation.json`,
`kernel-build.log`, `native-metal-v2/manifest.json`, the preservation-test logs
and `ram-bundle/bundle.json`.

Resumed RAM tests found a local startup regression in v13: the Spacewar beam
guard wrote to the late-read-only `gBeam` tunable from framebuffer `start()`.
The returned panic's fault address resolves to `gBeam`. v14 computes the
effective beam policy in a local variable instead; its full stage9 GPU probe
passed all 282 checks. The regression now models the tunable as read-only.

v14 also passed seven pipeline and 18 scheduling checks. Eight of the nine
alpha/MRT cases passed in each mode, but `feedback-overlap-one-draw` failed
in serial, batched and fresh-source modes: the first overlap pixel read 8
instead of 4. The equivalent two-draw case passed. This reproduces a
within-draw framebuffer-feedback ordering error; its relationship to the
moving-icon patches still requires a patched visual comparison. Evidence:
`build/spacewar-metal-20261105/phone/metal-debug-014-upstream-bootfix/`.

The historical v15 candidate is selected with `--sysmem-feedback-order --alpha-check` in
`tools/build-spacewar-metal.py`. Its two scoped patches select sysmem
`FLUSH_PER_OVERLAP_AND_OVERWRITE`, keep the existing per-draw CCU/cache barriers,
and preserve GMEM ordering and the ordering-off control. All 261 host tests
pass, including inspection of the actual flush/wait/invalidate packets before
both first-fetch and subsequent-fetch draws. The full stage9 phone GPU probe
also passes 282 checks. The candidate remains opt-in and is recorded in native
and payload manifests; its applied source requires that flag to pass the
strict source guard. The v14 binaries remain available for comparison.

The user also confirms that split/staggered animation and occasional icon/text
rectangles existed before Metal. Keep common presentation, source-surface reuse
and panel scanout in scope. The prepared GLES control uses exactly the v15
kernel/root and removes only three Metal-selection boot arguments. Its
Metal-only preflight initialization failures are not GLES UI test failures.

The host collector now preserves an already staged terminal kernel log
instead of replacing it with `oem log`, and returns failure when `--until`
was not reached. No boot partitions have been flashed. Combined native Metal,
measured battery, brightness, Wi-Fi and touch remain unverified.

Continuing v15 jitter is now investigated across the display, surface ownership,
CA workload, GPU cost, CPU and touch layers in the
[presentation investigation](spacewar-presentation.md). The new display-use
candidate is off by default and is isolated from five other presentation
controls in matched Metal/GLES RAM profiles. Its rebuilt kernel passes the full
282-check stage9 phone probe. The ownership comparison still requires the new
RAM loader; probe success is not evidence that jitter is fixed.

The subsequent [full stack audit](spacewar-metal-audit-20261105.md) supersedes
the pending ownership comparison above. Ownership did not resolve the reported
jitter. The unpaced RAM test removed a median 7.11 ms presentation wait and the
user confirmed noticeably smoother motion. GPU pass costs, FT12 input timing,
constant-buffer draw refusals and physical scanout remain separate issues.
The new compiler-cache locking candidate builds with the additional
`--compiler-cache-hits` flag and passes 263 host tests; it is not the currently
running phone build.

The later [combined fixes and layout validation](spacewar-metal-fixes-20261105.md)
record the user-confirmed smooth 750 × 1624 canvas with correct 375 × 812-point
layout. Renderer validation reached 264 host tests and 285 on-phone checks.
The automatic wallpaper optimization was subsequently removed at the user's
request. Both normal Metal profiles now retain measured battery reporting.
The [charging checkpoint](spacewar-charging-20261105.md) records the same-cable
Android PD comparison, experimental UCSI/PAN clients and pending C4 log recovery.
Actual charging, ADSP-related USB loss, reported freezes and physical scanout
synchronization remain unresolved.
