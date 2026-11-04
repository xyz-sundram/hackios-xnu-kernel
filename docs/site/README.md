# Phone (1) guide as a web page

`index.html` is a standalone summary of this fork's Nothing Phone (1) status,
verified RAM-boot path and ongoing U-Boot work. Open it locally or serve this
folder with GitHub Pages. It contains no installation/repartition instructions:
The full U-Boot → EFI loader → XNU → iOS RAM path is phone-verified at 550 MHz with
physical touch and volume, including a quiet run with debugging off and no log
text. Menu/input and USB fastboot are verified; quiet-run live-log stability is
unverified. The read-only UFS
survey fails with `-6`; loading from storage and persistent dual boot still need
validation. Recovery to stock fastboot currently uses the hardware keys.

The maintained technical pages are [Phone (1) status](../spacewar-status.md),
[RAM boot](../getting-started/phone.md) and [dual boot](../dual-boot.md).
This static page does not update the separately hosted upstream website.
