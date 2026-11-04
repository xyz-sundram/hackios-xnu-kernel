#!/usr/bin/env python3
"""Test actual diagnostic snapshot framing and file cap without Darwin/phone."""
from pathlib import Path
import os, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
s = (root / 'userland/src/audio_health.c').read_text()
code = s[s.index('#define CAPTURE_MAX'):s.index('static int snapshot(')]
harness = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
'''
main = r'''
static unsigned checks;
static void check(const char*a,const char*b){
 size_t an=strlen(a),bn=strlen(b),expected=0;
 for(size_t n=1;n<=an && n<=bn;n++)if(b[n-1]=='\n' && (an==n || a[an-n-1]=='\n') && !memcmp(a+an-n,b,n))expected=n;
 assert(matching_prefix(a,an,b,bn)==expected);checks++;
}
static void word(char*out,unsigned code,unsigned n){
 for(unsigned i=0;i<n;i++){out[i]="ab\n"[code%3];code/=3;}out[n]=0;
}
int main(void){
 char a[8],b[8];
 for(unsigned an=0,ac=1;an<=5;an++,ac*=3)for(unsigned i=0;i<ac;i++){
  word(a,i,an);
  for(unsigned bn=0,bc=1;bn<=5;bn++,bc*=3)for(unsigned j=0;j<bc;j++){word(b,j,bn);check(a,b);}
 }
 check("adsp: before\nadsp: ready\n","adsp: ready\nsound: clock\n");
 check("prefix adsp: ready\n","adsp: ready\n");
 char*large=malloc(SNAPSHOT_MAX);assert(large);memset(large,'a',SNAPSHOT_MAX);large[SNAPSHOT_MAX-1]='\n';
 assert(matching_prefix(large,SNAPSHOT_MAX,large,SNAPSHOT_MAX)==SNAPSHOT_MAX);checks++;free(large);
 FILE*f=tmpfile();assert(f);int fd=fileno(f);char bytes[4096];memset(bytes,0,sizeof bytes);
 while(total<CAPTURE_MAX)assert(write_all(fd,bytes,sizeof bytes)==0);
 assert(write_all(fd,bytes,1)==-1);struct stat st;assert(!fstat(fd,&st) && st.st_size==CAPTURE_MAX);checks++;fclose(f);
 printf("PASS: %u actual audio-health framing/capture-cap cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-audio-health-') as d:
    p = Path(d)
    (p / 'test.c').write_text(harness + code + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Werror', '-Wno-format',
                    '-fsanitize=address,undefined', '-g', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
