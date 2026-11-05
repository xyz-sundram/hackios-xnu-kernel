# Spacewar charging, 5 November 2026

The wallpaper optimization was removed at the user's request. The user
confirmed normal wallpaper and visible battery percentage in the rollback
build. Correct scale-2 layout, renderer fixes and touch changes are retained.
Both normal Metal UI profiles now keep measured battery reporting enabled.

Actual charging is still under hardware investigation. A charging icon or
firmware status 1 is insufficient: the recovered baseline shows the battery
losing charge while USB is online. Only a PC USB supply is available for the
current tests. A later Android comparison establishes that this same connection
can negotiate USB-PD at 5 V / 1.6 A and charge the battery; the source itself is
not limited to the roughly 0.4 A observed under HakiOS.

## Measured baseline

The recovered private `hakios-audio-B91bLj` capture contains these readings:

| Source uptime (s) | Raw SOC | Charge counter (uAh) | Battery current (uA) | USB current (uA) |
| --- | ---: | ---: | ---: | ---: |
| 2622.244919 | 50.98% | 2221198 | -1222856 | 397000 |
| 2642.593560 | 50.95% | 2219891 | -734873 | 397000 |
| 2683.300596 | 50.71% | 2209434 | -1137100 | 397000 |
| 2744.331471 | 50.48% | 2199413 | -664987 | 397000 |

The counter fell by 21785 uAh and SOC by 0.50 percentage points over 122.087 s.
Firmware reported USB adapter type 1 (SDP), approximately 5.074 V input and
charging status 1. This establishes net battery discharge on the tested PC
connection. Input power was approximately 2 W; a 500 mA configured USB budget
may still be insufficient for an active UI. The derived measurements are in
`build/spacewar-charging-20261105/baseline-measurements.json`; raw logs remain
private.

## Implemented charger path

The protocol follows Nothing's published
[PMIC battery client](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/power/supply/qti_battery_charger.c)
and
[USB current callback](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/drivers/usb/dwc3/dwc3-msm.c).
Stock `dwc3_msm_gadget_vbus_draw()` forwards the configured gadget current to
`POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT`; the frozen U-Boot USB shim has no
equivalent power-supply vote. This is evidence for adding the configured-current
handoff, rather than proof that it alone fixes net charging.

The loader advertises USB2 500 mA (USB3 descriptor 504 mA; the active shim is high
speed). It publishes its actual configured state, generation, heartbeat and
500 mA budget in a separate 64-byte cache line at offset 8192 of the existing
16 KiB touch page. The producer publishes the odd sequence before changing
data, then the completed even sequence. The kernel validates the ABI and
observes an advancing heartbeat before admitting a vote.

The Spacewar PMIC client registers the vendor's 28-byte notification message,
waits for its acknowledgement and settling period, and reads the existing input
limit. The only numeric SET is USB opcode `0x33`, property 5, exactly 500000 uA.
It requires a fresh configured budget, USB online, adapter type 1 and a known
existing limit of at least 100 mA and below 500 mA. Suspended, unknown, stale,
unconfigured, dedicated and PD cases do not qualify. The write is attempted once
per configuration, even if its acknowledgement is lost, and followed by a GET
read-back. Firmware battery-current, voltage and thermal limits remain in
control. No charge-enable override is implemented.

The monitor survives channel interruptions and re-registers notifications on
reopening. Raw SOC, charge counter, voltage, current and temperature are logged
by the bounded private diagnostic helper. Charger samples include health,
FCC/max FCC, USB maximum/input limit, charge-enable and moisture state.

## Validation and artifacts

`tools/spacewar-charging-test.py` executes the actual producer, cache reader,
protocol, policy and reconnect loop with host mocks. It checks stale or racing
publications, matching replies, ACK/read-back failure, stop cancellation, one
write per generation and refusal of suspended or unqualified sources. It also
checks persistent battery defaults in both UI profiles and all matrix variants.
The battery bridge tests cover both device profiles; link tests cover 12288
cases and diagnostic framing covers 132518 cases. The wallpaper rollback native
build passes 264 renderer tests.

The charger kernel is `2.3.4-hakios-darwin22.5-sc7280-c1`, SHA-256
`15f72d15356b505f54d83578ad5a589e3ccc2ab93b06acf5683078a58e0db10c`.
The timed 180-second probe lost USB after ADSP startup. Recovery validated the
135842-byte private `hakios-audio-vMcCra` capture. It establishes notification
registration ACK, correct raw gauge/sysctl updates and a live loader heartbeat.
However, the loader reported `configured=0`, generation 3. The firmware reported
USB online, SDP, USB maximum 900000 uA, input limit 100000 uA and raw
`charge_enable=0`; that last vendor property is recorded without assuming its
meaning overrides the reported charging status. No 500 mA SET qualified.

Before the later cable transitions, raw SOC fell from 49.47% to 48.91% and the
counter from 2155407 to 2131008 uAh between diagnostic seconds 3 and 124. The
whole saved run ends at 48.72% and 2122730 uAh. Derived evidence is in
`charger-controls-measurements.json`. The source of the USB loss still needs
isolation; the trace establishes that it prevents the configured-current vote.

A subsequent loader candidate uses the frozen U-Boot protocol's Stop/StartEx
to reprobe the gadget once if it remains disconnected for five seconds during
the first 30 seconds. It applies only to the known shim revision and a run
that started configured with the shared power page. It reuses the shim's
existing transfer buffers. It never declares the gadget configured: a later
host SET_CONFIGURATION must do that before the charger policy can qualify.
Host tests check the grace period, reconnects, one-attempt bound, protocol
revision, startup window and failure cases. The phone test did not restore
USB. Recovery validated the 139317-byte `hakios-audio-5eTul7` capture: the loader
heartbeat remained fresh but configuration stayed 0, the input limit stayed
100000 uA, and no 500 mA SET qualified. Between diagnostic seconds 3 and 171,
SOC fell from 48.69% to 47.74% and the counter from 2121423 to 2080031 uAh.
This candidate is not a USB or charging fix. `HAKIOS_USB_RECOVER` now defaults
off in the loader source and build script; host tests also execute that default.
No positive-charge result is claimed yet.

## USB supply diagnostic

The base USB PHY source does not acquire the board's `vdda-pll`, `vdda18` and
`vdda33` supplies. The generic PHY core only acquires `phy-supply`, which this
node does not specify. This is a concrete integration gap and a possible cause
of power-state loss; it does not establish that a rail actually collapsed.

The `c2` kernel adds a diagnostic restricted to Spacewar `battmgr=2`. It compares
three candidate supply peripherals before the DSP proxy vote, after that vote,
after firmware authentication and during battery polling. It uses the existing
SPMI observer read command under the common lock, validates the arbiter and
peripheral type, bounds all addresses/transfers and times out after 2 ms.
It cannot write a PMIC register or change a supply vote. Tests execute these
guards and the exact read commands under ASan/UBSan.

The early phone log in `phone/charger-usb-rails/` captured all three startup
phases. The expected `ldob2` and `ldoc1` entries were absent from the AP observer
table; the candidate `ldoc10` entry reported type 9, not the expected LDO type
4. The diagnostic therefore refused the control reads. The supply comparison
is inconclusive. Those candidate addresses must not be used for regulator
writes. The diagnostic kernel SHA-256 is
`4352837a7fd4a7632cfd8d753cc0dfd168c9d370d6a210d5909ced09b43da898`.

Windows reported the loader as absent during the run, rather than a currently
present phone endpoint with a driver error.

## Android comparison on the same PC supply

The user booted Android with the same PC USB cable. The charging comparison used
read-only ADB queries and its existing root shell. Android reports USB-PD, device data role, sink
power role, PD revision 3, a 5000000 uV maximum and 1600000 uA negotiated input
limit. Actual input current was 1300000 uA. Two raw battery readings had
1240861 and 1075759 uA positive current. The charge counter rose from 2123166
to 2184164 uAh, an increase of 60998 uAh, and reported SOC rose from 49% to 50%.

Android's `usb_charger_en` is **0 while successfully charging**. It must not be
interpreted as proof that charging is disabled or blindly changed to 1.
All 38 ADSP firmware files, including the metadata header, match HakiOS byte for
byte. The missing PD/Type-C integration is now the main investigation; the
earlier SDP power deficit is an observed fallback state, not proof that the PC
cannot supply sufficient power.

The candidate UCSI client uses the same owner 32779, 48-byte buffer ABI and
initialization sequence as Nothing's published `ucsi_glink.c` and `ucsi.c`.
It admits only PPM interface reset, notification setup, capability/status reads
and completion acknowledgements. Role changes, connector resets, PDO writes
and charger limits are refused. It first validates the reported UCSI version
and single-connector topology. Transport, completions, errors, stop, timeouts,
notifications and the command whitelist are tested using the production code.
It remains restricted to `battmgr=2` because phone testing has not established
charging or UI stability; normal UI profiles do not run it. SDP current votes re-read the source before transmission so
a new PD contract cannot inherit the earlier sampled SDP limit.

## UCSI phone result

The `c3` kernel is `2.3.4-hakios-darwin22.5-sc7280-c3`, SHA-256
`4f1f8ebd5596b70d21b8a9a173a6c55df47532ae9e909e66993e69a6c45bd507`.
Recovery validated the 154521-byte private `hakios-audio-9ts6H6` capture.
The firmware accepts the UCSI transport, reports version `0x100` and initializes
one connector with capability bytes `7f 03`. Every subsequent status still
reports disconnected, flags 0 and RDO 0, while the battery service reports USB
online as SDP. Interface initialization alone did not restore PD negotiation.

Between diagnostic seconds 4 and 173, the charge counter fell from 2386764 to
2345373 uAh, a loss of 41391 uAh, and raw SOC fell from 54.78% to 53.83%.
The loader stayed unconfigured, so no configured-current SET qualified.
The derived evidence is `charger-ucsi-measurements.json` in the private build
directory. The user reported a visible freeze after USB diagnostics disappeared.
Battery recording and the loader heartbeat continued through second 173; those
observations establish continued background execution, not UI responsiveness.

## PAN and freeze-diagnostic checkpoint

The next candidate adds the published PMIC alternate-mode notification service
(PAN owner 32780). It registers port 0 and acknowledges notices after at least
20 ms from the worker, including while UCSI initialization waits. Transport
callbacks never wait for their own replies. Only registration and notice ACK
commands are admitted; this client does not change roles, PDOs, mux registers
or charger limits. UCSI/PAN tests execute the production modules, including
deferred PAN acknowledgement during UCSI initialization. Both services remain
restricted to the explicit `battmgr=2` diagnostic profile.

The `c4` kernel is `2.3.4-hakios-darwin22.5-sc7280-c4`, SHA-256
`9de9a2123780b7745af2396050f01fb6866822861b5a2aabb1b4e49d2dc32602`.
Its build passes, and the APFS root passes full read-back verification.
The diagnostic helper now records framebuffer submissions, completed copies,
drops, input posts/reads/drops and named UI process runtime alongside battery
samples. GPU, touch, surface and panic messages are retained in the bounded
private log. These counters distinguish progress in individual paths; they
do not measure physical scanout or establish a frame deadline.

The phone run is `phone/charger-pan-freeze/`, using `charge-probe-pan/` and
`kernel-charge-pan/`. The user initially reported responsive swipes after a
three-second USB disconnect/reconnect, then later reported a frozen display.
This diagnostic automatically returns to the loader after 180 seconds and
can leave its last framebuffer visible. Saved timestamps and counters must
distinguish that planned stop from an earlier freeze. The C4 private log has
**not yet been recovered**, so neither a PAN charging result nor a freeze fix
is claimed.

Work stopped at the user's request to save and push progress. The last device
action was a RAM boot of `ram-bundle-charge-normal/loader-debug.img` from stock
fastboot, preparing for C4 log recovery; its resulting loader/menu state has
not been checked. No further charging probe was started.

To resume:

1. Recover the saved C4 HFS log without overwriting an already staged terminal
   kernel log. `tools/spacewar-debug.py` preserves that staged result.
2. Compare PAN registration/notices, UCSI connector/RDO state, charge-counter
   change and the framebuffer/input/process counters at the reported freeze.
3. Resolve the missing Type-C attach/PD handoff. Android's positive charging
   result on the same cable remains the control; do not force its 1.6 A limit
   without the corresponding negotiated contract.
4. Use an untimed UI run for stability validation. Promote experimental clients
   only after positive measured charge and sustained UI responsiveness.

**Charging is not fixed.** Normal UI packages retain measured battery reporting
and the wallpaper rollback. The failed USB restart and unvalidated scanout paths
remain off by default.

## Local artifacts

Source snapshots, build judgments, audited roots and private phone evidence are
under `build/spacewar-charging-20261105/`. The loader in `loader-charge-final/`
includes both cache-publication cleans; the first timed charger probe used an
earlier compatible ABI-1 loader with only the final clean. Recovery uses the
later loader. `loader-usb-recovery/` and `ram-bundle-charge/` preserve the failed
startup reprobe. The current normal bundle is `ram-bundle-charge-normal/`, with
`loader-charge-normal/` and the `c1` charger kernel. Its startup reprobe is off,
both UI profiles enable battery reporting and wallpaper freezing is off.
`kernel-charge-rails/` and `charge-probe-rails/` preserve the separate `c2`
diagnostic. All phone tests use RAM boot; no boot partitions are flashed.
