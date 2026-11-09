#!/usr/bin/env python3
"""Execute imported graphics defaults without a phone, kernel build or SDK."""
import os
from pathlib import Path
import re
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
    branch = re.search(r"\tif \((\w+) != 0 && kernel_thread_start\(beamProbeMain", source[start:])
    assert branch, "beam-probe startup branch not found"
    end = start + branch.start()
    setup = source[start:end]
    # Return the actual variable controlling thread startup, so retaining a
    # stale `if (gBeam)` cannot pass merely because the policy computed `beam`.
    effective_beam = branch.group(1)
    defaults = source[source.index('#define kFBDefaultWidth'):source.index('/* Boot-arg fbtrace=')]
    start = source.index('\tfWidth = gFBWidth ?', source.index('IOMobileFramebuffer::start('))
    display = source[start:source.index('\tfColumn = IONewZero', start)]
    harness = r"""
#include <cassert>
#include <cstdint>
#include <cstring>
static uint32_t bootBeam;
static bool fSourceSnapshots;
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
""" + policy + "\nstatic uint32_t setup(const uint32_t gBeam) {\n" + setup + \
        "\nreturn " + effective_beam + ";\n" + r"""
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
    fSourceSnapshots=true;
    display();
    assert(fWidth==1125 && fHeight==2436 && properties==0);
    assert(fWidth/3==375 && fHeight/3==812);
    properties=0;
    gFBScale=3;
    display();
    assert(fWidth==1125 && fHeight==2436 && properties==0);
    fSourceSnapshots=false;
    gFBWidth=828;gFBHeight=1792;gFBScale=2;gFBSubtype=1792;
    display();
    assert(fWidth==828 && fHeight==1792 && properties==3);
    properties=0;fSourceSnapshots=true;gFBWidth=750;gFBHeight=1624;
    display();
    assert(fWidth==750 && fHeight==1624 && properties==3);
    assert(fWidth/2==375 && fHeight/2==812);
    for (int board = 0; board <= 2; board++)
    for (int present = 0; present <= 1; present++)
    for (unsigned requested = 0; requested <= 2; requested++) {
        spacewarPresent = board != 0;
        bootSpacewar = board;
        beamPresent = present;
        bootBeam = requested;
        const unsigned tunable = present ? requested : 1; // actual TUNABLE default
        unsigned expected = board == 1 && !present ? 0 : tunable;
        assert(setup(tunable) == expected);
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
        # A TUNABLE becomes read-only after kernel startup. Restore the old
        # write at the extracted call site and prove the harness rejects it;
        # a mutable fake global previously allowed this real panic to pass.
        mutation, count = re.subn(r"(?m)^(\s*)beam = (spacewarBeamPolicy\([^\n]+)",
                                 r"\1gBeam = \2", harness)
        assert count == 1, "effective beam policy must assign a local beam"
        bad_cpp = temp / "beam-readonly-regression.cpp"
        bad_cpp.write_text(mutation)
        result = subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                 str(bad_cpp), "-o", str(temp / "bad-beam")], capture_output=True, text=True)
        assert result.returncode != 0 and "gBeam" in result.stderr, "write to read-only TUNABLE compiled"
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
    print("Spacewar graphics: native 1125x2436 default, explicit 3x/XR overrides, 18 read-only beam-policy cases, rejected TUNABLE write and 7 executable runner cases passed")


if __name__ == "__main__":
    main()
