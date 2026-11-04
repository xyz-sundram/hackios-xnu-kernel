# Dual boot on Nothing Phone (1)

The storage boot path now works on **Nothing Phone (1)** (`spacewar`, SM7325).
U-Boot starts the EFI loader, which reads and verifies the system image from
UFS, preloads it into RAM, and starts XNU. iOS then reaches the home screen. The user confirmed touch and volume in
the ROOT-only test. HFS+ persistence is also verified: Dark appearance survived
a subsequent quiet RAM restart, with touch, volume and no log text confirmed.
Clean HFS+ sync before returning to the loader passed.

**Installed B startup, HakiOS persistence, the Android A menu entry and automatic
menu rearming are verified after the GPT compatibility repair below. HakiOS's
normal iOS reboot and PC-free return remain unverified; its tested clean return
uses the USB sequence in [daily use](#daily-use).**

## Verified checkpoint — 4 November 2026

| Part | Phone (1) evidence |
|---|---|
| Quiet GPU/touch RAM baseline | Home screen, wallpaper, touch and buttons confirmed; GPU measured at about 550 MHz |
| U-Boot menu and USB | Menu/button navigation, USB fastboot and EFI-loader handoff verified |
| UFS discovery | All six logical units discovered after the host-supply, OPP timing and UIC-result fixes |
| Storage layout | 2.5 GiB `hakios_system` APFS and 1.5 GiB `hakios_data` HFS+ allocated in the unused tail of LUN 4 |
| Image and GPT writes | Both images and every changed GPT range read back with matching SHA-256; both GPT copies verified |
| Boot from stored system | ROOT-only RAM test reached the home screen; user confirmed touch and volume |
| Data volume | HFS+ mounted at `/private/var`; Dark appearance retained across a quiet RAM restart; clean sync passed |
| Installed B menu and Android return | B readback/cold startup, HakiOS, Android A and automatic service rearming verified |

The existing Android userdata partition was neither resized nor wiped. This
phone's firmware LUN had enough unused tail space; that is an observation from
its verified GPT, not a promise about every Phone (1). Allocation and placeholder
repair preserve all real Android partitions, entries 1–67, and their unique
identities and physical bounds; ABL/HAL still manages normal slot attributes. The new
system/data entries now occupy 68/69, reusing the formerly unused zero-type
placeholder slot; entry 70 is cleared. No ESP is needed: the loader and small
XNUP payload fit in the verified 96 MiB B boot image.

The initial layout left the zero-type `last_parti` entry before the new entries.
Qualcomm's ABL update routine advances its destination pointer only for used
entries. An unused hole before used entries can therefore make a slot switch copy
later type/attribute records into earlier GPT slots. Reproducing that behavior explained the observed overlap after an Android
return. The repair makes used entries contiguous, preserves partition GUIDs and
physical extents, and changed only four GPT metadata blocks. Readback and the
Android helper's checks passed; boot images and real Android entries were
unchanged. The subsequent B → HakiOS → Android A round trip kept both GPT copies
and CRCs valid, with entries 68/69 used and 70 empty. Android A remained healthy,
and the service prepared B again without a manual slot change. [Pinned Qualcomm update routine](https://github.com/edk2-porting/edk2-msm/blob/e1952621f419f8db60ed28271264e1b5184c571d/GPLDrivers/Library/BootSlotLib/PartitionTableUpdate.c#L290-L358).

The former UFS `-6` failure is resolved. The host enables its declared supplies,
uses the actual 300 MHz UniPro timing from its OPP table, and propagates UIC command
errors. The VCC correction follows the upstream report that ABL leaves VCC off on
Nothing Phone (1) UFS 3.1 hardware. [Upstream regulator report](https://lists.u-boot-project.org/pipermail/u-boot/2026-May/619649.html).
The corresponding changes are in
[`devices/spacewar/u-boot/`](../devices/spacewar/u-boot/).

The storage path uses the loader's bounded EFI block service; this does not prove
native XNU UFS-controller takeover. The system image remains a RAM root after
preload. The status-bar clock adjustment is [paused with app visibility unresolved](spacewar-statusbar.md).

## Build the source components

First follow the [Phone (1) build recipe](../README.md#build-for-nothing-phone-1-spacewar)
for the matching DTB, private firmware, IPSW inputs and graphics userland. The
common UEFI dependency is pinned by `devices/manifest.conf`; U-Boot is pinned to
v2026.07 by `tools/spacewar-u-boot.sh`.

```bash
python3 tools/hakios-device.py sync hakios-uefi
make spacewar_defconfig FRAGMENTS=spacewar-storage
make kernel JOBS=8
HAKIOS_PERSIST_LOG=0 HAKIOS_LOADER_START_TRACE=0 \
  XNULOADER_OUT=../build/spacewar-loader bash xnuloader/build.sh
SPACEWAR_UBOOT_PROFILE=menu JOBS=8 tools/spacewar-u-boot.sh
```

The storage fragment includes the tested performance configuration and adds the
UFS driver, making eleven drivers. Writes still require the explicit installed
profile. `HAKIOS_PERSIST_LOG=0` keeps EFI log-partition discovery/writes disabled;
USB/RAM logs remain available. `hakios_debug=0` disables routine display logging
without removing the debugging code. Early-entry, initialization and UFS traces
remain optional build flags, off by default.

A firmware-only RAM menu can be packaged without installing anything:

```bash
python3 tools/mkubootimg.py -d spacewar \
  -u u-boot/build-spacewar-menu/u-boot.bin --dtb spacewar.dtb \
  --no-ramdisk -o build/spacewar-menu-ram.img
```

From stock Android fastboot, `fastboot boot spacewar-menu-ram.img` runs that menu
in RAM. `SPACEWAR_UBOOT_PROFILE=storage` builds the read-only storage survey;
`usbdiag` isolates USB startup, and `loaderdiag` starts an embedded diagnostic
payload. Those profiles do not supply installed-slot write permissions.

## Private plan, maps and staged storage

Keep the phone's backups and installation identities outside Git. The current
private recovery collection covers 82 unique partitions and 36 GPT metadata
ranges across the original and updated layouts. Radio/NV backups contain device
identity storage and remain private; no decoded identity values or backup data
are published here.

[`spacewar-install-plan.py`](../tools/spacewar-install-plan.py) consumes a verified
backup directory and the working `system.apfs`. It requires the reviewed manifest
SHA-256 and expected disk GUID, size and block size. It checks every backup hash,
both GPT copies and CRCs, then creates a new private plan directory containing:

- `plan.json` and bounded GPT metadata files that place system/data in entries
  68/69, with contiguous used entries and the required CRC fields updated;
- `install.map`: ROOT flag only, with the complete DATA extent zeroed;
- `install-persist.map`: ROOT and DATA flags, using the same generated identities.

Both maps are 152-byte records defined by
[`hakios.spacewar.install.h`](../drivers/storage/hakios.spacewar.install.h).
They pin the disk, LUN, partition types, unique GUIDs and bounds, plus the system
image byte length and streamed CRC32. Generate those GUIDs once and retain the
plan. Another phone needs its own verified plan.

[`spacewar-install-stage.py`](../tools/spacewar-install-stage.py) defaults to host
validation and read-only phone checks. It requires the plan and backup-manifest
SHA-256 pins, an explicit ADB serial, `system.apfs` and a correctly sized journaled
HFSX image. Its explicit `--apply` mode stages and hashes the files, freezes the
staging directory under root ownership, rechecks live slot/GPT identity, writes
and verifies the two images, then publishes backup GPT before primary GPT. It
never writes boot partitions or selects a slot.

After those writes, use `--verify-staged`, rather than repeating `--apply`: the old
GPT precondition intentionally no longer matches. This mode checks the **new**
GPT and both image readbacks without block writes. `--cleanup-staging` removes
only known staging files after all checks pass. It is for a freshly staged data
volume: normal HFS+ use later changes its whole-image hash. Use each tool's
`--help` for required private paths and pins.

## Installed image and Android return

The installed U-Boot build requires a reviewed private environment:

```bash
SPACEWAR_UBOOT_PROFILE=installed \
  SPACEWAR_UBOOT_INSTALL_ENV=/private/uboot-identity.env \
  JOBS=8 tools/spacewar-u-boot.sh
```

That environment supplies `spacewar_install=1`, the explicit HakiOS slot, disk
GUID, HakiOS boot unique GUID and Android boot unique GUID. RAM profiles supply
none of those permissions. The handoff changes only the identified menu slot's
successful/unbootable flags; Qualcomm ABL handles active-slot selection, type-GUID
changes and the UFS boot LUN.

Use `xnuloader/mkpayload.py --usb-auto` with a private `install.map=FILE` entry to
start the stored system automatically while retaining optional USB logs. Use
`install.map` for the first ROOT-only test. The later data profile uses
`install-persist.map`, the storage kernel and the approved GPU/touch command line
with `spacewar_install=1 spacewar_storage=1 ufs=2 ufs_write=1
var_hfs=ufs_darwin_data`. It keeps the system in RAM and gives write access only to
the pinned HFS+ extent. Do not add the older `root_apfs=ufs_darwin` layout.
Package the matched loader, XNUP and installed U-Boot binary with
`tools/mkubootimg.py -d spacewar --dtb spacewar.dtb`, and verify the result fits the
actual boot partition. Packaging an image does not flash or activate it.

The [Android return helper](../tools/android/spacewar-boot-menu.md) uses the phone's
existing Magisk `bootctl` client and Qualcomm HAL. Its private identity pins the
GPT, boot unique GUIDs, exact nodes/bounds and full boot B image hash. `--check`
performs the guards without selecting a slot. The optional Magisk service prepares
B after a successful Android A boot without rebooting Android. An OTA or other
boot-image change causes a hash mismatch and refusal until the new installation
is reviewed; the helper does not overwrite an update or bypass that mismatch.
The helper's checks and automatic rearm service passed after the GPT repair.
Android A stayed current and healthy while the service made B active for the next
boot, without a manual prepare command. The original A boot-image hash and the
installed B boot-image hash both remained unchanged. The user then confirmed
that a normal Android reboot returned to the menu and HakiOS started again.

## Daily use

The installed boot menu appears without a PC. Use volume to select **HakiOS** or
**Android**, then power to confirm. HakiOS startup, retained Dark appearance,
touch/volume and a display without log text are phone-confirmed. The Android
entry boots Android A normally.

The tested clean HakiOS return uses the loader's USB fastboot connection:

```sh
fastboot oem stop
fastboot getvar result
```

Wait until `getvar result` reports `returned`, then run:

```sh
fastboot reboot
```

The clean stop syncs HFS+ before return. From Android, reboot normally to return
to the menu: its guarded Magisk service prepares B after Android A finishes
booting. This automatic rearm passed the post-repair round trip. A normal iOS
reboot and PC-free switching out of HakiOS are not verified. The five-finger, 1.5-second escape exists
in source but is not hardware-tested and returns to loader recovery USB, not the
menu.

Relevant host checks are `spacewar-install-plan-test.py`,
`spacewar-install-stage-test.py`, `loader-install-map-test.py`,
`spacewar-ufs-install-test.py` and `android/spacewar-boot-menu-test.py` under `tools/`.
Hardware keys remain the recovery path to stock fastboot during RAM testing.
