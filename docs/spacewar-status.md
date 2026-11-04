# Spacewar hardware progress

The active scope is functionality already reported working on Redwood. **Seven targets remain for Spacewar:** GPU acceleration, loader-backed storage, audio, haptics, cameras, Wi-Fi and battery level reporting. Battery charging is excluded. Display, buttons, basic CPU/SMP operation and single-contact touch now have Spacewar verification; temperature readings also work here.

The full 18-area inventory below is retained for context. Its broader partial/unverified areas are not the active task count. Bluetooth, cellular service, native USB, general sensors, charging, torch and Metal drawing are deferred under the requested scope. Native UFS takeover is also outside the loader-backed storage target. See [Redwood scope](spacewar-redwood-scope.md).

Using the repository's full 18 feature areas: **5 have basic phone verification; 13 remain partial or unverified**. These are planning areas, with overlapping hardware dependencies.

| Area | State | Evidence / scope |
|---|---|---|
| Display/framebuffer | works | UEFI framebuffer, XNU console and user-reported Darwin home screen at 1080x2400; GPU acceleration unverified |
| Touch | basic hardware works; graphical interaction failing | Physical single-contact DOWN/MOVE/UP:600 frames,346 valid,279 events,0 rejected,138 IRQ edges and clean return. GUI tap became long press/edit mode, then no response; no final log. Multitouch and edge calibration pending. |
| Buttons | works, including GUI volume events | Earlier raw polling passed for power and volume. Current CPU7 GUI snapshot has five complete cycles for each volume key reaching backboardd/SpringBoard; user reports power and volume working. Some startup events were delayed; one dropped SPMI read recovered. |
| CPU/SMP | works | basic seven-core SMP on physical CPUs 7-1 verified, delayed little-core startup, RAM-root hello as PID 1 and clean timed return; sustained load and frequency control untested |
| GPU acceleration | unverified | No Spacewar phone test recorded |
| Native UFS storage | unverified | RAM-root/APFS works; native UFS driver remains unvalidated |
| Audio | unverified | No Spacewar phone test recorded |
| Battery/charging | unverified | No Spacewar phone test recorded |
| Haptics | unverified | No Spacewar phone test recorded |
| Cameras | unverified | No Spacewar phone test recorded |
| Torch | unverified | No Spacewar phone test recorded |
| Wi-Fi | unverified | No Spacewar phone test recorded |
| Bluetooth | unverified | No Spacewar phone test recorded |
| Cellular | unverified | No Spacewar phone test recorded |
| Network integration | unverified | BSD stack/loopback works; native interface and userland integration remain unvalidated |
| Temperature sensors | works | all 27 sensors polled with changing 39.5-43.0 C readings and user-space sysctls; 105 C stop remains untested |
| Native XNU USB | unverified | UEFI fastboot works; native XNU USB remains unvalidated |
| Other sensors | unverified | No Spacewar phone test recorded |

CPU frequency control, sustained load, thermal emergency-stop behavior and longer GUI stability remain additional work within areas that already have basic verification.

The 2026-11-04 live-log GUI run now has a frozen working baseline under
`build/spacewar-live-gui-working-baseline/`: exact payload/kernel/root/loader,
phone log, host transcript and checksums. All36 bootstrap and80 surface
checks passed; SpringBoard, backboardd and mediaserverd ran without a recorded
crash in this snapshot. The user confirms wallpaper and physical keys.
CPU7/SMP7 leaves CPU0 with USB, physical touch off, and no scheduled kernel
cutoff. The snapshot covers about124 seconds after launch, not sustained
stability or final return. Physical-touch integration remains unresolved.

Touch is the active priority. The combined test image is built, independently reviewed, copied with `./copy` and destination-verified in `C:\platform-tools`. It batches proven power setup, clock/ownership checks, GPIO SPI, controller identification, the ROM-matched M146 volatile firmware uploader and 25 seconds of FT12 touch polling. The first physical run stopped at the clock gate check before SE access and returned cleanly; the AHB readiness check has been corrected to match the vendor HWCG logic. The replacement `spacewar-touch-clockfix-payload.bin` is built, reviewed, copied and destination-verified; its physical test remains pending. Physical SPI, firmware and finger-event verification remains pending; the count above has not changed. See `build/spacewar-touch-combined/manifest.json`.

The clock-fix phone run passed the corrected gate check and read three stable GENI snapshots: SPI firmware `0x102`, master/slave inactive, DMA debug/FIFO/interrupts/lengths zero, with retained DMA mode `1`, GSI enable `0xf` and disabled FIFO interface. The GPIO ownership guard stopped before pinmux or controller traffic; its treatment of retained configuration is under review. Evidence: `logs/spacewar-touch-clockfix-idle-stop.log`.

The replacement `spacewar-touch-gpi-payload.bin` adds read-only external DMA channel/queue checks before GPIO SPI while preserving the retained GENI configuration. It is built, peer-reviewed, host-tested, copied with `./copy` and Windows checksum-verified. Controller identity and physical touch remain pending. Manifest: `build/spacewar-touch-gpi/manifest.json`.

GPIO SPI and all external GPI idle checks have now passed on the phone. Runtime A3/9F returned CRC-valid `EF:EF`; ROM identification and firmware/input remain pending. The copied, destination-verified `spacewar-touch-recovery-payload.bin` handles that recovery candidate while retaining the exact ROM `56:62` gate. Current work is root-only on touch; other agents are stopped.

The firmware path is now phone-verified. `spacewar-touch-pollfix-payload.bin` handles FF startup reports and provides45 seconds for gestures after the measured53-second upload, with120-second return. It is built, host-tested, copied and Windows checksum-verified. Input events remain pending.

Touch hardware passed. The exact working kernel/payload/source/log are preserved under `build/spacewar-touch-working-baseline/`. The first CPU0 graphical test reached the home screen after about 2.5 minutes, but touch did not respond; its 60-second window likely expired before then. The extended 300-second touch/390-second return run reached iOS but taps became a long press/edit mode, then touch stopped. The user also reports missing wallpaper and no return after the deadline; no final log was recovered. Buttons are deliberately off in this profile. The new continuous payload removes both scheduled cutoffs and adds polling summaries; its phone behavior remains unverified. See [continuous mode and current limitations](spacewar-userland-touch.md).
