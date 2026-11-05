#!/system/bin/sh
# Read-only Android/recovery facts. This file is prepared, not executed here.
# From the host, once the intended phone is selected and root is available:
#   adb shell -T "su -c 'sh -s'" < tools/spacewar-android-survey.sh > android-read-only-survey.txt
# A recovery root shell can instead receive it with `adb shell -T sh -s`.
# Use shell -T: exec-out does not forward the script on standard input.
# No set-active, mark-successful, mount, resize, flash, format, dd output, GPT
# mutation, property mutation, or dm-crypt table/key dump is performed.
# This text report is not a partition backup. Keep later hashed backups private.

section() { printf '\n=== %s ===\n' "$1"; }
read_value() { if [ -r "$1" ]; then printf '%s: ' "$1"; cat "$1"; fi; }

section 'Identity and boot state (selected properties only)'
id
uname -a
for key in ro.product.device ro.product.vendor.device ro.product.model \
    ro.build.fingerprint ro.vendor.build.fingerprint ro.boot.slot_suffix \
    ro.boot.slot ro.boot.flash.locked ro.boot.vbmeta.device_state \
    ro.boot.verifiedbootstate ro.boot.veritymode ro.boot.dynamic_partitions \
    ro.boot.dynamic_partitions_retrofit ro.virtual_ab.enabled \
    ro.virtual_ab.retrofit ro.virtual_ab.compression.enabled \
    ro.crypto.state ro.crypto.type ro.crypto.volume.contents_mode \
    ro.crypto.volume.filenames_mode ro.crypto.metadata.enabled \
    sys.boot_completed; do
    printf '%s=' "$key"
    getprop "$key"
done

section 'Boot-control read operations'
if command -v bootctl >/dev/null 2>&1; then
    for operation in get-number-slots get-current-slot get-active-boot-slot \
        get-snapshot-merge-status; do
        printf '\nbootctl %s\n' "$operation"
        bootctl "$operation" 2>&1
        printf 'exit=%s\n' "$?"
    done
    for slot in 0 1; do
        for operation in is-slot-bootable is-slot-marked-successful; do
            printf '\nbootctl %s %s\n' "$operation" "$slot"
            bootctl "$operation" "$slot" 2>&1
            printf 'exit=%s\n' "$?"
        done
    done
else
    printf 'bootctl unavailable\n'
fi

section 'Partition names and kernel geometry'
ls -l /dev/block/by-name 2>&1
cat /proc/partitions
for block in /sys/class/block/sd*; do
    [ -d "$block" ] || continue
    printf '\n%s -> %s\n' "$block" "$(readlink -f "$block/device")"
    for field in size start partition ro queue/logical_block_size \
        queue/physical_block_size queue/minimum_io_size queue/optimal_io_size \
        device/vendor device/model device/rev; do
        read_value "$block/$field"
    done
done

section 'Physical UFS disks and GPT information (read operations only)'
for block in /sys/class/block/sd?; do
    [ -d "$block" ] && [ ! -f "$block/partition" ] || continue
    disk=/dev/block/${block##*/}
    printf '\n%s\n' "$disk"
    blockdev --getsize64 "$disk" 2>&1
    blockdev --getss "$disk" 2>&1
    blockdev --getpbsz "$disk" 2>&1
    if command -v sgdisk >/dev/null 2>&1; then
        sgdisk --print "$disk" 2>&1
        # Android's sgdisk also exposes a complete read-only dump.
        sgdisk --android-dump "$disk" 2>&1
        for partition in "$block"/${block##*/}*; do
            [ -r "$partition/partition" ] || continue
            number=$(cat "$partition/partition")
            sgdisk --info="$number" "$disk" 2>&1
        done
    else
        printf 'sgdisk unavailable; capture primary/backup GPT bytes separately\n'
    fi
done

section 'Mounted filesystems and userdata space'
cat /proc/mounts
cat /proc/self/mountinfo
cat /proc/filesystems
df -k /data /metadata /sdcard 2>&1
for fstab in /vendor/etc/fstab* /odm/etc/fstab* /first_stage_ramdisk/fstab* \
    /system/etc/fstab* /fstab.*; do
    [ -f "$fstab" ] || continue
    printf '\n%s\n' "$fstab"
    cat "$fstab"
done

section 'Device-mapper identities and dependencies (no tables or keys)'
for block in /sys/class/block/dm-*; do
    [ -d "$block" ] || continue
    read_value "$block/dm/name"
    read_value "$block/size"
    ls -l "$block/slaves" 2>&1
done
if command -v dmsetup >/dev/null 2>&1; then
    dmsetup info -c 2>&1
    dmsetup ls --tree 2>&1
fi

section 'Dynamic partitions and snapshot status'
if command -v lpdump >/dev/null 2>&1; then lpdump 2>&1; fi
if command -v snapshotctl >/dev/null 2>&1; then snapshotctl dump 2>&1; fi
if command -v update_engine_client >/dev/null 2>&1; then update_engine_client --status 2>&1; fi

section 'Power and recovery tooling'
dumpsys battery 2>&1
for tool in su bootctl sgdisk blockdev sha256sum dd lpdump snapshotctl \
    fsck.f2fs resize.f2fs fsck.ext4 resize2fs; do
    command -v "$tool" 2>/dev/null
done
printf '\nEnd of read-only facts. No partitions or slot flags were modified.\n'
