# Phone (1) U-Boot port

Source-only U-Boot support for Nothing Phone (1), `spacewar` / SM7325. The current
work validates RAM startup before any installation. The verified iOS GUI baseline
still uses Mu-Silicium; the complete U-Boot → loader → XNU path and persistent
storage are not established working Phone (1) features.

## Current results

- The LLD layout correction allowed U-Boot to complete diagnostic startup.
- With `stdin=nulldev`, the USB diagnostic profile reached fastboot and supplied a
  console log.
- The corrected custom menu displays on the phone, with working volume/power
  navigation. Selecting its USB fastboot entry physically reaches fastboot, and
  the console log can be retrieved. Startup tracing is off in this quiet build.
- The earlier button-enabled failure was console assignment before
  `jumptable_init`: it dereferenced a NULL `gd->jt` after keyboard start returned.
  `input-console-init-order.patch` defers assignment until the jump table exists;
  the corrected menu's startup and input are now phone-verified.
- The storage-enabled kernel has been built but not phone-tested. These RAM tests
  have not written partitions, changed the active slot or installed a boot image.
- The separate U-Boot storage profile reaches USB fastboot, but its read-only
  UFS/SCSI scan returns `-6`, including after the reference-clock adjustment.
- Return to stock fastboot uses hardware keys. Software reboot to the Android
  bootloader is unsupported in these RAM profiles; `reset bootloader` only resets
  the phone and is not a bootloader-selection command.

## Build

The base is **U-Boot v2026.07**, commit
`ece349ade2973e220f524ce59e59711cc919263f`. The build script checks that exact commit,
then applies the pinned common `hakios-uefi` changes and this device's patch stack.
It uses an AArch64 GCC when available, otherwise Clang/lld and LLVM binutils.

From the kernel repository root:

```bash
python3 tools/hakios-device.py sync hakios-uefi
JOBS=8 tools/spacewar-u-boot.sh
```

| Profile | Selection | Default result |
|---|---|---|
| Menu | `SPACEWAR_UBOOT_PROFILE=menu` (default) | `u-boot/build-spacewar-menu/u-boot.bin` |
| USB diagnostic | `SPACEWAR_UBOOT_PROFILE=usbdiag` | `u-boot/build-spacewar-usbdiag/u-boot.bin` |
| Read-only storage survey | `SPACEWAR_UBOOT_PROFILE=storage` | `u-boot/build-spacewar-storage/u-boot.bin` |
| Automatic loader diagnostic | `SPACEWAR_UBOOT_PROFILE=loaderdiag` | `u-boot/build-spacewar-loaderdiag/u-boot.bin` |

The diagnostic profile disables UART and UFS probing and uses `stdin=nulldev` to
isolate USB startup from button-console initialization. The storage profile uses
`stdin=nulldev`, runs `scsi scan`/`scsi info`, then serves USB fastboot. The menu profile includes
volume/power input and waits for a choice. Its Android entry restarts the phone;
its HakiOS entry needs a loaded payload. It is not an installed dual-boot image.

`U_BOOT` selects an existing source directory; `SPACEWAR_UBOOT_OUT` selects an
output directory. Use absolute paths when overriding them.

To package the empty RAM menu test, supply this phone's matching Android DTB:

```bash
mkdir -p build/spacewar-dualboot
python3 tools/mkubootimg.py -d spacewar \
  -u u-boot/build-spacewar-menu/u-boot.bin --dtb spacewar.dtb \
  --no-ramdisk -o build/spacewar-dualboot/spacewar-bootmenu-ram.img
```

The packer uses Android boot header v3. It can use Mu-Silicium's AOSP
`mkbootimg.py`, or an explicit `--mkbootimg /path/to/mkbootimg.py`. The RAM-test
image contains no XNU/system payload. From stock Android fastboot on the PC:

```sh
fastboot boot spacewar-bootmenu-ram.img
```

For the diagnostic image, build with `SPACEWAR_UBOOT_PROFILE=usbdiag` and use its
binary path in the packaging command.

## Source changes

Only the patches enumerated by [the build script](../../../tools/spacewar-u-boot.sh)
are applied.

| Group | Purpose |
|---|---|
| Common HakiOS changes and `hakios.patch` | Loader/payload handoff, USB protocol support and the guarded Spacewar slot-state command |
| `lld-layout.patch` | Places BSS in ordinary `NOLOAD` memory, preserving the appended DTB offset and correcting relocation/image reservations |
| `simplefb-rotation.patch` | Initializes the optional framebuffer rotation value |
| `spmi-*.patch` | PMIC Arbiter v5 addressing, byte counts, empty-APID handling, bounded polling and optional diagnostics |
| `input-console-init-order.patch` | Registers the keyboard before the console jump table exists; defers console assignment until it is initialized |
| `abl-hkub-ramdisk.patch` | Finds a bounded, CRC-validated HKUB after Android's vendor ramdisk; rejects corrupt or ambiguous containers before copying |
| `sc7280-ufs-qref.patch`, `rpmhpd-bounds.patch` | UFS reference-clock selection and power-domain bounds for read-only storage diagnosis; storage discovery still fails on the phone |
| `early-trace.patch`, `initcall-trace.patch`, `stdio-trace.patch` | Optional startup-stage and console/input tracing |
| `files/` | Spacewar device trees, defconfigs, environments and the device command |

Startup tracing is off by default. These compile-time switches retain the debug
implementation for diagnosis:

```bash
SPACEWAR_UBOOT_TRACE=1 SPACEWAR_UBOOT_INIT_TRACE=1 JOBS=8 tools/spacewar-u-boot.sh
```

The script validates ELF/BSS/copy-region bounds, the Linux image header and the
appended control DTB after building. Passing these checks does not establish
menu, loader or storage behavior on the phone.

The EFI loader also has optional startup tracing, disabled by default:

```bash
XNULOADER_OUT="$PWD/build/loader-start-trace" HAKIOS_LOADER_START_TRACE=1 xnuloader/build.sh
```

This writes a separate diagnostic loader; `HAKIOS_LOADER_START_TRACE=0` omits its
trace messages. U-Boot reaches the loader screen and its USB command loop from a fresh
embedded payload. XNU execution through this path still needs validation.

The `loaderdiag` profile attempts `hakios load` and `hakios boot` automatically,
then serves U-Boot USB fastboot if those commands return. Package it with a
loader and a USB-only XNUP (`xnuloader/mkpayload.py --usb`), without
`--no-ramdisk`. Its firmware USB status/debug flags are enabled for diagnosis;
the normal menu keeps firmware status output off.

## Installation status

No Phone (1) installation sequence is validated yet. Loader/XNU handoff, read-only
storage discovery and the complete Android return path must pass before a
phone-specific partition plan and verified backups can support an
installation image. The current RAM menu issues no GPT writes or active-slot
changes; its guarded slot-state command is reserved for a separately validated
installation path.

See [dual-boot status](../../../docs/dual-boot.md),
[hardware status](../../../docs/spacewar-status.md), and the
[root build recipe](../../../README.md#build-for-nothing-phone-1-spacewar).
