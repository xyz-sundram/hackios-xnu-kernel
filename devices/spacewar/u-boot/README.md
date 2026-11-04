# Phone (1) U-Boot port

Source-only U-Boot support for Nothing Phone (1), `spacewar` / SM7325. The current
work validates RAM boot before any installation. The complete U-Boot → EFI loader
→ XNU → iOS RAM path is phone-verified at 550 MHz with physical touch and volume,
including a quiet run with debugging off and no log text.
Mu-Silicium remains a separate verified baseline. Persistent storage and
installation are not established working Phone (1) features.

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
- The complete RAM chain reached the iOS GUI at 550 MHz; the user confirmed touch
  and volume after the RSC IRQ-state handoff correction. Controller-RAM firmware
  upload passed matching ECC and runtime/version checks.
- The quiet rerun with `hakios_debug=0` and firmware USB status/debug off passed
  the user's touch/volume check with no log text. Quiet-run live-log stability
  remains unverified: its collector did not fetch a first snapshot within 60 seconds.
  USB and full return-console capture were verified in diagnostic runs.
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

The saved Windows bundle is `C:\platform-tools\final\uboot`. Its
`Start-iOS-U-Boot.ps1` boots the RAM loader and uploads the fixed quiet payload,
using `fastboot.exe` and `system.apfs` from the parent `final` directory. The
launcher source is [tools/spacewar-u-boot.ps1](../../../tools/spacewar-u-boot.ps1);
`-BootImage`, `-PayloadFile` and `-PlatformTools` select locally built inputs.
The shared userland launcher also accepts `-PayloadFile` without replacing the
existing Mu-Silicium payload.

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
trace messages. U-Boot reaches the loader screen and its USB command loop from a
fresh embedded payload. The complete loader/XNU/iOS RAM path is now phone-verified.

The `loaderdiag` profile attempts `hakios load` and `hakios boot` automatically,
then serves U-Boot USB fastboot if those commands return. Package it with a
loader and a USB-only XNUP (`xnuloader/mkpayload.py --usb`), without
`--no-ramdisk`. Firmware USB status/debug output defaults to zero in both this
profile and the normal menu; enable the environment flags temporarily when
diagnosing the USB bridge.

## Touch firmware and power handoff

The private M146 V27 image is 119,284 bytes, SHA-256
`04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726`,
with host ECC `0x22ed`. Its complete bytes are embedded in the XNU kernel, not
the USB-only bootstrap payload. The driver loads controller RAM when needed;
it does not permanently flash touch firmware. Success is established by the
device ECC/runtime checks and FT12 reports, rather than by the blob's presence.

An initial U-Boot GUI test displayed the home screen with 550 MHz graphics and
working volume, but touch stopped before firmware access: U-Boot enables active
RSC slot IRQs `0x3`, while the working Mu-Silicium baseline left `0x1`. The old
preflight rejected slot1's enabled IRQ even though its request/completion state
was idle. The driver now accepts that state only under the exclusive live lease,
preserves slot0/foreign IRQ bits, and restores slot1's original IRQ enable bit
after its own acknowledged request. Run `python3 tools/spacewar-touch-rsc-test.py`
from the repository root for the host transaction regressions.

The corrected driver uploaded all 119,284 bytes in 3,728 packets, matched device
and host ECC `0x22ed`, and verified runtime bytes `0x56:0x62` and A6 version 27.
The user then confirmed physical touch and volume in the 550 MHz iOS GUI through
U-Boot. Twenty sanitizer-backed helper regressions cover the IRQ-state handling
across 24 vote cases.

The phone-verified quiet run used kernel SHA-256
`501873c9f921fa5be1bc57dffb48cd86b40b288293d1c58131fb7f049a9faf76`
and payload SHA-256
`40c7749e6c3b4eed795f800db75a184593450282dfc89074af0bfa31c6bbce0b`.

Verbose live logging has a bounded buffer. Capture the full console on clean
kernel return before concluding that its last live message was the stopping
point; the full return log established this power-preflight failure after all
seven CPUs had started.

## Installation status

No Phone (1) installation sequence is validated yet. Read-only storage discovery,
loading from storage and the complete Android return path must pass before a
phone-specific partition plan and verified backups can support an
installation image. The current RAM menu issues no GPT writes or active-slot
changes; its guarded slot-state command is reserved for a separately validated
installation path.

See [dual-boot status](../../../docs/dual-boot.md),
[hardware status](../../../docs/spacewar-status.md), and the
[root build recipe](../../../README.md#build-for-nothing-phone-1-spacewar).
