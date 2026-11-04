# Android return to the Phone (1) boot menu

The reviewed installation uses Android A and the HakiOS menu in B. The helper
uses Android's existing `/data/adb/magisk/bootctl` HAL client to prepare B for the
next boot. Android keeps running. `--check` runs every identity and state check
without selecting a slot or rebooting. `--reboot-menu` additionally reboots when the
user explicitly invokes that mode.

The helper requires a completed, successful Android A boot with exactly two
slots and no snapshot merge. Before selecting B it verifies the GPT with
`/system/bin/sgdisk`, disk and both boot unique GUIDs, partition labels, exact
bounds/device mappings and the SHA-256 of the entire 96 MiB B partition. It does
not flash images, edit GPT directly or mark a slot successful.

The surveyed OEM GPT has 96 entries at LBA 2 (ending at 4), usable LBAs
6..1572858 and 4096-byte sectors. Its two known gdisk warnings describe the
one-sector gap and the 12288-byte table below the specification's 16 KiB minimum.
The helper accepts only those exact warning paragraphs after the private disk
GUID and all four GPT geometry fields match both the identity and printed GPT.
Changed, duplicate or additional diagnostics, recovery messages and CRC errors
remain failures. No GPT geometry is repaired or rewritten by this allowance.

The private identity is `/data/adb/spacewar-boot-menu/identity.env`, owned by root
and mode 0600. Its format is shown in `spacewar-boot-menu.identity.example.env`;
placeholders intentionally fail validation. The displayed geometry belongs to
the surveyed phone and must be checked against the installation record. Fields
are parsed as data, never sourced as shell commands. An explicit
`--identity FILE` selects another reviewed identity file.

An optional Magisk service.d entry is provided in
`spacewar-boot-menu-service.sh`. After review, its installed copy calls the
helper at `/data/adb/spacewar-boot-menu/spacewar-boot-menu.sh` in
`--prepare-next-boot` mode after `sys.boot_completed=1`. It never reboots Android.
No installer or phone deployment is performed by these source files.

Preparing B through the HAL can reset B's retry/successful state. The installed
U-Boot profile must establish its own successful-boot handoff only after the
reviewed GUI validation; this helper does not certify HakiOS startup. Returning
from B to Android A remains the installed U-Boot/ABL handoff's responsibility.

Run the fake-command regression on the host with
`python3 tools/android/spacewar-boot-menu-test.py`. It executes the actual shell
validation and control flow with command paths redirected to temporary fakes.
