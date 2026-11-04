# Higher GPU clocks with quiet UI and touch

The550MHz UI run is phone-verified. The clock measured550000228Hz, and the
user confirmed "Smoother, touch/buttons work". CPU requests remained1900MHz
for big cores and1862MHz for prime. This confirms a subjective improvement;
a numerical FPS gain was not measured.

Spacewar's measured fuse speed bin is0x81. Its native device-tree power table
(`qcom,gpu-pwrlevels-4`) contains315/450/550/608MHz. The existing GPU driver
implements the first three, with supply levels64/128/192. This build exposes
315,450 and550MHz through the matched loader, touch profile and CPU mode.
The GMU applies the corresponding firmware supply votes.

Phone hardware tests passed drawing, pixel readback and clean return at both
450 and550MHz. Clock measurements were450001824Hz and550002132Hz. Those
tests prove hardware bring-up; UI behavior and short thermal observations are
recorded separately in `build/spacewar-gpu-clocks/manifest.json`.

From stock Android fastboot in `C:\platform-tools`:

```powershell
.\fastboot boot .\xnu-spacewar-gpu-clocks.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -Touch -Quiet -GpuMHz 550
```

Use `-GpuMHz 450` for the intermediate setting. The default remains315MHz
and selects the preserved, previously verified quiet payload. The new loader
advertises `getvar gpu-clocks`; the launcher refuses higher clocks on an old
loader before staging anything. The selectable files are
`spacewar-userland-gpu450-touch-quiet-payload.bin` and
`spacewar-userland-gpu550-touch-quiet-payload.bin`. Higher clock selection
requires the quiet GPU/touch profile.

The CPU requests remain1900MHz for big cores and1862MHz for prime, with the
1920MHz firmware-LUT cap. Seven XNU cores, the CPU0 USB loader, physical touch,
buttons, thermal monitoring, `hakios_debug=0`, `touch_lat=0` and no scheduled
cutoff are preserved. GPU initialization still finishes before touch starts.

The kernel/loader checks still require the exact eight-, nine- or ten-driver
profile for the corresponding mode. Only driver speed indices1..3 are added;
arbitrary clocks and diagnostic overrides remain outside the live-touch
whitelist. The GPU register code, firmware and Mesa library are unchanged.

At550MHz the requested GPU clock is about75% higher than315MHz. This is not
an FPS guarantee: CPU work, memory transfers and compositor scheduling can
still limit frame delivery. Sustained stability and a numerical FPS comparison
are not established by the short functional test.

Artifacts, frozen logs, DT evidence and checksums are under
`build/spacewar-gpu-clocks/`. The original315MHz quiet build and loader remain
under `build/spacewar-gpu-touch-performance/` and in `C:\platform-tools`.
