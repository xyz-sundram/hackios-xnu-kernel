# Spacewar FocalTech report-decoder groundwork

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


An isolated C candidate now decodes the official Nothing driver's standard
12-bit FocalTech report and tracks contact lifecycles. It is not connected to
the kernel or current hardware probes. Controller identity, firmware selection
and a live packet must establish the phone's actual report mode before this
candidate is used for input. No phone, emulator, GPIO, SPI or power operation
was performed for this work.

Files:

- `build/spacewar-touch-reports/spacewar_ft12.c` and
  `build/spacewar-touch-reports/spacewar_ft12.h`: pure decoder,
  state update and explicit cleanup helper.
- `build/spacewar-touch-reports/test_spacewar_ft12.c`:
  hand-authored packet fixtures calling that actual helper.
- `build/spacewar-touch-reports/run-tests.sh`,
  `build/spacewar-touch-reports/test.log`,
  `build/spacewar-touch-reports/manifest.json` and
  `build/spacewar-touch-reports/SHA256SUMS`.

## Pinned format

The reference is NothingOSS `android_kernel_msm-5.4_nothing_sm7325`, branch
`sm7325/v/mr`, commit `1d557de4d904a76c0b679c746cc77cf9ae89b6c6`. Cached
`focaltech_core.c`, `focaltech_core.h` and configuration hashes were verified
against the pinned source record. Their cached files remain unchanged.

The vendor defaults to a 62-byte read from register `0x01`. Byte 1 carries the
frame type in its high nibble and the active-finger count in its low nibble.
Type 0 is the default format. Ten six-byte records begin at byte 2. No register
address byte is prepended to the returned buffer. Sources:
[constants and frame types](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.h),
[read and default parser](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.c#L684-L810).

For a record beginning at `2 + 6 * slot`:

| Relative byte | Meaning |
|---:|---|
| 0 | Event in bits 7–6; X bits 11–8 in the low nibble |
| 1 | X bits 7–0 |
| 2 | Contact ID in the high nibble; Y bits 11–8 in the low nibble |
| 3 | Y bits 7–0 |
| 4 | Pressure, independent of both coordinates |
| 5 | Area, independent of both coordinates |

Events are DOWN 0, UP 1 and CONTACT 2. In default mode, any contact ID at least
10 terminates the record list; it is not a contact. Zero pressure/area use the
vendor's defaults `0x3F`/`0x09`. These offsets and formulas come directly from
the pinned parser. They avoid the common Redwood decoder's fifteen-bit
coordinate construction, which incorporates pressure bits.

The current DTB supplies display coordinates `0, 0, 1080, 2400`. The vendor
passes these maxima directly to Linux ABS limits, so the helper makes them
explicit and inclusive: X 0–1080 and Y 0–2400. Future Darwin normalization and
pixel-edge mapping remain separate work. Source:
[DT parsing and ABS registration](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.c#L1059-L1060).

## Lifecycle and rejection policy

DOWN or CONTACT for an inactive ID begins a contact; either event for an
active ID moves it. This recovers a missed initial DOWN during future polling.
An explicit UP releases an existing contact at its last accepted position.
The vendor does not report UP coordinates, so undefined coordinates in an UP
record are not used for bounds checking or position changes. A previously
active contact missing from a complete valid frame gets an inferred release.
The candidate also exposes explicit release-all cleanup for a future stop or
recovery path. Source:
[vendor slot reporting and disappearance handling](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.c#L497-L559).

An empty default frame with no valid records is rejected, matching the vendor
parser. A valid explicit-UP record can carry finger count 0 and also release
other omitted contacts. An invalid transport read or malformed frame does not
release contacts automatically; neither state nor output changes on rejection.

Several checks are deliberately stricter than the vendor parser: duplicate
IDs, event 3, active positions beyond configured bounds, and a mismatch between
the default header's finger count and the active records reject the entire
frame. The vendor only checks that DOWN/CONTACT has a nonzero finger count.
These policies prevent a contradictory or partial snapshot from generating
false input. They must be checked against captured live packets before
integration. Event-number, extra-message, pen and gesture modes are rejected;
their semantics are not guessed.

## Host verification

Run:

```bash
bash build/spacewar-touch-reports/run-tests.sh
sha256sum -c build/spacewar-touch-reports/SHA256SUMS
```

The actual helper passed 1,699 checks per run under GCC with strict warnings
and Clang with AddressSanitizer/UndefinedBehaviorSanitizer. Hand-authored bytes
decode X 1001 and Y 2307, including the nonzero Y high nibble. Sweeping all 256
pressure values and all area values leaves X/Y unchanged. Fixtures also cover
ten simultaneous IDs; down, move and up; missing contacts; explicit cleanup;
inclusive edges; vendor terminators; malformed lengths, types, counts, IDs,
events and coordinates; and atomic rejection when a later slot is invalid.

These checks establish decoder behavior for constructed standard-format
packets. They do not establish the fitted controller, loaded firmware, live
packet layout, electrical access or working phone touch.
