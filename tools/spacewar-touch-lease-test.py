#!/usr/bin/env python3
"""Host-test the production kernel lease logic; no phone or hardware access.

Only ARM cache primitives and platform functions are replaced by a simulated
peer. Init/profile/acquire/held/release are compiled from the actual .inc.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "drivers/input/hakios.spacewar.lease.inc").read_text()


def replace_function(text, name, replacement):
    marker = "\n" + name + "("
    start = text.index(marker)
    start = text.rfind("static ", 0, start)
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[:start] + replacement + "\n" + text[end:]


source = replace_function(source, "spacewar_live_wait_trace", "static void spacewar_live_wait_trace(const char *s, uint64_t d, uint64_t a) { (void)s;(void)d;(void)a; }")
source = replace_function(source, "spacewar_live_ctr", "static uint64_t spacewar_live_ctr(void) { return fake_ctr; }")
source = replace_function(source, "spacewar_live_irqs_masked", "static bool spacewar_live_irqs_masked(void) { return fake_irqs_masked; }")
source = replace_function(source, "spacewar_live_barrier", "static void spacewar_live_barrier(void) { barriers++; }")
source = replace_function(source, "spacewar_live_clean_line", "static void spacewar_live_clean_line(const volatile void *p) { host_clean(p); }")
source = replace_function(source, "spacewar_live_invalidate_line", "static void spacewar_live_invalidate_line(const volatile void *p) { host_invalidate(p); }")

prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "hakios.spacewar.lease.h"
typedef uintptr_t vm_offset_t;
typedef uintptr_t vm_size_t;
#define NSEC_PER_MSEC 1000000u
static const int hakios_debug=0;
static unsigned board=1, drivers_count=8, processor_count=7, processor_avail_count=7;
static const char *drivers_list="virtio_sound socinfo virtio_input goodix buttons virtio_net thermal virtio_blk";
#define HAKIOS_DEVICE_SPACEWAR board
#define HAKIOS_DRIVERS_COUNT drivers_count
#define HAKIOS_DRIVERS_LIST drivers_list
static uint64_t now, fake_mpidr=0x700, fake_ctr=4u<<16, online_after, cpu_change_after;
static uint8_t shared_bytes[SW_TOUCH_LEASE_PAGE_BYTES] __attribute__((aligned(16384)));
static volatile struct sw_touch_lease *shared=(volatile struct sw_touch_lease *)shared_bytes;
static uint64_t peer_request,peer_release,peer_grant,requested_at,grant_delay;
static bool automatic_grant, map_fails, fake_irqs_masked;
static unsigned maps,barriers,clean_requests,clean_releases,grant_invalidations,sleeps;
static uint64_t expected_pa=0x8f000000;
struct arg {const char *name; uint64_t value; const char *text;};
static struct arg args[64];
static unsigned argc;
static void setarg(const char *name,uint64_t value) {
    for(unsigned i=0;i<argc;i++)if(!strcmp(args[i].name,name)){args[i].value=value;args[i].text=NULL;return;}
    assert(argc<64);args[argc++]=(struct arg){name,value,NULL};
}
static void setstr(const char *name,const char *value) {
    setarg(name,0);for(unsigned i=0;i<argc;i++)if(!strcmp(args[i].name,name))args[i].text=value;
}
static void delarg(const char *name) {
    for(unsigned i=0;i<argc;i++)if(!strcmp(args[i].name,name)){args[i]=args[--argc];return;}
}
static bool PE_parse_boot_argn(const char *name,void *out,unsigned size) {
    for(unsigned i=0;i<argc;i++)if(!strcmp(args[i].name,name)) {
        if(args[i].text) {assert(size>strlen(args[i].text));strcpy(out,args[i].text);return true;}
        if(size!=4&&size!=8)return false;
        if(size==8)*(uint64_t *)out=args[i].value;else *(uint32_t *)out=(uint32_t)args[i].value;
        return true;
    }
    return false;
}
static int quiet_printf(const char *format,...) {(void)format;return 0;}
#define printf quiet_printf
static uint64_t spacewar_power_mpidr(void) {return fake_mpidr;}
static uint64_t mach_absolute_time(void) {return now;}
static void progress(unsigned nanoseconds) {
    now+=nanoseconds;
    if(online_after&&now>=online_after)processor_count=processor_avail_count=7;
    if(cpu_change_after&&now>=cpu_change_after)fake_mpidr=0;
}
static void sleep_ms(uint32_t n) {sleeps++;progress(n*1000000u);}
static void sleep_us(uint32_t n) {progress(n*1000u);}
static void clock_interval_to_deadline(uint32_t n,uint32_t unit,uint64_t *out) {*out=now+(uint64_t)n*unit;}
vm_offset_t ml_io_map_unmappable(vm_offset_t pa,vm_size_t size,unsigned flags) {
    maps++;assert(pa==expected_pa&&size==SW_TOUCH_LEASE_PAGE_BYTES&&flags==2);
    return map_fails?0:(vm_offset_t)shared;
}
static void host_clean(const volatile void *p) {
    uintptr_t offset=(uintptr_t)p-(uintptr_t)shared;
    assert(offset==64||offset==192);barriers++;
    if(offset==64){peer_request=shared->request;requested_at=now;clean_requests++;}
    else {peer_release=shared->release;clean_releases++;}
}
static void host_invalidate(const volatile void *p) {
    uintptr_t offset=(uintptr_t)p-(uintptr_t)shared;
    assert(offset==0||offset==64||offset==128||offset==192);barriers++;
    if(offset==128) {
        grant_invalidations++;
        enum sw_touch_lease_action action=sw_touch_lease_action(peer_request,peer_grant,peer_release,0);
        if(automatic_grant&&action==SW_TOUCH_LEASE_GRANT&&now-requested_at>=grant_delay)peer_grant=peer_request;
        shared->grant=peer_grant;
    }
}
'''

tests = r'''
static unsigned cases;
static void setup(void) {
    board=1;drivers_count=8;drivers_list="virtio_sound socinfo virtio_input goodix buttons virtio_net thermal virtio_blk";
    processor_count=processor_avail_count=7;now=1;fake_mpidr=0x700;fake_ctr=4u<<16;
    online_after=cpu_change_after=0;grant_delay=0;automatic_grant=true;map_fails=false;fake_irqs_masked=false;
    maps=barriers=clean_requests=clean_releases=grant_invalidations=sleeps=0;
    memset(shared_bytes,0,sizeof(shared_bytes));
    shared->magic=SW_TOUCH_LEASE_MAGIC;shared->version=SW_TOUCH_LEASE_VERSION;
    shared->size=SW_TOUCH_LEASE_BYTES;shared->capabilities=SW_TOUCH_LEASE_CAP_EXCLUSIVE;
    peer_request=peer_release=peer_grant=requested_at=0;
    touch_live=0;spacewar_live_page=NULL;spacewar_live_ready=spacewar_live_dead=spacewar_live_stopping=false;
    spacewar_live_depth=0;spacewar_live_pending=spacewar_live_sequence=0;spacewar_live_cache_bytes=64;
    argc=0;setarg("touch_live",1);setarg("touch_shared_pa",expected_pa);
    setarg("bootcpu",7);setarg("cpus",7);setarg("buttons",1);
}
static void refused(void) {
    assert(!spacewar_live_init());assert(!spacewar_live_ready);
    assert(!spacewar_live_acquire()&&!spacewar_live_held());
    assert(!clean_requests&&!clean_releases);cases++;
}
static void cancel_checked(void) {
    assert(!spacewar_live_acquire());assert(spacewar_live_dead&&!spacewar_live_held());
    assert(peer_request==1&&peer_release==1&&!spacewar_live_depth&&!spacewar_live_pending);
    assert(clean_requests==1&&clean_releases==1);cases++;
}
int main(void) {
    /* Legacy probes still use their own CPU0 guard; the new helper is inert. */
    setup();delarg("touch_live");assert(spacewar_live_init()&&spacewar_live_acquire()&&spacewar_live_held());
    spacewar_live_release();assert(!maps&&!clean_requests&&!clean_releases);cases++;
    setup();setarg("touch_live",0);assert(spacewar_live_init()&&spacewar_live_acquire());
    spacewar_live_release();assert(!maps);cases++;
    setup();setarg("touch_live",2);refused();assert(!maps);
    setup();board=0;refused();assert(!maps);
    setup();drivers_count=9;refused();assert(!maps);
    setup();drivers_list="virtio_sound socinfo virtio_input goodix buttons virtio_net thermal adsp";refused();assert(!maps);
    const char *required[]={"bootcpu","cpus","buttons"};
    for(unsigned i=0;i<3;i++){setup();delarg(required[i]);refused();assert(!maps);}
    setup();setarg("bootcpu",0);refused();
    setup();setarg("cpus",1);refused();
    setup();setarg("buttons",0);refused();
    setup();setarg("cpumask",0);refused();
    const char *inactive[]={"cpufreq","gpu","adsp","sound","wifi","haptic","modem","ufs","ufs_native",
        "battery","pmic","battguard","bt","cam","camera","mic","torch","speaker_probe"};
    for(unsigned i=0;i<sizeof(inactive)/sizeof(inactive[0]);i++){setup();setarg(inactive[i],1);refused();assert(!maps);}
    setup();setarg("gpu",0);assert(spacewar_live_init());cases++;
    const char *gpu_drivers="virtio_sound socinfo adreno virtio_input goodix buttons virtio_net thermal virtio_blk";
    setup();drivers_count=9;drivers_list=gpu_drivers;setarg("gpu",1);setarg("adreno_speed",1);
    assert(spacewar_live_init()&&spacewar_live_acquire()&&spacewar_live_held());spacewar_live_release();cases++;
    setup();drivers_count=9;drivers_list=gpu_drivers;refused();assert(!maps);
    setup();drivers_count=9;drivers_list=gpu_drivers;setarg("gpu",1);refused();assert(!maps);
    setup();drivers_count=9;drivers_list=gpu_drivers;setarg("gpu",1);setarg("adreno_speed",4);refused();assert(!maps);
    setup();drivers_count=9;drivers_list=gpu_drivers;setarg("gpu",2);setarg("adreno_speed",1);refused();assert(!maps);
    setup();setarg("adreno_speed",1);refused();assert(!maps);
    setup();setarg("gpu",1);setarg("adreno_speed",1);refused();assert(!maps);
    setup();drivers_count=9;setarg("gpu",1);setarg("adreno_speed",1);refused();assert(!maps);
    const char *perf_drivers="virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal virtio_blk";
    setup();drivers_count=10;drivers_list=perf_drivers;setarg("gpu",1);setarg("adreno_speed",1);setarg("cpufreq",2);
    assert(spacewar_live_init()&&spacewar_live_acquire());spacewar_live_release();cases++;
    for(unsigned speed=2;speed<=3;speed++) {
        setup();drivers_count=10;drivers_list=perf_drivers;setarg("gpu",1);setarg("adreno_speed",speed);setarg("cpufreq",2);
        assert(spacewar_live_init()&&spacewar_live_acquire());spacewar_live_release();cases++;
    }
    setup();drivers_count=10;drivers_list=perf_drivers;setarg("gpu",1);setarg("adreno_speed",1);refused();assert(!maps);
    setup();drivers_count=10;drivers_list=perf_drivers;setarg("gpu",1);setarg("adreno_speed",1);setarg("cpufreq",1);refused();assert(!maps);
    setup();drivers_count=9;drivers_list=gpu_drivers;setarg("gpu",1);setarg("adreno_speed",1);setarg("cpufreq",2);refused();assert(!maps);

    const char *install_drivers="virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal ufs virtio_blk";
    for(unsigned bad=0;bad<13;bad++) {
        setup();drivers_count=11;drivers_list=install_drivers;
        setarg("gpu",1);setarg("adreno_speed",3);setarg("cpufreq",2);
        setarg("spacewar_install",1);setarg("spacewar_storage",1);setarg("ufs",2);setarg("ufs_write",1);
        setstr("var_hfs","ufs_darwin_data");
        if(bad==0) {assert(spacewar_live_init()&&spacewar_live_acquire());spacewar_live_release();cases++;continue;}
        if(bad==1)delarg("spacewar_install");
        if(bad==2)setarg("spacewar_install",0);
        if(bad==3)delarg("spacewar_storage");
        if(bad==4)setarg("ufs",1);
        if(bad==5)setarg("ufs_write",0);
        if(bad==6)setstr("var_hfs","ufs_userdata");
        if(bad==7)setstr("root_apfs","ufs_darwin");
        if(bad==8)setstr("var_store","1");
        if(bad==9)setarg("ufs_native",0);
        if(bad==10){drivers_count=10;drivers_list=perf_drivers;}
        if(bad==11)setarg("gpu",0);
        if(bad==12)setarg("cpufreq",0);
        refused();assert(!maps);
    }
    setup();fake_mpidr=0;refused();assert(!maps);
    setup();fake_mpidr=0x701;refused();
    setup();fake_mpidr=0x10000;refused();
    setup();fake_mpidr=0x800;refused();
    setup();fake_ctr=5u<<16;refused();assert(!maps);
    setup();delarg("touch_shared_pa");refused();assert(!maps);
    setup();setarg("touch_shared_pa",expected_pa+64);refused();assert(!maps);
    setup();setarg("touch_shared_pa",UINT64_MAX-16383);refused();assert(!maps);
    setup();map_fails=true;refused();assert(maps==1);
    setup();shared->magic=0;refused();
    setup();shared->version++;refused();
    setup();shared->size++;refused();
    setup();shared->capabilities=0;refused();
    setup();shared->capabilities=3;refused();
    setup();shared->reserved[1]=1;refused();
    setup();shared->request=1;refused();
    setup();peer_grant=1;refused();
    setup();shared->release=1;refused();
    /* Bring-up waits yield until every approved core is online. */
    setup();processor_avail_count=1;online_after=1000000001ULL;
    assert(spacewar_live_init()&&sleeps>=100&&now>=online_after);cases++;
    setup();processor_avail_count=4;online_after=28000000001ULL;
    assert(spacewar_live_init()&&sleeps>=2800&&now>=online_after);cases++;
    setup();processor_avail_count=1;refused();assert(now>=60000000000ULL&&sleeps>=6000);
    setup();processor_count=8;refused();
    /* Actual per-writer cache clean and peer invalidate paths publish/observe the nonce. */
    setup();assert(spacewar_live_init());assert(spacewar_live_acquire()&&spacewar_live_held());
    assert(peer_request==1&&peer_grant==1&&peer_release==0&&clean_requests==1&&grant_invalidations>0);
    assert(spacewar_live_cpu_safe());spacewar_live_release();assert(peer_release==1&&!spacewar_live_depth);
    assert(!spacewar_live_held());assert(spacewar_live_acquire());assert(peer_request==2&&peer_grant==2);
    spacewar_live_release();assert(peer_release==2&&barriers>0);cases++;
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());
    assert(spacewar_live_acquire()&&spacewar_live_depth==2&&clean_requests==1);
    spacewar_live_release();assert(!peer_release&&spacewar_live_held());
    spacewar_live_release();assert(peer_release==1);cases++;
    setup();assert(spacewar_live_init());grant_delay=3000000;
    assert(spacewar_live_acquire()&&spacewar_live_held()&&now>=3000000&&sleeps>0);
    spacewar_live_release();cases++;
    /* Timeout cancellation makes a late grant harmless and prohibits reuse in this boot. */
    setup();assert(spacewar_live_init());automatic_grant=false;cancel_checked();assert(now>=5000000000ULL);
    peer_grant=1;shared->grant=1;assert(!spacewar_live_held()&&!spacewar_live_acquire());
    assert(sw_touch_lease_action(peer_request,peer_grant,peer_release,0)==SW_TOUCH_LEASE_IDLE);cases++;
    setup();assert(spacewar_live_init());automatic_grant=false;peer_grant=2;cancel_checked();
    /* If CPU membership changes before ACK, cancel without authorizing GPIO writes. */
    setup();assert(spacewar_live_init());automatic_grant=false;cpu_change_after=1000001;cancel_checked();
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());fake_mpidr=0;
    assert(!spacewar_live_held()&&!spacewar_live_cpu_safe());spacewar_live_release();assert(peer_release==1);cases++;
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());processor_avail_count=6;
    assert(!spacewar_live_held());spacewar_live_release();assert(peer_release==1);cases++;
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());shared->capabilities=0;
    assert(!spacewar_live_held());spacewar_live_release();assert(peer_release==1);cases++;
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());shared->release=2;
    assert(!spacewar_live_held());spacewar_live_release();cases++;
    setup();assert(spacewar_live_init());spacewar_live_sequence=UINT64_MAX;
    shared->request=shared->release=UINT64_MAX;assert(!spacewar_live_acquire()&&!clean_requests);cases++;
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());spacewar_live_depth=UINT32_MAX;
    assert(!spacewar_live_acquire()&&spacewar_live_depth==UINT32_MAX);
    spacewar_live_depth=1;spacewar_live_release();cases++;
    /* Every listed XNU physical CPU is accepted, including MPIDR's non-affinity flag. */
    for(unsigned i=1;i<=7;i++) {
        setup();fake_mpidr=(uint64_t)i<<8|0x80000000ULL;
        assert(spacewar_live_init()&&spacewar_live_acquire()&&spacewar_live_cpu_safe());spacewar_live_release();cases++;
    }
    /* Stop-only ownership follows the caller's positive peer-stop proof. */
    setup();delarg("touch_live");assert(spacewar_live_init()&&spacewar_live_stop_acquire());
    assert(!spacewar_live_stopping&&!maps);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;
    assert(!spacewar_live_acquire()&&!spacewar_live_cpu_safe()&&!clean_requests);
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&!clean_requests);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;
    assert(spacewar_live_stop_acquire()&&spacewar_live_stopping&&spacewar_live_held()&&spacewar_live_cpu_safe());
    assert(peer_request==1&&peer_grant==1&&!peer_release);
    spacewar_live_release();assert(peer_release==1&&!spacewar_live_stopping);
    assert(!spacewar_live_acquire()&&!spacewar_live_cpu_safe()&&clean_requests==1);cases++;
    /* A stopped SPI thread's complete/nested grant is taken over, never prematurely released. */
    setup();fake_mpidr=0x100;assert(spacewar_live_init()&&spacewar_live_acquire()&&spacewar_live_acquire());
    fake_mpidr=0x700;processor_avail_count=1;fake_irqs_masked=true;
    assert(spacewar_live_stop_acquire()&&spacewar_live_depth==1&&clean_requests==1&&!peer_release);
    spacewar_live_release();assert(peer_release==1&&!spacewar_live_stopping);cases++;
    setup();fake_mpidr=0x600;assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&!clean_requests);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;shared->capabilities=0;
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&!clean_requests);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;spacewar_live_dead=true;
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&!clean_requests);cases++;
    setup();touch_live=1;fake_irqs_masked=true;assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&!clean_requests);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;setarg("cam",1);
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&!clean_requests);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;automatic_grant=false;
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&spacewar_live_dead);
    assert(peer_request==1&&peer_release==1&&!spacewar_live_depth);cases++;
    setup();assert(spacewar_live_init()&&spacewar_live_acquire());processor_avail_count=1;fake_irqs_masked=true;
    peer_grant=2;
    assert(!spacewar_live_stop_acquire()&&!spacewar_live_stopping&&clean_requests==1&&!clean_releases);cases++;
    setup();assert(spacewar_live_init());processor_avail_count=1;fake_irqs_masked=true;
    assert(spacewar_live_stop_acquire());assert(!spacewar_live_stop_acquire());
    fake_mpidr=0x600;assert(!spacewar_live_cpu_safe()&&!spacewar_live_held());
    spacewar_live_release();assert(peer_release==1&&!spacewar_live_stopping);cases++;
    fprintf(stdout,"PASS %u actual kernel-helper cases: profile/ABI/CPU guards, cache-line publication, ACK/release, nesting, cancellation and late grants\n",cases);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="spacewar-kernel-lease-") as temp:
    temp = Path(temp)
    (temp / "vm").mkdir()
    (temp / "vm/memory_types.h").write_text("#define VM_WIMG_DEFAULT 2\n")
    (temp / "probe.c").write_text(prefix + source + tests)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-fsanitize=address,undefined",
                    "-I", str(temp), "-I", str(root / "drivers/input"), str(temp / "probe.c"),
                    "-o", str(temp / "probe")], check=True)
    subprocess.run([str(temp / "probe")], check=True,
                   env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
