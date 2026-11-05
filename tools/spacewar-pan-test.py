#!/usr/bin/env python3
"""Exercise actual PMIC port-notification registration/deferred ACK code."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
code = (root / "drivers/audio/hakios.spacewar.pan.inc").read_text()
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define HAKIOS_DEVICE_SPACEWAR 1
#define LCK_MTX_DECLARE(n,g) int n
static bool locked;
static void lck_mtx_lock(int*p){(void)p;assert(!locked);locked=true;}
static void lck_mtx_unlock(int*p){(void)p;assert(locked);locked=false;}
static struct {bool wanted;}battmgr={true};
static struct {bool up,broken;struct {bool open;}channel[1];}glink={.up=true,.channel={{true}}};
#define CHANNEL_PMIC 0
static unsigned sends,elapsed,mode,enables,acks;
static uint64_t ticks=1;
static void tell(const char*f,...){(void)f;}
static uint64_t now_us(void){return ticks;}
static void sleep_ms(unsigned ms){assert(!locked);elapsed+=ms;ticks+=ms*1000;if(mode==3)battmgr.wanted=false;}
static int glink_send(void *channel,const void *data,uint32_t length);
'''
model = r'''
static int glink_send(void *channel,const void *data,uint32_t length){
    (void)channel;assert(!locked);sends++;
    const uint32_t*q=data;assert(length==24 && q[0]==32780 && q[1]==1 && q[2]==0x15 && q[4]==0 && q[5]==0);
    if(q[3]==0x10)enables++;else{assert(q[3]==0x11);acks++;}
    if(mode==5)return -1;
    if(mode==3)return 0;
    uint32_t answer[8]={32780,1,0x15,0};
    if(mode==2)answer[0]=32779;
    if(mode==4)answer[3]=q[3];
    spacewar_pan_input((const uint8_t*)answer,mode==1?11:mode==4?24:12);
    return 0;
}
static void fresh(unsigned next){
    memset(&spacewar_pan,0,sizeof(spacewar_pan));mode=next;sends=elapsed=enables=acks=0;
    ticks=1;battmgr.wanted=true;glink.up=true;glink.broken=false;glink.channel[0].open=true;
}
'''
cases = r'''
int main(void){
    fresh(0);assert(spacewar_pan_start()&&enables==1&&!spacewar_pan.pending);
    uint8_t notice[32]={0};uint32_t h[3]={32780,1,0xff010016};memcpy(notice,h,12);
    notice[13]=0x42;spacewar_pan_input(notice,32);assert(spacewar_pan.notices==1);
    spacewar_pan_poll();assert(!acks);ticks+=19999;spacewar_pan_poll();assert(!acks);
    ticks++;spacewar_pan_poll();assert(acks==1&&spacewar_pan.handled==1);
    spacewar_pan_poll();assert(acks==1);
    // An unsupported port or malformed notification must never generate an ACK.
    notice[12]=1;spacewar_pan_input(notice,32);notice[12]=0;spacewar_pan_input(notice,31);
    ticks+=50000;spacewar_pan_poll();assert(acks==1&&spacewar_pan.notices==1);
    spacewar_pan_input(notice,32);spacewar_pan_reset();ticks+=50000;spacewar_pan_poll();assert(acks==1);
    unsigned before=sends;
    assert(!spacewar_pan_exchange(0x10,1)&&!spacewar_pan_exchange(0x11,2));
    for(unsigned command=0;command<256;command++)if(command!=0x10&&command!=0x11)
        assert(!spacewar_pan_exchange(command,0));
    assert(sends==before);
    fresh(1);assert(!spacewar_pan_start()&&elapsed==1000&&!spacewar_pan.pending);
    fresh(2);assert(!spacewar_pan_start()&&elapsed==1000&&!spacewar_pan.pending);
    fresh(3);assert(!spacewar_pan_start()&&elapsed==1&&!spacewar_pan.pending);
    fresh(4);assert(spacewar_pan_start()&&spacewar_pan.reply_bytes==24&&spacewar_pan.reply_word==0x10);
    fresh(5);assert(!spacewar_pan_start()&&!elapsed&&!spacewar_pan.pending);
    fresh(0);battmgr.wanted=false;assert(!spacewar_pan_start()&&!sends);
}
'''
other = r'''
int main(void){assert(!spacewar_pan_start()&&!sends);}
'''
with tempfile.TemporaryDirectory(prefix="spacewar-pan-") as temp:
    folder = Path(temp)
    for name, source in (("pan", prefix + code + model + cases),
                         ("other", prefix.replace("#define HAKIOS_DEVICE_SPACEWAR 1", "#define HAKIOS_DEVICE_SPACEWAR 0") + code + model + other)):
        p = folder / (name + ".c")
        p.write_text(source)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-fsanitize=address,undefined", str(p), "-o", str(folder / name)], check=True)
        subprocess.run([str(folder / name)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
print("PASS actual PAN: exact registration/ACK wire, deferred one-port replies, strict command bounds, malformed/foreign/stop/timeout/reset and board guard")
