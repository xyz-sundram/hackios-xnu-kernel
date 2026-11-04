# Dual boot on Nothing Phone (1)

Dual boot is being implemented for **Nothing Phone (1)** (`spacewar`, SM7325).
The working iOS GUI currently boots through Mu-Silicium and runs in RAM. Persistent
installation and the complete U-Boot → loader → XNU chain have not been validated
on this phone.

## Current implementation

| Part | State on Phone (1) |
|---|---|
| Mu-Silicium RAM boot with touch/buttons and 550 MHz graphics | Phone-verified; [current hardware status](spacewar-status.md) |
| U-Boot source port | Builds from pinned U-Boot v2026.07 with Spacewar device tree, environment and source patches |
| U-Boot diagnostic startup/USB | Phone-verified after the linker fix with `stdin=nulldev`: USB fastboot answered and the recorded console was retrieved |
| Linker-layout correction | Applied: ordinary `NOLOAD` BSS placement restores the relocation reservation and permits full diagnostic startup |
| Menu and buttons | Phone-verified: the quiet custom menu displays and volume/power navigation works |
| Input-console ordering | Fixed: console assignment waits for `jumptable_init`; this resolved the button-enabled startup failure |
| Return to stock fastboot | Use hardware keys; software bootloader restart is unsupported in the current RAM profiles |
| U-Boot read-only storage survey | The separate `storage` profile reaches USB fastboot, but UFS/SCSI discovery returns `-6`, including after the reference-clock adjustment |
| EFI loader USB | Phone-verified from a fresh embedded USB-only payload after finding HKUB within the combined Android initrd |
| Loader-backed storage profile | Source/build preparation exists; persistent reads/writes and GUI boot from storage remain unverified |
| Android/HakiOS boot menu installed on storage | Not validated |
| Persistent `/private/var` | Not established working on this phone |

The quiet menu now reaches U-Boot USB fastboot through a physical button selection;
console retrieval works. Startup tracing is off in that build. The read-only
storage survey has not discovered a usable disk. The loader/XNU and storage
handoff still need separate tests before installation can be validated.
No partition writes were performed in these tests.

## Build the RAM test

Use a Linux/WSL build host and the source dependencies/private inputs described in
the [Phone (1) build recipe](../README.md#build-for-nothing-phone-1-spacewar).
`uefi/` must be synced at the revision in `devices/manifest.conf`.

```bash
python3 tools/hakios-device.py sync hakios-uefi
JOBS=8 tools/spacewar-u-boot.sh
mkdir -p build/spacewar-dualboot
python3 tools/mkubootimg.py -d spacewar \
  -u u-boot/build-spacewar-menu/u-boot.bin --dtb spacewar.dtb \
  --no-ramdisk -o build/spacewar-dualboot/spacewar-bootmenu-ram.img
```

The build script applies the pinned common U-Boot changes and the patches/files in
`devices/spacewar/u-boot/`. It uses an AArch64 GCC when available, otherwise Clang
and LLVM tools. Early-entry and initialization tracing flags are off by default.
The packer uses the Phone (1) Android boot header v3 and the supplied matching DTB.
`U_BOOT` and `SPACEWAR_UBOOT_OUT` select separate source/output trees.

The image above contains no XNU/system payload. It is for firmware startup and menu
validation. From **stock Android fastboot**, after copying that image to the PC:

```sh
fastboot boot spacewar-bootmenu-ram.img
```

This is a RAM test. The current menu waits for a choice rather than installing
anything. Its Android entry restarts the phone; its HakiOS entry needs a loaded
payload. `SPACEWAR_UBOOT_PROFILE=usbdiag` skips UART and storage probing and uses
`stdin=nulldev` to isolate USB startup. `SPACEWAR_UBOOT_PROFILE=storage` also uses
`stdin=nulldev`, runs a read-only UFS/SCSI survey, then enters USB fastboot. Its
default binary is `u-boot/build-spacewar-storage/u-boot.bin`; the USB-only profile
uses `u-boot/build-spacewar-usbdiag/u-boot.bin`. Both have reached USB fastboot on
the phone, but the storage survey still fails with `-6`.

## What installation still needs

The source includes a bounded loader-storage profile and an installed-boot handoff
command. The handoff changes only the current slot's successful/unbootable state,
with identity and GPT checks. Qualcomm ABL remains responsible for selecting the
other active slot, its GUIDs and the UFS boot LUN. Host tests of those checks do not
establish that the phone boot path works.

Before installation can be offered, the Phone (1) needs successful RAM tests of
the loader and XNU, read-only storage discovery, the
Android return path and a phone-specific storage layout with verified backups.
The actual phone's boot partition sizes, active slot and GPT must determine the
plan. No fixed 20 GiB partition, slot-b Android layout or 12 GB data-volume result
is established for this phone.

The earlier generic installation guide described another device. Its partition
writes, data wipe and slot commands are not a Phone (1) installation procedure.
This page therefore provides no flash, repartition or wipe sequence yet.

## Recover a RAM test

Use the hardware keys to return to stock Android fastboot. From stock fastboot,
`fastboot reboot` boots the existing active Android slot. U-Boot's current RAM
profiles do not support software reboot to the Android bootloader: the fastboot
command depends on an unavailable MMC boot-control path, and `reset bootloader`
does not select that destination. The RAM tests do not change the active slot or
install a boot image.

[U-Boot reference](u-boot.md) retains earlier engineering history with its original
device identity. The [Phone (1) status](spacewar-status.md) records this fork's
observations; results from another SM7325 phone do not establish support here.
