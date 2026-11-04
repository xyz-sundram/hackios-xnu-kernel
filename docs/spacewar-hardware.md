# Spacewar kernel hardware bring-up

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The minimal kernel baseline has passed on the phone: UEFI reaches the resident
loader, USB upload/download works, XNU mounts the RAM root, `hello` runs as
process 1 with uid 0, and the 30-second timer returns to the loader. Evidence:
`logs/spacewar-smoke-success.log`.
The firmware fixes and reproduction commands are in
[spacewar-bringup.md](spacewar-bringup.md).

The first hardware probe has now also passed. The phone's
`logs/spacewar-socinfo-success.log` records SMEM
item 137 in the global partition, format `0xf`, SoC **475**, revision **1.0**,
board platform **11**, and the corresponding `kern.hakios.soc_*` values read by
`hello`. Process 1 runs as uid 0 and the 30-second timer returns to the loader.
Temperature monitoring was deliberately absent from that command line, so its
empty sensor list and `-273000` uninitialized-temperature value are expected.

The temperature probe subsequently passed as well. The full
`logs/spacewar-thermal-success.log` records
five live summaries with **27 of 27 sensors valid**, changing readings from
**39.5 to 43.0 C**, and the corresponding thermal sysctls visible to `hello`.
The kernel ran process 1 as uid 0 and returned on its 30-second budget without a
panic or early exception. The 105 C stop was enabled but never reached; its
threshold/shutdown path is still untested.

Physical button polling has also passed through all five bring-up stages.
CPU7 handoff, two-core XNU and all seven kernel-owned cores have now passed as
well, as recorded in [spacewar-cpu.md](spacewar-cpu.md). Touch inspection and
power stages1–3 have passed. HWCG-aware clocks and SE access subsequently passed;
the old DMA/GSI zero-mode guard then stopped before controller access. The
retained-mode/read-only GPI ownership correction is built and copied, awaiting
the combined GPIO-SPI/identity/RAM-ECC/FT12 phone run described in
[spacewar-touch.md](spacewar-touch.md). The button milestones and reproduction
procedure remain below.

## Status and order

| Part | Evidence | Runtime status on Spacewar |
|---|---|---|
| XNU, RAM root, framebuffer console, user space, timed return | Successful phone log and visible greeting | Verified |
| SoC identity (`SOCINFO`) | Phone parsed SMEM item 137 and user space read SoC 475 / revision 1.0 | Verified |
| Temperature sensors (`THERMAL`) | Phone polled all 27 sensors and user space read changing values | Live readings verified; 105 C stop remains untested |
| Buttons (`BUTTONS`) | All five stages passed; two physical press/release cycles for each key and zero bus failures in the polling summary | Polling verified; graphical userland consumption pending |
| Secondary CPUs / SMP | Phone registered physical CPUs7–1, reached seven running after the delayed callback, then returned cleanly | Basic seven-core SMP verified; sustained load and frequency control untested |
| Touch (`GOODIX`, including its FocalTech branch) | Power, HWCG clocks and SE snapshots passed; old guard stopped on retained DMA/GSI mode. Read-only external GPI ownership correction reviewed, built and copied | GPI/GPIO-SPI/controller/RAM-ECC/physical-event phone test pending |

The supplied [SM7325 reference repository](https://github.com/oosexgang/hakios-sm7325)
is useful for the SoC layer. Its pinned
[`soc/configs/sc7280.config`](https://github.com/oosexgang/hakios-sm7325/blob/04fbfff933f52faf203a55aca9dd5680092e6ec5/soc/configs/sc7280.config)
enables eight CPUs, boot CPU 7, CPU frequency control, SoC identity, thermal
monitoring and the PMIC driver. Those settings describe the reference port;
Spacewar should establish each capability incrementally.

## First probes: identity and temperature

The Spacewar fragment now builds `HAKIOS_SOCINFO` and `HAKIOS_THERMAL` into the
probe kernel. Their runtime entry points remain gated by `socinfo=1` and `thermal=1`.
The initial probes need no CPU clock, PMIC configuration or storage-write
arguments.

| Source in the current-ROM `spacewar.dtb` | Driver expectation |
|---|---|
| `/reserved-memory/smem@80900000`: base `0x80900000`, size `0x200000` | `SOCINFO` defaults; reads firmware SMEM item 137 |
| `/soc/tsens@c222000`: reading bank `0x0C263000` | `thermal_tm0`; 15 sensor IDs |
| `/soc/tsens@c223000`: reading bank `0x0C265000` | `thermal_tm1`; 12 sensor IDs |

`tools/dt-thermal.py spacewar.dtb --c` reproduces all 27 existing thermal table
rows, including names, bank addresses, sensor IDs and trip temperatures. This
establishes the address/table match. The subsequent phone test established live
readings; independent calibration accuracy and the temperature stop remain
untested. The drivers read these regions without changing their
configuration. Keep a bounded kernel run while checking `socinfo:` and
`thermal:` output. `thermal_log=5` can make the thermal summary visible during a
short run; the existing stop defaults to 105 C.

Primary implementation sources:
[`hakios.socinfo.c`](../drivers/core/hakios.socinfo.c),
[`hakios.thermal.c`](../drivers/power/hakios.thermal.c),
[`dt-thermal.py`](../tools/dt-thermal.py).

### Built probe artifacts and first phone test

The probe kernel built and linked successfully. The build wrapper accepted only
the existing kext symbol-set/export-check failures; there were no compile or
link failures. The generated driver list contains the original four virtio
drivers plus SOCINFO and THERMAL. A NULL check was added to the thermal polling
allocation so an allocation failure logs and stops that driver. Build log:
`logs/spacewar-kernel-probes-build.log`.

| Artifact | SHA-256 | Runtime status |
|---|---|---|
| `hakios-kernel-spacewar-probes` | `3617c4bdc4cdde6af18075a17161e0fb8a5ca1a44409bba2be5f4ef804a53327` | Boot, SOCINFO and live THERMAL readings passed |
| `spacewar-socinfo-payload.bin` | `3c01c727f183fb47bfe0c4969ffeb6a62704356163715e8721d76b6d3b428110` | Chip identity passed on the phone |
| `spacewar-thermal-payload.bin` | `bd4be739a9277e0014b13618e07e9def1adbbd133e3b1787071e10933f7ad704` | Identity plus temperature passed on the phone |

Both payloads contain the probe kernel and proven `hello` program, with a
30-second return budget and `hello_wait=60`. XNUP bounds, embedded bytes and
command-line lengths were checked. No CPU-frequency, SMP, storage, touch or
other phone driver is requested. The tested kernel/config/hello/smoke payload
were copied to `build/spacewar-kernel-baseline/`; `prebuilt/hakios-kernel` and the
original `spacewar-smoke-payload.bin` remain unchanged.

Copy `spacewar-socinfo-payload.bin` to `C:\platform-tools`. Start from the phone's
stock fastboot bootloader, then run in PowerShell:

```powershell
fastboot boot xnu-spacewar-ramfix-debug.img
# Wait until fastboot devices shows xnuloader, then:
fastboot stage spacewar-socinfo-payload.bin
fastboot oem run
Start-Sleep -Seconds 35
fastboot getvar result
fastboot get_staged spacewar-socinfo.log
Select-String -Path .\spacewar-socinfo.log -Pattern 'socinfo:','kern.hakios.soc_','Hello from user space','loader_return_after expired','EARLY KERNEL EXCEPTION'
```

The delay avoids querying USB while the single-core kernel occupies the loader's
CPU. Success means a parsed SMEM item 137, an actual numeric SoC identity, `hello`
and the timed return. The DTB allows IDs 475 and 515; the driver may have no
marketing name for a valid ID, which alone does not mean parsing failed.

Both identity and temperature tests have passed. To reproduce the latter, boot
a fresh loader and repeat with
`spacewar-thermal-payload.bin`, saving `spacewar-thermal.log` and searching
`thermal:`, `kern.hakios.thermal_`, the greeting and expiry. The explicit TM
addresses are `0xC263000` and `0xC265000`, polling every 2000 ms and logging every
5 seconds, with the existing 105 C stop enabled. Live sensor readings are now
verified; the high-temperature stop is still untested. Send the complete
resulting logs if a later run differs from the saved successful logs.

To rebuild the probe kernel and payloads:

```sh
make spacewar_defconfig
KERNEL_LOG=logs/spacewar-kernel-probes-build.log tools/make-kernel.sh -j8
cp hakios-kernel hakios-kernel-spacewar-probes
python3 xnuloader/mkpayload.py -o spacewar-socinfo-payload.bin \
  kernel=hakios-kernel-spacewar-probes ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000'
python3 xnuloader/mkpayload.py -o spacewar-thermal-payload.bin \
  kernel=hakios-kernel-spacewar-probes ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105'
```

## Buttons after the probes

The [buttons driver](../drivers/input/hakios.buttons.c) matches these DTB facts:
SPMI arbiter core `0x0C440000`, configuration `0x0C40A000`, observer
`0x0E600000`, execution environment 0; PMK8350 slave 0 PON `0x1300` supplies
power and volume-down, and PM7325 slave 1 GPIO6 supplies active-low volume-up.

The Spacewar fragment now builds `HAKIOS_BUTTONS`, gated by `buttons=1` (or the
touchscreen argument, which these probes omit). Use `buttons_steps=1` first. Subsequent fresh
runs can advance through channel discovery (2), peripheral identity (3), input
configuration/status (4), then polling (5). Steps 3 onward issue read commands
through the arbiter; they do not configure PMIC registers. The driver checks
the arbiter version before using its supported layout. Touch can remain off.

All three `ml_io_map()` results are now checked before use. A mapping failure
logs the failed block and stops the probe. Thread-start and synthetic-key
callback-allocation failures also log and stop their respective paths. These
guards do not establish access to every register. Step 1 maps the three windows and reads only the
core version and features: it sends no SPMI commands and makes no PMIC writes.
The DTB's larger configuration and observer regions contain the mapped subsets;
the last possible table/observer accesses for channel 511 fit those subsets.

The staged acceptance checks are:

| Step | Access made | Required evidence before advancing |
|---|---|---|
| 1 | Map the three arbiter windows; read version/features | Supported version, plausible channel count, timed return |
| 2 | Read the channel and owner tables | PON slave 0 / peripheral `0x13` and GPIO slave 1 / peripheral `0x8D` discovered |
| 3 | Issue read-request commands for peripheral type/subtype | PON type `0x01`, HLOS subtype `0x09`; GPIO type `0x10`, subtype `0x10`–`0x13` |
| 4 | Read PON real-time status/interrupt configuration and GPIO setup/status | Power/volume-down status readable; volume-up is enabled input with pull-up |
| 5 | Poll key status and post input records | Each physical press and release appears in the log without sustained bus errors |

Use a fresh loader and bounded kernel run for each step. The raw channel count
is printed before validation; zero or values above 512 trigger the existing
512-channel fallback and do not establish a valid hardware count. Stop on an
unsupported arbiter version, mapping error, early exception or missing timed
return. A `stopping here` line alone is insufficient: it also appears when a
probe exits after an error.

The built artifact is `spacewar-buttons-step1-payload.bin`, containing
`hakios-kernel-spacewar-buttons` and the proven raw-syscall `hello`. It keeps the
already tested identity and temperature arguments and adds exactly
`buttons=1 buttons_steps=1`. This step has now passed on the phone: arbiter
version `0x50020000`, 512 channels, `hello` as PID 1 uid 0, repeated 27/27 thermal
readings and a clean 30-second return. There were no mapping errors or early
exceptions, and the probe stopped before step 2. Evidence:
`logs/spacewar-buttons-step1-success.log`
and its SHA-256 sidecar. Physical key events were subsequently verified in
step 5 below.
The separate userland payload and previous probe artifacts are
preserved.

The kernel compiled and linked at `g96ffbc53` with seven drivers (four virtio,
SOCINFO, THERMAL and BUTTONS); the build wrapper accepted only the existing
kext symbol-set checks. Payload bounds, exact embedded kernel/hello bytes,
221-character command line and synchronized driver source passed inspection.
Build: `logs/spacewar-kernel-buttons-build.log`; inspection:
`logs/spacewar-buttons-payload-inspect.log`; checksums:
`logs/spacewar-buttons.sha256`; artifact manifest:
`build/spacewar-buttons/manifest.json`.
An isolated QEMU smoke test of this kernel also passed with the physical
hardware arguments absent: `hello` ran as PID 1, the 15-second budget returned
cleanly, and no buttons/SPMI probe or early exception occurred. Evidence:
`logs/spacewar-qemu-buttons-gated.log`. This checks the compiled driver's gated
boot path; it does not test phone registers.

The second payload, `spacewar-buttons-step2-payload.bin`, has identical embedded
kernel, `hello` and version bytes; only `buttons_steps=1` changes to
`buttons_steps=2`. Bounds, the 221-character command line and byte identity were
checked. It reads channel and ownership tables without issuing SPMI commands or
writing PMIC registers. Inspection: `logs/spacewar-buttons-step2-payload-inspect.log`;
checksums: `logs/spacewar-buttons-step2.sha256`; manifest:
`build/spacewar-buttons/step2-manifest.json`.

Step 2 passed on the phone: channel **101** is the PMK8350 PON (map
`0x1001300`), channel **134** is PM7325 GPIO6 (map `0x1018D00`), both owned by
system 0 with interrupts routed to that owner. The probe stopped at step 2,
`hello` ran as PID 1 uid 0 and the 30-second timer returned cleanly. Evidence:
`logs/spacewar-buttons-step2-success.log`
and its SHA-256 sidecar. One thermal summary rejected the `video` sample
(26/27 valid); the preceding and following summaries had all 27 valid. The
driver rejects a sample on a clear valid bit or out-of-range value; the raw
status/rejection reason was not logged, so the cause remains unknown.

The third payload, `spacewar-buttons-step3-payload.bin`, preserves the same
kernel/hello/version bytes and changes only `buttons_steps=2` to
`buttons_steps=3`. It sends two SPMI read requests for peripheral type/subtype
registers `0x04–0x05`, using observer offsets `0x3280` and `0x4300`. Command
`0x08000041` asks for two bytes; the driver checks completion/error bits with a
2000 microsecond timeout. It writes only arbiter read commands, with no PMIC
register writes. Expected identities are PON type `0x01` subtype `0x09`, and GPIO
type `0x10` subtype `0x10`–`0x13`. Inspection:
`logs/spacewar-buttons-step3-payload-inspect.log`; checksums:
`logs/spacewar-buttons-step3.sha256`; manifest:
`build/spacewar-buttons/step3-manifest.json`.

Step 3 passed on the phone: PON type `0x01`, subtype `0x09` read in 10
microseconds; GPIO6 type `0x10`, subtype `0x11` read in 9 microseconds. Both
match the driver's layout. The probe stopped at step 3, ran `hello` as PID 1
uid 0 and returned on its 30-second budget, without a bus error or early
exception. Evidence:
`logs/spacewar-buttons-step3-success.log`
and its SHA-256 sidecar.

The fourth payload, `spacewar-buttons-step4-payload.bin`, changes only
`buttons_steps=3` to `buttons_steps=4`. It preserves the tested kernel, `hello`
and version bytes. It reads PON status/interrupt registers `0x1310–0x1319`,
GPIO6 configuration `0x8D40–0x8D47` and GPIO6 status `0x8D10`; it does not
change the PMIC configuration. Bounds and byte identity passed inspection:
`logs/spacewar-buttons-step4-payload-inspect.log`; checksums:
`logs/spacewar-buttons-step4.sha256`; manifest:
`build/spacewar-buttons/step4-manifest.json`.

Step 4 passed on the phone. The PON status was `0x10`, with power and volume
down both released; its interrupt configuration/status fields were zero.
GPIO6 was mode 0 (input), supply 1, pull 0 (up), enabled, and high (released).
Its configuration bytes were `00 01 00 00 00 01 80 00`. The probe stopped at
step 4, `hello` ran as PID 1 uid 0 and the 30-second budget returned cleanly,
without a bus error or early exception. No step-5 polling occurred. Evidence:
`logs/spacewar-buttons-step4-success.log`
and its SHA-256 sidecar. This established readable input setup and released
levels before the physical transition test.

The fifth payload is `spacewar-buttons-step5-payload.bin`, using the same tested
kernel, `hello` and 30-second budget with `buttons_steps=5`. It polls the inputs
every 16 ms, confirms changes on two successive looks and posts button records.
It still makes no PMIC configuration writes.

Step 5 passed on the phone. The direct log records two physical presses and
two releases for each of power, volume up and volume down. The 10-second bus
summary reports 1,256 reads, 9 microseconds per read on average, 11 microseconds
maximum and zero failures. Its early counts show only one power press, one
volume-up press and no volume-down press because the later taps occurred after
that summary; the individual events verify all six presses and releases.
`hello` ran as PID 1 uid 0, thermal data stayed live and the kernel returned
cleanly on its 30-second budget, without a bus error or early exception.
No synthetic `press_power` argument was used. Evidence:
`logs/spacewar-buttons-step5-success.log`
and its SHA-256 sidecar. This verifies physical polling and kernel event
generation; consumption by graphical userland has not yet been tested.

To reproduce step 5, copy phone test artifacts with `./copy FILENAME`.
Starting from the stock bootloader,
run in PowerShell:

```powershell
./fastboot boot xnu-spacewar-ramfix-debug.img
# Wait until fastboot devices shows xnuloader, then:
./fastboot stage spacewar-buttons-step5-payload.bin
./fastboot oem run
Start-Sleep -Seconds 5
# Now tap/release power, volume up, then volume down individually while this waits:
Start-Sleep -Seconds 30
./fastboot getvar result
./fastboot get_staged spacewar-buttons-step5.log
Select-String -Path .\spacewar-buttons-step5.log -Pattern 'buttons:','Hello from user space','loader_return_after expired','EARLY KERNEL EXCEPTION'
```

Leave the keys released during boot. After the first five seconds, press each
key for roughly half a second and release it fully before trying the next.
Finish the three taps within the kernel's 30-second run. Require the step-5
watching line, a `PRESSED` and `released` event for each of power, volume up and
volume down, a bus summary with reads and zero failures, the greeting and timed
return. At the 10-second summary, later taps may not yet be included in the
press counts, so also inspect the individual events. Direct log and message
buffer replay copies of the same timestamp are one event. Step 5 polls until
the timed return; it does not print the earlier stages' `stopping here` line.
Stop on bus errors, unsupported GPIO setup or an early exception.

Rebuild commands (hashes of earlier artifacts above identify their
original builds, and will differ after source/config changes):

```sh
make spacewar_defconfig
KERNEL_LOG=logs/spacewar-kernel-buttons-build.log tools/make-kernel.sh -j8
cp hakios-kernel hakios-kernel-spacewar-buttons
python3 xnuloader/mkpayload.py -o spacewar-buttons-step1-payload.bin \
  kernel=hakios-kernel-spacewar-buttons ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 buttons=1 buttons_steps=1'
python3 xnuloader/mkpayload.py -o spacewar-buttons-step2-payload.bin \
  kernel=hakios-kernel-spacewar-buttons ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 buttons=1 buttons_steps=2'
python3 xnuloader/mkpayload.py -o spacewar-buttons-step3-payload.bin \
  kernel=hakios-kernel-spacewar-buttons ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 buttons=1 buttons_steps=3'
python3 xnuloader/mkpayload.py -o spacewar-buttons-step4-payload.bin \
  kernel=hakios-kernel-spacewar-buttons ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 buttons=1 buttons_steps=4'
python3 xnuloader/mkpayload.py -o spacewar-buttons-step5-payload.bin \
  kernel=hakios-kernel-spacewar-buttons ramdisk=userland/hello \
  --cmdline '-v hello_wait=60 loader_return_after=30 socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 buttons=1 buttons_steps=5'
```

## Secondary CPUs after the probes

CPU7 handoff passed on the phone. Firmware returned 0 for the request to start
MPIDR `0x700`; XNU registered logical CPU0 at physical ID `0x700`, ran `hello`
as PID 1 uid 0 and returned cleanly after 30 seconds. There was no early
exception, hung-core or core-left-running diagnostic. The payload had no
explicit `cpus=` argument; the loader supplied `cpus=1`. Evidence:
`logs/spacewar-cpu7-success.log` and its SHA-256
sidecar. One thermal poll omitted `mdmss-0` and recovered on the next poll.
The two-core stage subsequently passed: logical CPU0 used `0x700`, logical
CPU1 used `0x600`, and GenericARMPE reported `2 of 2 processors are running`.
`hello` ran as PID 1 uid 0 and the 30-second return completed without a CPU
start/stop fault, early exception, hung result or core-left-running diagnostic.
Evidence: `logs/spacewar-smp2-success.log` and
its SHA-256 sidecar.

The seven-core stage also passed. All seven logical CPU records mapped to
physical IDs `0x700` through `0x100`; the initial four-running count became
seven after the 28-second little-core callback. `hello` ran as PID 1 uid 0 and
the 60-second return, initiated by logical CPU2, handed back through the boot
processor without an early fault, start/stop failure or core-left-running
diagnostic. Evidence: `logs/spacewar-smp7-success.log`
and its SHA-256 sidecar. This establishes basic SMP availability and clean
return. Sustained CPU load and CPU-frequency control remain untested. Full CPU
milestones and reproduction commands are in
[spacewar-cpu.md](spacewar-cpu.md).

The DTB has MPIDRs `0`, `0x100`, through `0x700`, grouped as four little, three
big and one fastest CPU. Its GIC bases are `0x17A00000` / `0x17A60000`, and PSCI
uses `smc`. These agree with the loader's shared SC7280/Redwood profile:
`core_shift=8`, eight possible cores and `small_cores=0x0F`.

The passed first stage used `bootcpu=7` and no explicit `cpus=`: the loader
supplied one CPU and retained CPU0 for firmware/USB. The passed second stage
added `cpus=2`; the passed third stage used `cpus=7`, with a 60-second return
budget and the existing 28-second little-core delay. Initial `4 of 7` was
expected and the subsequent seven-running count plus all seven registrations
established success. With CPU0 retained by the loader, at
most seven CPUs can belong to XNU; `cpus=8` does not establish eight kernel CPUs
in that mode. Use a fresh loader before each kernel run. Source:
[`xnuloader/loader.c`](../xnuloader/loader.c).

## Touch: guarded read-only stages 1–2 passed

An eight-driver kernel now compiles `GOODIX` with a Spacewar-specific guard
that allows the read-only inspections when `touchscreen=1` is
explicitly supplied. Runtime touch remains off by default. Its stage-1 payload
passed on the phone: reset GPIO105 and IRQ GPIO81 both reported configuration
`0x1`, I/O `0`; GCC votes `0x8000`, gates `0/0x8000000A/0x80004220`; command DB
resolved `ldoc8=0x42500` and `ldoc3=0x40E00`. The probe stopped without writes,
`hello` ran as PID 1 uid 0 and the 30-second timer returned cleanly. Evidence:
`logs/spacewar-touch-step1-success.log`
and its SHA-256 sidecar. Stage 2 also passed: RSC ID `0x20300`, version 2.3,
eight TCS and 16 commands each. ACTIVE slot 0 retains firmware clock requests
at `0x30000` / `0x30010`; slot 1 had status `1`, command-enable `0`, wait `0`,
control `0x10000` and zero first-three command records. Shared IRQ enable was
`1`, status `0`. These are snapshots, not an ownership claim. Evidence:
`logs/spacewar-touch-step2-success.log`.
Stage 3 then passed: all six L8/L3 voltage/mode/enable requests received status
`0x10101`, slot 0 retained its state, reset105 stayed low and the kernel returned
cleanly after 30 seconds. This verifies accepted power votes, not a measured
rail voltage. Evidence:
`logs/spacewar-touch-step3-success.log`.
The next image combines checked clock access, SPI setup and controller identity,
advancing automatically when each prerequisite passes. Neither live controller identity
nor touch events are verified yet. Source evidence, guard validation, artifact
hashes and the exact test procedure are in
[spacewar-touch.md](spacewar-touch.md).

The current ROM selects `focaltech,fts_ts` at SPI `0xA94000`, with bus pins
52–55, IRQ81 and reset105. Those signals match the
[common touch driver](../drivers/input/hakios.goodix.c). Full bring-up still
requires a device-specific power sequence: the DTB supplies AVDD from PM8350C
**L3** and IOVDD from **L8**, while the Redwood path votes **L2** and uses GPIO107
for analog power. Its Redwood strap
selection, FT3680 ROM-ID/embedded-firmware handling and high-resolution report
assumptions also require comparison with the matching Android touch driver.
The DTB's FocalTech IC type is `0x3658D488`, with 1080×2400 display coordinates.

Use the matching touch source and firmware before enabling full controller
initialization. The guarded read-only paths do not perform that initialization.
The tree also describes a Goodix GT9916S alternative and names
`goodix_firmware.bin` / `goodix_cfg_group.bin`; their presence in the DTB does
not establish which controller is fitted or make Redwood firmware appropriate.
