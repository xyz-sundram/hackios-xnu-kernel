#!/usr/bin/env python3
"""Execute the actual Darwin PCM producer: guard, volume, record and DMA data."""
from pathlib import Path
import os, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
s = (root / 'drivers/audio/hakios.sound.c').read_text()
header = (root / 'drivers/audio/hakios.spacewar.audio.h').read_text()
volume = s[s.index('static void\nspacewar_pcm_limit('):s.index('static void\nperiod_fill(unsigned')]
producer = s[s.index('static void\nperiod_fill_darwin('):s.index('static void\nfeeder_thread(')]
# Cache maintenance is ARM64-only and independent of the period's PCM contents.
producer = producer[:producer.index('\tfor (vm_offset_t line =')] + '}\n'
harness = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define HAKIOS_DEVICE_SPACEWAR 1
#define PERIOD_FRAMES 4
#define RING_FRAMES 8
#define bzero(p,n) memset((p),0,(n))
static uint32_t level=4,below,ring_tail,ring_head,extra_value;
static unsigned fed_frames,silent_frames,guard_periods,recorded_frames,checks;
static bool muted_value;
static int16_t ring[RING_FRAMES*2],data[PERIOD_FRAMES*2],recorded[PERIOD_FRAMES*2];
static uintptr_t buffer;
static void OSMemoryBarrier(void){}
static bool hakios_audio_volume(uint32_t*extra,bool*muted,uint32_t*generation){
 *extra=extra_value;*muted=muted_value;*generation=1;return true;
}
static void hakios_guard_process(int16_t*samples,unsigned n){
 assert(n==PERIOD_FRAMES);for(unsigned i=0;i<n*2;i++)samples[i]*=2;
}
static bool hakios_audio_recording_fed(void){return true;}
static void hakios_audio_record_fed(const int16_t*samples,uint32_t n){
 memcpy(recorded,samples,n*4);recorded_frames=n;
}
'''
main = r'''
int main(void){
 const uint32_t requested[]={240,248,1000,UINT32_MAX},extras[]={0,8,80,480,UINT32_MAX};
 for(unsigned b=0;b<4;b++)for(unsigned e=0;e<5;e++)for(unsigned mute=0;mute<2;mute++)for(unsigned n=0;n<=4;n++){
  buffer=(uintptr_t)data;below=requested[b];extra_value=extras[e];muted_value=mute;
  ring_tail=0;ring_head=n;recorded_frames=0;fed_frames=silent_frames=guard_periods=0;
  memset(recorded,0x55,sizeof recorded);for(unsigned i=0;i<8;i++)ring[i]=i&1?-1000:1000;
  period_fill_darwin(0);assert(recorded_frames==n && fed_frames==n && silent_frames==4-n && guard_periods==1);
  assert(!memcmp(recorded,data,n*4)); /* The recording is exactly the final DMA PCM. */
  for(unsigned i=0;i<8;i++){
   assert(data[i]>=-62 && data[i]<=62);
   if(mute || i>=n*2)assert(data[i]==0);
  }
  if(!mute && b==0 && !e && n)assert(data[0]==62 && data[1]==-62);
  checks++;
 }
 printf("PASS: %u actual Darwin PCM volume/mute/recording cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-audio-pcm-') as d:
    p = Path(d)
    (p / 'test.c').write_text(header + harness + volume + producer + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Werror', '-Wno-format',
                    '-fsanitize=address,undefined', '-g', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
