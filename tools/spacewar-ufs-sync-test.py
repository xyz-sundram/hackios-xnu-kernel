#!/usr/bin/env python3
"""Host-test the actual orderly-return HFS sync hook and installed data I/O.

No device access. Reuse the installed-storage test's mock capability and loader,
then extract the mount callback and public return hook from the production file.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

VFS_STUBS = r'''
#include <stdarg.h>
#define MNT_WAIT 1
#define VFS_RETURNED 0
#define VFS_RETURNED_DONE 1
struct vfsstatfs {
 char f_fstypename[16],f_mntonname[128],f_mntfromname[128];
};
struct mount {struct vfsstatfs status;bool missing_status;};
typedef struct mount *mount_t;
struct vfs_context {int unused;};
typedef struct vfs_context *vfs_context_t;
static struct vfs_context kernel_context;
static struct mount mounts[5];
static unsigned mount_count,iterations,syncs,sleeps;
static int sync_error,iterate_error;
static bool change_cap_before_callback,change_cap_before_bio,complete_saver;
static char log_text[4096];
static size_t log_used;
static volatile int ufs_var_save;

static int capture_printf(const char *format,...){
 va_list args;va_start(args,format);
 int count=vsnprintf(log_text+log_used,sizeof(log_text)-log_used,format,args);
 va_end(args);assert(count>=0&&(size_t)count<sizeof(log_text)-log_used);
 log_used+=(size_t)count;return count;
}
static struct vfsstatfs *vfs_statfs(mount_t mp){return mp->missing_status?NULL:&mp->status;}
static vfs_context_t vfs_context_kernel(void){return &kernel_context;}
static int VFS_SYNC(mount_t mp,int wait,vfs_context_t context){
 assert(wait==MNT_WAIT&&context==&kernel_context);
 assert(!ufs_write_mtx&&!ufs_data_mtx&&!ufs_lock);
 assert(!strcmp(mp->status.f_fstypename,"hfs"));
 assert(!strcmp(mp->status.f_mntonname,"/private/var"));
 assert(!strcmp(mp->status.f_mntfromname,"/dev/ufs_darwin_data"));
 syncs++;
 if(sync_error)return sync_error;
 if(change_cap_before_bio)page.install_cap.data_guid[0]^=1;
 uint8_t bytes[4096]={0};
 return ufs_data_io(true,0,bytes,sizeof(bytes));
}
static int vfs_iterate(int flags,int (*callback)(mount_t,void *),void *arg){
 assert(!flags&&!ufs_write_mtx&&!ufs_data_mtx&&!ufs_lock);iterations++;
 if(iterate_error)return iterate_error;
 if(change_cap_before_callback)page.install_cap.data_guid[0]^=1;
 for(unsigned i=0;i<mount_count;i++){
  int result=callback(&mounts[i],arg);
  if(result==VFS_RETURNED_DONE)return 0;
  assert(result==VFS_RETURNED);
 }
 return 0;
}
static void IOSleep(unsigned ms){
 assert(ms==20);sleeps++;
 if(complete_saver)ufs_var_save=VAR_SAVE_DONE;
}
#define printf capture_printf
'''

TESTS = r'''
#undef printf
static void setup(void){
 reset();assert(!ufs_write_mtx&&!ufs_data_mtx&&!ufs_lock);
 memset(mounts,0,sizeof(mounts));
 mount_count=3;iterations=syncs=sleeps=0;sync_error=iterate_error=0;
 change_cap_before_callback=change_cap_before_bio=complete_saver=false;
 log_used=0;memset(log_text,0,sizeof(log_text));ufs_var_save=VAR_SAVE_NONE;
 strcpy(mounts[0].status.f_fstypename,"apfs");
 strcpy(mounts[0].status.f_mntonname,"/");
 strcpy(mounts[0].status.f_mntfromname,"/dev/md0");
 strcpy(mounts[1].status.f_fstypename,"hfs");
 strcpy(mounts[1].status.f_mntonname,"/private/var");
 strcpy(mounts[1].status.f_mntfromname,"/dev/ufs_darwin_data");
 mounts[2]=mounts[1];strcpy(mounts[2].status.f_mntonname,"/other");
}
static void skipped(void){
 hakios_var_save_on_return();
 assert(!iterations&&!syncs&&!requests&&!sleeps&&!log_used);
}
int main(void){
 setup();hakios_var_save_on_return();
 assert(iterations==1&&syncs==1&&requests==1&&!sleeps);
 assert(sent[0].op==BLOCK_OP_WRITE&&sent[0].part==1&&sent[0].lba==0&&sent[0].count==1);
 assert(ufs_var_save==VAR_SAVE_NONE&&strstr(log_text,"sync completed (MNT_WAIT)"));cases++;

 for(unsigned variant=0;variant<9;variant++){
  setup();switch(variant){
  case 0:ufs_install_requested=false;break;
  case 1:ufs_install_enabled=false;break;
  case 2:ufs_ready=false;break;
  case 3:ufs_write_part=-1;break;
  case 4:page.install_cap.data_guid[0]^=1;break;
  case 5:page.part[1].start_lba++;break;
  case 6:args[0].value="0";break;
  case 7:args[4].value="ufs_userdata";break;
  case 8:board_spacewar=false;break;
  }skipped();cases++;
 }
 for(unsigned variant=0;variant<9;variant++){
  setup();switch(variant){
  case 0:strcpy(mounts[1].status.f_fstypename,"apfs");break;
  case 1:strcpy(mounts[1].status.f_fstypename,"hfsx");break;
  case 2:strcpy(mounts[1].status.f_mntonname,"/var");break;
  case 3:strcpy(mounts[1].status.f_mntonname,"/private/var/extra");break;
  case 4:strcpy(mounts[1].status.f_mntfromname,"/dev/ufs_darwin");break;
  case 5:strcpy(mounts[1].status.f_mntfromname,"/dev/ufs_darwin_dataX");break;
  case 6:strcpy(mounts[1].status.f_mntfromname,"/dev/ufs_userdata");break;
  case 7:mounts[1].missing_status=true;break;
  case 8:mount_count=0;break;
  }hakios_var_save_on_return();
  assert(iterations==1&&!syncs&&!requests&&!sleeps);
  assert(strstr(log_text,"exact mount not found")&&!strstr(log_text,"completed"));cases++;
 }
 setup();sync_error=EIO;hakios_var_save_on_return();
 assert(syncs==1&&!requests&&!sleeps&&strstr(log_text,"sync failed (5)")&&!strstr(log_text,"completed"));cases++;
 setup();iterate_error=ENOMEM;hakios_var_save_on_return();
 assert(iterations==1&&!syncs&&!requests&&strstr(log_text,"sync failed")&&!strstr(log_text,"completed"));cases++;
 setup();change_cap_before_callback=true;hakios_var_save_on_return();
 assert(iterations==1&&!syncs&&!requests&&strstr(log_text,"sync failed")&&!strstr(log_text,"completed"));cases++;
 setup();change_cap_before_bio=true;hakios_var_save_on_return();
 assert(syncs==1&&!requests&&strstr(log_text,"sync failed")&&!strstr(log_text,"completed"));cases++;

 /* The callback does not relax any write policy: actual I/O rejects every
  * other partition and offsets at/past the capability's data limit. */
 for(unsigned part=0;part<6;part++)if(part!=1){
  setup();assert(ufs_write(BLOCK_OP_WRITE,part,0,buffer,4096)==EROFS&&!requests);cases++;
 }
 setup();assert(ufs_write(BLOCK_OP_WRITE,1,page.install_cap.blocks*4096,buffer,4096)==EROFS&&!requests);cases++;
 setup();assert(ufs_write(BLOCK_OP_WRITE,1,UINT64_MAX-4095,buffer,8192)==EROFS&&!requests);cases++;

 /* Legacy saver behavior: no installed sync, same request/wait/answer flow. */
 setup();ufs_install_requested=ufs_install_enabled=false;
 ufs_var_save=VAR_SAVE_IDLE;complete_saver=true;hakios_var_save_on_return();
 assert(!iterations&&!syncs&&!requests&&sleeps==1&&ufs_var_save==VAR_SAVE_DONE);
 assert(strstr(log_text,"saver has finished")&&!strstr(log_text,"HFS sync"));cases++;
 setup();ufs_install_requested=ufs_install_enabled=false;
 ufs_var_save=VAR_SAVE_IDLE;args[5]=(struct argument){"var_save_ms","20"};
 hakios_var_save_on_return();assert(!iterations&&!syncs&&!requests&&sleeps==1);
 assert(strstr(log_text,"saver did not answer")&&!strstr(log_text,"HFS sync"));cases++;
 printf("PASS %u actual orderly-return HFS sync cases: exact mount, MNT_WAIT, pre-saver ordering, "
        "installed capability/BIO bounds, error logs and unchanged legacy saver\n",cases);
 return 0;
}
'''


def function(source: str, name: str) -> str:
    match = re.search(r'^(?:static )?(?:bool|int|void|uint64_t|uint32_t)\s+'
                      + re.escape(name) + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise ValueError(f"Missing actual helper: {name}")
    return source[match.start():source.index('\n}', match.end()) + 2] + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "drivers/storage/hakios.ufs.c")
    args = parser.parse_args()
    source = args.source.read_text()
    spec = importlib.util.spec_from_file_location("ufs_install_test", ROOT / "tools/spacewar-ufs-install-test.py")
    if spec is None or spec.loader is None:
        raise SystemExit("Cannot load installed-storage mock fixtures")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    structures = source[source.index('#define BLOCK_MAGIC'):source.index('#define UFS_PAGE')]
    saver_constants = source[source.index('#define VAR_SAVE_NONE'):source.index('static volatile int ufs_var_save')]
    sync_struct = source[source.index('struct ufs_install_sync_result {'):source.index('\n};', source.index('struct ufs_install_sync_result {')) + 3]
    globals_text = fixtures.GLOBALS.replace('(void)lock;', 'assert(!*lock);*lock=1;', 1)
    globals_text = globals_text.replace('(void)lock;', 'assert(*lock);*lock=0;', 1)
    helpers = ('ufs_install_profile', 'ufs_install_guid_present', 'ufs_install_offer',
               'ufs_install_still_valid', 'ufs_part_bytes', 'ufs_write_bounds', 'ufs_write',
               'ufs_data_bytes', 'ufs_data_op', 'ufs_data_io', 'ufs_copen')
    source_functions = ''.join(function(source, name) for name in helpers)
    sync_functions = ''.join(function(source, name) for name in
                             ('ufs_install_sync_mount', 'ufs_install_sync_on_return', 'hakios_var_save_on_return'))
    fixture_setup = fixtures.CASES[:fixtures.CASES.index('int main(void){')]
    header = ROOT / "drivers/storage/hakios.spacewar.install.h"
    program = (fixtures.STUBS + '\n#include <stddef.h>\n#include "' + str(header) + '"\n'
               + structures + globals_text + source_functions + saver_constants + VFS_STUBS
               + sync_struct + '\n' + sync_functions + fixture_setup + TESTS)
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    with tempfile.TemporaryDirectory(prefix="spacewar-ufs-sync-") as directory:
        work = Path(directory)
        test = work / "test.c"
        binary = work / "test"
        test.write_text(program)
        subprocess.run(compiler + ['-std=gnu11', '-g', '-O1', '-fsanitize=address,undefined',
                                  '-fno-sanitize-recover=all', str(test), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True,
                       env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})


if __name__ == "__main__":
    main()
