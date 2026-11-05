#!/usr/bin/env python3
"""Execute production display surface holds and audit the comparison matrix."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    end = source.index("\n}", start) + 2
    return source[start:end]


def main():
    surface = (ROOT / "xnu/files/iokit/Kernel/MiniIOSurface.cpp").read_text()
    framebuffer = (ROOT / "xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp").read_text()
    functions = "\n".join(function(surface, signature) for signature in (
        "static MiniIOSurface *\nsurfaceCopy(", "MiniIOSurface *\nMiniIOSurfaceCopy(",
        "MiniIOSurface *\nMiniIOSurfaceCopyForDisplay(", "void\nMiniIOSurfaceDropForDisplay(",
        "void\nMiniIOSurfaceDrop("))
    functions += "\n" + function(framebuffer, "static inline MiniIOSurface *\ncopySwapSurface(")
    functions += "\n" + function(framebuffer, "static inline void\ndropSwapSurface(")
    # Every queue/presenter/settle reference must use the matched API pair.
    presenter = framebuffer[framebuffer.index("IOMobileFramebuffer::present("):
                            framebuffer.index("IOMobileFramebuffer::settle(")]
    assert "MiniIOSurfaceCopy(" not in presenter and "MiniIOSurfaceDrop(" not in presenter
    harness = r"""
#include <cassert>
#include <cstdint>
#include <cstddef>
struct Shared { uint64_t useCount = 0; };
struct MiniIOSurface {
    unsigned holders = 1, useCount[2] = {0, 0};
    Shared state;
    Shared *shared() { return &state; }
};
static bool locked;
static int lock;
static int *gSurfaceLock = &lock;
static const unsigned MINISURF_TABLE_SIZE = 4;
static MiniIOSurface *gSurfaceTable[MINISURF_TABLE_SIZE];
static unsigned gSurfaceUse;
static void IOLockLock(int *) { assert(!locked); locked = true; }
static void IOLockUnlock(int *) { assert(locked); locked = false; }
static void surfaceHold(MiniIOSurface *s) { assert(locked); s->holders++; }
static void surfaceUnhold(MiniIOSurface *s) { assert(locked && s->holders); s->holders--; }
""" + functions + r"""
int main() {
    MiniIOSurface surface;
    gSurfaceTable[1] = &surface;
    for (unsigned mode = 0; mode <= 1; mode++) {
        gSurfaceUse = mode;
        surface.useCount[0] = 2; surface.useCount[1] = 3;
        surface.state.useCount = (3ULL << 32) | 2;
        assert(copySwapSurface(0) == NULL);
        assert(copySwapSurface(2) == NULL);
        assert(copySwapSurface(MINISURF_TABLE_SIZE) == NULL);
        gSurfaceLock = NULL;
        assert(copySwapSurface(1) == NULL);
        gSurfaceLock = &lock;
        dropSwapSurface(NULL);
        // Two queued swaps, with the same surface in multiple layers.
        for (unsigned n = 1; n <= 8; n++) {
            assert(copySwapSurface(1) == &surface);
            assert(surface.holders == 1+n);
            assert(surface.state.useCount == ((3ULL << 32) | (2+n*mode)));
        }
        // A normal GPU/object hold must not publish display use.
        auto *gpu = MiniIOSurfaceCopy(1);
        assert(surface.state.useCount == ((3ULL << 32) | (2+8*mode)));
        MiniIOSurfaceDrop(gpu);
        // Simulate the client's independently accounted counts going away.
        surface.useCount[0] -= 2; surface.useCount[1] -= 3;
        surface.state.useCount = (uint64_t)surface.useCount[1] << 32 | surface.useCount[0];
        for (unsigned n = 8; n != 0; n--) {
            assert(surface.state.useCount == n*mode);
            dropSwapSurface(&surface);
            assert(surface.holders == n);
            assert(surface.state.useCount == (n-1)*mode);
        }
        assert(surface.holders == 1 && !locked);
        assert(surface.useCount[0] == 0 && surface.useCount[1] == 0);
    }
}
"""
    with tempfile.TemporaryDirectory(prefix="spacewar-display-use-") as tmp:
        source, exe = Path(tmp) / "ownership.cpp", Path(tmp) / "ownership"
        source.write_text(harness)
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", str(source), "-o", str(exe)], check=True)
        # This runner uses ptrace; LeakSanitizer cannot enumerate its threads.
        subprocess.run([str(exe)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    spec = importlib.util.spec_from_file_location("metal_package", ROOT / "tools/spacewar-metal-package.py")
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    base = dict(metal="1", metal_fps="1", ca_metallib="/redwood/ca.metallib", touch_lat="0",
                bootcpu="7", cpus="7", cpufreq="2", var_hfs="ufs_darwin_data", adsp="0", battmgr="0")
    variants = package.graphics_variants(base)
    assert len(variants) == 12 and base["touch_lat"] == "0"
    for name, args in variants.items():
        renderer, control = name.split("-", 1)
        assert args["touch_lat"] == "1"
        changes = {k: v for k, v in args.items() if k not in base}
        assert len(changes) == (control != "baseline")
        for key in ("bootcpu", "cpus", "cpufreq", "var_hfs", "adsp", "battmgr"):
            assert args[key] == base[key]
        for key in ("metal", "metal_fps", "ca_metallib"):
            assert (key in args) == (renderer == "metal")
        if renderer == "metal":
            gles = variants["gles-" + control]
            assert {k: v for k, v in args.items() if k not in ("metal", "metal_fps", "ca_metallib")} == gles
    # A Metal trace has no GLES draw markers. Its missing draw duration must
    # stay unknown; a timer wait must not be reported as CPU copy or GPU work.
    with tempfile.TemporaryDirectory(prefix="spacewar-frame-report-") as tmp:
        log = Path(tmp) / "frames.log"
        lines = ["tl: p 1 0 100 200 at 1000000"]
        for i in range(3):
            stamp = 1000000 + i * 33400
            lines += [f"tl: s {i+1} at {stamp}",
                      f"tl: v {i+1} ready {stamp+1000} shown {stamp+3000} done {stamp+5000}"]
        log.write_text("\n".join(lines) + "\n")
        result = subprocess.run([sys.executable, str(ROOT / "tools/frames.py"), str(log)],
                                capture_output=True, text=True, check=True)
        assert "draw/idle unknown" in result.stdout, result.stdout
        assert "queued/GPU  1.0, pacing  2.0, copy 2.0 ms" in result.stdout, result.stdout
    print("PASS: production display holds, queued duplicate surfaces, client counts, legacy mode, 12 matched controls and Metal timing attribution")


if __name__ == "__main__":
    main()
