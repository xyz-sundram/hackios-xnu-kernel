#!/usr/bin/env python3
"""Compile Phone (1)'s actual NXP probe/write gate with hardware stubs."""
from pathlib import Path
import os,re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
s=(root/'drivers/audio/hakios.speaker.c').read_text()
probe=s[s.index('static void\nspacewar_probe_thread'):s.index('static void\nprobe_thread')]
write=s[s.index('static bool\nregister_write'):s.index('static const char *\namplifier_name')]
pin_defs='\n'.join(x for x in s.splitlines() if x.startswith('#define PIN_SD') or x.startswith('#define PIN_SC'))
harness=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define HAKIOS_DEVICE_SPACEWAR 1
#define __unused __attribute__((unused))
#define TLMM_PHYS 0x0f100000
#define TLMM_SIZE 0x100000
#define GPIO_CFG(n) (tlmm+0x1000*(n))
#define GPIO_IO(n) (tlmm+0x1000*(n)+4)
typedef uintptr_t vm_offset_t;typedef int wait_result_t;
static vm_offset_t tlmm;
static unsigned probe_level,pin_reads,amp_reads,writes,lookups,fail_map,fail_i2c,checks;
static int say(const char*f,...){(void)f;return 0;}
static vm_offset_t ml_io_map(uintptr_t a,unsigned n){(void)n;return fail_map?0:a;}
static unsigned rd(vm_offset_t a){assert(a>=0x0f100000 && a<0x0f200000);unsigned p=(a-0x0f100000)/0x1000;assert(p==8||p==9||(p>=97&&p<=101));pin_reads++;return 0;}
static bool register_read(uint8_t a,uint8_t reg,uint16_t*v){assert(a==0x34||a==0x35);assert(reg==3);amp_reads++;*v=0x0b73;return !fail_i2c;}
static bool bus_take(void){lookups++;return true;}
static void bus_give_back(void){}
static void i2c_start(void){}
static void i2c_stop(void){}
static bool i2c_write(uint8_t v){(void)v;writes++;return true;}
static unsigned level(unsigned pin){(void)pin;return 1;}
static int current_thread(void){return 0;}
static void thread_terminate(int t){(void)t;}
'''
main=r'''
int main(void){
 assert(PIN_SDA==8 && PIN_SCL==9);
 for(unsigned map=0;map<2;map++)for(unsigned fail=0;fail<2;fail++)for(unsigned l=1;l<4;l++){
  fail_map=map;fail_i2c=fail;probe_level=l;pin_reads=amp_reads=0;
  spacewar_probe_thread(NULL,0);checks++;
  assert(pin_reads==(map?0:14));assert(amp_reads==((!map&&l>=2)?2:0));assert(!writes);
 }
 /* Every Awinic write is blocked even if callers accidentally include SOUND. */
 for(unsigned reg=0;reg<256;reg++)for(unsigned a=0x34;a<=0x35;a++){
  assert(!register_write(a,reg,0x55aa));checks++;assert(!writes && !lookups);
 }
 fprintf(stdout,"PASS: %u actual Phone (1) speaker probe/write-gate cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-speaker-') as d:
 p=Path(d);(p/'test.c').write_text(harness+pin_defs+'\n'+write+probe+main)
 subprocess.run([os.environ.get('CC','clang'),'-std=c11','-Werror','-Wno-format','-fsanitize=address,undefined','-g',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
