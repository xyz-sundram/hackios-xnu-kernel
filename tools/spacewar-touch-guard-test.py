#!/usr/bin/env python3
"""Exercise the actual touch init gate using generated device headers, without MMIO."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hakios_config", root / "tools/hakios-config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)
source = (root / "drivers/input/hakios.goodix.c").read_text()
init = source[source.index("void\nhakios_goodix_init(void)"):]
guard_start = source.index("#ifndef HAKIOS_DEVICE_SPACEWAR")
header_guard = source[guard_start:source.index("#endif", guard_start) + len("#endif")]
built = config.drivers_built({})
cc = shutil.which("cc")
if cc is None:
    raise SystemExit("spacewar-touch-guard-test: a host C compiler is required")

harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hakios.drivers.h"
typedef int thread_t;
typedef int kern_return_t;
#define KERN_SUCCESS 0
static uint32_t touch_spacewar, touch_steps = 1, flip;
static uint32_t enabled_arg, profile_arg, steps_arg;
static bool have_profile, have_steps;
static unsigned start_calls;
static bool PE_parse_boot_argn(const char *name, void *out, unsigned size) {
    (void)size;
    if (!strcmp(name, "touchscreen")) { *(uint32_t *)out = enabled_arg; return true; }
    if (!strcmp(name, "touch_spacewar") && have_profile) { *(uint32_t *)out = profile_arg; return true; }
    if (!strcmp(name, "touch_steps") && have_steps) { *(uint32_t *)out = steps_arg; return true; }
    return false;
}
static void goodix_thread(void *arg, int result) { (void)arg; (void)result; }
static kern_return_t kernel_thread_start(void (*fn)(void *, int), void *arg, thread_t *thread) {
    (void)fn; (void)arg; *thread = 1; start_calls++; return KERN_SUCCESS;
}
static void thread_deallocate(thread_t thread) { (void)thread; }
'''

cases = r'''
static int run_case(const char *name, unsigned enabled, bool profile_present,
    unsigned profile, bool steps_present, unsigned steps, unsigned expected_calls,
    unsigned expected_spacewar) {
    enabled_arg = enabled; have_profile = profile_present; profile_arg = profile;
    have_steps = steps_present; steps_arg = steps;
    start_calls = 0; touch_spacewar = 0; touch_steps = 1;
    hakios_goodix_init();
    if (start_calls != expected_calls || touch_spacewar != expected_spacewar) {
        fprintf(stderr, "FAIL %s: calls=%u profile=%u\n", name, start_calls, touch_spacewar);
        return 1;
    }
    return 0;
}
int main(void) {
    unsigned failed = 0;
#if HAKIOS_DEVICE_SPACEWAR
    failed += run_case("Spacewar omitted profile defaults safe", 1, false, 0, false, 0, 1, 1);
    failed += run_case("Spacewar profile disable rejected", 1, true, 0, false, 0, 0, 1);
    failed += run_case("Spacewar invalid profile rejected", 1, true, 2, false, 0, 0, 1);
    failed += run_case("Spacewar explicit read-only probe", 1, true, 1, true, 1, 1, 1);
    failed += run_case("Spacewar read-only RSC stage2 allowed", 1, false, 0, true, 2, 1, 1);
    failed += run_case("Spacewar guarded power stage3 allowed", 1, false, 0, true, 3, 1, 1);
    failed += run_case("Spacewar stage0 rejected", 1, false, 0, true, 0, 0, 1);
    failed += run_case("Spacewar gated status stage4 allowed", 1, false, 0, true, 4, 1, 1);
    failed += run_case("Spacewar GPIO identity5 allowed", 1, false, 0, true, 5, 1, 1);
    failed += run_case("Spacewar fulltouch6 allowed", 1, false, 0, true, 6, 1, 1);
    failed += run_case("Spacewar stage7 rejected", 1, false, 0, true, 7, 0, 1);
#else
    failed += run_case("other board existing path retained", 1, false, 0, false, 0, 1, 0);
    failed += run_case("other board explicit legacy path", 1, true, 0, false, 0, 1, 0);
    failed += run_case("other board explicit read-only profile", 1, true, 1, true, 1, 1, 1);
    failed += run_case("read-only RSC profile allowed", 1, true, 1, true, 2, 1, 1);
    failed += run_case("other board power profile rejected", 1, true, 1, true, 3, 0, 1);
    failed += run_case("other board gate profile rejected", 1, true, 1, true, 4, 0, 1);
    failed += run_case("other board identity5 rejected", 1, true, 1, true, 5, 0, 1);
    failed += run_case("other board fulltouch6 rejected", 1, true, 1, true, 6, 0, 1);
#endif
    failed += run_case("touch disabled starts nothing", 0, true, 1, true, 1, 0, 0);
    return failed != 0;
}
'''

with tempfile.TemporaryDirectory(prefix="spacewar-touch-guard-") as temp:
    temp = Path(temp)
    (temp / "probe.c").write_text(harness + header_guard + "\n" + init + cases)
    for device in ("spacewar", "redwood", "qemu", ""):
        value = config.typed("DEVICE", '"' + device + '"') if device else ""
        header = config.drivers_header(built, "guard test", value)
        expected = int(device == "spacewar")
        assert "#define HAKIOS_DEVICE_SPACEWAR  " + str(expected) in header
        (temp / "hakios.drivers.h").write_text(header)
        subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(temp),
                        str(temp / "probe.c"), "-o", str(temp / "probe")], check=True)
        subprocess.run([str(temp / "probe")], check=True)
        print("PASS generated device guard and actual touch init:", device or "no device")
    assert "#define HAKIOS_DEVICE_SPACEWAR  0" in config.drivers_header(built, "no device")
    (temp / "hakios.drivers.h").write_text("/* stale header without device wiring guards */\n")
    stale = subprocess.run([cc, "-std=c11", "-fsyntax-only", "-I", str(temp),
                            str(temp / "probe.c")], text=True, capture_output=True)
    assert stale.returncode != 0 and "Regenerate hakios.drivers.h" in stale.stderr
    print("PASS stale generated header refuses to compile")

print("spacewar-touch-guard-test: all passed; no hardware access")
