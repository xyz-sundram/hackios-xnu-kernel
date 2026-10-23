# Spacewar firmware extraction

Source: Nothing Phone (1) `xbl_a.img`, extracted from the device by the user.

- Qualcomm build ID: `BOOT.MXF.1.0-01002-LAHAINA-2`
- Extractor: [UEFIReader](https://github.com/MSMTools/UEFIReader)
- Output: 96 firmware module manifests, their payloads, and the firmware's `RawFiles/`, `DXE.inc`, and `APRIORI.inc`.

The original `.efi` files are the unmodified extraction outputs. The six `.patched.efi` variants for
`ButtonsDxe`, `DALSYSDxe`, `TzDxeLA`, `UsbConfigDxe`, `UsbMsdDxe`, and `UFSDxe` come from the pinned upstream
Project-Silicium Device-Binaries submodule. The Spacewar INF files select those patched variants. The first five
patches are documented for Redwood; upstream does not document the UFSDxe patch rationale for Spacewar.

These binaries still need on-device validation. The platform package selects and orders drivers against this
image; do not treat the extracted DXE/APRIORI lists as a ready-to-build platform configuration. In particular,
display setup needs the correct Spacewar device tree and panel selection.
