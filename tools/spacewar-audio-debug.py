#!/usr/bin/env python3
"""Package a gated Phone (1) audio-debug RAM image from explicit local files.

This builds files only: no fastboot, flashing, partitioning or slot changes.
Use a matching normal diagnostic userland RAM disk, not the recovery PID1 root.
The current profile starts the ADSP and bounded sound=3 diagnostic tone, retains
Wi-Fi and battery monitoring, and leaves haptics disabled.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

SPEC = importlib.util.spec_from_file_location("recovery_package", Path(__file__).with_name("spacewar-log-recovery.py"))
common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)

AUDIO_ARGUMENTS = {
    **common.SAFE_ARGUMENTS, "sb_delay": "8", "adsp": "1", "battmgr": "1", "wifi": "1",
    "wifi_pause": "0", "wifi_new": "17", "wifi_watch": "0", "adsp_listen": "10",
    "sound": "3", "wifi_loud": "0",
}


def validate_cmdline(text, version):
    common.require(re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", version), "invalid kernel version")
    parsed = {}
    for word in text.split():
        common.require(word.count("=") == 1, "audio arguments must be name=value words")
        key, value = word.split("=", 1)
        common.require(key not in parsed and key in {*AUDIO_ARGUMENTS, "epoch"}, "duplicate or unexpected audio argument")
        parsed[key] = value
    common.require(all(parsed.get(k) == v for k, v in AUDIO_ARGUMENTS.items()), "audio arguments do not match the gated sound=3 profile")
    common.require(re.fullmatch(r"[0-9]{1,20}", parsed.get("epoch", "")) and int(parsed["epoch"]) < 1 << 64,
                   "missing or invalid epoch")
    line = " ".join(text.split())
    prefix = ("debug=0x8 rd=md0 hv_wdt_pet=1 ufs_shared_pa=0x0000000000000000 "
              "touch_shared_pa=0x0000000000000000 hakios_kver=" + version + " ")
    budget = len((prefix + line).encode("ascii"))
    common.require(budget <= common.BOOTARGS_LIMIT, "audio boot arguments exceed loader budget")
    return line, budget


def package(args):
    paths = {name: getattr(args, name).resolve(strict=True) for name in
             ("kernel", "kernel_version_file", "kernel_manifest", "install_map", "loader", "uboot", "dtb", "ramdisk")}
    if args.cmdline_file:
        paths["cmdline_file"] = args.cmdline_file.resolve(strict=True)
    blobs = {name: path.read_bytes() for name, path in paths.items()}
    version = blobs["kernel_version_file"].decode("ascii").strip()
    metadata = json.loads(blobs["kernel_manifest"])
    common.require(isinstance(metadata, dict) and isinstance(metadata.get("files"), dict) and
                   isinstance(metadata["files"].get("kernel"), dict), "invalid kernel manifest")
    common.require(isinstance(metadata, dict) and metadata.get("result") == "PASS" and
                   metadata.get("driver_count") == 19 and
                   metadata.get("kernel_version", metadata.get("version")) == version and
                   metadata.get("files", {}).get("kernel", {}).get("sha256") == common.digest(blobs["kernel"]),
                   "kernel does not match its audited 19-driver manifest/version")
    common.require(blobs["kernel"][:4] == b"\xcf\xfa\xed\xfe", "kernel is not Mach-O arm64")
    common.require(blobs["loader"][:2] == b"MZ", "loader is not a UEFI application")
    common.require(blobs["uboot"][0x38:0x3c] == b"ARM\x64", "U-Boot lacks its arm64 Linux image header")
    common.require(blobs["dtb"][:4] == b"\xd0\x0d\xfe\xed", "DTB is not an FDT")
    common.require(blobs["ramdisk"][32:36] == b"NXSB", "RAM disk is not an APFS container")
    common.validate_map(blobs["install_map"])
    text = blobs["cmdline_file"].decode("ascii") if args.cmdline_file else (
        " ".join(f"{k}={v}" for k, v in AUDIO_ARGUMENTS.items()) + f" epoch={int(time.time())}")
    line, budget = validate_cmdline(text, version)
    return common.pack_inputs(args, paths, blobs, version, line, budget,
        {"purpose": "RAM-only gated sound=3 audio diagnostic", "audio_result": "unverified",
         "normal_userland_ramdisk_supplied": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel", "kernel-version-file", "kernel-manifest", "install-map", "loader", "uboot", "dtb", "ramdisk", "output-prefix"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cmdline-file", type=Path, help="optional exact gated profile; default uses the current host epoch")
    args = parser.parse_args()
    try:
        report = package(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, "spacewar-audio-debug: " + str(error) + "\n")
    print("Audio-debug packaging audit PASS: %d/%d boot-argument bytes; no phone commands." %
          (report["boot_args_bytes_worst_case"], common.BOOTARGS_LIMIT))


if __name__ == "__main__":
    main()
