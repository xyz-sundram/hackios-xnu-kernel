#!/usr/bin/env python3
"""Host-test actual imported DRM queue-pressure paths; no GPU or SDK needed."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'tools/mesa/hakios-drm/hakios.drm.c').read_text()


def function(name):
    start = source.index('\n' + name + '(')
    start = source.rfind('\n', 0, start) + 1
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


prefix = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <time.h>
#include <stdio.h>
#define DRM_COMMAND_BASE 64
#define DRM_MSM_WAIT_FENCE 7
#define DRM_MSM_GEM_SUBMIT 8
#define MSM_SUBMIT_SYNCOBJ_IN 1
#define MSM_SUBMIT_SYNCOBJ_OUT 2
#define MSM_SUBMIT_SYNCOBJ_RESET 4
#define DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL 1
#define DRM_SYNCOBJ_WAIT_FLAGS_WAIT_FOR_SUBMIT 2
struct drm_msm_wait_fence { uint32_t fence, queueid; struct { int64_t tv_sec, tv_nsec; } timeout; };
struct drm_msm_gem_submit_syncobj { uint32_t handle, flags; uint64_t point; };
struct drm_msm_gem_submit { uint32_t flags, syncobj_stride, nr_in_syncobjs, nr_out_syncobjs, nr_cmds, fence;
    uint64_t in_syncobjs, out_syncobjs; };
static int wait_error;
static unsigned polls, waits, gpu_calls;
static bool poll_complete;
static int call(unsigned number, void *data, size_t size) {
    if (number == DRM_COMMAND_BASE + DRM_MSM_WAIT_FENCE) {
        struct drm_msm_wait_fence *w = data;
        assert(size == sizeof(*w));
        if (!w->timeout.tv_sec && !w->timeout.tv_nsec) {
            polls++; return poll_complete ? 0 : ETIMEDOUT;
        }
        waits++; return wait_error;
    }
    assert(number == DRM_COMMAND_BASE + DRM_MSM_GEM_SUBMIT);
    gpu_calls++; ((struct drm_msm_gem_submit *)data)->fence=99; return 0;
}
static int drmSyncobjTimelineWait(int fd, uint32_t *h, uint64_t *p, unsigned n, int64_t d, unsigned f, uint32_t *first) {
    (void)fd;(void)h;(void)p;(void)n;(void)d;(void)f;(void)first;return 0;
}
static int drmSyncobjReset(int fd, const uint32_t *h, uint32_t n) { (void)fd;(void)h;(void)n;return 0; }
'''
state = source[source.index('#define SYNCOBJS'):source.index('static struct syncobj *\nsyncobj_of(')]
production = '\n'.join(function(n) for n in ('syncobj_of', 'fence_wait', 'fence_done',
                                              'now_ns', 'syncobj_poll', 'syncobj_add', 'drmSyncobjTransfer'))
production += '\nstatic uint32_t last_fence;\n' + function('submit_with_syncobjs')
tests = r'''
static void full(struct syncobj *s) {
    memset(s,0,sizeof(*s)); s->used=true; s->reached=3; s->pending=PENDING;
    for (unsigned i=0;i<PENDING;i++) { s->on_the_way[i].point=10+i;s->on_the_way[i].fence=100+i; }
}
static void unlocked(void) { assert(!pthread_mutex_trylock(&syncobj_lock));pthread_mutex_unlock(&syncobj_lock); }
int main(void) {
    int errors[]={ETIMEDOUT,EIO,EINVAL};
    for(unsigned i=0;i<sizeof(errors)/sizeof(*errors);i++) {
        struct syncobj s,before;
        wait_error=errors[i];poll_complete=false;full(&s);before=s;
        assert(syncobj_add(&s,90,900)==-errors[i]);
        assert(!memcmp(&s,&before,sizeof(s))); // no fabricated reached point or dropped fence
        full(&syncobjs[1]); before=syncobjs[1];
        memset(&syncobjs[2],0,sizeof(syncobjs[2]));syncobjs[2].used=true;
        syncobjs[2].pending=1;syncobjs[2].on_the_way[0].point=90;syncobjs[2].on_the_way[0].fence=900;
        assert(drmSyncobjTransfer(0,1,90,2,90,0)==-errors[i]);
        assert(!memcmp(&syncobjs[1],&before,sizeof(before)));unlocked();
        struct drm_msm_gem_submit_syncobj out={.handle=1,.point=90};
        struct drm_msm_gem_submit submit={.flags=MSM_SUBMIT_SYNCOBJ_OUT,.nr_out_syncobjs=1,
            .out_syncobjs=(uintptr_t)&out};
        assert(submit_with_syncobjs(&submit,sizeof(submit))==-errors[i]);
        assert(!memcmp(&syncobjs[1],&before,sizeof(before)));unlocked();
    }
    struct syncobj s; full(&s);wait_error=0;
    assert(syncobj_add(&s,90,900)==0);
    assert(s.reached==10 && s.pending==PENDING && s.on_the_way[0].point==11);
    assert(s.on_the_way[PENDING-1].point==90 && s.on_the_way[PENDING-1].fence==900);
    full(&s);poll_complete=true;unsigned old_waits=waits;
    assert(syncobj_add(&s,90,900)==0 && s.pending==1 && s.reached==25 && waits==old_waits);
    memset(&s,0,sizeof(s));assert(syncobj_add(&s,1,9)==0 && s.pending==1 && !s.reached);
    assert(polls && waits && !gpu_calls);
    puts("PASS actual DRM queue: timeout/EIO/EINVAL preserve pending points; transfer/submit propagate and unlock; successful waits and polling retire correctly");
}
'''
with tempfile.TemporaryDirectory(prefix='drm-syncobj-') as temp:
    temp = Path(temp)
    src, exe = temp / 'syncobj.c', temp / 'syncobj'
    src.write_text(prefix + state + production + tests)
    subprocess.run([os.environ.get('CC','cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-pthread', str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
