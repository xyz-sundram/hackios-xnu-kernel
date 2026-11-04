# Nothing Phone (1) (`spacewar`)

Snapdragon 778G+ / SM7325, using the shared SC7280 layer. This device port is
included in the common repository. The [root README](../../README.md#build-for-nothing-phone-1-spacewar)
covers the complete reproducible build; [hardware status](../../docs/spacewar-status.md)
records Phone (1) results.

## Verified configuration

Home screen/wallpaper, physical single-contact touch, power/volume keys, seven XNU
cores (7–1), loader USB on CPU0, temperature readings, and Mesa/OpenGL ES on Adreno
642L at 550 MHz. Big/prime CPU requests are about 1900/1862 MHz. The user confirmed
smoother rendering with touch/buttons working. Long-duration stability and multitouch
are unverified. Audio, networking, cameras, battery reporting, charging and storage
persistence are not established working features.

```bash
make spacewar_defconfig FRAGMENTS=spacewar-performance
test -d xnu-src/xnu-8796.121.2 || xnu/apply.sh
python3 tools/spacewar-touch-firmware.py fw-touch/spacewar-m146.bin
make kernel JOBS=8
```

The performance fragment adds the graphics and CPU clock drivers to the base device
configuration. Do not enable the shared audio, Wi-Fi or camera profiles until their
Nothing-specific power/pin/firmware settings have been validated.

## Private inputs

- `spacewar.dtb`: matching Android device tree; the tested board reports Qualcomm Yupik QRD.
- `fw-touch/spacewar-m146.bin`: M146 V27, 119284 bytes, SHA256
  `04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726`.
- `fw-gpu/`: `a660_gmu.bin`, `a660_sqe.fw`, `a660_zap.mdt`, `a660_zap.b02`.
- `Mu-Silicium/Binaries/spacewar/`: vendor UEFI modules from this phone.
- Your own iOS 16.5 IPSW/system images and generated loader identity.

The private firmware archive's README explains restoring those inputs. No proprietary
firmware is committed. The Spacewar touch extractor is pinned to the tested ROM;
it rejects a different kernel/blob instead of silently selecting another panel's image.

## UEFI and source layout

[The UEFI overlay](uefi/README.md) reserves the current ROM's memory carveouts and
64 MiB payload region, and runs RAM discovery early enough for DXE initialization.
`uefi/apply.sh spacewar` applies it on top of the pinned common HakiOS UEFI changes.
The tested result is `DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd`.

| Path | Purpose |
|---|---|
| `device.conf` | Phone (1) status and device metadata; source of generated docs |
| `configs/spacewar_defconfig` | Common + SC7280 + Spacewar configuration |
| `configs/spacewar.config` | Device guards and enabled/disabled drivers |
| `soc/` | Shared SC7280 family facts |
| `soc.args` | SoC addresses extracted from the supplied DTB |
| `uefi/files/` | Spacewar UEFI source overlay |
| `prebuilt/boot-config.toml` | Source-controlled boot packaging parameters |

`soc.args` records addresses, not proof that a peripheral works. The legacy userland
mount `/redwood` is shared runtime ABI and is still used on this phone.

Validate with `python3 tools/hakios-device.py check spacewar`. See
[dual boot](../../docs/dual-boot.md) for the separate installation implementation.
