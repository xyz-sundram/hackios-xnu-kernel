#!/usr/bin/env python3
"""Package a Phone (1) RAM-only log recovery image from explicit local inputs.

This packages and audits files only. It never invokes fastboot, flashes a
partition, changes a slot, or copies private identity data into documentation.
The separate recovery-root builder supplies the read-only HFS PID1 helper.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_LIMIT = 0x3f00000
BOOTARGS_LIMIT = 607
SAFE_ARGUMENTS = {
    "loader_return_after": "0", "bootcpu": "7", "cpus": "7", "buttons": "1",
    "thermal": "1", "thermal_stop": "105", "gpu": "1", "adreno_speed": "3",
    "touchscreen": "1", "touch_spacewar": "1", "touch_steps": "6", "touch_seconds": "0",
    "touch_live": "1", "touch_lat": "0", "hakios_debug": "1", "cpufreq": "2",
    "spacewar_install": "1", "spacewar_storage": "1", "ufs": "2", "ufs_write": "1",
    "var_hfs": "ufs_darwin_data", "spacewar_services": "2", "adsp": "0", "battmgr": "0",
    "haptic": "0", "wifi": "0", "sound": "0",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_cmdline(text, version):
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", version), "invalid kernel version")
    words = text.split()
    parsed = {}
    for word in words:
        require(word.count("=") == 1, "recovery arguments must be name=value words")
        name, value = word.split("=", 1)
        require(name not in parsed, "duplicate recovery argument")
        require(name in SAFE_ARGUMENTS or name == "epoch", "unexpected recovery argument")
        parsed[name] = value
    require(all(parsed.get(k) == v for k, v in SAFE_ARGUMENTS.items()),
            "recovery arguments do not match the approved disabled-services profile")
    require(re.fullmatch(r"[0-9]{1,20}", parsed.get("epoch", "")), "missing or invalid epoch")
    require(int(parsed["epoch"]) < 1 << 64, "epoch exceeds uint64")
    line = " ".join(words)
    # The real loader chooses these addresses; count their maximum-width form.
    prefix = ("debug=0x8 rd=md0 hv_wdt_pet=1 ufs_shared_pa=0x0000000000000000 "
              "touch_shared_pa=0x0000000000000000 hakios_kver=" + version + " ")
    length = len((prefix + line).encode("ascii"))
    require(length <= BOOTARGS_LIMIT, "recovery boot arguments exceed loader budget")
    return line, length


def validate_map(data):
    require(len(data) == 152, "installed map has the wrong size")
    magic, version, size, flags = struct.unpack_from("<4I", data)
    lun, block_size = struct.unpack_from("<II", data, 32)
    root_bytes, _, reserved = struct.unpack_from("<QII", data, 136)
    require((magic, version, size, flags, lun, block_size, reserved) ==
            (0x31495753, 1, 152, 3, 4, 4096, 0), "installed map header/profile mismatch")
    disk = data[16:32]
    require(any(disk), "installed map has no disk identity")
    extents = []
    for offset, kind in ((40, "7c3457ef-0000-11aa-aa11-00306543ecac"),
                         (88, "48465300-0000-11aa-aa11-00306543ecac")):
        typ, unique = data[offset:offset + 16], data[offset + 16:offset + 32]
        first, blocks = struct.unpack_from("<QQ", data, offset + 32)
        require(typ == uuid.UUID(kind).bytes_le and any(unique) and unique != disk,
                "installed map partition type/identity mismatch")
        require(first >= 2 and blocks > 0 and first + blocks <= (1 << 64) - 1 and
                blocks <= ((1 << 64) - 1) // 4096, "installed map extent overflow")
        extents.append((unique, first, blocks))
    root, data_part = extents
    require(root[0] != data_part[0] and
            (root[1] + root[2] <= data_part[1] or data_part[1] + data_part[2] <= root[1]),
            "installed map extents overlap")
    require(0 < root_bytes <= root[2] * 4096, "installed root length is out of bounds")


def validate_ramdisk_manifest(metadata, ramdisk, allow_journal_replay=False):
    require(isinstance(metadata, dict), "recovery-root manifest is not an object")
    helper_hash = metadata.get("helper_sha256")
    require(metadata.get("format") == "hakios-log-recovery-root-v1" and
            metadata.get("ramdisk_sha256") == digest(ramdisk) and
            isinstance(helper_hash, str) and re.fullmatch(r"[0-9a-f]{64}", helper_hash) and
            metadata.get("pid1_helper") is True and
            metadata.get("runtime_hardware_services") == "off" and
            metadata.get("frame_chunk_bytes") == 768 and metadata.get("frame_file_cap") == 2097152,
            "RAM disk does not match its dedicated bounded recovery-helper manifest")
    name = metadata.get("exact_log_name", "")
    require(isinstance(name, str) and (not name or re.fullmatch(r"hakios-audio-[A-Za-z0-9]{6}", name)),
            "recovery manifest log name is not a safe basename")
    policy = metadata.get("mount_policy")
    if policy == "readonly":
        require(metadata.get("mount_readonly") is True and not metadata.get("journal_replay", False),
                "read-only recovery manifest has inconsistent mount flags")
    elif policy == "replay-then-readonly":
        require(allow_journal_replay and metadata.get("mount_readonly") is False and
                metadata.get("journal_replay") is True,
                "journal replay recovery requires the explicit --allow-journal-replay flag")
    else:
        raise ValueError("unknown recovery mount policy")
    return policy


def audit_payload(data, expected):
    require(len(data) >= 32 and len(data) <= PAYLOAD_LIMIT, "payload size is out of bounds")
    require(struct.unpack_from("<II", data) == (0x50554e58, len(data) - 8), "bad XNUP header")
    entries, at, terminated = {}, 8, False
    while at + 24 <= len(data):
        name, size, reserved = struct.unpack_from("<16sII", data, at)
        at += 24
        if not name.strip(b"\0"):
            require(size == reserved == 0 and at == len(data), "bad XNUP terminator")
            terminated = True
            break
        key = name.rstrip(b"\0").decode("ascii")
        require(not reserved and key not in entries and at + size <= len(data), "bad XNUP entry")
        entries[key] = data[at:at + size]
        at += (size + 15) & ~15
    require(terminated and entries == expected, "XNUP entries do not match the supplied inputs")


def audit_hkub(data, expected):
    require(len(data) >= 16, "truncated HKUB header")
    magic, version, count, header_size = struct.unpack_from("<4sIII", data)
    require((magic, version, count, header_size) == (b"HKUB", 1, 2, 80), "unexpected HKUB layout")
    entries, ranges = {}, []
    for i in range(count):
        name, at, size, crc, reserved = struct.unpack_from("<16sIIII", data, 16 + 32 * i)
        key = name.rstrip(b"\0").decode("ascii")
        require(not reserved and key not in entries and at >= 4096 and not at % 4096 and
                at + size <= len(data), "bad HKUB entry bounds")
        require(all(at + size <= lo or hi <= at for lo, hi in ranges), "overlapping HKUB entries")
        ranges.append((at, at + size))
        entries[key] = data[at:at + size]
        require(zlib.crc32(entries[key]) == crc, "HKUB CRC mismatch")
    require(entries == expected, "HKUB inputs differ")


def audit_image(data, uboot, dtb, container):
    require(4096 <= len(data) < 96 * 1024 * 1024 and data[:8] == b"ANDROID!", "bad Android boot image")
    require(struct.unpack_from("<I", data, 40)[0] == 3, "recovery requires Android boot header v3")
    kernel_size, ramdisk_size = struct.unpack_from("<II", data, 8)
    require(kernel_size > 0 and 4096 + kernel_size <= len(data), "truncated Android kernel")
    compressed = data[4096:4096 + kernel_size]
    dec = zlib.decompressobj(31)
    unpacked = dec.decompress(compressed)
    require(dec.eof and unpacked == uboot and dec.unused_data == dtb, "U-Boot/DTB inputs differ")
    at = (4096 + kernel_size + 4095) & ~4095
    require(ramdisk_size == len(container) and at + ramdisk_size <= len(data) and
            data[at:at + ramdisk_size] == container, "Android ramdisk differs from HKUB")


def package(args):
    paths = {name: getattr(args, name).resolve(strict=True) for name in
             ("kernel", "kernel_version_file", "kernel_manifest", "install_map", "loader", "uboot", "dtb", "ramdisk", "ramdisk_manifest", "cmdline_file")}
    blobs = {name: path.read_bytes() for name, path in paths.items()}
    version = blobs["kernel_version_file"].decode("ascii").strip()
    metadata = json.loads(blobs["kernel_manifest"])
    require(isinstance(metadata, dict) and isinstance(metadata.get("files"), dict) and
            isinstance(metadata["files"].get("kernel"), dict), "invalid kernel manifest")
    require(metadata.get("result") == "PASS" and metadata.get("driver_count") == 19 and
            metadata.get("kernel_version", metadata.get("version")) == version and
            metadata["files"]["kernel"].get("sha256") == digest(blobs["kernel"]),
            "kernel does not match its audited 19-driver manifest/version")
    require(blobs["kernel"][:4] == b"\xcf\xfa\xed\xfe", "kernel is not Mach-O arm64")
    require(blobs["loader"][:2] == b"MZ", "loader is not a UEFI application")
    require(blobs["uboot"][0x38:0x3c] == b"ARM\x64", "U-Boot lacks its arm64 Linux image header")
    require(blobs["dtb"][:4] == b"\xd0\x0d\xfe\xed", "DTB is not an FDT")
    require(blobs["ramdisk"][32:36] == b"NXSB", "RAM disk is not an APFS container")
    mount_policy = validate_ramdisk_manifest(json.loads(blobs["ramdisk_manifest"]), blobs["ramdisk"], args.allow_journal_replay)
    validate_map(blobs["install_map"])
    line, budget = validate_cmdline(blobs["cmdline_file"].decode("ascii"), version)
    context = {"purpose": "RAM-only log recovery with dedicated PID1 helper",
               "mount_policy": mount_policy, "journal_replay_requested": mount_policy == "replay-then-readonly"}
    return pack_inputs(args, paths, blobs, version, line, budget, context)


def pack_inputs(args, paths, blobs, version, line, budget, context):
    """Shared audited container writer; callers validate their runtime profile first."""
    prefix = args.output_prefix.absolute()
    outputs = {key: Path(str(prefix) + suffix) for key, suffix in
               (("payload", ".bin"), ("container", ".hkub"), ("image", ".img"), ("audit", ".audit.json"))}
    require(not any(p.exists() or p.is_symlink() for p in outputs.values()), "recovery output already exists")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".log-recovery-", dir=prefix.parent) as temporary:
        tmp = Path(temporary)
        payload, container, image = tmp / "payload.bin", tmp / "recovery.hkub", tmp / "recovery.img"
        subprocess.run([sys.executable, str(ROOT / "xnuloader/mkpayload.py"), "--usb-auto", "--cmdline", line,
                        "--kernel-version", version, "-o", str(payload), "kernel=" + str(paths["kernel"]),
                        "ramdisk=" + str(paths["ramdisk"]), "install.map=" + str(paths["install_map"])], check=True)
        subprocess.run([sys.executable, str(ROOT / "tools/mkubootimg.py"), "-d", "spacewar", "-u", str(paths["uboot"]),
                        "-l", str(paths["loader"]), "--dtb", str(paths["dtb"]), "-p", str(payload),
                        "-o", str(image), "-c", str(container)], check=True)
        packed = {"payload": payload.read_bytes(), "container": container.read_bytes(), "image": image.read_bytes()}
        audit_payload(packed["payload"], {"kernel": blobs["kernel"], "ramdisk": blobs["ramdisk"],
                      "install.map": blobs["install_map"], "cmdline": line.encode() + b"\0",
                      "kernel.ver": version.encode() + b"\0", "usb": b"2\0"})
        audit_hkub(packed["container"], {"loader": blobs["loader"], "payload": packed["payload"]})
        audit_image(packed["image"], blobs["uboot"], blobs["dtb"], packed["container"])
        report = {"result": "PASS", **context,
                  "phone_commands": 0, "installed_images_changed": False, "kernel_drivers": 19,
                  "kernel_version": version, "boot_args_bytes_worst_case": budget, "boot_args_limit": BOOTARGS_LIMIT,
                  "cmdline": line, "private_map_values_in_report": False,
                  "inputs": {name: {"file": path.name, "bytes": len(blobs[name]), "sha256": digest(blobs[name])}
                             for name, path in paths.items()},
                  "outputs": {name: {"file": outputs[name].name, "bytes": len(data), "sha256": digest(data)}
                              for name, data in packed.items()}}
        packed["audit"] = (json.dumps(report, indent=2) + "\n").encode()
        written = []
        try:
            for name, data in packed.items():
                with outputs[name].open("xb") as stream:
                    written.append(outputs[name])
                    stream.write(data)
                outputs[name].chmod(0o600)
        except Exception:
            for path in written:
                path.unlink()
            raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel", "kernel-version-file", "kernel-manifest", "install-map", "loader", "uboot", "dtb", "ramdisk", "ramdisk-manifest", "cmdline-file", "output-prefix"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-journal-replay", action="store_true",
                        help="explicitly allow a matching replay-then-readonly helper; default accepts read-only helpers only")
    args = parser.parse_args()
    try:
        report = package(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, "spacewar-log-recovery: " + str(error) + "\n")
    print("Log recovery packaging audit PASS: %d/%d boot-argument bytes; no phone commands." %
          (report["boot_args_bytes_worst_case"], BOOTARGS_LIMIT))


if __name__ == "__main__":
    main()
