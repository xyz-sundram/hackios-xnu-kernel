#!/usr/bin/env python3
"""Check the production touch escape flushes data before parking peer CPUs."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
source=(root/'drivers/input/hakios.input.c').read_text();start=source.index('static void\nexit_hold_elapsed(');end=source.index('\n}\n',start)+3
body=source[start:end]
prefix=r'''
#include <assert.h>
#include <stddef.h>
#define __unused __attribute__((unused))
#define printf(...) ((void)0)
typedef void *thread_call_param_t;
static unsigned stage;
static void hakios_var_save_on_return(void) { assert(stage++==0); }
static void hakios_adreno_stop(void) { assert(stage++==1); }
static void hakios_wifi_stop(void) { assert(stage++==2); }
static void hakios_adsp_stop(void) { assert(stage++==3); }
static void loader_return(void) { assert(stage++==4); }
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'exit.c').write_text(prefix+body+'\nint main(void) { exit_hold_elapsed(NULL,NULL); assert(stage==5); return 0; }\n')
 subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',str(p/'exit.c'),'-o',str(p/'exit')],check=True)
 subprocess.run([str(p/'exit')],check=True)
print('PASS: touch escape syncs HFS and stops both DSPs before the CPU handoff')
