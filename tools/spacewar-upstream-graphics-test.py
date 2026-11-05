#!/usr/bin/env python3
"""Execute imported graphics defaults without a phone, kernel build or SDK."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    source = (ROOT / "xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp").read_text()
    policy = function(source, "static inline uint32_t\nspacewarBeamPolicy(")
    # Execute the actual start-up call site, with boot-argument reads substituted.
    start = source.index("\tint spacewar = 0;", source.index("IOMobileFramebuffer::start("))
    end = source.index("\tif (gBeam != 0 && kernel_thread_start", start)
    setup = source[start:end]
    defaults = source[source.index('#define kFBDefaultWidth'):source.index('/* Boot-arg fbtrace=')]
    start = source.index('\tfWidth = gFBWidth ?', source.index('IOMobileFramebuffer::start('))
    display = source[start:source.index('\tfColumn = IONewZero', start)]
    harness = r"""
#include <cassert>
#include <cstdint>
#include <cstring>
static uint32_t gBeam, bootBeam;
static int bootSpacewar;
static bool beamPresent, spacewarPresent;
static bool PE_parse_boot_argn(const char *name, void *out, unsigned size) {
    if (!strcmp(name, "fb_beam") && beamPresent) {
        assert(size == sizeof(bootBeam)); memcpy(out, &bootBeam, size); return true;
    }
    if (!strcmp(name, "touch_spacewar") && spacewarPresent) {
        assert(size == sizeof(bootSpacewar)); memcpy(out, &bootSpacewar, size); return true;
    }
    return false;
}
""" + policy + "\nstatic void setup() {\n" + setup + r"""
}
#define TUNABLE(type, variable, name, value) type variable = value
static unsigned properties;
static const char *gIODTPlane = "device-tree";
struct IORegistryEntry {
    static IORegistryEntry *fromPath(const char *, const char *) { static IORegistryEntry entry; return &entry; }
    void setProperty(const char *, const void *, unsigned) { properties++; }
    void release() {}
};
static uint32_t fWidth, fHeight, fPanelWidth=1080, fPanelHeight=2400;
""" + defaults + '\nstatic void display() {\n' + display + r"""
}
int main() {
    display();
    assert(fWidth==1125 && fHeight==2436 && properties==0);
    gFBWidth=828;gFBHeight=1792;gFBScale=2;gFBSubtype=1792;
    display();
    assert(fWidth==828 && fHeight==1792 && properties==3);
    for (int board = 0; board <= 2; board++)
    for (int present = 0; present <= 1; present++)
    for (unsigned requested = 0; requested <= 2; requested++) {
        spacewarPresent = board != 0;
        bootSpacewar = board;
        beamPresent = present;
        bootBeam = requested;
        gBeam = present ? requested : 1; // actual TUNABLE default
        unsigned expected = board == 1 && !present ? 0 : gBeam;
        setup();
        assert(gBeam == expected);
    }
}
"""
    with tempfile.TemporaryDirectory(prefix="spacewar-graphics-") as temp:
        temp = Path(temp)
        cpp, exe = temp / "beam.cpp", temp / "beam"
        cpp.write_text(harness)
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        (temp / "tools").mkdir()
        script = temp / "tools/metal-run.sh"
        shutil.copyfile(ROOT / "tools/metal-run.sh", script)
        cases = [({"DEVICE": "spacewar"}, None),
                 ({}, 'CONFIG_DEVICE="spacewar"\n'),
                 ({}, "CONFIG_DEVICE=spacewar\n"),
                 ({"MORE": "gpu=1 touch_spacewar=1"}, None),
                 ({"XL_CMDLINE": "touch_spacewar=1 cpus=7"}, None),
                 ({"XL_DTB": "/tmp/spacewar.dtb"}, None)]
        clean = dict(os.environ)
        for name in ("DEVICE", "MORE", "XL_CMDLINE", "XL_DTB"):
            clean.pop(name, None)
        for env, config in cases:
            config_path = temp / ".config"
            config_path.unlink(missing_ok=True)
            if config:
                config_path.write_text(config)
            result = subprocess.run(["bash", str(script)], env=clean | env,
                                    capture_output=True, text=True)
            assert result.returncode == 2, result
            assert "unsupported on Nothing Phone (1)" in result.stderr, result
        (temp / ".config").write_text('CONFIG_DEVICE="redwood"\n')
        result = subprocess.run(["bash", str(script)], env=clean | {"DEVICE": "redwood"},
                                capture_output=True, text=True)
        # Generic builds still reach their ordinary missing-image prerequisite.
        assert result.returncode == 1 and "userland/root.apfs is missing" in result.stdout, result
    print("Spacewar graphics: actual 1125x2436 default/XR opt-in, 18 beam-policy cases and 7 executable runner cases passed")


if __name__ == "__main__":
    main()
