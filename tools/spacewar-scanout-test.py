#!/usr/bin/env python3
"""Validate retained-display page-table walks without touching hardware."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
#include <cassert>
#include <cstdint>
#include <map>
#include "SpacewarScanout.h"
static std::map<uint64_t,uint64_t> entries;
static bool readEntry(uint64_t at,uint64_t *value) {
    auto p=entries.find(at);if(p==entries.end())return false;*value=p->second;return true;
}
struct LatchOps {
    uint64_t time=0;
    uint32_t current=10,target=0,waiting=0;
    unsigned writes=0,pauses=0;
    bool owner=true,finished=true,ack=true;
    bool owned(){return owner;}
    bool complete(){return finished;}
    uint32_t pending(){return waiting;}
    uint32_t frame(){return current;}
    uint64_t now(){return time;}
    void address(uint32_t value){assert(finished);target=value;writes++;}
    void flush(){assert(writes);waiting=1;}
    void pause(){time+=1000;if(++pauses==3 && ack){waiting=0;current++;}}
};
int main() {
    const uint32_t span=(4320*2400+0x3fff)&~0x3fff;
    assert(spacewarScanoutBacking(0x80000000,span,span));
    assert(spacewarScanoutBacking(0x100000000ULL-span,span,span));
    assert(!spacewarScanoutBacking(0x80000000,span-1,span));
    assert(!spacewarScanoutBacking(0x7fffc000,span,span));
    assert(!spacewarScanoutBacking(0x100000000ULL,span,span));
    assert(!spacewarScanoutBacking(0xffffc000,span,span));
    assert(!spacewarScanoutBacking(0x80000004,span,span));
    assert(!spacewarScanoutBacking(0x80000000,0,0));
    assert(!spacewarScanoutBacking(0x80000000,64*1024*1024,64*1024*1024));
    LatchOps latch;
    assert(spacewarScanoutLatch(latch,0xe19e4000));
    assert(latch.writes==1 && latch.current==11 && latch.waiting==0);
    LatchOps incomplete;incomplete.finished=false;
    assert(!spacewarScanoutLatch(incomplete,0xe19e4000) && incomplete.writes==0);
    LatchOps lost;lost.owner=false;
    assert(!spacewarScanoutLatch(lost,0xe19e4000) && lost.writes==0);
    LatchOps busy;busy.waiting=0x80;
    assert(!spacewarScanoutLatch(busy,0xe19e4000) && busy.writes==0);
    LatchOps timeout;timeout.ack=false;
    assert(!spacewarScanoutLatch(timeout,0xe19e4000) && timeout.time==60000);
    LatchOps wrap;wrap.current=0xffffffff;
    assert(spacewarScanoutLatch(wrap,0xe19e4000) && wrap.current==0);
    assert(spacewarScanoutCounterStep(100,392,2000000,2432,16667000));
    assert(spacewarScanoutCounterStep(2300,160,2000000,2432,16667000));
    assert(!spacewarScanoutCounterStep(100,100,2000000,2432,16667000));
    assert(!spacewarScanoutCounterStep(0,1200,2000000,2432,16667000));
    assert(!spacewarScanoutCounterStep(2432,100,2000000,2432,16667000));
    assert(!spacewarScanoutCounterStep(0,10,0,2432,16667000));
    assert(!spacewarScanoutCounterStep(0,10,20000000,2432,16667000));
    const uint64_t root=0x80000000,mid=0x80001000,leaf=0x80002000,iova=0xe1000123;
    uint64_t physical=0;
    entries[root+3*8]=0xc0000000|0x401; // L1 block covers the whole identity-mapped GB
    assert(spacewarScanoutTranslate(root,32,1,iova,&physical,readEntry) && physical==iova);
    entries[root+3*8]=mid|3;
    entries[mid+((iova>>21)&511)*8]=0x90000000|0x401;
    assert(spacewarScanoutTranslate(root,32,1,iova,&physical,readEntry) && physical==0x90000123);
    entries[mid+((iova>>21)&511)*8]=leaf|3;
    entries[leaf+((iova>>12)&511)*8]=0x180000000ULL|0x403;
    assert(spacewarScanoutTranslate(root,25,1,iova,&physical,readEntry) && physical==0x180000123ULL);
    // A cached/unmapped byte, disabled table, unsupported geometry, or table
    // outside this phone's DRAM must never authorize a scanout buffer.
    entries[leaf+((iova>>12)&511)*8]=0;
    assert(!spacewarScanoutTranslate(root,32,1,iova,&physical,readEntry));
    entries[leaf+((iova>>12)&511)*8]=0x90000000|3; // AF clear
    assert(!spacewarScanoutTranslate(root,32,1,iova,&physical,readEntry));
    assert(!spacewarScanoutTranslate(root,32,0,iova,&physical,readEntry));
    assert(!spacewarScanoutTranslate(root,32|(1<<7),1,iova,&physical,readEntry));
    assert(!spacewarScanoutTranslate(root,32|(1<<14),1,iova,&physical,readEntry));
    assert(!spacewarScanoutTranslate(root,24,1,iova,&physical,readEntry));
    assert(!spacewarScanoutTranslate(root,33,1,iova,&physical,readEntry));
    assert(!spacewarScanoutTranslate(root,32,1,1ULL<<32,&physical,readEntry));
    assert(!spacewarScanoutTranslate(0x15000000,32,1,iova,&physical,readEntry));
    entries[root+3*8]=0x15000000|3;
    assert(!spacewarScanoutTranslate(root,32,1,iova,&physical,readEntry));
}
'''

PROBE_PREFIX = r"""
#include <cassert>
#include <cstdint>
#include <cstddef>
#include "SpacewarScanout.h"
#define NSEC_PER_MSEC 1000ULL
#define NSEC_PER_USEC 1ULL
#define INTF_TIMING_ENGINE_EN 0
#define INTF_FRAME_COUNT 0xac
#define INTF_LINE_COUNT 0xb0
using vm_offset_t=uintptr_t;
static uint32_t regs[256];
static uint64_t now;
static unsigned mode;
static vm_offset_t gSpacewarPanelIntf;
static bool gSpacewarSynchronized;
static bool spacewarNativeAvailable(){return false;}
static void update() {
    const unsigned phase=now%16667;
    regs[INTF_FRAME_COUNT/4]=mode==2?0:now/16667;
    regs[INTF_LINE_COUNT/4]=mode==2?0:mode==1?(phase<3000?phase*2400/3000:2400):phase*2432/16667;
}
static uint64_t mach_absolute_time() {now+=5;update();return now;}
static void IOSleep(unsigned ms) {now+=uint64_t(ms)*1000;update();}
static vm_offset_t panelInterface() {return reinterpret_cast<vm_offset_t>(regs);}
static void nanoseconds_to_absolutetime(uint64_t v,uint64_t *out) {*out=v;}
static void absolutetime_to_nanoseconds(uint64_t v,uint64_t *out) {*out=v;}
static void quiet(const char *s,...) {(void)s;}
#define IOLog quiet
#define hakios_tl quiet
class IOMobileFramebuffer {
public:
    bool fSourceSnapshots=true;
    uint32_t fPanelHeight=2400,fBeamLines=0;
    uint64_t fBeamPeriod=0;
    vm_offset_t fBeamIntf=0;
    void beamProbe();
};
"""
PROBE_CASES = r"""
int main() {
    for(mode=0;mode<4;mode++) {
        for(auto &v:regs)v=0;
        now=0;gSpacewarSynchronized=false;
        gSpacewarPanelIntf=reinterpret_cast<vm_offset_t>(regs);
        regs[0x284/4]=1;regs[0x2b4/4]=0x80000001;
        if(mode==3)gSpacewarPanelIntf=0; // no validated access clock / interface
        IOMobileFramebuffer display;
        display.beamProbe();
        if(mode==0) {
            assert(display.fBeamIntf==gSpacewarPanelIntf && gSpacewarSynchronized);
            assert(display.fBeamLines>=2400 && display.fBeamLines<=2432);
            assert(display.fBeamPeriod>=16600 && display.fBeamPeriod<=16700);
        } else {
            assert(display.fBeamIntf==0 && !gSpacewarSynchronized);
        }
    }
}
"""


RESTORE_PREFIX = r"""
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
struct {bool armed,active,mirrorValid;uint8_t *boot,*mirror;uint8_t *pixels[2];unsigned front,flips;} gSpacewarNative;
static bool gSpacewarSynchronized,owner,ack;
static uint32_t pending,frame,restoredAddress,writes;
static uint64_t timeUs;
static void quiet(const char *s,...) {(void)s;}
#define hakios_tl quiet
struct SpacewarScanoutOps {
    bool owned(){return owner;}
    uint32_t pending(){return ::pending;}
    uint32_t frame(){return ::frame;}
    uint64_t now(){return timeUs;}
    bool complete(){return true;}
    void address(uint32_t value){restoredAddress=value;writes++;}
    void flush(){::pending=1;}
    void pause(){timeUs+=1000;if(ack){::pending=0;::frame++;}}
};
"""
RESTORE_CASES = r"""
int main() {
    std::vector<uint8_t> boot(4320*2400,0x55),mirror(4320*2400,0x7b);
    gSpacewarNative.boot=boot.data();gSpacewarNative.mirror=mirror.data();
    // Never read the display front: it may be mapped as device memory.
    gSpacewarNative.pixels[1]=reinterpret_cast<uint8_t*>(1);gSpacewarNative.front=1;
    for(unsigned test=0;test<4;test++) {
        gSpacewarNative.armed=gSpacewarNative.active=true;gSpacewarNative.flips=12;
        gSpacewarNative.mirrorValid=test!=3;
        gSpacewarSynchronized=true;owner=test!=2;ack=test==0;pending=writes=frame=0;timeUs=0;
        std::memset(boot.data(),0x55,boot.size());
        bool restored=hakios_display_quiesce();
        if(test==2) {assert(!restored && writes==0 && boot.front()==0x55);continue;}
        assert(writes==1 && restoredAddress==0xe1000000);
        assert(boot.front()==(test==3?0x55:0x7b) && boot.back()==boot.front());
        assert(!gSpacewarNative.active && !gSpacewarSynchronized);
        if(test==0)assert(!gSpacewarNative.armed && pending==0 && frame!=0);
        else assert(!restored && gSpacewarNative.armed && timeUs==60000); // backing memory must stay reserved
    }
    gSpacewarNative.armed=false;
    assert(hakios_display_quiesce());
}
"""

with tempfile.TemporaryDirectory(prefix="spacewar-scanout-") as directory:
    d = Path(directory)
    (d / "test.cpp").write_text(SOURCE)
    subprocess.run(["c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "xnu/files/iokit/Kernel"), str(d / "test.cpp"), "-o", str(d / "test")], check=True)
    subprocess.run([str(d / "test")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    framebuffer = (ROOT / "xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp").read_text()
    begin = framebuffer.index("void\nIOMobileFramebuffer::beamProbe(void)")
    end = framebuffer.index("\n}", begin) + 2
    (d / "probe.cpp").write_text(PROBE_PREFIX + framebuffer[begin:end] + PROBE_CASES)
    subprocess.run(["c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "xnu/files/iokit/Kernel"), str(d / "probe.cpp"), "-o", str(d / "probe")], check=True)
    subprocess.run([str(d / "probe")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    begin = framebuffer.index('extern "C" bool\nhakios_display_quiesce(void)')
    end = framebuffer.index("\n}", begin) + 2
    (d / "restore.cpp").write_text(RESTORE_PREFIX + framebuffer[begin:end] + RESTORE_CASES)
    subprocess.run(["c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    str(d / "restore.cpp"), "-o", str(d / "restore")], check=True)
    subprocess.run([str(d / "restore")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
print("PASS: native latch/frame fence and production boot restore/timeout/ownership; production command-mode probe rejects burst/stopped/unverified counters; linear/wrapping scan counters; stalled/burst counters refused; 1GB/2MB/4KB mappings and physical bounds")
