#!/system/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Prepare the verified HakiOS B menu through Android's boot HAL; never flash.
# bootctl semantics: android.googlesource.com/platform/system/extras/+/main/bootctl/bootctl.cpp
set -eu
PATH=/system/bin:/system/xbin:/vendor/bin:/data/adb/magisk
export PATH
LC_ALL=C
export LC_ALL
BOOTCTL=/data/adb/magisk/bootctl
SGDISK=/system/bin/sgdisk
IDENTITY=/data/adb/spacewar-boot-menu/identity.env
fail() { printf 'spacewar-boot-menu: %s; no reboot requested\n' "$*" >&2; exit 1; }
MODE=${1-}
case "$MODE" in --check|--prepare-next-boot|--reboot-menu) shift ;; *) fail 'use --check, --prepare-next-boot or --reboot-menu' ;; esac
if [ "${1-}" = --identity ] && [ "$#" = 2 ]; then IDENTITY=$2; shift 2; fi
[ "$#" = 0 ] || fail 'unexpected arguments'
[ "$(id -u)" = 0 ] || fail 'root is required'
[ -x "$BOOTCTL" ] && [ -x "$SGDISK" ] || fail 'bootctl or sgdisk unavailable'
[ -f "$IDENTITY" ] && [ ! -L "$IDENTITY" ] || fail 'installed identity missing or linked'
[ "$(stat -c '%u:%a' "$IDENTITY")" = 0:600 ] || fail 'identity must be root-owned mode 0600'
[ "$(wc -c < "$IDENTITY")" -le 4096 ] || fail 'identity file too large'
FORMAT_VERSION= DEVICE= ANDROID_SLOT= MENU_SLOT= BOOT_B_NODE= BOOT_B_BYTES= BOOT_B_SHA256=
DISK_GUID= BOOT_A_GUID= BOOT_B_GUID= GPT_DISK_NODE= GPT_BLOCK_SIZE=
GPT_ENTRY_COUNT= GPT_ENTRIES_LBA= GPT_FIRST_USABLE_LBA= GPT_LAST_USABLE_LBA=
BOOT_A_GPT_INDEX= BOOT_B_GPT_INDEX= BOOT_A_FIRST_LBA= BOOT_B_FIRST_LBA= BOOT_PARTITION_BLOCKS=
seen='|'
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; *=*) key=${line%%=*}; value=${line#*=} ;; *) fail 'malformed identity line' ;; esac
    case "$seen" in *"|$key|"*) fail 'duplicate identity field' ;; esac
    seen="$seen$key|"
    # Assign only known fields; never evaluate or source this file.
    case "$key" in
        FORMAT_VERSION) FORMAT_VERSION=$value ;; DEVICE) DEVICE=$value ;;
        ANDROID_SLOT) ANDROID_SLOT=$value ;; MENU_SLOT) MENU_SLOT=$value ;;
        BOOT_B_NODE) BOOT_B_NODE=$value ;; BOOT_B_BYTES) BOOT_B_BYTES=$value ;;
        BOOT_B_SHA256) BOOT_B_SHA256=$value ;; DISK_GUID) DISK_GUID=$value ;;
        BOOT_A_GUID) BOOT_A_GUID=$value ;; BOOT_B_GUID) BOOT_B_GUID=$value ;;
        GPT_DISK_NODE) GPT_DISK_NODE=$value ;; GPT_BLOCK_SIZE) GPT_BLOCK_SIZE=$value ;;
        GPT_ENTRY_COUNT) GPT_ENTRY_COUNT=$value ;; GPT_ENTRIES_LBA) GPT_ENTRIES_LBA=$value ;;
        GPT_FIRST_USABLE_LBA) GPT_FIRST_USABLE_LBA=$value ;; GPT_LAST_USABLE_LBA) GPT_LAST_USABLE_LBA=$value ;;
        BOOT_A_GPT_INDEX) BOOT_A_GPT_INDEX=$value ;; BOOT_B_GPT_INDEX) BOOT_B_GPT_INDEX=$value ;;
        BOOT_A_FIRST_LBA) BOOT_A_FIRST_LBA=$value ;; BOOT_B_FIRST_LBA) BOOT_B_FIRST_LBA=$value ;;
        BOOT_PARTITION_BLOCKS) BOOT_PARTITION_BLOCKS=$value ;; *) fail 'unknown identity field' ;;
    esac
done < "$IDENTITY"
[ "$FORMAT_VERSION:$DEVICE:$ANDROID_SLOT:$MENU_SLOT" = 1:spacewar:0:1 ] || fail 'identity profile mismatch'
[ "$BOOT_B_NODE" = /dev/block/by-name/boot_b ] || fail 'unexpected B partition path'
[ "$BOOT_B_BYTES:$GPT_BLOCK_SIZE:$BOOT_PARTITION_BLOCKS" = 100663296:4096:24576 ] || fail 'unexpected partition geometry'
[ "$GPT_ENTRY_COUNT:$GPT_ENTRIES_LBA:$GPT_FIRST_USABLE_LBA:$GPT_LAST_USABLE_LBA" = 96:2:6:1572858 ] || fail 'unexpected factory GPT geometry'
case "$GPT_DISK_NODE" in /dev/block/sd[a-z]) ;; *) fail 'unexpected GPT disk path' ;; esac
printf '%s\n' "$BOOT_B_SHA256" | grep -Eq '^[0-9a-f]{64}$' || fail 'invalid expected B hash'
for guid in "$DISK_GUID" "$BOOT_A_GUID" "$BOOT_B_GUID"; do
    printf '%s\n' "$guid" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' || fail 'invalid GUID'
    [ "$guid" != 00000000-0000-0000-0000-000000000000 ] || fail 'zero GUID'
done
[ "$BOOT_A_GUID" != "$BOOT_B_GUID" ] || fail 'boot GUIDs must differ'
for number in "$BOOT_A_GPT_INDEX" "$BOOT_B_GPT_INDEX" "$BOOT_A_FIRST_LBA" "$BOOT_B_FIRST_LBA"; do
    printf '%s\n' "$number" | grep -Eq '^[1-9][0-9]{0,11}$' || fail 'invalid index or LBA'
done
[ "$BOOT_A_GPT_INDEX" -le 256 ] && [ "$BOOT_B_GPT_INDEX" -le 256 ] && [ "$BOOT_A_GPT_INDEX" != "$BOOT_B_GPT_INDEX" ] || fail 'invalid GPT indexes'

check_android() {
    [ "$(getprop ro.product.device | tr '[:upper:]' '[:lower:]')" = spacewar ] || fail 'wrong device'
    [ "$(getprop sys.boot_completed)" = 1 ] || fail 'Android boot is incomplete'
    [ "$(getprop ro.boot.slot_suffix)" = _a ] || fail 'Android suffix is not A'
    [ "$("$BOOTCTL" get-number-slots)" = 2 ] || fail 'expected exactly two slots'
    [ "$("$BOOTCTL" get-current-slot)" = 0 ] || fail 'Android A is not current'
    "$BOOTCTL" is-slot-bootable 0 >/dev/null || fail 'Android A is not bootable'
    "$BOOTCTL" is-slot-marked-successful 0 >/dev/null || fail 'Android A is not successful'
    snapshot=$("$BOOTCTL" get-snapshot-merge-status) || fail 'snapshot state unavailable'
    case "$snapshot" in none|MergeStatus::NONE) ;; *) fail 'snapshot merge is not NONE' ;; esac
}
check_android
[ "$(blockdev --getss "$GPT_DISK_NODE")" = "$GPT_BLOCK_SIZE" ] || fail 'logical block size changed'
[ "$(blockdev --getsize64 "$BOOT_B_NODE")" = "$BOOT_B_BYTES" ] || fail 'B size changed'
[ "$(readlink -f /dev/block/by-name/boot_a)" = "$GPT_DISK_NODE$BOOT_A_GPT_INDEX" ] || fail 'A device mapping changed'
[ "$(readlink -f "$BOOT_B_NODE")" = "$GPT_DISK_NODE$BOOT_B_GPT_INDEX" ] || fail 'B device mapping changed'
verification=$("$SGDISK" --verify "$GPT_DISK_NODE" 2>&1) || fail 'GPT verification failed'
layout=$("$SGDISK" --print "$GPT_DISK_NODE") || fail 'cannot inspect GPT'
actual_disk=$(printf '%s\n' "$layout" | sed -n 's/^Disk identifier (GUID): //p' | tr '[:upper:]' '[:lower:]')
[ "$actual_disk" = "$DISK_GUID" ] || fail 'disk GUID changed'
actual_entries=$(printf '%s\n' "$layout" | sed -n 's/^Partition table holds up to \([0-9][0-9]*\) entries$/\1/p')
actual_table=$(printf '%s\n' "$layout" | sed -n 's/^Main partition table begins at sector \([0-9][0-9]*\) and ends at sector \([0-9][0-9]*\)$/\1:\2/p')
actual_usable=$(printf '%s\n' "$layout" | sed -n 's/^First usable sector is \([0-9][0-9]*\), last usable sector is \([0-9][0-9]*\)$/\1:\2/p')
[ "$actual_entries" = "$GPT_ENTRY_COUNT" ] && [ "$actual_table" = "$GPT_ENTRIES_LBA:4" ] &&
    [ "$actual_usable" = "$GPT_FIRST_USABLE_LBA:$GPT_LAST_USABLE_LBA" ] || fail 'factory GPT geometry changed'

# The identity-pinned OEM table uses 96 entries and a one-sector gap. These
# exact gdisk paragraphs describe that factory layout, not a damaged GPT.
# Consume only these complete paragraphs; every other diagnostic is refused.
factory_gap=$(cat <<'EOF'
Warning: There is a gap between the main partition table (ending sector 4)
and the first usable sector (6). This is helpful in some exotic configurations,
but is unusual. The util-linux fdisk program often creates disks like this.
Using 'j' on the experts' menu can adjust this gap.
EOF
)
factory_size=$(cat <<'EOF'
Warning: The size of the partition table (12288 bytes) is less than the minimum
required by the GPT specification. Most OSes and tools seem to work fine on
such disks, but this is a violation of the GPT specification and so may cause
problems.
EOF
)
remaining=$verification
gap_seen=0; size_seen=0
newline='
'
while :; do
    remaining=$(printf '%s\n' "$remaining" | sed '/./,$!d')
    case "$remaining" in
        "$factory_gap$newline"*)
            [ "$gap_seen" = 0 ] || fail 'duplicate GPT diagnostic'
            remaining=${remaining#"$factory_gap$newline"}; gap_seen=1 ;;
        "$factory_size$newline"*)
            [ "$size_seen" = 0 ] || fail 'duplicate GPT diagnostic'
            remaining=${remaining#"$factory_size$newline"}; size_seen=1 ;;
        *) break ;;
    esac
done
summary=$(printf '%s\n' "$remaining" | sed '/^$/d')
size_pattern='[0-9]+([.][0-9]+)? (bytes|[KMGT]iB)'
first_pattern="^No problems found[.] [0-9]+ free sectors [(]$size_pattern[)] available in [0-9]+$"
last_pattern="^segments, the largest of which is [0-9]+ [(]$size_pattern[)] in size[.]$"
printf '%s\n' "$summary" | grep -Eq "$first_pattern" || fail 'GPT verification did not finish cleanly'
printf '%s\n' "$summary" | grep -Eq "$last_pattern" || fail 'GPT verification summary is incomplete'
unexpected=$(printf '%s\n' "$summary" | grep -Ev "$first_pattern|$last_pattern" || :)
[ -z "$unexpected" ] && [ "$(printf '%s\n' "$summary" | wc -l)" -eq 2 ] || fail 'unrecognized GPT verification diagnostic'
check_partition() {
    index=$1; label=$2; expected_guid=$3; first=$4
    info=$("$SGDISK" "--info=$index" "$GPT_DISK_NODE") || fail 'cannot inspect boot partition'
    actual_guid=$(printf '%s\n' "$info" | sed -n 's/^Partition unique GUID: //p' | tr '[:upper:]' '[:lower:]')
    actual_name=$(printf '%s\n' "$info" | sed -n "s/^Partition name: '\(.*\)'$/\1/p")
    actual_first=$(printf '%s\n' "$info" | sed -n 's/^First sector: \([0-9][0-9]*\).*$/\1/p')
    actual_last=$(printf '%s\n' "$info" | sed -n 's/^Last sector: \([0-9][0-9]*\).*$/\1/p')
    [ "$actual_guid" = "$expected_guid" ] && [ "$actual_name" = "$label" ] &&
        [ "$actual_first" = "$first" ] && [ "$actual_last" = "$((first + BOOT_PARTITION_BLOCKS - 1))" ] || fail 'boot partition identity or bounds changed'
}
check_partition "$BOOT_A_GPT_INDEX" boot_a "$BOOT_A_GUID" "$BOOT_A_FIRST_LBA"
check_partition "$BOOT_B_GPT_INDEX" boot_b "$BOOT_B_GUID" "$BOOT_B_FIRST_LBA"
actual_hash=$(sha256sum "$BOOT_B_NODE") || fail 'cannot hash B image'
actual_hash=${actual_hash%% *}
[ "$actual_hash" = "$BOOT_B_SHA256" ] || fail 'B image hash changed'
# Recheck HAL state after the read-only GPT/hash work.
check_android
active=$("$BOOTCTL" get-active-boot-slot) || fail 'cannot read next slot'
case "$active" in 0|1) ;; *) fail 'unexpected active slot' ;; esac
if [ "$MODE" = --check ]; then
    printf 'spacewar-boot-menu: identity, Android A, GPT and B image verified; read-only check complete\n'
    exit 0
fi
case "$active" in
    0) "$BOOTCTL" set-active-boot-slot 1 || fail 'HAL refused next menu slot' ;;
    1) ;; *) fail 'unexpected active slot' ;;
esac
[ "$("$BOOTCTL" get-active-boot-slot)" = 1 ] || fail 'HAL did not select menu B'
if [ "$MODE" = --reboot-menu ]; then
    printf 'spacewar-boot-menu: verified menu B selected; rebooting by explicit request\n'
    reboot
else
    printf 'spacewar-boot-menu: verified menu B selected for next boot; Android stays running\n'
fi
