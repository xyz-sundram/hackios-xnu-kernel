# HakiOS: one Makefile at the top, the way an Android tree has one. Every target is a script
# below it; run the script itself for its options.
#
#   make                 help
#   make unpack IPSW=F   the OS files out of your own Darwin userland 16.5 IPSW into sysimage/ (sysimage/unpack.sh)
#   make images          userland/root.apfs and userland/system.apfs (userland/build.sh)
#   make kernel          the kernel from source (xnu/apply.sh, xnu-deps/setup.sh, xnu-build.sh)
#   make loader          the boot loader, with identity.h made from your IPSW (xnuloader/build.sh)
#   make firmware        the phone's firmware off the phone over adb (devices/$(DEVICE)/pull-firmware.sh)
#   make qemu            a 20-minute session in QEMU (needs the Darwin images)
#   make qemu-open       boot the open kernel in QEMU with NO Apple software
#                        (kernel + the hello RAM disk + a placeholder loader)
#   make phone           a session on the phone, then back to Android (tools/modem-cycle.sh)
#   make prebuilt        refresh prebuilt/ from what was built (tools/update-prebuilt.sh)
#   make clean           build output of the kernel, loader and images
#   make doctor          check this machine and a phone on adb for what HakiOS needs (tools/doctor.sh)
#   make esp             an EFI system partition tree in build/esp: the loader and a payload it reads itself,
#                        to copy to a FAT32 volume (UEFI-native boot, no fastboot; docs/uefi-boot.md)
#   make u-boot          HakiOS's U-Boot for $(DEVICE) (uefi/u-boot/apply.sh, uefi/u-boot/build.sh; docs/u-boot.md)
#   make u-boot-img      hakios-u-boot.img: U-Boot as a boot image for fastboot boot, starting the loader from RAM
#   make qemu-u-boot     make qemu-open's payload booted the phone's way: U-Boot, the hakios command, bootefi
#   make spacewar-audio-debug   package a gated sound=3 RAM image from explicit AUDIO_DEBUG_* files
#   make spacewar-log-recovery  build a separate read-only PID1 log-export RAM image (LOG_RECOVERY_* files)
#                        neither target boots or flashes; see docs/spacewar-log-recovery.md
#
# Configuration, the way Linux's make does it (configs/, tools/hakios-config.py; docs/configs.md):
#   make redwood_defconfig   .config from configs/defconfig + redwood_defconfig (devices/redwood/configs/), which is
#                            common_defconfig + sc7280.config (devices/redwood/soc/) + redwood.config
#                            (any NAME_defconfig; make common_defconfig is the common kernel alone; docs/configs.md)
#   make devices             the device and SoC repositories (tools/hakios-device.py list; docs/porting/repositories.md)
#   make virt.config         lay a fragment (configs/virt.config) over the .config there is
#   make config              show the .config: its symbols, the kernel's arguments, the firmware it wants
#   make savedefconfig       the .config as a minimal defconfig
#   make phone               with a .config, its arguments are the kernel's; without, the phone line below
#   FRAGMENTS="open trace"   fragments for a NAME_defconfig: make redwood_defconfig FRAGMENTS=open
#
#   DEVICE=redwood   the device directory under devices/ (default redwood)
#   ARGS="..."           kernel arguments for make phone (default: the .config's, else everything the phone has)
#   SECS=600             the session's length for make phone / make qemu
#   JOBS=8               parallel jobs for the kernel build

DEVICE ?= redwood
JOBS ?= 8
SECS ?= 600
# qemu-open runs the open configuration: configs/defconfig (nothing of a phone's own, one core)
ARGS_OPEN ?= $(shell tools/hakios-config.py args --defconfig defconfig)
ifneq ($(wildcard .config),)
# a .config is the kernel's arguments; the runner then does not add its own four (cores, clock, storage): they are in it
ARGS ?= $(shell tools/hakios-config.py args)
RUNNER_BASE = BASE_ARGS=
else
ARGS ?= touchscreen=1 gpu=1 adsp=1 sound=4 wifi=1 haptic=5 modem=1 modem_again=1 modem_buffer=0xa8000000
RUNNER_BASE =
endif
IMAGE_OPTS ?= REST=1
ESP_DIR ?= build/esp
PAYLOAD ?= qemu/payload.bin
# make u-boot-img: the payload put in the image's ramdisk with the loader (none: U-Boot waits for them over fastboot)
UBOOT_PAYLOAD ?=

LOG_RECOVERY_OUT ?= build/spacewar-log-recovery
LOG_RECOVERY_NAME ?=
LOG_RECOVERY_REPLAY ?= 0
AUDIO_DEBUG_OUT ?= build/spacewar-audio-debug
export LOG_RECOVERY_OUT LOG_RECOVERY_NAME LOG_RECOVERY_REPLAY LOG_RECOVERY_GPU_FW
export LOG_RECOVERY_KERNEL LOG_RECOVERY_KERNEL_VERSION_FILE LOG_RECOVERY_KERNEL_MANIFEST
export LOG_RECOVERY_INSTALL_MAP LOG_RECOVERY_LOADER LOG_RECOVERY_UBOOT LOG_RECOVERY_DTB
export AUDIO_DEBUG_OUT AUDIO_DEBUG_KERNEL AUDIO_DEBUG_KERNEL_VERSION_FILE AUDIO_DEBUG_KERNEL_MANIFEST
export AUDIO_DEBUG_INSTALL_MAP AUDIO_DEBUG_LOADER AUDIO_DEBUG_UBOOT AUDIO_DEBUG_DTB AUDIO_DEBUG_RAMDISK

.PHONY: devices help unpack images kernel loader loader-open hello qemu qemu-open qemu-u-boot u-boot u-boot-img phone prebuilt clean all doctor defconfig config savedefconfig esp spacewar-log-recovery spacewar-audio-debug

help:
	@awk '{ if (!/^#/) exit; sub(/^# ?/, ""); print }' Makefile

doctor:
	tools/doctor.sh

all: kernel loader images

unpack:
	sysimage/unpack.sh $(IPSW)

images:
	$(IMAGE_OPTS) userland/build.sh

# Explicit external inputs only: never rebuild or replace normal userland or installed images.
spacewar-log-recovery:
	@set -eu; \
	for name in LOG_RECOVERY_GPU_FW LOG_RECOVERY_KERNEL LOG_RECOVERY_KERNEL_VERSION_FILE LOG_RECOVERY_KERNEL_MANIFEST LOG_RECOVERY_INSTALL_MAP LOG_RECOVERY_LOADER LOG_RECOVERY_UBOOT LOG_RECOVERY_DTB; do \
		value=$$(printenv "$$name" || true); [ -n "$$value" ] && [ -e "$$value" ] || { echo "set $$name to an existing external input" >&2; exit 2; }; \
	done; \
	bash tools/build-log-recovery.sh "$${LOG_RECOVERY_OUT}" "$${LOG_RECOVERY_GPU_FW}"; \
	set --; [ "$${LOG_RECOVERY_REPLAY}" != 1 ] || set -- --allow-journal-replay; \
	python3 tools/spacewar-log-recovery.py \
		--kernel "$${LOG_RECOVERY_KERNEL}" --kernel-version-file "$${LOG_RECOVERY_KERNEL_VERSION_FILE}" \
		--kernel-manifest "$${LOG_RECOVERY_KERNEL_MANIFEST}" --install-map "$${LOG_RECOVERY_INSTALL_MAP}" \
		--loader "$${LOG_RECOVERY_LOADER}" --uboot "$${LOG_RECOVERY_UBOOT}" --dtb "$${LOG_RECOVERY_DTB}" \
		--ramdisk "$${LOG_RECOVERY_OUT}/ramdisk.apfs" --ramdisk-manifest "$${LOG_RECOVERY_OUT}/ramdisk.manifest.json" \
		--cmdline-file "$${LOG_RECOVERY_OUT}/cmdline" --output-prefix "$${LOG_RECOVERY_OUT}/spacewar-log-recovery" "$$@"

spacewar-audio-debug:
	@set -eu; \
	for name in AUDIO_DEBUG_KERNEL AUDIO_DEBUG_KERNEL_VERSION_FILE AUDIO_DEBUG_KERNEL_MANIFEST AUDIO_DEBUG_INSTALL_MAP AUDIO_DEBUG_LOADER AUDIO_DEBUG_UBOOT AUDIO_DEBUG_DTB AUDIO_DEBUG_RAMDISK; do \
		value=$$(printenv "$$name" || true); [ -n "$$value" ] && [ -f "$$value" ] || { echo "set $$name to an existing external input" >&2; exit 2; }; \
	done; \
	python3 tools/spacewar-audio-debug.py \
		--kernel "$${AUDIO_DEBUG_KERNEL}" --kernel-version-file "$${AUDIO_DEBUG_KERNEL_VERSION_FILE}" \
		--kernel-manifest "$${AUDIO_DEBUG_KERNEL_MANIFEST}" --install-map "$${AUDIO_DEBUG_INSTALL_MAP}" \
		--loader "$${AUDIO_DEBUG_LOADER}" --uboot "$${AUDIO_DEBUG_UBOOT}" --dtb "$${AUDIO_DEBUG_DTB}" \
		--ramdisk "$${AUDIO_DEBUG_RAMDISK}" --output-prefix "$${AUDIO_DEBUG_OUT}/spacewar-audio-debug"

kernel:
	[ -d xnu-src/xnu-8796.121.2 ] || xnu/apply.sh
	[ -e xnu-deps/host/bin/ld64 ] || xnu-deps/setup.sh
	tools/make-kernel.sh -j$(JOBS)

loader:
	xnuloader/build.sh

firmware:
	devices/$(DEVICE)/pull-firmware.sh

qemu:
	mkdir -p logs
	qemu/run-darwin.sh $(SECS) -display gtk | tee logs/qemu.log

# The Apple-free path: no IPSW, no Darwin user space. The loader is built with a placeholder identity
# (xnuloader/mkidentity.sh --placeholder), and the kernel boots the raw-syscall `hello` RAM disk.
# This is the configuration an outside contributor can build and run with nothing of Apple's.
loader-open:
	PLACEHOLDER=1 xnuloader/mkidentity.sh
	xnuloader/build.sh

# `hello` on its own: a static, raw-syscall program (no Darwin SDK, no Apple libraries).
hello:
	bash -c '. ./xnu-env.sh && xnu-clang -arch arm64 -Os -static -nostdlib -ffreestanding \
		-fno-stack-protector -fno-builtin -Wl,-e,_start -Wl,-pagezero_size,0x100000000 \
		-o userland/hello userland/src/hello.c'

qemu-open: kernel loader-open hello
	mkdir -p logs
	xnuloader/mkpayload.py -o qemu/payload.bin \
		kernel=hakios-kernel \
		ramdisk=userland/hello --cmdline "$(ARGS_OPEN)"
	qemu/run.sh $(SECS) | tee logs/qemu-open.log

# HakiOS's U-Boot (docs/u-boot.md): the phone's ABL starts it from RAM (fastboot boot), and it starts the loader from
# memory; nothing is flashed. u-boot/ is made once by uefi/u-boot/apply.sh, with every device's U-Boot files.
u-boot:
	[ -d u-boot ] || uefi/u-boot/apply.sh
	uefi/u-boot/build.sh $(DEVICE)

u-boot-img:
	tools/mkubootimg.py -d $(DEVICE) $(if $(wildcard $(UBOOT_PAYLOAD)),-p $(UBOOT_PAYLOAD))

qemu-u-boot: kernel loader-open hello
	[ -f u-boot/build-qemu/u-boot.bin ] || { [ -d u-boot ] || uefi/u-boot/apply.sh; uefi/u-boot/build.sh qemu; }
	mkdir -p logs
	xnuloader/mkpayload.py -o qemu/payload.bin \
		kernel=hakios-kernel \
		ramdisk=userland/hello --cmdline "$(ARGS_OPEN)"
	qemu/run-u-boot.sh $(SECS) | tee logs/qemu-u-boot.log

phone:
	[ ! -f .config ] || tools/hakios-config.py check
	XL_SB_DELAY=8 $(RUNNER_BASE) tools/modem-cycle.sh "$(ARGS)" $(SECS)

# The loader and a payload (make qemu-open makes qemu/payload.bin; xnuloader/mkpayload.py makes any other) as the files an
# EFI system partition holds, which the loader reads itself: UEFI-native boot. The default tree is made again each time.
esp:
	[ "$(ESP_DIR)" != build/esp ] || rm -rf build/esp
	tools/mkesp.py $(ESP_DIR) $(PAYLOAD)

# ---- configuration (configs/, tools/hakios-config.py) ---------------------------------------------------------
defconfig:
	tools/hakios-config.py defconfig defconfig $(FRAGMENTS)

devices:
	@tools/hakios-device.py list

%_defconfig:
	tools/hakios-config.py defconfig $* $(FRAGMENTS)

%.config:
	tools/hakios-config.py merge $*

config:
	tools/hakios-config.py show

savedefconfig:
	tools/hakios-config.py savedefconfig

prebuilt:
	tools/update-prebuilt.sh

clean:
	rm -rf xnu-src/xnu-8796.121.2/BUILD xnuloader/loader.obj xnuloader/loader.efi userland/root.apfs userland/system.apfs userland/obj
