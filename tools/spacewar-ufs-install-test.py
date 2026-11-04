#!/usr/bin/env python3
"""Exercise actual installed-storage capability and data I/O helpers on the host.

No phone commands. Compile extracted driver functions with ASan/UBSan; use a
mock loader to record the exact partition and block ranges requested.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define __unused
#define Z_WAITOK 0
#define MIN(a,b) ((a)<(b)?(a):(b))
#define FWRITE 2
#define dev_t unsigned long
#define proc_t void *
#define minor(d) ((unsigned)(d))
#define HAKIOS_DEVICE_SPACEWAR board_spacewar
static bool board_spacewar=true;
struct argument {const char *name,*value;};
static struct argument args[10];
static bool PE_parse_boot_argn(const char *name,void *out,size_t size){
 for(unsigned i=0;i<10;i++)if(args[i].name&&!strcmp(name,args[i].name)){
  if(!strcmp(name,"var_hfs")){size_t length=strlen(args[i].value)+1;memcpy(out,args[i].value,length<size?length:size);}
  else{uint32_t value=strtoul(args[i].value,NULL,0);assert(size==sizeof(value));memcpy(out,&value,size);}return true;
 }return false;
}
'''
GLOBALS = r'''
static struct loader_block page;
static volatile struct loader_block *ufs_page=&page;
static uint32_t ufs_parts,ufs_buffer_size;
static int ufs_write_part;
static uint64_t ufs_write_first;
static bool ufs_install_requested,ufs_install_enabled,ufs_ready;
static struct sw_install_cap ufs_install_identity;
static uint64_t ufs_write_refused,ufs_write_errors;
static bool ufs_write_out,ufs_write_was_native,ufs_native_on,ufs_write_busy,ufs_data_used;
static int ufs_write_mtx,ufs_data_mtx,ufs_lock;
static unsigned requests,reads;
static bool mutate_after_request;
struct request {uint32_t op,part;uint64_t lba,count;};
static struct request sent[16];
static void lck_mtx_lock(int *lock){(void)lock;}
static void lck_mtx_unlock(int *lock){(void)lock;}
static bool ufshc_list_in(void){return false;}
static int ufs_native_out(const char *why){(void)why;return 0;}
static int ufs_request(uint32_t op,uint32_t part,uint64_t lba,uint64_t count,uint8_t *dst,const uint8_t *src){
 (void)dst;assert(src&&requests<16);sent[requests++]=(struct request){op,part,lba,count};
 if(mutate_after_request)page.install_cap.data_guid[0]^=1;return 0;
}
static int ufs_read(uint32_t part,uint64_t offset,uint8_t *out,uint64_t count){
 assert(part==1&&offset%4096==0&&count==4096);reads++;memset(out,0xa5,count);return 0;
}
static void *kalloc_data(size_t size,int flags){(void)flags;return malloc(size);}
static void kfree_data(void *p,size_t size){(void)size;free(p);}
static int ufs_open(dev_t dev,int flags,int type,proc_t p){(void)dev;(void)flags;(void)type;(void)p;return 0;}
'''
CASES = r'''
static unsigned cases;
static uint8_t buffer[8192];
static void reset(void){
 memset(&page,0,sizeof(page));memset(&ufs_install_identity,0,sizeof(ufs_install_identity));memset(sent,0,sizeof(sent));
 page.magic=BLOCK_MAGIC;page.version=BLOCK_VERSION;page.size=sizeof(page);page.parts=ufs_parts=4;page.buffer_size=ufs_buffer_size=4096;
 page.write_part_plus_one=2;page.write_first=0;page.write_caps=BLOCK_CAN_FAST;
 page.install_cap=(struct sw_install_cap){.magic=SW_INSTALL_CAP_MAGIC,.first_lba=755360,.blocks=262144,.lun=4,.block_size=4096};
 page.install_cap.disk_guid[0]=0x11;page.install_cap.data_guid[0]=0x22;
 strcpy(page.part[0].name,"hakios_system");page.part[0].block_size=4096;page.part[0].start_lba=100000;page.part[0].blocks=655360;page.part[0].unit=page.part[0].lun=4;
 strcpy(page.part[1].name,"hakios_data");page.part[1].block_size=4096;page.part[1].start_lba=755360;page.part[1].blocks=262144;page.part[1].unit=page.part[1].lun=4;
 strcpy(page.part[2].name,"unit4");page.part[2].block_size=4096;page.part[2].blocks=2000000;page.part[2].unit=page.part[2].lun=4;page.part[2].flags=BLOCK_PART_UNIT;
 strcpy(page.part[3].name,"boot_b");page.part[3].block_size=4096;page.part[3].blocks=24576;page.part[3].unit=page.part[3].lun=5;
 ufs_write_part=ufs_install_offer(&page,ufs_parts,&ufs_install_identity);assert(ufs_write_part==1);
 ufs_write_first=0;ufs_install_requested=ufs_install_enabled=ufs_ready=true;
 requests=reads=0;mutate_after_request=ufs_write_out=ufs_write_was_native=ufs_native_on=ufs_write_busy=ufs_data_used=false;
 memset(args,0,sizeof(args));args[0]=(struct argument){"spacewar_install","1"};args[1]=(struct argument){"spacewar_storage","1"};args[2]=(struct argument){"ufs","2"};args[3]=(struct argument){"ufs_write","1"};args[4]=(struct argument){"var_hfs","ufs_darwin_data"};board_spacewar=true;
}
static void rejected(void){
 struct sw_install_cap unused;
 assert(ufs_install_offer(&page,ufs_parts,&unused)<0);
 assert(ufs_write(BLOCK_OP_WRITE,1,0,buffer,4096)==EROFS);assert(!requests);cases++;
}
int main(void){
 reset();assert(offsetof(struct loader_block,install_cap)==48&&offsetof(struct loader_block,ask)==128);cases++;
 assert(ufs_install_profile());cases++;
 for(unsigned i=0;i<4;i++){reset();args[i].value=i==2?"1":"0";assert(!ufs_install_profile());cases++;}
 reset();args[4].value="hakios_data";assert(!ufs_install_profile());cases++;
 reset();args[5]=(struct argument){"ufs_native","0"};assert(!ufs_install_profile());cases++;
 reset();args[5]=(struct argument){"var_store","0"};assert(!ufs_install_profile());cases++;
 reset();board_spacewar=false;assert(!ufs_install_profile());cases++;
 for(unsigned variant=0;variant<34;variant++){
  reset();switch(variant){
  case 0:page.install_cap.magic^=1;break;
  case 1:memset(page.install_cap.disk_guid,0,16);break;
  case 2:memset(page.install_cap.data_guid,0,16);break;
  case 3:page.install_cap.lun=0;break;
  case 4:page.install_cap.block_size=512;break;
  case 5:page.install_cap.first_lba=0;break;
  case 6:page.install_cap.blocks=0;break;
  case 7:page.install_cap.first_lba=UINT64_MAX;break;
  case 8:page.install_cap.blocks=UINT64_MAX;break;
  case 9:page.write_part_plus_one=0;break;
  case 10:page.write_part_plus_one=5;break;
  case 11:page.write_first=UFS_WRITE_FIRST;break;
  case 12:page.buffer_size=0;break;
  case 13:page.part[1].unit=0;break;
  case 14:page.part[1].lun=0xff;break;
  case 15:strcpy(page.part[1].name,"darwin");break;
  case 16:strcpy(page.part[1].name,"hakios_dataX");break;
  case 17:page.part[1].start_lba++;break;
  case 18:page.part[1].blocks++;break;
  case 19:page.part[1].flags=BLOCK_PART_UNIT;break;
  case 20:page.part[1].block_size=512;break;
  case 21:page.part[2].lun=0xff;break;
  case 22:page.part[2].blocks=755360+262144-1;break;
  case 23:page.part[2].start_lba=1;break;
  case 24:page.part[3]=page.part[2];break;
  case 25:strcpy(page.part[3].name,"hakios_data");break;
  case 26:page.part[0].blocks++;break;
  case 27:page.magic^=1;break;
  case 28:page.size--;break;
  case 29:page.parts=BLOCK_MAX_PARTS+1;break;
  case 30:page.install_cap.first_lba=UINT64_MAX/4096;page.part[1].start_lba=UINT64_MAX/4096;page.part[2].blocks=UINT64_MAX;break;
  case 31:page.part[2].blocks=UINT64_MAX;break;
  case 32:page.part[0].lun=0xff;break;
  case 33:page.part[0].flags=2;break;
  }rejected();
 }
 reset();assert(!ufs_write(BLOCK_OP_WRITE,1,0,buffer,8192));assert(requests==2&&sent[0].part==1&&sent[0].lba==0&&sent[1].lba==1);cases++;
 for(unsigned part=0;part<6;part++)if(part!=1){reset();assert(ufs_write(BLOCK_OP_WRITE,part,0,buffer,4096)==EROFS&&!requests);cases++;}
 reset();assert(ufs_write(BLOCK_OP_WRITE_FAST,1,0,buffer,4096)==EROFS&&!requests);cases++;
 reset();assert(ufs_write(BLOCK_OP_FLUSH,1,0,buffer,4096)==EROFS&&!requests);cases++;
 reset();assert(ufs_write(BLOCK_OP_WRITE,1,262144ULL*4096-4096,buffer,4096)==0&&requests==1);cases++;
 reset();assert(ufs_write(BLOCK_OP_WRITE,1,262144ULL*4096,buffer,4096)==EROFS&&!requests);cases++;
 reset();assert(ufs_write(BLOCK_OP_WRITE,1,UINT64_MAX-4095,buffer,8192)==EROFS&&!requests);cases++;
 reset();assert(ufs_write(BLOCK_OP_WRITE,1,1,buffer,4096)==EINVAL&&!requests);cases++;
 reset();assert(ufs_data_bytes()==262144ULL*4096&&ufs_data_op()==BLOCK_OP_WRITE);assert(!ufs_data_io(true,17,buffer,1024));assert(reads==1&&requests==1&&sent[0].lba==0&&sent[0].part==1);cases++;
 reset();assert(ufs_copen(1,FWRITE,0,NULL)==EROFS&&!requests);cases++;
 reset();mutate_after_request=true;assert(ufs_write(BLOCK_OP_WRITE,1,0,buffer,8192)==EROFS&&requests==1);cases++;
 reset();page.install_cap.disk_guid[0]^=1;assert(ufs_write(BLOCK_OP_WRITE,1,0,buffer,4096)==EROFS&&!requests);cases++;
 /* Legacy floor and fast-mode selection remain intact. */
 reset();ufs_install_requested=ufs_install_enabled=false;ufs_write_first=UFS_WRITE_FIRST;page.part[1].blocks=UFS_DARWIN_MIN;
 assert(ufs_write(BLOCK_OP_WRITE,1,0,buffer,4096)==EROFS&&!requests);
 assert(!ufs_write(BLOCK_OP_WRITE,1,UFS_WRITE_FIRST*4096,buffer,4096)&&requests==1);
 assert(ufs_data_op()==BLOCK_OP_WRITE_FAST);assert(ufs_data_bytes()==(UFS_DARWIN_MIN-UFS_WRITE_FIRST)*4096);cases++;
 printf("PASS: %u actual installed-storage helper cases; ABI/profile/capability/physical unit/bounds; only data minor writes; per-chunk revalidation; legacy preserved\n",cases);return 0;
}
'''

def function(source, name):
    match = re.search(r'^static (?:bool|int|uint64_t|uint32_t)\s+' + re.escape(name) + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise ValueError(name)
    return source[match.start():source.index('\n}', match.end()) + 2] + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'drivers/storage/hakios.ufs.c')
    args = parser.parse_args()
    source = args.source.read_text()
    structures = source[source.index('#define BLOCK_MAGIC'):source.index('#define UFS_PAGE')]
    names = ('ufs_install_profile','ufs_install_guid_present','ufs_install_offer','ufs_install_still_valid',
             'ufs_part_bytes','ufs_write_bounds','ufs_write','ufs_data_bytes','ufs_data_op','ufs_data_io','ufs_copen')
    with tempfile.TemporaryDirectory(prefix='spacewar-ufs-install-') as temporary:
        work = Path(temporary)
        program = work/'test.c'
        header = ROOT/'drivers/storage/hakios.spacewar.install.h'
        program.write_text(STUBS+'\n#include <stddef.h>\n#include "'+str(header)+'"\n'+structures+GLOBALS+
                           ''.join(function(source,name) for name in names)+CASES)
        compiler = shlex.split(os.environ.get('HOSTCC','cc'))
        subprocess.run(compiler+['-std=gnu11','-g','-O1','-fsanitize=address,undefined','-fno-sanitize-recover=all',
                                str(program),'-o',str(work/'test')],check=True)
        subprocess.run([str(work/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})


if __name__=='__main__':
    main()
