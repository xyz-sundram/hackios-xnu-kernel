#!/usr/bin/env python3
"""Run the actual bounded USB-supply observer diagnostic with an SPMI model."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "drivers/audio/hakios.adsp.c").read_text()
start = source.index('static struct block usb_rail_core =')
end = source.index('/* ---- RPMh:', start)
production = source[start:end]
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
typedef uintptr_t vm_offset_t;
#define HAKIOS_DEVICE_SPACEWAR 1
struct block {const char *name;uint32_t phys,size;vm_offset_t base;};
static unsigned maps,commands,reports,data_reads,mode=2,channels=512;
static unsigned channel,reg,count,status=1,type=4,version=0x50020000;
static uint64_t ticks;
static bool locked,map_fail,missing;
static void need(struct block*b){maps++;if(!map_fail)b->base=b->phys;}
static bool PE_parse_boot_argn(const char*k,void*v,unsigned n){assert(!strcmp(k,"battmgr")&&n==4);*(uint32_t*)v=mode;return true;}
static void hakios_spmi_lock(void){assert(!locked);locked=true;}
static void hakios_spmi_unlock(void){assert(locked);locked=false;}
static uint64_t now_us(void){ticks+=100;return ticks;}
static void tell(const char*fmt,...){(void)fmt;reports++;}
static const unsigned ppids[]={0x141,0x240,0x249};
static uint32_t rd(vm_offset_t a){
    if(a>=0xc440000 && a<0xc441100){
        unsigned off=a-0xc440000;assert(!locked);
        if(!off)return version;if(off==4)return channels;
        assert(off>=0x900 && !(off&3));
        unsigned index=(off-0x900)/4;assert(index<channels);
        return !missing && index<3?ppids[index]<<8:0;
    }
    assert(locked && a>=0xe600000 && a<0xe610000);
    unsigned off=a-0xe600000;assert(off/0x80==channel);off%=0x80;
    if(off==8)return status;
    assert(off==0x18 || (off==0x1c && count==8));data_reads++;
    if(reg==4)return type|0x3b00;
    assert(reg==0x40);return off==0x18?0x03020100:0x07800504;
}
static void wr(vm_offset_t a,uint32_t value){
    assert(locked && a>=0xe600000 && a<0xe610000);
    assert(!((a-0xe600000)%0x80));channel=(a-0xe600000)/0x80;
    assert(channel<3);reg=(value>>4)&255;count=(value&7)+1;
    assert((reg==4&&count==2)||(reg==0x40&&count==8));
    // Only an observer READ command may be written. There is no PMIC write.
    assert(value==((1u<<27)|(reg<<4)|(count-1)));commands++;
}
'''
cases = r'''
int main(void){
    usb_rails_sample("before-proxy");assert(commands==6&&reports==3&&data_reads==9&&!locked);
    unsigned before=commands;mode=1;usb_rails_sample("normal");assert(commands==before);mode=2;
    version=0x70000000;usb_rails_sample("wrong-arbiter");assert(commands==before);version=0x50020000;
    channels=0;usb_rails_sample("zero");channels=513;usb_rails_sample("too-many");assert(commands==before);channels=512;
    missing=true;usb_rails_sample("unmapped");assert(commands==before);missing=false;
    type=0x10;usb_rails_sample("wrong-type");assert(commands==before+3);before=commands;type=4;
    unsigned old_reads=data_reads;status=5;usb_rails_sample("denied");
    assert(commands==before+3&&data_reads==old_reads&&!locked);before=commands;
    uint64_t began=ticks;status=0;usb_rails_sample("timeout");
    assert(commands==before+3&&data_reads==old_reads&&ticks-began>=6000&&ticks-began<7000&&!locked);
    uint8_t bytes[8];before=commands;
    assert(!usb_rail_read(512,4,bytes,2)&&!usb_rail_read(0,0x46,bytes,2));
    assert(!usb_rail_read(0,4,bytes,3)&&!usb_rail_read(0,4,NULL,2)&&commands==before);
    usb_rail_core.base=usb_rail_observer.base=0;map_fail=true;usb_rails_sample("map-failed");assert(commands==before);
}
'''
disabled = r'''
int main(void){usb_rails_sample("other-board");assert(!maps&&!commands&&!reports);}
'''
with tempfile.TemporaryDirectory(prefix="spacewar-usb-rails-") as temp:
    folder = Path(temp)
    for name, code in (("rails", prefix + production + cases),
                       ("other-board", prefix.replace("#define HAKIOS_DEVICE_SPACEWAR 1", "#define HAKIOS_DEVICE_SPACEWAR 0") + production + disabled)):
        path = folder / (name + ".c")
        path.write_text(code)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-misleading-indentation",
                        "-fsanitize=address,undefined", str(path), "-o", str(folder / name)], check=True)
        subprocess.run([str(folder / name)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
print("PASS actual USB-rail observer: exact peripherals/read commands, ID gate, bounds, timeout, denied/missing/mapping/board/profile refusal; no PMIC writes")
