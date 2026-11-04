# On Nothing Phone (1)

Use the Spacewar build on **Nothing Phone (1)** (SM7325). The verified path is a
RAM boot through Mu-Silicium, with touch/buttons and quiet 550 MHz OpenGL ES graphics.
[Hardware status](../spacewar-status.md) lists what has actually been tested.

## Build and prepare

Follow the [root README](../../README.md#build-for-nothing-phone-1-spacewar) for the
complete build. Restore your private GPU/touch firmware, vendor UEFI modules and
matching DTB first. Generic upstream prebuilts and another phone's firmware do not
reproduce this configuration.

Package with:

```sh
python3 tools/spacewar-package.py --gpu-mhz 550 --out build/spacewar --copy-system
```

Copy its output to a Windows platform-tools directory containing Google's
`fastboot.exe` and DLLs. The tested local bundle is `C:/platform-tools/final`.
Keep its `SHA256SUMS` and manifests with the images.

## Start

Put the phone into **stock Android fastboot**. In Windows PowerShell:

```powershell
.\fastboot.exe boot .\xnu-spacewar-gpu-clocks.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -Touch -Quiet -GpuMHz 550
```

Wait for the system image upload and touch firmware initialization. The home screen
and wallpaper should appear; test a short tap followed by release, a swipe and volume.
The launcher's quiet profile has no scheduled cutoff. Debugging code remains present.
The paused [cutout adjustment](../spacewar-statusbar.md) fixes the home-screen clock
across sleep/wake, but the clock remains missing inside apps. It is a partial source
checkpoint; the original verified local bundle remains separate.

## Logs and stopping

The launcher writes `spacewar-userland-*.live.log` beside the images. Ctrl+C stops
the host collector; it does not stop XNU. To request return to the loader:

```powershell
.\fastboot.exe oem stop
```

Once the loader responds again, reboot through fastboot. If USB disappears, use the
phone's hardware restart/fastboot keys and return to stock Android fastboot before
issuing another `fastboot boot` command. Keep the verified bundle for comparison.

## Storage and installation

The current working system runs in RAM. Networking, audio, battery reporting,
cameras and persistent data are not established Phone (1) features. The
[dual-boot page](../dual-boot.md) describes the separate port; another phone's
partition layout and installation commands must not be treated as this phone's.
