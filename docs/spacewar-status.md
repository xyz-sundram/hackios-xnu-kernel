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
| UFS / stored system | All six LUNs discovered; APFS preload and ROOT-only GUI boot verified with touch/volume |
| Persistent data | HFS+ cross-restart retention verified: Dark appearance survived a quiet RAM restart |
| U-Boot / dual boot | Installed B menu/HakiOS, Android A return and automatic menu rearming verified |
| Status-bar camera clearance | Home-screen adjustment partly works; clock missing in apps; work paused |
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
loader USB are also verified in the diagnostic runs.

UFS now discovers all six logical units after the host-supply, 300 MHz OPP
timing and UIC-result fixes. The regulator change follows the
[upstream Nothing Phone (1) VCC report](https://lists.u-boot-project.org/pipermail/u-boot/2026-May/619649.html).
The user-approved layout adds 2.5 GiB APFS `hakios_system` and 1.5 GiB HFS+
`hakios_data` in the unused LUN 4 tail, preserving every existing GPT entry and
Android's userdata size. Both images and changed GPT ranges passed SHA-256
readback, and both GPT copies were verified.

The ROOT-only storage test preloaded the system into RAM and reached the home
screen with user-confirmed touch and volume. The current eleven-driver build
mounts the HFS+ data volume at `/private/var`, with GPU frequency measured at
about 550 MHz. The user changed appearance to Dark; a subsequent quiet RAM
restart retained Dark, with touch, volume and no log text confirmed. Clean HFS+
sync before return passed. This uses loader-backed storage, not a verified native
XNU controller takeover.
Boot B passed full-image readback and installed startup. The user confirmed its
cold-start menu, HakiOS, retained Dark appearance, touch, volume and no log text.
The Android menu entry boots Android A normally, and A remains healthy with its
boot image unchanged. A Qualcomm GPT placeholder-compaction issue was repaired
without changing Android entries, boot images or filesystem bounds. After the
post-repair round trip, the Magisk service selected B for the next boot without
manual preparation while Android A stayed current and healthy. Both GPT copies
and CRCs remained valid, with system/data entries 68/69 and empty entry 70 exactly
matching the repaired layout. Both boot images retained their expected hashes.
The user then confirmed the menu returned after a normal Android reboot and
HakiOS was running again.

The CPU profile leaves CPU0 in the live USB loader and uses physical cores 7–1 for
XNU. The big/prime EPSS requests are about 1900/1862 MHz; the little domain remains
at its existing request. Thermal readings work; the 105 °C return path was not
physically exercised. `hakios_debug=0` suppresses routine diagnostics without
removing debugging code. `loader_return_after=0 touch_seconds=0` removes scheduled
run and touch cutoffs.

The Mu-Silicium RAM baseline is saved locally in `C:/platform-tools/final`, with
its firmware companion in `C:/platform-tools/private-firmware`. Private recovery
copies now cover 82 unique partitions and 36 GPT metadata ranges across the
original and updated layouts. Radio/NV identity storage remains in private
backups. Generated installation maps, disk/partition identities and backup data
are not part of a source clone.

## Remaining work

The installed startup, persistent settings, Android return and automatic menu
rearming are verified. The tested clean return from HakiOS still uses the loader's
USB stop/reboot sequence; normal iOS reboot and PC-free switching out of HakiOS
remain unverified. See [daily use](dual-boot.md#daily-use). The
[status-bar clock work](spacewar-statusbar.md) remains paused with app visibility
unresolved. Wi-Fi, audio, battery reporting, haptics and cameras need
Nothing-specific profiles and phone tests. Multitouch, edge calibration and longer
stability runs remain open. No working claim is inherited from another SM7325 device.

See [GPU clocks](spacewar-gpu-clocks.md), [CPU profile](spacewar-quiet-performance.md),
[touch and GPU](spacewar-gpu-touch.md), and [dual boot](dual-boot.md).
