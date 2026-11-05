# Phone (1) brightness

## Selected boot path and current status

Use the frozen working **U-Boot → resident XNU loader → HakiOS** path. Display
output and touch already work on that path. Native panel writes and their exact
readbacks now pass on the phone; the user confirmed visible dimming/brightening.
The same setter also passes through IOKit while iOS is running. The registration
revision now works through both Settings and Control Center: the user confirmed
that both sliders move and visibly change brightness. The saved phone capture
contains 113 acknowledged native requests spanning 0–100%, with matching panel
readbacks and no display-module creation errors.

The successful revision is `native-ui-v2` under the isolated brightness build.
It is running from RAM; normal boot images have not been flashed. The source
and userland build now include the registration bridge for future builds.
Do not resume the Mu display-driver experiments for this task.

The reported symptoms were an immovable Control Center slider and a Settings
slider that moved without changing the panel. The original framebuffer exposed
no `backlight-control` property. The recovered 120,738-byte diagnostic capture
showed BackBoardServices availability false/current `1.0` through 270 seconds,
one `disp0` without that property and no `AppleBacklightDisplay` service.

## Native panel transport

The supplied current-ROM DTB selects
`qcom,mdss_dsi_rm692e5_visionox_amoled_120hz_cmd`. Its primary display binds that
panel to DSI controller 0 (`0x0ae94000`, v2.5). The panel uses DCS brightness,
maximum 4095, virtual channel 0 and `qcom,mdss-dsi-bl-inverted-dbv`. Combining
that flag with the matching Android setter establishes the on-wire command:
`0x51`, high brightness byte, low brightness byte. Default brightness writes use
HS mode; status queries use LP mode. The matching Android controller catalog
uses the common embedded-command FIFO implementation for DSI v2.2–v2.5.

Primary register/packet references are Nothing's
[panel setter](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/techpack/display/msm/dsi/dsi_panel.c),
[packet construction](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/techpack/display/msm/dsi/dsi_ctrl.c),
[controller catalog](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/techpack/display/msm/dsi/dsi_catalog.c) and
[command FIFO/readback implementation](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/1d557de4d904a76c0b679c746cc77cf9ae89b6c6/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c).

`drivers/gpu/hakios.spacewar.dcs.h` implements that transport with host-supplied
MMIO and microsecond-delay providers. The loader first requires U-Boot's firmware
identity and an FDT identifying both `nothing,spacewar` and
`nothing,rm692e5-spacewar`. Its bounded FDT parser is also checked against the
actual control DTB embedded in the frozen U-Boot binary. Matching screen
dimensions alone cannot enable this profile.

The transport adopts an already enabled, four-lane command-mode pipeline. It
checks the controller version, software trigger, idle command engine, secure
state and error flags before sending commands. It does not initialize the
panel, alter display modes, reprogram clocks or power, or reset the controller
or PHY. It uses the embedded command FIFO, preserves command parameters and IRQ
masks, and acknowledges only its DMA/BTA completions. FIFO cleanup is confined
to the command FIFO. If hardware is still fetching after the bounded timeout,
parameters are left intact and further requests are refused.

Before exposing brightness, it reads panel power mode (`0x0a`) and the current
12-bit brightness (`0x52`). Each brightness write is followed by fresh `0x52` reads and exact raw-value
comparison. Up to six reads, spaced ten milliseconds apart, allow for a panel
scan before declaring a mismatch. A completed, error-free HS write that never
matches may be retried once as an LP packet, which the stock panel init also
uses for DBV. LP is retained only after a matching readback; no clock or display
operating mode is changed. A failed read or mismatch is not reported
as an applied brightness. Controller snapshots retain the failure details for
USB retrieval. CPU0 masks local interrupts around each short command exchange
and restores its prior interrupt state afterwards.

## iOS interface

The matching iOS 16.5 dyld cache establishes that Control Center uses
`BKSDisplayBrightnessIsBrightnessLevelControlAvailable`,
`BKSDisplayBrightnessGetCurrent` and `BKSDisplayBrightnessSet`. CoreBrightness's
`BLControl.findBacklight` matches `backlight-control=true` in IORegistry.

`drivers/gpu/hakios.spacewar.backlight.inc` publishes the native
BrightnessControl interface on the framebuffer: `backlight-control`, one byte
of data for `force-brightness-control`, and `IODisplayParameters` entries for
`brightness` and `BrightnessMilliNits`. `BuiltIn=true` associates the embedded
panel with IOMFB's modern BrightnessControl monitor; without it, that monitor
requires a matching external display container ID. Its setter accepts the framework's
fixed-point `brightness-nits` request. It advertises the new level only after a
successful hardware acknowledgement. A failed request or two-second mailbox
timeout disables the backend instead of silently accepting a level.

In the first native iOS run, the legacy CoreBrightness search matched the
framebuffer but rejected it as `unsupported service`: it expects
`AppleARMBacklight` or an HID device. The matching CoreAnimation cache selects
its modern monitor through `DeviceSupportsFrameSynchronousBrightness` or the
`SynchronousBrightnessTransactions` preference. Its stock value on the phone
was zero. `userland/src/brightness_bridge.c` initializes that stock once-only
configuration, then enables the modern transaction path in backboardd's memory
only when the exact 20F66 code/layout and the ready native built-in service
match. Its service matcher binds a modern brightness container to the real
native framebuffer's parameter service, retaining the service references the
stock container owns. The stock matcher otherwise requires AppleARMBacklight
even for this modern path, leaving a zero service argument and failing module
creation. The bridge preserves the legacy matcher for containers without a
modern proxy. It does not write preferences, override BKS availability, skip the ALS
wait, or alter touch timing. This selects the framework's control interface;
it does not establish atomic frame/brightness presentation on Spacewar.

The existing ABI-v1 mailbox occupies offset 4096 in the touch shared page;
request and completion writers own separate cache lines. CPU0 handles native
DCS between completed touch leases. This preserves the compiled kernel
interface while replacing its initial Mu-dependent transport. The legacy
`HakiosBacklightFirmwarePercent` diagnostic property name is retained for
existing capture tools; the value now comes from native DCS readback.

The interface uses a nominal 500-nit SDR scale from
[Nothing's published normal brightness](https://checkout-eu.nothing.tech/pages/phone-1).
This is a relative mapping, not measured luminance calibration or HDR support.
`backlight=1` remains an explicit opt-in for the approved live-touch profile.

## Focused RAM test

The isolated native bundle is under
`build/spacewar-brightness-backend-20261105/native-dcs-v2/`. Its boot image contains
the frozen working U-Boot and DTB plus the new idle loader. Its staged iOS payload
uses the audited kernel containing the brightness interface and the independently
checked RAM root. The UI kernel preserves the saved working rendering/audio
baseline and adds the backlight interface. The installed boot images and normal
kernel have not been replaced.

The brightness diagnostic uses the previously stable USB profile with touch,
GPU, storage and WLAN enabled; audio DSP, battery probes and sound stay idle
for this RAM test. The loader command line fits 590 of its 607-byte budget.

With the idle loader connected:

- `getvar firmware` confirms the actual boot provider.
- `getvar backlight` validates phone identity and reads controller state.
- `oem backlight-check` queries power mode/current brightness; it sends no
  brightness-setting command.
- Only after positive query results, `oem backlight-test` requests 25%, 65%,
  and restoration of the exact starting raw level. Results and failed
  restoration attempts are logged.
- After successful hardware validation, stage `native-backlight-debug.bin`
  using `tools/spacewar-debug.py` and test both iOS sliders in the same loader
  session. Actual visible response and slider behavior require phone validation.

The RAM root also contains `backlight_check`: after ten seconds it exercises
25%, 65% and restoration through IOKit, independently of the slider frontend.
It does not change brightness preferences. `brightness_probe` saves a bounded
mode-0600 `hakios-debug-XXXXXX` capture under the existing HFS data volume,
including BKS availability/current value, registry properties and filtered
kernel messages. It runs for at most 300 seconds and calls no setters itself.

Host verification: 460 production native DCS/FDT cases under ASan/UBSan,
including the actual frozen U-Boot control DTB; 29 production mailbox/IOKit
cases; and the existing loader live-profile/touch-lease checks. Tests cover
byte order, readback, unchanged clock/power/PHY registers, state refusals,
malformed FDTs, busy engines, errors, stale requests and bounded timeouts. These
checks establish code behavior against providers, not physical panel response.

## Why the Mu transport was stopped

The initial loader backend expected Qualcomm's display power-control protocol.
U-Boot does not provide it, so its first iOS test correctly left brightness
unavailable. The saved log basename was `hakios-debug-EWLbmD`.

An idle Mu build reached USB but used SimpleFb and omitted DisplayDxe. Enabling
the vendor display driver caused an automatic return. A later USB-first test
confirmed `LoadImage` succeeded, then USB disconnected during `StartImage`;
the user confirmed no manual reboot. A diagnostic retry recorded driver base
`0xe0204000` and size `0x4d000` before the same USB loss. No exception report
was recovered, so the exact reset mechanism is unconfirmed. No iOS or brightness
write test ran on those Mu attempts. Evidence is retained privately under the
local build directory. The Mu-only executable-loading and exception-hold
commands were removed from the active loader source.

## Native phone test evidence

The first native U-Boot test passed device/panel identity and controller-state
checks: version `0x20050000`, control `0x1f5`, trigger 4, no ACK/timeout error.
Panel power and DCS52 queries succeeded, returning initial brightness 2048 of
4095. USB remained connected throughout. The first 25% HS write completed but
its immediate fresh read still returned 2048, so verification failed; the exact
starting value was restored and read back. No iOS test started. This establishes
working native queries, but not a successful brightness-setting operation.

The v2 revision adds bounded delayed readback and verified LP packet fallback.
The host cases include scan-delayed application and an inherited pipeline that
ignores HS brightness writes while accepting LP. On the phone, 25%, 65% and
exact restoration to raw 2048 all passed; HS worked without LP fallback. The
restoration used two fresh reads, confirming delayed latch verification was
necessary. The user confirmed visible changes. Inside iOS, the bounded IOKit
self-test also passed 25%, 65% and restoration to 50%. BKS remained unavailable
through 270 seconds and the user confirmed both UI controls still failed.
Private evidence is retained under the v2 native bundle and the Windows USB
capture `phone-ios-001`. Live USB remained usable through orderly shutdown and
staging the registration test in the same resident loader.

The first registration test enabled CoreAnimation's transaction path and
published `BuiltIn`, but display-module creation failed with `invalid arguments`.
The stock modern-container matcher still skipped the framebuffer because it
does not inherit AppleARMBacklight, leaving its parameter service zero. The
second registration revision binds the actual native service to that container.
The display module then initialized, and Settings requests plus Control Center
requests reached the verified native DCS setter. The user confirmed both work.
The unsupported AOD module remains absent; manual brightness does not depend on
it. The early standalone BKS probe retains unavailable/current-1 values after
starting before backboardd, so those samples must not be treated as the live UI
client's state. The slider observations and hardware acknowledgements establish
the successful result. Private evidence is in `native-ui-v2/phone/` and the
Windows USB capture `phone-ui-002`.
