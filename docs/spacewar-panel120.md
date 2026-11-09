# Spacewar 120 Hz experiment: not working yet

This opt-in experiment has not achieved visible 120 Hz. Keep it separate from
claims about the successful tear reduction and compact-canvas smoothness.

The local Spacewar ROM DTB describes RM692E5 command modes at 60, 90 and
120 Hz. The 60/120 PHY timings and DSC descriptions match. The mode sequence
selects page 0x40 (FE 40), writes BD 07 for 120 Hz (BD 00 for 60 Hz), and
returns to page zero (FE 00), with the ROM post-command delays.

## Implementation

`--native-scanout --panel-120` packages fb_native=1, fb_panel_hz=120 and
fb_ca_hz=240. CA's advertised rate is an experimental scheduling request,
not a measured panel rate or proof of 120 rendered frames per second.

The presenter first requires the exact retained controller/stream ownership
checks and an acknowledged native flip. It checks the DSI transport state,
sends the mode sequence and reads panel page 0x40 register BD. Only mode 7
and the previously observed TE word 0x180085 qualify a timing attempt.
The clocks-per-line field changes to 0x43 while preserving enable/external-TE
bits. No PHY reset or DSI link-clock reprogramming is performed.

The hardware frame counter is measured over at least 500 ms. Only 110--130
measured controller refreshes per second activates the 120 Hz software
interval. Otherwise the original TE word and 60 Hz panel sequence are restored.
Clean return to the loader also requests restoration while ownership matches.
A failed DSI transaction refuses further unsafe command traffic.

Register-field reference: Linux DPU command-mode tear-check setup documents
counter enable at bit 19, external TE at bit 20, and clock cycles per line in
the low count field:
[Linux DPU interface implementation](https://git.zx2c4.com/laptop-kernel/tree/drivers/gpu/drm/msm/disp/dpu1/dpu_hw_intf.c?id=de8a334f215c5a5c5bb070df4325af824d7eaaf1).

## Observed phone failures

| Attempt | Observation | Outcome |
| --- | --- | --- |
| Panel mode commands only | Controller measured about 59 Hz | Requested 60 Hz rollback |
| Panel readback plus qualified TE change | BD read back 7; controller measured 39 Hz | Original TE restored; 60 Hz command returned 0 |

The latter is the v6 compact gesture run. Register readback is not proof that
120 distinct frames reached the panel. The timing relationship, retained
external TE/auto-refresh behavior and actual panel scan still need investigation.
Do not promote this flag as working 120 Hz support. The static-wallpaper v7
bundle preserved the experiment's flags, but no new phone result is recorded.

The normal compact reproduction in
[the display report](spacewar-display-fix-20261109.md) omits --panel-120.
Host protocol/policy checks and compilation pass; they do not validate hardware
refresh behavior. All firmware, IPSW assets, generated images and raw logs
remain local to the builder.
