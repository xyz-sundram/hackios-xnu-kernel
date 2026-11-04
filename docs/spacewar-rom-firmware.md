# Spacewar touch firmware from the current ROM archive

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The supplied AOSPA archive contains the matching touch-controller firmware
**inside its boot kernel**. The extracted 119,284-byte blob is byte-for-byte
identical to Nothing's `FT3680_WXN_M146_V27_D01_20220706_app.i` reference,
SHA-256 `04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726`.
It is saved as
`build/spacewar-rom-fw/FT3680_WXN_M146_V27_D01_20220706_app-from-aospa-kernel.bin`.
This establishes the firmware's provenance in the user's ROM, without relying
on a similar phone's firmware or only the device-tree IC-type value.

No firmware was sent to the phone, no partition was mounted or flashed, and no
common driver was changed by this extraction. Physical controller identity,
device-side ECC and working touch reports still require staged phone tests.

## Archive and kernel provenance

Input: `C:\platform-tools\aospa-uvite-beta-phone1-20240903-image.zip`,
2,520,703,072 bytes, SHA-256
`23fd6d9c597231be2486945e58e11f8d79be70a93a96500a8556850efc7b05fa`.
The ZIP central directory has 12 entries: `android-info.txt`, `boot.img`,
`vendor_boot.img`, `system.img`, `vendor.img`, `product.img`, `system_ext.img`,
`odm.img`, `dtbo.img`, two vbmeta images and `super_empty.img`. Only the boot,
vendor-boot, vendor, ODM and DTBO images were extracted for inspection; the
system/product/system-ext images were not unpacked.

`android-info.txt` requires `lahaina|Spacewar`. Vendor/ODM build properties
identify Nothing A063/Spacewar, Android 14 and fingerprint
`Nothing/aospa_phone1/phone1:14/UKQ1.240723.001/nobody09031921:user/release-keys`,
dated September 3, 2024. The boot header reports Android 14 and the August 2024
patch level.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `boot.img` | 100,663,296 | `3a3025d4b67cb8c5b211763eba6b44998858441e8586c17a599326614465cbd3` |
| Uncompressed `boot/kernel` | 50,573,824 | `94004b2076cbefcf21eae09336a577b6311f667a79a82c73edf1f5e82c9ee2f8` |
| `vendor_boot.img` | 100,663,296 | `45078a0e7bfb3263609478a043a8abd235848da947452197091edcbfabc525e4` |

The kernel banner is `5.4.281-qgki-g1528426aab39`, built with Android clang
17.0.4. The embedded IKCONFIG was decompressed to `kernel.config`. It has
`CONFIG_TOUCHSCREEN_NOTHING=y` and
`CONFIG_TOUCHSCREEN_FTS_DIRECTORY="focaltech_nothing"`. The generic
`CONFIG_TOUCHSCREEN_FTS` option is unset; that does not mean the Nothing touch
driver is absent. Built-in strings include `Focaltech V3.4 20211214`,
`fts_ts_probe`, `fts_ts_init` and `focaltech,fts_ts`.

## Exact embedded selection and header checks

The full matching image occurs once at kernel Image offset **`0x2D88838`**.
The compiled module-table entry at `0x2DA5A30` contains module ID 0, an empty
vendor name, firmware pointer `0xFFFFFFC012E08838` and length **119,284**. The
pointer corresponds exactly to that file offset using kernel link base
`0xFFFFFFC010080000`. The next two entries have firmware length zero. The
layout matches `struct upgrade_module` in the cached Nothing source; these
are decoded binary table fields, not a claim that the driver was executed.

The compiled chip map at `0x26522A8` has type **`0x8A`**, runtime ID **`56:62`**,
ROM ID **`56:62`**, PRAM-boot ID **`56:E2`** and bootloader ID `00:00`. The
matching upgrade-setting row at `0x2DA5B70` specifies 128 KiB application/ECC
limits, ready value `0xA5`, initialization delay 8, no SPI-PE path, length
coefficient **4**, no DRAM write and ECC delay 5. These match the cached
Nothing FT3680 source. Live reads must still identify the physical chip.

The firmware's length header is `0x747D` with complement `0x8B82`:
`0x747D × 4 = 119284`. Bytes `0x10E/0x10F` are `0x27/0xD8`, consistent with the
V27 filename; this is not a read of the running controller's version. The
offline host ECC, using Nothing's big-endian 16-bit-word algorithm and
polynomial `0x8408`, is **`0x22ED`** over the full image. Device ECC has not been
queried or compared.

The current common kernel's other embedded FocalTech image is 116,448 bytes,
SHA-256 `dcf91d0de00baa35954dd5b99b121187af112d7b975970e2b0206e42814b28c6`.
That complete byte sequence is absent from this ROM kernel and differs from
the extracted image. It must not be substituted for this ROM's M146 image.

## DTBO and filesystem selection evidence

The vendor-boot image has two base DTBs, neither containing a FocalTech node.
The touch definitions are in the three DTBO entries; all three contain the
same relevant FocalTech wiring: SPI at 6 MHz, reset GPIO105, IRQ GPIO81,
1080×2400 coordinates and `focaltech,ic-type=0x3658D488`. Their fixups resolve
the analog supply through **L3C** and the I/O supply through **L8C**. These
values agree with the active `spacewar.dtb` supplied earlier. The IC-type
property alone remains insufficient to identify the live controller; the
compiled map and actual ID reads are separate evidence.

The vendor filesystem and vendor ramdisk inventories contain no separate
FocalTech touch firmware file or touch `.ko`. The compiled-in image/table and
Nothing driver configuration explain where this ROM obtains the firmware.
No full system partition extraction was needed.

## Reproduction and records

The pinned extractor reads only `boot.img` from the ZIP, verifies the kernel
and firmware hashes, checks the length header, then writes a local file:

```sh
python3 build/spacewar-rom-fw/extract-controller-firmware.py \
  /mnt/c/platform-tools/aospa-uvite-beta-phone1-20240903-image.zip \
  /tmp/spacewar-current-rom-touch.bin
```

Records under `build/spacewar-rom-fw/` include `zip-central-directory.json`,
`archive-sha256.json`, `firmware-match.json`, `firmware-selection-table.json`,
`chip-map.json`, `firmware-header-check.json`, `dtbo-touch-selection.json`,
`kernel.config`, vendor/ODM build properties and directory listings.
`SHA256SUMS` and `artifact-manifest.json` identify the extracted review files.

The cached official reference is pinned to NothingOSS commit
`1d557de4d904a76c0b679c746cc77cf9ae89b6c6`,
`drivers/input/touchscreen/focaltech_touch/`, with provenance in
`build/reference-spacewar-touch/sources.json`. Its full firmware equality and
matching ID/upgrade table rows are independently confirmed by this archive.
The actual downstream driver directory in the AOSPA build is
`focaltech_nothing`; its exact full source commit was not recovered here.
