# Nothing Phone (1) Wi-Fi bring-up

The Phone (1) profile now selects its own WCN6750 board and regulatory data,
without falling back to the original Redwood files. WPSS startup, GLINK/QRTR
discovery and clean shutdown passed a Phone (1) RAM test. The subsequent full
driver run reached Settings and the user confirmed nearby networks appear.
Joining a network currently fails immediately; association, DHCP, DNS and data
traffic are not working claims. An earlier isolated run submitted no ASSOCIATE
request. The later cold-start diagnostic reaches ASSOCIATE for open networks,
but the station rejects them with EOPNOTSUPP because it previously implemented
only WPA2. The station now has a real open authentication/association path,
without RSN, key derivation or a four-way handshake; it refuses this path when
the access point advertises Privacy, RSN or WPA. Wire-format and protection
checks pass under ASan/UBSan; phone validation remains pending.

The password-protected attempt separately returns framework error -3903 before
ASSOCIATE. A synthetic wifid-compatible keychain set/copy/remove check passes.
The optional framework diagnostic reports only whitelisted numeric RSN/WPA
cipher and authentication selectors, to identify unsupported security without
reading a network name or password. No security downgrade or capability guess
has been applied.

A later return-to-loader test exposed a separate warm-start failure: the DSP
retained `slave-kernel=0x10e` (READY plus STOP_ACK), which startup incorrectly
accepted after another SMP2P entry changed. WLFW then never appeared and the
radio stayed down. Startup now requires a new state or restart generation and
READY with STOP_ACK/FATAL clear; a doorbell alone is insufficient. The actual C
policy passes host regression and sanitizer checks, including the recorded warm
sequence. A fresh cold boot with this policy starts the radio in 12 seconds and
shows nearby networks again. Warm recovery still needs phone verification.

The supplied DT and private Android runtime survey establish:

| Part | Phone (1) value |
|---|---|
| Radio | WCN6750 |
| Firmware processor | WPSS, TrustZone PAS 6 |
| WPSS reservation | `0x9ae00000`, 25 MiB |
| Shared memory | `0x80900000`, 2 MiB; remote processor 13 |
| SMP2P | Items 617/616; IPCC client 24, signal 2 |
| Radio DMA | SMMU stream `0x1c00`; IOVA pool `0xa0000000`, 256 MiB |
| Android-selected files | `qca6750/bdwlan.elf`, `qca6750/regdb.bin` |

The signed `wpss.mdt` and load segments were recovered locally from the phone's
verified `modem_a` backup, rather than from another device. Their destination
ranges fit the DT reservation. Matching filenames alone do not prove signed
images from a different phone are interchangeable. Firmware remains private.

## Prepare the private inputs

`tools/spacewar-wifi-firmware.py` checks the WPSS signed-header layout, segment
sizes, reserved-memory bounds and board ELF before producing a new output
directory. Give it the BDF actually selected by the phone's Android logs:

```sh
python3 tools/spacewar-wifi-firmware.py \
  --image-dir /private/extracted/image \
  --board-file bdwlan.elf \
  --mac-file /private/station-address \
  --output /private/fw-wifi-ready
```

The private station-address file can contain one colon-separated address or
Android's `Intf0MacAddress` record. The generated `mac` file has mode 0600. A
stable software-assigned, locally administered unicast address can be supplied
explicitly when a factory address is unavailable; it is not a recovered factory
identity. Neither an address nor a network password belongs in boot arguments
or published logs. `--mac-file` may be omitted for steps 1–14 diagnostics, which
do not start the station.

Build the actual Wi-Fi object and its QMI descriptions, then rebuild the kernel
with `CONFIG_HAKIOS_WIFI=y` and `CONFIG_HAKIOS_QMI=y`:

```sh
bash tools/mk-ath11k.sh
bash tools/mk-wlfw.sh
```

The object uses pinned Linux 7.0.14 and FreeBSD 15.1 inputs with checked archive
hashes. The local build completed with 18 actual ath11k source files and no
generated function stubs. `FW_WIFI_DIR=/private/fw-wifi-ready` tells
`userland/build.sh` where to copy the private WPSS and canonical
`wifi-board.elf`, `wifi-regdb.bin`, and `wifi-mac` files.

## Phone test stages

The first phone test passed `wifi_step=1 wifi_pause=0`: SMEM version 12, the
APPS/WPSS partition and its GLINK descriptor/TX queue matched the expected layout.
The subsequent step-14 test authenticated the signed image, reached DSP ready,
opened IPCRTR and discovered WLFW service `0x45`, version 1, instance 3, at node
13/port 10. The DSP acknowledged stop in 12.600 ms and TrustZone shut it down.
No radio SMMU context or mission mode was used in that test.

For reproducing those stages, start with `wifi_step=1 wifi_pause=0`. After that,
`wifi_step=14 wifi_pause=0 wifi_new=17 wifi_watch=0` loads/authenticates WPSS and
tests GLINK/QRTR without sending board data or starting the radio data path.
`wifi_step=15 wifi_lx=1` exercises the driver's firmware exchange without radio
MMIO. Full `wifi=1` starts the actual driver and station; it first requires the
Phone (1) board ELF, regulatory data and private address, before DSP/SMMU startup.

The radio uses the regulatory domain supplied by this phone's firmware and
board/regulatory files. Ath11k falls back to the world domain `00` if firmware
does not provide one. The shim applies the domain's disabled-channel, passive,
DFS and transmit-power limits; it does not assume a US or Redwood country.
Record the actual domain from the firmware log before claiming full RF operation.

`hakios_debug=0` keeps the display quiet. `hakios_debug=1 wifi_loud=0` enables
bounded Apple80211 request/event diagnostics directly in the live loader stream
without flooding it with the driver's startup detail. The optional
`userland/src/inject_wifi.c` diagnostic library belongs in `wifid` only; it
reports association entry/return and typed numeric security metadata only.
Do not replace the verified installed boot image until RAM tests
and a clean Wi-Fi shutdown have passed. The host checks are
`python3 tools/spacewar-wifi-test.py`; they exercise the actual C policy under
ASan/UBSan and reject wrong ELF bounds, missing fragments, invalid addresses,
foreign aliases and output collisions.
