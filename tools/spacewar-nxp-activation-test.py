#!/usr/bin/env python3
"""Run the actual NXP activation/cleanup code with register/I2C fault mocks.

No private profile, gain changes, MMIO or hardware access. Also checks that
diagnostics use existing status reads rather than consuming sticky bits twice.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
header = (root / 'drivers/audio/hakios.spacewar.audio.h').read_text()
source = (root / 'drivers/audio/hakios.spacewar.speaker.inc').read_text().replace('#include "hakios.spacewar.audio.h"', '')
harness = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uintptr_t tlmm=0x1000;
static bool leaving;
#define TLMM_PHYS 0x0f100000u
#define TLMM_SIZE 0x100000u
#define CFG_OUTPUT 0x203u
#define GPIO_CFG(n) ((uintptr_t)(n)*0x1000)
#define GPIO_IO(n) (GPIO_CFG(n)+4)
#define NSEC_PER_MSEC 1000000ull
static uint16_t regs[2][256];
static char logtext[16000];
static unsigned used,status_reads,temp_reads,writes,waits,checks;
static unsigned fault_kind;
static const char *fault_stage;
static uint8_t wire[4];
static unsigned wire_count;
static bool wire_bad,pending_mismatch,persistent_fault;
static uint64_t fake_time,read_cost;
static unsigned temp_delay[2],temp_samples[2];
static unsigned status_after_samples;
static uint16_t status_after_value;
static uint32_t boot_mode=3,boot_seconds=3;
static bool boot_seconds_present;
static bool PE_parse_boot_argn(const char*name,void*out,int length){
 assert(length==4);
 if(!strcmp(name,"sound")){memcpy(out,&boot_mode,4);return true;}
 assert(!strcmp(name,"sound_secs"));
 if(!boot_seconds_present)return false;
 memcpy(out,&boot_seconds,4);return true;
}
static uint64_t mach_absolute_time(void){return fake_time;}
static void nanoseconds_to_absolutetime(uint64_t n,uint64_t*out){*out=n;}
static int say(const char *f,...){
 va_list ap;va_start(ap,f);int n=vsnprintf(logtext+used,sizeof logtext-used,f,ap);va_end(ap);
 assert(n>=0&&(unsigned)n<sizeof logtext-used);used+=(unsigned)n;return n;
}
static bool register_read(uint8_t,uint8_t,uint16_t*);
static bool bus_take(void);
static void bus_give_back(void){}
static void i2c_start(void){wire_count=0;wire_bad=false;}
static void i2c_stop(void);
static bool i2c_write(uint8_t);
static uintptr_t ml_io_map(uintptr_t a,unsigned n){(void)n;return a;}
static uint32_t rd(uintptr_t a){(void)a;assert(!"unexpected MMIO read");return 0;}
static void wr(uintptr_t a,uint32_t v){(void)a;(void)v;assert(!"unexpected MMIO write");}
static void wait_ms(unsigned ms){waits+=ms;fake_time+=(uint64_t)ms*NSEC_PER_MSEC;}
'''
mocks = r'''
static bool fault_here(unsigned kind){
 if(fault_kind!=kind||!fault_stage||strcmp(fault_stage,sw_tfa_stage))return false;
 if(!persistent_fault)fault_kind=0;return true;
}
static bool register_read(uint8_t a,uint8_t reg,uint16_t *v){
 fake_time+=read_cost;
 assert(a==0x34||a==0x35);if(reg==0x10)status_reads++;if(reg==0x16)temp_reads++;
 if(fault_here(1)&&a==0x34&&reg==0)return false;
 if(a==0x34&&reg==0x16&&temp_samples[0]>=1&&fault_here(5))return false;
 *v=regs[a-0x34][reg];
 if(a==0x34&&reg==0x10&&status_after_samples&&temp_samples[0]>=status_after_samples)*v=status_after_value;
 if(reg==0x16&&++temp_samples[a-0x34]<=temp_delay[a-0x34])*v=0x100;
 if(pending_mismatch&&reg==0&&a==0x34){*v^=1;pending_mismatch=false;}
 return true;
}
static bool bus_take(void){return !fault_here(4);}
static bool i2c_write(uint8_t v){
 assert(wire_count<4);wire[wire_count++]=v;
 if(wire_count==3&&fault_here(2)){wire_bad=true;return false;}
 return true;
}
static void i2c_stop(void){
 if(wire_count!=4||wire_bad)return;
 unsigned dev=(wire[0]>>1)-0x34;assert(dev<2);
 uint16_t value=(uint16_t)((unsigned)wire[2]<<8)|wire[3];
 regs[dev][wire[1]]=value;writes++;
 if(dev==0&&wire[1]==0&&fault_here(3))pending_mismatch=true;
}
static void reset(void){
 memset(regs,0,sizeof regs);memset(sw_tfa_identified,1,sizeof sw_tfa_identified);
 sw_tfa_prepared=true;sw_tfa_started=sw_tfa_fault=sw_tfa_reset_saved=leaving=false;
 sw_tfa_stage="test";fault_kind=0;fault_stage=NULL;used=status_reads=temp_reads=writes=waits=0;
 logtext[0]=0;pending_mismatch=persistent_fault=false;fake_time=read_cost=0;
 memset(temp_delay,0,sizeof temp_delay);memset(temp_samples,0,sizeof temp_samples);
 status_after_samples=0;status_after_value=0;
 boot_mode=3;boot_seconds=3;boot_seconds_present=false;
 memset(sw_tfa_unknown_temperature,0,sizeof sw_tfa_unknown_temperature);
 for(unsigned d=0;d<2;d++){
  regs[d][0]=0x4201;regs[d][1]=0x8000;regs[d][0x10]=0x57;regs[d][0x16]=25;
  regs[d][0x13]=0x500;regs[d][0x14]=0x4f;
 }
}
static void runtime(void){sw_tfa_started=true;for(unsigned d=0;d<2;d++)regs[d][0]=(regs[d][0]&~1u)|0x18;}
static void safely_stopped(void){
 assert(sw_tfa_fault&&!sw_tfa_started);
 for(unsigned d=0;d<2;d++)assert((regs[d][0]&0x19)==1);
}
int main(void){
 reset();assert(sw_tfa_start());assert(sw_tfa_started&&!sw_tfa_fault&&!strstr(logtext,"FAIL"));
 assert(status_reads==4&&temp_reads==2); /* one CLKS poll + one health read/device */
 for(unsigned d=0;d<2;d++)assert((regs[d][0]&0x19)==0x18&&(regs[d][1]&4));checks++;
 for(unsigned mode=0;mode<3;mode++){
  reset();if(mode==0)sw_tfa_prepared=false;if(mode==1)sw_tfa_fault=true;if(mode==2)leaving=true;
  assert(!sw_tfa_start());assert(!writes&&!status_reads&&strstr(logtext,"op=start-refused"));checks++;
 }
 const char *stages[]={"start-powerup","start-mansconf","start-dca","start-ampe"};
 const char *operations[]={"read-io","write-no-ack","write-readback","bus-not-idle"};
 for(unsigned k=1;k<=4;k++){
  reset();fault_kind=k;fault_stage=stages[k-1];assert(!sw_tfa_start());safely_stopped();
  assert(strstr(logtext,stages[k-1])&&strstr(logtext,operations[k-1]));checks++;
 }
 reset();regs[0][0x10]=0;assert(!sw_tfa_start());safely_stopped();
 assert(status_reads==50&&!temp_reads&&waits==50&&strstr(logtext,"clock-timeout-50ms"));checks++;
 reset();regs[0][0x10]=0x48;assert(!sw_tfa_start());safely_stopped();
 assert(status_reads==3&&temp_reads==1&&strstr(logtext,"op=native-status")&&strstr(logtext,"actual=0x48"));checks++;
 const int temperatures[]={-41,-40,79,80};
 for(unsigned i=0;i<4;i++){
  reset();regs[0][0x16]=(uint16_t)temperatures[i]&0x1ff;
  bool ok=sw_tfa_start();assert(ok==(i==1||i==2));
  if(!ok){safely_stopped();assert(strstr(logtext,"op=temperature"));}checks++;
 }
 reset();sw_tfa_stage="prepare-powerdown";fault_kind=2;fault_stage=sw_tfa_stage;persistent_fault=true;sw_tfa_stop();
 assert(sw_tfa_fault&&strstr(logtext,"stage=prepare-powerdown")&&strstr(logtext,"powerdown-unverified"));checks++;
 /* Native-only UNKNOWN exception: sound3 default<=3s, exactADCnotready,
  * both protectedchips; never a fabricated temperature or extra wait. */
 reset();regs[0][0x16]=regs[1][0x16]=0x100;regs[0][0x13]=regs[1][0x13]=0x100;
 assert(sw_tfa_start());assert(!waits&&strstr(logtext,"temperature UNKNOWN")&&!strstr(logtext,"decoded=-256"));checks++;
 /* A subsequently available real hot reading is still terminal. */
 regs[0][0x16]=80;regs[0][0x13]=0x500;assert(!sw_tfa_healthy());safely_stopped();checks++;
 /* Disable each required native protection/operating condition separately. */
 const struct {uint8_t reg;uint16_t bit;bool set;} bad[]={
  {0x10,0x2,0},{0x10,0x4,0},{0x10,0x10,0},{0x10,0x40,0},
  {0x10,0x8,1},{0x10,0x20,1},{0x10,0x100,1},{0x10,0x400,1},
  {0x13,0x100,0},{0x14,0x40,0},
  {0,1,1},{0,8,0},{0,0x10,0},{0,0x800,1},{0,0x1000,1},
  {0xb0,2,1},{0xb0,8,1},{0xb0,0x10,1},{0xb0,0x20,1},{0xb0,0x40,1},{0xc0,1,1}};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++)for(unsigned dev=0;dev<2;dev++){
  reset();runtime();for(unsigned d=0;d<2;d++){regs[d][0x16]=0x100;regs[d][0x13]=0x100;}
  if(bad[i].set)regs[dev][bad[i].reg]|=bad[i].bit;else regs[dev][bad[i].reg]&=~bad[i].bit;
  assert(!sw_tfa_healthy());safely_stopped();assert(strstr(logtext,"FAIL")&&!waits);checks++;
 }
 /* Every rawencoding + mode + requested duration + ADCready state.
  * All not-readyADC values are UNKNOWN; sound4 is bounded by actualwatch(). */
 const uint32_t durations[]={0,1,3,4,UINT32_MAX};
 for(unsigned raw=0;raw<512;raw++)for(unsigned ready=0;ready<2;ready++)
 for(unsigned mode=3;mode<=4;mode++)for(unsigned duration=0;duration<5;duration++){
  reset();runtime();boot_mode=mode;boot_seconds=durations[duration];boot_seconds_present=true;
  regs[0][0x16]=raw;regs[0][0x13]=0x100|(ready?0x400:0);
  int c=raw;if(c&0x100)c-=512;
  bool expected=ready?(c>=-40&&c<80):(mode==4||(mode==3&&durations[duration]<=3));
  bool ok=sw_tfa_healthy();assert(ok==expected);assert(!waits);
  if(!ok)safely_stopped();checks++;
 }
 for(unsigned mode=0;mode<=5;mode++){
  reset();runtime();boot_mode=mode;regs[0][0x16]=0x100;regs[0][0x13]=0x100;
  bool ok=sw_tfa_healthy();assert(ok==(mode==3||mode==4));if(!ok)safely_stopped();checks++;
 }
 /* Absent duration uses3, not the mockprovider's unreturnedMAX value. */
 reset();runtime();boot_seconds=UINT32_MAX;regs[0][0x16]=0x100;regs[0][0x13]=0x100;
 assert(sw_tfa_healthy()&&!waits);checks++;
 /* A real I/O failure still terminates; never fallback to unknown. */
 reset();runtime();regs[0][0x16]=0x100;regs[0][0x13]=0x100;temp_samples[0]=1;
 fault_kind=5;fault_stage="health";assert(!sw_tfa_healthy());safely_stopped();checks++;
 fprintf(stdout,"PASS: %u actual NXP activation/protection/cleanup/diagnostic cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-nxp-activation-') as d:
    p = Path(d)
    (p / 'test.c').write_text(header + harness + source + mocks)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-fsanitize=address,undefined', '-g',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True,
                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
