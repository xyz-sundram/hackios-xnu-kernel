// SPDX-License-Identifier: GPL-2.0+
/* Phone (1) installed-boot handoff. Never selects another active slot:
 * Qualcomm ABL must move the GUIDs, slot attributes and UFS boot LUN together.
 * Only the current boot_a entry's successful/unbootable bits are changed.
 * Attribute byte layout: hardware/qcom/bootctrl gpt-utils.h (AB_FLAG_OFFSET),
 * https://github.com/LineageOS/android_hardware_qcom_bootctrl/tree/lineage-24.0-caf
 */
#include <command.h>
#include <env.h>
#include <malloc.h>
#include <part.h>
#include <part_efi.h>
#include <dm.h>
#include <dm/ofnode.h>
#include <asm/cache.h>
#include <asm/global_data.h>
#include <linux/libfdt.h>
#include <u-boot/crc.h>
#include <u-boot/uuid.h>
DECLARE_GLOBAL_DATA_PTR;

#define SLOT_ACTIVE 0x04
#define SLOT_SUCCESSFUL 0x40
#define SLOT_UNBOOTABLE 0x80
#define ATTR_BYTE 54 /* GPT attributes start at 48; Qualcomm's A/B flags at +6 */
#define MAX_ENTRIES 256

struct table {
	struct blk_desc *disk;
	u8 *head, *tail, *entries, *backup;
	ulong bytes, sectors;
	lbaint_t first, last;
	int a, b;
};

static u32 get32(const u8 *p) { return (u32)p[0] | (u32)p[1]<<8 | (u32)p[2]<<16 | (u32)p[3]<<24; }
static u64 get64(const u8 *p) { return (u64)get32(p) | (u64)get32(p+4)<<32; }
static void put32(u8 *p, u32 v) { for (int i=0;i<4;i++) p[i]=v>>(8*i); }

static bool valid_header(u8 *h, struct blk_desc *d, lbaint_t here, lbaint_t other)
{
	u32 saved, actual, size = get32(h+12);
	if (memcmp(h,"EFI PART",8) || get32(h+8)!=0x10000 || size<92 || size>d->blksz ||
	    get64(h+24)!=here || get64(h+32)!=other || get64(h+40)>get64(h+48) ||
	    get64(h+48)>=d->lba-1 || get32(h+84)!=128 ||
	    !get32(h+80) || get32(h+80)>MAX_ENTRIES) return false;
	saved=get32(h+16);put32(h+16,0);actual=crc32(0,h,size);put32(h+16,saved);
	return saved==actual;
}

static bool named(const u8 *e, const char *name)
{
	for (unsigned i=0;i<36;i++) {
		if (e[56+2*i]!=(u8)*name || e[57+2*i]) return false;
		if (!*name) return true;
		name++;
	}
	return false;
}

static void release_table(struct table *t)
{
	free(t->head);free(t->tail);free(t->entries);free(t->backup);
	memset(t,0,sizeof(*t));
}

static int read_table(struct table *t, struct blk_desc *d)
{
	memset(t,0,sizeof(*t));t->disk=d;t->a=t->b=-1;
	if (d->blksz!=4096 || d->lba<32) return -1;
	t->head=memalign(ARCH_DMA_MINALIGN,d->blksz);
	t->tail=memalign(ARCH_DMA_MINALIGN,d->blksz);
	if (!t->head || !t->tail || blk_dread(d,1,1,t->head)!=1 ||
	    blk_dread(d,d->lba-1,1,t->tail)!=1 ||
	    !valid_header(t->head,d,1,d->lba-1) || !valid_header(t->tail,d,d->lba-1,1)) goto fail;
	if (memcmp(t->head+40,t->tail+40,32) || memcmp(t->head+80,t->tail+80,12)) goto fail;
	t->bytes=get32(t->head+80)*128;t->sectors=DIV_ROUND_UP(t->bytes,d->blksz);
	t->first=get64(t->head+72);t->last=get64(t->tail+72);
	/* Check each start before subtracting. Summing a malicious array LBA
	 * with its sector count can wrap and otherwise pass these bounds. */
	u64 usable_first=get64(t->head+40),usable_last=get64(t->head+48);
	if (t->first<2 || t->first>usable_first || t->sectors>usable_first-t->first ||
	    t->last<=usable_last || t->last>=d->lba-1 || t->sectors>d->lba-1-t->last) goto fail;
	t->entries=memalign(ARCH_DMA_MINALIGN,t->sectors*d->blksz);
	t->backup=memalign(ARCH_DMA_MINALIGN,t->sectors*d->blksz);
	if (!t->entries || !t->backup || blk_dread(d,t->first,t->sectors,t->entries)!=t->sectors ||
	    blk_dread(d,t->last,t->sectors,t->backup)!=t->sectors ||
	    memcmp(t->entries,t->backup,t->bytes) ||
	    crc32(0,t->entries,t->bytes)!=get32(t->head+88)) goto fail;
	for (unsigned i=0;i<t->bytes/128;i++) {
		const u8 *e=t->entries+128*i;
		if (named(e,"boot_a")) { if (t->a>=0) goto fail;t->a=i; }
		if (named(e,"boot_b")) { if (t->b>=0) goto fail;t->b=i; }
	}
	if (t->a<0 || t->b<0) goto fail;
	return 0;
fail:
	release_table(t);return -1;
}

static void seal(u8 *header, const u8 *entries, ulong size)
{
	put32(header+88,crc32(0,entries,size));put32(header+16,0);
	put32(header+16,crc32(0,header,get32(header+12)));
}

static bool guid_matches(const u8 *guid, const char *env_name)
{
	char text[UUID_STR_LEN+1];const char *wanted=env_get(env_name);
	uuid_bin_to_str(guid,text,UUID_STR_FORMAT_GUID);
	return wanted && !strcmp(wanted,text);
}

static int write_copy(struct table *t, bool backup)
{
	struct blk_desc *d=t->disk;
	lbaint_t entries=backup?t->last:t->first, hdr=backup?d->lba-1:1;
	u8 *h=backup?t->tail:t->head;
	u8 *data=backup?t->backup:t->entries;
	/* Write the backup first. Stop on any I/O failure; never reset into ABL
	 * with a write that has not been read back and compared byte-for-byte. */
	u8 *verify=memalign(ARCH_DMA_MINALIGN,t->sectors*d->blksz);
	if (!verify) return -1;
	if (blk_dwrite(d,entries,t->sectors,data)!=t->sectors ||
	    blk_dwrite(d,hdr,1,h)!=1 ||
	    blk_dread(d,entries,t->sectors,verify)!=t->sectors ||
	    memcmp(data,verify,t->sectors*d->blksz)) {free(verify);return -1;}
	int ret=blk_dread(d,hdr,1,verify)==1 && !memcmp(h,verify,d->blksz)?0:-1;
	free(verify);return ret;
}

static int do_spacewar(struct cmd_tbl *cmdtp, int flag, int argc, char *const argv[])
{
	struct table t;
	bool inspect=argc==3 && !strcmp(argv[1],"slot") && !strcmp(argv[2],"inspect");
	bool success=argc==3 && !strcmp(argv[1],"slot") && !strcmp(argv[2],"successful");
	bool android=argc==3 && !strcmp(argv[1],"slot") && !strcmp(argv[2],"unbootable");
	if ((!inspect && !success && !android) ||
	    fdt_node_check_compatible(gd->fdt_blob,0,"nothing,spacewar")) return CMD_RET_USAGE;
	for (int dev=0;dev<=blk_find_max_devnum(UCLASS_SCSI);dev++) {
		struct blk_desc *d=blk_get_devnum_by_uclass_id(UCLASS_SCSI,dev);
		if (!d || read_table(&t,d)) continue;
		u8 *a=t.entries+128*t.a,*b=t.entries+128*t.b;
		printf("Phone (1): scsi %d boot_a flags=%02x boot_b flags=%02x\n",dev,a[ATTR_BYTE],b[ATTR_BYTE]);
		if (inspect) {release_table(&t);return CMD_RET_SUCCESS;}
		if (env_get_yesno("spacewar_install")!=1 ||
		    !guid_matches(t.head+56,"spacewar_disk_guid") || !guid_matches(a+16,"spacewar_boot_guid") ||
		    !(a[ATTR_BYTE]&SLOT_ACTIVE) || b[ATTR_BYTE]&SLOT_ACTIVE ||
		    b[ATTR_BYTE]&SLOT_UNBOOTABLE || !(b[ATTR_BYTE]&(SLOT_SUCCESSFUL|0x38))) {
			printf("Phone (1): install identity/current-slot/Android fallback gate failed; no write\n");
			release_table(&t);return CMD_RET_FAILURE;
		}
		u8 updated=a[ATTR_BYTE] | (success?SLOT_SUCCESSFUL:SLOT_UNBOOTABLE);
		if (updated==a[ATTR_BYTE]) {release_table(&t);return CMD_RET_SUCCESS;}
		a[ATTR_BYTE]=updated;
		t.backup[128*t.a+ATTR_BYTE]=updated;
		seal(t.head,t.entries,t.bytes);seal(t.tail,t.entries,t.bytes);
		int ret=write_copy(&t,true) || write_copy(&t,false);
		release_table(&t);
		if (ret) printf("Phone (1): GPT update/readback failed; remain here for recovery\n");
		return ret?CMD_RET_FAILURE:CMD_RET_SUCCESS;
	}
	printf("Phone (1): no matching validated boot GPT; no write\n");
	return CMD_RET_FAILURE;
}
U_BOOT_CMD(spacewar,3,0,do_spacewar,"Phone (1) current-slot state only",
	"slot inspect|successful|unbootable\n"
	"  successful/unbootable require the private installed-image identity;\n"
	"  never changes active slots, GUIDs, partition bounds or the UFS boot LUN");
