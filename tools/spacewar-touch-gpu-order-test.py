#!/usr/bin/env python3
"""Compile actual input startup with fake drivers; check GPU/touch ordering."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'drivers/input/hakios.input.c').read_text()
function = source[source.index('void\nhakios_input_init(void)'):]
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
static unsigned board, live, gpu, order, touch_at, gpu_at, touch_calls, gpu_calls;
#define HAKIOS_DEVICE_SPACEWAR board
#define DEVFS_CHAR 0
#define UID_ROOT 0
#define GID_WHEEL 0
static int touch_cdevsw, exit_hold_elapsed;
static void *exit_call;
static int cdevsw_add(int n, const void *p) {(void)n;(void)p;return 1;}
static int makedev(int n, int m) {return n+m;}
static void *devfs_make_node(int a,int b,int c,int d,int e,const char *f) {
    (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;return &touch_cdevsw;
}
static void *thread_call_allocate(int a,void *b) {(void)a;(void)b;return &touch_cdevsw;}
static bool PE_parse_boot_argn(const char *name,void *out,unsigned size) {
    assert(size==sizeof(uint32_t));
    if(!strcmp(name,"touch_live")) {*(uint32_t *)out=live;return live!=0;}
    assert(!strcmp(name,"gpu"));*(uint32_t *)out=gpu;return gpu!=0;
}
#define DRIVER(name) static void name(void) {order++;}
DRIVER(hakios_cpufreq_init)
DRIVER(hakios_virtio_input_init)
DRIVER(hakios_buttons_init)
DRIVER(hakios_battery_init)
DRIVER(hakios_ufs_init)
DRIVER(hakios_net_init)
DRIVER(hakios_wifi_init)
DRIVER(hakios_adsp_init)
DRIVER(hakios_mpss_init)
static void hakios_goodix_init(void) {touch_at=++order;touch_calls++;}
static void hakios_adreno_init(void) {gpu_at=++order;gpu_calls++;}
'''
tests = r'''
int main(void) {
    for(board=0;board<2;board++)for(live=0;live<3;live++)for(gpu=0;gpu<3;gpu++) {
        order=touch_at=gpu_at=touch_calls=gpu_calls=0;
        hakios_input_init();
        assert(touch_calls==1&&gpu_calls==1&&order==11);
        if(board&&live==1&&gpu==1)assert(gpu_at<touch_at);
        else assert(touch_at<gpu_at);
    }
    puts("PASS 18 startup cases: combined profile starts touch after GPU returns; legacy order and one call per driver retained");
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-touch-gpu-order-') as tmp:
    tmp = Path(tmp)
    (tmp / 'test.c').write_text(prefix + function + tests)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                    '-fsanitize=address,undefined', str(tmp / 'test.c'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True,
                   env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
