#!/system/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Optional Magisk service.d entry. Root installs this only after reviewing the
# matching private identity and verified B image. It never reboots Android.
PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/magisk
export PATH
attempt=0
while [ "$(getprop sys.boot_completed)" != 1 ]; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 180 ] || exit 0
    sleep 1
done
exec /system/bin/sh /data/adb/spacewar-boot-menu/spacewar-boot-menu.sh --prepare-next-boot
