# HakiOS on Nothing Phone (1)

This fork targets Nothing Phone (1), codename `spacewar`, with Snapdragon 778G+ / SM7325.
The tested build uses Mu-Silicium, a seven-core XNU kernel, physical touch and
Mesa/OpenGL ES graphics at 550 MHz. It runs the supplied iOS 16.5 userland in RAM.

- [Build for Phone (1)](../README.md#build-for-nothing-phone-1-spacewar): private inputs, UEFI, kernel, graphics and packaging.
- [Run on the phone](getting-started/phone.md): Windows launcher, logs, stopping and recovery.
- [Hardware status](spacewar-status.md): results established on this phone.
- [Dual boot](dual-boot.md): installation implementation and validation status.
- [Device page](devices/spacewar.md) and [device source](../devices/spacewar/README.md).
- [QEMU](getting-started/qemu.md), [architecture](concepts/architecture.md), [configuration](configs.md).
- [Drivers](reference/drivers.md), [boot arguments](boot-args.md), [tools](reference/tools.md).

## What works

| Feature | Nothing Phone (1) result |
|---|---|
| Home screen and wallpaper | Working at 1080 × 2400; user confirmed |
| Physical touch | Single-contact taps, releases and swipes working with GPU acceleration; multitouch unverified |
| Power and volume keys | Working with touch and GPU enabled |
| CPU | Seven XNU cores (7–1); CPU0 serves USB; big/prime requests about 1900/1862 MHz |
| GPU | Adreno 642L with Mesa/OpenGL ES; 450/550 MHz draw/readback passed; quiet 550 MHz UI user-confirmed |
| Temperature readings | 27 sensors read; emergency thermal-stop behavior untested |
| Persistent data and native storage | Not established working; current GUI runs from RAM |
| U-Boot port | RAM menu, volume/power navigation, U-Boot USB and EFI-loader USB phone-verified; XNU handoff and storage installation unvalidated; UFS scan fails |
| Wi-Fi, audio, microphone, vibration, cameras | Not established working on this phone |
| Battery level, charging, Bluetooth, cellular | Not established working on this phone |
| Metal rendering | Not verified; the working graphics path is OpenGL ES |
| Apple online services | Unsupported |

The root README and this page describe Phone (1) results. Older bring-up reports
and shared-driver reviews retain their original device identities and observations;
they are historical engineering references, not additional Phone (1) feature claims.
The runtime mount path `/redwood` is a legacy shared-userland path, including on
Spacewar; changing that string would require a coordinated runtime migration.

## Source dependencies

The Phone (1) configuration and UEFI overlay are in this repository. `devices/manifest.conf`
pins the shared UEFI and optional upstream reference device. A dependency's hardware
status does not describe this phone. Proprietary firmware, the DTB and iOS images
remain private build inputs. Apple online services are unsupported.
