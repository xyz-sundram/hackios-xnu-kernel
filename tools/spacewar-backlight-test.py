#!/usr/bin/env python3
"""Exercise the actual kernel property/mailbox setter and loader native DCS mailbox poll.

Only cache maintenance, IOKit containers, sleeps and firmware calls are host
providers. The production validation, request/completion and conversion code
runs unchanged apart from the ARM memory barriers.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
loader = (ROOT / "xnuloader/loader.c").read_text()
poll = loader[loader.index("static void\nbacklight_poll(void)"):loader.index("static void\ntouch_lease_quiet_cpu(void)")]
kernel = (ROOT / "drivers/gpu/hakios.spacewar.backlight.inc").read_text()
setter = kernel[kernel.index("IOReturn\nIOMobileFramebuffer::backlightSet"):]
barrier = r'__asm__ volatile\s*\("dmb ish(?:st|ld)" ::: "memory"\);'
poll, n = re.subn(barrier, "", poll)
assert n == 1
setter, n = re.subn(barrier, "", setter)
assert n == 2

harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include "efi.h"
#include "hakios.spacewar.backlight.h"
typedef int IOReturn;
enum {kIOReturnSuccess=0,kIOReturnNotReady=1,kIOReturnBusy=2,kIOReturnTimeout=3,
 kIOReturnIOError=4,kIOReturnNoMemory=5,kIOReturnBadArgument=6,kIOReturnUnsupported=7};
struct OSObject {virtual ~OSObject()=default;};
struct OSNumber:OSObject {uint64_t value;explicit OSNumber(uint64_t v):value(v){} uint64_t unsigned64BitValue(){return value;}};
struct OSDictionary:OSObject {
 std::map<std::string,OSObject*> entries;
 OSObject *getObject(const char *key){auto p=entries.find(key);return p==entries.end()?nullptr:p->second;}
};
#define OSDynamicCast(type,p) dynamic_cast<type*>(p)
static OSObject boolean_false;
#define kOSBooleanFalse (&boolean_false)
static unsigned lock_depth, firmware_sets, firmware_gets, sleeps, publishes, cleans;
static bool firmware_failure, firmware_mismatch, blocked;
static uint32_t firmware_percent;
static sw_backlight mailbox;
static sw_backlight *backlight_shared=&mailbox;
static void print(const char *s,...){(void)s;}
static void IOLog(const char *s,...){(void)s;}
static void flush_dcache(uint64_t a,uint64_t b){assert(a==(uintptr_t)&mailbox.request&&b-a==64);}
static void clean_dcache(uint64_t a,uint64_t b){assert(a==(uintptr_t)&mailbox.answered&&b-a==64);}
static void backlight_invalidate(const volatile void *p){assert(p==&mailbox.answered);}
static void backlight_clean(const volatile void *p){assert(p==&mailbox.request);cleans++;}
enum {SW_DCS_OK=0,SW_DCS_RANGE=7};
#define SW_DCS_MAX 4095u
struct sw_dcs_io {};
static int backlight_native_ready=1;
static int backlight_native_set(uint16_t raw){
 assert(raw<=4095);firmware_sets++;
 if(firmware_failure)return 5;
 firmware_percent=raw;firmware_gets++;return firmware_mismatch?8:0;
}
static void backlight_report(int){}
static void IOLockLock(void*){assert(lock_depth==0);lock_depth++;}
static void IOLockUnlock(void*){assert(lock_depth==1);lock_depth--;}
static void backlight_poll(void);
static void IOSleep(unsigned ms){assert(ms==1&&lock_depth==1);sleeps++;if(!blocked)backlight_poll();}
struct IOService {virtual IOReturn setProperties(OSObject*){return kIOReturnUnsupported;}};
struct IOMobileFramebuffer:IOService {
 bool fBacklightReady=true;void *fBacklightLock=nullptr;
 volatile sw_backlight *fBacklightPage=&mailbox;
 uint64_t fBacklightSequence=0;uint32_t fBacklightApplied=37;
 bool backlightPublish(){publishes++;return true;}
 void setProperty(const char*,OSObject *p){assert(p==kOSBooleanFalse);}
 IOReturn backlightSet(uint32_t);
 IOReturn setProperties(OSObject*) override;
};
static void reset(){mailbox={};firmware_percent=37;firmware_failure=firmware_mismatch=blocked=false;
 lock_depth=firmware_sets=firmware_gets=sleeps=publishes=cleans=0;}
'''

tests = r'''
int main(){unsigned checks=0;
 for(unsigned percent:{0u,1u,25u,75u,100u}){
  reset();IOMobileFramebuffer fb;
  assert(fb.backlightSet(percent)==0&&fb.fBacklightApplied==percent);
  assert(mailbox.answered==1&&mailbox.request==1&&firmware_sets==1&&firmware_gets==1&&publishes==1);checks++;
  backlight_poll();assert(firmware_sets==1);checks++;
 }
 reset();IOMobileFramebuffer fb;
 assert(fb.backlightSet(37)==0&&mailbox.request==0&&!firmware_sets);checks++;
 for(uint64_t nits:{0ull,1ull,125ull<<16,250ull<<16,500ull<<16}){
  reset();IOMobileFramebuffer v;OSNumber value(nits);OSDictionary props;props.entries["brightness-nits"]=&value;
  assert(v.setProperties(&props)==0&&v.fBacklightApplied<=100);
  if(nits==1)assert(v.fBacklightApplied==1);
  if(nits==(125ull<<16))assert(v.fBacklightApplied==25);
  checks++;
 }
 for(uint64_t nits:{(500ull<<16)+1,static_cast<unsigned long long>(UINT64_MAX)}){
  reset();IOMobileFramebuffer v;OSNumber value(nits);OSDictionary props;props.entries["brightness-nits"]=&value;
  assert(v.setProperties(&props)==kIOReturnBadArgument&&!firmware_sets&&!mailbox.request);checks++;
 }
 reset();{IOMobileFramebuffer v;OSNumber value(750);OSDictionary params,props;
 params.entries["brightness"]=&value;props.entries["IODisplayParameters"]=&params;
 assert(v.setProperties(&props)==0&&v.fBacklightApplied==75);checks++;}
 reset();{IOMobileFramebuffer v;OSNumber bad(1001);OSDictionary props;props.entries["brightness"]=&bad;
 assert(v.setProperties(&props)==kIOReturnBadArgument&&!firmware_sets);checks++;}
 reset();{IOMobileFramebuffer v;OSNumber nits(100<<16),user(100);OSDictionary props;
 props.entries["brightness-nits"]=&nits;props.entries["brightness"]=&user;
 assert(v.setProperties(&props)==kIOReturnBadArgument&&!firmware_sets);checks++;}
 for(unsigned mode=0;mode<3;mode++){
  reset();IOMobileFramebuffer v;firmware_failure=mode==0;firmware_mismatch=mode==1;blocked=mode==2;
  assert(v.backlightSet(80)==(mode==2?kIOReturnTimeout:kIOReturnIOError));
  assert(v.fBacklightApplied==37&&!v.fBacklightReady&&!publishes&&!lock_depth);
  if(mode==2)assert(sleeps==2000&&!firmware_sets);
  assert(v.backlightSet(50)==kIOReturnNotReady);checks++;
 }
 reset();mailbox.request=1;mailbox.percent=101;backlight_poll();assert(mailbox.status&&!firmware_sets&&mailbox.answered==1);checks++;
 reset();mailbox.request=1;mailbox.percent=50;mailbox.request_reserved=1;backlight_poll();assert(mailbox.status&&!firmware_sets);checks++;
 reset();mailbox.answered=2;mailbox.request=1;backlight_poll();assert(!firmware_sets);checks++;
 reset();backlight_native_ready=0;mailbox.request=1;backlight_poll();assert(!firmware_sets);backlight_native_ready=1;checks++;
 reset();mailbox.answered=5;IOMobileFramebuffer busy;assert(busy.backlightSet(50)==kIOReturnBusy&&!firmware_sets);checks++;
 printf("PASS %u production backlight cases: native properties, bounds, hardware acknowledgements, stale requests, errors and timeout\n",checks);
}
'''

with tempfile.TemporaryDirectory(prefix="spacewar-backlight-") as tmp:
    p = Path(tmp)
    (p / "test.cpp").write_text(harness + poll + setter + tests)
    subprocess.run([os.environ.get("CXX", "clang++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-g", "-I" + str(ROOT / "drivers/gpu"),
        "-I" + str(ROOT / "xnuloader"), str(p / "test.cpp"), "-o", str(p / "test")], check=True)
    subprocess.run([str(p / "test")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
