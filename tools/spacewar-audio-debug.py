#!/usr/bin/env python3
"""Package a gated Phone (1) audio-debug RAM image from explicit local files.

This builds files only: no fastboot, flashing, partitioning or slot changes.
Use a matching normal diagnostic userland RAM disk, not the recovery PID1 root.
The default profile starts a bounded sound=3 tone. The optional playback profile
requires an audited kernel with a one-shot, three-second sound=4 amplifier lease.
The continuous profile explicitly enables sound_cont=1 in a supporting kernel.
All retain Wi-Fi and battery monitoring and leave haptics disabled.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

SPEC = importlib.util.spec_from_file_location("recovery_package", Path(__file__).with_name("spacewar-log-recovery.py"))
common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)

AUDIO_ARGUMENTS = {
    **common.SAFE_ARGUMENTS, "sb_delay": "8", "adsp": "1", "battmgr": "1", "wifi": "1",
    "wifi_pause": "0", "wifi_new": "17", "wifi_watch": "0", "adsp_listen": "10",
    "sound": "3", "wifi_loud": "0",
}


def audio_arguments(mode):
    common.require(mode in ("tone", "playback", "continuous"), "unknown audio diagnostic mode")
    arguments = {**AUDIO_ARGUMENTS, "sound": "3" if mode == "tone" else "4"}
    if mode == "continuous":
        # wifi=1 resets its watch to zero and returns before parsing wifi_watch.
        # Omit that redundant word to retain the loader's boot-argument budget.
        del arguments["wifi_watch"]
        arguments["sound_cont"] = "1"
    return arguments


def validate_cmdline(text, version, mode="tone"):
    arguments = audio_arguments(mode)
    common.require(re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", version), "invalid kernel version")
    parsed = {}
    for word in text.split():
        common.require(word.count("=") == 1, "audio arguments must be name=value words")
        key, value = word.split("=", 1)
        common.require(key not in parsed and key in {*arguments, "epoch"}, "duplicate or unexpected audio argument")
        parsed[key] = value
    common.require(all(parsed.get(k) == v for k, v in arguments.items()), "audio arguments do not match the selected diagnostic profile")
    common.require(re.fullmatch(r"[0-9]{1,20}", parsed.get("epoch", "")) and int(parsed["epoch"]) < 1 << 64,
                   "missing or invalid epoch")
    line = " ".join(text.split())
    prefix = ("debug=0x8 rd=md0 hv_wdt_pet=1 ufs_shared_pa=0x0000000000000000 "
              "touch_shared_pa=0x0000000000000000 hakios_kver=" + version + " ")
    budget = len((prefix + line).encode("ascii"))
    common.require(budget <= common.BOOTARGS_LIMIT, "audio boot arguments exceed loader budget")
    return line, budget


def validate_live_profile(line, loader, metadata, mode):
    """Check the production loader policy before packaging a new runtime flag."""
    source = (common.ROOT / "xnuloader/loader.c").read_text()
    policy_path = common.ROOT / "drivers/input/hakios.spacewar.services.h"
    policy = policy_path.read_bytes()
    if mode == "continuous":
        common.require(b"sound_cont\0" in loader,
                       "supplied loader lacks the sound_cont live-profile token; rebuild the matching loader")
        common.require(metadata.get("files", {}).get("services-policy.built.h", {}).get("sha256") == common.digest(policy),
                       "continuous kernel and current shared service policy do not match")
    functions = source[source.index("static int\ntouch_token_is("):source.index("static int\nrun_kernel(")]
    harness = ('#include <stddef.h>\n#include <stdint.h>\n#include <string.h>\n'
               '#include "hakios.spacewar.services.h"\n'
               '#define str_len strlen\n#define str_eq(a,b) (!strcmp((a),(b)))\n' + functions +
               '\nint main(int argc,char **argv) { return argc == 2 && '
               'touch_live_profile(argv[1],0,0x700,1) == 1 ? 0 : 1; }\n')
    with tempfile.TemporaryDirectory(prefix="spacewar-audio-profile-") as temporary:
        folder = Path(temporary)
        cfile, executable = folder / "profile.c", folder / "profile"
        cfile.write_text(harness)
        subprocess.run([os.environ.get("HOSTCC", "clang"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(policy_path.parent), str(cfile), "-o", str(executable)], check=True)
        result = subprocess.run([str(executable), line], check=False)
        common.require(result.returncode == 0, "audio arguments fail the production loader live-profile policy")
    return {"loader_live_profile_host_check": "PASS", "services_policy_sha256": common.digest(policy)}


def package(args):
    arguments = audio_arguments(args.mode)
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
    if args.mode in ("playback", "continuous"):
        common.require(type(metadata.get("spacewar_playback_diagnostic_max_seconds")) is int and
                       metadata["spacewar_playback_diagnostic_max_seconds"] == 3,
                       "playback diagnostic requires an audited kernel with a one-shot three-second lease")
    if args.mode == "continuous":
        common.require(type(metadata.get("spacewar_continuous_playback_flag")) is int and
                       metadata["spacewar_continuous_playback_flag"] == 1,
                       "continuous playback requires an audited kernel with the sound_cont opt-in flag")
    common.require(blobs["kernel"][:4] == b"\xcf\xfa\xed\xfe", "kernel is not Mach-O arm64")
    common.require(blobs["loader"][:2] == b"MZ", "loader is not a UEFI application")
    common.require(blobs["uboot"][0x38:0x3c] == b"ARM\x64", "U-Boot lacks its arm64 Linux image header")
    common.require(blobs["dtb"][:4] == b"\xd0\x0d\xfe\xed", "DTB is not an FDT")
    common.require(blobs["ramdisk"][32:36] == b"NXSB", "RAM disk is not an APFS container")
    common.validate_map(blobs["install_map"])
    text = blobs["cmdline_file"].decode("ascii") if args.cmdline_file else (
        " ".join(f"{k}={v}" for k, v in arguments.items()) + f" epoch={int(time.time())}")
    line, budget = validate_cmdline(text, version, args.mode)
    policy_checks = validate_live_profile(line, blobs["loader"], metadata, args.mode)
    return common.pack_inputs(args, paths, blobs, version, line, budget,
        {"purpose": "RAM-only gated audio diagnostic", "audio_mode": args.mode,
         "playback_diagnostic_max_seconds": 3 if args.mode == "playback" else None,
         "continuous_playback_requested": args.mode == "continuous", "audio_result": "unverified",
         "normal_userland_ramdisk_supplied": True, **policy_checks})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("tone", "playback", "continuous"), default="tone")
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
