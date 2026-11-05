#!/usr/bin/env python3
"""Execute the production UCSI client against a bounded PMIC wire model."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
production = (root / "drivers/audio/hakios.spacewar.ucsi.inc").read_text()
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define HAKIOS_DEVICE_SPACEWAR 1
#define LCK_MTX_DECLARE(n,g) int n
static bool locked;
static void lck_mtx_lock(int*p){(void)p;assert(!locked);locked=true;}
static void lck_mtx_unlock(int*p){(void)p;assert(locked);locked=false;}
static struct {bool wanted;}battmgr={true};
static struct {bool up,broken;struct {bool open;}channel[1];}glink={.up=true,.channel={{true}}};
#define CHANNEL_PMIC 0
static unsigned sends,writes,elapsed,mode;
static uint64_t ticks,last_control;
static uint32_t cci;
static uint8_t message[16];
static void tell(const char*f,...){(void)f;}
static void spacewar_pan_poll(void){}
static uint64_t now_us(void){return ticks;}
static void sleep_ms(unsigned ms){assert(!locked);elapsed+=ms;ticks+=ms*1000;if(mode==8)battmgr.wanted=false;}
static int glink_send(void *channel,const void *data,uint32_t length);
'''
model = r'''
static int glink_send(void *channel,const void *data,uint32_t length){
    (void)channel;assert(!locked);sends++;
    const uint32_t*q=data;assert(q[0]==32779 && q[1]==1);
    if(mode==8)return 0;
    if(q[2]==0x12){
        assert(length==64);writes++;
        const uint8_t*p=data;memcpy(&last_control,p+20,8);
        assert(spacewar_ucsi_allowed(last_control));
        for(unsigned i=12;i<64;i++)if(i<20 || i>=28)assert(p[i]==0);
        memset(message,0,sizeof(message));
        switch(last_control){
          case 1:cci=1u<<27;break;
          case 0x20004:case 0x30004:cci=1u<<29;break;
          case 0x80010005:case 0xdbe70005:cci=1u<<31;break;
          case 6:cci=(1u<<31)|(16u<<8);message[4]=mode==3?2:1;break;
          case 0x10007:cci=(1u<<31)|(2u<<8);message[0]=3;message[1]=3;break;
          case 0x10012:{
            cci=(1u<<31)|(9u<<8);uint16_t flags=0x202b;uint32_t rdo=(1u<<28)|(160u<<10)|160u;
            memcpy(message+2,&flags,2);memcpy(message+4,&rdo,4);message[8]=1;
            if(mode==4)cci=(1u<<31)|(17u<<8);
            if(mode==5)cci=(1u<<31)|(1u<<30);
            break;
          }
          default:assert(0);
        }
        uint32_t ack[4]={32779,1,0x12,mode==7?1:0};
        spacewar_ucsi_input((const uint8_t*)ack,sizeof(ack));return 0;
    }
    assert(q[2]==0x11 && length==12);
    uint8_t ack[64]={0};uint32_t header[3]={32779,1,0x11};memcpy(ack,header,12);
    uint16_t version=mode==2?0:0x120;memcpy(ack+12,&version,2);memcpy(ack+16,&cci,4);memcpy(ack+28,message,16);
    if(mode==6){uint32_t result=1;memcpy(ack+60,&result,4);}
    spacewar_ucsi_input(ack,mode==1?63:64);
    if(mode==1)return 0;
    // A duplicate cannot overwrite the completed read result.
    memset(ack+12,0,48);spacewar_ucsi_input(ack,64);
    return 0;
}
static void reset_case(unsigned next){
    memset(&spacewar_ucsi,0,sizeof(spacewar_ucsi));mode=next;sends=writes=elapsed=0;
    ticks=1;cci=0;last_control=0;battmgr.wanted=true;glink.up=true;glink.broken=false;glink.channel[0].open=true;
}
int main(void){
    reset_case(0);assert(spacewar_ucsi_start() && !locked && !spacewar_ucsi.pending);
    assert(last_control==0x20004 && writes==11);
    unsigned before=sends;spacewar_ucsi_poll();assert(sends==before);
    uint32_t notice[6]={32779,1,0x13,2,0,0};
    spacewar_ucsi_input((const uint8_t*)notice,sizeof(notice));spacewar_ucsi_poll();assert(sends>before);
    before=sends;spacewar_ucsi_poll();assert(sends==before);
    ticks+=10000000;spacewar_ucsi_poll();assert(sends>before);
    // Every role/PDO/connector-reset operation is refused before transmission.
    uint8_t buffer[48];before=sends;
    const uint64_t forbidden[]={0,3,8,9,10,11,15,0x10003,0x10009,0x1000b,0x40010005,UINT64_MAX};
    for(unsigned i=0;i<sizeof(forbidden)/sizeof(forbidden[0]);i++)
        assert(!spacewar_ucsi_exchange(0x12,forbidden[i],NULL));
    assert(!spacewar_ucsi_exchange(0x11,1,buffer)&&!spacewar_ucsi_exchange(0x11,0,NULL));
    assert(!spacewar_ucsi_exchange(0x33,1,NULL)&&sends==before);
    assert(!spacewar_ucsi_command(6,buffer,17)&&sends==before);
    reset_case(2);assert(!spacewar_ucsi_start()&&!writes); // unknown version has no control side effect
    reset_case(3);assert(!spacewar_ucsi_start()); // reject unexpected multiport topology
    reset_case(4);assert(!spacewar_ucsi_start()&&!spacewar_ucsi.pending); // oversized UCSI payload
    reset_case(5);assert(!spacewar_ucsi_start() && last_control==0x20004); // acknowledge the PPM error
    reset_case(6);assert(!spacewar_ucsi_start()&&!writes); // firmware transport error
    reset_case(7);assert(!spacewar_ucsi_start()&&writes==1); // failed control write
    reset_case(8);assert(!spacewar_ucsi_start()&&elapsed==1&&!spacewar_ucsi.pending); // stop cancellation
    reset_case(1);assert(!spacewar_ucsi_start()&&elapsed==1000&&!writes&&!spacewar_ucsi.pending);
    reset_case(0);spacewar_ucsi.pending=true;spacewar_ucsi.expected=0x11;
    uint32_t wrong[4]={32778,1,0x12,0};spacewar_ucsi_input((const uint8_t*)wrong,16);
    wrong[0]=32779;spacewar_ucsi_input((const uint8_t*)wrong,16);
    assert(spacewar_ucsi.pending&&!spacewar_ucsi.answers);
}
'''
other = r'''
int main(void){uint8_t buffer[48];assert(!spacewar_ucsi_exchange(0x11,0,buffer)&&!sends);}
'''
pan_production = (root / "drivers/audio/hakios.spacewar.pan.inc").read_text()
combined_prefix = prefix.replace("static void spacewar_pan_poll(void){}\n", "") + r'''
static unsigned pan_enables,pan_acks;
'''
combined_model = model[:model.index("static void reset_case")].replace(
    "const uint32_t*q=data;assert(q[0]==32779 && q[1]==1);", r'''
    const uint32_t*q=data;
    if(q[0]==32780){
        assert(length==24&&q[1]==1&&q[2]==0x15&&q[4]==0&&q[5]==0);
        if(q[3]==0x10)pan_enables++;else{assert(q[3]==0x11);pan_acks++;cci=1u<<27;}
        uint32_t reply[3]={32780,1,0x15};spacewar_pan_input((const uint8_t*)reply,12);
        if(q[3]==0x10){uint32_t notice[8]={32780,1,0x16,0};spacewar_pan_input((const uint8_t*)notice,32);}
        return 0;
    }
    assert(q[0]==32779 && q[1]==1);
''').replace("case 1:cci=1u<<27;break;", "case 1:cci=pan_acks?1u<<27:0;break;")
combined_cases = r'''
int main(void){
    assert(spacewar_pan_start()&&pan_enables==1);
    assert(spacewar_ucsi_start()&&pan_acks==1&&elapsed>=40&&!locked);
    assert(spacewar_pan.notices==spacewar_pan.handled);
}
'''
with tempfile.TemporaryDirectory(prefix="spacewar-ucsi-") as temp:
    folder = Path(temp)
    for name, code in (("ucsi", prefix + production + model),
                       ("other", prefix.replace("#define HAKIOS_DEVICE_SPACEWAR 1", "#define HAKIOS_DEVICE_SPACEWAR 0") + production + model[:model.index("static void reset_case")] + other),
                       ("combined", combined_prefix + pan_production + production + combined_model + combined_cases)):
        path = folder / (name + ".c")
        path.write_text(code)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-fsanitize=address,undefined", str(path), "-o", str(folder / name)], check=True)
        subprocess.run([str(folder / name)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
print("PASS actual UCSI: exact wire/init/ACK/poll, source policy preserved, strict command whitelist, bad versions/topology/replies/errors, timeout/stop and board guard")
