#!/usr/bin/env python3
"""Run the actual PM8350B backend against a bounded SPMI model, no phone access."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
code=(root/'drivers/haptic/hakios.spacewar.haptic.inc').read_text()
harness=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define __unused __attribute__((unused))
#define ACTIVE_US 30000
#define TOLD_EVENTS 24
#define DEVFS_CHAR 1
#define UID_ROOT 0
#define GID_WHEEL 0
#define makedev(a,b) (a)
typedef uintptr_t vm_offset_t;
typedef int wait_result_t;
static unsigned level=1,locks,register_writes,fail_reg=0xfff,fail_p=99,failures,checks,nodes;
static bool leaving,broken,feed,short_feed;static int haptic_cdevsw;
static uint64_t clock_us,heard_us,quiet_us,onset_us,written_frames;
static uint32_t heard_peak;
static uint8_t regs[4][256],last[4],staged[4];
static unsigned fail_read_p=99,fail_read_reg=0xfff;
static uint64_t starts[32],stops[32];static unsigned nstarts,nstops;
static uint8_t cal_writes[32];static unsigned ncal;
static uint64_t now_us(void){return clock_us++;}
static uint16_t ids[4]={0x3f0,0x3f1,0x3f2,0x09d};
static uint32_t rd(vm_offset_t a){
 if(a==0x0c440000)return 0x50020000;
 if(a==0x0c440004)return 16;
 for(unsigned p=0;p<4;p++){
  if(a==0x0c440900+4*(10+p))return (uint32_t)ids[p]<<8;
  if(a==0x0c40a700+4*(10+p))return p==2?1:0;
  vm_offset_t obs=0x0e600000+0x80*(10+p),wr=0x0c600000+0x10000*(10+p);
  if(a==obs+8 || a==wr+8)return 1;
  if(a==obs+0x18){uint8_t v=regs[p][last[p]];if(p==fail_read_p && last[p]==fail_read_reg){fail_read_p=99;return v^1;}return v;}
 }
 return 0;
}
static void wr(vm_offset_t a,uint32_t v){
 for(unsigned p=0;p<4;p++){
  vm_offset_t obs=0x0e600000+0x80*(10+p),port=0x0c600000+0x10000*(10+p);
  if(a==obs){assert(v&(1u<<27));last[p]=(v>>4)&255;return;}
  if(a==port+0x10){staged[p]=v;return;}
  if(a==port){unsigned reg=(v>>4)&255;register_writes++;regs[p][reg]=staged[p];
   if(p==0 && reg==0x72){assert(ncal<32);cal_writes[ncal++]=staged[p];}
   if(p==0 && reg==0x4c){if(staged[p]&0x80){assert(nstarts<32);starts[nstarts++]=clock_us;}else if(nstarts>nstops){stops[nstops++]=clock_us;}}
   if(p==fail_p && reg==fail_reg){fail_read_p=p;fail_read_reg=reg;fail_p=99;failures++;}
   if((p==3 && reg==0xe5) || (p==0 && reg==0x66))regs[p][reg]=0;
   return;
  }
 }
}
static void hakios_spmi_lock(void){locks++;}
static void hakios_spmi_unlock(void){assert(locks);locks--;}
static void spin_us(unsigned us){clock_us+=us;}
static void wait_ms(unsigned ms){clock_us+=(uint64_t)ms*1000;if(feed){if(!short_feed || clock_us<10000)heard_us=clock_us;else quiet_us=clock_us;if(clock_us>1200000)leaving=true;}}
static void say(const char*f,...){(void)f;}
static vm_offset_t ml_io_map(uintptr_t a,unsigned n){(void)n;return a;}
static int cdevsw_add(int a,void*b){(void)a;(void)b;return 1;}
static void*devfs_make_node(int a,int b,int c,int d,int e,const char*f){(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;nodes++;return(void*)1;}
static int current_thread(void){return 0;}
static void thread_terminate(int t){(void)t;}
'''
main=r'''
static void reset(void){
 memset(regs,0,sizeof regs);memset(sw_hap_saved,0,sizeof sw_hap_saved);
 for(unsigned p=0;p<4;p++){
  regs[p][1]=(p<2)?2:0;sw_hap_peripherals[p].found=true;sw_hap_peripherals[p].writable=true;
  sw_hap_peripherals[p].channel=10+p;sw_hap_peripherals[p].write=0x0c600000+0x10000*(10+p);
 }
 regs[2][0]=1;regs[0][0x4c]=1;regs[0][0x4e]=0xe0; /* configured SWR is not active SWR input */
 sw_hap_observer=0x0e600000;sw_hap_ready=true;sw_hap_fault=sw_hap_changed=sw_hap_active=leaving=feed=short_feed=false;
 clock_us=1;heard_us=quiet_us=heard_peak=onset_us=written_frames=0;nodes=register_writes=locks=failures=nstarts=nstops=ncal=0;
 fail_p=fail_read_p=99;fail_reg=fail_read_reg=0xfff;
}
int main(void){
 reset();assert(sw_hap_configure());checks++;assert(regs[0][0x48]==72);assert(regs[0][0x5c]==4 && regs[0][0x5d]==0x98);
 assert(sw_hap_start(255));checks++;assert(regs[1][0x26]==64);assert(regs[0][0x4c]==0x81 && (regs[0][0x68]&1));assert(sw_hap_active);
 assert(sw_hap_stop());checks++;assert(regs[0][0x46]==0 && !(regs[0][0x4c]&0x80) && !(regs[0][0x68]&1));
 /* A write which lands but fails readback must still get every off write. */
 reset();assert(sw_hap_configure());fail_p=0;fail_reg=0x46;assert(!sw_hap_start(20));checks++;
 assert(failures==1 && sw_hap_fault && !sw_hap_active && regs[0][0x46]==0 && !(regs[0][0x4c]&0x80) && regs[3][0x42]==2);
 reset();assert(sw_hap_configure());assert(sw_hap_start(20));regs[0][0x0c]=4;assert(!sw_hap_healthy());checks++;
 assert(!sw_hap_active && regs[0][0x46]==0 && regs[3][0x42]==2);
 /* Register whitelist and owner checks remain active even for forced cleanup. */
 for(unsigned p=0;p<4;p++)for(unsigned r=0;r<256;r++){
  reset();bool allowed=sw_hap_allowed(p,r);bool result=sw_hap_write(p,r,1,false);
  assert(allowed==result);checks++;
 }
 reset();sw_hap_peripherals[0].writable=false;assert(!sw_hap_write(0,0x46,0,true));checks++;assert(!register_writes);
 reset();assert(sw_hap_configure());regs[2][0x41]=regs[2][0x46]=0x80;assert(!sw_hap_start(20));checks++;assert(!sw_hap_active && nstarts==0 && clock_us<25000);
 /* The Nothing driver accepts enabled open-loop boost without waiting for
  * standby; PM8350B toggles AUTO calibration only in that same state. */
 for(unsigned enabled=0;enabled<2;enabled++)for(unsigned follow=0;follow<2;follow++)for(unsigned cal=0;cal<4;cal++){
  reset();assert(sw_hap_configure());regs[2][0x46]=enabled?0x80:0;regs[2][0x41]=follow?0x80:0;
  regs[0][0x72]=0xa0|(cal<<2);regs[0][0x68]=0xa0;ncal=0;
  bool ok=sw_hap_start(32);assert(ok==!(enabled && follow));checks++;
  if(ok){bool toggled=enabled && !follow && cal==1;
   assert(ncal==(toggled?2u:0u));if(toggled)assert(cal_writes[0]==0xa0 && cal_writes[1]==0xa4);
   assert(regs[0][0x72]==(0xa0|(cal<<2)) && regs[0][0x68]==0xa1);
   assert(sw_hap_stop() && regs[0][0x68]==0xa0);
  }
 }
 reset();regs[0][0x4c]=0x81;assert(!sw_hap_configure());checks++;assert(!register_writes);
 reset();regs[0][0x4e]=0xe0;assert(sw_hap_configure());checks++;assert(regs[0][0x4e]==0xa0);sw_hap_restore();assert(regs[0][0x4e]==0xe0 && regs[0][0x46]==0);
 /* Read-only levels never issue payload writes or expose an output endpoint. */
 for(unsigned l=1;l<=2;l++){reset();level=l;sw_haptic_thread(NULL,0);assert(!register_writes && !nodes);checks++;}
 reset();level=4;sw_haptic_thread(NULL,0);assert(nstarts==1 && nstops==1);assert(stops[0]-starts[0]>=40000 && stops[0]-starts[0]<41000);checks++;
 reset();regs[0][1]=3;level=4;sw_haptic_thread(NULL,0);assert(!register_writes);checks++;
 reset();assert(sw_hap_configure());feed=true;heard_us=onset_us=clock_us;heard_peak=32768;sw_hap_serve();
 assert(nodes==1 && nstarts>=2 && nstops==nstarts);
 for(unsigned i=0;i<nstarts;i++){assert(stops[i]-starts[i]<=SW_HAP_MAX_US+5000);if(i)assert(starts[i]-stops[i-1]>=SW_HAP_REST_US);checks++;}
 assert(!sw_hap_active && regs[0][0x46]==0 && !locks);
 /* A short, soft keyboard wave survives a subsequent silence packet and8ms
  * settling, then plays at the physically verified32/40ms floor exactly once. */
 reset();assert(sw_hap_configure());feed=short_feed=true;heard_us=onset_us=clock_us;heard_peak=431;sw_hap_serve();
 assert(nstarts==1 && nstops==1 && starts[0]>=SW_HAP_SETTLE_US && regs[1][0x26]==32);
 assert(stops[0]-starts[0]>=SW_HAP_TAP_US && stops[0]-starts[0]<SW_HAP_TAP_US+5000);checks++;
 fprintf(stdout,"PASS: %u actual PM8350B backend cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-haptic-') as d:
 p=Path(d);(p/'test.c').write_text(harness+code+main)
 subprocess.run([os.environ.get('CC','clang'),'-std=c11','-Werror','-Wno-format','-fsanitize=address,undefined','-g',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
