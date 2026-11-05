#!/usr/bin/env python3
"""Exercise actual USB power publication and charger policy without hardware."""
import os
import importlib.util
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("metal_package", ROOT / "tools/spacewar-metal-package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
for profile in ("metal-debug", "metal-services"):
    assert package.battery_arguments(profile) == {"adsp": "1", "battmgr": "1"}
for arguments in package.graphics_variants({"metal": "1", **package.battery_arguments("metal-debug")}).values():
    assert arguments["adsp"] == "1" and arguments["battmgr"] == "1"
for profile in ("probe", "render-probe"):
    assert package.battery_arguments(profile) == {"adsp": "0", "battmgr": "0"}


def function(source, signature):
    start = source.index(signature)
    end = source.index("\n}", start) + 2
    return source[start:end]


loader = (ROOT / "xnuloader/loader.c").read_text()
adsp = (ROOT / "drivers/audio/hakios.adsp.c").read_text()
prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "hakios.spacewar.lease.h"
#include "hakios.spacewar.power.h"
#define HAKIOS_USB_RECOVER 1
static uint64_t counter=100,frequency=1000;
static unsigned cleans;
#define READ_SYSREG(reg) fake_##reg()
static uint64_t fake_CNTVCT_EL0(void){return counter;}
static uint64_t fake_CNTFRQ_EL0(void){return frequency;}
static void clean_dcache(uint64_t start,uint64_t end){assert(end-start==64);cleans++;}
static void quiet(const char *s,...){(void)s;}
#define print quiet
static int usb_connected;
static struct sw_usb_power *usb_power_shared;
static uint64_t usb_power_last_tick;
static uint64_t usb_recover_started,usb_recover_lost;
static int usb_recover_armed,usb_recover_attempted;
'''
cases = r'''
int main(void) {
    uint8_t *page=aligned_alloc(16384,16384);assert(page);memset(page,0x5a,16384);
    usb_power_prepare(0,(struct sw_touch_lease*)page);assert(!usb_power_shared && !cleans);
    usb_connected=1;usb_power_prepare(1,(struct sw_touch_lease*)page);
    struct sw_usb_power *p=usb_power_shared;
    assert(sw_usb_power_valid(p) && p->configured && p->max_current_ua==500000);
    assert(p->heartbeat==1 && p->sequence==2 && cleans==2);
    for(unsigned i=0;i<16384;i++)if(i<8192 || i>=8256)assert(page[i]==0x5a);
    counter=500;usb_power_poll();assert(cleans==2);
    counter=1100;usb_power_poll();assert(p->heartbeat==2 && cleans==4);
    uint32_t generation=p->generation;
    usb_connected=0;counter++;usb_power_poll();assert(!p->configured && p->generation==generation+1);
    assert(!sw_usb_input_vote(p,1,1,1));
    usb_connected=1;counter++;usb_power_poll();assert(p->configured && p->generation==generation+2);
    assert(sw_usb_input_vote(p,1,1,1)==500000);
    assert(sw_usb_limit_raise(p,1,1,1,100000)==500000);
    assert(sw_usb_limit_raise(p,1,1,1,400000)==500000);
    assert(!sw_usb_limit_raise(p,1,1,1,0) && !sw_usb_limit_raise(p,1,1,1,2000));
    assert(!sw_usb_limit_raise(p,1,1,1,500000) && !sw_usb_limit_raise(p,1,1,1,UINT32_MAX));
    assert(!sw_usb_input_vote(p,0,1,1) && !sw_usb_input_vote(p,1,0,1));
    for(unsigned adapter=0;adapter<256;adapter++)if(adapter!=1)assert(!sw_usb_input_vote(p,1,1,adapter));
    struct sw_usb_power good=*p;
    for(unsigned test=0;test<10;test++) {
        *p=good;
        switch(test) {
          case 0:p->magic=0;break;case 1:p->version++;break;case 2:p->size--;break;
          case 3:p->sequence|=1;break;case 4:p->sequence=0;break;case 5:p->configured=2;break;
          case 6:p->max_current_ua=3000000;break;case 7:p->heartbeat=0;break;
          case 8:p->reserved[0]=1;break;case 9:p->reserved[2]=1;break;
        }
        assert(!sw_usb_power_valid(p) && !sw_usb_input_vote(p,1,1,1));
    }
    free(page);
}
'''

protocol_prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "hakios.spacewar.power.h"
#define HAKIOS_DEVICE_SPACEWAR 1
#define bzero(p,n) memset((p),0,(n))
#define printf(...) ((void)0)
#define release 0
#define acquire 0
#define os_atomic_inc(p,order) (++*(p))
#define os_atomic_load(p,order) (*(p))
#define LCK_MTX_DECLARE(name,group) int name
static bool held;
static void lck_mtx_lock(int *p){(void)p;assert(!held);held=true;}
static void lck_mtx_unlock(int *p){(void)p;assert(held);held=false;}
static void tell(const char *s,...){(void)s;}
static unsigned elapsed,sends,writes,mode;
static uint32_t hardware_limit;
static struct {int channel[1];} glink;
#define CHANNEL_PMIC 0
static int glink_send(void *c,const void *data,unsigned bytes);
static void sleep_hook(unsigned ms);
static void sleep_ms(unsigned ms){assert(ms==1 || ms==200);elapsed+=ms;sleep_hook(ms);}
static struct sw_usb_power published;
static bool fresh;
static bool battmgr_power_snapshot(struct sw_usb_power *out){*out=published;return fresh;}
'''
protocol_send = r'''
static void sleep_hook(unsigned ms){if(mode==4 && ms==1)battmgr.wanted=false;}
static int glink_send(void *c,const void *data,unsigned bytes) {
    (void)c;assert(!held);sends++;
    const struct battmgr_message *q=data;
    assert(q->owner==32778);
    if(q->opcode==4) {
        assert(q->type==2 && bytes==28);
        const uint32_t *words=data;
        for(unsigned i=3;i<7;i++)assert(!words[i]);
        struct battmgr_message ack={.owner=32778,.type=1,.opcode=4};
        battmgr_input((const uint8_t*)&ack,mode==1?24:12);
        return 0;
    }
    assert(bytes==24 && q->type==1 && q->words[0]==0);
    if(mode==4)return 0; // stop cancels an in-flight wait
    if(q->opcode==0x33) {
        assert(q->words[1]==5 && q->words[2]==500000);writes++;
        if(mode!=3)hardware_limit=q->words[2];
        if(mode==2)return 0; // hardware applied it but the ACK was lost
    } else {
        assert(q->opcode==0x30 || q->opcode==0x32);
        if(mode==5)hardware_limit=400000; // firmware reduced it before read-back
    }
    uint32_t answer=q->opcode==0x33?0:hardware_limit;
    if(q->opcode==0x32 && q->words[1]==0)answer=mode==7?0:1;
    if(q->opcode==0x32 && q->words[1]==7)answer=mode==6?6:1;
    struct battmgr_message ack={.owner=32778,.type=1,.opcode=q->opcode,
        .words={q->words[1],answer,mode==3 && q->opcode==0x33?1:0}};
    struct battmgr_message unrelated=ack;unrelated.words[0]=17;
    battmgr_input((const uint8_t*)&unrelated,sizeof(unrelated));
    battmgr_input((const uint8_t*)&ack,sizeof(ack));
    ack.words[1]=999; // a second response cannot overwrite the completed one
    battmgr_input((const uint8_t*)&ack,sizeof(ack));
    return 0;
}
int main(void) {
    published=(struct sw_usb_power){.magic=SW_USB_POWER_MAGIC,.version=1,.size=64,
        .sequence=2,.configured=1,.max_current_ua=500000,.generation=1,.heartbeat=1};
    battmgr.wanted=true;fresh=true;hardware_limit=100000;
    assert(battmgr_notify_enable() && elapsed==200);
    mode=1;assert(!battmgr_notify_enable() && elapsed==1200);mode=0;
    uint32_t generation=0;
    battmgr_configure_usb(&generation,true,1,1);
    assert(writes==1 && hardware_limit==500000 && generation==1);
    hardware_limit=100000;
    battmgr_configure_usb(&generation,true,1,1);assert(writes==1); // once per configuration
    published.generation++;mode=2;
    battmgr_configure_usb(&generation,true,1,1);
    assert(writes==2 && hardware_limit==500000 && generation==2 && elapsed==2200);
    hardware_limit=100000;
    battmgr_configure_usb(&generation,true,1,1);assert(writes==2); // no SET retry after lost ACK
    published.generation++;mode=0;
    battmgr_configure_usb(&generation,false,1,1);assert(writes==2);
    fresh=false;battmgr_configure_usb(&generation,true,1,1);assert(writes==2);fresh=true;
    for(unsigned adapter=0;adapter<256;adapter++)if(adapter!=1) {
        battmgr_configure_usb(&generation,true,1,adapter);assert(writes==2);
    }
    for(unsigned limit=0;limit<100000;limit+=2000) {
        hardware_limit=limit;battmgr_configure_usb(&generation,true,1,1);assert(writes==2);
    }
    hardware_limit=500000;battmgr_configure_usb(&generation,true,1,1);assert(writes==2);
    hardware_limit=100000;published.configured=0;
    battmgr_configure_usb(&generation,true,1,1);assert(writes==2);published.configured=1;
    battmgr_configure_usb(&generation,true,0,1);assert(writes==2);
    struct battmgr_message q={.owner=32778,.type=1,.opcode=0x33,.words={0,17,1}};
    uint32_t ignored=0;unsigned before=sends;
    assert(!battmgr_exchange(&q,&ignored) && sends==before);
    q.words[1]=5;q.words[2]=3000000;
    assert(!battmgr_exchange(&q,&ignored) && sends==before);
    assert(!battmgr_get(0x33,5,&ignored) && sends==before);
    mode=6;assert(!battmgr_usb_limit(&published,true,1,1,100000) && writes==2);
    mode=7;assert(!battmgr_usb_limit(&published,true,1,1,100000) && writes==2);
    before=sends;battmgr.wanted=false;q.words[2]=500000;
    assert(!battmgr_exchange(&q,&ignored) && sends==before);
    battmgr.wanted=true;mode=4;before=elapsed;
    assert(!battmgr_get(0x32,5,&ignored) && elapsed==before+1 && !battmgr.pending);
    battmgr.wanted=true;mode=3;
    assert(!battmgr_usb_limit(&published,true,1,1,100000) && writes==3);
    mode=5;assert(!battmgr_usb_limit(&published,true,1,1,100000) && writes==4);
}
'''

snapshot_prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdalign.h>
#include <string.h>
#include "hakios.spacewar.power.h"
#define HAKIOS_DEVICE_SPACEWAR 1
#define VM_WIMG_DEFAULT 0
typedef uintptr_t vm_offset_t;
static alignas(16384) uint8_t page[16384];
static volatile struct sw_usb_power *battmgr_power_shared;
static uint64_t battmgr_power_heartbeat,battmgr_power_seen;
static bool battmgr_power_live;
static uint64_t now,physical=0x80000000;
static unsigned invalidates;
static bool race;
static uint64_t now_us(void){return now;}
static bool PE_parse_boot_argn(const char *key,void *out,unsigned size){
    if(!strcmp(key,"touch_shared_pa")){assert(size==8);*(uint64_t*)out=physical;}
    else {assert(size==4);*(uint32_t*)out=!strcmp(key,"bootcpu")?7:1;}
    return true;
}
static vm_offset_t ml_io_map_unmappable(vm_offset_t pa,unsigned bytes,unsigned attr){
    assert(pa==physical && bytes==16384 && !attr);return (uintptr_t)page;
}
static void battmgr_power_invalidate(const volatile void *p){
    assert(p==page+8192);invalidates++;
    if(race && invalidates%2==0)((struct sw_usb_power*)(page+8192))->sequence+=2;
}
'''
snapshot_cases = r'''
int main(void){
    struct sw_usb_power out={0};
    struct sw_usb_power *p=(void*)(page+8192);
    *p=(struct sw_usb_power){.magic=SW_USB_POWER_MAGIC,.version=1,.size=64,.sequence=2,
        .configured=1,.max_current_ua=500000,.generation=1,.heartbeat=1};
    assert(!battmgr_power_snapshot(&out));
    now=1000000;p->heartbeat++;assert(battmgr_power_snapshot(&out));
    now=2000000;assert(battmgr_power_snapshot(&out));
    now=5000001;assert(!battmgr_power_snapshot(&out));
    now=5100000;p->heartbeat++;assert(!battmgr_power_snapshot(&out));
    now=6100000;p->heartbeat++;assert(battmgr_power_snapshot(&out));
    race=true;invalidates=0;assert(!battmgr_power_snapshot(&out) && invalidates==8);
    race=false;p->heartbeat++;now+=1000000;assert(battmgr_power_snapshot(&out));
    battmgr_power_shared=NULL;physical=0;assert(!battmgr_power_snapshot(&out));
    physical=0x80000001;assert(!battmgr_power_snapshot(&out));
    physical=UINT64_MAX-100;assert(!battmgr_power_snapshot(&out));
}
'''

thread_prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "hakios.spacewar.power.h"
#define HAKIOS_DEVICE_SPACEWAR 1
#define __unused __attribute__((unused))
#define LCK_MTX_DECLARE(name,group) int name
#define CHANNEL_PMIC 0
typedef int wait_result_t;
static struct {bool up,broken;struct{bool open;}channel[1];} glink;
static unsigned samples,phase,opens,notify_calls,terminates;
static void tell(const char *s,...){(void)s;}
static bool PE_parse_boot_argn(const char *k,void *v,unsigned size){assert(!strcmp(k,"battmgr") && size==4);*(uint32_t*)v=1;return true;}
static int current_thread(void){return 1;}
static void thread_terminate(int t){assert(t==1);terminates++;}
'''
thread_support = r'''
static void sleep_ms(unsigned ms){
    if(phase==0 && ms==10 && ++opens==2){glink.up=true;glink.channel[0].open=true;phase=1;}
    else if(phase==1 && samples==1 && ms==50){glink.channel[0].open=false;phase=2;opens=0;}
    else if(phase==2 && ms==200 && ++opens==3){glink.channel[0].open=true;phase=3;}
}
static bool battmgr_get(uint32_t op,uint32_t property,uint32_t *out){
    assert(glink.up && glink.channel[0].open);
    if(op==0x32){*out=property==0?1:property==7?1:property==1?5075000:400000;return true;}
    assert(op==0x30);
    *out=property==4?5000+samples*2:property==0?1:property==2?1:property==7?4100000:
        property==9?(uint32_t)-100000:property==12?3700:4357000;
    return true;
}
static bool battmgr_notify_enable(void){notify_calls++;return true;}
static bool battmgr_power_snapshot(struct sw_usb_power *out){memset(out,0,sizeof(*out));return false;}
static void battmgr_configure_usb(uint32_t *g,bool n,uint32_t u,uint32_t a){(void)g;(void)n;(void)u;(void)a;}
static void battmgr_charge_sample(void){}
static void usb_rails_sample(const char *phase){(void)phase;}
static bool spacewar_ucsi_start(void){return true;}
static void spacewar_ucsi_poll(void){}
static bool spacewar_pan_start(void){return true;}
static void spacewar_pan_poll(void){}
static void spacewar_pan_reset(void){}
static void hakios_battery_report(const struct hakios_battery_state *b){
    assert(b->percent_hundredths==5000+(int)samples*2);samples++;
    if(samples==2)battmgr.wanted=false;
}
'''
thread_cases = r'''
int main(void){
    battmgr.wanted=true;battmgr_thread(NULL,0);
    assert(samples==2 && notify_calls==2 && terminates==1 && !battmgr.runs);
}
'''

recovery_prefix = r'''
typedef uint64_t efi_status;
#define EFI_ERROR(s) ((s)>>63)
static int run_active,usb_rx_queued;
static unsigned stops,starts,stop_error,start_error;
static efi_status stop_usb(void){assert(run_active && !usb_connected);stops++;return (uint64_t)stop_error<<63;}
static efi_status usb_start_descriptors(void){starts++;return (uint64_t)start_error<<63;}
static struct {uint64_t revision;efi_status (*stop)(void);} protocol={0x00010001,stop_usb},*USB=&protocol;
'''
recovery_cases = r'''
static void prepare(uint8_t *page){
    usb_connected=1;usb_power_prepare(1,(struct sw_touch_lease*)page);
    usb_connected=0;usb_power_poll();usb_rx_queued=1;stops=starts=0;
}
int main(void){
    uint8_t *page=aligned_alloc(16384,16384);assert(page);
    run_active=1;prepare(page);usb_recover_poll();
    counter+=4999;usb_recover_poll();assert(!stops && !starts);
    counter++;usb_recover_poll();assert(stops==1 && starts==1 && usb_recover_attempted && !usb_rx_queued);
    assert(!usb_connected && !sw_usb_input_vote(usb_power_shared,1,1,1));
    counter+=1000;usb_recover_poll();assert(stops==1 && starts==1);
    // Only a later host configuration can admit the current vote.
    usb_connected=1;usb_power_poll();assert(sw_usb_input_vote(usb_power_shared,1,1,1)==500000);
    prepare(page);USB->revision=1;usb_recover_poll();counter+=6000;usb_recover_poll();assert(!stops);
    USB->revision=0x00010001;prepare(page);run_active=0;
    usb_recover_poll();counter+=6000;usb_recover_poll();assert(!stops);
    run_active=1;prepare(page);usb_recover_armed=0;
    usb_recover_poll();counter+=6000;usb_recover_poll();assert(!stops);
    prepare(page);usb_recover_poll();counter+=30000;usb_recover_poll();assert(!stops);
    prepare(page);usb_recover_poll();counter+=2000;usb_connected=1;usb_recover_poll();
    usb_connected=0;counter+=4000;usb_recover_poll();assert(!stops); // grace restarts after reconnect
    counter+=5000;usb_recover_poll();assert(stops==1 && starts==1);
    prepare(page);stop_error=1;usb_recover_poll();counter+=5000;usb_recover_poll();
    assert(stops==1 && !starts && usb_recover_attempted && !usb_connected);
    counter+=5000;usb_recover_poll();assert(stops==1);stop_error=0;
    prepare(page);start_error=1;usb_recover_poll();counter+=5000;usb_recover_poll();
    assert(stops==1 && starts==1 && !sw_usb_input_vote(usb_power_shared,1,1,1));
    counter+=5000;usb_recover_poll();assert(stops==1 && starts==1);
    free(page);
}
'''
recovery_disabled_cases = r'''
int main(void){
    uint8_t *page=aligned_alloc(16384,16384);assert(page);
    run_active=1;usb_connected=1;usb_power_prepare(1,(struct sw_touch_lease*)page);
    assert(sw_usb_power_valid(usb_power_shared) && !usb_recover_armed);
    usb_connected=0;usb_recover_poll();counter+=6000;usb_recover_poll();
    assert(!stops && !starts);free(page);
}
'''

with tempfile.TemporaryDirectory(prefix="spacewar-charging-") as folder:
    folder = Path(folder)
    c = folder / "power.c"
    code = prefix + function(loader, "static void\nusb_power_poll(") + "\n" + function(
        loader, "static void\nusb_power_prepare(") + cases
    # Only the architecture barrier is substituted; publication code is real.
    code = code.replace('__asm__ volatile ("dmb ishst" ::: "memory");', '(void)0;')
    c.write_text(code)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "drivers/input"), str(c), "-o", str(folder / "power")], check=True)
    subprocess.run([str(folder / "power")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    recovery = (prefix + recovery_prefix + function(loader, "static void\nusb_power_poll(") +
                function(loader, "static void\nusb_power_prepare(") +
                function(loader, "static void\nusb_recover_poll(") + recovery_cases)
    recovery = recovery.replace('__asm__ volatile ("dmb ishst" ::: "memory");', '(void)0;')
    (folder / "recovery.c").write_text(recovery)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "drivers/input"), str(folder / "recovery.c"), "-o", str(folder / "recovery")], check=True)
    subprocess.run([str(folder / "recovery")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    disabled = recovery.replace("#define HAKIOS_USB_RECOVER 1", "#define HAKIOS_USB_RECOVER 0").replace(
        recovery_cases, recovery_disabled_cases)
    (folder / "disabled.c").write_text(disabled)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "drivers/input"), str(folder / "disabled.c"), "-o", str(folder / "disabled")], check=True)
    subprocess.run([str(folder / "disabled")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    declarations = adsp[adsp.index("#define BATTMGR_OWNER"):adsp.index("static void\nbattmgr_input(")]
    functions = "\n".join(function(adsp, signature) for signature in (
        "static void\nbattmgr_input(", "static bool\nbattmgr_exchange(", "static bool\nbattmgr_get(",
        "static bool\nbattmgr_notify_enable(", "static bool\nbattmgr_usb_limit(", "static void\nbattmgr_configure_usb("))
    (folder / "protocol.c").write_text(protocol_prefix + declarations + functions + protocol_send)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-fsanitize=address,undefined",
                    "-I" + str(ROOT / "drivers/input"), str(folder / "protocol.c"), "-o", str(folder / "protocol")], check=True)
    subprocess.run([str(folder / "protocol")], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    snapshot = function(adsp, "static bool\nbattmgr_power_snapshot(").replace(
        '__asm__ volatile ("mrs %0, ctr_el0" : "=r" (ctr));', 'ctr=0;')
    interface = (ROOT / "xnu/files/bsd/sys/hakios.input.h").read_text()
    start = interface.index("struct hakios_battery_state {")
    state = interface[start:interface.index("\n};", start)+3]
    thread = thread_prefix + declarations + state + thread_support + function(
        adsp, "static void\nbattmgr_thread(") + thread_cases
    for name, code in (("snapshot", snapshot_prefix + snapshot + snapshot_cases), ("thread", thread)):
        path=folder/(name+".c");path.write_text(code)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-fsanitize=address,undefined",
                        "-I" + str(ROOT / "drivers/input"), str(path), "-o", str(folder/name)], check=True)
        subprocess.run([str(folder/name)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
    old = thread.replace("for (unsigned round = 0; battmgr.wanted;)",
                         "for (unsigned round = 0; battmgr.wanted && glink.up && glink.channel[CHANNEL_PMIC].open;)")
    assert old != thread
    (folder / "old-thread.c").write_text(old)
    subprocess.run(["cc", "-std=c11", "-Wno-unused-variable", "-I" + str(ROOT / "drivers/input"),
                    str(folder / "old-thread.c"), "-o", str(folder / "old-thread")], check=True)
    failed = subprocess.run([str(folder / "old-thread")], capture_output=True)
    assert failed.returncode != 0, "the old permanently stopped monitor passed the reconnect test"
print("PASS: USB publication/cache snapshots/freshness; bounded startup gadget recovery; 500mA SET/read-back and matching replies; timeout/stop; monitor reconnect; no repeats or suspended/unknown/dedicated/PD votes")
