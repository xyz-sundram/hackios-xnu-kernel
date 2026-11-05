#!/usr/bin/env python3
"""Execute actual loader-token and kernel-value service policies together.

The loader owns duplicate/raw-number validation. Kernel PE supplies parsed
values, so this test does not pretend that its API exposes duplicate tokens.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
s = (root / 'xnuloader/loader.c').read_text()
loader = s[s.index('static int\ntouch_token_is('):s.index('static int\nrun_kernel(')]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static bool have_cont;
static uint32_t parsed_cont;
static bool PE_parse_boot_argn(const char *name,void *out,unsigned size){
 if(strcmp(name,"sound_cont")||!have_cont)return false;
 assert(size==4);memcpy(out,&parsed_cont,4);return true;
}
#define SW_SERVICES_KERNEL_POLICY
#include "hakios.spacewar.services.h"
static size_t str_len(const char*s){return strlen(s);}
static int str_eq(const char*a,const char*b){return !strcmp(a,b);}
'''
main = r'''
int main(void){
 unsigned checks=0;
 assert(SW_SERVICES_DRIVERS_COUNT==18&&SW_SERVICES_AUDIO_DRIVERS_COUNT==19);
 assert(sw_services_arg_known("sound_cont",2)&&!sw_services_arg_known("sound_cont",1));checks++;
 const char *base="touch_live=1 bootcpu=7 cpus=7 buttons=1 touchscreen=1 touch_spacewar=1 touch_steps=6 gpu=1 adreno_speed=3 cpufreq=2 spacewar_install=1 spacewar_storage=1 ufs=2 ufs_write=1 var_hfs=ufs_darwin_data";
 const char *good[]={"0","1","00","01"};char line[768];
 for(unsigned i=0;i<4;i++){
  snprintf(line,sizeof line,"%s spacewar_services=2 sound_cont=%s",base,good[i]);
  have_cont=true;parsed_cont=i&1;
  assert(touch_live_profile(line,0,0x700,1)==1&&sw_services_args_valid(2));checks++;
 }
 const struct {const char*token;uint32_t parsed;} bad[]={
  {"sound_cont=2",2},{"sound_cont=4294967295",UINT32_MAX},
  {"sound_cont=18446744073709551615",UINT32_MAX},{"sound_cont=-1",UINT32_MAX},
  {"sound_cont=+1",1},{"sound_cont=1 sound_cont=1",1},
  {"sound_cont=0 sound_cont=1",0},{"sound_cont=1 sound_cont=0",1},
  {"sound_cont",1},{"sound_cont=",0},{"sound_cont=1x",UINT32_MAX}};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){
  snprintf(line,sizeof line,"%s spacewar_services=2 %s",base,bad[i].token);
  have_cont=true;parsed_cont=bad[i].parsed;
  bool loader_ok=touch_live_profile(line,0,0x700,1)==1;
  bool kernel_ok=sw_services_args_valid(2);
  assert(!loader_ok&&!(loader_ok&&kernel_ok));checks++;
 }
 for(unsigned value=0;value<=1;value++){
  have_cont=true;parsed_cont=value;
  snprintf(line,sizeof line,"%s spacewar_services=1 sound_cont=%u",base,value);
  assert(touch_live_profile(line,0,0x700,1)==-1);checks++;
  snprintf(line,sizeof line,"%s sound_cont=%u",base,value);
  assert(touch_live_profile(line,0,0x700,1)==-1);checks++;
 }
 /* Actual kernel value/profile guards, independently of loader rejection.
  * Like existing higher-profile options, parsed0 is inert in profile1. */
 have_cont=true;parsed_cont=1;assert(!sw_services_args_valid(1));checks++;
 parsed_cont=0;assert(sw_services_args_valid(1));checks++;
 const uint32_t rejected[]={2,UINT32_MAX,0x80000000u};
 for(unsigned i=0;i<3;i++){parsed_cont=rejected[i];assert(!sw_services_args_valid(2));checks++;}
 have_cont=false;assert(sw_services_args_valid(2)&&sw_services_args_valid(1));checks++;
 assert(!sw_services_args_valid(0)&&!sw_services_args_valid(3));checks++;
 printf("PASS: %u actual loader/kernel sound_cont policy-chain cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-services-policy-') as d:
    p = Path(d)
    (p / 'test.c').write_text(harness + loader + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-format', '-fsanitize=address,undefined', '-g',
                    '-I' + str(root / 'drivers/input'), str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True,
                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
