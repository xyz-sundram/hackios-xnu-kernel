#!/usr/bin/env python3
"""Actual NXP parser/backend tests with synthetic container and simulated I2C.

No private firmware is needed. A local private ROM container, if present, is
validated in addition without embedding it in this test or its output.
"""
from pathlib import Path
import os,struct,subprocess,tempfile,zlib
root=Path(__file__).resolve().parents[1]
def fixture():
 b=bytearray(3745);b[:6]=b'PM3_01';struct.pack_into('<I',b,6,len(b));struct.pack_into('<H',b,40,2)
 op_at=1000
 for d in range(2):
  at=64+256*d;profile=at+128;name=at+200;b[name:name+8]=b'speaker\0'
  struct.pack_into('<I',b,46+4*d,at);b[at]=7;b[at+2]=0x34+d
  ops=[(0x20c3,2),(0x2144,31),(0x2224,31),(0x2013,1),(0x2603,d),(0x0203,8)]
  for i,(field,value) in enumerate(ops):
   struct.pack_into('<I',b,at+12+4*i,op_at|(16<<24));struct.pack_into('<HH',b,op_at,value,field);op_at+=4
  struct.pack_into('<I',b,at+12+4*6,profile|(1<<24))
  struct.pack_into('<I',b,profile,1|(0x1234<<16));struct.pack_into('<I',b,profile+4,name|(3<<24))
 # Solve32 bits of unused container padding for the fixed known-ROM CRC. This
 # fixture is synthetic metadata; no vendor profile bytes are copied here.
 pos=len(b)-4;base=zlib.crc32(b[14:]);target=0x8e4ae995^base;columns=[]
 for bit in range(32):
  t=bytearray(b);t[pos+bit//8]^=1<<(bit%8);columns.append(zlib.crc32(t[14:])^base)
 basis={}
 for bit,value in enumerate(columns):
  mask=1<<bit
  while value:
   pivot=value.bit_length()-1
   if pivot not in basis:basis[pivot]=(value,mask);break
   q,m=basis[pivot];value^=q;mask^=m
 mask=0
 while target:
  pivot=target.bit_length()-1;q,m=basis[pivot];target^=q;mask^=m
 for bit in range(32):
  if mask>>bit&1:b[pos+bit//8]^=1<<(bit%8)
 struct.pack_into('<I',b,10,0x8e4ae995);assert zlib.crc32(b[14:])==0x8e4ae995
 return bytes(b)
header=(root/'drivers/audio/hakios.spacewar.audio.h').read_text()
backend=(root/'drivers/audio/hakios.spacewar.speaker.inc').read_text().replace('#include "hakios.spacewar.audio.h"','')
harness=r'''
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#define GPIO_CFG(n) (0x0f100000+0x1000*(n))
#define GPIO_IO(n) (GPIO_CFG(n)+4)
#define CFG_OUTPUT 512
#define TLMM_PHYS 0x0f100000
#define TLMM_SIZE 0x100000
static uintptr_t tlmm=TLMM_PHYS;static bool leaving;
static uint16_t r[2][256];static uint8_t tx[8];static unsigned ntx,writes,gpio_writes,fail_at,checks;static bool fail_enable_readback,mismatch;
static int say(const char*f,...){(void)f;return 0;}
static uintptr_t ml_io_map(uintptr_t a,unsigned n){(void)n;return a;}
static uint32_t rd(uintptr_t a){assert(a==GPIO_CFG(101)||a==GPIO_IO(101));return 0;}
static void wr(uintptr_t a,uint32_t v){(void)v;assert(a==GPIO_CFG(101)||a==GPIO_IO(101));gpio_writes++;}
static void wait_ms(unsigned ms){(void)ms;}
static bool bus_take(void){return !fail_at || writes+1!=fail_at;}
static void bus_give_back(void){}
static void i2c_start(void){ntx=0;}
static bool i2c_write(uint8_t v){assert(ntx<sizeof tx);tx[ntx++]=v;return true;}
static void i2c_stop(void){if(ntx==4){assert(tx[0]==0x68||tx[0]==0x6a);unsigned d=(tx[0]>>1)-0x34;r[d][tx[1]]=(uint16_t)tx[2]<<8|tx[3];if(tx[1]==0){r[d][0]&=~2u;if(fail_enable_readback && (r[d][0]&8)){mismatch=true;fail_enable_readback=false;}}writes++;}}
static bool register_read(uint8_t a,uint8_t reg,uint16_t*v){assert(a==0x34||a==0x35);*v=r[a-0x34][reg];if(reg==0 && mismatch){*v^=1;mismatch=false;}return true;}
'''
main=r'''
static void reset(void){
 memset(r,0,sizeof r);memset(sw_tfa_identified,0,sizeof sw_tfa_identified);
 sw_tfa_prepared=sw_tfa_started=sw_tfa_fault=sw_tfa_reset_saved=leaving=fail_enable_readback=mismatch=false;writes=gpio_writes=fail_at=0;
 for(unsigned d=0;d<2;d++){r[d][3]=0x0b73;r[d][0]=0x4201;r[d][0x10]=0x40;r[d][0x16]=25;r[d][0xfb]=0x1234;}
}
static void store16(uint8_t*p,uint16_t v){p[0]=v;p[1]=v>>8;}
static void store32(uint8_t*p,uint32_t v){store16(p,v);store16(p+2,v>>16);}
static void malformed(const uint8_t*valid,unsigned offset,uint32_t value,unsigned bytes){
 uint8_t changed[3745];memcpy(changed,valid,sizeof changed);
 if(bytes==1)changed[offset]=value;else if(bytes==2)store16(changed+offset,value);else store32(changed+offset,value);
 uint32_t crc=sw_audio_crc32(changed+14,sizeof changed-14);store32(changed+10,crc);
 struct sw_tfa_profile parsed[2];assert(!sw_tfa_parse(changed,sizeof changed,crc,parsed));checks++;
}
int main(int argc,char**argv){
 assert(argc>=2);FILE*f=fopen(argv[1],"rb");assert(f);uint8_t b[3745];assert(fread(b,1,sizeof b,f)==sizeof b);fclose(f);
 struct sw_tfa_profile profiles[2];assert(sw_tfa_parse(b,sizeof b,SW_AUDIO_CONTAINER_CRC,profiles));checks++;
 for(unsigned n=0;n<sizeof b;n++){assert(!sw_tfa_parse(b,n,SW_AUDIO_CONTAINER_CRC,profiles));checks++;}
 /* Corrupt every byte, independently keeping expectedCRC pinned. */
 for(unsigned i=0;i<sizeof b;i++){b[i]^=1;assert(!sw_tfa_parse(b,sizeof b,SW_AUDIO_CONTAINER_CRC,profiles));b[i]^=1;checks++;}
 /* CRC-valid malformed metadata reaches bounds, identity and register guards.
  * The production caller still pins the known-ROM CRC independently. */
 malformed(b,40,1,2);malformed(b,46,0x00fffffc,4);malformed(b,46,0x01000040,4);
 malformed(b,64,49,1);malformed(b,66,0x35,1);malformed(b,76,0x100ffffe,4);
 malformed(b,1002,0xa30f,2);malformed(b,1000,3,2);malformed(b,1018,0,2);
 malformed(b,1020,1,2);malformed(b,1022,0x2603,2);
 malformed(b,100,0x010ffffe,4);malformed(b,192,49,1);malformed(b,194,0,2);
 malformed(b,196,0x030fffff,4);malformed(b,264,'x',1);
 reset();assert(sw_tfa_prepare(b,sizeof b));checks++;assert(sw_tfa_prepared && !sw_tfa_started);
 assert((r[0][0]&0x19)==1 && (r[1][0]&0x19)==1 && r[0][0xa1]==0 && r[1][0xa1]==0);
 assert(sw_tfa_start());checks++;assert((r[0][0]&0x19)==0x18 && (r[1][0]&0x19)==0x18);
 assert(sw_tfa_healthy());checks++;assert(sw_tfa_volume(240) && !sw_tfa_volume(0));
 r[1][0x16]=80;assert(!sw_tfa_healthy());checks++;assert((r[0][0]&0x19)==1 && (r[1][0]&0x19)==1);
 reset();assert(sw_tfa_prepare(b,sizeof b));fail_enable_readback=true;assert(!sw_tfa_start());checks++;
 assert(sw_tfa_fault && !sw_tfa_started && (r[0][0]&0x19)==1 && (r[1][0]&0x19)==1);

 reset();r[1][3]=0x0a73;assert(!sw_tfa_prepare(b,sizeof b));checks++;assert(!gpio_writes && !writes);
 reset();b[100]^=1;assert(!sw_tfa_prepare(b,sizeof b));checks++;assert(!gpio_writes && !writes);b[100]^=1;
 reset();assert(sw_tfa_prepare(b,sizeof b));r[0][0x10]=0;assert(!sw_tfa_start());checks++;assert(!sw_tfa_started && (r[0][0]&0x19)==1 && (r[1][0]&0x19)==1);
 for(unsigned reg=0;reg<256;reg++){
  assert(!sw_tfa_write_allowed(reg,0x18,true));checks++;
 }
 /* No whitelist path contains MTP manual-copy or calibration command. */
 for(unsigned v=0;v<65536;v++){assert(!sw_tfa_write_allowed(0xa3,v,false));checks++;}
 for(unsigned v=0;v<65536;v++){
  int16_t sample=(int16_t)v;sw_audio_limit_pcm(&sample,1);assert(sample>=-1024 && sample<=1023);checks++;
 }
 uint32_t last=1u<<31;
 for(unsigned extra=0;extra<=720;extra++){
  uint32_t g=sw_audio_gain_q31(240,extra);assert(g<=last);last=g;
  assert(g==sw_audio_gain_q31(240+extra,0));checks++;
  for(int v=-32768;v<=32767;v+=257){
   int16_t sample=v;sw_audio_limit_pcm_at(&sample,1,g);
   assert(sample>=-1024 && sample<=1023 && abs(sample)<=abs(v/32));checks++;
  }
 }
 assert(sw_audio_gain_q31(0,0)==(1u<<31));checks++;
 assert(sw_audio_gain_q31(UINT32_MAX,UINT32_MAX)==sw_audio_gain_q31(720,0));checks++;
 for(unsigned v=0;v<65536;v++){
  int16_t sample=(int16_t)v;sw_audio_limit_pcm_at(&sample,1,UINT32_MAX);
  assert(sample==(int16_t)v/32);sw_audio_limit_pcm_at(&sample,1,0);assert(sample==0);checks++;
 }
 if(argc==3){FILE*f=fopen(argv[2],"rb");assert(f);assert(fread(b,1,sizeof b,f)==sizeof b);fclose(f);assert(sw_tfa_parse(b,sizeof b,SW_AUDIO_CONTAINER_CRC,profiles));checks++;}
 printf("PASS: %u actual NXP profile/backend/PCM cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-nxp-') as d:
 p=Path(d);(p/'fixture.cnt').write_bytes(fixture());(p/'test.c').write_text(header+harness+backend+main)
 subprocess.run([os.environ.get('CC','clang'),'-std=c11','-Werror','-Wno-format','-fsanitize=address,undefined','-g',str(p/'test.c'),'-o',str(p/'test')],check=True)
 private=root/'build/spacewar-port-inventory/rom-files/firmware/tfa98xx.cnt'
 args=[str(p/'test'),str(p/'fixture.cnt')]+([str(private)] if private.exists() else [])
 subprocess.run(args,check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
