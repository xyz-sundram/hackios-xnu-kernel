#!/bin/bash
# Refresh prebuilt/ from what is built in this tree: the kernel, the loader, our user-space programs
# and the pieces of one phone's UEFI. DEVICE=spacewar selects another device (default: redwood).
# DTB=spacewar.dtb selects a current-ROM tree instead of Mu-Silicium's bundled tree.
# With them somebody without the cross toolchain can still make the images and run the system.
set -e
cd "$(dirname "$(readlink -f "$0")")/.."
# KERNEL=... to take the kernel from a tree built elsewhere (xnu/apply.sh makes one anywhere)
K=${KERNEL:-hakios-kernel}
DEVICE=${DEVICE:-redwood}
MU=Mu-Silicium
DEVICE_PREBUILT="devices/$DEVICE/prebuilt"
mkdir -p prebuilt/userland "$DEVICE_PREBUILT"
USERLAND_BINS=(init child stage0 hello_dyn svc_test surf_test rwtouch mg_test cpu_test sound_test libhakios.dylib libhakios.sb.dylib
	libhakios.media.dylib libhakios.app.dylib libhakios.tweaks.dylib HakiosAudio net_test)
MISSING=()
USERLAND_SOURCES=()
[ -f "$K" ] || MISSING+=("$K")
for f in hello "${USERLAND_BINS[@]}"; do
	if [ "$f" = hello ] && [ -f userland/hello ]; then
		USERLAND_SOURCES+=(userland/hello)
	elif [ -f "userland/obj/$f" ]; then
		USERLAND_SOURCES+=("userland/obj/$f")
	elif [ -f "prebuilt/userland/$f" ]; then
		# Keep the checked-in/preexisting program when this checkout has not rebuilt userland yet.
		USERLAND_SOURCES+=("prebuilt/userland/$f")
	else
		MISSING+=("userland/obj/$f (or prebuilt/userland/$f)")
	fi
done
if [ ${#MISSING[@]} -ne 0 ]; then
	echo "update-prebuilt.sh: build outputs are missing:" >&2
	printf '  %s\n' "${MISSING[@]}" >&2
	echo "Build the kernel and missing programs before refreshing prebuilts." >&2
	exit 1
fi
cp "$K" prebuilt/hakios-kernel
# The loader in prebuilt/ is built with the PLACEHOLDER identity (xnuloader/mkidentity.sh --placeholder: no Apple data), in a
# temporary copy so that the real xnuloader/identity.h of this tree, made from your own IPSW, is left alone and never copied
# here. It boots the open kernel (make qemu-open); to start Darwin, build the loader yourself (make loader).
if command -v lld-link >/dev/null && command -v clang >/dev/null; then
	T=$(mktemp -d)
	mkdir "$T/xnuloader"
	cp xnuloader/loader.c xnuloader/efi.h xnuloader/build.sh xnuloader/mkidentity.sh xnuloader/mkidentity.py xnuloader/im4p.py "$T/xnuloader/"
	( cd "$T/xnuloader" && PLACEHOLDER=1 ./mkidentity.sh >/dev/null && ./build.sh >/dev/null ) && cp "$T/xnuloader/loader.efi" prebuilt/loader.efi ||
		echo "update-prebuilt.sh: the placeholder loader did not build; prebuilt/loader.efi kept"
	rm -rf "$T"
else
	echo "update-prebuilt.sh: no lld-link or clang here; prebuilt/loader.efi kept"
fi
for f in "${USERLAND_SOURCES[@]}"; do
	[ "$f" = "prebuilt/userland/${f##*/}" ] || cp "$f" prebuilt/userland/
done
FD="$MU/Build/${DEVICE}Pkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd"
DTB=${DTB:-$MU/Resources/DTBs/${DEVICE}.dtb}
if [ -f "$FD" ]; then
	for f in "$DTB" "$MU/Resources/ramdisk" "$MU/Resources/Scripts/mkbootimg.py"; do
		[ -f "$f" ] || { echo "update-prebuilt.sh: missing $f for DEVICE=$DEVICE" >&2; exit 1; }
	done
	# build_uefi.py leaves a stock shim without XNUP copying in Mu/BootShim. Build
	# the HakiOS shim with this device's addresses rather than copying that file.
	SHIM_BUILD=$(mktemp -d)
	if ! ./mkxnuimg.py -d "$DEVICE" --dtb "$DTB" -o "$SHIM_BUILD/uefi.img" --shim-output "$SHIM_BUILD/BootShim.bin"; then
		rm -rf "$SHIM_BUILD"
		exit 1
	fi
	cp "$FD" "$DEVICE_PREBUILT/SILICIUM_UEFI.fd"
	cp "$SHIM_BUILD/BootShim.bin" "$DEVICE_PREBUILT/BootShim.bin"
	rm -rf "$SHIM_BUILD"
	CONFIG="$MU/Resources/Configs/$DEVICE.toml"
	[ ! -f "$CONFIG" ] || cp "$CONFIG" "$DEVICE_PREBUILT/boot-config.toml"
	cp "$DTB" "$DEVICE_PREBUILT/device-tree.dtb"
	cp "$MU/Resources/ramdisk" "$MU/Resources/Scripts/mkbootimg.py" "$DEVICE_PREBUILT/"
else
	echo "update-prebuilt.sh: no built UEFI FD at $FD; $DEVICE_PREBUILT UEFI prebuilts kept"
fi
{
	echo "Built $(date '+%Y-%m-%d %H:%M') from this tree."
	echo
	sha256sum prebuilt/hakios-kernel prebuilt/loader.efi prebuilt/userland/* "$DEVICE_PREBUILT"/* 2>/dev/null
} > prebuilt/SHA256SUMS
ls -la prebuilt prebuilt/userland "$DEVICE_PREBUILT"
