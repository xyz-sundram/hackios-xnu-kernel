#!/usr/bin/env python3
"""Compile the actual bounded EPSS policy with fake registers; no hardware access."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
source=(root/'drivers/power/hakios.spacewar.cpufreq.inc').read_text().replace('__asm__ volatile("dsb sy" ::: "memory");','barriers++;')
prefix=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uintptr_t vm_offset_t;
#define EPSS_PHYS 0x18591000ul
#define EPSS_DOMAIN_SIZE 0x1000ul
#define EPSS_PERF_STATE 0x320
#define EPSS_FREQ_TABLE 0x100
#define EPSS_TABLE_ROWS 40
#define XO_KHZ 19200
#define ALTERNATE_KHZ 300000
#define NSEC_PER_MSEC 1000000
static unsigned board=1,driver_count=10,masked,maps,sleeps,barriers,fail_map,fail_readback,gpu_speed=1;
static const char *badarg;
static const char *driver_list="virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal virtio_blk";
#define HAKIOS_DEVICE_SPACEWAR board
#define HAKIOS_DRIVERS_COUNT driver_count
#define HAKIOS_DRIVERS_LIST driver_list
static uint32_t regs[2][1024],original[2][1024];
static vm_offset_t ml_io_map(vm_offset_t pa,unsigned size){assert(size==4096&&pa>=EPSS_PHYS+4096&&pa<=EPSS_PHYS+8192);maps++;if(fail_map==maps)return 0;return(vm_offset_t)regs[(pa-EPSS_PHYS-4096)/4096];}
static void delay_for_interval(unsigned n,unsigned unit){assert(n==20&&unit==NSEC_PER_MSEC);sleeps++;if(fail_readback==sleeps)regs[sleeps-1][EPSS_PERF_STATE/4]=0;}
static bool PE_parse_boot_argn(const char *name,void *out,unsigned size){assert(size==4);if(!strcmp(name,"cpumask"))return masked!=0;if(badarg&&!strcmp(name,badarg))return false;*(uint32_t*)out=!strcmp(name,"adreno_speed")?gpu_speed:!strcmp(name,"cpufreq")?2:(!strcmp(name,"cpus")||!strcmp(name,"bootcpu"))?7:1;return true;}
static void tell(const char *fmt,...){(void)fmt;}
'''
tests=r'''
static void setup(void){board=1;driver_count=10;masked=maps=sleeps=barriers=fail_map=fail_readback=0;badarg=NULL;memset(regs,0,sizeof(regs));memset(spacewar_cpu_perf,0,sizeof(spacewar_cpu_perf));for(unsigned d=0;d<2;d++)for(unsigned r=0;r<40;r++)regs[d][EPSS_FREQ_TABLE/4+r]=(1u<<30)|(r<9?20+10*r:100);memcpy(original,regs,sizeof(regs));}
static void restored(void){unsigned before_sleeps=sleeps;hakios_cpufreq_quiesce();assert(sleeps==before_sleeps&&!memcmp(original,regs,sizeof(regs)));}
int main(void){unsigned cases=0;
setup();assert(spacewar_cpu_perf_start()&&maps==2&&sleeps==2);for(unsigned d=0;d<2;d++){assert(regs[d][EPSS_PERF_STATE/4]==8);original[d][EPSS_PERF_STATE/4]=8;}assert(!memcmp(original,regs,sizeof(regs)));for(unsigned d=0;d<2;d++)original[d][EPSS_PERF_STATE/4]=0;restored();cases++;
const char *required[]={"cpufreq","bootcpu","cpus","buttons","gpu","adreno_speed","touch_live"};for(unsigned i=0;i<7;i++){setup();badarg=required[i];assert(!spacewar_cpu_perf_start()&&!maps);restored();cases++;}
for(gpu_speed=2;gpu_speed<=3;gpu_speed++){setup();assert(spacewar_cpu_perf_start());restored();cases++;}
gpu_speed=0;setup();assert(!spacewar_cpu_perf_start()&&!maps);cases++;
gpu_speed=4;setup();assert(!spacewar_cpu_perf_start()&&!maps);cases++;gpu_speed=1;
setup();board=0;assert(!spacewar_cpu_perf_start()&&!maps);cases++;
setup();driver_count=9;assert(!spacewar_cpu_perf_start()&&!maps);cases++;
setup();masked=1;assert(!spacewar_cpu_perf_start()&&!maps);cases++;
setup();fail_map=2;assert(!spacewar_cpu_perf_start());restored();cases++;
setup();fail_readback=2;assert(!spacewar_cpu_perf_start());restored();cases++;
setup();regs[1][EPSS_PERF_STATE/4]=39;memcpy(original,regs,sizeof(regs));assert(!spacewar_cpu_perf_start());restored();cases++;
setup();for(unsigned i=0;i<40;i++)regs[0][EPSS_FREQ_TABLE/4+i]=(1u<<30)|20;memcpy(original,regs,sizeof(regs));assert(!spacewar_cpu_perf_start());restored();cases++;
setup();for(unsigned i=0;i<40;i++)regs[0][EPSS_FREQ_TABLE/4+i]=(1u<<30)|200;memcpy(original,regs,sizeof(regs));assert(!spacewar_cpu_perf_start());restored();cases++;
setup();assert(spacewar_cpu_perf_start());regs[0][EPSS_PERF_STATE/4]=3;hakios_cpufreq_quiesce();assert(regs[0][EPSS_PERF_STATE/4]==3&&regs[1][EPSS_PERF_STATE/4]==0);cases++;
setup();for(unsigned d=0;d<2;d++)regs[d][EPSS_PERF_STATE/4]=8;memcpy(original,regs,sizeof(regs));assert(spacewar_cpu_perf_start()&&!sleeps);restored();cases++;
printf("PASS %u actual EPSS cases: firmware LUT cap, no domain0/DDR access, guarded profile, partial rollback, readback and ownership-aware restore\n",cases);}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-cpu-perf-') as d:
 p=Path(d);(p/'test.c').write_text(prefix+source+tests)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-O2','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
