#!/usr/bin/env python3
"""Test actual diagnostic snapshot framing and file cap without Darwin/phone."""
from pathlib import Path
import os, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
s = (root / 'userland/src/audio_health.c').read_text()
code = s[s.index('#define CAPTURE_MAX'):s.index('static void battery_snapshot(')]
harness = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
static const char*provider_text;
static uint32_t provider_size;
static int provider_mode;
int proc_kmsgbuf(void*buffer,uint32_t size){
 assert(size==1024u*1024u);
 if(provider_mode==1){errno=EPERM;return 0;}
 if(provider_mode==2){errno=EIO;return -1;}
 if(provider_mode==3)return (int)size+1;
 assert(provider_size<=size);memcpy(buffer,provider_text,provider_size);return (int)provider_size;
}
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
 /* Exercise actual libproc retrieval, filtering and overlap, not just framing. */
 f=tmpfile();assert(f);fd=fileno(f);total=0;
 provider_text="unrelated private network\nadsp: READY\nsound: port started\n";provider_size=strlen(provider_text)+1;
 assert(snapshot(fd,0)==0);size_t captured=total;assert(captured>0);checks++;
 assert(snapshot(fd,1)==0 && total==captured);checks++;
 provider_text="sound: port started\nspeaker: configured\n";provider_size=strlen(provider_text)+1;
 assert(snapshot(fd,2)==0 && total>captured);checks++;
 assert(lseek(fd,0,SEEK_SET)==0);char output[4096]={0};ssize_t got=read(fd,output,sizeof output-1);assert(got>0);
 assert(strstr(output,"adsp: READY") && strstr(output,"speaker: configured") && !strstr(output,"private network"));checks++;
 assert(lseek(fd,0,SEEK_END)>=0);
 provider_text="adreno: fence timeout\nspacewar-touch: lease ended\nMiniIOMobileFramebuffer: dropped\nMiniIOSurface: source missing\nprivate unrelated app line\n";
 provider_size=strlen(provider_text)+1;assert(snapshot(fd,3)==0);checks++;
 assert(lseek(fd,0,SEEK_SET)==0);memset(output,0,sizeof output);got=read(fd,output,sizeof output-1);assert(got>0);
 assert(strstr(output,"fence timeout") && strstr(output,"lease ended") && strstr(output,"Framebuffer: dropped") &&
        strstr(output,"source missing") && !strstr(output,"unrelated app"));checks++;
 assert(lseek(fd,0,SEEK_END)>=0);
 char embedded[]="adsp: valid\n\0speaker: after NUL must not be parsed\n";
 provider_text=embedded;provider_size=sizeof embedded;assert(snapshot(fd,3)==0);checks++;
 for(int failure=1;failure<=3;failure++){
  provider_mode=failure;captured=total;assert(snapshot(fd,0)==-1 && total>captured);checks++;
  captured=total;assert(snapshot(fd,4)==-1 && total==captured);checks++;
  assert(snapshot(fd,10)==-1 && total>captured);checks++;
 }
 assert(lseek(fd,0,SEEK_SET)==0);memset(output,0,sizeof output);got=read(fd,output,sizeof output-1);assert(got>0);
 assert(!strstr(output,"after NUL") && strstr(output,"proc_kmsgbuf failed") && strstr(output,"errno=1"));checks++;
 fclose(f);free(previous);previous=NULL;previous_size=0;
 /* A maximum-sized, non-NUL-terminated result stays bounded, under ASan. */
 f=tmpfile();assert(f);fd=fileno(f);total=0;provider_mode=0;
 char*full=malloc(SNAPSHOT_MAX);assert(full);memset(full,'x',SNAPSHOT_MAX);memcpy(full,"adsp:",5);
 provider_text=full;provider_size=SNAPSHOT_MAX;assert(snapshot(fd,0)==0 && total>SNAPSHOT_MAX && total<CAPTURE_MAX);checks++;
 free(full);free(previous);previous=NULL;previous_size=0;fclose(f);
 printf("PASS: %u actual audio-health framing/capture-cap cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-audio-health-') as d:
    p = Path(d)
    (p / 'test.c').write_text(harness + code + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Werror', '-Wno-format',
                    '-fsanitize=address,undefined', '-g', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
