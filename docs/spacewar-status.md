# Nothing Phone (1) hardware status

Current baseline: quiet 550 MHz GPU UI with physical touch and buttons, confirmed on
2026-11-04. This page reports Spacewar evidence only. The source of the generated
device page is `devices/spacewar/device.conf`.

| Feature | Nothing Phone (1) result |
|---|---|
| Home screen and wallpaper | Working at 1080 × 2400; user confirmed |
| Physical touch | Single-contact taps, releases and swipes working with GPU acceleration; multitouch unverified |
| Power and volume keys | Working with touch and GPU enabled |
| CPU | Seven XNU cores (7–1); CPU0 serves USB; big/prime requests about 1900/1862 MHz |
| GPU | Adreno 642L with Mesa/OpenGL ES; 450/550 MHz draw/readback passed; quiet 550 MHz UI user-confirmed |
| Temperature readings | 27 sensors read; emergency thermal-stop behavior untested |
| Persistent data and native storage | Not established working; current GUI runs from RAM |
| U-Boot port | Quiet RAM boot to the 550 MHz iOS GUI with touch/volume and no log text phone-verified; menu/buttons and loader USB work; UFS scan fails; persistent installation unvalidated |
| Wi-Fi, audio, microphone, vibration, cameras | Not established working on this phone |
| Battery level, charging, Bluetooth, cellular | Not established working on this phone |
| Metal rendering | Not verified; the working graphics path is OpenGL ES |
| Apple online services | Unsupported |

## Verified baseline

The phone reached the home screen and displayed wallpaper. The user confirmed taps,
swipes, releases and volume/power controls with graphics enabled. Adreno firmware
came from the matching Phone (1) ROM; 450 and 550 MHz passed drawing/pixel-readback
checks. The user reported improved smoothness at 550 MHz; there is no measured FPS
or long-duration stability result.

The full U-Boot → EFI loader → XNU → iOS RAM path is now phone-verified at
550 MHz, including a quiet run with `hakios_debug=0` and firmware USB status/debug
disabled. The user explicitly confirmed touch/volume and no log text. The touch driver
preserves U-Boot's idle RSC slot IRQ state under the exclusive loader lease.
Its 119,284-byte controller-RAM firmware upload completed with matching ECC
`0x22ed` and runtime/version checks. U-Boot menu navigation and both U-Boot and
loader USB are also verified in the diagnostic runs. Quiet-run live-log stability
was not established: its collector did not fetch a first snapshot within 60 seconds.
Native storage remains blocked by UFS/SCSI scan
error `-6`; no persistent installation or partition writes were performed.

The CPU profile leaves CPU0 in the live USB loader and uses physical cores 7–1 for
XNU. The big/prime EPSS requests are about 1900/1862 MHz; the little domain remains
at its existing request. Thermal readings work; the 105 °C return path was not
physically exercised. `hakios_debug=0` suppresses routine diagnostics without
removing debugging code. `loader_return_after=0 touch_seconds=0` removes scheduled
run and touch cutoffs.

The exact local working bundle is `C:/platform-tools/final`. Its firmware-only
companion is `C:/platform-tools/private-firmware`. Both contain manifests/checksums;
these private artifacts are not part of a source clone.

## Remaining work

Validate persistent storage and dual boot separately from the RAM baseline. Wi-Fi,
audio, battery reporting, haptics and cameras need Nothing-specific profiles and
phone tests. Multitouch, edge calibration and longer stability runs remain open.
No working claim is inherited from another SM7325 device.

See [GPU clocks](spacewar-gpu-clocks.md), [CPU profile](spacewar-quiet-performance.md),
[touch and GPU](spacewar-gpu-touch.md), and [dual boot](dual-boot.md).
