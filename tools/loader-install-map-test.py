#!/usr/bin/env python3
"""Exercise installed-map/GPT/preload/capability production helpers on fake EFI disks."""
from pathlib import Path
import os
import subprocess
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'xnuloader/loader.c').read_text()
crc = source[source.index('static uint32_t\nufs_crc32_update('):source.index('/*\n * A unit\'s GPT checks itself:')]
ufs_name = source[source.index('static void\nufs_name('):source.index('/* "bluetooth_a"')]
constants = source[source.index('#define BLOCK_MAGIC'):source.index('struct loader_block_part {')]
abi = source[source.index('struct loader_block_part {'):source.index('/* (8 KB is as far as this target aligns;')]
paths = source[source.index('static uint64_t\ndevice_path_bytes('):source.index('/* Private installed-image identity.')]
install = source[source.index('static int\ninstall_zero('):source.index('/*\n * The UFS host controller,')]
legacy = source[source.index('static int\nblock_is_darwin('):source.index('static void\nblock_start(')]
block_start = source[source.index('static void\nblock_start('):source.index('/*\n * Whether the storage could have more')]
profile = source[source.index('static int\ntouch_token_is('):source.index('static int\nrun_kernel(')]
payload_types = source[source.index('struct payload_header {'):source.index('#define KERNEL_VIRT_BASE')]
payload = source[source.index('struct payload_info {'):source.index('/* NVRAM image')]
dispatch = source[source.index('static void\npayload_boot_dispatch('):source.index('efi_status EFIAPI\nefi_main(')]
mailbox = source[source.index('static void\nblock_poll('):source.index('/* One synchronous mailbox operation')]
mailbox = mailbox.replace('__asm__ volatile ("dmb sy");', '__asm__ volatile ("" ::: "memory");')
ROOT_BYTES = 2 * 1024 * 1024 + 123
ROOT_CRC = zlib.crc32(bytes(i % 251 for i in range(ROOT_BYTES)))

PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "efi.h"
#include "hakios.spacewar.install.h"
#define BLOCK_BUFFER_SIZE (4u << 20)
#define PAYLOAD_MAGIC 0x50554e58U
#define PAYLOAD_MAX_SIZE 0x04000000ULL
static uint8_t payload_memory[4096];
#define PAYLOAD_BASE ((uintptr_t)payload_memory)
'''

STUBS = r'''
#define BSIZE 4096U
#define DISK_BLOCKS 1024U
#define ROOT_FIRST 10U
#define ROOT_BLOCKS ((ROOT_BYTES + BSIZE - 1) / BSIZE)
#define DATA_FIRST 600U
#define DATA_BLOCKS 256U
static uint8_t disk[DISK_BLOCKS * BSIZE], resident[ROOT_BYTES + 4096];
static uint8_t ufs_buffer[1024 * 1024];
struct handle {efi_block_io bio; efi_block_media media; efi_partition_info info; uint8_t path[64]; int partition;};
static struct handle raw, root, data, duplicate;
static struct sw_install_map install_identity;
static int install_active;
static efi_block_io *install_unit_bio, *install_root_bio, *install_data_bio;
static const efi_device_path *install_unit_path;
static uint64_t install_unit_path_bytes;
static struct loader_block block_shared;
static efi_block_io *block_bios[BLOCK_MAX_PARTS];
static int block_enabled, block_probe_requested, block_write_requested, block_write_part;
static uint64_t block_write_first, block_writes, block_writes_refused, block_write_bytes;
static uint8_t block_buffer[BLOCK_BUFFER_SIZE], hc_area[4096];
static int hc_known, hc_requested, hc_refused_reads, hc_log_skips;
static uint32_t hc_uefi_lo, hc_uefi_hi;
#define HC_UTRLDBR 0x58
#define HC_HCE 0x34
#define HC_PA 0x1d84000
#define HC_UCD_OFFSET 0x1000
#define HC_UCD_SIZE 0x800
#define HC_SLOTS 32
static unsigned reads, root_reads, data_reads, writes, flushes, invalid_reads, fail_root_read;
static bool duplicate_raw, duplicate_root;
static bool flush_fails;
static bool legacy_sparse;
static int wdt_armed;
static uint64_t ticks;
#define READ_SYSREG(name) (++ticks)
static char dispatch_trace[32];
static bool usb_start_result, kernel_start_result;
static const char *dispatch_result;
static unsigned dispatch_logs_composed;
static void *USB;
static void dispatch_note(char c) {size_t n = strlen(dispatch_trace); assert(n + 1 < sizeof(dispatch_trace)); dispatch_trace[n] = c;}
static void set_result(const char *state, uint64_t checkpoint, uint64_t value) {
    assert(!checkpoint && !value);
    dispatch_result = state;
    dispatch_note(!strcmp(state, "running") ? 'R' : !strcmp(state, "not-started") ? 'N' : '?');
}
static void compose_logs(const void *live, uint64_t slide) {
    assert(!live && !slide && !strcmp(dispatch_result, "not-started"));
    dispatch_logs_composed++; dispatch_note('C');
}
static int usb_start(void) {dispatch_note('S'); USB = (void *)1; return usb_start_result;}
static void flush_log(void) {dispatch_note('F');}
static int run_kernel(int have_checkpoint, uint64_t checkpoint) {
    assert(!have_checkpoint && !checkpoint && !strcmp(dispatch_result, "running")); dispatch_note('K');
    if (!usb_start_result) assert(!USB);
    if (kernel_start_result) dispatch_result = "returned";
    return kernel_start_result;
}
static void usb_command_loop(void) {dispatch_note('L');}
static void print(const char *format, ...) {(void)format;}
static void flush_dcache(uint64_t first, uint64_t last) {assert(last > first);}
static void clean_dcache(uint64_t first, uint64_t last) {assert(last > first);}
static void wdt_pet(void) {}
static size_t str_len(const char *s) {return strlen(s);}
static int str_eq(const char *a, const char *b) {return !strcmp(a, b);}
static void block_tables(void) {}
static void block_io2_probe(efi_handle h, efi_block_io *bio) {(void)h; (void)bio; assert(0);}
static int hc_list_is_uefis(void) {return 1;}
static uint32_t hc_rd(uint32_t reg) {(void)reg; return 0;}
static efi_status read_blocks(efi_block_io *bio, uint32_t id, uint64_t lba, uint64_t bytes, void *out) {
    struct handle *h = (struct handle *)bio;
    reads++; assert(id == h->media.media_id && bytes && bytes % BSIZE == 0);
    if (lba > h->media.last_block || bytes / BSIZE > h->media.last_block - lba + 1) {invalid_reads++; return EFI_ERROR_BIT;}
    uint64_t at = lba + (h->partition ? h->info.start_lba : 0);
    assert(at < DISK_BLOCKS && bytes / BSIZE <= DISK_BLOCKS - at);
    if (h == &root && ++root_reads == fail_root_read) {
        memcpy(out, disk + at * BSIZE, (size_t)bytes / 2); return EFI_ERROR_BIT;
    }
    if (h == &data) data_reads++;
    memcpy(out, disk + at * BSIZE, (size_t)bytes); return 0;
}
static efi_status write_blocks(efi_block_io *bio, uint32_t id, uint64_t lba, uint64_t bytes, void *in) {
    struct handle *h = (struct handle *)bio; writes++;
    if (legacy_sparse) {assert(lba >= BLOCK_WRITE_FIRST); lba -= BLOCK_WRITE_FIRST;}
    assert(h == &data && id == h->media.media_id && lba < DATA_BLOCKS && bytes / BSIZE <= DATA_BLOCKS - lba);
    memcpy(disk + (h->info.start_lba + lba) * BSIZE, in, (size_t)bytes); return 0;
}
static efi_status flush_blocks(efi_block_io *bio) {assert(bio == &data.bio); flushes++; return flush_fails ? EFI_ERROR_BIT : 0;}
static efi_status locate(int type, const efi_guid *guid, void *key, uint64_t *count, efi_handle **out) {
    static const efi_guid block = EFI_BLOCK_IO_GUID, part = EFI_PARTITION_INFO_GUID;
    (void)key; assert(type == ByProtocol);
    *out = malloc(4 * sizeof(efi_handle)); assert(*out);
    if (!memcmp(guid, &block, 16)) {
        (*out)[0] = &root; (*out)[1] = &data; (*out)[2] = &raw; *count = 3;
        if (duplicate_raw) (*out)[(*count)++] = &duplicate;
    } else {
        assert(!memcmp(guid, &part, 16)); (*out)[0] = &root; (*out)[1] = &data; *count = 2;
        if (duplicate_root) (*out)[(*count)++] = &root;
    }
    return 0;
}
static efi_status protocol(efi_handle handle, const efi_guid *guid, void **out) {
    static const efi_guid block = EFI_BLOCK_IO_GUID, part = EFI_PARTITION_INFO_GUID, path = EFI_DEVICE_PATH_GUID;
    struct handle *h = handle;
    if (!memcmp(guid, &block, 16)) *out = &h->bio;
    else if (!memcmp(guid, &path, 16)) *out = h->path;
    else if (!memcmp(guid, &part, 16) && h->partition) *out = &h->info;
    else return EFI_ERROR_BIT;
    return 0;
}
static efi_status free_pool(void *p) {free(p); return 0;}
static efi_boot_services services = {.locate_handle_buffer = locate, .handle_protocol = protocol, .free_pool = free_pool};
static efi_boot_services *BS = &services;
'''

CASES = r'''
static void put32(uint8_t *p, uint32_t v) {for (unsigned i = 0; i < 4; i++) p[i] = v >> (8 * i);}
static void put64(uint8_t *p, uint64_t v) {put32(p, v); put32(p + 4, v >> 32);}
static void seal_header(uint8_t *h) {put32(h + 16, 0); put32(h + 16, ufs_crc32(h, install_u32(h + 12)));}
static void seal_tables(void) {
    memcpy(disk + 1022 * BSIZE, disk + 2 * BSIZE, BSIZE);
    put32(disk + BSIZE + 88, ufs_crc32(disk + 2 * BSIZE, BSIZE));
    put32(disk + 1023 * BSIZE + 88, ufs_crc32(disk + 2 * BSIZE, BSIZE));
    seal_header(disk + BSIZE); seal_header(disk + 1023 * BSIZE);
}
static void entry(uint8_t *e, const struct sw_install_extent *extent, const char *name) {
    memcpy(e, extent->type_guid, 16); memcpy(e + 16, extent->unique_guid, 16);
    put64(e + 32, extent->first_lba); put64(e + 40, extent->first_lba + extent->blocks - 1);
    for (unsigned i = 0; name[i]; i++) e[56 + i * 2] = name[i];
}
static void handle(struct handle *h, const struct sw_install_extent *extent, const char *name) {
    memset(h, 0, sizeof(*h)); h->partition = extent != NULL;
    h->media = (efi_block_media){.media_id = 7, .present = 1, .logical = extent != NULL, .block_size = BSIZE,
                                .io_align = 4096, .last_block = extent ? extent->blocks - 1 : DISK_BLOCKS - 1};
    h->bio = (efi_block_io){.media = &h->media, .read_blocks = read_blocks, .write_blocks = write_blocks, .flush_blocks = flush_blocks};
    const uint8_t scsi[] = {3, 2, 8, 0, 0x34, 0x12, 4, 0}; memcpy(h->path, scsi, 8);
    unsigned end = 8;
    if (extent) {
        h->info.type = 2; memcpy(&h->info.type_guid, extent->type_guid, 16); memcpy(&h->info.unique_guid, extent->unique_guid, 16);
        h->info.start_lba = extent->first_lba; h->info.end_lba = extent->first_lba + extent->blocks - 1;
        for (unsigned i = 0; name[i]; i++) h->info.name[i] = name[i];
        h->path[end] = 4; h->path[end + 1] = 1; h->path[end + 2] = 42; end += 42;
    }
    h->path[end] = 0x7f; h->path[end + 1] = 0xff; h->path[end + 2] = 4;
}
static struct sw_install_map reset(void) {
    static const efi_guid apfs = BLOCK_DARWIN_TYPE;
    static const efi_guid hfs = {0x48465300, 0, 0x11aa, {0xaa, 0x11, 0, 0x30, 0x65, 0x43, 0xec, 0xac}};
    struct sw_install_map map = {.magic = SW_INSTALL_MAGIC, .version = 1, .size = sizeof(map), .flags = 3,
                                .lun = 4, .block_size = BSIZE, .root_bytes = ROOT_BYTES, .root_crc32 = ROOT_CRC};
    map.disk_guid[0] = 0x11; map.root.unique_guid[0] = 0x22; map.data.unique_guid[0] = 0x33;
    memcpy(map.root.type_guid, &apfs, 16); memcpy(map.data.type_guid, &hfs, 16);
    map.root.first_lba = ROOT_FIRST; map.root.blocks = ROOT_BLOCKS;
    map.data.first_lba = DATA_FIRST; map.data.blocks = DATA_BLOCKS;
    memset(disk, 0, sizeof(disk)); memset(resident, 0xa5, sizeof(resident));
    for (unsigned i = 0; i < ROOT_BYTES; i++) disk[ROOT_FIRST * BSIZE + i] = i % 251;
    uint8_t *head = disk + BSIZE, *tail = disk + 1023 * BSIZE;
    memcpy(head, "EFI PART", 8); put32(head + 8, 0x10000); put32(head + 12, 92);
    put64(head + 24, 1); put64(head + 32, 1023); put64(head + 40, 3); put64(head + 48, 1021);
    memcpy(head + 56, map.disk_guid, 16); put64(head + 72, 2); put32(head + 80, 32); put32(head + 84, 128);
    memcpy(tail, head, BSIZE); put64(tail + 24, 1023); put64(tail + 32, 1); put64(tail + 72, 1022);
    entry(disk + 2 * BSIZE, &map.root, "hakios_system"); entry(disk + 2 * BSIZE + 128, &map.data, "hakios_data"); seal_tables();
    handle(&raw, NULL, ""); handle(&root, &map.root, "hakios_system"); handle(&data, &map.data, "hakios_data"); handle(&duplicate, NULL, "");
    reads = root_reads = data_reads = writes = flushes = invalid_reads = fail_root_read = 0;
    duplicate_raw = duplicate_root = flush_fails = legacy_sparse = false;
    install_active = block_enabled = block_probe_requested = block_write_requested = 0; block_write_part = -1;
    hc_known = hc_requested = hc_refused_reads = hc_log_skips = 0; install_unit_bio = install_root_bio = install_data_bio = NULL;
    return map;
}
static unsigned cases;
static void payload_entry(const char *name, const void *bytes, uint32_t size) {
    struct payload_header *head = (void *)payload_memory;
    struct payload_entry *entry = (void *)(payload_memory + sizeof(*head) + head->size);
    assert(head->size + sizeof(*entry) + size + 16 < sizeof(payload_memory));
    memset(entry, 0, sizeof(*entry)); strcpy(entry->name, name); entry->size = size;
    memcpy(entry + 1, bytes, size); head->size += sizeof(*entry) + (size + 15) / 16 * 16;
}
static void payload_reset(void) {
    memset(payload_memory, 0, sizeof(payload_memory));
    ((struct payload_header *)(void *)payload_memory)->magic = PAYLOAD_MAGIC;
}
#define BAD_MAP(statement) do {struct sw_install_map map = reset(), out; statement; assert(!install_map_decode((uint8_t *)&map, sizeof(map), &out)); assert(!writes && !flushes); cases++;} while (0)
#define BAD_DISK(statement) do {struct sw_install_map map = reset(); statement; assert(!install_prepare(&map)); assert(!writes && !flushes && !invalid_reads); cases++;} while (0)
int main(void) {
    assert(ufs_crc32((const uint8_t *)"123456789", 9) == 0xcbf43926);
    uint32_t c = ufs_crc32_update(0xffffffffu, (const uint8_t *)"1234", 4);
    assert(~ufs_crc32_update(c, (const uint8_t *)"56789", 5) == 0xcbf43926);
    struct sw_install_map map = reset(), out;
    struct payload_info pi;
    payload_reset(); payload_entry("install.map", &map, sizeof(map));
    assert(parse_payload(&pi, 0) && pi.have_install && !memcmp(&pi.install, &map, sizeof(map))); cases++;
    payload_entry("install.map", &map, sizeof(map)); assert(!parse_payload(&pi, 0)); cases++;
    payload_reset(); payload_entry("install.map", &map, sizeof(map) - 1); assert(!parse_payload(&pi, 0)); cases++;
    payload_reset(); payload_entry("install.map", &map, sizeof(map));
    ((struct payload_entry *)(void *)(payload_memory + sizeof(struct payload_header)))->reserved = 1;
    assert(!parse_payload(&pi, 0)); cases++;
    payload_reset(); payload_entry("install.map", &map, sizeof(map)); payload_entry("resident", "x", 1);
    assert(!parse_payload(&pi, 0)); cases++;
    payload_reset(); payload_entry("rdres", "1000", 5); payload_entry("install.map", &map, sizeof(map));
    assert(!parse_payload(&pi, 0)); cases++;
    payload_reset(); payload_entry("kernel", "x", 1); assert(parse_payload(&pi, 0) && !pi.have_install); cases++;
    payload_reset(); payload_entry("usb", "1", 2); assert(parse_payload(&pi, 0) && pi.usb_mode == 1); cases++;
    payload_reset(); payload_entry("usb", "2", 2); assert(!parse_payload(&pi, 0)); cases++;
    payload_entry("install.map", &map, sizeof(map)); assert(parse_payload(&pi, 0) && pi.usb_mode == 2); cases++;
    payload_entry("usb", "2", 2); assert(!parse_payload(&pi, 0)); cases++;
    const char *invalid_usb[] = {"", "0", "3", "11", "1x"};
    for (unsigned i = 0; i < sizeof(invalid_usb) / sizeof(invalid_usb[0]); i++) {
        payload_reset(); payload_entry("usb", invalid_usb[i], strlen(invalid_usb[i]) + 1);
        assert(!parse_payload(&pi, 0)); cases++;
    }
    payload_reset(); payload_entry("usb", "1", 1); assert(!parse_payload(&pi, 0)); cases++;
    for (unsigned mode = 0; mode <= 2; mode++) {
        for (unsigned available = 0; available <= 1; available++) {
            for (unsigned started = 0; started <= 1; started++) {
                memset(dispatch_trace, 0, sizeof(dispatch_trace)); USB = NULL; usb_start_result = available;
                kernel_start_result = started; dispatch_result = "idle"; dispatch_logs_composed = 0;
                memset(&pi, 0, sizeof(pi)); pi.usb_mode = mode; pi.have_install = mode == 2;
                payload_boot_dispatch(1, &pi);
                const char *expected = mode == 0 ? (started ? "RK" : "RKNC") : !available ? (started ? "SRK" : "SRKNC") :
                                       mode == 1 ? "SFL" : (started ? "SFRKL" : "SFRKNCL");
                bool ran = mode != 1 || !available;
                assert(!strcmp(dispatch_trace, expected));
                assert(!strcmp(dispatch_result, !ran ? "idle" : started ? "returned" : "not-started"));
                assert(dispatch_logs_composed == (unsigned)(ran && !started)); cases++;
            }
        }
    }
    memset(dispatch_trace, 0, sizeof(dispatch_trace)); dispatch_result = "idle"; dispatch_logs_composed = 0;
    payload_boot_dispatch(0, &pi); assert(!dispatch_trace[0] && !strcmp(dispatch_result, "idle") && !dispatch_logs_composed); cases++;
    assert(install_map_decode((uint8_t *)&map, sizeof(map), &out));
    assert(!install_map_decode((uint8_t *)&map, sizeof(map) - 1, &out)); cases += 3;
    BAD_MAP(map.magic++); BAD_MAP(map.version++); BAD_MAP(map.size--); BAD_MAP(map.flags = 0); BAD_MAP(map.flags = 2);
    BAD_MAP(map.flags = 7); BAD_MAP(map.reserved = 1); BAD_MAP(map.lun = 0); BAD_MAP(map.block_size = 512);
    BAD_MAP(memset(map.disk_guid, 0, 16)); BAD_MAP(memset(map.root.unique_guid, 0, 16)); BAD_MAP(map.root.type_guid[0]++);
    BAD_MAP(map.root.first_lba = 1); BAD_MAP(map.root.blocks = 0); BAD_MAP(map.root.blocks = UINT64_MAX);
    BAD_MAP(map.root.first_lba = UINT64_MAX); BAD_MAP(map.root_bytes = 0); BAD_MAP(map.root_bytes = map.root.blocks * BSIZE + 1);
    BAD_MAP(map.data.first_lba = ROOT_FIRST); BAD_MAP(memset(map.data.unique_guid, 0, 16)); BAD_MAP(map.data.type_guid[0]++);
    BAD_MAP(memcpy(map.data.unique_guid, map.root.unique_guid, 16)); BAD_MAP(map.flags = 1);
    map = reset(); map.flags = 1; memset(&map.data, 0, sizeof(map.data)); assert(install_prepare(&map)); cases++;
    BAD_DISK(disk[BSIZE]++); BAD_DISK(disk[1023 * BSIZE + 16]++); BAD_DISK(disk[1022 * BSIZE + 16]++);
    BAD_DISK(disk[BSIZE + 56]++; seal_header(disk + BSIZE));
    BAD_DISK(put64(disk + BSIZE + 72, UINT64_MAX); seal_header(disk + BSIZE));
    BAD_DISK(put64(disk + 1023 * BSIZE + 72, UINT64_MAX); seal_header(disk + 1023 * BSIZE));
    BAD_DISK(put32(disk + BSIZE + 80, 257); seal_header(disk + BSIZE));
    BAD_DISK(put64(disk + 2 * BSIZE + 128 + 32, ROOT_FIRST); seal_tables());
    BAD_DISK(memcpy(disk + 2 * BSIZE + 128 + 16, disk + 2 * BSIZE + 16, 16); seal_tables());
    BAD_DISK(disk[2 * BSIZE + 56] = 'x'; seal_tables());
    BAD_DISK(raw.path[6] = 3); BAD_DISK(root.path[6] = 3); BAD_DISK(((uint8_t *)&root.info.unique_guid)[0]++);
    BAD_DISK(root.media.last_block--); BAD_DISK(root.info.start_lba++); BAD_DISK(data.media.read_only = 1);
    BAD_DISK(duplicate_raw = true); BAD_DISK(duplicate_root = true);
    uint8_t ufs_path[] = {3, 0x19, 6, 0, 0, 4, 0x7f, 0xff, 4, 0}; uint64_t prefix = 0;
    assert(install_path_lun((efi_device_path *)ufs_path, &prefix) == 4 && prefix == 6); cases++;
    ufs_path[2] = 3; assert(install_path_lun((efi_device_path *)ufs_path, NULL) < 0); cases++;
    map = reset(); assert(install_prepare(&map)); uint64_t loaded = 99;
    assert(install_preload(&map, resident, sizeof(resident), &loaded) && loaded == ROOT_BYTES && root_reads == 3);
    assert(!memcmp(resident, disk + ROOT_FIRST * BSIZE, ROOT_BYTES)); assert(resident[ROOT_BYTES] == 0xa5 && !writes && !flushes); cases++;
    block_write_requested = 1; block_start();
    assert(block_write_part == 1 && !block_write_first && block_shared.write_part_plus_one == 2 && !block_shared.write_first);
    assert(!block_shared.write_caps);
    assert(block_shared.install_cap.magic == SW_INSTALL_CAP_MAGIC && !memcmp(block_shared.install_cap.disk_guid, map.disk_guid, 16));
    assert(!memcmp(block_shared.install_cap.data_guid, map.data.unique_guid, 16) && block_shared.install_cap.first_lba == DATA_FIRST);
    assert(block_shared.install_cap.blocks == DATA_BLOCKS && block_shared.part[1].unit == 4 && block_shared.part[1].lun == 4);
    assert(block_shared.part[2].unit == 4 && block_shared.part[2].lun == 4 && !strcmp(block_shared.part[2].name, "unit4"));
    assert(!block_shared.native.hc_pa && !writes && !flushes); cases++;
    block_shared.ask.request = 1; block_shared.ask.part = 1; block_shared.ask.count = 1; block_shared.ask.op = BLOCK_OP_WRITE;
    memset(block_buffer, 0x75, BSIZE); flush_fails = true; block_poll();
    assert(writes == 1 && flushes == 1 && !data_reads && block_shared.answer.failed == 1 && !block_shared.answer.served);
    assert(EFI_ERROR((uint64_t)block_shared.answer.status)); cases++;
    block_shared.ask.request++; flush_fails = false; block_poll();
    assert(writes == 2 && flushes == 2 && data_reads == 1 && block_shared.answer.served == 1 && !block_shared.answer.status); cases++;
    block_shared.ask.request++; block_shared.ask.op = BLOCK_OP_WRITE_FAST; block_poll();
    assert(writes == 2 && flushes == 2 && data_reads == 1 && block_shared.answer.status); cases++;
    block_shared.ask.request++; block_shared.ask.op = BLOCK_OP_WRITE; block_shared.ask.part = 0; block_poll();
    assert(writes == 2 && block_shared.answer.status && block_shared.answer.served == 1); cases++;
    block_shared.ask.request++; block_shared.ask.part = 1; block_shared.ask.lba = DATA_BLOCKS; block_poll();
    assert(writes == 2 && block_shared.answer.status); cases++;
    data.info.start_lba++; block_start(); assert(block_write_part < 0 && !block_shared.install_cap.magic); cases++;
    map = reset(); map.flags = 1; memset(&map.data, 0, sizeof(map.data)); assert(install_prepare(&map));
    block_write_requested = 1; block_start(); assert(block_write_part < 0 && !block_shared.install_cap.magic); cases++;
    map = reset(); assert(install_prepare(&map)); fail_root_read = 2; loaded = 99;
    assert(!install_preload(&map, resident, sizeof(resident), &loaded) && !loaded && root_reads == 2 && !writes && !flushes); cases++;
    map = reset(); assert(install_prepare(&map)); disk[ROOT_FIRST * BSIZE + 100] ^= 1; loaded = 99;
    assert(!install_preload(&map, resident, sizeof(resident), &loaded) && !loaded && root_reads == 3 && !writes && !flushes); cases++;
    map = reset(); assert(install_prepare(&map)); reads = 0; loaded = 99;
    assert(!install_preload(&map, resident, ROOT_BYTES - 1, &loaded) && !loaded && !reads); cases++;
    assert(!install_preload(&map, NULL, sizeof(resident), &loaded) && !loaded && !reads); cases++;
    reset(); legacy_sparse = true; install_active = 0;
    memset(&block_shared, 0, sizeof(block_shared)); block_shared.parts = 2;
    block_shared.part[1].block_size = BSIZE; block_shared.part[1].blocks = BLOCK_WRITE_FIRST + DATA_BLOCKS;
    data.media.last_block = BLOCK_WRITE_FIRST + DATA_BLOCKS - 1; block_bios[1] = &data.bio;
    block_write_part = 1; block_write_first = BLOCK_WRITE_FIRST;
    block_shared.ask.request = 1; block_shared.ask.part = 1; block_shared.ask.lba = BLOCK_WRITE_FIRST;
    block_shared.ask.count = 1; block_shared.ask.op = BLOCK_OP_WRITE_FAST; block_poll();
    assert(writes == 1 && !flushes && !data_reads && !block_shared.answer.status && block_shared.answer.served == 1); cases++;
    const char *required = "touch_live=1 bootcpu=7 cpus=7 buttons=1 touchscreen=1 touch_spacewar=1 touch_steps=6";
    char line[1024]; snprintf(line, sizeof(line), "%s spacewar_install=1 spacewar_storage=1 ufs=2 ufs_write=1 var_hfs=ufs_darwin_data", required);
    assert(touch_live_profile(line, 0, 0x700, 1) == 1); cases++;
    const char *bad[] = {"spacewar_install=1", "spacewar_storage=1 ufs=2 ufs_write=1 var_hfs=ufs_darwin_data",
                        "spacewar_install=1 spacewar_storage=1 ufs=2 ufs_write=1 root_apfs=ufs_darwin var_hfs=ufs_darwin_data",
                        "spacewar_install=1 spacewar_storage=1 ufs=2 ufs_write=1 var_hfs=ufs_userdata"};
    for (unsigned i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        snprintf(line, sizeof(line), "%s %s", required, bad[i]); assert(touch_live_profile(line, 0, 0x700, 1) < 0); cases++;
    }
    printf("install-map-test: %u actual-helper map/GPT/EFI/preload/capability/policy cases passed; zero preload writes, data flush errors refused\n", cases);
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='loader-install-map-') as directory:
        tmp = Path(directory)
        unit = tmp / 'test.c'
        sizes = f'#define ROOT_BYTES {ROOT_BYTES}U\n#define ROOT_CRC {ROOT_CRC}U\n'
        unit.write_text('\n'.join((PREFIX, payload_types, sizes, constants, abi, STUBS, crc, ufs_name, paths, install,
                                   legacy, block_start, mailbox, payload, profile, dispatch, CASES)))
        target = tmp / 'test'
        subprocess.run(['cc', '-std=gnu11', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        '-I', str(ROOT / 'xnuloader'), '-I', str(ROOT / 'drivers/storage'), str(unit), '-o', str(target)], check=True)
        subprocess.run([str(target)], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})


if __name__ == '__main__':
    main()
