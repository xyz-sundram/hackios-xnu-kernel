#!/usr/bin/env python3
"""Validate actual Spacewar PMIC-link lifetime policy independently of APR."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1];s=(root/'drivers/audio/hakios.adsp.c').read_text()
a=s.index('static bool\nspacewar_battery_link_ready');b=s.index('static void\nsteps_over',a)
h=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define STEPS 11
#define CHANNEL_PMIC 0
#define HAKIOS_DEVICE_SPACEWAR spacewar
static bool spacewar,keep,dsp_started,link_runs,battery;
static unsigned last_step,wanted;
static struct {bool agreed,broken;struct{bool open;}channel[1];}glink;
static void PE_parse_boot_argn(const char*n,uint32_t*v,unsigned s){(void)n;(void)s;*v=wanted;}
static bool option(const char*s){(void)s;return battery;}
'''
main=r'''
int main(void){unsigned cases=0;
 for(unsigned bit=0;bit<1024;bit++)for(unsigned done=0;done<=11;done++){
 spacewar=bit&1;keep=bit&2;dsp_started=bit&4;link_runs=bit&8;
 glink.agreed=bit&16;glink.broken=bit&32;glink.channel[0].open=bit&64;
 wanted=(bit&128)?1:0;battery=bit&256;last_step=(bit&512)?11:9;
 bool expected=spacewar&&keep&&dsp_started&&link_runs&&glink.agreed&&!glink.broken&&glink.channel[0].open&&wanted&&!battery&&last_step==11&&done>=8;
 assert(spacewar_battery_link_ready(done)==expected);cases++;
 }
 printf("PASS: %u actual PMIC GLINK lifetime policy cases\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-adsp-link-') as d:
 p=Path(d);(p/'test.c').write_text(h+s[a:b]+main)
 subprocess.run([os.environ.get('CC','clang'),'-std=c11','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
