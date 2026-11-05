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
and `ram-bundle/bundle.json`. None of these rebased payloads has been booted
on the phone or flashed. In particular, the added missing-variant and
framebuffer-feedback diagnostics are compiled, not hardware-pass claims.
