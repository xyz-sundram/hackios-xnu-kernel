# Phone (1) audio and haptics bring-up

The Phone (1) has NXP TFA9873 amplifiers and a PM8350B haptic controller. The
common Redwood drivers target Awinic AW88261 speakers and an AW86927 haptic
controller. Matching I2C addresses do not make those register tables compatible.

Android's live kernel log confirms both TFA9873 devices at addresses 0x34/0x35
on QUP02 (GPIO 8/9), with reset GPIO 101. Its sound card uses `PRI_MI2S_RX`, stereo
S32 at 48 kHz. The old sound driver instead uses the primary TDM port 0x9000 and
Awinic ACF data. That Redwood playback path is not selected on Phone (1).

The PM8350B haptic controller is SID 3, peripherals F0/F1/F2. The live Android
probe identifies configuration revision 2, pattern revision 2 and boost revision 1.
The Phone (1) DT specifies 5880 µs LRA period; Android reports a calibrated 5798 µs.
Its haptic boost command scratchpad is PMK8350 SID 0 SDAM 9D00.

A subsequent XNU RAM probe also read revision 0B73 from both NXP devices on the
correct pins, and CFG 2 / PTN 2 / BOOST 0001 from the haptic controller. It found the
boost peripheral owned by AOP and the configuration/pattern/SDAM peripherals
writable by HLOS. Firmware left SoundWire access at E0 with SPMI PLAY disabled.
That probe establishes identity/ownership, not audible or tactile output.

The later RAM test completed the bounded 40 ms, 12.5% SPMI pulse, returned PLAY
and module enable to off with no fault, and the user confirmed feeling the
vibration. Keyboard feedback through the runtime endpoint needs its own test.
A following runtime test confirmed Darwin keyboard PCM reaches the endpoint,
but the PMIC backend subsequently stopped with a fault. The updated backend
logs the specific failure and waits 8 ms for a waveform's peak, then uses the
verified amplitude-32/40-ms minimum for a tap. Its voltage, maximum amplitude,
maximum duration and rest limits remain in force. That update awaits a phone test.
It also follows the matching Nothing driver's enabled RichTap variant: set
`FORCE_VREG_RDY` (CFG68 bit0) before PLAY and clear it on stop; accept an enabled
open-loop boost when BOOST41 bit7 is clear and BOOST46 bit7 is set. Only in that
state, PM8350B toggles automatic RC calibration from AUTO to disabled and back.
These sequences come from
[`haptics_wait_hboost_ready` and `haptics_open_loop_drive_config`](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/539538747842faa8e22145ac0b557b1c905f8979/drivers/input/misc/qcom-hv-haptics.c#L1440)
with `RICHTAP_FOR_PMIC_ENABLE` defined in the same source. The backend reads the
boost state and writes only HLOS-owned configuration/pattern/SDAM registers.
It preserves TLRA configuration and treats a hardware fault as terminal.

These facts come from the extracted Phone (1) DT/mixer and live Android startup
log. Register definitions and command ordering follow the
[matching Nothing Qualcomm driver](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/539538747842faa8e22145ac0b557b1c905f8979/drivers/input/misc/qcom-hv-haptics.c)
and [NXP TFA driver](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/tree/539538747842faa8e22145ac0b557b1c905f8979/techpack/audio/asoc/codecs/tfa98xx).

## Implemented paths

- ADSP loading uses the Phone (1)'s 40 MiB reservation at 0x86700000–0x88F00000,
  followed immediately by CDSP. It rejects load/BSS overlap and segments outside
  that reservation before firmware writes or PAS calls. The signed ADSP files
  extracted from this phone's verified modem-A backup fit that reservation
  exactly, including the final BSS segment. Firmware stays private.
- `speaker_probe=1` observes Phone (1) audio pins. `speaker_probe=2` also reads
  only NXP revision register 0x03 at 0x34/0x35, preserving the I2C pin settings after each
  transaction. It does not toggle reset or write amplifier configuration. The
  device-specific write/ACF gates reject the old Awinic setup.
- The PM8350B backend uses `haptic=1` or2 for a read-only register/ownership probe,
  3 to configure without playing, 4 for one 40 ms low-amplitude pulse, and 5 for the
  Darwin Actuator PCM endpoint. All output is capped at 3600 mV and 25% direct-play
  amplitude, with 250 ms output and at least 250 ms rest. The first pulse uses 12.5%.
  Register writes require the matching PMIC family and HLOS-owned arbiter
  channels for CFG, pattern and SDAM; the AOP-owned boost block is read only.
  The firmware's SoundWire input is excluded while SPMI owns playback and
  restored after the module is stopped. A fault or failed readback stops output; cleanup attempts PLAY off,
  module off and the matching boost-off request independently.

`CONFIG_DEVICE="spacewar"` selects these paths at build time. A runtime argument
cannot select the Phone (1) wiring in a kernel built for another device.
`hakios_debug=0` keeps the new audio/haptic display diagnostics quiet while
retaining debugging code and USB loader logs.

## Phone (1) playback implementation

The new `SOUND` path uses PRI_MI2S RX port 0x1000, SD1, stereo 32-bit 48 kHz,
internal word select and clock 0x100 at 3.072 MHz with the matching non-inverted
clock attribute. It does not configure the Redwood TDM group. An attempted
start always gets an explicit stop during cleanup, including when its reply was
lost.

The NXP backend requires both chips to identify as revision 0B73 before any
configuration writes. It follows that revision's nine optimal settings from the
matching Nothing driver and validates the exact 3,745-byte ROM container and its
CRC before applying the `speaker` profile. Profile parsing rejects unknown
operations and cannot select amplifier-enable, OTP/MTP-copy or calibration-start
commands. The temporary initialization keys are closed after preparation.
Faults, missing clocks and ADC-ready numeric die readings outside -40 to 79 °C
stop both amplifiers. Unavailable temperature still prevents general continuous
playback. The bounded diagnostic policies below require additional native
protection checks and do not assign an invented Celsius value.

The private `fw/tfa98xx.cnt` is read from the boot payload first, with `/fw` on
the stored system as a fallback. Boot-payload data is copied into an owned
buffer before the sound driver validates and releases it. This permits testing
the matching profile without replacing the installed system image.

The initial output has an unconditional 30 dB PCM attenuation, applied to test tones
and Darwin samples before DMA. Boot arguments and volume controls cannot remove
it. Normal Darwin volume still scales its PCM. This is an initial output limit,
not proof that speaker protection or long-term acoustic calibration is complete.
Requests for further attenuation use saturating integer eighth-dB scaling;
muting zeros the DMA PCM. The optional fed recording captures the final attenuated
samples, so it matches the samples sent to the DSP.

With the matching firmware in the private payload, `adsp=1 sound=1` tests only
the port/clock, `sound=2` adds stream/DMA with amplifiers off, `sound=3` plays a
bounded low-level tone, and `sound=4` connects Darwin playback through the bounded
Phone (1) diagnostic described below. Enable successive
levels only after the preceding phone result and clean stop have been verified.
Levels 3/4 require an additional 120 ms zero-PCM DMA preflight before any NXP
configuration. Each completion must name an outstanding page, its mapped
address and handle, and report success. Missing, malformed, duplicate or refused
completions prevent amplifier configuration.
The bounded diagnostic tone has been heard on the phone. v17 also established
that real Darwin PCM reaches the amplifiers, with output ending at its
intentional three-second lease. The user subsequently confirmed that v19
continuous audio works, with low volume. Its explicit `sound_cont=1` opt-in
retains the fixed `/32` PCM ceiling. Progress is saved at this limited level;
full-volume calibration and long-term speaker protection are not established.

### Measured audio progress

The following checkpoints are RAM test labels from 2026-11-04. Their private
captures were recovered and validated; they are not release tags.

| Checkpoint | Measured result |
|---|---|
| v12 | All eleven ADSP stages, including APR service discovery, passed. Sound stopped before pin or amplifier writes because its profile lookup checked only `/fw` on the stored system. |
| v13 | The owned boot-payload profile lookup read all 3,745 bytes. AFE bit-clock/config/start replies passed, with clock and frame sync observed on GPIO97/100. ASM mapping, opening, routing, format and run replies passed. The zero-PCM preflight returned all 15 submitted periods, with zero errors and underflows. Both NXP chips accepted preparation; output then stopped without a sufficiently specific amplifier diagnostic. |
| v14 | Added diagnostics established that both amplifiers passed power-up/configuration/enable readbacks and reported stable clocks (`status10=0x57`). The next health check rejected raw temperature register `0x100`, interpreted by the old scalar check as signed-nine-bit -256. No tone was submitted. |
| v15 | A startup-only 50 ms readiness budget did not change that raw reading. Both chips reported manager register `0x4f`, whose state field is 9, while temperature remained `0x100`. Output stopped. The readiness hypothesis did not resolve the failure. |
| v16 | The user heard the bounded tone, with battery percentage visible. Its recovered log shows all 159 periods returned, zero DMA errors or underflows, 15 healthy amplifier checks and clean `LEVEL DONE` cleanup. Both chips passed the native protection snapshot below. Ordinary app playback is not established. |
| v17 | Real Darwin PCM reached the sink, amplifiers started 26 ms later and native checks passed. No NXP fault was logged. The intentional lease expired after 3,003 ms, with 146,432 accepted and 145,472 fed frames. The user heard brief output. |
| v18 | Added `sound_cont=1` in the audio code and packaged successfully, but the older loader refused this unknown service argument before starting XNU. No v18 audio run occurred. |
| v19 | Added `sound_cont` range 0–1 to audio service profile 2 and used a matching rebuilt EFI loader, retaining the exact v18 audio code. The user confirmed that continuous audio works, with low volume. The fixed `/32` PCM ceiling remains in place. |

The v14/v15 logs were exported through the
[dedicated fastboot recovery image](spacewar-log-recovery.md), without booting
Android or copying the full data partition. These results establish DSP routing,
DMA completion and amplifier configuration separately from audible playback.

### v16 tone policy (historical checkpoint)

This subsection describes the saved v16 tone build. The current v17 policy
below supersedes its raw-value classification and adds a separate, mandatory
one-shot app-playback lease.

The matching Nothing TFA9873 code checks clock stability for startup and uses
the normal manager-controlled operating path. It does not read numeric `TEMPS`
as a startup prerequisite. Its status logic treats `OTDS`/`flag_otpok` as healthy
when **1**, while `OCDS` is an active-high overcurrent alarm. See the pinned
[TFA9873 initialization](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/539538747842faa8e22145ac0b557b1c905f8979/techpack/audio/asoc/codecs/tfa98xx/tfa_init.c#L715),
[operating state and status checks](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/539538747842faa8e22145ac0b557b1c905f8979/techpack/audio/asoc/codecs/tfa98xx/tfa_dsp.c#L3839),
and [B0 register definitions](https://github.com/NothingOSS/android_kernel_msm-5.4_nothing_sm7325/blob/539538747842faa8e22145ac0b557b1c905f8979/techpack/audio/asoc/codecs/tfa98xx/tfa9873_tfafieldnames_B0.h#L478).

Raw `0x100` is recorded as **UNKNOWN**, never as a usable temperature. The source
does not document it as a special sentinel; the diagnostic infers unavailable
ADC data only when the B0 `flag_adc10_ready` bit is also clear. This exception
applies only to `sound=3` with a requested or default `sound_secs` no greater
than 3, and retains the immutable `/32` PCM ceiling. `sound=4` still rejects it.
An ADC-ready reading of `0x100`, another out-of-range numeric reading or any I/O
failure remains terminal.

The actual v16 startup snapshot on both chips was `status10=0x56`,
`status13=0x810f`, `manager14=0x4f`, `ctl00=0x4218`, `protectB0=0`,
`directC0=0`, `lp6e=7` and `temp16=0x100`. ADC-ready remained 0. About 207 ms
after enabling output, both raw temperature registers changed to 0 while
ADC-ready was still 0. The v16 diagnostic printed a numeric zero at that point;
**it is not a verified 0 °C die reading**. v17 keeps every ADC-not-ready sample
UNKNOWN regardless of its raw value. This evidence establishes native
protection checks and a completed bounded tone, not an available numeric sensor.

Before this bounded exception is accepted, both chips must have the exact B0
identity, the known profile and all of these read-only checks:

- Healthy clock, thermal, boost-overcurrent, undervoltage and overvoltage flags;
  no overcurrent, manager, lost-clock or TDM alarm.
- Manager state 9, with amplifier/converter enabled and power-down clear.
- OCP bypass and OCP test disabled; the OCP-counter, OVP, UVP, OTP and lost-clock
  protection bypass bits all clear; direct control disabled.

Each snapshot logs its existing status/configuration values, ADC-ready state
and LP0/LP1 state. It does not write sensor, ADC, direct-mode or test controls,
and does not clear a fault through retry. The failed v15 temperature wait is
removed.

Unknown temperature means the supplemental 80 °C numeric cutoff cannot be
established. Hardware OTP is a different limit: the manufacturer's
[TFA9873 SDS, revision 1.2](https://www.goodix.com/en/docview/TFA9873%20SDS_TFA9873_SDS_Rev.1.2?objectId=87&objectType=document&version=273)
specifies a minimum thermal-protection activation temperature of 130 °C
(printed page 15; PDF page 17). This protects the amplifier IC and does not
establish stock SpeakerBoost coil-temperature or excursion protection. The
three-second output bound and fixed PCM ceiling remain necessary for this
diagnostic; v16 does not claim ordinary music playback or complete speaker
protection.

### v17 bounded app-playback policy (default)

The v17 `sound=4` test connects the existing Darwin Speaker endpoint to the
verified PRI_MI2S/NXP path. It retains the fixed `/32` ceiling, 200 Hz high-pass,
peak/power guard, all native protection gates, terminal-fault behavior and
200 ms health checks. `sound_secs` defaults to 3 and is capped at 3 in the
Phone (1) kernel. Zero disables amplifier activation.

There is one amplifier lease, starting with the first successful start call;
its deadline conservatively includes that call's I2C/readback time. An initial
mute leaves it unarmed. After the first start, mute, silence, volume changes and
new PCM cannot start it again. Expiry explicitly stops both amplifiers, turns
the guard off and prevents rearming. PCM continues draining until ordinary
cleanup. The existing silence power-down is retained. The watcher logs the
first nonzero PCM batch and lease start/expiry, so an earlier system sound that
consumes the window can be distinguished from the selected app.

All ADC-not-ready raw values, including `0x100` and 0, are **UNKNOWN** in this
diagnostic. They are permitted only with every native protection gate satisfied
and either the bounded `sound=3` tone or the one-shot `sound=4` lease. Only an
ADC-ready sample can be decoded as a temperature; its numeric limits and invalid
`0x100` rejection remain in force. No sensor, ADC, test-mode or direct-control
register is written. Continuous app output is not enabled by this default
policy.

The recovered v17 log establishes the cutoff's cause: first nonzero Darwin PCM,
successful amplifier activation and native checks, followed by lease expiry
at 3,003 ms rather than a hardware fault. It also observed the left ADC-ready
bit set with raw `0x28`, a valid 40 °C sample. Later, the same raw value with
ADC-ready clear was correctly UNKNOWN; a cached number cannot establish a
current die temperature.

Use `AUDIO_DEBUG_MODE=playback` with the
[RAM diagnostic build target](spacewar-log-recovery.md). Its packager requires
the matching kernel manifest to attest to the three-second one-shot policy;
it refuses an earlier unrestricted kernel. This is a test of actual Darwin PCM
routing and audible output, not stock SpeakerBoost.

### Continuous low-level opt-in (v18 audio, v19 loader policy)

`sound=4 sound_cont=1` explicitly removes the diagnostic lease on Phone (1).
Only the exact value 1 enables it. Omission, 0 or another value retains the
bounded default; other modes and other devices do not gain this opt-in.
`sound=3` remains a tone of at most three seconds regardless of the flag.

Continuous means playback can keep running while Darwin sends PCM. It retains
normal mute/unmute behavior, powers both amplifiers down after three seconds
of silence and restarts when new sound arrives. Native faults remain terminal;
new PCM does not restart output after a fault. The fixed `/32` ceiling, 200 Hz
high-pass, peak/power guard, 200 ms native health checks and all private-profile,
identity, DMA and protection gates remain in force. ADC-not-ready data stays
UNKNOWN; ADC-ready numeric limits remain enforced. No sensor, ADC, direct/test
or MTP register is newly written.

Use `AUDIO_DEBUG_MODE=continuous` with the public RAM build target. Its manifest
must attest to `spacewar_continuous_playback_flag: 1` as well as the default
three-second bounded policy. The profile retains Wi-Fi and battery reporting.
It omits redundant `wifi_watch=0`: the `wifi=1` whole-radio path already sets
watching to 0 and returns before parsing that diagnostic-only argument.

The loader and kernel must use the same service-policy header. `sound_cont`
is allowed only in audio service profile 2, with values 0 or 1; the 18/19 driver
counts and existing profile requirements are unchanged. The loader validates
raw tokens, including duplicate and malformed-number refusal, before the
kernel's parsed-value checks. The public packager checks the actual production
loader policy against the full command line, the loader's support for the
token, and the kernel's matching policy hash. It refuses the old EFI that
blocked v18. Boot the newly packaged v19 RAM image to obtain the matching loader;
staging its payload into the older loader cannot add policy support.

This opt-in keeps the deliberately limited output level. Stock host-DSP
speaker-coil calibration/protection has not been integrated. The user confirmed
working continuous playback at low volume in v19; extended runs, full-volume
calibration and long-term acoustic behavior remain unverified. No gain or
protection limits were raised after that result.

The optional `audio_health` diagnostic records new audio/haptic kernel log lines
and one-second battery/process health to a private file in `/private/var/log`.
It reads log text with libproc's `proc_kmsgbuf`; `kern.msgbuf` is an integer
buffer-size control in this XNU and cannot provide the log contents. API failures
are recorded immediately and every ten seconds, including their numeric errno.
The helper must run as root: use the diagnostic `RC=rc.audio-health` profile
with the compiled helper supplied through `EXTRA`. This starts it from PID1
before stock launchd; the ordinary demo job runs in the mobile user session and
cannot read the kernel log. Captures record PID, real UID and effective UID.
It handles ring-buffer overlap without repeating old snapshots, fsyncs each
cycle, and limits the capture to 180 seconds and 2 MiB. It changes no hardware
controls. Kernel log wrap between samples can still lose short-lived messages;
the loader's complete log remains useful after a clean return.

## Validation

The host checks execute the actual C routines against bounded hardware/file
models, using AddressSanitizer and UndefinedBehaviorSanitizer:

```sh
python3 tools/spacewar-adsp-test.py
python3 tools/spacewar-adsp-battery-link-test.py
python3 tools/spacewar-haptic-test.py
python3 tools/spacewar-speaker-test.py
python3 tools/spacewar-nxp-test.py
python3 tools/spacewar-nxp-activation-test.py
python3 tools/spacewar-audio-firmware-test.py
python3 tools/spacewar-audio-route-test.py
python3 tools/spacewar-audio-dma-test.py
python3 tools/spacewar-audio-pcm-test.py
python3 tools/spacewar-audio-playback-test.py
python3 tools/spacewar-services-policy-test.py
python3 tools/spacewar-audio-health-test.py
```

They cover 13 firmware-validation cases, 12,288 battery-link lifetime cases,
1,057 PM8350B backend cases, 524 NXP probe/write-gate cases, 389,679 NXP
profile/backend/PCM cases (including the optional private-ROM check), 14 AFE
route/cleanup cases, 14 ASM completion/preflight fault cases, 200 Darwin producer
cases, 10,307 activation/protection/mode/duration/temperature matrix cases,
10 firmware-lookup ownership/fallback cases, and diagnostic framing/capture-cap
cases. The activation matrix executes the actual backend and covers all 512
nine-bit temperature encodings, both ADC-ready states, each native protection
gate on either chip, modes and requested durations. Another 32 cases execute
the actual Darwin sink, watcher and kernel boot-duration clamp, including
initial/post-start mute, silence, lease expiry, slow initialization, no rearm,
continued draining, 20 seconds of modeled continuous playback, mute/silence
restart, terminal-fault behavior, exact opt-in semantics and unchanged
non-Phone (1) watcher behavior. The focused service-policy test adds 27 actual
loader/kernel-chain cases for
the opt-in's values, duplicates, malformed numbers and profile boundaries.
Hardware output and
integration results are recorded separately; host checks alone do not establish
audible playback or felt haptics.

The read-only haptic and NXP probes, startup haptic pulse and bounded v16 audio
tone have passed on the phone. The user heard the first PRI_MI2S/NXP tone while
battery percentage remained visible. v19 continuous app audio was subsequently
confirmed working at low volume. The fixed output limit remains; stock host-DSP
speaker protection, full-volume calibration and extended validation remain
separate. The old Awinic/TDM configuration must not be enabled as a substitute.
