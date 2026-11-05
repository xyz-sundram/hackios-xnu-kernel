#!/bin/bash
# Build the XNU loader as a freestanding AArch64 UEFI application.
set -e
cd "$(dirname "$0")"
OUT=${XNULOADER_OUT:-.}
TRACE=${HAKIOS_LOADER_START_TRACE:-0}
PERSIST_LOG=${HAKIOS_PERSIST_LOG:-0}
USB_RECOVER=${HAKIOS_USB_RECOVER:-0}
case "$TRACE" in 0|1) ;; *) echo 'HAKIOS_LOADER_START_TRACE must be 0 or 1' >&2; exit 1 ;; esac
case "$PERSIST_LOG" in 0|1) ;; *) echo 'HAKIOS_PERSIST_LOG must be 0 or 1' >&2; exit 1 ;; esac
case "$USB_RECOVER" in 0|1) ;; *) echo 'HAKIOS_USB_RECOVER must be 0 or 1' >&2; exit 1 ;; esac
# the iPhone's identity for XNU, from the device tree of the builder's own IPSW (not in the repository)
[ -f identity.h ] || ./mkidentity.sh
mkdir -p "$OUT"

clang --target=aarch64-unknown-windows -ffreestanding -fno-builtin -fno-stack-protector -fshort-wchar \
  -mgeneral-regs-only -O1 -Wall -Wextra -Wno-unused-parameter \
  -DHAKIOS_LOADER_START_TRACE="$TRACE" -DHAKIOS_PERSIST_LOG="$PERSIST_LOG" \
  -DHAKIOS_USB_RECOVER="$USB_RECOVER" \
  -c loader.c -o "$OUT/loader.obj"
# /base: UEFI loads an image at its preferred address when that is free. The PE default (0x140000000)
# is in the middle of the phone's largest block of RAM, which XNU needs in one piece.
lld-link /subsystem:efi_application /entry:efi_main /nodefaultlib /base:0xA1000000 /out:"$OUT/loader.efi" "$OUT/loader.obj"
ls -la "$OUT/loader.efi"
