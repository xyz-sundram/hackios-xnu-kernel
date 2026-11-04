#!/usr/bin/env python3
"""Compile the real ADSP firmware validation routine; no phone/MMIO calls.

Synthetic ELF32 MDTs model Phone (1)'s40MiB reservation including a final BSS
segment. Tests reject CDSP overlap, load-segment overlap, truncation and files
whose real size differs from the signed segment header. No private data needed.
"""
from pathlib import Path
import os,re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
s=(root/'drivers/audio/hakios.adsp.c').read_text()
start=s.index('static bool\nstep_firmware(void)');end=s.index('/* ---- step 3:',start)
function=s[start:end]
harness=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MDT_HASH(flags) (((flags)>>24&7)==2)
#define MAX_SEGMENTS 48
#define PAGE_SIZE 16384
#define ADSP_MEMORY 0x86700000u
#define ADSP_MEMORY_SIZE (spacewar?0x02800000u:0x04000000u)
struct elf_segment {uint32_t type,offset,vaddr,paddr,file_size,memory_size,flags,align;};
static struct {const uint8_t *header;uint64_t header_size;struct {uint32_t at,file_size,memory_size;const uint8_t *file;}segment[MAX_SEGMENTS];unsigned segments;uint32_t size;}image;
static bool spacewar=true,from_root;static unsigned step,scenario,checks;
static unsigned allocations;static uint8_t mdt[256];static size_t mdt_size=212;
static int quiet_printf(const char *f,...){(void)f;return 0;}
#define printf quiet_printf
static void announce(const char*s){(void)s;}
static void image_forget(void){if(image.header){free((void*)image.header);allocations--;}for(unsigned i=0;i<image.segments;i++)if(image.segment[i].file){free((void*)image.segment[i].file);allocations--;}memset(&image,0,sizeof image);}
static const uint8_t *image_file(const char*name,uint64_t*size){
 size_t n=4;if(strcmp(name,"adsp.mdt")==0)n=mdt_size;
 else if(scenario==7 && strcmp(name,"adsp.b02")==0)n=3;
 uint8_t*p=malloc(n?n:1);assert(p);allocations++;memset(p,0,n);
 if(strcmp(name,"adsp.mdt")==0)memcpy(p,mdt,n);*size=n;return p;
}
'''
main=r'''
static void put32(unsigned at,uint32_t v){memcpy(mdt+at,&v,4);}
static void make(unsigned which){
 memset(mdt,0,sizeof mdt);memcpy(mdt,"\177ELF\1",5);mdt[44]=5;mdt_size=212;
 struct elf_segment ph[5]={
 {.type=0,.file_size=0},
 {.type=1,.file_size=0,.flags=2u<<24},
 {.type=1,.paddr=ADSP_MEMORY,.file_size=4,.memory_size=0x1000},
 {.type=1,.paddr=ADSP_MEMORY+0x1000,.file_size=4,.memory_size=0x1000},
 {.type=1,.paddr=ADSP_MEMORY+0x2000,.file_size=0,.memory_size=0x02800000-0x2000}};
 memcpy(mdt+52,ph,sizeof ph);scenario=which;
 switch(which){
 case 1:put32(52+4*32+20,0x02800001-0x2000);break;
 case 2:put32(52+3*32+12,ADSP_MEMORY+0x800);break;
 case 3:put32(52+2*32+12,ADSP_MEMORY-1);break;
 case 4:put32(52+2*32+20,3);break;
 case 5:mdt_size=4;break;
 case 6:mdt[44]=49;break;
 case 8:put32(52+4*32+20,0xfffffff0);break;
 case 9:mdt_size=0;break;
 case 10:mdt[0]=0;break;
 case 11:mdt_size=200;break;
 }
}
int main(void){
 for(unsigned t=0;t<=11;t++){
  make(t);bool okay=step_firmware();checks++;
  assert(okay==(t==0));if(okay)assert(image.size==0x02800000);
  image_forget();assert(!allocations);
 }
 /* Legacy non-Spacewar reservation remains64MiB, while the exact same image
  * crossing40MiB is rejected on Phone(1) before PAS/MMIO can happen. */
 make(1);spacewar=false;assert(step_firmware());image_forget();checks++;
 assert(!allocations);fprintf(stdout,"PASS: %u real ADSP firmware validation cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-adsp-') as d:
 p=Path(d);(p/'test.c').write_text(harness+function+main)
 compiler=os.environ.get('CC','clang')
 subprocess.run([compiler,'-std=c11','-Werror','-Wno-format','-fsanitize=address,undefined','-g',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
