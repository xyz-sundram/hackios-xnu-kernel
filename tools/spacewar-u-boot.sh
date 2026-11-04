#!/bin/bash
# Build the source-only Phone (1) U-Boot RAM menu. No phone commands.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
UB=${U_BOOT:-$ROOT/u-boot}
PROFILE=${SPACEWAR_UBOOT_PROFILE:-menu}
case "$PROFILE" in
    menu) CONFIG=hakios_spacewar_defconfig ;;
    usbdiag) CONFIG=hakios_spacewar_usbdiag_defconfig ;;
    storage) CONFIG=hakios_spacewar_storage_defconfig ;;
    loaderdiag) CONFIG=hakios_spacewar_loaderdiag_defconfig ;;
    *) echo 'SPACEWAR_UBOOT_PROFILE must be menu, usbdiag, storage or loaderdiag' >&2; exit 1 ;;
esac
OUT=${SPACEWAR_UBOOT_OUT:-$UB/build-spacewar-$PROFILE}
BASE=ece349ade2973e220f524ce59e59711cc919263f # U-Boot v2026.07
COMMON=$ROOT/uefi/u-boot
DEVICE=$ROOT/devices/spacewar/u-boot
[ -f "$COMMON/hakios.patch" ] || { echo 'Sync the pinned hakios-uefi dependency first.' >&2; exit 1; }
if [ ! -d "$UB" ]; then
    git clone --depth 1 --branch v2026.07 https://github.com/u-boot/u-boot.git "$UB"
fi
[ "$(git -C "$UB" rev-parse HEAD)" = "$BASE" ] || { echo "Unexpected U-Boot base in $UB; preserving it." >&2; exit 1; }
python3 "$ROOT/tools/spacewar-uboot-patches.py" "$UB" \
    "$COMMON/hakios.patch" "$DEVICE/hakios.patch" "$DEVICE/lld-layout.patch" \
    "$DEVICE/simplefb-rotation.patch" "$DEVICE/early-trace.patch" \
    "$DEVICE/initcall-trace.patch" "$DEVICE/spmi-v5-addressing.patch" \
    "$DEVICE/spmi-byte-count.patch" "$DEVICE/spmi-timeout.patch" \
    "$DEVICE/spmi-skip-empty-apid.patch" "$DEVICE/spmi-trace.patch" \
    "$DEVICE/stdio-trace.patch" "$DEVICE/input-console-init-order.patch" \
    "$DEVICE/sc7280-ufs-qref.patch" "$DEVICE/rpmhpd-bounds.patch"
cp -r "$COMMON/files/." "$UB/"
cp -r "$DEVICE/files/." "$UB/"
# This command is supplied by the common overlay, rather than upstream HEAD.
# Copying it above restores the canonical base before every application.
git -C "$UB" apply "$DEVICE/abl-hkub-ramdisk.patch"
mkdir -p "$OUT"
args=(-s -C "$UB" "O=$OUT")
if command -v "${CROSS_COMPILE:-aarch64-linux-gnu-}gcc" >/dev/null; then
    args+=("CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}")
else
    # Clang's integrated assembler and LLVM binutils avoid installing a system
    # cross-GCC. Build the actual binary; llvm-objcopy need not make unused SREC.
    args+=(CROSS_COMPILE=aarch64-linux-gnu- CC=clang LD=ld.lld AR=llvm-ar
           NM=llvm-nm STRIP=llvm-strip OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump
           READELF=llvm-readelf CLANG_FLAGS=--target=aarch64-linux-gnu)
fi
if [ "${SPACEWAR_UBOOT_TRACE:-0}" = 1 ]; then
    args+=(KAFLAGS=-DHAKIOS_SPACEWAR_EARLY_TRACE)
fi
if [ "${SPACEWAR_UBOOT_INIT_TRACE:-0}" = 1 ]; then
    args+=(KCFLAGS=-DHAKIOS_SPACEWAR_INIT_TRACE)
fi
make "${args[@]}" "$CONFIG"
make "${args[@]}" -j"${JOBS:-8}" u-boot.bin checkarmreloc
python3 "$ROOT/tools/spacewar-uboot-layout.py" "$OUT/u-boot" "$OUT/u-boot.bin" --dtb "$OUT/dts/dt.dtb"
printf 'Phone (1) U-Boot: %s/u-boot.bin\n' "$OUT"
