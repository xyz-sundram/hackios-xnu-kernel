#!/usr/bin/env python3
"""Host-test the actual bounded EPSS policy with fake registers; no hardware access."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "drivers/power/hakios.spacewar.cpufreq.inc"
BARRIER = '__asm__ volatile("dsb sy" ::: "memory");'

PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uintptr_t vm_offset_t;
#define EPSS_PHYS 0x18591000ul
#define EPSS_DOMAIN_SIZE 0x1000ul
#define EPSS_PERF_STATE 0x320
#define EPSS_FREQ_TABLE 0x100
#define EPSS_TABLE_ROWS 40
#define XO_KHZ 19200
#define ALTERNATE_KHZ 300000
#define NSEC_PER_MSEC 1000000

static const char legacy_drivers[] =
    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal virtio_blk";
static const char installed_drivers[] =
    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal ufs virtio_blk";
static unsigned board, driver_count, maps, sleeps, barriers, fail_map, fail_readback;
static unsigned last_domain;
static const char *driver_list;
#define HAKIOS_DEVICE_SPACEWAR board
#define HAKIOS_DRIVERS_COUNT driver_count
#define HAKIOS_DRIVERS_LIST driver_list

struct argument { const char *name, *value; };
static struct argument args[24];
static unsigned arg_count;
static uint32_t regs[2][1024], original[2][1024];

static void setarg(const char *name, const char *value)
{
    for (unsigned i = 0; i < arg_count; i++) {
        if (!strcmp(args[i].name, name)) {
            args[i].value = value;
            return;
        }
    }
    assert(arg_count < sizeof(args) / sizeof(args[0]));
    args[arg_count++] = (struct argument){name, value};
}

static bool PE_parse_boot_argn(const char *name, void *out, unsigned size)
{
    for (unsigned i = 0; i < arg_count; i++) {
        if (strcmp(args[i].name, name) || !args[i].value)
            continue;
        if (size == sizeof(uint32_t)) {
            char *end;
            unsigned long value = strtoul(args[i].value, &end, 10);
            assert(!*end && value <= UINT32_MAX);
            *(uint32_t *)out = value;
        } else {
            size_t length = strlen(args[i].value) + 1;
            memcpy(out, args[i].value, length < size ? length : size);
        }
        return true;
    }
    return false;
}

static vm_offset_t ml_io_map(vm_offset_t pa, unsigned size)
{
    /* Any CPU0/little-domain, DDR or other peripheral mapping fails the test. */
    assert(size == EPSS_DOMAIN_SIZE);
    assert(pa == EPSS_PHYS + EPSS_DOMAIN_SIZE || pa == EPSS_PHYS + 2 * EPSS_DOMAIN_SIZE);
    maps++;
    if (fail_map == maps)
        return 0;
    last_domain = (pa - EPSS_PHYS) / EPSS_DOMAIN_SIZE - 1;
    return (vm_offset_t)regs[last_domain];
}

static void delay_for_interval(unsigned n, unsigned unit)
{
    assert(n == 20 && unit == NSEC_PER_MSEC);
    sleeps++;
    if (fail_readback == sleeps)
        regs[last_domain][EPSS_PERF_STATE / 4] = 0;
}

static void tell(const char *fmt, ...) { (void)fmt; }
'''

TESTS = r'''
static void setup(bool installed)
{
    board = 1;
    driver_count = installed ? 11 : 10;
    driver_list = installed ? installed_drivers : legacy_drivers;
    maps = sleeps = barriers = fail_map = fail_readback = last_domain = 0;
    arg_count = 0;
    memset(args, 0, sizeof(args));
    memset(regs, 0, sizeof(regs));
    memset(spacewar_cpu_perf, 0, sizeof(spacewar_cpu_perf));
    setarg("cpufreq", "2"); setarg("bootcpu", "7"); setarg("cpus", "7");
    setarg("buttons", "1"); setarg("gpu", "1"); setarg("touch_live", "1");
    setarg("adreno_speed", "1");
    if (installed) {
        setarg("spacewar_install", "1"); setarg("spacewar_storage", "1");
        setarg("ufs", "2"); setarg("ufs_write", "1");
        setarg("var_hfs", "ufs_darwin_data");
    }
    for (unsigned domain = 0; domain < 2; domain++)
        for (unsigned row = 0; row < EPSS_TABLE_ROWS; row++)
            regs[domain][EPSS_FREQ_TABLE / 4 + row] =
                (1u << 30) | (row < 9 ? 20 + 10 * row : 100);
    memcpy(original, regs, sizeof(regs));
}

static void restored(void)
{
    unsigned before = sleeps;
    hakios_cpufreq_quiesce();
    assert(sleeps == before && !memcmp(original, regs, sizeof(regs)));
    for (unsigned domain = 0; domain < 2; domain++)
        assert(!spacewar_cpu_perf[domain].changed);
}

static void refused(void)
{
    assert(!spacewar_cpu_perf_start());
    assert(!maps && !sleeps && !barriers);
    assert(!memcmp(original, regs, sizeof(regs)));
    restored();
}

static void started(void)
{
    assert(spacewar_cpu_perf_start() && maps == 2 && sleeps == 2 && barriers == 2);
    for (unsigned domain = 0; domain < 2; domain++) {
        assert(regs[domain][EPSS_PERF_STATE / 4] == 8);
        original[domain][EPSS_PERF_STATE / 4] = 8;
    }
    /* Only the requested states changed, and row8 is firmware's 1920 MHz entry. */
    assert(!memcmp(original, regs, sizeof(regs)));
    for (unsigned domain = 0; domain < 2; domain++)
        original[domain][EPSS_PERF_STATE / 4] = 0;
    restored();
}

int main(void)
{
    unsigned cases = 0;
    const char *required[] = {"cpufreq", "bootcpu", "cpus", "buttons", "gpu", "touch_live", "adreno_speed"};
    const char *speeds[] = {"1", "2", "3"};
    const char *bad_speeds[] = {"0", "4", "4294967295"};
    for (unsigned installed = 0; installed < 2; installed++) {
        for (unsigned i = 0; i < 3; i++) {
            setup(installed); setarg("adreno_speed", speeds[i]); started(); cases++;
        }
        for (unsigned i = 0; i < sizeof(required) / sizeof(required[0]); i++) {
            setup(installed); setarg(required[i], NULL); refused(); cases++;
            setup(installed); setarg(required[i], "0"); refused(); cases++;
        }
        for (unsigned i = 0; i < 3; i++) {
            setup(installed); setarg("adreno_speed", bad_speeds[i]); refused(); cases++;
        }
        setup(installed); board = 0; refused(); cases++;
        setup(installed); driver_count--; refused(); cases++;
        setup(installed); driver_count++; refused(); cases++;
        setup(installed); driver_list = installed ? legacy_drivers : installed_drivers; refused(); cases++;
        setup(installed); driver_list = "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal ufs virtio_blk extra"; refused(); cases++;
        setup(installed); setarg("cpumask", "0"); refused(); cases++;

        setup(installed); fail_map = 1;
        assert(!spacewar_cpu_perf_start() && maps == 1 && !sleeps); restored(); cases++;
        setup(installed); fail_map = 2;
        assert(!spacewar_cpu_perf_start() && maps == 2 && sleeps == 1); restored(); cases++;
        for (unsigned nth = 1; nth <= 2; nth++) {
            setup(installed); fail_readback = nth;
            assert(!spacewar_cpu_perf_start() && sleeps == nth); restored(); cases++;
        }
        setup(installed); regs[1][EPSS_PERF_STATE / 4] = 39;
        memcpy(original, regs, sizeof(regs));
        assert(!spacewar_cpu_perf_start()); restored(); cases++;
        setup(installed);
        for (unsigned row = 0; row < EPSS_TABLE_ROWS; row++)
            regs[0][EPSS_FREQ_TABLE / 4 + row] = (1u << 30) | 20;
        memcpy(original, regs, sizeof(regs));
        assert(!spacewar_cpu_perf_start()); restored(); cases++;
        setup(installed);
        for (unsigned row = 0; row < EPSS_TABLE_ROWS; row++)
            regs[0][EPSS_FREQ_TABLE / 4 + row] = (1u << 30) | 200;
        memcpy(original, regs, sizeof(regs));
        assert(!spacewar_cpu_perf_start()); restored(); cases++;
        setup(installed); assert(spacewar_cpu_perf_start());
        regs[0][EPSS_PERF_STATE / 4] = 3;
        hakios_cpufreq_quiesce();
        assert(regs[0][EPSS_PERF_STATE / 4] == 3 && regs[1][EPSS_PERF_STATE / 4] == 0);
        assert(!spacewar_cpu_perf[0].changed && !spacewar_cpu_perf[1].changed); cases++;
        setup(installed);
        for (unsigned domain = 0; domain < 2; domain++)
            regs[domain][EPSS_PERF_STATE / 4] = 8;
        memcpy(original, regs, sizeof(regs));
        assert(spacewar_cpu_perf_start() && maps == 2 && !sleeps && !barriers);
        restored(); cases++;
    }


    /* Service drivers keep the same EPSS/installed-storage bounds. */
    for(unsigned test=0;test<13;test++) {
        setup(true);driver_count=SW_SERVICES_DRIVERS_COUNT;driver_list=SW_SERVICES_DRIVERS_LIST;
        setarg("spacewar_services","1");
        if(test==0) {setarg("adsp_step","1");setarg("wifi_step","1");setarg("haptic","1");setarg("pmic","1");setarg("speaker_probe","2");}
        if(test==1) {setarg("adsp","1");setarg("wifi","1");setarg("haptic","5");}
        if(test==2)setarg("spacewar_services",NULL);
        if(test==3)setarg("spacewar_services","0");
        if(test==4)setarg("spacewar_install",NULL);
        if(test==5)setarg("ufs_write","0");
        if(test==6)setarg("var_hfs","ufs_userdata");
        if(test==7)setarg("battery","1");
        if(test==8)setarg("haptic","6");
        if(test==9)setarg("speaker_probe","3");
        if(test==10)driver_count--;
        if(test==11)driver_list=installed_drivers;
        if(test==12)setarg("wifi_step","17");
        if(test<2)started();else refused();cases++;
    }
    for(unsigned i=0;i<SW_SERVICES_BOUND_COUNT;i++)for(unsigned boundary=0;boundary<3;boundary++) {
        if(sw_services_bounds[i].profile>1)continue;
        char value[16];setup(true);driver_count=SW_SERVICES_DRIVERS_COUNT;driver_list=SW_SERVICES_DRIVERS_LIST;
        setarg("spacewar_services","1");
        snprintf(value,sizeof(value),"%u",boundary==0?sw_services_bounds[i].minimum:
            boundary==1?sw_services_bounds[i].maximum:sw_services_bounds[i].maximum+1);
        setarg(sw_services_bounds[i].name,value);
        if(boundary<2)started();else refused();cases++;
    }

    /* Playback is a separate exact nineteen-driver mode, never an override
     * admitting the Awinic sound path into the eighteen-driver probe. */
    for(unsigned test=0;test<15;test++) {
        setup(true);driver_count=SW_SERVICES_AUDIO_DRIVERS_COUNT;driver_list=SW_SERVICES_AUDIO_DRIVERS_LIST;
        setarg("spacewar_services","2");
        const char *sound[]={"0","1","2","3","4"};
        if(test<=4)setarg("sound",sound[test]);
        if(test==5)setarg("spacewar_services","1");
        if(test==6)setarg("spacewar_services","3");
        if(test==7)setarg("spacewar_install",NULL);
        if(test==8)setarg("battery","1");
        if(test==9)setarg("sound","5");
        if(test==10)setarg("var_hfs","ufs_userdata");
        if(test==11)driver_count--;
        if(test==12)driver_list=SW_SERVICES_DRIVERS_LIST;
        if(test==13)setarg("mic","1");
        if(test==14)setarg("ufs_native","0");
        if(test<=4)started();else refused();cases++;
    }
    for(unsigned i=0;i<SW_SERVICES_BOUND_COUNT;i++)for(unsigned boundary=0;boundary<3;boundary++) {
        char value[16];setup(true);driver_count=SW_SERVICES_AUDIO_DRIVERS_COUNT;driver_list=SW_SERVICES_AUDIO_DRIVERS_LIST;
        setarg("spacewar_services","2");
        snprintf(value,sizeof(value),"%u",boundary==0?sw_services_bounds[i].minimum:
            boundary==1?sw_services_bounds[i].maximum:sw_services_bounds[i].maximum+1);
        setarg(sw_services_bounds[i].name,value);
        if(boundary<2)started();else refused();cases++;
    }
    const char *installed_args[] = {"spacewar_install", "spacewar_storage", "ufs", "ufs_write", "var_hfs"};
    for (unsigned i = 0; i < sizeof(installed_args) / sizeof(installed_args[0]); i++) {
        setup(true); setarg(installed_args[i], NULL); refused(); cases++;
        setup(true); setarg(installed_args[i], "0"); refused(); cases++;
    }
    const char *wrong_volumes[] = {"", "ufs_darwin", "ufs_userdata", "hakios_data", "ufs_darwin_data_extra",
        "ufs_darwin_dataxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"};
    for (unsigned i = 0; i < sizeof(wrong_volumes) / sizeof(wrong_volumes[0]); i++) {
        setup(true); setarg("var_hfs", wrong_volumes[i]); refused(); cases++;
    }
    const char *forbidden[] = {"var_store", "root_apfs", "ufs_native"};
    const char *forbidden_values[] = {"", "0", "1"};
    for (unsigned i = 0; i < 3; i++) {
        for (unsigned j = 0; j < 3; j++) {
            setup(true); setarg(forbidden[i], forbidden_values[j]); refused(); cases++;
        }
    }
    setup(false); setarg("spacewar_install", "1"); refused(); cases++;
    setup(false); setarg("spacewar_install", "0"); refused(); cases++;
    /* No marker: the original exact ten-driver policy remains compatible. */
    setup(false); setarg("spacewar_storage", "1"); setarg("ufs", "2");
    setarg("ufs_write", "1"); setarg("var_hfs", "ufs_darwin_data");
    setarg("var_store", "1"); setarg("root_apfs", "ufs_darwin"); setarg("ufs_native", "0");
    started(); cases++;

    printf("PASS %u actual EPSS cases: exact legacy10/installed11/services18/audio19 profiles, full install tuple, "
           "no preflight MMIO, firmware 1920MHz cap, no domain0/DDR, rollback and ownership restore\n", cases);
    return 0;
}
'''


def main() -> None:
    source = SOURCE.read_text()
    if source.count(BARRIER) != 2:
        raise SystemExit("Expected exactly two EPSS memory barriers")
    source = source.replace(BARRIER, "barriers++;")
    compiler = shlex.split(os.environ.get("CC", "cc"))
    with tempfile.TemporaryDirectory(prefix="spacewar-cpu-perf-") as directory:
        temp = Path(directory)
        test = temp / "test.c"
        binary = temp / "test"
        test.write_text(PREFIX + source + TESTS)
        subprocess.run(compiler + ["-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                                  "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                  "-I", str(ROOT/"drivers/input"), str(test), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True,
                       env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    main()
