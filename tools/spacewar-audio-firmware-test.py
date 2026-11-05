#!/usr/bin/env python3
"""Execute the actual sound-profile firmware wrapper with image/root mocks.

Checks boot-image precedence and owned copies (including the reservation bound),
safe rejection, and stored-system fallback without private firmware or hardware.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'drivers/audio/hakios.adsp.c').read_text()
start = source.index('const uint8_t *\nhakios_adsp_fw_file(')
end = source.index('\nvoid\nhakios_adsp_fw_free(', start)
function = source[start:end]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define ADSP_MEMORY_SIZE 0x02800000u
#define Z_WAITOK 0
static const uint8_t *image;
static uint64_t reported_size;
static bool allocation_fails, root_exists;
static unsigned image_calls,root_calls,allocations,checks;
static size_t allocation_bytes;
static uint8_t root_bytes[]={9,8,7,6};
static void *kalloc_data(size_t n,int flags) {
 (void)flags;allocations++;allocation_bytes=n;
 return allocation_fails?NULL:malloc(n);
}
static const uint8_t *hakios_image_file(const char *dir,const char *name,uint64_t *size,int *disk) {
 assert(!strcmp(dir,"fw")&&!strcmp(name,"tfa98xx.cnt")&&!disk);
 image_calls++;*size=reported_size;return image;
}
static const uint8_t *root_file(const char *name,uint64_t *size) {
 assert(!strcmp(name,"tfa98xx.cnt"));root_calls++;
 if(!root_exists)return NULL;
 uint8_t *p=malloc(sizeof root_bytes);assert(p);memcpy(p,root_bytes,sizeof root_bytes);
 *size=sizeof root_bytes;return p;
}
static int quiet_printf(const char *f,...){(void)f;return 0;}
#define printf quiet_printf
'''
main = r'''
static void reset(void){image=NULL;reported_size=0;allocation_fails=false;root_exists=true;image_calls=root_calls=allocations=0;allocation_bytes=0;}
int main(void) {
 uint64_t n=99;uint8_t bytes[]={1,2,3,4,5};const uint8_t *p;
 reset();image=bytes;reported_size=sizeof bytes;
 p=hakios_adsp_fw_file("tfa98xx.cnt",&n);
 assert(p&&p!=bytes&&n==sizeof bytes&&!memcmp(p,bytes,n));
 assert(image_calls==1&&!root_calls&&allocations==1&&allocation_bytes==n);
 bytes[0]=42;assert(p[0]==1);free((void*)p);checks++;
 reset();p=hakios_adsp_fw_file("tfa98xx.cnt",&n);
 assert(p&&n==sizeof root_bytes&&!memcmp(p,root_bytes,n)&&root_calls==1&&!allocations);free((void*)p);checks++;
 reset();root_exists=false;n=99;p=hakios_adsp_fw_file("tfa98xx.cnt",&n);
 assert(!p&&!n&&image_calls==1&&root_calls==1&&!allocations);checks++;
 /* An explicitly invalid boot file must fail closed, not select another
  * source behind its back or allocate from its untrusted oversized length. */
 const uint64_t bad[]={0,ADSP_MEMORY_SIZE+1ull,UINT64_MAX};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){
  reset();image=bytes;reported_size=bad[i];n=99;
  assert(!hakios_adsp_fw_file("tfa98xx.cnt",&n));
  assert(!n&&!root_calls&&!allocations);checks++;
 }
 reset();image=bytes;reported_size=sizeof bytes;allocation_fails=true;n=99;
 assert(!hakios_adsp_fw_file("tfa98xx.cnt",&n));assert(!n&&!root_calls&&allocations==1);checks++;
 reset();uint8_t *big=malloc(ADSP_MEMORY_SIZE);assert(big);memset(big,0x5a,ADSP_MEMORY_SIZE);
 image=big;reported_size=ADSP_MEMORY_SIZE;p=hakios_adsp_fw_file("tfa98xx.cnt",&n);
 assert(p&&p!=big&&n==ADSP_MEMORY_SIZE&&!memcmp(p,big,n)&&!root_calls);
 free((void*)p);free(big);checks++;
 reset();assert(!hakios_adsp_fw_file(NULL,&n));assert(!hakios_adsp_fw_file("tfa98xx.cnt",NULL));
 assert(!image_calls&&!root_calls&&!allocations);checks+=2;
 fprintf(stdout,"PASS: %u actual audio firmware lookup/ownership/bounds cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-audio-firmware-') as d:
    p = Path(d)
    (p / 'test.c').write_text(harness + function + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True,
                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
