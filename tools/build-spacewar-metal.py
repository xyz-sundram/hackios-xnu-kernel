#!/usr/bin/env python3
"""Build the pinned native Metal core and CA plug-in; no phone or image writes.

Use a local checkout of github.com/oosexgang/hakios-metal. The Mesa cross build
must already exist. Host tests use its checked-in reference fixtures, without
regenerating them through a second, unavailable host Mesa installation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d7495f43485176785a03a77c4fb82cfff0204043"
ARCHIVES = (
    "src/compiler/air/libair.a", "src/freedreno/ir3/libfreedreno_ir3.a",
    "src/freedreno/isa/libir3encode.a", "src/freedreno/isa/libir3decode.a",
    "src/compiler/isaspec/libisaspec.a", "src/compiler/nir/libnir.a",
    "src/compiler/libcompiler.a", "src/freedreno/fdl/libfreedreno_layout.a",
    "src/freedreno/common/libfreedreno_common.a", "src/util/libmesa_util.a",
    "src/util/libmesa_util_sse41.a", "src/util/blake3/libblake3.a",
    "src/c11/impl/libmesa_util_c11.a",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, cwd, log, timeout=None):
    with log.open("ab") as stream:
        subprocess.run([str(a) for a in args], cwd=cwd, stdout=stream,
                       stderr=subprocess.STDOUT, check=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, default=Path.home() / "theos/sdks/iPhoneOS16.5.sdk")
    parser.add_argument("--gmem-min-draws", type=int, choices=(0, 56), default=56,
                        help="0: sysmem-only compositor control; 56: upstream adaptive GMEM policy")
    parser.add_argument("--deferred-recording", action="store_true",
                        help="enable the core's existing queue worker for command recording")
    parser.add_argument("--alpha-check", action="store_true",
                        help="include the bounded production CA alpha/MRT readback diagnostic")
    parser.add_argument("--no-specialization", action="store_true",
                        help="correctness control: use original uber shaders for every draw")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.absolute()
    if output.exists():
        parser.error("--output must be a new directory")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if revision != REVISION:
        parser.error("source must be the reviewed revision " + REVISION)
    changed = set(subprocess.check_output(["git", "diff", "HEAD", "--name-only"],
                                         cwd=source, text=True).splitlines())
    if changed - {"include/hakios-metal/a6xx.hpp", "tools/probe.cpp"}:
        parser.error("source has unrelated changes; use an isolated checkout")
    output.mkdir(parents=True)
    log = output / "build.log"
    for name in ("clang-constexpr.patch", "probe-stages.patch"):
        patch = ROOT / "tools/metal" / name
        applied = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)],
                                 cwd=source, capture_output=True).returncode == 0
        if not applied:
            subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
            run(["git", "apply", patch], source, log)

    mesa = ROOT / "xnu-deps/build/mesa"
    run([mesa / "venv/bin/ninja", "-C", mesa / "build-softpipe-freedreno",
         "src/compiler/air/libair.a"], ROOT, log)
    frozen = []
    for fixture in sorted((source / "tests/fixtures").glob("*.hpp")):
        frozen += ["-o", str(fixture.relative_to(source))]
    common = ["make", "MESA_DEFS=", "HAKIOS=" + str(ROOT), "SDK=" + str(args.sdk.resolve(strict=True)), *frozen]
    run([*common, "out/host/tests"], source, log)
    run([source / "out/host/tests"], source, output / "host-tests.log")
    run([*common, "ios", "MESA=1"], source, log)
    for name in ("libhakios-metal.a", "hakios-metal-probe"):
        shutil.copy2(source / "out/ios" / name, output / name)
    archives = [mesa / "build-softpipe-freedreno" / a for a in ARCHIVES]
    run([ROOT / "xnu-deps/host/bin/xnu-clang", "-arch", "arm64", "-isysroot", args.sdk,
         "-O1", "-fblocks", "-bundle", "-DHAKIOS_METAL_GPU", "-I" + str(source / "include"),
         "-DHAKIOS_METAL_DEFAULT_GMEM_DRAWS=" + str(args.gmem_min_draws),
         "-DHAKIOS_METAL_DEFAULT_DEFERRED=" + str(int(args.deferred_recording)),
         "-DHAKIOS_METAL_DEFAULT_NOSPEC=" + str(int(args.no_specialization)),
         "-framework", "Foundation", "-framework", "IOKit", "-framework", "IOSurface",
         "-framework", "Metal", "-lobjc", "-lc++", "-o", output / "HakiosMetal",
         ROOT / "userland/src/HakiosMetalCA.m", output / "libhakios-metal.a", *archives, "-lz", "-lm"], ROOT, log)
    run([ROOT / "xnu-deps/host/bin/xnu-clang", "-arch", "arm64", "-isysroot", args.sdk,
         "-O1", "-fblocks", "-dynamiclib", "-install_name", "/redwood/lib/libhakios.sb.dylib",
         "-framework", "CoreFoundation", "-framework", "CoreGraphics", "-framework", "IOKit",
         "-framework", "IOSurface", "-lobjc", "-o", output / "libhakios.sb.dylib",
         ROOT / "userland/src/inject_sb.c"], ROOT, log)
    run([ROOT / "xnu-deps/host/bin/xnu-clang", "-arch", "arm64", "-isysroot", args.sdk,
         "-O1", "-framework", "Foundation", "-framework", "Metal", "-o", output / "metal-ca-pipeline-check",
         ROOT / "userland/src/metal_ca_pipeline_check.m"], ROOT, log)
    files = ("libhakios-metal.a", "hakios-metal-probe", "HakiosMetal", "libhakios.sb.dylib", "metal-ca-pipeline-check")
    if args.alpha_check:
        run([ROOT / "xnu-deps/host/bin/xnu-clang", "-arch", "arm64", "-isysroot", args.sdk,
             "-O1", "-fblocks", "-framework", "Foundation", "-framework", "Metal", "-o",
             output / "metal-ca-alpha-check", ROOT / "userland/src/metal_ca_alpha_check.m"], ROOT, log)
        files += ("metal-ca-alpha-check",)
    if args.deferred_recording:
        check = ROOT / "tools/metal/deferred-recording-check.cpp"
        core = "pm4 gpu command compute texture render link compiler metallib c_api fake_gpu".split()
        run(["clang++", "-std=c++17", "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
             "-pthread", "-I" + str(source / "include"), check,
             *[source / "src" / (n + ".cpp") for n in core], "-o", output / "deferred-check-host"], ROOT, log)
        run(["env", "ASAN_OPTIONS=detect_leaks=0", output / "deferred-check-host"], ROOT,
            output / "deferred-host-tests.log", timeout=45)
        run([ROOT / "xnu-deps/host/bin/xnu-clang", "-arch", "arm64", "-isysroot", args.sdk,
             "-O1", "-std=c++17", "-I" + str(source / "include"), "-o", output / "deferred-recording-check",
             check, output / "libhakios-metal.a", *archives,
             "-framework", "IOKit", "-framework", "CoreFoundation", "-framework", "IOSurface", "-lc++", "-lz", "-lm"], ROOT, log)
        files += ("deferred-recording-check",)
    report = {"result": "PASS", "repository": "https://github.com/oosexgang/hakios-metal",
              "revision": revision, "sdk": str(args.sdk), "backend": "native Adreno; AIR/NIR/ir3",
              "gmem_min_draws": args.gmem_min_draws,
              "deferred_recording": args.deferred_recording,
              "alpha_check": args.alpha_check,
              "shader_specialization": not args.no_specialization,
              "deferred_checks": "host ASan/UBSan PASS; phone diagnostic included" if args.deferred_recording else "not selected",
              "patches": {p: sha(ROOT / "tools/metal" / p) for p in ("clang-constexpr.patch", "probe-stages.patch")},
              "source_files": {p: sha(ROOT / p) for p in ("userland/src/HakiosMetalCA.m", "userland/src/inject_sb.c", "userland/src/metal_ca_pipeline_check.m", "drivers/gpu/hakios.gpu.cpp", "tools/metal/deferred-recording-check.cpp") + (("userland/src/metal_ca_alpha_check.m",) if args.alpha_check else ())},
              "archives": {a: sha(p) for a, p in zip(ARCHIVES, archives)},
              "files": {p: {"bytes": (output / p).stat().st_size, "sha256": sha(output / p)} for p in files},
              "host_tests": "passed; see host-tests.log", "hardware_status": "not tested by this builder"}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Built native Metal core, staged GPU probe and CoreAnimation plug-in:", output)


if __name__ == "__main__":
    main()
