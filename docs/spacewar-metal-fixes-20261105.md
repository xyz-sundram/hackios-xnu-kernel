# Spacewar Metal fixes and canvas correction

The user confirmed **“Smooth and everything fits”** with the compositor at
750 × 1624 pixels, scale 2, and UIKit at **375 × 812 points**. The phone test is
`build/spacewar-metal-fixes-20261105/phone/scale2-cache/`. Control Centre and the
dock fit again without returning to the more expensive 1125 × 2436 canvas.
All changes were tested through RAM payloads; no boot partition was flashed.

The subsequent charging work removes the automatic still-wallpaper change at
the user's request. The user confirmed normal wallpaper and visible battery
percentage with that rollback. Both normal Metal UI profiles now retain
`adsp=1 battmgr=1`; standalone GPU probes remain DSP-idle. See the
[charging record](spacewar-charging-20261105.md) for the current bundle and
charger validation status.

## Why the smaller canvas was zoomed

The original combined candidate changed the framebuffer and device-tree scale
to 2, but the installed data volume retained MobileGestalt's scale-3 answer.
Backboardd therefore reported 750 × 1624 at scale 3: only 250 × 541.33 points.
Restoring 1125 × 2436 fixed layout, and the user reported that the lag returned.
Freezing the wallpaper helped slightly, but the user requested the smaller
canvas with correct placement.

The reduced-canvas test established the stale cache directly:

```text
display_info: scale cache 3, device tree 2
StartWindowServer: MG scale:2 display preferredScale:2
StartWindowServer: initial main screen size:{750, 1624} scale:2
globalBounds: {{0, 0}, {375, 812}}
```

`userland/src/display_info.c` checks the board, both device-tree scale
properties and actual framebuffer dimensions before retiring a mismatched
saved answer. It renames the bounded regular cache file to a unique backup in
the same directory, preserving its contents, permissions and owner. It rejects
symbolic links. A matching saved answer needs no file change. The cache is
`/private/var/containers/Shared/SystemGroup/` followed by
`systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist`.

MobileGestaltHelper rebuilds an absent answer after launchd starts. The first
test queried before that helper existed, so immediate verification reported
no answer; the subsequent window-server logs established successful scale-2
regeneration. The final helper distinguishes that pending state from an
invalid scale. A separate read-only startup check verifies the answer in fresh
processes, avoiding a retained library cache.

The final package was restarted successfully. The warm boot reported
`scale cache 2, device tree 2`, made no cache backup, and verified
`750x1624 pixels at 2x = 375x812 points`. Backboardd independently reported
375 × 812-point bounds. All three compositor alpha modes and deferred-recording
checks passed again. Evidence is `phone/final-warm-scale2/`; this build was left
running on the phone.

The native builder includes the helper and its source/binary hashes. The
package repairs the cache before UI initialization and verifies alongside
launchd. It refuses a reduced-canvas kernel without the matching helper.
Kernel and boot root must be kept together. Normal Spacewar rendering uses
750 × 1624; explicit scale 3 remains supported. Resampling to the physical
1080 × 2400 panel preserves layout with fewer rendered pixels and lower sharpness.

## Delivered stack changes

| Cause | Change and evidence |
| --- | --- |
| Presentation delay | Normal Metal profiles retain `fb_beat=0`, already confirmed noticeably smoother by the user. |
| GPU workload | GMEM threshold 16 and deferred recording retain the feedback-ordering fix. Scale 2 has 44.4% of the scale-3 pixel count. The automatic still-wallpaper change was subsequently removed; wallpaper uses its original behavior. |
| Touch cadence and release | FT12 honors bounded polling, uses 1 ms active polling, reads active contacts despite a missed IRQ pulse and uses a 16 ms idle fallback. SPI delays use the hardware counter. The first physical combined capture measured changed-coordinate intervals around 3.31 ms median and final MOVE-to-UP around 3.28 ms, replacing roughly 23 ms samples and a 100 ms release gap. |
| Shader cache stalls | Cache I/O has a separate mutex from Mesa compilation. A hit can finish during another compilation; a miss rechecks after taking the compiler lock. Deterministic tests reject both old locking and an omitted recheck. |
| Missing compositor draws | Padded constants at a valid buffer tail use bounded GPU copies and zero padding, preserving GPU-produced values. New phone readbacks cover 16-, 20- and 48-byte allocation tails; the full GPU probe passed 285 checks. Previously refused UI draws disappeared in the combined capture. |
| Surface reuse and scaling changes | The presenter takes immutable per-layer snapshots under the GPU submission lock, rechecks fences after waits and drops an unfinished source. Spacewar uses stable filtering and avoids delayed settle reads of a reusable live surface. |

The earlier positive combined-build report did not establish correct geometry;
the later zoom report exposed the stale-scale defect. The scale-cache test
above is the one confirmed smooth with correct geometry.

The full-size still-wallpaper capture had a median SwapEnd-to-ready interval
of 11.65 ms; the coherent scale-2 capture had 3.34 ms. Median host submit/fence
intervals for Metal callbacks in gesture windows were 14.84 and 8.07 ms.
These captures contain different physical gestures and are diagnostic
evidence, not a controlled speedup benchmark. GPU waits, command-buffer
callbacks and framebuffer copies do not measure physical scanout rate or
prove every frame meets a 60 Hz deadline.

## Validation and remaining presentation work

The renderer passes **264 host tests**, including compiler concurrency and
constant-tail staging. The same rendering core passed **285 on-phone GPU
checks**, and the coherent scale-2 UI test passed all three nine-case compositor
alpha/readback modes. The production touch/snapshot regression runs under
ASan/UBSan and rejects the old missed-release and stale-fence behavior. The
display-scale regression executes real file backups and production coherence
guards, including first-boot regeneration and unchanged-cache cases.

The command-mode panel was measured around 60 Hz. Its row counter did not
consistently pass strict linear-scan qualification, so guarded row
synchronization refused to activate. An earlier native-scanout prototype using
additional splash-reservation addresses hung before its first acknowledged
flip and required a fresh stock-fastboot RAM boot. Its exact failing operation
was not isolated. It is not a working scanout fix.

The source contains a separate owned-buffer prototype with a cached CPU
mirror, bounded address checks and a boot-buffer restoration requirement.
It compiles, but allocation and physical flips remain unvalidated. Both
`fb_native` and `fb_scan_sync` default off and are absent from the live-touch
loader allowlist. The smooth canvas build uses the established framebuffer
copy path. Physical scanout synchronization remains open.

Artifacts, manifests, source snapshots and private captures are under
`build/spacewar-metal-fixes-20261105/`. Final reproducible inputs are
`kernel-final/`, `native-final/` and `ram-bundle-final/`. Earlier `layout-fixed/`,
`static-wallpaper/` and `scale2-cache/` artifacts preserve the comparison and
diagnosis. Those historical graphics diagnostics disabled DSP battery transport
to retain USB. Current Metal UI packages enable measured battery reporting in
both UI profiles; ADSP-related USB loss is tracked in the charging record.
