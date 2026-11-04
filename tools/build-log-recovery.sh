#!/bin/bash
# Build an isolated static PID1 recovery RAM disk; never changes normal images.
# Usage: tools/build-log-recovery.sh NEW_OUTPUT_DIRECTORY GPU_FIRMWARE_DIRECTORY
# LOG_RECOVERY_NAME=hakios-audio-XXXXXX selects one exact capture; empty selects latest.
# LOG_RECOVERY_REPLAY=1 explicitly builds journal-replay-then-readonly recovery.
set -euo pipefail
umask 077
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[ "$#" -eq 2 ] || { echo "usage: $0 NEW_OUTPUT_DIRECTORY GPU_FIRMWARE_DIRECTORY" >&2; exit 2; }
OUTPUT_DIR=$1
GPU_FW_DIR=$2
CAPTURE_NAME=${LOG_RECOVERY_NAME:-}
REPLAY=${LOG_RECOVERY_REPLAY:-0}
[[ "$REPLAY" == 0 || "$REPLAY" == 1 ]] || { echo "LOG_RECOVERY_REPLAY must be 0 or 1" >&2; exit 2; }
[[ -z "$CAPTURE_NAME" || "$CAPTURE_NAME" =~ ^hakios-audio-[[:alnum:]]{6}$ ]] || {
    echo "LOG_RECOVERY_NAME must be empty or hakios-audio- followed by six ASCII letters/digits" >&2; exit 2;
}
# Force ASCII validation independent of the caller's locale.
LC_ALL=C
export LC_ALL
[[ -z "$CAPTURE_NAME" || "$CAPTURE_NAME" =~ ^hakios-audio-[a-zA-Z0-9]{6}$ ]] || exit 2
[ ! -e "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ] || { echo "recovery output directory already exists" >&2; exit 2; }
GPU_FILES=(a660_gmu.bin a660_sqe.fw a660_zap.mdt a660_zap.b02)
for file in "${GPU_FILES[@]}"; do
    [ -f "$GPU_FW_DIR/$file" ] && [ -s "$GPU_FW_DIR/$file" ] || { echo "missing GPU firmware: $file" >&2; exit 2; }
done
mkdir -p "$(dirname "$OUTPUT_DIR")"
mkdir "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
GPU_FW_DIR=$(cd "$GPU_FW_DIR" && pwd)
mkdir -p "$OUTPUT_DIR/root/sbin" "$OUTPUT_DIR/root/fw"
source "$REPO/xnu-env.sh"
CC=(xnu-clang -arch arm64 -Os -static -nostdlib -ffreestanding -fno-stack-protector -fno-builtin -Wall -Wextra)
"${CC[@]}" -I"$REPO/userland/libmini" -c "$REPO/userland/libmini/mini.c" -o "$OUTPUT_DIR/mini.o"
"${CC[@]}" -c "$REPO/userland/libmini/start.S" -o "$OUTPUT_DIR/start.o"
"${CC[@]}" -I"$REPO/userland/libmini" "-DLOG_RECOVERY_REPLAY=$REPLAY" "-DLOG_RECOVERY_NAME=\"$CAPTURE_NAME\"" \
    -Wl,-e,_start -Wl,-pagezero_size,0x100000000 -o "$OUTPUT_DIR/root/sbin/init" \
    "$REPO/userland/src/log_recover.c" "$OUTPUT_DIR/mini.o" "$OUTPUT_DIR/start.o"
for file in "${GPU_FILES[@]}"; do cp "$GPU_FW_DIR/$file" "$OUTPUT_DIR/root/fw/$file"; done
python3 "$REPO/tools/apfs/mkapfs.py" -z --plain fw -o "$OUTPUT_DIR/ramdisk.apfs" "redwood=$OUTPUT_DIR/root"
bash "$REPO/tools/apfs/check-image.sh" "$OUTPUT_DIR/ramdisk.apfs" "redwood=$OUTPUT_DIR/root"
python3 - "$REPO" "$OUTPUT_DIR" "$CAPTURE_NAME" "$REPLAY" <<'PY'
import hashlib, importlib.util, json, pathlib, sys, time
repo, output = map(pathlib.Path, sys.argv[1:3])
name, replay = sys.argv[3], sys.argv[4] == '1'
spec = importlib.util.spec_from_file_location('recovery_package', repo / 'tools/spacewar-log-recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = dict(format='hakios-log-recovery-root-v1',
    ramdisk_sha256=sha(output / 'ramdisk.apfs'), helper_sha256=sha(output / 'root/sbin/init'),
    helper_source_sha256=sha(repo / 'userland/src/log_recover.c'), pid1_helper=True,
    mount_readonly=not replay, mount_policy='replay-then-readonly' if replay else 'readonly',
    journal_replay=replay, exact_log_name=name, frame_chunk_bytes=768, frame_file_cap=2097152,
    runtime_hardware_services='off',
    gpu_firmware={p.name: sha(p) for p in sorted((output / 'root/fw').iterdir())})
(output / 'ramdisk.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
line = ' '.join(f'{k}={v}' for k, v in module.SAFE_ARGUMENTS.items()) + f' epoch={int(time.time())}'
(output / 'cmdline').write_text(line + '\n')
print('Recovery root checked; mount policy: ' + manifest['mount_policy'])
print('RAM disk: ' + str(output / 'ramdisk.apfs'))
PY
