# Spacewar hardware port inventory

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The active work queue is now limited to [Redwood's working features](spacewar-redwood-scope.md): **eight remaining targets**, with touch first. This document preserves the broader offline hardware map; its 14-area count and broad probe proposal are historical and do not define the current scope. Audio, cameras and haptics need Nothing-specific profiles before their common Redwood drivers can be used. Matching SoC addresses alone does not establish working hardware.

This is an offline audit of the supplied `spacewar.dtb`, the current-ROM archive `aospa-uvite-beta-phone1-20240903-image.zip`, its boot kernel/config, vendor files and DTBs/overlays, and the common drivers. No phone operation, kernel build, configuration change or payload publication was performed. The source DTB has the generic root model `Qualcomm Technologies, Inc. Yupik QRD`; that string is not a physical component identification.

The planning tool has 18 feature areas. The recorded physical baseline has four working areas: display through UEFI, seven-core CPU operation, temperature readings and buttons. Touch is being ported separately. The other 14 areas below are feature categories, not 14 chips. In particular, working loader USB and RAM-backed APFS do not establish native XNU USB or native storage support.

**Source evidence**

| Input | Exact identity / saved evidence |
|---|---|
| Supplied active-style DTB | `spacewar.dtb`, SHA-256 `70ce7880155f48d55f453ec530656f0c843ce3136f5d9743a404801fd359999b`; 3,789 parsed nodes |
| Current ROM archive | 2,520,703,072 bytes; SHA-256 `23fd6d9c597231be2486945e58e11f8d79be70a93a96500a8556850efc7b05fa` |
| ROM Android kernel | `5.4.281-qgki-g1528426aab39`; SHA-256 `94004b2076cbefcf21eae09336a577b6311f667a79a82c73edf1f5e82c9ee2f8` |
| Reproducible FDT decoding | `build/spacewar-port-inventory/inventory.py`, `*-nodes.json`; phandles resolved with provider cell counts |
| Selected vendor files | `build/spacewar-port-inventory/extracted-files.json`; 25 files, 1,811,287 bytes, all locally extracted and hashed |
| Common Redwood reference | Driver comments and constants, plus `build/reference-redwood/device.conf` and `soc.args`; the reference full DTB is absent, so this is not a full DTB-to-DTB comparison |

The AOSPA ZIP has no `modem.img`, `bluetooth.img`, or equivalent firmware partition image. The fully listed vendor image contains an empty `firmware_mnt` mount point and links to it. Signed `adsp.mdt`/segments, `wpss.mdt`/segments and Bluetooth rampatch/NVM are absent from the inspected vendor/vendor-ramdisk locations; they have not been recovered. System/product/system_ext partitions were not unpacked for this audit, so this is not a claim that every byte of the ZIP was searched. Existing files from another phone must not fill that gap.

**Feature comparison**

| Area | Verified source facts for Spacewar | Common Redwood assumption / next work; physical operation unverified |
|---|---|---|
| Touch | SPI `0xa94000`, reset GPIO105, IRQ GPIO81, L3C/L8C supplies; actual ROM embeds the M146 FT3680 firmware, 119,284 bytes, SHA-256 `04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726` | Separate active touch work owns identity, RAM-only firmware upload and event polling. See [ROM provenance](spacewar-rom-firmware.md). |
| GPU | DT `qcom,adreno-gpu-a642l`, model `Adreno642Lv1`, chipid `0x06030500`; KGSL register block `0x03d00000`; ROM has `a660_gmu.bin`, `a660_sqe.fw`, `a660_zap.*` | Same advertised family as common Adreno642L driver. Extract and validate **this ROM's** firmware, clock/supply/domain/SMMU layout before bring-up. Software home screen is not GPU proof. |
| Storage | UFS host `0x01d84000`, QMP PHY `0x01d87000`; device reset GPIO175; VCC PM7325 L7, VCCQ/ref PM7325 L9; IOMMU stream `0x80` | Host base matches common `hakios.ufshc.c`. First establish UEFI's controller/list offer and partition/LUN identity. `ufs_native=2` has a look-only path; `ufs_native=1/3` changes controller ownership. No storage writes are proposed. |
| Audio | Stereo `tfa,tfa98xx` amplifiers at I2C `0x00988000`, addresses `0x34/0x35`, GPIO8/9 (`qup02`), reset GPIO101 on the first node. WCD938x codec is enabled; WCD937x/WSA883x candidates are disabled. ROM has `firmware/tfa98xx.cnt`. | Common speaker code is Awinic AW88261 at the same two I2C addresses **on GPIO12/13**, with an Awinic ACF profile. It cannot be reused by matching the addresses. Needs NXP probe/profile/protection and Nothing audio routes. ADSP reserved-memory mismatch below blocks loading as written. |
| Battery | DT `qcom,pmic-glink`, channel `PMIC_RTR_ADSP_APPS`, subsystem `adsp`, protection domain `msm/adsp/charger_pd`; child `qcom,battery-charger`. Actual kernel `CONFIG_QTI_BATTERY_CHARGER=y`. | Existing GLINK battery-manager transport is relevant, but its property numbers/units were calibrated on Redwood. Prefer a verified firmware status path or Nothing-specific GLINK mapping; do not assume local QG/SMB register layout or charging support. |
| Haptic | PM8350B slave3 `qcom,hv-haptics`, peripheral bases `0xf000/0xf100/0xf200`; SoundWire `qcom,pm8350b-swr-haptics` also present. Kernel `CONFIG_INPUT_QCOM_HV_HAPTICS=y`. DT includes waveforms and LRA timing. | Common driver is AW86927, I2C `0x5a`, GPIO36/37, reset35, IRQ18, using `aw8697_haptic.bin`. This is a different controller. Requires a PM8350B backend; no Awinic reset, I2C probe, register table or waveform upload. |
| Camera | Current ROM contains IMX766 wide, S5KJN1 ultra-wide, IMX471 front; two supplier variants for front. Three DT sensor nodes, CCI/CSIPHY/reset/MCLK wiring below. | Common code contains OV02B10, S5KHM2SD, OV16A1Q and S5K4H7. Requires new sensor profiles, power-step interpreter, gain/exposure and PHY data. Generating new `.tab` files alone is insufficient. |
| Torch | PM8350C slave2 flash peripheral `0xee00`, `qcom,pm8350c-flash-led`; four channels, 12,500 µA resolution. DT switch masks `0x9`, `0x6`, `0xf`; torch nodes permit up to500mA/channel. | Common code drives channel0 alone at125mA. Same peripheral family supports a read-only survey, but actual LED grouping/current needs a Nothing profile. DT maximum is not the correct normal torch current. |
| Wi-Fi | DT WCN6750; WPSS PAS6; reserved WLAN image region `0x9ae00000 + 0x01900000`; shared SMEM `0x80900000 + 0x200000`. PM7325 S7/S1/S8 supplies; details below. | Much SoC infrastructure matches common driver. Steps1–5 are a survey; later steps authenticate/load/start firmware and manipulate shared processor state. Obtain matching signed firmware, board/calibration data and a complete profile before `wifi=1`. |
| Bluetooth | DT `qcom,wcn6750-bt`; WLAN reset84, BT reset85, antenna-switch86 match common pin table; supply mappings below. Actual vendor HAL contains many alternative rampatch/NVM names. | Common BT support only reads those pins. It does not implement UART/HCI/firmware download. UART selection, version-specific firmware, NVM/MAC and board power sequence remain unverified; a generic HAL filename list does not select the correct blob. |
| Cellular | `devices/spacewar/soc.args` exactly matches cached Redwood values for modem memory, IPA/GSI, IPCC/AOSS/RSC, SMMU and PAS IDs; current ROM contains `yupik_ipa_fws.*` | Address match helps SoC code only. Modem firmware, SIM/QRTR/QMI services, board RF configuration and registration/data remain unverified. Existing Redwood status itself reports no registration. |
| Network | No physical network interface has been verified. Networking above a radio is separate from bringing up that radio. | Common station/interface and Darwin Wi-Fi integration depend on WLAN success; modem IPA is another route. Do not count loopback or Skywalk self-tests as a working network. |
| Native USB | Enabled USB controller is `qcom,dwc-usb3-msm`/`snps,dwc3` at `0x0a600000`, `dr_mode=otg`, IRQ SPI133; HS PHY `0x088e3000`, SS QMP/DP PHY `0x088e8000`. Second controller `0x08c00000` is disabled. | Loader fastboot continues in UEFI on reserved core0. No native DWC3 XNU backend was found in `drivers/`. Requires explicit ownership/handoff, endpoint DMA/events/interrupts and PHY/type-C profile. Avoid touching the live loader controller while it owns fastboot. |
| Sensors | ROM sensor HAL selects `sensors.nothing.so` and `sensors.ssc.so`; SSC registry/config/protobuf files are present. Several candidate chip profiles share the same SoC475/QRD selector. | Installed profile names do not prove fitted chips. First obtain actual Android sensor-service inventory/persist registry or read-only SSC enumeration. Do not probe application-processor I2C blindly: SSC bus-instance numbers are not a proved AP GENI bus mapping. |

**Battery and PMIC first**

The DT lists PMK8350 slave0, PM7325 slave1, PM8350C slave2, PM8350B slave3 and PMR735A slave4. These are source descriptions, not a live inventory. The same arbiter bases already used successfully by Spacewar buttons are `core=0x0c440000`, `cnfg=0x0c40a000`, `observer=0x0e600000`, version `0x50020000`. Common `hakios.pmic.c` enumerates channels, reads type/subtype at offsets4/5 under the shared SPMI lock, and reads the seconds counter only after finding a compatible RTC block. It does not change PMIC supplies. This is a useful next batched survey and can distinguish present blocks from generic DT candidates.

Battery has two existing paths with different prerequisites:

- XNU's `battery=1` path only consumes the loader's validated shared cache. However, **loader** `charger_start()` also calls `QCOM_CHARGER->initialize(1)` before polling. The existing command therefore is not a pure read-only firmware probe. First verify the matching Nothing UEFI protocol ABI/availability; a status-only probe should avoid initialization/TakeAction/limit changes.
- ADSP GLINK reads use owner32778, GET opcodes `0x30/0x32`; the common code's capacity/voltage/current/temperature/cycle/full-charge property offsets and unit conversions explicitly come from Redwood measurements. Validate those against Nothing's Android values before enabling the Darwin power-source UI. Do not infer battery temperature from the separately working SoC TSENS values.

Both ROM base DTBs and the supplied DTB reserve **40MiB** at `0x86700000` for ADSP (`size=0x02800000`). Common `hakios.adsp.c` reserves/maps/checks **64MiB** (`ADSP_MEMORY_SIZE=0x04000000`). The extra24MiB overlaps other reserved regions: CDSP begins at `0x88f00000`. Correct the per-device bounds and inspect matching firmware segments before any ADSP image load. `adsp_step=1` only surveys SMEM and does not access this image destination.

**Radio profile facts**

| Function | DT mapping | Voltage description |
|---|---|---|
| WLAN CX/MX; BT AON/DIG/RFACMN/RFA0p8 | PM7325 S7 (`smpb7`) | 824,000–1,120,000µV |
| WLAN XO / BT RFA1 | PM7325 S1 | 1,872,000–2,040,000µV |
| WLAN RFA / BT RFA2 | PM7325 S8 | 1,256,000–1,500,000µV |
| BT IO | PM7325 L19 | 1,800,000–2,000,000µV |
| BT IPA2p2 | PM8350C S1 | 2,200,000–2,210,000µV |
| BT ASD | PM8350C L11 | 2,800,000–3,544,000µV |

WPSS remote SMEM processor13, SMP2P items617/616 and IPCC client24 signal2 match common Wi-Fi code. ADSP uses remote processor2, items443/429 and IPCC client3 signal2. These are good candidates for one read-only shared-memory survey before starting either DSP. WLAN image reservation agrees with common bounds; the separate `wlan_fw@80c00000` 12MiB reservation is not the WPSS node's `memory-region` and must not be substituted for it.

The real vendor Bluetooth HAL contains alternative `*.tlv`/`*.bin` filenames and paths under `/vendor/bt_firmware/image` and `/vendor/firmware`. Selection depends on chip/ROM version and board data. No exact firmware selection or physical controller version has yet been established.

Firmware availability has three distinct states: current-ROM touch firmware, NXP speaker container and camera module files are extracted and hashed; GPU `a660_*` and IPA `yupik_ipa_fws.*` are present in the complete vendor inventory but intentionally not extracted for this task; ADSP/WPSS signed segments and BT controller blobs are absent from inspected firmware directories. Sensor calibration/persist data and camera supplier selection are additional runtime dependencies, not files established by a generic firmware filename.

**Camera profile facts**

The existing `tools/camsensor.py` successfully decoded all four actual module binaries. Full register/mode/power summaries are saved as `build/spacewar-port-inventory/com.qti.sensormodule.*-tables.txt`; no generated table has been installed in userland or sent to a phone.

| Role / ROM module | Module address (8-bit) → 7-bit | Read-only ID specification from module | DT wiring |
|---|---|---|---|
| Main `abra_qtech_imx766_wide` | `0x20 → 0x10` | ID `0x0766` at register `0x0016`, 2 address / 2 data bytes | sensor0: CCI1 master1, CSIPHY3, reset78, MCLK GPIO67 /19.2MHz; VANA GPIO79, VDIG108, VIO49, custom1 GPIO72 |
| Ultra-wide `abra_qtech_s5kjn1_uw` | `0x5a → 0x2d` | ID `0x38e1` at register `0x0000`, 2/2 bytes | sensor2: CCI1 master0, CSIPHY2, reset77, MCLK GPIO66 /19.2MHz; VANA68, VDIG50, VIO49 |
| Front `abra_qtech_imx471_front`, `abra_truly_imx471_front` | `0x34 → 0x1a` | ID `0x0471` at register `0x0016`, 2/2 bytes | sensor1: CCI0 master0, CSIPHY0, reset20, MCLK GPIO64 /19.2MHz; VDIG35, VIO49, custom2 GPIO43 |

The profiles contain custom power kinds10/11 and VAF kind4, which common `power_step()` does not handle. Some regulator selector values are0; current code interprets0 as off. A new Nothing-specific interpretation must reproduce the actual module sequence and DT wiring, including autofocus/OIS custom pins and shutdown order. The existing generic PM8008 nodes in the DT do not mean these sensors use Redwood's LDO profile.

The stereo speaker container is only3,745 bytes, SHA-256 `642ebff8fa8c27b711b3814d47abdf2ce045c21e6ea5a27c51428b63333c7037`; its strings name `TFA9873N1B`, speaker/receiver and dummy patch/profile data. That is configuration evidence, not a live TFA9873 chip-ID read. The current ROM's audio mixer also uses `TFA_CHIP_SELECTOR`, `TFA Profile` and `Powerdown_All`.

**Sensor selection remains open**

The ROM registry loader reads SoC ID, board platform/subtype/version and persisted registry files. SoC475/QRD candidates include ICM4x6xx at SSC bus2/`0x68`, LSM6DSO/DST at bus2/`0x6a`, MMC56x3x at bus2/`0x30`, STK3a6x at bus2/`0x48`, AK991x at bus1/`0x0c`, and additional ALS/SAR alternatives. Multiple candidates match the known selectors. This list is an offline search map only; actual fitted sensors and bus ownership need positive runtime evidence.

**Earlier broad survey proposal — superseded by the Redwood scope**

The following proposal preceded the user's narrower scope. It is preserved for the audit trail, not queued for execution. In particular, dedicated BT/torch and native-UFS work is deferred; see [the current eight-target plan](spacewar-redwood-scope.md).

1. Finish the separate touch identity/firmware/event test and preserve its log. Do not combine the first touch transaction with unrelated processor boots.
2. Prepare one RAM-root inventory kernel containing only the additional audited survey drivers. Candidate work in one bounded run: PMIC type/subtype/RTC (`pmic=1`), BT pin snapshots (`bt=1 bt_again=5`), WLAN SMEM (`wifi_step=1 wifi_pause=0`) and ADSP SMEM (`adsp_step=1 adsp_pause=0`). Keep DSP startup, radio power/reset changes, speaker/haptic/camera/torch output and native storage takeover off. Include existing SoC/thermal evidence and a timed return. These are proposed source-audited probes, not already built or phone-verified support.
3. Add a read-only torch type/status survey to that batch only after its SPMI address/ownership paths are reviewed against the full Nothing profile. PMIC inventory already gives the useful first evidence without enabling torch code.
4. UFS look-only mode is a separate loader-handshake preflight: require a valid UEFI controller/list offer and no requests in flight; avoid controller error registers that clear on read. Do not bundle a native ownership switch with another first-time hardware operation.
5. In parallel offline, obtain/pin the correct firmware and implement the Nothing ADSP bounds, battery mapping, NXP amplifier, PM8350B haptics and camera profiles. Hardware writes should be grouped only after each component's identity and rollback/stop path are established. Native USB must wait for an explicit loader handoff.

The current device fragment excludes these unported drivers. Enabling a boot argument in an existing eight-driver image cannot create the missing implementation. Preserve the working home-screen bundle while reviewing/building isolated future kernels.
