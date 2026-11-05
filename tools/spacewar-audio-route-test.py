#!/usr/bin/env python3
"""Execute real AFE packet builders for Phone (1) and legacy TDM, no phone."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1];s=(root/'drivers/audio/hakios.sound.c').read_text()
a=s.index('#define AFE_PORT_CMD_DEVICE_START');b=s.index('/* ---- the shared memory:',a) if '/* ---- the shared memory:' in s[a:] else s.index('static struct block apps_smmu',a)
code=s[a:b];code=code[:code.rfind('/*')] if code.rstrip().endswith('*/') else code
header=(root/'drivers/audio/hakios.spacewar.audio.h').read_text()
harness=r'''
#include <assert.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#define HAKIOS_DEVICE_SPACEWAR spacewar
#define APR_SVC_AFE 4
#define bzero(p,n) memset((p),0,(n))
static bool spacewar;static unsigned commands,fail_at,groups,clocks,ports,starts,stops,checks;
static bool command(const char*what,unsigned svc,unsigned src,uint32_t op,const void*data,uint32_t n,unsigned response,void*answer){
 (void)what;(void)src;(void)response;(void)answer;assert(svc==4);commands++;
 if(fail_at==commands)return false;
 const uint8_t*p=data;
 if(op==0x100f3){
  assert(n>=28);uint32_t module=sw_audio_u32(p+16),param=sw_audio_u32(p+20);
  if(module==0x1028f){assert(param==0x10290 && n==48);assert(sw_audio_u32(p+28)==1);assert(sw_audio_u32(p+32)==(spacewar?0x100:0x200));assert(sw_audio_u32(p+36)==3072000);assert(sw_audio_u16(p+40)==(spacewar?1:4));clocks++;}
  else {assert(!spacewar && module==0x10254);groups++;}
 }else if(op==0x100ef){
  assert(n>=28);assert(sw_audio_u16(p)==(spacewar?0x1000:0x9000));uint32_t param=sw_audio_u32(p+20);
  if(spacewar){assert(n==48 && param==0x1020d);assert(memcmp(p+28,&sw_audio_i2s_config,20)==0);}ports++;
 }else if(op==0x100e5){assert(n==4 && sw_audio_u16(p)==(spacewar?0x1000:0x9000));starts++;}
 else if(op==0x100e6){assert(n==4 && sw_audio_u16(p)==(spacewar?0x1000:0x9000));stops++;}
 else assert(0);
 return true;
}
static void announce(const char*s){(void)s;}
'''
main=r'''
int main(void){
 for(unsigned device=0;device<2;device++)for(unsigned failure=0;failure<=6;failure++){
  spacewar=device;commands=groups=clocks=ports=starts=stops=0;clock_on=group_on=port_on=false;fail_at=failure;
  bool ok=afe_on();unsigned during=commands;fail_at=0;afe_off();checks++;
  assert(!clock_on && !group_on && !port_on);
  if(spacewar){assert(groups==0);if(!failure || failure>3){assert(ok && starts==1 && stops==1 && clocks==2 && ports==1);}else assert(!ok && during==failure);}
  else {if(!failure){assert(ok && starts==1 && stops==1 && clocks==2 && groups==3 && ports==2);}else assert(!ok);}
 }
 printf("PASS: %u real AFE route/failure cleanup cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-route-') as d:
 p=Path(d);(p/'test.c').write_text(header+harness+code+main)
 subprocess.run([os.environ.get('CC','clang'),'-std=c11','-Werror','-Wno-format','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
