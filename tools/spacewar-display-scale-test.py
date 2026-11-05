#!/usr/bin/env python3
"""Run the real cache-retirement code and display-coherence guards on the host."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "userland/src/display_info.c").read_text()


def function(signature):
    start = source.index(signature)
    return source[start:source.index("\n}", start) + 2]


backup = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
''' + function("static bool\nbackup_scale_cache(") + r'''
int main(void) {
    char path[]="cache.XXXXXX",saved[256];
    int fd=mkstemp(path);assert(fd>=0);
    assert(write(fd,"retained",8)==8);assert(fchmod(fd,0640)==0);close(fd);
    assert(backup_scale_cache(path,saved,sizeof(saved)));
    assert(access(path,F_OK)!=0);
    struct stat st;assert(stat(saved,&st)==0 && st.st_size==8 && (st.st_mode&0777)==0640);
    char bytes[8];FILE *f=fopen(saved,"rb");assert(f && fread(bytes,1,8,f)==8);fclose(f);
    assert(!memcmp(bytes,"retained",8));
    assert(symlink(saved,path)==0);
    char other[256];assert(!backup_scale_cache(path,other,sizeof(other)));
    assert(lstat(path,&st)==0 && S_ISLNK(st.st_mode));unlink(path);
    char big[]="big.XXXXXX";
    fd=mkstemp(big);assert(fd>=0);
    assert(ftruncate(fd,3*1024*1024)==0);close(fd);
    assert(!backup_scale_cache(big,other,sizeof(other)));
    assert(stat(big,&st)==0 && st.st_size==3*1024*1024);unlink(big);
    assert(!backup_scale_cache("missing",other,sizeof(other)));
    assert(!backup_scale_cache(saved,other,4));assert(access(saved,F_OK)==0);
    assert(mkdir("directory",0700)==0);
    assert(!backup_scale_cache("directory",other,sizeof(other)));
    assert(rmdir("directory")==0);unlink(saved);
}
'''

policy = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static const char cache_path[]="unused";
#define CFSTR(s) (s)
static const char *args;
static unsigned chosen,product,first,second,queries,backups;
static bool geometry,backable;
static int sysctlbyname(const char *key,void *out,size_t *size,void *in,size_t length) {
    (void)key;(void)in;(void)length;assert(strlen(args)<*size);strcpy(out,args);return 0;
}
static unsigned tree_word(const char *path,const char *key) {
    (void)key;return strstr(path,"chosen")?chosen:product;
}
static bool canvas_matches(unsigned scale) {(void)scale;return geometry;}
static unsigned gestalt_scale(void) {return queries++==0?first:second;}
static bool backup_scale_cache(const char *path,char *out,size_t size) {
    (void)path;assert(size>10);strcpy(out,"backup");backups++;return backable;
}
''' + function("static bool\nrefresh_scale_cache(") + r'''
int main(void) {
    for(unsigned test=0;test<12;test++) {
        args="touch_spacewar=1";chosen=product=2;geometry=backable=true;
        first=3;second=2;queries=backups=0;
        switch(test) {
          case 1:first=2;break;
          case 2:chosen=product=3;first=2;second=3;break;
          case 3:chosen=product=1;break;
          case 4:product=3;break;
          case 5:geometry=false;break;
          case 6:first=0;break;
          case 7:args="other_touch_spacewar=1";break;
          case 8:backable=false;break;
          case 9:second=3;break;
          case 10:second=4;break; // helper regenerates the cache after launchd
          case 11:first=4;break; // first boot has no saved scale answer
        }
        bool ok=refresh_scale_cache();
        assert(ok==(test<=2 || test==10 || test==11));
        assert(backups==((test==0 || test==2 || test==8 || test==9 || test==10)?1u:0u));
        if(test>=3 && test<=5)assert(queries==0);
    }
}
'''

with tempfile.TemporaryDirectory(prefix="spacewar-display-scale-") as temp:
    directory = Path(temp)
    for name, program in (("backup", backup), ("policy", policy)):
        path = directory / (name + ".c")
        path.write_text(program)
        exe = directory / name
        subprocess.run(["cc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", str(path), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], cwd=directory, check=True,
                       env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
print("PASS: production cache backups preserve bytes/mode and reject links/bounds; scale, canvas, board, cache-hit, mismatch and failed-refresh guards")
