# Spacewar CPU handoff and SMP

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


CPU7 handoff passed on the phone using `spacewar-cpu7-payload.bin`. Firmware
accepted the SMC request for MPIDR `0x700`, XNU registered logical CPU0 at
physical ID `0x700`, and `hello` ran as PID 1 uid 0 before the clean 30-second
return. No early exception, hung-core or core-left-running diagnostic appeared.
Evidence: `logs/spacewar-cpu7-success.log` and its
SHA-256 sidecar. Two-core XNU has subsequently passed as well: logical CPU0
used physical `0x700`, logical CPU1 used physical `0x600`, and GenericARMPE
reported **2 of 2 processors running**. `hello` and the 30-second return passed
without a CPU start/stop fault. Evidence:
`logs/spacewar-smp2-success.log` and its SHA-256
sidecar. All seven kernel-owned cores have now passed as well, including the
delayed little-core startup and clean return from a secondary-initiated stop.
Evidence: `logs/spacewar-smp7-success.log` and
its SHA-256 sidecar. The next phone focus is the guarded, read-only touch
inspection in [spacewar-touch.md](spacewar-touch.md). Sustained CPU load and
CPU-frequency control remain untested.

The first CPU milestone moves the existing single-core XNU boot from physical
CPU0 to physical CPU7 while the resident loader stays on CPU0. It does not add
kernel CPUs. `bootcpu=7` alone makes the loader describe only MPIDR `0x700` and
inject `cpus=1`; an explicit `cpus=` or `cpumask=` would activate the separate
secondary-core discovery path, so neither is included.

The current-ROM DTB and loader agree:

| Property | DTB | Loader |
|---|---|---|
| CPU MPIDRs | `0x000`, `0x100`, …, `0x700` | Eight cores, `core_shift=8` |
| PSCI | `arm,psci-1.0`, method `smc` | `platform_redwood.psci_hvc=0` |
| GIC distributor | `0x17A00000`, size `0x10000` | Same |
| GIC redistributors | `0x17A60000`, size `0x100000` | Same; kernel searches by actual MPIDR |
| Little cores | Capacity 1024 for CPUs0–3 | `small_cores=0x0F` |
| CPU7 | Largest DTB capacity, 1985 | Selected as MPIDR `0x700` |

The loader cleans the new core's boot data/code to memory before PSCI CPU_ON,
checks whether it arrives, and records early faults or a missing heartbeat.
The return stub communicates the result to CPU0 and requests CPU_OFF on CPU7.
The loader source is unchanged between `a3384e03` and `96ffbc53`; both the
working ramfix boot image and the rebuilt identity-bearing loader contain this
CPU logic. The phone has now exercised SMC handoff, all seven kernel-owned
cores and a clean return that began on a secondary CPU. Successful CPU_OFF
requests do not each emit a log line; the returned log has no start/stop fault
or core-left-running diagnostic.

The prepared payload preserves the exact tested
`hakios-kernel-spacewar-buttons` kernel and raw `hello`, keeps SoC/thermal
probes, omits buttons, touch, CPU-frequency and storage arguments, and uses a
30-second return budget. The command line is 205 characters. Payload bounds,
entry bytes, version, kernel hash and DTB facts passed inspection.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `spacewar-cpu7-payload.bin` | 14,873,712 | `34849b65edae2f19debe00c82dfa3cfdb5b9964472afa212696eab01afc0b4d2` |
| `hakios-kernel-spacewar-buttons` | 14,856,800 | `afade5c61dd66c0d4dba0729ba439c0d8ffbe8ca4d0a36ab8e184fe2692ff317` |

An earlier isolated emulator test also passed; its historical records remain
listed below. The phone log above is the evidence for the physical CPU7
handoff. The phone continued to read SoC/thermal data. One thermal poll rejected
`mdmss-0` (26/27 valid), followed by recovery to all 27 sensors; the rejected
status word/reason was not logged.

Evidence and reproducible test runner:

- `build/spacewar-cpu/audit.json`
- `build/spacewar-cpu/manifest.json`
- `build/spacewar-cpu/SHA256SUMS`
- `logs/spacewar-qemu-cpu7.log`
- `build/spacewar-cpu/qemu/result.json`
- `build/spacewar-cpu/qemu-handoff.py`

To reproduce CPU7 handoff, first run `./copy spacewar-cpu7-payload.bin` from the
workspace. Start from a fresh stock bootloader and run in PowerShell:

```powershell
./fastboot boot xnu-spacewar-ramfix-debug.img
# Wait until fastboot devices shows xnuloader, then:
./fastboot stage spacewar-cpu7-payload.bin
./fastboot oem run
Start-Sleep -Seconds 35
./fastboot getvar result
./fastboot get_staged spacewar-cpu7.log
Select-String -Path .\spacewar-cpu7.log -Pattern 'core 700','phys id','processors running','Hello from','thermal:','loader_return_after expired','EARLY KERNEL EXCEPTION','THE KERNEL'
```

Require the loader's core700 selection/start, kernel registration at physical
ID `0x700`, the greeting, live probes and clean timed return. `cpu_start() cpu:
0` is expected: it is the kernel's logical numbering of its only CPU. A
`no-such-core`, hung result, early exception, missing greeting or missing expiry
fails the test. Preserve the full log. The loader's CPU0 is excluded from XNU's
secondary-core list.

## Two kernel CPUs: passed

The CPU7 prerequisite has passed. `spacewar-smp2-payload.bin` adds only
`cpus=2` to that payload's arguments, with the same kernel, raw `hello`, SoC
and thermal probes and 30-second budget. No button polling, touch, CPU-frequency
or storage argument is added. The packaged command line is 212 characters.
The artifact was promoted after the CPU7 pass, copied with `./copy` to
`C:\platform-tools`, and its destination checksum verified. Physical two-core
operation passed on the phone, including `2 of 2 processors are running`,
`hello` and timed return. SHA-256:
`2439be21ee6eeb13b5d310d686d248acf2f71f62d12d893e2821cd383cfea18c`.
The published bytes/hash and acceptance checks are recorded in
`build/spacewar-cpu/smp2-manifest.json`
and `build/spacewar-cpu/SMP2-SHA256SUMS`. The read-only
audit and original staging plan remain under `build/spacewar-cpu/pending/`.

`cpus=2` is a count, not a fixed physical mask. The loader puts MPIDR `0x700`
first, then eligible off cores in the order `0x600`, `0x500`, `0x400`, `0x300`,
`0x200`, `0x100`. It skips cores the firmware reports already on. The normal
second core is physical CPU6, but the phone log must establish the actual
selection. UEFI retains physical CPU0. Do not combine `cpus=` with `cpumask=`;
the kernel flags that configuration invalid.

The current kernel contains the required PSCI secondary entry, Aff1-aware
CPU-data lookup, early fault recording, cache cleaning before the cache-off
start, per-core GIC redistributor search, Aff1-aware SGI routing, and the
secondary stop/CPU_OFF path. The physical two-core test passed. A failed
secondary start can leave `hello` running
on CPU7, so a greeting alone is insufficient.

To reproduce two-core SMP, copy its payload with `./copy` and start from a
fresh stock bootloader:

```powershell
./fastboot boot xnu-spacewar-ramfix-debug.img
# Wait until fastboot devices shows xnuloader, then:
./fastboot stage spacewar-smp2-payload.bin
./fastboot oem run
Start-Sleep -Seconds 35
./fastboot getvar result
./fastboot get_staged spacewar-smp2.log
Select-String -Path .\spacewar-smp2.log -Pattern 'cores for the kernel','core 700','phys id','processors','psci:','Hello from','loader_return_after expired','EARLY KERNEL EXCEPTION','THE KERNEL'
```

Require logical CPU0 at physical `0x700`, logical CPU1 at the selected
secondary, `GenericARMPE: 2 of 2 processors are running`, hello, live probes
and clean timed return. Preserve the full log. Stop on PSCI denial/start
timeout, early fault, watchdog reset, hung result, a core left running or
missing expiry. A fresh phone boot is required after incomplete CPU shutdown.

## Seven kernel CPUs: passed

`spacewar-smp7-payload.bin` preserves the same tested kernel, `hello` and
version bytes. It changes `cpus=2` to `cpus=7`, extends the return budget to 60
seconds and keeps `hello` alive for 90 seconds. SoC and thermal probes remain
enabled; button polling, touch, frequency changes and storage stay absent.
Bounds, exact embedded bytes and the 212-character command line passed
inspection. The kernel supports eight CPU records; this mode uses seven,
retaining physical CPU0 for UEFI. No four-core intermediate requirement was
identified in the source/DTB audit after the two-core pass.

The loader's eligible order is `700 600 500 400 300 200 100`. All seven must
be registered. Physical CPUs7, 6, 5 and 4 start first. The three little cores
(physical CPUs3, 2 and 1) start through the existing callback after 28 seconds.
**An initial `4 of 7 processors are running` line is expected.** Success needs
the later `the slower cores started too: 7 processors running` line, all seven
distinct CPU registrations, and a clean return. The delayed path does not emit
a literal `7 of 7` line. A 60-second run leaves roughly 32 seconds after the
little-core callback to exercise the complete set.

The scheduler code pairs each of the three little cores with a different
non-boot big core, leaving physical CPU7 independent. Secondary startup has
full-affinity MPIDR matching, early exception recording and cache-cleaning;
GIC redistributor lookup and SGI routing handle each `0xN00` affinity.

The phone test passed all seven registrations, logical CPUs0–6 at physical
IDs `0x700`, `0x600`, `0x500`, `0x400`, `0x300`, `0x200`, `0x100`. The initial
`4 of 7` count was followed by `the slower cores started too: 7 processors
running` after 28.006 seconds. `hello` ran as PID 1 uid 0, SoC/thermal data
remained live and the 60-second budget expired. The return began on logical
CPU2 (physical `0x500`), handed the job to the boot processor and reached the
loader cleanly. There was no early fault, hung result, CPU start timeout,
failed-stop or core-left-running diagnostic. Evidence:
`logs/spacewar-smp7-success.log` and its SHA-256
sidecar. This verifies basic startup, scheduling availability and return for
seven kernel cores; sustained-load stability and CPU-frequency control are
still untested.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `spacewar-smp7-payload.bin` | 14,873,728 | `29ebb7c0af6e517d0daf49300f7d0fe2ef0684d0d05caef103b6919b9d3cb66e` |

Manifest and validation:
`build/spacewar-cpu/smp7-manifest.json`,
`build/spacewar-cpu/smp7-SHA256SUMS`,
`build/spacewar-cpu/smp7-inspect.log`.
The artifact was copied with `./copy` to `C:\platform-tools` and its destination
checksum verified. To reproduce, start from a fresh stock bootloader:

```powershell
./fastboot boot xnu-spacewar-ramfix-debug.img
# Wait until fastboot devices shows xnuloader, then:
./fastboot stage spacewar-smp7-payload.bin
./fastboot oem run
Start-Sleep -Seconds 65
./fastboot getvar result
./fastboot get_staged spacewar-smp7.log
Select-String -Path .\spacewar-smp7.log -Pattern 'cores for the kernel','phys id','processors','psci:','Hello from','loader_return_after expired','EARLY KERNEL EXCEPTION','THE KERNEL'
```

Require all seven registrations, the post-callback seven-running count, hello,
live probes and the 60-second expiry/return. Stop on a CPU start failure,
timeout, early fault, a core left running or a hung result. Preserve the full
log and boot fresh before another test. No additional emulator test was run
for the seven-core stage.
