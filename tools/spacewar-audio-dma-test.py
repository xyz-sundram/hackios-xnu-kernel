#!/usr/bin/env python3
"""Exercise actual Phone (1) ASM completion/preflight C against event faults.

No phone, firmware, SMMU or amplifier is accessed.
"""
from pathlib import Path
import os, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
s = (root / 'drivers/audio/hakios.sound.c').read_text()
event_code = s[s.index('#define ADM_CMD_DEVICE_OPEN_V5'):s.index('static bool\nstream_on(void)')]
write_code = s[s.index('static bool\nperiod_write(unsigned period)'):s.index('/*\n * The tone, written period')]
play_code = s[s.index('static bool\nplay(uint32_t ms'):s.index('/* ---- sound=4:')]
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define __unused __attribute__((unused))
#define HAKIOS_DEVICE_SPACEWAR 1
#define PERIODS 8
#define PERIOD_FRAMES 960
#define PERIOD_BYTES (PERIOD_FRAMES*4)
#define APR_SVC_ASM 7
#define APR_BASIC_RSP_RESULT 0x110e8u
typedef int32_t SInt32;
static uint64_t buffer_address=0x110000000ull,us;
static unsigned mode,queued,fills,checks,amp_checks;
static uint32_t queued_words[PERIODS][8];
static int say(const char*s,...){(void)s;return 0;}
static int32_t OSIncrementAtomic(volatile int32_t*p){return ++*p;}
static uint32_t OSBitOrAtomic(uint32_t v,volatile uint32_t*p){uint32_t old=*p;*p|=v;return old;}
static uint32_t OSBitAndAtomic(uint32_t v,volatile uint32_t*p){uint32_t old=*p;*p&=v;return old;}
static bool OSCompareAndSwap(uint32_t old,uint32_t n,volatile uint32_t*p){if(*p!=old)return false;*p=n;return true;}
'''
mocks = r'''
static bool hakios_adsp_apr(unsigned svc,unsigned port,uint32_t token,uint32_t op,
 const void*data,uint32_t length,unsigned response,void*answer){
 assert(svc==APR_SVC_ASM && port==STREAM_PORT && op==ASM_DATA_CMD_WRITE_V2);
 assert(length==32 && !response && !answer);unsigned p=token&~WRITE_TOKEN;assert(p<PERIODS);
 if(mode==1)return false;
 assert(!(queued&(1u<<p)));queued|=1u<<p;memcpy(queued_words[p],data,length);return true;
}
static void period_fill(unsigned p,bool silent){assert(p<PERIODS && silent);fills++;}
static uint32_t tone_at;
static uint64_t now_us(void){return us;}
static bool hakios_speaker_healthy(void){amp_checks++;return true;}
static void sleep_ms(uint32_t ms){
 us+=ms*1000u;if(mode==9 && us>=20000)stopping=true;
 if(mode==2 || us%20000)return;
 unsigned ready=queued;
 for(unsigned p=0;p<PERIODS;p++)if(ready&(1u<<p)){
  uint32_t payload[4]={queued_words[p][0],queued_words[p][1],queued_words[p][2],0};
  uint32_t token=WRITE_TOKEN|p;unsigned length=sizeof payload;
  if(mode==3)payload[3]=1;
  if(mode==4)length=12;
  if(mode==5)payload[0]^=4096;
  if(mode==6)payload[2]^=1;
  if(mode==10)token|=0x10;
  queued&=~(1u<<p);
  events(APR_SVC_ASM,STREAM_PORT,mode==8?APR_BASIC_RSP_RESULT:ASM_DATA_EVENT_WRITE_DONE_V2,token,payload,length);
  if(mode==7)events(APR_SVC_ASM,STREAM_PORT,ASM_DATA_EVENT_WRITE_DONE_V2,token,payload,length);
 }
}
'''
main = r'''
static void reset(unsigned failure){
 mode=failure;queued=fills=amp_checks=0;us=0;returned=written_done=write_errors=spacewar_pending=0;
 stopping=false;map_handle=0x1234;
}
int main(void){
 for(unsigned failure=0;failure<=10;failure++){
  reset(failure);bool ok=spacewar_dma_preflight();assert(ok==(failure==0));checks++;
  assert(fills>=PERIODS && !amp_checks && us<2300000);
  if(ok)assert(written_done>=PERIODS && !write_errors && !spacewar_pending && us>=120000);
 }
 /* A stale completion cannot release a period that was never submitted. */
 reset(0);uint32_t valid[4]={(uint32_t)buffer_address,(uint32_t)(buffer_address>>32),map_handle,0};
 events(APR_SVC_ASM,STREAM_PORT,ASM_DATA_EVENT_WRITE_DONE_V2,WRITE_TOKEN,valid,sizeof valid);
 assert(!written_done && !returned && write_errors==1);checks++;
 /* Wrong services and non-write tokens are left to the dispatcher. */
 assert(!events(1,STREAM_PORT,ASM_DATA_EVENT_WRITE_DONE_V2,WRITE_TOKEN,valid,sizeof valid));checks++;
 assert(!events(APR_SVC_ASM,STREAM_PORT,ASM_DATA_EVENT_WRITE_DONE_V2,0,valid,sizeof valid));checks++;
 printf("PASS: %u actual ASM completion/DMA preflight cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-audio-dma-') as d:
    p = Path(d)
    (p / 'test.c').write_text(prelude + event_code + mocks + write_code + play_code + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Werror', '-Wno-format',
                    '-fsanitize=address,undefined', '-g', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
