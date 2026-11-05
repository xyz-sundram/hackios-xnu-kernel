#!/usr/bin/env python3
"""Build the pinned native Metal core and CA plug-in; no phone or image writes.

Defaults to this kernel repository's bundled hakios-metal, reviewed at the
pinned upstream revision. --source also accepts the earlier pinned standalone
checkout. The Mesa cross build must already exist. Host tests use reference fixtures, without
regenerating them through a second, unavailable host Mesa installation.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d7495f43485176785a03a77c4fb82cfff0204043"
BUNDLED_REVISION = "2631f5d7d4f9d7322aca60991b34ba465dec343b"
BUNDLED_TREE = "9c7428002f4d07a0e60dc5ae64c8553770bc8f71"
PATCH_FILES = {"clang-constexpr.patch": "include/hakios-metal/a6xx.hpp",
               "probe-stages.patch": "tools/probe.cpp"}
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


def git(cwd, *args, binary=False):
    return subprocess.check_output(["git", *args], cwd=cwd, text=not binary)


def inspect_source(source):
    """Require the complete reviewed source tree plus exactly our scoped patches.

    Compare bytes against Git objects, rather than trusting a dirty filename
    allowlist: an unrelated change in an allowed header is still refused.
    Build outputs ignored by the source repository are not source inputs.
    This function does not modify the source or its index.
    """
    top = Path(git(source, "rev-parse", "--show-toplevel").strip()).resolve()
    prefix = source.relative_to(top).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    revision = git(source, "rev-parse", "HEAD").strip()
    bundled = source == (ROOT / "hakios-metal").resolve() and top == ROOT.resolve()
    patch_files = dict(PATCH_FILES)
    if bundled:
        patch_files["host-chip-check.patch"] = "src/c_api.cpp"
        tree = git(top, "rev-parse", BUNDLED_REVISION + ":hakios-metal").strip()
        if tree != BUNDLED_TREE:
            raise ValueError("bundled upstream tree does not match the reviewed pin")
        subprocess.run(["git", "merge-base", "--is-ancestor", BUNDLED_REVISION, "HEAD"],
                       cwd=top, check=True)
        repository = "https://github.com/oosexgang/hakios-xnu-kernel"
        reviewed_revision = BUNDLED_REVISION
    else:
        if source != top or revision != REVISION:
            raise ValueError("--source must be the in-tree hakios-metal or standalone revision " + REVISION)
        tree = git(top, "rev-parse", REVISION + "^{tree}").strip()
        repository = "https://github.com/oosexgang/hakios-metal"
        reviewed_revision = REVISION
    baseline = {}
    archive = git(top, "archive", "--format=tar", tree, binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("unexpected non-file in reviewed source: " + member.name)
            baseline[member.name] = stream.extractfile(member).read()
    expected = dict(baseline)
    # Apply patches only to a temporary reference tree to derive exact accepted
    # bytes. A changed patch cannot silently authorize a second source path.
    with tempfile.TemporaryDirectory(prefix="spacewar-metal-reference-") as folder:
        reference = Path(folder)
        for name, relative in patch_files.items():
            patch = ROOT / "tools/metal" / name
            stat = git(ROOT, "apply", "--numstat", str(patch)).strip().splitlines()
            if len(stat) != 1 or stat[0].split("\t")[-1] != relative:
                raise ValueError("patch is not scoped to its reviewed source path: " + name)
            target = reference / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(baseline[relative])
            subprocess.run(["git", "apply", "--no-index", str(patch)], cwd=reference,
                           capture_output=True, check=True)
            expected[relative] = target.read_bytes()
    # Include staged additions and nonignored untracked source (e.g. another
    # *_test.cpp picked up by Make's wildcard); exclude unrelated kernel files.
    listed = git(top, "ls-files", "--cached", "--others", "--exclude-standard", "-z",
                 "--", prefix or ".").split("\0")
    names = {name[len(prefix):] for name in listed if name}
    if names != set(baseline):
        raise ValueError("source paths differ from reviewed tree: " +
                         ", ".join(sorted(names.symmetric_difference(baseline))))
    pending = []
    hashes = {}
    for relative, original in baseline.items():
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing or redirected source file: " + relative)
        actual = path.read_bytes()
        accepted = (original, expected[relative]) if relative in patch_files.values() else (original,)
        if actual not in accepted:
            raise ValueError("source has unreviewed changes: " + relative)
        if actual != expected[relative]:
            pending.append(next(name for name, file in patch_files.items() if file == relative))
        hashes[relative] = hashlib.sha256(expected[relative]).hexdigest()
    digest = hashlib.sha256()
    for relative in sorted(hashes):
        digest.update(relative.encode() + b"\0" + bytes.fromhex(hashes[relative]))
    provenance = {"mode": "bundled" if bundled else "standalone", "path": str(source),
                  "repository": repository, "revision": revision,
                  "reviewed_revision": reviewed_revision, "reviewed_tree": tree,
                  "kernel_repository_revision": git(ROOT, "rev-parse", "HEAD").strip(),
                  "kernel_worktree_dirty": bool(git(ROOT, "status", "--porcelain", "--untracked-files=no").strip()),
                  "compiled_source_sha256": digest.hexdigest(), "compiled_source_files": hashes,
                  "scoped_patch_files": patch_files,
                  "hashes_describe": "reviewed tree with the listed scoped patches applied"}
    if bundled:
        provenance["bundled_upstream_revision"] = BUNDLED_REVISION
        provenance["bundled_upstream_tree"] = BUNDLED_TREE
    return provenance, pending, top, prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "hakios-metal",
                        help="default: bundled hakios-metal; accepts the pinned legacy standalone checkout")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="validate source/patches and print core build commands without changing files")
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
    try:
        provenance, pending, top, prefix = inspect_source(source)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    mesa = ROOT / "xnu-deps/build/mesa"
    frozen = []
    for fixture in sorted((source / "tests/fixtures").glob("*.hpp")):
        frozen += ["-o", str(fixture.relative_to(source))]
    common = ["make", "MESA_DEFS=", "HAKIOS=" + str(ROOT), "SDK=" + str(args.sdk.resolve(strict=True)), *frozen]
    core_commands = [
        [mesa / "venv/bin/ninja", "-C", mesa / "build-softpipe-freedreno", "src/compiler/air/libair.a"],
        [*common, "out/host/tests"], [source / "out/host/tests"],
        # Upstream's `ios` target includes the probe only when a host Mesa
        # ir3_cmdline.c exists. This workflow always needs the real-GPU probe.
        [*common, "ios", "out/ios/hakios-metal-probe", "MESA=1"],
    ]
    archives = [mesa / "build-softpipe-freedreno" / a for a in ARCHIVES]
    plugin_command = [ROOT / "xnu-deps/host/bin/xnu-clang", "-arch", "arm64", "-isysroot", args.sdk,
         "-O1", "-fblocks", "-bundle", "-DHAKIOS_METAL_GPU", "-I" + str(source / "include"),
         "-DHAKIOS_METAL_DEFAULT_GMEM_DRAWS=" + str(args.gmem_min_draws),
         "-DHAKIOS_METAL_DEFAULT_DEFERRED=" + str(int(args.deferred_recording)),
         "-DHAKIOS_METAL_DEFAULT_NOSPEC=" + str(int(args.no_specialization)),
         "-framework", "Foundation", "-framework", "IOKit", "-framework", "IOSurface",
         "-framework", "Metal", "-lobjc", "-lc++", "-o", output / "HakiosMetal",
         ROOT / "userland/src/HakiosMetalCA.m", output / "libhakios-metal.a", *archives, "-lz", "-lm"]
    if args.dry_run:
        print(json.dumps({"result": "SOURCE VALIDATED; NOT BUILT", "source_provenance": provenance,
                          "patches_to_apply": pending, "output": str(output),
                          "commands": [[str(a) for a in command] for command in [*core_commands, plugin_command]],
                          "gmem_min_draws": args.gmem_min_draws, "deferred_recording": args.deferred_recording,
                          "shader_specialization": not args.no_specialization, "alpha_check": args.alpha_check}, indent=2))
        return
    output.mkdir(parents=True)
    log = output / "build.log"
    for name in pending:
        command = ["git", "apply"] + (["--directory=" + prefix.rstrip("/")] if prefix else [])
        run([*command, ROOT / "tools/metal" / name], top, log)
    provenance, still_pending, _, _ = inspect_source(source)
    if still_pending:
        raise ValueError("source patches were not fully applied")
    for i, command in enumerate(core_commands):
        run(command, ROOT if i == 0 else source,
            output / "host-tests.log" if i == 2 else log)
    for name in ("libhakios-metal.a", "hakios-metal-probe"):
        shutil.copy2(source / "out/ios" / name, output / name)
    run(plugin_command, ROOT, log)
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
    report = {"result": "PASS", "repository": provenance["repository"],
              "revision": provenance["revision"], "source_provenance": provenance,
              "sdk": str(args.sdk), "backend": "native Adreno; AIR/NIR/ir3",
              "gmem_min_draws": args.gmem_min_draws,
              "deferred_recording": args.deferred_recording,
              "alpha_check": args.alpha_check,
              "shader_specialization": not args.no_specialization,
              "deferred_checks": "host ASan/UBSan PASS; phone diagnostic included" if args.deferred_recording else "not selected",
              "patches": {p: sha(ROOT / "tools/metal" / p) for p in provenance["scoped_patch_files"]},
              "source_files": {p: sha(ROOT / p) for p in ("tools/build-spacewar-metal.py", "userland/src/HakiosMetalCA.m", "userland/src/inject_sb.c", "userland/src/metal_ca_pipeline_check.m", "drivers/gpu/hakios.gpu.cpp", "tools/metal/deferred-recording-check.cpp") + (("userland/src/metal_ca_alpha_check.m",) if args.alpha_check else ())},
              "archives": {a: sha(p) for a, p in zip(ARCHIVES, archives)},
              "files": {p: {"bytes": (output / p).stat().st_size, "sha256": sha(output / p)} for p in files},
              "host_tests": "passed; see host-tests.log", "hardware_status": "not tested by this builder"}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Built native Metal core, staged GPU probe and CoreAnimation plug-in:", output)


if __name__ == "__main__":
    main()
