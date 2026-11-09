# Spacewar display presentation and compact canvases

## Phone results

These changes were exercised on Nothing Phone (1), Spacewar stock build
V3.2-260618-1045, using the rebase/upstream-20261105 checkout and RAM boot.

- The implicit 375x812-point 2x identity produced a red rdar://45025538 banner
  and missing wallpaper. Keeping the original 1125x2436 identity by default
  restored the working 3x layout.
- Row-copy synchronization failed its strict counter test (6/8 linear steps).
  It remained unpaced; that experiment did not eliminate tearing.
- The first native-flip attempt qualified the controller but could not allocate
  contiguous physical memory below 4 GiB after XNU started.
- Reserving scanout memory in EFI enabled acknowledged native flips. The user
  reported that tearing had more or less disappeared, but motion stuttered.
- Two cached EFI-owned buffers, direct full-frame rendering, cache cleaning
  before submission, and next-frame snapshot work overlapping the previous
  flip substantially improved perceived smoothness. Hardware acknowledgment
  remains mandatory before either scanout buffer is reused.
- An 828x1792 2x canvas was reported much smoother; the home indicator was lost.
- The 750x1624 2x compact profile with the virtual-home capability was reported
  smooth with the navigation bar restored. Wallpaper was still missing.
- The latest static-wallpaper fallback compiled and packaged successfully but
  has not yet been confirmed on the phone. Rectangle corruption is not
  established as fixed.

Some recorded active windows contain 302 presented frames over 5000 ms, with
zero late incoming swaps. These aggregates include gesture/app differences and
are not a controlled benchmark or proof of 120 fps.

## Source changes

The default screen identity remains native 3x. Explicit reduced profiles are:

| Profile | Canvas | Scale | UIKit points |
| --- | --- | --- | --- |
| Native | 1125x2436 | 3 | 375x812 |
| XR | 828x1792 | 2 | 414x896 |
| Compact gestures | 750x1624 | 2 | 375x812 |

The live loader accepts only coherent dimension/scale/subtype combinations.
Reserved scanout addresses are inserted by the loader; input payloads cannot
supply the internal fb_rsv2 address. EFI reserves two aligned low-memory
buffers before claiming XNU's RAM window. The controller's stream, format,
stride, routing and ownership must match the qualified retained pipeline.
On return, the original boot framebuffer is restored; failed restoration
keeps backing memory reserved.

Compact packages rebuild display_info and libhakios.sb.dylib. The cache helper
retires stale scale/geometry answers while preserving a backup. Gesture hooks
require an explicit fb_gestures=1 profile and check the runtime method ABI.
The wallpaper fallback loads a stock static asset from the builder's own
unpacked IPSW into existing wallpaper windows; no image is included here.
It uses aspect fill and does not implement live wallpaper customization.

## Reproduction

First build the Spacewar performance kernel, loader, root and system inputs
using README.md and the user's own IPSW/vendor inputs. Build a separate loader:

```sh
XNULOADER_OUT="$PWD/build/spacewar-display-loader" bash xnuloader/build.sh
python3 tools/spacewar-package.py   --out build/spacewar-compact-review   --loader build/spacewar-display-loader/loader.efi   --compact-gestures --native-scanout --unpaced --fb-threads 3 --ca-120   --copy-system
```

The compact package needs SydneyWallpaper.jpg from the local unpacked IPSW.
The output is a private RAM-boot bundle. Do not commit or distribute its iOS
images, wallpaper asset, firmware, identity header or generated boot images.
The normal compact command above targets the measured 60 Hz path. See
[the 120 Hz experiment](spacewar-panel120.md) for its unsuccessful status.

## Validation

Focused host checks cover DSI packets/readback, deferred latch acknowledgment,
controller timing qualification, timeout/ownership handling, boot-buffer
restoration, display identity/cache retirement and live-loader argument policy.
Kernel compilation, custom userland-library compilation and boot-image
structure/FD/DTB validation completed. XNU's existing expected kext symbol-set
failures are accepted by tools/make-kernel.sh; hardware success is reported
separately above. Private raw logs and generated artifacts are excluded.
