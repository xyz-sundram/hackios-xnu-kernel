# Phone (1) status-bar camera clearance — partial checkpoint

Work paused at the user's request on 2026-11-04 to prioritize U-Boot.

The current adjustment moves the clock to the right of the Phone (1) camera and
keeps it visible on the home screen across screen off/on transitions. The user
confirmed those results. The clock is still missing inside Settings and Apple
Music, including in dark mode. App status bars remain unresolved; this is not a
completed UI fix and is not the verified original `platform-tools/final` bundle.

The source is `userland/src/inject_sb.c`, `userland/src/spacewar.cutout.h` and
`tools/spacewar-ui-inject.py`. It uses the AOSPA Phone (1) overlay's conservative
top-left exclusion rectangle: 187.83 × 135.65 native pixels on the 1080-pixel-wide
panel. The source and values are pinned in the header to
[AOSPA commit 6e4ad6b](https://github.com/AOSPA/android_device_nothing_phone1/blob/6e4ad6b994a51348104acf3b1c22162ccde9da21/overlay/Phone1Frameworks/res/values/config.xml).
UIKit coordinates are logical points, so the horizontal bound is scaled and
rounded upward. UIKit's original transform is restored before layout; only the
translation still owned by this hook is undone.

The hook is gated by `touch_spacewar=1` and the exact iOS 16.5/20F66 UIKit method
address. `statusbar_cutout=0` disables it in a loader profile accepting that flag.
Debug tracing remains controlled by `hakios_debug`; the local diagnostic build can
also define `HAKIOS_STATUSBAR_TRACE=1`. The quiet checkpoint retains debugging code.

Local source, root image, payload and logs are preserved under
`build/spacewar-clock-progress-20261104/`, with a SHA-256 inventory. The Windows
RAM-test payload and logs remain under `C:/platform-tools/clock-test`. The original
550 MHz bundle in `C:/platform-tools/final` was not overwritten.
