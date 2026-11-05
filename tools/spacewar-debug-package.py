#!/usr/bin/env python3
"""Build an isolated debug loader bootstrap and XNUP RAM payloads. No phone I/O.

Source xnu-env.sh first. Supply the audited installed-system inputs and a clean
normal RAM root; this never rebuilds or replaces the installed kernel/images.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


audio = module("audio_debug", ROOT / "tools/spacewar-audio-debug.py")
common = audio.common


def command(*args, env=None):
    subprocess.run([str(a) for a in args], cwd=ROOT, env=env, check=True)


def payload(output, paths, line, metadata, kind, recovery_manifest=None):
    blobs = {key: path.read_bytes() for key, path in paths.items()}
    version = blobs["kernel_version_file"].decode("ascii").strip()
    common.require(metadata["files"]["kernel"]["sha256"] == common.digest(blobs["kernel"]),
                   "kernel differs from its build manifest")
    common.require(metadata.get("result") == "PASS" and metadata.get("driver_count") == 19 and
                   metadata.get("kernel_version", metadata.get("version")) == version,
                   "expected a successful matching 19-driver build")
    common.validate_map(blobs["install_map"])
    if kind == "recovery":
        _, budget = common.validate_cmdline(line.removeprefix("-v "), version)
        common.validate_ramdisk_manifest(recovery_manifest, blobs["ramdisk"], allow_journal_replay=True)
    else:
        _, budget = audio.validate_cmdline(line.removeprefix("-v "), version, "continuous")
        common.require(metadata.get("spacewar_continuous_playback_flag") == 1 and
                       metadata.get("spacewar_playback_diagnostic_max_seconds") == 3,
                       "normal debug profile requires the matching continuous-playback build")
    common.require(line.startswith("-v ") and budget + 3 <= common.BOOTARGS_LIMIT,
                   "verbose debug command line exceeds the loader budget")
    audio.validate_live_profile(line, blobs["loader"], metadata, "continuous")
    command(sys.executable, ROOT / "xnuloader/mkpayload.py", "--usb", "--cmdline", line,
            "--kernel-version", version, "-o", output,
            "kernel=" + str(paths["kernel"]), "ramdisk=" + str(paths["ramdisk"]),
            "install.map=" + str(paths["install_map"]))
    blob = output.read_bytes()
    common.audit_payload(blob, {"kernel": blobs["kernel"], "ramdisk": blobs["ramdisk"],
        "install.map": blobs["install_map"], "cmdline": line.encode() + b"\0",
        "kernel.ver": version.encode() + b"\0", "usb": b"1\0"})
    report = {"format": "spacewar-debug-payload-v1", "result": "PASS", "kind": kind,
        "file": output.name, "bytes": len(blob), "sha256": common.digest(blob),
        "kernel_version": version, "cmdline": line, "boot_args_bytes_worst_case": budget + 3,
        "usb_mode": 1, "loader_start_trace": True, "raw_partition_log_writes": False,
        "kernel_sha256": common.digest(blobs["kernel"]),
        "ramdisk_sha256": common.digest(blobs["ramdisk"]),
        "loader_sha256": common.digest(blobs["loader"]),
        "mount_policy": recovery_manifest.get("mount_policy") if recovery_manifest else "normal installed HFS data",
        "phone_commands": 0}
    output.with_suffix(".audit.json").write_text(json.dumps(report, indent=2) + "\n")
    output.chmod(0o600)
    return report


def build(args):
    out = args.output.absolute()
    common.require(not out.exists() and not out.is_symlink(), "output must be a new directory")
    kernel_dir = args.kernel_dir.resolve(strict=True)
    baseline = args.baseline_root.resolve(strict=True)
    for name in ("bin/app_install_bootstrap", "bin/ipa_fetch_install", "bin/TrollStore.tar",
                 "bin/trollstorehelper", "etc/instagram-transfer.conf"):
        common.require(not (baseline / name).exists(), "baseline contains an abandoned app installer")
    rc = (baseline / "etc/rc").read_text()
    common.require("exec /sbin/launchd.darwin" in rc and "app_install" not in rc and "ipa_fetch" not in rc,
                   "baseline rc is not a clean normal launchd root")
    out.mkdir(parents=True, mode=0o700)
    env = {**os.environ, "XNULOADER_OUT": str(out / "loader"),
           "HAKIOS_LOADER_START_TRACE": "1", "HAKIOS_PERSIST_LOG": "0"}
    command("bash", ROOT / "xnuloader/build.sh", env=env)
    sdk = args.sdk.resolve(strict=True)
    probe = out / "brightness_probe"
    command("xnu-clang", "-arch", "arm64", "-isysroot", sdk, "-O1", "-Wall", "-Wextra", "-Werror",
            "-framework", "CoreFoundation", "-framework", "IOKit", "-o", probe,
            ROOT / "userland/src/brightness_probe.c")
    paths = {"kernel": kernel_dir / "kernel", "kernel_version_file": kernel_dir / "kernel.ver",
        "install_map": args.install_map.resolve(strict=True), "loader": out / "loader/loader.efi"}
    metadata = json.loads((kernel_dir / "manifest.json").read_text())
    version = paths["kernel_version_file"].read_text().strip()
    epoch = int(time.time())
    normal_line = "-v " + " ".join(f"{k}={v}" for k, v in audio.audio_arguments("continuous").items()) + f" epoch={epoch}"
    reports = []
    for kind in ("baseline", "brightness"):
        tree = out / (kind + "-root")
        shutil.copytree(baseline, tree, symlinks=True)
        if kind == "brightness":
            shutil.copy2(probe, tree / "bin/brightness_probe")
            (tree / "etc/rc").write_text(rc.replace("exec /sbin/launchd.darwin",
                "/redwood/bin/brightness_probe 300 &\nexec /sbin/launchd.darwin"))
        ramdisk = out / (kind + ".apfs")
        command(sys.executable, ROOT / "tools/apfs/mkapfs.py", "-z", "--plain", "fw", "-o", ramdisk,
                "redwood=" + str(tree))
        command("bash", ROOT / "tools/apfs/check-image.sh", ramdisk, "redwood=" + str(tree))
        reports.append(payload(out / (kind + "-debug.bin"), {**paths, "ramdisk": ramdisk}, normal_line, metadata, kind))
    for replay in (False, True):
        name = "recovery-replay" if replay else "recovery-readonly"
        folder = out / name
        env = {**os.environ, "LOG_RECOVERY_REPLAY": str(int(replay)), "LOG_RECOVERY_NAME": args.recovery_name}
        command("bash", ROOT / "tools/build-log-recovery.sh", folder, args.gpu_fw.resolve(strict=True), env=env)
        recovery_manifest = json.loads((folder / "ramdisk.manifest.json").read_text())
        line = "-v " + (folder / "cmdline").read_text().strip()
        reports.append(payload(out / (name + ".bin"), {**paths, "ramdisk": folder / "ramdisk.apfs"},
                               line, metadata, "recovery", recovery_manifest))
    # An idle USB loader is enough: the installed system is preloaded later,
    # when an explicitly staged payload supplies its private install map.
    bootstrap = out / "loader-idle.bin"
    command(sys.executable, ROOT / "xnuloader/mkpayload.py", "--usb", "--kernel-version", version,
            "-o", bootstrap)
    common.audit_payload(bootstrap.read_bytes(), {"usb": b"1\0", "kernel.ver": version.encode() + b"\0"})
    image, container = out / "loader-debug.img", out / "loader-debug.hkub"
    command(sys.executable, ROOT / "tools/mkubootimg.py", "-d", "spacewar", "-u", args.uboot.resolve(strict=True),
            "-l", paths["loader"], "--dtb", args.dtb.resolve(strict=True), "-p", bootstrap,
            "-o", image, "-c", container)
    common.audit_hkub(container.read_bytes(), {"loader": paths["loader"].read_bytes(), "payload": bootstrap.read_bytes()})
    common.audit_image(image.read_bytes(), args.uboot.read_bytes(), args.dtb.read_bytes(), container.read_bytes())
    manifest = {"format": "spacewar-debug-bundle-v1", "result": "PASS", "phone_commands": 0,
        "installed_images_changed": False, "debug": {"verbose": True, "hakios_debug": 1,
        "loader_start_trace": True, "raw_partition_log_writes": False},
        "bootstrap": {"file": image.name, "bytes": image.stat().st_size,
                      "sha256": common.digest(image.read_bytes())}, "payloads": reports}
    (out / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name in ("spacewar-debug.py", "log-recovery.py"):
        shutil.copy2(ROOT / "tools" / name, out / name)
    shutil.copy2(ROOT / "docs/spacewar-debug.md", out / "README.md")
    (out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
        for p in sorted(out.iterdir()) if p.is_file() and p.suffix in (".bin", ".img", ".py")))
    print(f"Debug bundle PASS: idle loader + 4 staged payloads in {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("kernel-dir", "baseline-root", "install-map", "uboot", "dtb", "gpu-fw", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--sdk", type=Path, default=Path(os.environ.get("THEOS", str(Path.home() / "theos"))) / "sdks/iPhoneOS16.5.sdk")
    p.add_argument("--recovery-name", default="", help="optional exact saved audio/debug basename")
    args = p.parse_args()
    try:
        build(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        p.exit(1, f"spacewar-debug-package: {error}\n")


if __name__ == "__main__":
    main()
