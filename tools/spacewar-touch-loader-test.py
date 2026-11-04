#!/usr/bin/env python3
"""Host tests of the production live-touch loader policy and lease state machine."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'xnuloader/loader.c').read_text()
profile = source[source.index('static int\ntouch_token_is('):source.index('static int\nrun_kernel(')]
poll = source[source.index('static uint64_t\ntouch_lease_peer_read('):source.index('static uint64_t\nseconds_since(')]
assert poll.count('__asm__ volatile ("yield" ::: "memory");') == 1
poll = poll.replace('__asm__ volatile ("yield" ::: "memory");', 'sim_yield();')
prefix = r'''
#include <assert.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hakios.spacewar.lease.h"
#include "hakios.spacewar.services.h"
static struct sw_touch_lease backing;
static struct sw_touch_lease *touch_shared = &backing;
static int run_active, touch_live_enabled;
static volatile uint64_t other_cpu_done;
static unsigned clean_calls, flush_calls, yield_calls, firmware_turns, scenario, quiet_calls;
static uint64_t granted[8];
static size_t str_len(const char *s) { return strlen(s); }
static int str_eq(const char *a,const char *b) { return strcmp(a,b)==0; }
static void touch_lease_quiet_cpu(void) { quiet_calls++; }
static void flush_dcache(uint64_t start, uint64_t end) {
    assert(end-start==8);
    assert(start==(uintptr_t)&backing.request || start==(uintptr_t)&backing.release || start==(uintptr_t)&other_cpu_done);
    flush_calls++;
}
static void clean_dcache(uint64_t start, uint64_t end) {
    assert(start==(uintptr_t)&backing.grant && end-start==8);
    if(backing.grant)assert(quiet_calls>clean_calls);
    assert(clean_calls<8);granted[clean_calls++]=backing.grant;
    if(scenario==2 && backing.grant)backing.release=backing.request; /* cancellation races a late grant */
}
static void sim_yield(void) {
    yield_calls++;
    assert(firmware_turns==0 && backing.grant!=0);
    if(scenario==3) { other_cpu_done=1;return; }
    if(scenario==4) { backing.release=backing.grant;backing.request=backing.grant+1;return; }
    if(scenario==5 && yield_calls<10)return; /* acknowledged grants are never time-revoked */
    backing.release=backing.grant;
}
'''
tests = r'''
static void reset(unsigned which) {
    memset(&backing,0,sizeof(backing));run_active=touch_live_enabled=1;touch_shared=&backing;
    other_cpu_done=0;clean_calls=flush_calls=yield_calls=firmware_turns=quiet_calls=0;scenario=which;
    memset(granted,0,sizeof(granted));
}
static void policy_tests(void) {
    const char *required="touch_live=1 bootcpu=7 cpus=7 buttons=1 touchscreen=1 touch_spacewar=1 touch_steps=6";
    char line[2048];
    assert(touch_live_profile(required,0,0x700,1)==1);
    const char *storage_good[]={"statusbar_cutout=0", "statusbar_cutout=1",
        "spacewar_storage=1 ufs=2",
        "spacewar_storage=1 ufs=2 ufs_write=1 root_apfs=ufs_darwin var_hfs=ufs_darwin_data"};
    for(unsigned i=0;i<sizeof(storage_good)/sizeof(storage_good[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",required,storage_good[i]);
        assert(touch_live_profile(line,0,0x700,1)==1);
    }
    const char *storage_bad[]={"statusbar_cutout=2", "ufs=2", "spacewar_storage=1",
        "spacewar_storage=1 ufs=1", "spacewar_storage=1 ufs=2 ufs_write=1",
        "spacewar_storage=1 ufs=2 root_apfs=ufs_darwin var_hfs=ufs_darwin_data",
        "spacewar_storage=1 ufs=2 ufs_write=1 root_apfs=ufs_userdata var_hfs=ufs_darwin_data",
        "spacewar_storage=1 ufs=2 ufs_write=1 root_apfs=ufs_darwin var_hfs=ufs_userdata",
        "spacewar_storage=1 ufs=2 ufs_write=1 root_apfs=ufs_darwin",
        "spacewar_storage=1 ufs=2 ufs_native=1"};
    for(unsigned i=0;i<sizeof(storage_bad)/sizeof(storage_bad[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",required,storage_bad[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    snprintf(line,sizeof(line),"-v loader_return_after=0 epoch=1793763657 sb_delay=8 %s socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 touch_seconds=0 touch_lat=1",required);
    assert(touch_live_profile(line,0,0x700,1)==1);
    snprintf(line,sizeof(line),"%s gpu=1 adreno_speed=1 cpufreq=2 hakios_debug=0 cpu_probe=1",required);
    assert(touch_live_profile(line,0,0x700,1)==1);
    snprintf(line,sizeof(line),"%s cpufreq=2",required);
    assert(touch_live_profile(line,0,0x700,1)==-1);
    snprintf(line,sizeof(line),"%s gpu=1 adreno_speed=1 cpufreq=1",required);
    assert(touch_live_profile(line,0,0x700,1)==-1);
    snprintf(line,sizeof(line),"%s gpu=1 adreno_speed=1 hakios_debug=0 cpu_probe=1 touch_lat=0",required);
    assert(touch_live_profile(line,0,0x700,1)==1);
    snprintf(line,sizeof(line),"%s hakios_debug=1",required);
    assert(touch_live_profile(line,0,0x700,1)==1);
    snprintf(line,sizeof(line),"%s hakios_debug=2",required);
    assert(touch_live_profile(line,0,0x700,1)==-1);
    snprintf(line,sizeof(line),"%s gpu=1 adreno_speed=1",required);
    assert(touch_live_profile(line,0,0x700,1)==1);
    const char *gpu_bad[]={"gpu=1", "adreno_speed=1", "gpu=0 adreno_speed=1", "gpu=2 adreno_speed=1",
        "gpu=1 adreno_speed=0", "gpu=1 adreno_speed=4", "gpu=1 adreno_speed=01", "gpu=1 adreno_speed=1 gpu=1", "gpu=1 adreno_speed=1 adreno=22",
        "gpu=1 adreno_speed=1 adreno_keep=0", "gpu=1 adreno_speed=1 adreno_skip=0"};
    for(unsigned i=0;i<sizeof(gpu_bad)/sizeof(gpu_bad[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",required,gpu_bad[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    for(unsigned speed=1;speed<=3;speed++) {
        snprintf(line,sizeof(line),"%s gpu=1 adreno_speed=%u cpufreq=2 hakios_debug=0",required,speed);
        assert(touch_live_profile(line,0,0x700,1)==1);
    }

    const char *install="spacewar_install=1 spacewar_storage=1 ufs=2 ufs_write=1 var_hfs=ufs_darwin_data gpu=1 adreno_speed=3 cpufreq=2";
    char service_line[512];
    snprintf(service_line,sizeof(service_line),"%s %s spacewar_services=1",required,install);
    assert(touch_live_profile(service_line,0,0x700,1)==1);
    for(unsigned i=0;i<SW_SERVICES_BOUND_COUNT;i++) {
        if(sw_services_bounds[i].profile>1)continue;
        snprintf(line,sizeof(line),"%s %s=%u",service_line,sw_services_bounds[i].name,sw_services_bounds[i].minimum);
        assert(touch_live_profile(line,0,0x700,1)==1);
        snprintf(line,sizeof(line),"%s %s=%u",service_line,sw_services_bounds[i].name,sw_services_bounds[i].maximum);
        assert(touch_live_profile(line,0,0x700,1)==1);
        snprintf(line,sizeof(line),"%s %s=%u",service_line,sw_services_bounds[i].name,sw_services_bounds[i].maximum+1);
        assert(touch_live_profile(line,0,0x700,1)==-1);
        if(sw_services_bounds[i].minimum) {
            snprintf(line,sizeof(line),"%s %s=0",service_line,sw_services_bounds[i].name);
            assert(touch_live_profile(line,0,0x700,1)==-1);
        }
        snprintf(line,sizeof(line),"%s %s=1 %s=1",service_line,sw_services_bounds[i].name,sw_services_bounds[i].name);
        assert(touch_live_profile(line,0,0x700,1)==-1);
        snprintf(line,sizeof(line),"%s %s=-1",service_line,sw_services_bounds[i].name);
        assert(touch_live_profile(line,0,0x700,1)==-1);
        snprintf(line,sizeof(line),"%s %s %s=1",required,install,sw_services_bounds[i].name);
        assert(touch_live_profile(line,0,0x700,1)==-1); /* no services marker */
    }
    snprintf(line,sizeof(line),"%s adsp_step=1 haptic=1 pmic=1 wifi_step=1 adsp_pause=0 wifi_pause=0 speaker_probe=2",service_line);
    assert(touch_live_profile(line,0,0x700,1)==1);
    snprintf(line,sizeof(line),"%s adsp=1 wifi=1 haptic=5",service_line);
    assert(touch_live_profile(line,0,0x700,1)==1);
    const char *service_bad[]={"battery=1","adsp_phys=0x86700000","wifi_memory=0x9ae00000","sound=1","mic=1","modem=1",
        "haptic_again=1","adsp_again=1","wifi_again=1","speaker_probe=3","spacewar_services=1"};
    for(unsigned i=0;i<sizeof(service_bad)/sizeof(service_bad[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",service_line,service_bad[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    const char *wrong_services[]={"spacewar_services=0","spacewar_services=3","spacewar_services=01"};
    for(unsigned i=0;i<3;i++) {
        snprintf(line,sizeof(line),"%s %s %s",required,install,wrong_services[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }

    char audio_line[512];
    snprintf(audio_line,sizeof(audio_line),"%s %s spacewar_services=2",required,install);
    assert(touch_live_profile(audio_line,0,0x700,1)==1);
    for(unsigned i=0;i<SW_SERVICES_BOUND_COUNT;i++) {
        snprintf(line,sizeof(line),"%s %s=%u",audio_line,sw_services_bounds[i].name,sw_services_bounds[i].minimum);
        assert(touch_live_profile(line,0,0x700,1)==1);
        snprintf(line,sizeof(line),"%s %s=%u",audio_line,sw_services_bounds[i].name,sw_services_bounds[i].maximum);
        assert(touch_live_profile(line,0,0x700,1)==1);
        snprintf(line,sizeof(line),"%s %s=%u",audio_line,sw_services_bounds[i].name,sw_services_bounds[i].maximum+1);
        assert(touch_live_profile(line,0,0x700,1)==-1);
        snprintf(line,sizeof(line),"%s %s=1 %s=1",audio_line,sw_services_bounds[i].name,sw_services_bounds[i].name);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    for(unsigned sound=0;sound<=4;sound++) {
        snprintf(line,sizeof(line),"%s sound=%u",audio_line,sound);
        assert(touch_live_profile(line,0,0x700,1)==1);
        snprintf(line,sizeof(line),"%s sound=%u",service_line,sound);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    const char *continuous_bad[]={"sound_cont=2","sound_cont=4294967295","sound_cont=18446744073709551615",
        "sound_cont=-1","sound_cont=+1","sound_cont=1 sound_cont=0","sound_cont=0 sound_cont=1","sound_cont"};
    for(unsigned i=0;i<sizeof(continuous_bad)/sizeof(continuous_bad[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",audio_line,continuous_bad[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    for(unsigned continuous=0;continuous<=1;continuous++) {
        snprintf(line,sizeof(line),"%s sound_cont=%u",audio_line,continuous);
        assert(touch_live_profile(line,0,0x700,1)==1);
        snprintf(line,sizeof(line),"%s sound_cont=%u",service_line,continuous);
        assert(touch_live_profile(line,0,0x700,1)==-1);
        snprintf(line,sizeof(line),"%s %s sound_cont=%u",required,install,continuous);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    const char *audio_bad[]={"battery=1","sound=5","mic=1","ufs_native=1","speaker_probe=3","spacewar_services=1","spacewar_services=2","sound_phys=0x1234"};
    for(unsigned i=0;i<sizeof(audio_bad)/sizeof(audio_bad[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",audio_line,audio_bad[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    snprintf(line,sizeof(line),"%s %s spacewar_services=02",required,install);
    assert(touch_live_profile(line,0,0x700,1)==-1);
    assert(touch_live_profile("spacewar_services=2 touch_live=0",0,0x700,1)==-1);
    assert(touch_live_profile("spacewar_services=1 touch_live=0",0,0x700,1)==-1);
    assert(touch_live_profile("spacewar_services=1",0,0x700,1)==-1);
    snprintf(line,sizeof(line),"%s gpu=1 adreno_speed=3 cpufreq=2 spacewar_services=1",required);
    assert(touch_live_profile(line,0,0x700,1)==-1); /* no installed map tuple */
    assert(touch_live_profile(required,0,0x700,0)==-1);
    assert(touch_live_profile(required,1,0x700,1)==-1);
    assert(touch_live_profile(required,0,0x600,1)==-1);
    const char *bad[]={"cpumask=0xfe","cpufreq=0","battery=1","ufs=0","gpu=1","wifi=1","adsp=1","cam=5","touch_shared_pa=0x1234","cpus=7","buttons=1","epoch=2 epoch=3","touch_seconds=601","touch_lat=2","loader_return_after=-1","epoch=18446744073709551616","epoch=","epoch=12x","touch_steps=60","touch_live=0"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++) {
        snprintf(line,sizeof(line),"%s %s",required,bad[i]);
        assert(touch_live_profile(line,0,0x700,1)==-1);
    }
    assert(touch_live_profile("touch_live=1suffix bootcpu=7 cpus=7 buttons=1 touchscreen=1 touch_spacewar=1 touch_steps=6",0,0x700,1)==-1);
    assert(touch_live_profile("touch_live=1 bootcpu=7 cpus=07 buttons=1 touchscreen=1 touch_spacewar=1 touch_steps=6",0,0x700,1)==-1);
    assert(touch_live_profile("touch_live=1 bootcpu=7 cpus=7 buttons=1 touchscreen=1 touch_steps=6",0,0x700,1)==-1);
    assert(touch_live_profile("touch_live=1 bootcpu=7 cpus=7 buttons=0 touchscreen=1 touch_spacewar=1 touch_steps=6",0,0x700,1)==-1);
    assert(touch_live_profile("touch_live=0 gpu=1 cpus=8",0,0x700,1)==0);
    assert(touch_live_profile("gpu=1 cpus=8",0,0x700,1)==0);
    assert(touch_live_profile("-v bootcpu=7 cpus=7 buttons=1",0,0x700,1)==0);
    puts("PASS exact live profile; duplicates, malformed numbers, hardware extras and injected addresses refused; legacy modes retained");
}
int main(void) {
    policy_tests();
    reset(0);touch_lease_poll();assert(!clean_calls&&!yield_calls);
    reset(0);run_active=0;backing.request=1;touch_lease_poll();assert(!flush_calls&&!clean_calls);
    reset(0);touch_live_enabled=0;backing.request=1;touch_lease_poll();assert(!flush_calls&&!clean_calls);
    reset(0);touch_shared=NULL;touch_lease_poll();assert(!flush_calls&&!clean_calls);
    reset(1);backing.request=1;touch_lease_poll();assert(clean_calls==1&&granted[0]==1&&backing.release==1&&yield_calls==1);
    reset(1);backing.request=1;backing.release=1;touch_lease_poll();assert(!clean_calls&&!yield_calls); /* cancel before grant */
    reset(2);backing.request=1;touch_lease_poll();assert(clean_calls==1&&backing.release==1&&!yield_calls); /* cancel during grant */
    reset(3);backing.request=1;touch_lease_poll();assert(clean_calls==2&&granted[0]==1&&granted[1]==0&&other_cpu_done);
    reset(4);backing.request=1;touch_lease_poll();assert(clean_calls==1&&backing.grant==1&&backing.request==2&&backing.release==1);
    firmware_turns++; /* pending next packet cannot starve the caller's USB opportunity */
    assert(firmware_turns==1);firmware_turns=0;
    touch_lease_poll();assert(clean_calls==2&&backing.grant==2&&backing.request==3&&backing.release==2);
    reset(5);backing.request=9;touch_lease_poll();assert(clean_calls==1&&granted[0]==9&&yield_calls==10&&backing.release==9);
    reset(1);backing.request=4;backing.release=3;backing.grant=1;touch_lease_poll();assert(clean_calls==1&&granted[0]==4&&backing.release==4);
    reset(0);backing.request=4;backing.release=4;backing.grant=1;touch_lease_poll();assert(!clean_calls&&!yield_calls);
    reset(3);backing.request=2;backing.grant=1;touch_lease_poll();assert(clean_calls==1&&granted[0]==0&&other_cpu_done);
    puts("PASS actual loader poll: disabled/idle, grant/release, early/late cancellation, stopped kernel, nonce skips and no timeout revocation");
    puts("PASS next packet already pending still yields a USB turn after each acknowledged lease");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-loader-lease-') as tmp:
    tmp=Path(tmp)
    (tmp/'test.c').write_text(prefix+poll+profile+tests)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-O2','-fsanitize=address,undefined',
                    '-I',str(root/'drivers/input'),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
print('spacewar-touch-loader-test: production policy and protocol passed under ASAN/UBSAN; no hardware access')
