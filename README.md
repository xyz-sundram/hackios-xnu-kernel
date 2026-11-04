# HakiOS

English · [中文](README.zh.md) · [Українська](README.uk.md)

**Apple's open-source Darwin kernel (XNU, xnu-8796.121.2) on Android phones with a Qualcomm SoC**, the way Android is Linux on
phones: a boot loader of its own, drivers of its own, a configuration system and a version of its own. The same kernel runs in
QEMU with nothing of Apple's and nothing of any phone's (`make qemu-open`).

**Documentation: [docs/](docs/index.md)**, also on the project's website (`hakios-web`). Start with [Getting started](docs/getting-started/index.md), [Port a device](docs/porting/index.md) or the
[Architecture](docs/concepts/architecture.md).

## What works

| | POCO X5 Pro 5G (`redwood`) | QEMU `virt` |
|---|---|---|
| Lock screen, home screen, Control Center, the stock apps | yes | yes (slow: the CPU is emulated) |
| Touch, multitouch, buttons | yes | yes |
| All 8 CPU cores, the big one at 2.4 GHz | yes | no |
| GPU rendering on Adreno (OpenGL ES through Mesa) | yes | no |
| Speakers, microphone, the battery's level | yes | sound only |
| Wi-Fi, cameras, vibration | yes | network only |
| `/private/var` kept across reboots, Sileo, SSH | yes | no |
| Metal | partly: a device Metal loads; buffers and libraries; nothing drawn yet | partly |
| Cellular | partly: the modem is up, the SIM bound, the network seen; no registration yet | no |
| Bluetooth, charging under this kernel, Apple's services | no | no |

Every part, as each device's own `device.conf` records it: [Devices](docs/devices/index.md). The rows about the lock screen and the
apps describe what happens when you add layer 2 below yourself.

## The three layers

1. **The open core**: the kernel port, the loader, the drivers, the tools. All source, in these repositories.
2. **What you put on top**: the kernel can start a user space that you supply. It was built so that it can start the Darwin userland 16.5
   user space, which you lay out yourself from files you download from Apple; nothing of it is shipped, and nothing is
   circumvented to get it. Whether you may do that, and whether you want to, is your decision.
3. **Apple's services** (activation, Apple ID, iCloud, the App Store): not supported and not a goal.

By default the loader goes into RAM through `fastboot boot`, the system lives in RAM for a session, and a reboot brings the phone
back to its own Android. The one thing written by default is the loader's own log (a few KB) to the `logdump` partition; with
`ufs_write=1` the kernel writes only to a partition `darwin` that you create yourself. This is a research bench, not a ROM.

## Quick start

```sh
git clone https://github.com/oosexgang/hakios-xnu-kernel.git hakios
cd hakios
make doctor           # what this machine lacks, and what to do
make qemu-open        # the open kernel in QEMU: nothing of Apple's, nothing of a phone's
```

Darwin in QEMU (Linux, 12 GB of disk, Apple's IPSW downloaded for you), and a session on the phone:
[QEMU](docs/getting-started/qemu.md), [On the phone](docs/getting-started/phone.md).

```sh
make redwood_defconfig   # the phone's configuration: the common kernel + the SC7280's + the phone's own
make kernel              # the kernel with the drivers that configuration builds
make phone SECS=600      # a 10-minute session on the phone, in RAM
```

## Build for Nothing Phone (1) (`spacewar`)

This checkout includes the Phone (1) source port. The verified configuration boots the iOS home screen with physical touch,
volume/power buttons, wallpaper and Adreno GPU acceleration. It uses **seven XNU cores (7–1)** while CPU0 serves USB, requests
about **1.900/1.862 GHz** for big/prime cores, and supports **315/450/550 MHz GPU** settings. The latest tested setting is 550 MHz
with a quiet display; the user confirmed smoother rendering and working touch/buttons. Audio, Wi-Fi, cameras, cellular,
charging control and persistent storage are **not established working features of this Phone (1) build**.

These steps target the tested SM7325/Spacewar layout and FocalTech M146 V27 controller firmware. Different panel/ROM layouts
need validation. You need an already-unlocked bootloader, a Linux/WSL2 build host, Google platform-tools on Windows, enough
disk space for the kernel/UEFI/IPSW trees, and your own firmware inputs. This is a RAM-boot research build.

### 1. Set up the host and obtain the inputs

Install the tools listed in [building from source](docs/getting-started/building.md) and [Windows/WSL](docs/windows.md).
On Linux, Theos must provide its Darwin cross-toolchain and `iPhoneOS16.5.sdk` (normally under `~/theos`). Run:

```bash
make doctor
python3 tools/hakios-device.py sync  # fetch the pinned common UEFI/device dependencies
source ./xnu-env.sh
```

Supply the iPhone X iOS 16.5/20F66 IPSW expected by `sysimage/unpack.sh` and extract it locally:

```bash
make unpack IPSW=/absolute/path/iPhone10,3,iPhone10,6_16.5_20F66_Restore.ipsw
```

Extract your Phone (1)'s matching DTB to `spacewar.dtb`. Place its `a660_gmu.bin`, `a660_sqe.fw`, `a660_zap.mdt` and
`a660_zap.b02` in `fw-gpu/`, from your own Android `/vendor/firmware` or matching ROM image. For a readable Android vendor
partition, for example:

```bash
mkdir -p fw-gpu fw-touch
for file in a660_gmu.bin a660_sqe.fw a660_zap.mdt a660_zap.b02; do
    adb pull "/vendor/firmware/$file" fw-gpu/
done
```

The touch image used here is119284 bytes, SHA256
`04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726`. Obtain that M146 V27 image from your matching ROM.
For the specifically tested AOSPA Uvite 20240903 factory ZIP, the pinned, read-only extractor is:

```bash
python3 tools/spacewar-extract-touch-fw.py /path/to/aospa-uvite-beta-phone1-20240903-image.zip fw-touch/spacewar-m146.bin
```

It rejects another kernel or firmware hash. It is not a generic extractor for every Phone (1) ROM. Private ROM/IPSW images,
GPU binaries and the generated touch array remain local build inputs.

### 2. Build the Spacewar UEFI

The common UEFI comes from the pinned `hakios-uefi` dependency. Its apply script adds the HakiOS loader hook and this
repository's Spacewar RAM reservations/early RAM-discovery overlay:

```bash
uefi/apply.sh spacewar              # fresh Mu-Silicium tree; refuses to overwrite an existing tree
python3 -m venv build/uefi-venv
build/uefi-venv/bin/pip install -r Mu-Silicium/pip-requirements.txt
```

Supply the vendor UEFI modules extracted from your own Phone (1) XBL in the layout Mu-Silicium expects; follow
`uefi/README.md` and [the device overlay notes](devices/spacewar/uefi/README.md). These proprietary modules and the resulting
FD are not distributed here. Vendor extraction for an arbitrary ROM is a manual prerequisite, not an automated or universally
validated step. Then build from the Mu tree:

```bash
( cd Mu-Silicium && ../build/uefi-venv/bin/python build_uefi.py -d spacewar -r DEBUG )
```

The required result is `Mu-Silicium/Build/spacewarPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd`. For an existing configured Mu tree,
apply `devices/spacewar/uefi/files/` there before rebuilding; preserve your vendor modules. The DEBUG firmware build is the
phone-tested input; the runtime flag below suppresses the kernel/UI console while retaining debugging code.

### 3. Build the exact kernel, loader and graphics userland

```bash
make spacewar_defconfig FRAGMENTS=spacewar-performance
test -d xnu-src/xnu-8796.121.2 || xnu/apply.sh
python3 tools/spacewar-touch-firmware.py fw-touch/spacewar-m146.bin
make kernel JOBS=8
make loader
```

The firmware generator verifies the blob and writes its array only into the ignored kernel `BUILD/` directory. It preserves
the exact bytes used in the working build. Keep the generated IPSW identity local; a placeholder/open loader cannot boot
this userland. Build Mesa and the root/system images with the actual GL implementation:

```bash
# Mesa needs meson, ninja, bison, flex and Python venv support installed on the host.
GL=1 REST=1 WIFID=0 SHARE=1 CHECK=1 RC=rc.launchd RC_JOBS=rc.jobs.unlock bash userland/build.sh
```

This produces `hakios-kernel`, `xnuloader/loader.efi`, `userland/root.apfs` and `userland/system.apfs`. The root contains Mesa,
the GL bridge and your GPU firmware. `RC_JOBS=rc.jobs.unlock` avoids the default CPU stress job. `SHARE=1` omits local keys
and Wi-Fi credentials; it does not make the supplied system/firmware assets redistributable.

### 4. Package and boot

```bash
python3 tools/spacewar-package.py --gpu-mhz 550 --out build/spacewar --copy-system
```

The packer validates the ten-driver configuration, packages the kernel/root, creates the matched USB-loader boot image,
checks its FD/DTB, and writes checksums. Add its output files to a Windows platform-tools directory containing `fastboot.exe`
and its DLLs. From **stock Android fastboot** in that directory:

```powershell
.\fastboot.exe boot .\xnu-spacewar-gpu-clocks.img
powershell -NoProfile -ExecutionPolicy Bypass -File .\spacewar-userland-gpu.ps1 -Touch -Quiet -GpuMHz 550
```

Allow the system upload and touch firmware initialization to finish. The launcher saves live RAM logs. Ctrl+C stops only
the collector; `fastboot oem stop` requests return. There is no scheduled cutoff. Build/select 450 or 315 MHz with the same
`--gpu-mhz` / `-GpuMHz` values. To change clocks between runs, stop the existing run first; re-enter stock fastboot before
issuing another `fastboot boot` command.

`hakios_debug=0` suppresses console text and routine tracing; `hakios_debug=1` restores diagnostics when packaging a debug
command line. Debugging code remains compiled in. See [clock tests](docs/spacewar-gpu-clocks.md),
[quiet CPU profile](docs/spacewar-quiet-performance.md) and [touch ownership](docs/spacewar-gpu-touch.md) for limits and checks.

The build steps use the same source paths and packaging operations as the verified phone run. A complete clean-machine
rebuild including fresh extraction of proprietary vendor inputs has not been repeated; local `build/` manifests/logs mentioned
in historical bring-up notes are session evidence, not files a fresh clone is expected to contain.

## Repositories: common, SoC, device, UEFI

| Repository | What |
|---|---|
| [hakios-xnu-kernel](https://github.com/oosexgang/hakios-xnu-kernel/tree/main) | **common**: the kernel port, every common driver, the loader, user space, the tools, this documentation, and `devices/qemu` |
| [hakios-sm7325](https://github.com/oosexgang/hakios-sm7325) | POCO X5 Pro 5G: its defconfig, status, UEFI platform, firmware script, and its SoC's layer (`soc/`: the Qualcomm SC7280 family) |
| [hakios-uefi](https://github.com/oosexgang/hakios-uefi) | the firmware that starts the loader: Mu-Silicium with HakiOS's boot path, banner and name, and HakiOS's U-Boot (`u-boot/`) |
| [hakios-metal](https://github.com/oosexgang/hakios-metal) | Metal's command model on the Adreno, through the kernel's GPU driver |

`devices/manifest.conf` in the common repository names each one and the revision it is at; `tools/hakios-device.py sync` clones them
into `devices/` and `uefi/`. One repository per device: it carries its SoC's layer, and `tools/hakios-device.py new` makes one. [How the repositories fit](docs/porting/repositories.md).

```sh
git clone https://github.com/oosexgang/hakios-xnu-kernel.git hakios && cd hakios
tools/hakios-device.py sync      # the SoC, device and UEFI repositories, at the revisions the manifest names
```

A device of your own is a repository over the common one: `tools/hakios-device.py new` makes it from the phone's own device tree,
with every phone driver left out until it is ported. Each driver is an option of the kernel's own configuration (`CONFIG_HAKIOS_*`,
XNU's `options` and `conf/files`), so a device's defconfig builds exactly the drivers it has.
[Port a device](docs/porting/index.md), [Drivers in the configuration](docs/drivers-config.md).

## What is in the repository, and what never is

In it: only our own code and data: the patch to Apple's open-source XNU (`xnu/hakios.patch`, APSL 2.0) and our files, the boot
loader, our programs and libraries, the tools, `prebuilt/` (built from this source; the loader **without** any Apple data:
`make loader` makes the one that starts Darwin, on your machine, from your own IPSW), and the device and SoC directories.

Never in it: any file of Darwin (you build the system image on your machine from an IPSW you get yourself and hand to
`sysimage/unpack.sh`; nothing is downloaded for you, decrypted or circumvented); the phone's firmware (pulled from your own phone into
`fw-*/`, which git ignores); keys, certificates, activation, or anything that would make the phone pass as an iPhone to Apple.

## Independent, and Apple-free at the core

HakiOS is an independent research project. It is **not affiliated with or endorsed by Apple**;
"Darwin", "iPhone", "XNU" and "Apple" are named only to describe what the code works with. The core
kernel, the boot loader and the open tools build and run with nothing of Apple's:

```sh
make qemu-open      # the open kernel + a placeholder loader + a tiny raw-syscall user space, in QEMU
```

No IPSW, no Darwin user space, no firmware. Booting the stock Darwin user space (`make qemu`,
`make phone`) is an optional path that needs the IPSW and the phone's firmware, which you supply
from your own device.

## Licenses and the legal side

My code is under the license of the files it changes: the XNU patch under the Apple Public Source
License 2.0 (Apple publishes XNU under it; the terms are met: changes are published as source,
notices are kept), the rest as the file headers say. Darwin, its images, Apple's trademarks and the
phone's firmware belong to their owners, are not in this repository and are not distributed by me;
everyone takes them from their own phone and their own IPSW. "Darwin", "iPhone" and "Apple" are
named only to describe what the code works with. If you believe a file here infringes anyone's
rights, open an issue and I will remove it.

The detail is in separate files: `LEGAL.md` (what is and is not distributed, and the limits of
these statements), `COMPLIANCE.md` (how the APSL 2.0 and other terms are met, and the items still
to verify), `THIRD_PARTY_LICENSES.md` (every third-party component with origin and license), and
`NOTICE` (the short canonical list). None of this is legal advice.

hakios is an experimental project focused on bringing darwin to android hardware. our goal is to make hakios free and accessible to everyone.

owners:
@oodze - founder
@netusd - adversting / ceo-dev
@wannqn - dev 
@hxfuxyyXD - dev
Rhaenja Hun（杭润嘉） — Developer / Sponsor

tym sicilium for the sources!

