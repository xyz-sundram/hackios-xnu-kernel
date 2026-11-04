# Spacewar touch port: source evidence and staged identity probe

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The new matched `xnu-spacewar-touch-live-debug.img` and
`spacewar-userland-touch-live-payload.bin` add coordinated touch access to the
CPU7/SMP7/buttons-on live-log configuration. The loader grants exclusive
hardware transactions and serves USB between them; firmware and decoder
bytes are unchanged. This integration is built and host-tested, with its
first phone run pending. See [instructions and validation](spacewar-touch-live.md).
The CPU0-only restrictions described in the historical probes below remain
the default when the new live profile is not explicitly selected.

Touch hardware has passed on the phone: ROM56:62, full M146 firmware with
ECC22ed/22ed, runtime56:62/version27 and real single-contact presses, swipes
and releases. The45-second window recorded600 frames,346 valid,279 input
events, zero rejected reports and138 IRQ edges, then cleaned up and returned.
Multitouch, full edge calibration and graphical consumption remain untested.
The exact build/source/evidence is frozen under `build/spacewar-touch-working-baseline/`.
The first graphical test reached the home screen after about 2.5 minutes but
touch did not respond. Its 60-second touch window likely ended before the UI
appeared; that phone log could not be saved after the device left fastboot.
The extended `spacewar-userland-touch-window-payload.bin` run reached iOS,
but a tap became a long press/edit mode, touch stopped, and the phone remained
in iOS after its return deadline, according to the user. No final log was
recovered. Buttons were explicitly disabled; wallpaper was also missing.
The new continuous payload permits `touch_seconds=0` and disables the kernel
timer, with five-second polling summaries. It retains CPU0 alone and the
existing APFS/identity images. Its phone test is pending; the long-press,
wallpaper and failed-return causes remain unresolved.
See [current result and continuous-mode instructions](spacewar-userland-touch.md).
The probe runs only when `touchscreen=1`. Stages1–3 have passed on the phone.
The build selects the
Spacewar profile from the build configuration and cannot fall through to
Redwood's supplies, pins or firmware when the profile argument is omitted.

The phone reported GPIO105 reset and GPIO81 interrupt configuration `0x1` and
I/O `0`, GCC votes `0x8000`, gates `0/0x8000000A/0x80004220`, and distinct VRM
resources `ldoc8=0x42500` / `ldoc3=0x40E00`. It stopped without pin, clock or
regulator writes, ran `hello` as PID 1 uid 0 and returned after 30 seconds.
Evidence: `logs/spacewar-touch-step1-success.log`
and SHA-256 sidecar. These readings establish access to existing state, not
controller identity or touch reporting. The next probe reads RSC version,
capacity and ACTIVE-slot state without issuing power votes; that second
inspection has now also passed. RSC ID was `0x20300` (2.3), with eight TCS and
16 commands each. ACTIVE0 retained firmware clock commands; ACTIVE1 had idle
status `1`, no enabled commands, wait `0`, control `0x10000` and zero first-three
command records. Shared IRQ enable was `1`, status `0`. Evidence:
`logs/spacewar-touch-step2-success.log`
and SHA-256 sidecar. Slot 0 must remain intact. The proposed power stage uses
slot 1 only after a fresh checked snapshot; this read-only result does not by
itself claim ownership. Stage 3 subsequently passed on physical CPU0 alone:
all six L8/L3 voltage/mode/enable requests were acknowledged with `0x10101`,
slot 0 remained intact, reset105 stayed low, `hello` ran as PID 1 and the
30-second timer returned cleanly. Evidence:
`logs/spacewar-touch-step3-success.log`
and SHA-256 sidecar. An earlier attempt contained no kernel payload and did
not enter the power code; that separate not-started log is preserved.

## Verified report-polling build

The recovery phone run completed every firmware step successfully, then rejected its first FF report while IRQ81 was high. Upload took about52.65seconds. Phone evidence (`logs/spacewar-touch-recovery-fw-pass.log`).

`spacewar-touch-pollfix-payload.bin` uses a120-second return, hello_wait150 and45-second report window. It follows low IRQ with a100ms fallback, tolerates the exact vendor FF-init marker and idle no-record packets without inventing touches, and records runtime/mode/power/version, IRQ transitions and full initial packets. A50ms settle and bounded runtime wait cover transient initialization. Real bad frames/CRC still have an8-failure limit; EF/lost runtime stops without reupload. Firmware/ownership/framing/decoder code is unchanged.

All32 production-code host cases pass under ASAN/UBSAN. The kernel/payload and Windows destination are verified. Manifest (`build/spacewar-touch-pollfix/manifest.json`), diagnosis (`build/spacewar-touch-pollfix/diagnosis.md`). Begin gestures about50seconds after run and continue for60seconds. Physical DOWN/MOVE/UP subsequently passed. Evidence: `logs/spacewar-touch-input-success.log`; baseline: working manifest (`build/spacewar-touch-working-baseline/manifest.json`).

## Previous recovery test (firmware passed)

The GPI phone run passed power, clocks, SE/GPI idle guards and GPIO SPI. CRC-validated A3/9F reads returned `EF:EF`; the previous nonblank-ID check stopped before querying ROM. Evidence: `logs/spacewar-touch-gpi-ef-stop.log`.

The current `spacewar-touch-recovery-payload.bin` queries ROM read90 first on exact `EF:EF`. Only CRC-valid `56:62` can reach the uploader, which rechecks ROM before RAM writes. Mixed EF and unknown nonblank runtime/ROM IDs remain rejected. Existing 00/FF handling and reset/55-AA fallback remain available. This changes neither SPI ownership/framing nor firmware bytes, RAM ECC/start sequence or FT12 decoding. EF is a recovery candidate, not a chip identity.

The root agent worked alone per the user's request. Twenty actual-production GPIO-wire ASAN/UBSAN cases and exhaustive runtime-pair policy checks pass; compile/link and payload inspection pass. The file was copied with `./copy` and its Windows destination checksum verified. Its 75-second return and 25-second touch window are unchanged. Physical ROM identity, upload and input remain pending. Manifest (`build/spacewar-touch-recovery/manifest.json`), diagnosis (`build/spacewar-touch-recovery/diagnosis.md`).

## Current combined phone probe

The initial combined phone log is preserved as
`logs/spacewar-touch-combined-clock-stop.log`.
It passed all six power acknowledgements, then rejected the slave-AHB HALT
state before reading GENI or releasing reset. Nothing's clock implementation
explicitly skips HALT for declared hardware-gated branches; this phone's
slave-AHB value `0x8000000A` has that HWCG bit set. The corrected readiness
predicate applies that exception only to master/slave AHB, retaining strict
core/SE5 checks. The old log did not capture after-vote SE5 state, so it cannot
establish a second clock failure. The corrected payload logs all post-vote
GCC/RCG/DFS/five-gate values before SE access and on every uncertain stop.
GPIO/FW/report code and all five private helpers are byte-identical to the
reviewed combined baseline. See the diagnosis and primary source (`build/spacewar-touch-clockfix/diagnosis.md`)
and corrected manifest (`build/spacewar-touch-clockfix/manifest.json`).

That clockfix run subsequently passed all clocks and read three stable SE
snapshots: engines/commands/controls, DMA debug/lengths/FIFOs and pending IRQs
were all zero. DMA mode1, GSI routesF, FIFO-disable1, master clock11 and retained
IRQ-enable masks describe the vendor's GSI mode configuration; they are
preserved. The old zero-mode guard stopped before GPIO/controller commands.
Evidence: `logs/spacewar-touch-clockfix-idle-stop.log`.

The current image additionally checks the DTB's static touch GPII0 window at
A10000/4000. Channel states unallocated0, allocated1 and stopped3 are admitted;
allocated/stopped transfer rings must have the reviewed touch owner/layout,
valid aligned bounds and no queued TREs. Running2, stopping/error/unknown states
and pending IRQs remain refused. Event-ring WP returns free credits, so it is
not treated as a transfer-queue pointer. Stable64-bit reads, an initial1ms
interval and all44 context words are checked before/after every GPIO frame.
This combines nonrunning external queues with the existing CPU0-only run that
keeps UEFI paused; repeated SE snapshots alone are not the ownership basis.
All four bus pin CFG/IO values now print before early rejection. Only the exact
prior owned mode0 GPIO pattern may reuse output pins under the full fresh
guards. See the GPI diagnosis/source evidence (`build/spacewar-touch-gpi/diagnosis.md`)
and current manifest (`build/spacewar-touch-gpi/manifest.json`).

The user requested fewer manual test cycles. `touch_steps=6` automatically runs
the proven CPU0-only power stage, enables the five owned GCC access-clock
votes, inspects the idle GENI engine, claims the DTB's dedicated GPIO52–55 and
uses mode0 GPIO SPI at no more than500kHz. It preserves all SE configuration,
including harmless retained IRQ enables, DMA/GSI mode and master-clock state. Busy engines,
actual DMA/FIFO/pending IRQ activity, unowned/queued external GPI work, unexplained pin owners and routed asynchronous
events stop it before a GPIO bus claim. Every frame checks fresh CPU/owned-pin
state and compares all30 SE and44 GPI snapshot words before and after; no SE, GPI, RCG,
IRQ81 or GPIO107 write is issued by the new transport.

Reset release leads to CRC-protected runtime/ROM identification. Only exact
`56:62` permits progress. Already-running firmware skips upload; otherwise the
probe uses the exact119,284-byte M146 V27 extracted from the supplied AOSPA ROM.
It validates SHA256, header length/version complements and host ECC, writes
volatile RAM in32-byte chunks, requires device ECC `22ED` to match, sends one
`08` app-start command and verifies runtime `56:62` again. Mutating commands
are single-attempt: uncertain acknowledgements poison the probe rather than
retrying or resuming a partial upload. No NVM/flash commands exist in this path.

After `TOUCH READY`, the payload polls for25 seconds using the reviewed atomic
FT12 decoder:62 payload bytes/71 wire bytes, ten contacts,12-bit coordinates,
inclusive1080×2400 bounds and normalized0–65535 input records. DOWN/UP events
are logged, MOVE logs are limited, and every valid event is posted. Unsupported
report types and bad CRC/records do not partially change contacts. The bounded
probe releases owned contacts and holds reset low at stop. Graphical userland
consumption remains a later phone test; this kernel deliberately requires CPU0
alone and cannot share the current CPU7/SMP7 GUI profile.

| Current artifact | Bytes | SHA256 |
|---|---:|---|
| `hakios-kernel-spacewar-touch-gpi` |15122280 |`1e813baa35e8de6e9aabc3e141bb7fcf6d1ed849f0bdbeaffe41be49f56baff2` |
| `spacewar-touch-gpi-payload.bin` |15139264 |`9d698ea36bc9194b48065c7e3627030e7be4399129cbb77d201d551a8a127093` |

The payload was copied with `./copy` and its Windows checksum verified. It
contains the unchanged raw `hello`, `hello_wait=120`, `loader_return_after=75`,
`touch_seconds=25`, `buttons=0` and the tested SOCINFO/THERMAL arguments; no CPU,
frequency or WFI overrides. The full268-character command line and acceptance
checks are in the current manifest (`build/spacewar-touch-gpi/manifest.json`).
Earlier buttons/stage1/stage2/stage3 aliases, the main kernel, prebuilts and
userland images are preserved. The original combined kernel/payload remain
immutable at SHA256 `84ab9466cf8a78dd054391a6b7a724ccfec3f500323030ab0be0eda1d2f426e5`
and `e499b3c855abb4ced66973500c7db9bd9af4591218dab987e636f893f146c7c5`.
The separate clockfix kernel/payload also remain unchanged at SHA256
`c67decce49e204f38a75160adfff9041eb39b3f46555cc0e0a6cbfcaf19845a5`
and `74d530a6a4383f7f4ba7f21fba5fb6df046f9f2c0a128f1b809de291b75f634b`.

The actual GPIO bitstream/FTS framing/full RAM-ECC-runtime/input path passed an
ASan/UBSan host model, including uncertain writes, ECC mismatch, unknown chip,
busy/routed IRQ/CPU/pin ownership and state-change failures. Independent peer
review also passed the firmware helper's24 cases, the decoder's1699 checks and
the generated four-device initialization/stale-header guards. Kernel compile
and link passed; only the existing accepted kext symbol-set checks failed.
These are host results, not evidence of working live touch. No QEMU was run.
Current build: `logs/spacewar-kernel-touch-gpi-build.log`;111 independent focused
GPI/SE guard cases: `logs/spacewar-touch-gpi-guard-test.log`; actual ownership/pin-reuse
integration: `logs/spacewar-touch-gpi-ownership-test.log`; original wire tests:
`logs/spacewar-touch-combined-gpio-pipeline-test.log`; checksums:
`logs/spacewar-touch-gpi.sha256`.

## Reproducible sources

The official [Nothing Phone (1) kernel repository](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325)
was checked at two commits:

| Branch | Commit | Local reference |
|---|---|---|
| `sm7325/s` | `539538747842faa8e22145ac0b557b1c905f8979` | `build/reference-spacewar-touch/` |
| `sm7325/v/mr` | `1d557de4d904a76c0b679c746cc77cf9ae89b6c6` | `build/reference-spacewar-touch/android15/` |

The downloaded files are reference material, with paths and SHA-256 values in
`build/reference-spacewar-touch/sources.json` and
`build/reference-spacewar-touch/SHA256SUMS`. Only the relevant
driver files and one official firmware source array were fetched. No firmware
was installed, uploaded or flashed.

`spacewar.dtb` SHA-256:
`70ce7880155f48d55f453ec530656f0c843ce3136f5d9743a404801fd359999b`.
Reproduce the wiring extraction without hardware access:

```sh
python3 build/reference-spacewar-touch/inspect-dtb.py
```

## Controller identity requires a phone read

The DTB contains `focaltech,ic-type=0x3658D488`, which is named `_FT3658U` in
Nothing's headers. However, both checked branches explicitly select `_FT3680`
and map the controller's runtime/ROM IDs to `0x56:0x62`. Their `fts_parse_dt()`
does not read `focaltech,ic-type`. Therefore that property alone does not prove
which chip is fitted. The first SPI identity test must establish the actual
response before choosing firmware.
Sources: [configuration](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_config.h),
[ID mapping](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_common.h),
[DT parser and identity handling](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.c).

## Confirmed wiring and implementation changes

The supplied DTB is authoritative for this phone's wiring:

| Function | DTB fact | Existing common implementation |
|---|---|---|
| SPI | GENI SE13 at `0xA94000`, max 6 MHz, mode 0 in official driver | Same engine; conservative 1 MHz is within the DTB limit |
| Bus pins | GPIO52–55, `qup15`, drive 6 mA, no bias | Matches |
| Reset / interrupt | GPIO105 / GPIO81, active pull-ups and 8 mA | Matches |
| Analog supply | PM8350C L3, resource `ldoc3`, permitted 2.8–3.54 V | Uses GPIO107 instead |
| I/O supply | PM8350C L8, resource `ldoc8`, permitted 1.62–2.0 V | Votes `ldoc2` |
| Coordinates | 1080 × 2400, up to ten contacts | Decodes a 10800 × 24000 high-resolution variant |
| Chip selection | DTB selects `focaltech,fts_ts` on this bus | Reconfigures GPIO68/96 and selects a chip from Redwood straps |

Nothing's power sequence holds reset low, waits 1 ms, enables I/O at 1.8 V,
waits 1 ms, then enables analog at 3.3 V. Both values fit the supplied DTB's
ranges; its initial regulator voltages are lower than the driver's requested
operating voltages. Reset pulses are low for 1 ms; the initial reset requests
200 ms to settle. No analog-enable GPIO is used by that driver.
Source: [regulator and reset implementation](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.c).

The SPI framing matches the common implementation: register byte, control
`0xA0` for CRC-protected reads / `0x00` for writes, big-endian length, three
dummy bytes and little-endian CRC. This establishes a reusable transport, not
successful access through this phone's firmware.
Source: [official SPI transport](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_spi.c).

The official default touch report is 62 bytes at register `0x01`, with ten
six-byte slots after a two-byte header. X and Y each use twelve bits from their
high nibble/low byte; pressure is a separate byte. The common code currently
incorporates pressure bits into fifteen-bit coordinates. The Spacewar full
driver must use the twelve-bit decoder, 1080 × 2400 scaling, event-type checks,
contact bounds and explicit releases for disappearing contacts. The current
review patch never reaches either report decoder.
Sources: [report constants](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.h),
[report parser](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_core.c).

## Firmware is different

| Image | Bytes | SHA-256 |
|---|---:|---|
| Official `FT3680_WXN_M146_V27_D01_20220706_app.i`, decoded bytes | 119284 | `04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726` |
| Common embedded `focaltech_ts_fw_m20.bin` | 116448 | `dcf91d0de00baa35954dd5b99b121187af112d7b975970e2b0206e42814b28c6` |

The official source array is selected by Nothing's checked configuration. Its
Git blob is `c7ccba6d002c8e318e6b979e84cdec8814ec4a9c`. The user's supplied
AOSPA20240903 archive subsequently established the exact same full firmware in
the boot kernel at Image+`0x2d88838`, with matching compiled module/type/upgrade
tables. See [ROM firmware provenance](spacewar-rom-firmware.md). The combined
probe uses this extracted ROM-matched image, gated by live controller IDs and
device ECC; no phone upload has yet been confirmed.

The combined upload path validates the fitted controller and the ROM's selected
image, length/complement fields and host/controller ECC before application start.
The FT3680
setting uses a length coefficient of four; this source image's validated code
length is `29821 * 4 = 119284` bytes and has no constant-data section. The
existing whole-blob PRAM loop lacks the official ECC check. Merely replacing
the embedded array is incomplete.
Sources: [selected firmware](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_config.h),
[upload validation](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/input/touchscreen/focaltech_touch/focaltech_flash.c).

## Integrated read-only probe and initial review patch

`build/reference-spacewar-touch/spacewar-touch-identity-review.patch`
records the initial candidate against the pre-integration common driver. That candidate is
`build/reference-spacewar-touch/hakios.goodix.spacewar-probe.c`,
with its original base/candidate hashes in
`build/reference-spacewar-touch/review-patch-status.json`.
It passed `git apply --check` before integration. The integrated source adds
the device guard and separates the tested inspection stages from guarded power votes; use the built artifact
below rather than applying the historical patch to the updated source.

`tools/hakios-config.py kernel` now derives `HAKIOS_DEVICE_SPACEWAR` from typed
`CONFIG_DEVICE` in the generated `hakios.drivers.h`. The Spacewar build forces
the Spacewar profile, rejects `touch_spacewar=0` or any other override, defaults
to `touch_steps=1`. The initial kernel rejected every other step; the new RSC
inspection kernel permitted only read-only steps 1 and 2. The separate power
kernel adds guarded step3 for Spacewar on CPU0 alone. The current combined
kernel additionally permits access-clock inspection4, guarded GPIO identity5
and verified RAM-firmware/report flow6; stage7 and above are rejected, and
other device builds cannot select stages3–6.
A stale header missing the
device guard refuses to compile. Other device builds retain their existing
default path. All five mappings are checked and thread-start failures are
reported.

The first two stages have passed; the separate power build adds step 3:

| Stage | Access | Acceptance before advancing |
|---|---|---|
| 1, phone verified | Map/check TLMM, GCC, SE, RSC and command DB; read GPIO105/81, GCC gates and `ldoc8`/`ldoc3` addresses | Passed, including `hello` and timed return |
| 2, phone verified | Read RSC version/configuration, shared IRQ state, ACTIVE slots 0–1 and command snapshots | Passed: v2.3, 8 TCS / 16 commands, retained ACTIVE0 and idle ACTIVE1, clean return |
| 3, phone verified | CPU0-only, checked ACTIVE1 supply votes with reset105 held low | Six issued/completed command acknowledgments, retained slot0 unchanged, reset low and clean timed return passed |
| 4–6, combined build; phone pending | Access clocks/SE inspection, GPIO SPI/controller ID, ROM-matched RAM/ECC/application and FT12 polling | Automatic progress only after each fresh guard; physical DOWN/MOVE/UP and clean return still required |

The historical stage1–3 payloads do not configure GPIO107 or the Redwood strap
pins, select Goodix, transfer SPI, upload firmware, start an application or poll
reports. The combined flow above adds its own guarded GPIO transport and
ROM-matched path. Stage1 avoids reading serial-engine registers before their clocks
are enabled. Mapping and command-DB table bounds are checked. Neither SE nor
RSC registers are read by stage 1. The RSC inspection kernel removes the old
Spacewar mutating stage implementation entirely. The separate power kernel
adds a dedicated checked ACTIVE1 implementation rather than calling the old
`rpmh_write()` helper. Legacy RPMh code shared with
the other device path still assumes a fixed register layout and accepts
the completion IRQ without checking every command's status. The new power
stage accepts only the observed v2.3 layout and checks each command's completion.

Step 2 reads the RSC version before layout-dependent registers, supports only
reviewed major versions 2 and 3, validates the AP's TCS/command capacities and
bounds every aligned register read within the 64-KiB window. It prints control,
raw status, command-enable and wait state for both DTB ACTIVE slots, plus the
first three and any enabled command's message/address/data/status. It never
clears IRQs, disables slots or sends commands. Raw snapshots do not establish
ownership of a slot or permission to issue a power vote.

The integrated source passed independent review, compile/link, boot-argument
catalog checks and the existing boot-argument tests. A host test compiles the
actual touch initialization function with generated headers for Spacewar,
Redwood, QEMU and no device; it verifies forced safe defaults, refusal of
profile overrides and unsupported stages, disabled-touch behavior and stale-header failure.
An additional host test executes the actual RSC inspection with instrumented
fake MMIO: major-2/major-3 offsets, invalid-version rejection before later
reads, capacity checks, enabled-command selection, busy-state preservation and
alignment/window bounds pass. These tests establish code behavior, not phone
register accessibility or ownership.
The full reference configuration test was skipped because the Redwood device
tree is absent. No QEMU run was performed.

The Spacewar fragment compiles GOODIX but keeps `CONFIG_TOUCHSCREEN` off, so
normal payloads do not start the probe. The dedicated payload uses explicit
`buttons=0` because `touchscreen=1` would otherwise also enable the buttons
driver. The historical stage1–3 command lines are250 characters, include the
proven SOCINFO/THERMAL arguments and use raw `hello` with a30-second return.
The current combined payload's longer budget is recorded above.

| Built artifact | Bytes | SHA-256 |
|---|---:|---|
| `hakios-kernel-spacewar-touch-probes` | 14989688 | `b3cef03db7c405c72ba063381fa67444b12e7a7132478436258ddbba8519a94e` |
| `spacewar-touch-step1-payload.bin` | 15006656 | `5d9a3114da6062755dc537bb26350ca68f5023d65ddca9b98c5002bf5db4a077` |

Exact payload bounds, ARM64 load commands, embedded kernel/hello, version,
source synchronization and generated device header were checked. The tested
buttons kernel alias remains unchanged at SHA-256
`afade5c61dd66c0d4dba0729ba439c0d8ffbe8ca4d0a36ab8e184fe2692ff317`.
Build log: `logs/spacewar-kernel-touch-probes-build.log`; guard checks:
`logs/spacewar-touch-guard-test.log`; inspection:
`logs/spacewar-touch-payload-inspect.log`; artifact manifest:
`build/spacewar-touch/manifest.json`.

The next artifacts are separate and preserve those tested stage-1 files:

| RSC inspection artifact | Bytes | SHA-256 |
|---|---:|---|
| `hakios-kernel-spacewar-touch-rsc-probes` | 14989504 | `57b24892576ad47c2e83699c4b7bbc1c5498405805e92e07f48e3fef28e0fc67` |
| `spacewar-touch-step2-payload.bin` | 15006464 | `060a293e6c6b266bb6e9aa1463e9e2168acfe01e903e29dd6f6e5efeeef6b9ce` |

The new payload changes the kernel and only `touch_steps=1` to `touch_steps=2`
in the previous command line. Its `hello` and version bytes are identical;
`buttons=0`, SOCINFO/THERMAL and the 30-second budget remain explicit. Source,
generated device guard, ARM64 load commands, payload bounds and exact embedded
bytes passed inspection after independent peer review and a successful link.
Build: `logs/spacewar-kernel-touch-rsc-probes-build.log`; inspection:
`logs/spacewar-touch-rsc-payload-inspect.log`; host tests:
`logs/spacewar-touch-rsc-guard-test.log` and
`logs/spacewar-touch-rsc-fake-mmio-test.log`; checksums:
`logs/spacewar-touch-rsc.sha256`; manifest:
`build/spacewar-touch-rsc/manifest.json`.

## CPU0-only power stage

The first mutating stage admits only the tested Spacewar build on physical
CPU0 with one configured/online processor, the loader's `cpus=1`, no CPU mask
or frequency override and `buttons=0`. It validates the observed RSC ID
`0x20300`, configuration `0x800C8104`, resource addresses and fresh slot state
while holding `hakios_rsc_lock`, before changing any pins.

ACTIVE0 must remain untouched. Its enabled command addresses are checked for
conflicts with all six planned writes before GPIO105 changes and before every
transaction; its status must be idle. ACTIVE1 requires status `1`, no trigger,
no enabled commands or wait bits, no enabled/pending IRQ bit 1 and empty CMD0
RW fields. An idle control register containing ENABLE alone is accepted.

The probe drives reset105 low, verifies it, waits 1 ms, then sends three
separate waited votes for L8: 1800 mV, HPM mode 7, enable 1. After another 1 ms
it sends the equivalent L3 votes at 3300 mV. Every command uses only ACTIVE1,
bounded control readbacks, an explicit I/O barrier before TRIGGER, a fresh
completion IRQ and both ISSUED/COMPLETED status bits. It preserves all slot0
RW command/control state and unrelated IRQ bits. Successful transactions
restore the owned slot1 RW fields and IRQ-enable bit; RO status registers are
never written. Any uncertainty poisons the probe and stops further requests,
without automatic retry, abort cleanup or rail rollback. Reset remains low.

No step-3 code sets GCC clocks, configures GENI/SPI, releases reset, reads a chip
ID, uploads firmware or polls touches. An acknowledgment proves acceptance of
a vote, not a measured rail voltage or working controller.

| Power-only artifact | Bytes | SHA-256 |
|---|---:|---|
| `hakios-kernel-spacewar-touch-power-probes` | 15006200 | `5faf10d03b534cac601e33b149adba2866de64df716d4710f98189d9919b424c` |
| `spacewar-touch-step3-payload.bin` | 15023168 | `06e84af0cd14ce024649b6ba891abc5f559f35259984316bf1ec5da30f04fde7` |

Peer review, the actual-source host fault model, initialization guards,
compile/link, boot-argument tests, ARM64 load-command/payload checks and source
synchronization passed. The object contains the required ARM64 `DSB SY`.
Fault cases cover incompatible CPU/board/resource/version/slot state, conflicting
retained commands, failed readbacks, stale/sticky IRQs, missing completion bits,
timeouts and poisoned retries. No QEMU run was performed.

Manifest: `build/spacewar-touch-power/manifest.json`;
build: `logs/spacewar-kernel-touch-power-probes-build.log`; tests:
`logs/spacewar-touch-power-fault-test.log` and
`logs/spacewar-touch-power-guard-test.log`; inspection:
`logs/spacewar-touch-power-payload-inspect.log`; checksums:
`logs/spacewar-touch-power.sha256`. All earlier kernel aliases and payloads
remain byte-identical.

Rebuild a power-only payload from the current source on the host (earlier hashes identify their original
builds and need not match a later build):

```sh
make spacewar_defconfig
KERNEL_LOG=logs/spacewar-kernel-touch-power-probes-build.log tools/make-kernel.sh -j8
cp hakios-kernel hakios-kernel-spacewar-touch-power-probes
python3 xnuloader/mkpayload.py -o spacewar-touch-step3-payload.bin \
  kernel=hakios-kernel-spacewar-touch-power-probes ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 buttons=0 touchscreen=1 touch_spacewar=1 touch_steps=3'
```

Phone publication/copying stays coordinated with the sequential CPU/buttons
tests. The stage-1 payload was copied with `./copy`, destination-verified, and
passed on the phone. The new step-2 payload was also copied with `./copy` and
its Windows checksum verified; its read-only RSC phone test passed with `hello`
and a clean 30-second return.
The separate power payload passed its controlled CPU0 phone run. Its successful
acknowledgements establish request acceptance; SPI, controller identity and touch
events remain pending. The combined image documented above now batches clock
access, ownership checks, GPIO SPI, live identity, ROM-matched RAM firmware/ECC
and report decoding in one run. It stops at unsupported state. No controller
firmware upload or touch event has yet been confirmed on the phone.

Useful later Android evidence is the ROM build fingerprint, `/proc/bus/input/devices`,
the bound SPI driver, FocalTech boot/runtime IDs from Android's kernel log,
and the firmware version/image actually selected there. Collect read-only
information after the current buttons tests; no sysfs reset or upgrade write
is required to establish those facts.
