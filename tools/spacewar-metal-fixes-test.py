#!/usr/bin/env python3
"""Execute production FT12 polling and GPU/display snapshot synchronization."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    end = source.index("\n}", start) + 2
    return source[start:end]


def run(folder, name, source, cpp=False, success=True):
    path = folder / (name + (".cpp" if cpp else ".c"))
    path.write_text(source)
    binary = folder / name
    subprocess.run(["c++" if cpp else "cc", "-std=c++11" if cpp else "-std=c11",
                    "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "drivers/input"), str(path), "-o", str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    assert (result.returncode == 0) == success, result.stderr


def main():
    touch = (ROOT / "drivers/input/hakios.goodix.c").read_text()
    parser = (ROOT / "drivers/input/hakios.spacewar.ft12.inc").read_text().replace(
        "#include <libkern/libkern.h>", "#include <string.h>")
    poll = function(touch, "static void\nspacewar_ft12_poll(")
    touch_prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include "hakios.spacewar.ft12.h"
#define NSEC_PER_MSEC 1000
#define MIN(a,b) ((a)<(b)?(a):(b))
#define PIN_INT 81
static uint64_t now, began_at, ended_at;
static unsigned reads, begins, ends, configured_poll;
static bool spacewar_spi_poisoned, spacewar_wire_timing;
static uint64_t spacewar_wire_request, spacewar_wire_grant, spacewar_wire_done;
static void quiet(const char *s, ...) {(void)s;}
#define printf quiet
#define hakios_tl quiet
static bool PE_parse_boot_argn(const char *key, void *out, unsigned size) {
    assert(size==4);
    if (!strcmp(key,"touch_seconds")) {*(uint32_t*)out=0;return true;}
    if (!strcmp(key,"touch_poll_us")) {*(uint32_t*)out=configured_poll;return true;}
    return false;
}
static uint64_t mach_absolute_time(void) {return now;}
static void clock_interval_to_deadline(uint32_t v,unsigned scale,uint64_t *out) {*out=now+(uint64_t)v*scale;}
static void absolutetime_to_nanoseconds(uint64_t v,uint64_t *out) {*out=v*1000;}
static void sleep_us(unsigned us) {assert(us>=1000 && us<=8000);now+=us;}
static void sleep_ms(unsigned ms) {now+=(uint64_t)ms*1000;}
static bool gpio_get(unsigned pin) {assert(pin==81);return true;} // IRQ pulse was missed
static bool spacewar_gpio_cpu_safe(void) {return reads<5;}
static bool spacewar_ft12_wait_runtime(const char *s,bool detail) {(void)s;(void)detail;return true;}
static bool spacewar_ft12_state_probe(const char *s,bool detail) {(void)s;(void)detail;return true;}
static void spacewar_ft12_dump(const uint8_t *p,const char *s) {(void)p;(void)s;}
static bool spacewar_fts_read(uint8_t reg,uint8_t *data,uint32_t length) {
    assert(reg==1 && length==SW_FT12_REPORT_BYTES);
    memset(data,0xff,length);data[0]=0;data[1]=0;
    if(reads<3) {
        data[1]=reads==2?0:1;
        data[2]=(reads==0?0:reads==1?2:1)<<6;
        data[3]=100;data[4]=0;data[5]=200;data[6]=50;data[7]=9;
    }
    reads++;now+=2000; // bounded wire duration, independent of IRQ state
    return true;
}
static void spacewar_ft12_post(const struct sw_ft12_update *u,uint32_t *events) {
    for(unsigned i=0;i<u->count;i++) {
        (*events)++;
        if(u->events[i].action==SW_FT12_BEGIN) {begins++;began_at=now;}
        if(u->events[i].action==SW_FT12_END) {ends++;ended_at=now;}
    }
}
'''
    touch_cases = r'''
int main(void) {
    unsigned polls[]={1000,4000,8000,0,9000};
    for(unsigned i=0;i<sizeof(polls)/sizeof(polls[0]);i++) {
        now=began_at=ended_at=0;reads=begins=ends=0;configured_poll=polls[i];
        spacewar_ft12_poll();
        assert(reads==5 && begins==1 && ends==1);
        assert(ended_at-began_at<=6000); // no 100ms or idle fallback while active
    }
}
'''
    gpu = (ROOT / "drivers/gpu/hakios.gpu.cpp").read_text()
    snapshot = function(gpu, "bool\nMiniIOSurfaceSnapshot(")
    gpu_prefix = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstddef>
#define MIN(a,b) ((a)<(b)?(a):(b))
#define NSEC_PER_MSEC 1000000
struct Info {uint32_t bytesPerElement=4,planeCount=0,width=2,height=2,pixelFormat=1,bytesPerRow=16,offset=4,allocSize=32;};
struct MiniIOSurface {Info info;uint32_t drawn=0;uint8_t data[32]={};uint8_t *pixels(){return data+info.offset;}};
static int lock;
static int *gGPULock=&lock;
static bool locked, failWait;
static uint64_t now;
static unsigned waits, copies;
static MiniIOSurface *changing;
static void IOLockLock(int*) {assert(!locked);locked=true;}
static void IOLockUnlock(int*) {assert(locked);locked=false;}
static bool hakios_gpu_ready() {return true;}
static uint64_t mach_absolute_time() {return now;}
static void clock_interval_to_deadline(uint32_t ms,unsigned scale,uint64_t *out) {*out=now+uint64_t(ms)*scale;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out) {*out=t;}
static bool gpuWait(uint32_t fence,uint64_t) {
    assert(locked);waits++;now+=1000;
    if(failWait)return false;
    // A new submit arrives while gpuWait releases the submission lock.
    if(fence==1) {changing->drawn=2;memset(changing->data,0x33,sizeof changing->data);}
    return true;
}
static void copy_locked(void *to,const void *from,size_t bytes) {
    assert(locked);assert(bytes==8);copies++;std::memcpy(to,from,bytes);
}
#define memcpy copy_locked
'''
    gpu_cases = r'''
#undef memcpy
int main() {
    MiniIOSurface source,target;
    source.drawn=1;changing=&source;
    assert(MiniIOSurfaceSnapshot(&source,&target,500));
    assert(waits==2 && copies==2 && !locked);
    assert(target.data[4]==0x33 && target.data[20]==0x33 && target.data[3]==0 && target.data[28]==0);
    failWait=true;source.drawn=3;
    std::memset(target.data,0xaa,sizeof target.data);
    assert(!MiniIOSurfaceSnapshot(&source,&target,500));
    assert(copies==2 && !locked && target.data[4]==0xaa);
    failWait=false;source.info.allocSize=27;
    assert(!MiniIOSurfaceSnapshot(&source,&target,500));
    assert(copies==2 && !locked);
    assert(!MiniIOSurfaceSnapshot(&source,&source,500));
    assert(!MiniIOSurfaceSnapshot(nullptr,&target,500));
}
'''
    with tempfile.TemporaryDirectory(prefix="spacewar-metal-fixes-") as directory:
        folder = Path(directory)
        run(folder, "poll", touch_prefix + parser + poll + touch_cases)
        old_guard = poll.replace("irq && state.active_mask == 0 && now < next_fallback", "irq && now < next_fallback")
        assert old_guard != poll
        run(folder, "missed-release", touch_prefix + parser + old_guard + touch_cases, success=False)
        run(folder, "snapshot", gpu_prefix + snapshot + gpu_cases, cpp=True)
        stale = snapshot.replace("if (source->drawn == fence) break;", "break;")
        assert stale != snapshot
        run(folder, "stale-fence", gpu_prefix + stale + gpu_cases, cpp=True, success=False)
    print("PASS: actual FT12 polling/parser, missed release pulses, polling bounds; serialized snapshots, fence recheck, timeout and bounds; both old behaviors rejected")


if __name__ == "__main__":
    main()
