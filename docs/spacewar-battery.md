# Nothing Phone (1) battery percentage

The Phone (1) battery profile now uses the published Nothing PMIC GLINK property
mapping and forwards measured fuel-gauge samples to Darwin's `IOPMPowerSource`.
Code and host checks are complete; live ADSP replies and the iOS battery display
still require the phone test. A voltage curve is not used as an iOS percentage.

The profile follows Nothing's Android 15 source at
[`1d557de4`, qti_battery_charger.c](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/power/supply/qti_battery_charger.c).
Its `battery_property_id`, `usb_property_id` and `battery_psy_get_prop` definitions
establish the wire properties and conversion units:

| Measured value | Phone (1) GET property | Wire unit |
|---|---:|---|
| State of charge | Battery 4 | Hundredths of a percent |
| Battery voltage | Battery 7 | Microvolts |
| Battery current | Battery 9 | Signed microamps, positive while charging |
| Battery temperature | Battery 12 | Hundredths of a degree Celsius |
| Charge counter | Battery 14 | Microamp-hours |
| Cycles | Battery 15 | Count |
| Design/full charge | Battery 16/17 | Microamp-hours |
| USB online | USB 0 | Boolean |
| USB adapter type | USB 7 | `POWER_SUPPLY_USB_TYPE_*` |

The old Redwood map uses temperature 13, charge fields 15–18 and USB type 6;
those values are kept for that device only. Both profiles use owner 32778 and
GET opcodes `0x30`/`0x32` on `PMIC_RTR_ADSP_APPS`. Numeric replies must be exactly
24 bytes, with the expected owner, response type, opcode, property and success
result. SET opcodes are refused by the query routine. No charger initialization,
current/voltage limit, shipping-mode, notification-enable or firmware-update
command is sent.

Build both `CONFIG_HAKIOS_ADSP=y` and `CONFIG_HAKIOS_BATTERY=y`. Use the matching
Phone (1) ADSP firmware, the 40 MiB reserved-memory profile and `adsp=1`;
`battmgr=1` is its default. Do **not** use `battery=1` for this path: that selects
the separate vendor-UEFI cache and disables DSP queries. The installed U-Boot
firmware has not established that vendor charger protocol.

The combined touch/GPU/storage test uses
`make spacewar_defconfig FRAGMENTS=spacewar-services`. Its exact 18-driver
configuration includes the read-only NXP speaker probe; playback remains off.
The loader, touch lease and CPU performance gates require
`spacewar_services=1`, the complete installed-storage tuple and the existing
CPU7/seven-core/GPU profile before admitting bounded ADSP, Wi-Fi, haptic, PMIC
or speaker probe options. The original ten- and eleven-driver gates remain
separate. `battery=1`, native UFS and arbitrary hardware-address overrides are
outside this combined profile.

The Android reference captured on 4 November 2026 was 100%, approximately 4.49 V,
+0.846 A, 37.0 °C and 4,357,000 uAh design/full capacity, with a USB-PD source
online. It is a comparison point, not a fixed percentage to put in a build.
Confirm the real GLINK values and `battery_test`/`kern.hakios.battery_*` output
before claiming live iOS reporting. SOC outside 0–10000, invalid ABI/boolean or
status values, and implausible voltage/current/temperature are rejected rather
than rounded or clamped into a plausible UI value.

`python3 tools/spacewar-battery-test.py` compiles the actual protocol, C bridge,
C++ entry point and power-source projection against host stubs under ASan/UBSan.
It checks both profiles, GET-only traffic, malformed/wrong replies, unknown and
boundary SOC, negative temperature rounding, a charging-to-discharge transition,
and the real Android reference's power-source properties. It does not access a
phone. The firmware-cache bridge also now publishes measured gauge samples to
Darwin; its diagnostic voltage estimate never becomes an iOS power source.
