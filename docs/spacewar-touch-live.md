# Physical touch with seven cores and USB logs

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The new `spacewar-userland-touch-live-payload.bin` adds physical touch to the
CPU7/SMP7/buttons-on profile that displayed wallpaper and supplied live logs.
Both `touch_seconds=0` and `loader_return_after=0` disable scheduled cutoffs.
The first phone run reached iOS but touch did not initialize. A diagnostic
retry captured the cause: the driver's20-second seven-CPU wait ended before
GenericARMPE's default28-second delayed startup of the slower cores. The
updated payload waits up to60 seconds without relaxing ownership checks and
mirrors driver diagnostics into live logs. The86-case helper test now covers
cores joining after28 seconds. Diagnosis and corrected build:
phone-fix manifest (`build/spacewar-touch-live-phone-fix/manifest.json`).

It requires the matched `xnu-spacewar-touch-live-debug.img`, which contains a
new XNU loader. The vendor UEFI firmware, boot shim, current-ROM DTB, iOS
system/root images, touch firmware and packet decoder match their preserved
baselines. The existing touch-off GUI baseline remains under
`build/spacewar-live-gui-working-baseline/`.

From **stock Android fastboot**, in `C:\platform-tools`:

```powershell
.\fastboot boot .\xnu-spacewar-touch-live-debug.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-touch.ps1 -LiveLog -Touch
```

If the previous GUI run is active, stop its PC collector with Ctrl+C, request
`fastboot oem stop`, then return the phone to stock fastboot before loading the
new image. The resident xnuloader does not implement the stock `fastboot boot`
command. The launcher checks `getvar touch-lease` before staging and refuses
an incompatible loader.

Firmware upload can take about a minute. After the home screen appears and
the live log contains `TOUCH WINDOW: verified runtime`, try a short Settings
tap and release, then a swipe, and check volume/power. `TOUCH READY` confirms
the first valid report after touching. The collector saves the latest
successful snapshot every15 seconds to
`spacewar-userland-<timestamp>.live.log`; its `.fastboot.txt` file records host
commands separately. `FT12 DOWN`/`UP`, `tl: p`/`tl: i`, and five-second
`FT12 progress` lines help distinguish a missing controller release from
delayed delivery. A failed snapshot download preserves the previous file.

After touch is ready, `fastboot oem stop` from another terminal requests
return. For this first test, collect initialization evidence before requesting
stop rather than interrupting partial setup.

## Coordination and remaining limits

The previous touch path required CPU0 alone to exclude simultaneous UEFI
hardware access. The new opt-in profile uses an isolated shared page with
versioned request/grant/release generations on separate cache lines. CPU0
masks its interrupts and pauses firmware calls while XNU owns the power,
clock, pin or complete SPI transaction. USB resumes between transactions,
including between firmware packets. Existing RSC, GENI, GPI, pin and firmware
identity checks remain in place. Buttons use distinct SPMI observer channels.

The loader and kernel require the exact seven-core/buttons-on profile and
approved driver set. The separate [GPU/touch profile](spacewar-gpu-touch.md)
adds Adreno at315MHz with a matched updated loader and serialized startup.
An unavailable or invalid grant stops touch before any
new hardware write. Acknowledged ownership is never forcibly expired while
XNU could still be driving the pins. Consequently a hard hang inside a granted
transaction can also block USB; this coordination does not guarantee logs
from every possible failure.

Return cleanup runs only after proving that peer CPUs cannot resume their
interrupted work. It checks owned pin modes, obtains or takes over the valid
grant on the boot CPU, sets CS high/clock low/reset low, and releases CPU0.
If ownership cannot be established, cleanup leaves the pins alone and reports
the failure. This path is host-tested, not yet phone-verified.

Validation includes85 production-helper ASAN/UBSAN cases;37 legacy and42
live-model production GPIO-wire cases; strict loader-profile/cancellation/
fairness tests; peer-quiescence tests; generated-board guards; Windows
PowerShell5.1 mock startup/collection tests; and ARM64 kernel/loader builds.
Only the existing kernel kext symbol-set export checks are accepted by the
build wrapper. Packaging checks verify exact entries, RAM fit and unchanged
vendor firmware/DTB/root/decoder bytes.

Exact sources, images and verification:
`build/spacewar-touch-live/manifest.json`.
