# Spacewar boot investigation (2026-11-03)

The phone's latest screen log establishes a DXE allocation failure before BDS or
the XNU loader starts. The first correction preserved the loader payload and
made DEBUG output available. That output exposed a second porting defect: RAM
discovery ran only after nearly all other firmware drivers. The next images move
it earlier and protect the current-ROM DTB's fixed carveouts. The phone now
enumerates over fastboot as `xnuloader`, and `fastboot getvar product` returns
`product: xnuloader`. This confirms firmware reaches the resident loader and USB
works. The subsequent smoke test also passed: XNU mounted the RAM root, ran the
`hello` program as process 1 with uid 0, and returned to the loader after its
30-second budget. The complete phone log is saved as
`logs/spacewar-smoke-success.log`.

Verified baseline: UEFI, the resident loader, USB upload/download, XNU boot,
RAM-root mounting, framebuffer output, user-space execution/system calls, and
the timed return to firmware. Phone-specific touch, storage, networking, audio,
GPU acceleration and other drivers remain separate bring-up work.

The identity and temperature probe payloads have also passed on the phone:
SMEM reports SoC 475 / revision 1.0, and all 27 temperature sensors produce
changing readings visible to user space. The 105 C temperature-stop path remains
untested. Evidence and the next input-driver milestone are documented in
[spacewar-hardware.md](spacewar-hardware.md).

Userland setup, upstream integration, verified APFS images, Windows boot bundle
and QEMU results are in [spacewar-userland.md](spacewar-userland.md). The full
userland reaches launchd/SpringBoard/backboardd in QEMU; usable UI and the phone
boot remain unverified.

## Confirmed DXE failure and second correction

The phone reports repeated `AllocatePoolPages: failed to allocate ... pages`,
followed by missing Security, BDS, Capsule, Monotonic Counter and Real Time Clock
architectural protocols, then `DxeMain.c(581)` asserts `Status = Not Found`.
This is before `gBds->Entry`, so fastboot in the XNU loader cannot be reached.

Spacewar supplies a 49 MiB seed heap at `0xE3400000–0xE6500000`. Its APRIORI list
loads roughly 80 drivers, but `RamManagerDxe` was only an ordinary FDF component.
DXE drains the APRIORI queue before scheduling ordinary drivers. It also marks a
failed image load as initialized, so running RAM discovery later cannot recover
the drivers already lost to allocation failures.

`RamManagerDxe` is now in APRIORI after `SmemDxe` and before `DALSYSDxe`. By that
point `EnvDxe` has supplied the RAM partition protocol, and `CpuDxe` has supplied
the memory-attribute services. RAM discovery can expand the available DDR before
the remaining drivers consume the seed heap. Its existing normal FDF inclusion
is retained. The seed heap size is unchanged.

Before exposing additional RAM, eight gaps in the current-ROM DTB reservations
were closed: XBL, SEC data, CPU/WLAN firmware, the CDSP secure heap, ramoops and
the two secure gaps around the TZ statistics page. These use resource HOBs that
reserve memory without creating translations. The existing mapped TZ statistics
page is preserved. The original XBL CPU vector page is also retained. The
updated map has no overlapping DDR descriptors, and all 27 fixed DTB reserved
ranges are excluded from free allocation. The XNU payload reservation remains.

The initial QSEE status messages may require separate investigation. They do not
change the observed allocation failures and the final missing-protocol stop.

## Findings

1. The shim copies the `XNUP` container to `0xF0000000`. Spacewar's stock Mu memory
   map did not reserve `0xF0000000–0xF4000000`. HakiOS's
   [Redwood map at the manifest revision](https://github.com/oosexgang/hakios-sm7325/blob/150768e7aa2f2fee5557f033854d30c6aa151b69/uefi/files/Platforms/Xiaomi/redwoodPkg/Library/MemoryMapLib/MemoryMapLib.c)
   explicitly does. Mu's RAM Manager maps otherwise unused DDR as conventional
   memory before BDS, so firmware allocations can overwrite this container.
   Missing static mapping alone does **not** prove a translation fault because
   that driver can map the area later. An explicit reserved mapping is required
   to protect the payload regardless.
2. The original `xnu-spacewar.img` has Android header v3, a valid gzip stream, a
   176-byte payload-aware shim, the expected 2 MiB FD, and a complete `XNUP`.
   It contains `loader.efi`, `usb=1`, and version metadata. There is no kernel:
   this image starts the resident USB loader, which accepts a kernel afterward.
3. The fallback prebuilt shim was a 112-byte stock shim: it had an ARM64 header
   but no payload-copy loop. The original image used the correct shim from Mu,
   so this fallback defect does not explain that image's failure. Mu's normal
   build recreates the stock shim. The packer now builds a payload-aware shim
   in a temporary directory and rejects a stock fallback when given a payload.
   The device prebuilt shim has been refreshed.
4. The original image used Mu's bundled DTB, not the supplied current-ROM
   `spacewar.dtb`. They describe the same SoC and `0xE1000000` framebuffer, but
   have different chosen properties and panel data. The new images use the
   supplied DTB exactly. `--dtb`, `XL_DTB`, and the updater's `DTB` override make
   the choice explicit.
5. `xl` still selected Redwood for firmware discovery, and used different
   fastboot executables for discovery and commands. Both are corrected.
6. The supplied XBL's `uefiplat.cfg` is byte-identical to the bundled Spacewar
   configuration. The built FD contains the patched `TzDxeLA` required by the
   port. Some bundled EFI drivers differ from the supplied XBL, but those
   differences have not been established as a boot failure. No vendor binaries
   were replaced.

## Changes and artifacts

The reservation is in
`Mu-Silicium/Platforms/Nothing/spacewarPkg/Library/MemoryMapLib/MemoryMapLib.c`.
The source overlay is saved under
`devices/spacewar/uefi/files/Platforms/Nothing/spacewarPkg/`, including
`Library/MemoryMapLib/` and `Include/APRIORI.inc`.
The parent repository ignores device repositories and Mu build output; keep the
device overlay with the Spacewar port. Original image and FD backups are in
`build/spacewar-before/`; `xnu-spacewar.img` itself was preserved. The first
corrected images and their source maps are also preserved in
`build/spacewar-before-ram-fix/`.

| Image | Purpose | SHA-256 |
|---|---|---|
| `xnu-spacewar-ramfix.img` | Latest RELEASE: early RAM discovery, complete fixed DTB reservations, resident USB loader | `17efcda94cf8b8b55f3b0b39361fcab509c4ea0bb48d4e9b28d9bd817dba5cb8` |
| `xnu-spacewar-ramfix-debug.img` | Latest DEBUG: same correction, visible firmware progress | `8d7d5ca5f05a3421322238f56792e126797296bae43505fbde823d5cbf298322` |
| `xnu-spacewar-fixed.img` | Corrected RELEASE UEFI + resident USB loader + current-ROM DTB | `9592d9a1eef30b84a42a9071d12f7ab585aba4bfd182aec078344875ee007e20` |
| `xnu-spacewar-debug.img` | Corrected DEBUG UEFI with framebuffer progress output, same loader and DTB | `0c3cbe31768b4a26234317c3078f752cfd84dd0b3600650f2c9388bc7cebf369` |

Both UEFI builds passed and validated 48 PE images. The reserved descriptor was
verified in both compiled SEC binaries. Both boot images passed structural
inspection and byte comparisons against their built FD, the payload-aware shim,
and the supplied DTB. These checks do not establish that either image boots on
hardware.

Build logs: `logs/spacewar-uefi-release.log`, `logs/spacewar-uefi-debug.log`.
Image audits: `logs/spacewar-fixed-inspect.log`, `logs/spacewar-debug-inspect.log`.

The latest RAM-order builds also passed, each validating 48 PE images. Their
actual FV APRIORI list places RAM Manager between SMEM and DALSYS. The compiled
SEC map contains the added reservations. The new images passed the FD/shim/DTB
byte comparisons and boot structure inspection.

Latest logs: `logs/spacewar-uefi-ram-release.log`,
`logs/spacewar-uefi-ram-debug.log`, `logs/spacewar-ramfix-inspect.log`,
`logs/spacewar-ramfix-debug-inspect.log`.

## Next hardware test

The RAM-order image reached the USB loader, and the minimal XNU test below passed
on the phone. To reproduce, copy `spacewar-smoke-payload.bin` (14,835,136 bytes) to the Windows fastboot
directory. It contains the current `hakios-kernel`, `userland/hello` and
`-v hello_wait=60 loader_return_after=30`. In PowerShell:

```powershell
fastboot stage spacewar-smoke-payload.bin
fastboot oem run
fastboot getvar result
fastboot get_staged spacewar-kernel.log
Select-String -Path .\spacewar-kernel.log -Pattern 'Hello from user space','I am process','loader_return_after expired','kernel returned'
```

Look for `Hello from user space on Darwin!`, process 1 with uid 0, and the timed
`--- loader_return_after expired ---` return. A `returned` result alone can also
follow a panic; inspect the log. The complete successful log places the greeting
and expiry before the kernel message-buffer dump, so `-Tail 80` omitted them.
With the kernel running on the loader's own CPU, USB polling pauses during the
run: an immediate `getvar result` can time out even though the kernel succeeds.
The demonstrated run returned and then served `get_staged` successfully. Boot a
fresh loader before another kernel run.

To reproduce the confirmed firmware/USB test:

From the terminal where fastboot works, with the phone in its bootloader:

```sh
fastboot boot xnu-spacewar-ramfix-debug.img
```

This boots from RAM. Record the last firmware text on the phone. DEBUG UEFI uses
the framebuffer for its progress messages; RELEASE disables these messages.
Check USB independently of what the screen shows:

```sh
fastboot devices
fastboot getvar product
```

`product: xnuloader` means the resident loader is running. Then its log can be
retrieved with `DEVICE=spacewar ./xl log` from an environment where the selected
fastboot executable runs. If it still shows only the Nothing logo and no USB
device, record the final error lines. A recurrence of the DXE allocation failure
is a firmware-memory issue; XNU driver debugging begins only after the loader
answers on USB.

For normal resident-loader entry after confirming this build works:

```sh
DEVICE=spacewar XL_DTB=spacewar.dtb ./xl enter
```

`ADB` and `FASTBOOT` may name specific executables, including Windows executables
under WSL. Initial kernel testing can use `XL_RAMDISK=userland/hello`; this checkout
does not have a Darwin root/system image. Keep phone drivers disabled until the
loader and minimal kernel path work.

## Reproduce

Apply the saved device overlay to the existing Mu tree, then build from that tree
with the configured Python environment:

```sh
cp -r devices/spacewar/uefi/files/. Mu-Silicium/
cd Mu-Silicium
../../venv/bin/python build_uefi.py -d spacewar -r RELEASE
../../venv/bin/python build_uefi.py -d spacewar -r DEBUG
cd ..
python3 xnuloader/mkpayload.py -o /tmp/spacewar-usb-payload.bin loader.efi=prebuilt/loader.efi --usb
python3 mkxnuimg.py -d spacewar --dtb spacewar.dtb -p /tmp/spacewar-usb-payload.bin -o xnu-spacewar-ramfix.img
python3 mkxnuimg.py -d spacewar --mode DEBUG --dtb spacewar.dtb -p /tmp/spacewar-usb-payload.bin -o xnu-spacewar-ramfix-debug.img
python3 tools/inspect-boot-image.py xnu-spacewar-ramfix-debug.img --dtb spacewar.dtb \
  --fd Mu-Silicium/Build/spacewarPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd
```

Refresh prebuilts with `DEVICE=spacewar DTB=spacewar.dtb tools/update-prebuilt.sh`
when desired; it rebuilds the shim and saves the boot configuration, including
header v3, alongside the device artifacts.
