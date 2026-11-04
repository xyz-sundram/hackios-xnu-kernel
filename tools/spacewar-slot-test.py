#!/usr/bin/env python3
"""Run Phone (1)'s exact U-Boot GPT handoff command against an in-memory disk.

No phone commands. Exercises backup-first writes, both CRCs, byte preservation,
identity/active-slot gates, corrupt/mismatched GPTs and failed-I/O behavior.
"""
from pathlib import Path
import subprocess
import os
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STUB = r'''
#ifndef TEST_STUB_H
#define TEST_STUB_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
typedef unsigned long ulong;typedef uint64_t lbaint_t;
#define ARCH_DMA_MINALIGN 64
#define DIV_ROUND_UP(x,y) (((x)+(y)-1)/(y))
#define UUID_STR_LEN 36
#define UUID_STR_FORMAT_GUID 1
#define UCLASS_SCSI 1
#define CMD_RET_SUCCESS 0
#define CMD_RET_FAILURE 1
#define CMD_RET_USAGE 2
#define U_BOOT_CMD(...)
#define DECLARE_GLOBAL_DATA_PTR
struct global_data {void *fdt_blob;};
static struct global_data test_gd;
static struct global_data *gd=&test_gd;
struct cmd_tbl {int dummy;};
struct blk_desc {ulong blksz;lbaint_t lba;};
static u8 disk[4096*64],original[4096*64];
static struct blk_desc desc={4096,64};
static int writes,fail_write,compat,installed,invalid_reads,invalid_writes;
static bool disk_identity,boot_identity,android_identity;
static const char *hakios_slot;
static lbaint_t write_lbas[16];
static void *memalign(size_t a,size_t n) {void *p=NULL;return posix_memalign(&p,a,n)?NULL:p;}
static u32 crc32(u32 crc,const u8 *p,unsigned n) {crc=~crc;while(n--){crc^=*p++;for(int i=0;i<8;i++)crc=(crc>>1)^(0xedb88320u&-(crc&1));}return ~crc;}
static ulong blk_dread(struct blk_desc *d,lbaint_t first,ulong count,void *out) {if(first>d->lba || count>d->lba-first){invalid_reads++;return 0;}memcpy(out,disk+first*4096,count*4096);return count;}
static ulong blk_dwrite(struct blk_desc *d,lbaint_t first,ulong count,const void *in) {writes++;write_lbas[writes-1]=first;if(writes==fail_write)return 0;if(first>d->lba || count>d->lba-first){invalid_writes++;return 0;}memcpy(disk+first*4096,in,count*4096);return count;}
static int blk_find_max_devnum(int c) {(void)c;return 0;}
static struct blk_desc *blk_get_devnum_by_uclass_id(int c,int n) {(void)c;(void)n;return &desc;}
static int fdt_node_check_compatible(const void *f,int node,const char *name) {(void)f;(void)node;assert(!strcmp(name,"nothing,spacewar"));return compat;}
static int env_get_yesno(const char *name) {assert(!strcmp(name,"spacewar_install"));return installed;}
static const char *env_get(const char *name) {
 if(!strcmp(name,"spacewar_hakios_slot"))return hakios_slot;
 if(!strcmp(name,"spacewar_disk_guid"))return disk_identity?"disk":"wrong";
 bool b=hakios_slot&&!strcmp(hakios_slot,"b");
 if(!strcmp(name,"spacewar_boot_guid"))return boot_identity?(b?"boot-b":"boot-a"):NULL;
 assert(!strcmp(name,"spacewar_android_boot_guid"));
 return android_identity?(b?"boot-a":"boot-b"):NULL;
}
static void uuid_bin_to_str(const u8 *p,char *out,int format) {(void)format;strcpy(out,p[0]==0x11?"disk":p[0]==0x45?"boot-b":"boot-a");}
#endif
'''
CASES = r'''
static void put64(u8 *p,u64 v) {put32(p,v);put32(p+4,v>>32);}
static void entry(u8 *e,const char *name,int first,int last,u8 flags) {
 e[0]=0x33;e[16]=!strcmp(name,"boot_b")?0x45:0x44;put64(e+32,first);put64(e+40,last);e[ATTR_BYTE]=flags;
 for(int i=0;name[i];i++)e[56+2*i]=name[i];
}
static void reset_disk(void) {
 memset(disk,0,sizeof(disk));writes=fail_write=compat=invalid_reads=invalid_writes=0;installed=1;disk_identity=boot_identity=android_identity=true;hakios_slot="a";
 u8 *h=disk+4096,*t=disk+63*4096,*e=disk+2*4096;
 entry(e,"boot_a",3,10,0x3f);entry(e+128,"boot_b",11,18,0x40);entry(e+256,"other",19,61,0x12);
 memcpy(disk+62*4096,e,4096);
 memcpy(h,"EFI PART",8);put32(h+8,0x10000);put32(h+12,92);put64(h+24,1);put64(h+32,63);
 put64(h+40,3);put64(h+48,61);h[56]=0x11;put64(h+72,2);put32(h+80,32);put32(h+84,128);seal(h,e,4096);
 memcpy(t,h,4096);put64(t+24,63);put64(t+32,1);put64(t+72,62);seal(t,e,4096);
 memcpy(original,disk,sizeof(disk));
}
static int command(const char *op) {char *argv[]={"spacewar","slot",(char *)op};return do_spacewar(NULL,0,3,argv);}
static void update_flags(int a,int b) {disk[2*4096+54]=a;disk[2*4096+128+54]=b;memcpy(disk+62*4096,disk+2*4096,4096);seal(disk+4096,disk+2*4096,4096);seal(disk+63*4096,disk+62*4096,4096);memcpy(original,disk,sizeof(disk));}
static void refused(void) {assert(command("successful")!=0);assert(writes==0);assert(invalid_reads==0&&invalid_writes==0);assert(!memcmp(disk,original,sizeof(disk)));}
static void preserved(void) {
 size_t selected=hakios_slot&&!strcmp(hakios_slot,"b")?128:0;
 for(size_t i=0;i<sizeof(disk);i++) {
  bool allowed=i==2*4096+selected+54 || i==62*4096+selected+54 ||
   (i>=4096+16&&i<4096+20)||(i>=4096+88&&i<4096+92)||
   (i>=63*4096+16&&i<63*4096+20)||(i>=63*4096+88&&i<63*4096+92);
  if(!allowed)assert(disk[i]==original[i]);
 }
 struct table t;assert(read_table(&t,&desc)==0);release_table(&t);
}
int main(void) {
 assert(crc32(0,(const u8 *)"123456789",9)==0xcbf43926);
 reset_disk();assert(command("inspect")==0);assert(!writes);assert(!memcmp(disk,original,sizeof(disk)));
 reset_disk();assert(command("successful")==0);assert(disk[2*4096+54]==0x7f);assert(writes==4);assert(write_lbas[0]==62&&write_lbas[1]==63&&write_lbas[2]==2&&write_lbas[3]==1);preserved();
 reset_disk();assert(command("unbootable")==0);assert(disk[2*4096+54]==0xbf);preserved();
 reset_disk();update_flags(0x7f,0x40);assert(command("successful")==0);assert(!writes);
 reset_disk();installed=0;refused();reset_disk();installed=-1;refused();
 reset_disk();disk_identity=false;refused();reset_disk();boot_identity=false;refused();
 reset_disk();compat=1;refused();
 reset_disk();update_flags(0x3b,0x40);refused();reset_disk();update_flags(0x3f,0x44);refused();
 reset_disk();update_flags(0x3f,0xc0);refused();reset_disk();update_flags(0x3f,0);refused();
 reset_disk();disk[4096+16]^=1;memcpy(original,disk,sizeof(disk));refused();
 reset_disk();disk[62*4096+256+54]^=1;memcpy(original,disk,sizeof(disk));refused();
 reset_disk();disk[2*4096+256+54]^=1;disk[62*4096+256+54]^=1;memcpy(original,disk,sizeof(disk));refused();
 reset_disk();put32(disk+4096+80,257);seal(disk+4096,disk+2*4096,4096);memcpy(original,disk,sizeof(disk));refused();
 /* CRC-valid GPT headers must not trigger even a read outside the disk.
  * The UINT64_MAX cases wrapped the old `array_lba + sectors` tests. */
 reset_disk();put64(disk+4096+72,UINT64_MAX);seal(disk+4096,disk+2*4096,4096);memcpy(original,disk,sizeof(disk));refused();
 reset_disk();put64(disk+63*4096+72,UINT64_MAX);seal(disk+63*4096,disk+62*4096,4096);memcpy(original,disk,sizeof(disk));refused();
 reset_disk();put64(disk+4096+72,desc.lba);seal(disk+4096,disk+2*4096,4096);memcpy(original,disk,sizeof(disk));refused();
 reset_disk();put64(disk+63*4096+72,desc.lba);seal(disk+63*4096,disk+62*4096,4096);memcpy(original,disk,sizeof(disk));refused();
 reset_disk();put64(disk+4096+72,3);seal(disk+4096,disk+2*4096,4096);memcpy(original,disk,sizeof(disk));refused();
 reset_disk();fail_write=1;assert(command("successful")!=0);assert(writes==1);assert(!memcmp(disk,original,sizeof(disk)));
 reset_disk();fail_write=2;assert(command("successful")!=0);assert(writes==2);assert(!memcmp(disk,original,62*4096));
 /* Android A / HakiOS B: only B's attributes may change. */
 reset_disk();hakios_slot="b";update_flags(0x40,0x3f);assert(command("successful")==0);assert(disk[2*4096+128+54]==0x7f);assert(writes==4);preserved();
 reset_disk();hakios_slot="b";update_flags(0x40,0x3f);assert(command("unbootable")==0);assert(disk[2*4096+128+54]==0xbf);preserved();
 reset_disk();hakios_slot="b";update_flags(0x40,0x7f);assert(command("successful")==0);assert(!writes);
 /* No implicit role, fallback identity, or active-slot reassignment. */
 const char *bad_slots[]={NULL,"","c","B","boot_b"};
 for(unsigned i=0;i<sizeof(bad_slots)/sizeof(bad_slots[0]);i++){reset_disk();hakios_slot=bad_slots[i];refused();}
 reset_disk();android_identity=false;refused();
 reset_disk();hakios_slot="b";refused();
 reset_disk();hakios_slot="b";update_flags(0x44,0x3f);refused();
 reset_disk();hakios_slot="b";update_flags(0xc0,0x3f);refused();
 reset_disk();hakios_slot="b";update_flags(0,0x3f);refused();
 reset_disk();hakios_slot="b";update_flags(0x40,0x3b);refused();
 reset_disk();disk[2*4096+128+16]=0x44;update_flags(0x3f,0x40);refused();
 reset_disk();hakios_slot="b";update_flags(0x40,0x3f);installed=0;refused();
 reset_disk();hakios_slot="b";update_flags(0x40,0x3f);fail_write=1;assert(command("successful")!=0);assert(writes==1);assert(!memcmp(disk,original,sizeof(disk)));
 puts("spacewar-slot-test: both slot roles, GPT integrity, byte preservation and zero-write refusal cases passed; zero phone commands");
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='spacewar-slot-test-') as tmp:
        tmp = Path(tmp)
        (tmp/'stub.h').write_text(STUB)
        for header in ['command.h','env.h','malloc.h','part.h','part_efi.h','dm.h',
                       'dm/ofnode.h','asm/cache.h','asm/global_data.h','linux/libfdt.h','u-boot/crc.h','u-boot/uuid.h']:
            p=tmp/header;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#include "stub.h"\n')
        source=ROOT/'devices/spacewar/u-boot/files/cmd/spacewar.c'
        (tmp/'test.c').write_text(f'#include "{source}"\n'+CASES)
        subprocess.run(['cc','-std=gnu11','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
                        '-I',str(tmp),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
        subprocess.run([str(tmp/'test')],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})


if __name__ == '__main__':
    main()
