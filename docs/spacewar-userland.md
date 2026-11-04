# Spacewar userland setup

Local evidence paths below refer to the original session; ignored `build/`,
`logs/`, `xnu-src/` and ROM inputs are not distributed with this source.


The user has reported a visible home screen on the phone using the verified
identity loader and SMP7 userland payload. The saved
`logs/spacewar-userland-smp7-home-live.log`
confirms the IPSW identity, APFS root, 36 bootstrap checks with zero failures,
real launchd, and SpringBoard/backboardd spawning. It contains no actual panic
or hardware-target query failure. It is an early live snapshot; the home
screen is the user's later visual report, and the final return/screenshot for
this run is still pending. Physical touch remains disabled and untested.

The phone combination associated with that report is:

| File | SHA-256 |
|---|---|
| `xnu-spacewar-identity-debug.img` | `64c252db2608e6da1c4fe8c806c0316c88aeca20125d2514f66cc1c6babb0dfb` |
| `spacewar-userland-smp7-payload.bin` | `6da2060e56bd81feefab726df05fbcec3f6d581bac247f1f057ef93d5defe306` |
| `userland/system.apfs` | `c28cca0867c0839c36128686c328a1c4654d01648aed1120320be42d049aefe5` |

The payload uses the eight-driver `hakios-kernel-spacewar-touch-probes`, the
existing small APFS root, `bootcpu=7 cpus=7`, physical buttons, identity and
thermal polling, with a 240-second budget. It omits physical touch, GPU, UFS,
clock control and the other unported drivers. The older
`xnu-spacewar-userland-debug.img` (`f341ceb4...`) and initial 120-second payload
are preserved; they are not the combination tied to the successful home-screen
report. The earlier identity-related failure, static identity check and
current phone sequence are in [spacewar-userland-next.md](spacewar-userland-next.md).

The userland is built locally from iOS 16.5 build 20F66 for iPhone10,3/iPhone10,6,
the exact input expected by this repository. The IPSW was downloaded from
[Apple's CDN](https://updates.cdn-apple.com/2023SpringFCS/fullrestores/032-85130/6AB0D10A-8AF9-4F6C-8CF5-A1B315CABE88/iPhone10,3,iPhone10,6_16.5_20F66_Restore.ipsw).
Its 6,165,698,198-byte size and SHA-256
`c1091c401f67e3aa7cdfeb2cc19e6c79be3c957aae4e097a019e9ccea07d52f2`
were verified before extraction. The archive and metadata are in `downloads/`.

## Source and build

The common repository was fast-forwarded by 33 commits from `a3384e0` to
[`96ffbc532e970f3540885755d457e401e04d4707`](https://github.com/oosexgang/hakios-xnu-kernel/commit/96ffbc532e970f3540885755d457e401e04d4707).
All local Spacewar changes were preserved. The device/UEFI subrepositories were
left in their working state. Details are in `build/upstream-update.txt`.

The two updated framebuffer/IOSurface source overlays were applied to the XNU
tree after confirming its old versions matched the old upstream files, then the
six-driver kernel was rebuilt as `hakios-kernel-spacewar-userland`. The working
minimal kernel and the SOCINFO/THERMAL probe payloads remain preserved.

The loader was built from `xnuloader/loader.c`, with the identity header generated
from this IPSW's device tree. The previous placeholder loader remains in
`prebuilt/loader.efi`. The LZFSE fallback in `xnuloader/mkidentity.sh` was fixed to
build the decoder's `all` target.

The final image build uses `REST=1 WIFID=0 SHARE=1 CHECK=1`, without GPU, DSP or
modem firmware. `rc.jobs.unlock` replaces the default CPU stress jobs and issues
a synthetic unlock gesture after 32 seconds. Physical touch is still pending.
The local build helper is `build/userland-setup/build-images.sh`.

The initial build artifacts below are preserved alongside the current phone
combination above:

| Artifact | Size | Validation |
|---|---|---|
| `userland/root.apfs` | 4,194,304 bytes | APFS clean; 478 entries read back exactly by the kernel's reader |
| `userland/system.apfs` | 2,476,716,032 bytes | APFS clean; 62,262 entries read back exactly by the kernel's reader |
| `spacewar-userland-payload.bin` | 19,034,256 bytes | Kernel + small APFS root; XNUP bounds/bytes verified; below 63 MiB |
| `xnu-spacewar-userland-debug.img` | about 1.7 MiB | Working Spacewar UEFI + IPSW-derived loader; boot structure and DTB/FD checked |

The initial payload gives the kernel a 120-second run and enables only the
already verified SoC identity probe. The current SMP7 payload above adds the
phone-tested CPU, button and thermal settings. The system image is sent
separately to the loader's resident RAM-disk area; it does not fit in the small
XNUP container.

Logs and checksums:

- `logs/spacewar-userland-unpack.log`
- `logs/spacewar-userland-build.log`
- `logs/spacewar-userland-final-root-build.log`
- `logs/spacewar-kernel-userland-build.log`
- `logs/spacewar-userland-boot-inspect.log`
- `logs/spacewar-userland.sha256`
- `build/spacewar-userland-manifest.json`

## Windows files and phone boot

`build/spacewar-userland-windows/` contains the images and a complete copy of
Google's [Windows platform-tools](https://developer.android.com/tools/releases/platform-tools)
37.0.1. This original bundle is preserved and still contains the initial
six-driver/120-second artifacts. The current working files have separately
been copied to `C:\platform-tools` with `./copy` and their destination hashes
verified. Start a new session from the phone's stock fastboot bootloader:

```powershell
./fastboot boot xnu-spacewar-identity-debug.img
```

Wait until the loader appears, then confirm its identity before uploading the
large system image followed by the small kernel/boot payload:

```powershell
./fastboot getvar product
./fastboot getvar identity
./fastboot getvar hardware-target
./fastboot getvar model
# Require xnuloader / ipsw / D22AP / iPhone10,3, respectively.
./fastboot oem ramdisk
./fastboot stage system.apfs
./fastboot stage spacewar-userland-smp7-payload.bin
./fastboot oem run
Start-Sleep -Seconds 15
$status = ./fastboot getvar result 2>&1 | Out-String
$status
if ($status -match 'result:\s*running') { ./fastboot oem log }
./fastboot get_staged spacewar-userland-smp7-live.log
Select-String -Path .\spacewar-userland-smp7-live.log -Pattern 'identity:','stage0:','init:','launchd','SpringBoard','backboardd','failed to query','EARLY KERNEL EXCEPTION','panic\(cpu'
```

The large upload can take several minutes. This sequence boots from RAM. The
seven-core setup retains CPU0 for UEFI/USB, allowing live log and screenshot
queries. After the four-minute budget, fetch the final result/log as described
in [spacewar-userland-next.md](spacewar-userland-next.md). If the early status
already says `returned`, fetch the staged final log directly; `oem log` is only
for a running kernel. Fetch the log before requesting a screenshot, because a
screenshot replaces the loader's staged upload buffer.

The expected sequence is APFS root mount, stage0 mounting `/dev/md1` at
`/redwood`, the port's init handing over to Darwin launchd, then backboardd and
SpringBoard. Those services now have live phone-log evidence, followed by the
user's home-screen report. This establishes the first graphical boot
milestone; it does not establish physical touchscreen operation, native GPU
acceleration, persistent storage or the other unported hardware.

## Earlier local emulator checks

A workspace-local Ubuntu QEMU 8.2.2 runtime and AAVMF firmware are prepared in
`build/qemu-host/`; no system packages were installed. Its minimal test passed
with the latest kernel, IPSW-derived loader, PID 1 `hello` and timed return:
`logs/spacewar-qemu-minimal.log`.

The full userland QEMU check also completed with exit 0: the 120-second kernel
budget expired and control returned after 144.5 host seconds. It mounted the
APFS system root, passed the bootstrap checks, executed real launchd, and spawned
SpringBoard, backboardd and many other daemons. No kernel panic or early
exception was observed. The final screenshot still shows the Apple boot logo;
that emulator result did not establish a graphical session. The later phone
home-screen report and live log are recorded above. No further emulator work
is planned.

Evidence:

- `logs/spacewar-qemu-userland.log`
- `logs/spacewar-qemu-userland.png`
- `build/qemu-host/userland/result.txt`
