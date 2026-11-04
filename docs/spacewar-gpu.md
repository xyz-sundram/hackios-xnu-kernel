# Spacewar GPU acceleration

The November 4 phone test passed Adreno step22 (drawing and pixel readback),
then booted iOS with CoreAnimation using Mesa25.0.7/freedreno and OpenGL ES3.2.
The user confirmed that the home screen appeared and volume responded. The
30-frame summary reported zero GL errors; subsequent frames continued. This
is a working initial GPU profile, not a sustained stability or performance test.

The missing pieces were the compiled Adreno driver, Spacewar GPU firmware in
the root image, and the Mesa/OpenGL ES libraries. The existing common SC7280
driver worked without register changes. Redwood's reference configuration is
https://github.com/oosexgang/hakios-sm7325; the cached revision is
04fbfff933f52faf203a55aca9dd5680092e6ec5. Metal is a separate graphics API;
the accelerated iOS compositor in this profile uses OpenGL ES. The supplied
https://github.com/oosexgang/hakios-metal is not needed for that path.

## Run and collect

Files are copied into `C:\platform-tools`. The current phone run already has
GPU acceleration enabled. Attach live collection without restarting it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -CollectLog
```

For a fresh session, from stock Android fastboot:

```powershell
.\fastboot boot .\xnu-spacewar-touch-live-debug.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1
```

The launcher stages the existing `system.apfs` and the new
`spacewar-userland-gpu-payload.bin`. Live snapshots go to
`spacewar-userland-<timestamp>.live.log`. Ctrl+C stops collection and leaves
iOS running. There is no scheduled cutoff (`loader_return_after=0`). From
another terminal, `fastboot oem stop` requests return; use the collector to
retain the final log. Stop the current run before starting another one.

This profile uses seven cores, buttons, thermal monitoring, and a315MHz GPU.
Physical touch is disabled in this original GPU payload. A separate combined
profile is documented in [spacewar-gpu-touch.md](spacewar-gpu-touch.md); it
requires the updated loader. The touch payload, normal
kernel, normal root image, and2.47GB system image were preserved. Verbose
console text can overlay the UI in this diagnostic payload.

## Artifacts and reproduction

`build/spacewar-gpu-accel/manifest.json` records hashes, exact command line,
build inputs, and validation. `kernel.config` is the exact nine-driver build
configuration; `kernel-gpu` is its kernel. To reproduce the kernel, save the
current `.config` and `hakios-kernel`, use `kernel.config` as `.config`, run
`tools/make-kernel.sh -j8`, save the new kernel separately, and restore those
two saved files. The optional `configs/spacewar-gpu.config` fragment enables
the same GPU driver and315MHz startup setting over a Spacewar configuration.

Mesa was built with `tools/mk-mesa.sh` (pinned25.0.7 plus the repository's
Darwin/freedreno patches). Its private venv needed `meson` and `ninja`.
The bridge uses the existing `userland/build.sh` GL recipe: generate
`eagl_gen.h` with `userland/mkeagl.py`, then compile `eagl.c` and `gl_osmesa.c`
against Mesa, CoreFoundation, IOKit, IOSurface, and libobjc using `xnu-clang`
and the iPhoneOS16.5 SDK. The dylib install names are
`/redwood/lib/libOSMesa.8.dylib` and `/redwood/lib/libhakios.gl.dylib`.

The isolated `root/` copies `userland/root`, adds those two dylibs in `lib/`,
and adds the validated Spacewar firmware from
`build/spacewar-gpu-firmware/vendor/firmware/` in `fw/`. Build and verify it:

```bash
python3 tools/apfs/mkapfs.py -z --plain fw \
  -o build/spacewar-gpu-accel/root-gpu.apfs \
  redwood=build/spacewar-gpu-accel/root
tools/apfs/check-image.sh build/spacewar-gpu-accel/root-gpu.apfs \
  redwood=build/spacewar-gpu-accel/root
python3 xnuloader/mkpayload.py \
  -o build/spacewar-gpu-accel/gpu-ui-payload.bin \
  kernel=build/spacewar-gpu-accel/kernel-gpu \
  ramdisk=build/spacewar-gpu-accel/root-gpu.apfs \
  --cmdline "$(cat build/spacewar-gpu-accel/cmdline.txt)"
```

Evidence: `logs/spacewar-gpu-probe-live.log` (step22),
`logs/spacewar-gpu-ui-live.log` (compositor), and
`build/spacewar-gpu-accel/screen.png` (actual framebuffer). APFS verification
found a clean container and all488 directory entries matched the source.
