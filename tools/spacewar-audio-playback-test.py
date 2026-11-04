#!/usr/bin/env python3
"""Execute actual Darwin sink, watcher lease and boot-duration clamp; no device.

The virtual clock drives PCM, mute/silence, slow start and terminal-fault cases.
NXP register protections and PCM samples have separate actual-C tests.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'drivers/audio/hakios.sound.c').read_text()
sink = source[source.index('static uint32_t\ndarwin_sink('):source.index('static uint32_t guard_periods;')]
watch = source[source.index('static void\nwatch(void)'):source.index('/* ---- the whole', source.index('static void\nwatch(void)'))]
up = source[source.index('void\nhakios_sound_dsp_up(void)'):source.index('void\nhakios_sound_dsp_down(void)')]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define HAKIOS_DEVICE_SPACEWAR spacewar
#define HAKIOS_GUARD_MAX_GAIN 48
#define QUIET_BEFORE_OFF_US 3000000ull
#define PERIODS 8
#define PERIOD_FRAMES 960
#define RING_FRAMES 32768
#define SILENCE 8
#define KERN_SUCCESS 0
typedef int thread_t;
static bool spacewar=true,stopping,watcher_runs,guard_on,running;
static uint32_t level=4,seconds=3,below=240,guard_as=240,gain,pause_ms,sound_debug;
static uint64_t last_sound_us,darwin_first_nonzero_us,darwin_frames,fed_frames;
static uint32_t darwin_peak,ring_head,ring_tail;
static int16_t ring[RING_FRAMES*2];
static uint64_t clock_us=1,end_us,start_latency,volume_latency,first_start_us,last_stop_us;
static unsigned scenario,starts,stops,health_checks,guarded_ticks,checks,threads;
static bool amp_on,muted_previous;
static uint32_t generation;
static char logtext[32000];static size_t used;
static uint32_t boot_level,boot_seconds,boot_cont;
static bool boot_cont_present;
static bool PE_parse_boot_argn(const char*name,void*out,int length){
 assert(length==4);
 if(!strcmp(name,"sound")){memcpy(out,&boot_level,4);return true;}
 if(!strcmp(name,"sound_secs")){memcpy(out,&boot_seconds,4);return true;}
 if(!strcmp(name,"sound_cont")&&boot_cont_present){memcpy(out,&boot_cont,4);return true;}
 return false;
}
static uint64_t now_us(void){return clock_us;}
static int say(const char*f,...){va_list ap;va_start(ap,f);int n=vsnprintf(logtext+used,sizeof logtext-used,f,ap);va_end(ap);assert(n>=0&&(size_t)n<sizeof logtext-used);used+=(size_t)n;return n;}
static void say_when(const char*s){(void)s;}
static void OSMemoryBarrier(void){}
static void guard_level(uint32_t n){(void)n;}
static void guard_off(bool unheard){(void)unheard;guard_on=false;}
static uint32_t below_with(uint32_t extra){return below+extra;}
static void hakios_guard_monitor(uint32_t n){(void)n;}
static void hakios_guard_configure(bool on,uint32_t n){assert(on);(void)n;}
static void sound_thread(void*p,int r){(void)p;(void)r;}
static int kernel_thread_start(void(*f)(void*,int),void*p,thread_t*t){assert(f==sound_thread&&!p);*t=1;threads++;return KERN_SUCCESS;}
static void thread_deallocate(thread_t t){assert(t==1);}
static void hakios_speaker_stop(void){amp_on=false;stops++;last_stop_us=clock_us;}
static bool hakios_speaker_start(void){starts++;if(starts==1)first_start_us=clock_us;amp_on=true;clock_us+=start_latency;return true;}
static bool hakios_speaker_volume(uint32_t b,unsigned ms){(void)b;(void)ms;clock_us+=volume_latency;return scenario!=7;}
static bool hakios_speaker_healthy(void){health_checks++;if(scenario==6&&clock_us>=500001){hakios_speaker_stop();return false;}return true;}
static uint32_t hakios_speaker_monitor(bool more){(void)more;return 0;}
static bool hakios_audio_volume(uint32_t*extra,bool*muted,uint32_t*gen){
 bool m=(scenario==1&&clock_us<1000001)||(scenario==2&&clock_us>=1000001&&clock_us<2000001);
 if(m!=muted_previous){generation++;muted_previous=m;}
 *extra=0;*muted=m;*gen=generation;return true;
}
static void sleep_ms(unsigned ms){
 if(guard_on){assert(amp_on);guarded_ticks++;}
 clock_us+=(uint64_t)ms*1000;fed_frames+=240;darwin_frames+=240;
 bool silence=(scenario==3&&clock_us>=1000001&&clock_us<4000001)||
              (scenario==8&&clock_us>=1000001&&clock_us<4500001);
 if(!silence)last_sound_us=clock_us;
 if(clock_us>=end_us)stopping=true;
}
static void reset(unsigned which,bool phone,uint32_t duration){
 scenario=which;spacewar=phone;seconds=duration;level=4;clock_us=1;end_us=5000001;start_latency=which==4?4000000:0;
 volume_latency=0;first_start_us=last_stop_us=0;starts=stops=health_checks=guarded_ticks=0;
 stopping=guard_on=amp_on=muted_previous=false;watcher_runs=true;generation=1;
 last_sound_us=darwin_first_nonzero_us=1;darwin_frames=fed_frames=100;darwin_peak=100;
 ring_head=ring_tail=0;used=0;logtext[0]=0;
 boot_cont=0;boot_cont_present=false;
}
'''
main = r'''
int main(void){
 reset(0,true,3);watch();assert(starts==1&&!amp_on&&last_stop_us==3000001&&!watcher_runs);
 assert(clock_us==end_us&&fed_frames>200000); /* lease expiry keeps draining */
 assert(health_checks>0&&strstr(logtext,"lease expired")&&strstr(logtext,"first nonzero Darwin PCM"));checks++;
 reset(1,true,3);watch();assert(starts==1&&first_start_us==1000001&&last_stop_us==4000001&&!amp_on);checks++;
 reset(2,true,3);watch();assert(starts==1&&!amp_on&&last_stop_us==3000001);checks++;
 reset(3,true,3);watch();assert(starts==1&&!amp_on&&last_stop_us==3000001);checks++;
 reset(4,true,3);watch();assert(starts==1&&!amp_on&&!guarded_ticks&&last_stop_us==4000001);
 assert(strstr(logtext,"expired during start/readback"));checks++;
 reset(0,true,UINT32_MAX);watch();assert(starts==1&&!amp_on&&last_stop_us==3000001);checks++;
 reset(0,true,0);watch();assert(!starts&&!amp_on&&!guarded_ticks);checks++;
 reset(6,true,3);watch();assert(starts==1&&!amp_on&&strstr(logtext,"lease expired"));checks++;
 reset(7,true,3);watch();assert(starts==1&&!amp_on&&!guarded_ticks);checks++;
 reset(0,true,1);watch();assert(starts==1&&!amp_on&&last_stop_us==1000001);checks++;
 reset(0,true,1);start_latency=1100000;watch();assert(!amp_on&&!guarded_ticks&&last_stop_us==1100001);checks++;
 reset(0,true,1);volume_latency=1100000;watch();assert(!amp_on&&!guarded_ticks&&last_stop_us==1100001);checks++;
 /* Generic legacy watcher keeps its previous mute/unmute restart behavior. */
 reset(2,false,3);watch();assert(starts==2&&amp_on&&!strstr(logtext,"lease"));checks++;
 /* Actual sink: truezero never claimsPCM; sub-silence nonzero logs once,
  * and the regular sound timestamp still needs amplitude above8. */
 reset(0,true,3);darwin_first_nonzero_us=last_sound_us=darwin_frames=darwin_peak=0;clock_us=100;
 int16_t zero[2]={0,0},tiny[2]={1,-1},sound[2]={100,-100};
 assert(darwin_sink(zero,1)==1&&!darwin_first_nonzero_us&&!last_sound_us);
 assert(darwin_sink(tiny,1)==1&&darwin_first_nonzero_us==100&&!last_sound_us);
 clock_us=200;assert(darwin_sink(sound,1)==1&&darwin_first_nonzero_us==100&&last_sound_us==200&&darwin_frames==3);checks++;
 ring_head=RING_FRAMES;ring_tail=0;darwin_first_nonzero_us=last_sound_us=0;
 assert(!darwin_sink(sound,1)&&!darwin_first_nonzero_us&&!last_sound_us);checks++;
 /* Execute the real kernel entrypoint's clamp, not a second clamp model. */
 const uint32_t requested[]={0,1,3,4,UINT32_MAX};
 for(unsigned i=0;i<5;i++){
  spacewar=true;boot_level=4;boot_seconds=requested[i];seconds=3;threads=0;
  hakios_sound_dsp_up();assert(threads==1&&seconds==(requested[i]>3?3:requested[i]));checks++;
 }
 spacewar=false;boot_level=4;boot_seconds=100;seconds=3;hakios_sound_dsp_up();assert(seconds==100);checks++;
 /* Only explicit sound_cont=1 opts Phone1 into continuous playback. */
 reset(0,true,3);boot_cont_present=true;boot_cont=1;end_us=20000001;watch();
 assert(starts==1&&amp_on&&!stops&&health_checks>=95&&!strstr(logtext,"lease expired")&&strstr(logtext,"sound_cont=1"));checks++;
 reset(1,true,3);boot_cont_present=true;boot_cont=1;watch();
 assert(starts==1&&first_start_us==1000001&&amp_on&&!stops);checks++;
 reset(2,true,3);boot_cont_present=true;boot_cont=1;watch();
 assert(starts==2&&amp_on&&stops==1&&!strstr(logtext,"lease expired"));checks++;
 reset(8,true,3);boot_cont_present=true;boot_cont=1;end_us=8000001;watch();
 assert(starts==2&&amp_on&&stops==1&&last_stop_us==4000001);checks++;
 reset(6,true,3);boot_cont_present=true;boot_cont=1;watch();
 assert(starts==1&&!amp_on&&stops==1&&!strstr(logtext,"lease expired"));checks++;
 reset(7,true,3);boot_cont_present=true;boot_cont=1;watch();
 assert(starts==1&&!amp_on&&!guarded_ticks);checks++;
 const uint32_t invalid_cont[]={0,2,UINT32_MAX};
 for(unsigned i=0;i<3;i++){
  reset(0,true,3);boot_cont_present=true;boot_cont=invalid_cont[i];watch();
  assert(starts==1&&!amp_on&&last_stop_us==3000001&&strstr(logtext,"lease expired"));checks++;
 }
 reset(2,false,3);boot_cont_present=true;boot_cont=1;watch();
 assert(starts==2&&amp_on&&!strstr(logtext,"sound_cont=1")&&!strstr(logtext,"lease"));checks++;
 spacewar=true;boot_cont_present=true;boot_cont=1;boot_level=3;boot_seconds=UINT32_MAX;seconds=3;
 hakios_sound_dsp_up();assert(seconds==3);checks++;
 fprintf(stdout,"PASS: %u actual Darwin sink/watcher lease/clamp cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-playback-') as d:
    p = Path(d)
    (p / 'test.c').write_text(harness + sink + watch + up + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-format', '-fsanitize=address,undefined', '-g', str(p / 'test.c'),
                    '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True,
                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
