#!/usr/bin/env python3
"""Prepare isolated native-Metal RAM tests from an audited brightness baseline.

No phone I/O, flashing, slot changes or system-image rebuilds. Both UI profiles
retain measured battery reporting. Standalone GPU probes leave the DSP idle.
All preserve the brightness bridge.
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


def battery_arguments(profile):
    """Measured reporting is a normal UI feature, including graphics debug builds."""
    if profile in ("probe", "render-probe"):
        return {"adsp": "0", "battmgr": "0"}
    return {"adsp": "1", "battmgr": "1"}


def graphics_variants(arguments):
    """One presentation variable per pair, with the same kernel and APFS root."""
    assert not any(key in arguments for key in
                   ("fb_surface_use", "fb_filter", "fb_ca_hz", "fb_threads", "fb_beat")), \
        "matrix baseline already overrides a presentation control"
    controls = {"baseline": {}, "ownership": {"fb_surface_use": "1"},
                "filtered": {"fb_filter": "1"}, "ca120": {"fb_ca_hz": "120"},
                "copy6": {"fb_threads": "6"}, "unpaced": {"fb_beat": "0"}}
    variants = {}
    for control, changes in controls.items():
        for renderer in ("metal", "gles"):
            args = dict(arguments, touch_lat="1", **changes)
            if renderer == "gles":
                for key in ("metal", "metal_fps", "ca_metallib"):
                    args.pop(key, None)
            variants[f"{renderer}-{control}"] = args
    return variants


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "kernel-dir", "native-build", "loader", "metallib", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--graphics-matrix", action="store_true",
                   help="also audit matched Metal/GLES presentation controls with per-frame USB timing")
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
    if metadata.get("requires_scale_cache_refresh") or metadata.get("spacewar_default_render_scale", 3) == 2:
        assert build.get("spacewar_display_scale_check"), "reduced canvas requires its matching scale-cache helper"
    if build.get("spacewar_display_scale_check"):
        assert digest(native / "display_info") == build["files"]["display_info"]["sha256"]
    if build.get("deferred_recording"):
        assert digest(native / "deferred-recording-check") == build["files"]["deferred-recording-check"]["sha256"]
    if build.get("alpha_check"):
        assert digest(native / "metal-ca-alpha-check") == build["files"]["metal-ca-alpha-check"]["sha256"]
    assert library.read_bytes()[:4] == b"MTLB", "supply the converted iOS16.5 compositor library"
    assert all(token.encode() + b"\0" in loader.read_bytes() for token in
               ("ca_metallib", "backlight", "fb_beat")), \
        "loader must support the measured unpaced Spacewar presentation profile"
    if metadata.get("spacewar_usb_input_control"):
        assert b"usb-power: configured=" in loader.read_bytes(), \
            "charger control requires the loader's configured USB power handoff"
    if a.graphics_matrix:
        assert all(token.encode() + b"\0" in loader.read_bytes() for token in
                   ("fb_surface_use", "fb_filter", "fb_ca_hz", "fb_threads", "fb_beat")), \
            "rebuild the matching loader for presentation controls"
        assert b"fb_surface_use\0" in kernel, "rebuild the kernel with display-use ownership support"
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
        if build.get("spacewar_display_scale_check"):
            shutil.copy2(native / "display_info", root / "bin/display_info")
            # Retire a stale saved scale before any UI process initializes it.
            # Its helper rebuilds the answer under launchd; verify in parallel.
            rc = "/redwood/bin/display_info --refresh-scale-cache\n" + rc
            rc = rc.replace("exec /sbin/launchd.darwin",
                            "/redwood/bin/display_info --verify-scale &\nexec /sbin/launchd.darwin")
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
            # The phone comparison removed a median 7.1 ms wait after the GPU
            # and the user confirmed smoother motion. Keep the old beat in
            # the explicit matrix baseline, not the normal Metal profiles.
            args.update(metal="1", metal_fps="1", ca_metallib="/redwood/ca.metallib", fb_beat="0")
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
        args.update(battery_arguments(profile))
        if profile == "metal-services":
            rc = rc.replace("exec /sbin/launchd.darwin",
                            "/redwood/bin/battery_test 90 2 &\nexec /sbin/launchd.darwin")
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
                  "loader_sha256": digest(loader),
                  "ramdisk_sha256": digest(rd), "brightness_bridge_sha256": digest(root / "lib/libhakios.brightness.dylib"),
                  "metal_revision": build["revision"], "battery_enabled": args["adsp"] == "1" and args["battmgr"] != "0",
                  "gmem_min_draws": build.get("gmem_min_draws", 56),
                  "deferred_recording": build.get("deferred_recording", False),
                  "alpha_check": build.get("alpha_check", False) and profile == "metal-debug",
                  "shader_specialization": build.get("shader_specialization", True),
                  "sysmem_feedback_order": build.get("sysmem_feedback_order", False),
                  "compiler_cache_hits": build.get("compiler_cache_hits", False),
                  "padded_constants": build.get("padded_constants", False),
                  "spacewar_snapshot_sources": metadata.get("spacewar_snapshot_sources", False),
                  "spacewar_default_render_scale": metadata.get("spacewar_default_render_scale", 3),
                  "requires_scale_cache_refresh": metadata.get("requires_scale_cache_refresh", False),
                  "spacewar_display_scale_check": build.get("spacewar_display_scale_check", False),
                  "spacewar_static_wallpaper": build.get("spacewar_static_wallpaper", False),
                  "presentation_control": "unpaced" if profile.startswith("metal-") else "baseline",
                  "diagnostic_only": profile != "metal-services", "hardware_result": "not yet tested", **checks}
        payload.with_suffix(".audit.json").write_text(json.dumps(report, indent=2) + "\n")
        reports[profile] = report
        if a.graphics_matrix and profile == "metal-debug":
            matrix_args = dict(args)
            matrix_args.pop("fb_beat")
            for name, variant in graphics_variants(matrix_args).items():
                target = output / name
                target.mkdir()
                variant_line = " ".join(f"{k}={v}" for k, v in variant.items())
                variant_budget = len((prefix + variant_line).encode("ascii"))
                assert variant_budget <= 607, (name, variant_budget)
                variant_checks = d.audio.validate_live_profile(variant_line, loader.read_bytes(), metadata, "tone")
                (target / "cmdline").write_text(variant_line + "\n")
                candidate = target / (name + "-debug.bin")
                d.command("python3", ROOT / "xnuloader/mkpayload.py", "--usb", "--cmdline", variant_line,
                          "--kernel-version", version, "-o", candidate, "kernel=" + str(kd / "kernel"),
                          "ramdisk=" + str(rd), "install.map=" + str(install_map))
                d.common.audit_payload(candidate.read_bytes(), {"kernel": kernel, "ramdisk": rd.read_bytes(),
                    "install.map": install_map.read_bytes(), "cmdline": variant_line.encode() + b"\0",
                    "kernel.ver": version.encode() + b"\0", "usb": b"1\0"})
                variant_report = dict(report, kind=name, file=candidate.name, sha256=digest(candidate),
                    bytes=candidate.stat().st_size, cmdline=variant_line,
                    boot_args_bytes_worst_case=variant_budget, loader_sha256=digest(loader),
                    renderer=name.split("-", 1)[0], presentation_control=name.split("-", 1)[1],
                    timing_trace=True, shared_root="metal-debug/ramdisk.apfs", **variant_checks)
                candidate.with_suffix(".audit.json").write_text(json.dumps(variant_report, indent=2) + "\n")
                reports[name] = variant_report
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
    print("PASS native Metal RAM bundle; measured battery enabled in both UI profiles:", output)


if __name__ == "__main__":
    main()
