#!/usr/bin/env python3
"""Prepare isolated native-Metal RAM tests from an audited brightness baseline.

No phone I/O, flashing, slot changes or system-image rebuilds. The services
profile retains measured battery reporting; the probe/debug profiles leave the
DSP idle to retain live USB diagnostics. All preserve the brightness bridge.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "kernel-dir", "native-build", "loader", "metallib", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    baseline, kd, native = (x.resolve(strict=True) for x in (a.baseline, a.kernel_dir, a.native_build))
    loader, library = a.loader.resolve(strict=True), a.metallib.resolve(strict=True)
    output = a.output.absolute()
    if output.exists():
        p.error("--output must be a new directory")
    spec = importlib.util.spec_from_file_location("debug_package", ROOT / "tools/spacewar-debug-package.py")
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    kernel = (kd / "kernel").read_bytes()
    metadata = json.loads((kd / "manifest.json").read_text())
    build = json.loads((native / "manifest.json").read_text())
    version = (kd / "kernel.ver").read_text().strip()
    assert metadata["result"] == "PASS" and metadata["driver_count"] == 19
    assert digest(kd / "kernel") == metadata["files"]["kernel"]["sha256"]
    assert digest(baseline / "root/lib/libhakios.brightness.dylib") == metadata["native_registration"]["userland_bridge_sha256"]
    assert build["result"] == "PASS"
    for name in ("HakiosMetal", "hakios-metal-probe", "libhakios.sb.dylib", "metal-ca-pipeline-check"):
        assert digest(native / name) == build["files"][name]["sha256"]
    if build.get("deferred_recording"):
        assert digest(native / "deferred-recording-check") == build["files"]["deferred-recording-check"]["sha256"]
    if build.get("alpha_check"):
        assert digest(native / "metal-ca-alpha-check") == build["files"]["metal-ca-alpha-check"]["sha256"]
    assert library.read_bytes()[:4] == b"MTLB", "supply the converted iOS16.5 compositor library"
    assert b"ca_metallib\0" in loader.read_bytes() and b"backlight\0" in loader.read_bytes()
    install_map = ROOT / "build/spacewar-dualboot/install-plan-contiguous/install-persist.map"
    d.common.validate_map(install_map.read_bytes())
    base_arguments = dict(word.split("=", 1) for word in (baseline / "cmdline").read_text().split() if word != "-v")
    prefix = ("debug=0x8 rd=md0 hv_wdt_pet=1 ufs_shared_pa=0x0000000000000000 "
              "touch_shared_pa=0x0000000000000000 hakios_kver=" + version + " ")
    output.mkdir(parents=True)
    reports = {}
    for profile in ("probe", "render-probe", "metal-debug", "metal-services"):
        folder = output / profile
        root = folder / "root"
        shutil.copytree(baseline / "root", root, symlinks=True)
        for bad in ("bin/app_install_bootstrap", "bin/ipa_fetch_install", "bin/TrollStore.tar"):
            assert not (root / bad).exists(), "baseline contains abandoned installer files"
        args = dict(base_arguments, epoch=str(int(time.time())))
        rc = (root / "etc/rc").read_text()
        rc = "\n".join(line for line in rc.splitlines() if "/brightness_probe " not in line) + "\n"
        if profile in ("probe", "render-probe"):
            shutil.copy2(native / "hakios-metal-probe", root / "bin/hakios-metal-probe")
            stage = 6 if profile == "probe" else 9
            rc = rc.replace("exec /sbin/launchd.darwin",
                            f"/redwood/bin/hakios-metal-probe --max-stage {stage}\nexec /sbin/launchd.darwin")
        else:
            shutil.copy2(native / "HakiosMetal", root / "lib/HakiosMetal.bundle/HakiosMetal")
            # The brightness baseline predates the Metal-only legibility
            # gradient adaptation in the current SpringBoard injection source.
            shutil.copy2(native / "libhakios.sb.dylib", root / "lib/libhakios.sb.dylib")
            # The plug-in also opens this canonical path directly when it
            # mirrors QuartzCore's pipeline library and builds copy shaders.
            # Keep the boot argument short with a relative alias to that file.
            shaders = root / "share/ca-share"
            shaders.mkdir(parents=True, exist_ok=True)
            shutil.copy2(library, shaders / "QuartzCore-default.metallib")
            (root / "ca.metallib").symlink_to("share/ca-share/QuartzCore-default.metallib")
            assert digest((root / "ca.metallib").resolve(strict=True)) == digest(library)
            args.update(metal="1", metal_fps="1", ca_metallib="/redwood/ca.metallib")
            if profile == "metal-debug":
                shutil.copy2(native / "metal-ca-pipeline-check", root / "bin/metal-ca-pipeline-check")
                rc = rc.replace("exec /sbin/launchd.darwin",
                                "/redwood/bin/metal-ca-pipeline-check\nexec /sbin/launchd.darwin")
                if build.get("alpha_check"):
                    shutil.copy2(native / "metal-ca-alpha-check", root / "bin/metal-ca-alpha-check")
                    rc = rc.replace("exec /sbin/launchd.darwin",
                                    "/redwood/bin/metal-ca-alpha-check --frames 16 --batch 1\n"
                                    "/redwood/bin/metal-ca-alpha-check --frames 16 --batch 8\n"
                                    "/redwood/bin/metal-ca-alpha-check --frames 8 --batch 8 --fresh\n"
                                    "exec /sbin/launchd.darwin")
                if build.get("deferred_recording"):
                    shutil.copy2(native / "deferred-recording-check", root / "bin/deferred-recording-check")
                    rc = rc.replace("exec /sbin/launchd.darwin",
                                    "/redwood/bin/deferred-recording-check\nexec /sbin/launchd.darwin")
            # These defaults are already selected by the resident loader and
            # wifi=1; omit redundant diagnostic words to fit the 607-byte ABI.
            for key in ("loader_return_after", "wifi_pause", "wifi_loud"):
                args.pop(key, None)
        if profile == "metal-services":
            args.update(adsp="1", battmgr="1")
            rc = rc.replace("exec /sbin/launchd.darwin",
                            "/redwood/bin/battery_test 90 2 &\nexec /sbin/launchd.darwin")
        else:
            args.update(adsp="0", battmgr="0")
        (root / "etc/rc").write_text(rc)
        # Verbose text draws into the same framebuffer as CoreAnimation.
        # UI builds keep the USB log and debug code without that console.
        line = ("-v " if profile in ("probe", "render-probe") else "") + " ".join(f"{k}={v}" for k, v in args.items())
        budget = len((prefix + line).encode("ascii"))
        assert budget <= 607, (profile, budget)
        checks = d.audio.validate_live_profile(line, loader.read_bytes(), metadata, "tone")
        (folder / "cmdline").write_text(line + "\n")
        rd = folder / "ramdisk.apfs"
        d.command("python3", ROOT / "tools/apfs/mkapfs.py", "-z", "--plain", "fw", "-o", rd, "redwood=" + str(root))
        d.command(ROOT / "tools/apfs/check-image.sh", rd, "redwood=" + str(root))
        payload = folder / (profile + "-debug.bin")
        d.command("python3", ROOT / "xnuloader/mkpayload.py", "--usb", "--cmdline", line,
                  "--kernel-version", version, "-o", payload, "kernel=" + str(kd / "kernel"),
                  "ramdisk=" + str(rd), "install.map=" + str(install_map))
        d.common.audit_payload(payload.read_bytes(), {"kernel": kernel, "ramdisk": rd.read_bytes(),
            "install.map": install_map.read_bytes(), "cmdline": line.encode() + b"\0",
            "kernel.ver": version.encode() + b"\0", "usb": b"1\0"})
        report = {"format": "spacewar-debug-payload-v1", "result": "PASS", "kind": profile,
                  "file": payload.name, "sha256": digest(payload), "bytes": payload.stat().st_size,
                  "mount_policy": "normal installed HFS data", "cmdline": line,
                  "boot_args_bytes_worst_case": budget, "kernel_sha256": digest(kd / "kernel"),
                  "ramdisk_sha256": digest(rd), "brightness_bridge_sha256": digest(root / "lib/libhakios.brightness.dylib"),
                  "metal_revision": build["revision"], "battery_enabled": profile == "metal-services",
                  "gmem_min_draws": build.get("gmem_min_draws", 56),
                  "deferred_recording": build.get("deferred_recording", False),
                  "alpha_check": build.get("alpha_check", False) and profile == "metal-debug",
                  "shader_specialization": build.get("shader_specialization", True),
                  "diagnostic_only": profile != "metal-services", "hardware_result": "not yet tested", **checks}
        payload.with_suffix(".audit.json").write_text(json.dumps(report, indent=2) + "\n")
        reports[profile] = report
    idle = output / "loader-idle.bin"
    d.command("python3", ROOT / "xnuloader/mkpayload.py", "--usb", "--kernel-version", version, "-o", idle)
    image = output / "loader-debug.img"
    d.command("python3", ROOT / "tools/mkubootimg.py", "-d", "spacewar", "-u",
              ROOT / "build/spacewar-uboot-installed/u-boot.bin", "-l", loader, "--dtb", ROOT / "spacewar.dtb",
              "-p", idle, "-o", image, "-c", output / "loader-debug.hkub")
    container = (output / "loader-debug.hkub").read_bytes()
    d.common.audit_hkub(container, {"loader": loader.read_bytes(), "payload": idle.read_bytes()})
    d.common.audit_image(image.read_bytes(), (ROOT / "build/spacewar-uboot-installed/u-boot.bin").read_bytes(),
                         (ROOT / "spacewar.dtb").read_bytes(), container)
    for name in ("spacewar-debug.py", "log-recovery.py"):
        shutil.copy2(ROOT / "tools" / name, output / name)
    bundle = {"format": "spacewar-debug-bundle-v1", "result": "PASS", "boot_provider": "frozen working U-Boot",
              "bootstrap": {"file": image.name, "sha256": digest(image)}, "loader_sha256": digest(loader),
              "profiles": reports, "normal_boot_images_flashed": False}
    (output / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n")
    print("PASS native Metal RAM bundle; battery enabled in metal-services:", output)


if __name__ == "__main__":
    main()
