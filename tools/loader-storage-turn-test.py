#!/usr/bin/env python3
"""Test actual CPU0 storage/lease/USB scheduling helpers without a phone."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'xnuloader/loader.c').read_text()
lease = source[source.index('static uint64_t\ntouch_lease_peer_read('):source.index('static uint64_t\nseconds_since(')]
lease = lease.replace('__asm__ volatile ("yield" ::: "memory");', 'sim_yield();')
constants = source[source.index('#define BLOCK_MAGIC'):source.index('struct loader_block_part {')]
abi = source[source.index('struct loader_block_part {'):source.index('/* (8 KB is as far as this target aligns;')]
storage = source[source.index('static void\nblock_poll('):source.index('static void\ncharger_start(')]
assert storage.count('__asm__ volatile ("dmb sy");') == 1
storage = storage.replace('__asm__ volatile ("dmb sy");', '__asm__ volatile ("" ::: "memory");')
usb_wait = source[source.index('static int64_t\nusb_wait('):source.index('static int usb_rx_queued;')]
watch = source[source.index('static int64_t\nrun_on_other_cpu('):source.index('/* Hypervisor watchdog')]
assert 'block_service_turn();\n\t\tusb_poll_command();' in watch
burst = watch[watch.index('if (!touch_live_enabled && block_enabled)'):]
assert 'for (int k = 0; k < 64; k++)' in burst and 'block_service_turn();' in burst

PREFIX = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hakios.spacewar.lease.h"
#include "../storage/hakios.spacewar.install.h"
typedef uint64_t efi_status;
#define EFI_ERROR(value) (((uint64_t)(value) >> 63) != 0)
#define ERROR_STATUS (1ULL << 63)
#define BLOCK_BUFFER_SIZE (4u << 20)
typedef struct {uint32_t media_id;} test_media;
typedef struct efi_block_io efi_block_io;
struct efi_block_io {
    test_media *media;
    efi_status (*read_blocks)(efi_block_io *, uint32_t, uint64_t, uint64_t, void *);
    efi_status (*write_blocks)(efi_block_io *, uint32_t, uint64_t, uint64_t, const void *);
    efi_status (*flush_blocks)(efi_block_io *);
};
enum {UsbDeviceEventNoEvent, UsbDeviceEventDeviceStateChange, UsbDeviceEventTransferNotification};
enum {UsbDeviceStateConnected = 1, UsbDeviceTransferStatusCompleteOK = 0};
typedef union {
    uint32_t device_state;
    struct {uint32_t endpoint_index, status; uint64_t bytes_completed;} transfer;
} usb_device_event_data;
typedef struct {
    void (*handle_event)(uint32_t *, uint64_t *, usb_device_event_data *);
    void (*abort_xfer)(uint8_t);
} test_usb;
'''

STUBS = r'''
static struct loader_block block_shared;
static uint8_t block_buffer[BLOCK_BUFFER_SIZE];
static efi_block_io *block_bios[BLOCK_MAX_PARTS];
static int block_enabled, block_write_part = -1;
static int install_active;
static uint64_t block_write_first = BLOCK_WRITE_FIRST;
static uint64_t block_writes, block_writes_refused, block_write_bytes;
static int hc_refused_reads;
static struct sw_touch_lease backing;
static struct sw_touch_lease *touch_shared = &backing;
static int run_active, touch_live_enabled, usb_connected;
static volatile uint64_t other_cpu_done;
static uint64_t usb_data_timeout, ticks;
static unsigned reads, writes, flushes, grants, quiets, yields, usb_turns, watchdogs, aborts;
static unsigned release_after, usb_complete_after;
static bool firmware_busy, controller_owned, read_fails, late_cancel, stop_parked;
static bool request_during_read, cancel_during_read, stop_during_read;
static bool request_before_read, next_block_during_read, usb_requeues;
static uint64_t last_grant;
static void sim_yield(void);
static void firmware_entry(void) {
    assert(!firmware_busy);
    assert(!touch_live_enabled || !backing.grant || backing.release >= backing.grant);
    firmware_busy = true;
}
static void firmware_exit(void) {assert(firmware_busy); firmware_busy = false;}
static void touch_lease_quiet_cpu(void) {assert(!firmware_busy); quiets++;}
static void flush_dcache(uint64_t start, uint64_t end) {
    assert(end > start);
    if (start == (uintptr_t)&block_shared.ask && request_before_read) {
        request_before_read = false; backing.request = backing.release + 1;
    }
}
static void clean_dcache(uint64_t start, uint64_t end) {
    assert(end > start);
    if (start == (uintptr_t)&backing.grant && backing.grant) {
        assert(!firmware_busy && quiets > grants);
        grants++; last_grant = backing.grant;
        if (late_cancel) backing.release = backing.request;
    }
}
static uint64_t clock_read(const char *name) {
    return !strcmp(name, "CNTFRQ_EL0") ? 1000 : ++ticks;
}
#define READ_SYSREG(name) clock_read(#name)
static int hc_list_is_uefis(void) {return controller_owned;}
static void print(const char *format, ...) {(void)format;}
static uint32_t ufs_crc32(const uint8_t *p, uint64_t size) {(void)p; (void)size; return 0;}
static efi_status fake_read(efi_block_io *bio, uint32_t id, uint64_t lba, uint64_t bytes, void *out) {
    firmware_entry(); reads++;
    assert(bio->media->media_id == id && id == 7 && lba < 64 && bytes && bytes <= BLOCK_BUFFER_SIZE);
    if (request_during_read || cancel_during_read) {
        backing.request = backing.release + 1;
        assert(backing.grant != backing.request); /* CPU0 has not acknowledged an in-flight request. */
        if (cancel_during_read) backing.release = backing.request;
        request_during_read = cancel_during_read = false;
    }
    if (next_block_during_read) {block_shared.ask.request++; next_block_during_read = false;}
    if (stop_during_read) other_cpu_done = 1;
    memset(out, 0x6b, (size_t)bytes);
    firmware_exit(); return read_fails ? ERROR_STATUS : 0;
}
static efi_status fake_write(efi_block_io *bio, uint32_t id, uint64_t lba, uint64_t bytes, const void *p) {
    (void)bio; (void)id; (void)lba; (void)bytes; (void)p; writes++; assert(0 && "test must never write"); return ERROR_STATUS;
}
static efi_status fake_flush(efi_block_io *bio) {(void)bio; flushes++; assert(0 && "test must never flush"); return ERROR_STATUS;}
static test_media media = {7};
static efi_block_io bio = {&media, fake_read, fake_write, fake_flush};
static void sim_yield(void) {
    assert(!firmware_busy && backing.grant && backing.release < backing.grant);
    yields++;
    if (stop_parked) {other_cpu_done = 1; return;}
    if (yields >= release_after) backing.release = backing.grant;
}
static void event(uint32_t *msg, uint64_t *size, usb_device_event_data *data) {
    firmware_entry(); usb_turns++; *size = 0; *msg = UsbDeviceEventNoEvent;
    if (usb_turns >= usb_complete_after) {
        *msg = UsbDeviceEventTransferNotification;
        data->transfer.endpoint_index = 7;
        data->transfer.status = UsbDeviceTransferStatusCompleteOK;
        data->transfer.bytes_completed = 32;
    } else if (usb_requeues) {
        assert(block_shared.answer.answered == block_shared.ask.request);
        block_shared.ask.request++;
        if (touch_live_enabled) backing.request = backing.release + 1;
    }
    firmware_exit();
}
static void abort_transfer(uint8_t endpoint) {assert(endpoint == 7); firmware_entry(); aborts++; firmware_exit();}
static test_usb usb = {event, abort_transfer};
static test_usb *USB = &usb;
static void wdt_pet(void) {firmware_entry(); watchdogs++; firmware_exit();}
'''

CASES = r'''
static void reset(void) {
    memset(&backing, 0, sizeof(backing)); touch_shared = &backing;
    memset(&block_shared, 0, sizeof(block_shared)); memset(block_buffer, 0, sizeof(block_buffer));
    block_shared.parts = 1; block_shared.part[0].block_size = 4096; block_shared.part[0].blocks = 64;
    strcpy(block_shared.part[0].name, "test"); block_bios[0] = &bio;
    block_shared.ask.request = 1; block_shared.ask.part = 0; block_shared.ask.count = 1;
    block_shared.ask.op = BLOCK_OP_READ;
    run_active = touch_live_enabled = block_enabled = usb_connected = 1; other_cpu_done = 0;
    block_write_part = -1; block_writes = block_writes_refused = block_write_bytes = 0;
    hc_refused_reads = 0; usb_data_timeout = ticks = 0;
    reads = writes = flushes = grants = quiets = yields = usb_turns = watchdogs = aborts = 0;
    release_after = 1; usb_complete_after = 1; last_grant = 0;
    firmware_busy = read_fails = late_cancel = stop_parked = false; controller_owned = true;
    request_during_read = cancel_during_read = stop_during_read = false;
    request_before_read = next_block_during_read = usb_requeues = false;
}
static void completed_read(void) {
    assert(reads == 1 && block_shared.answer.answered == 1 && !block_shared.answer.status);
    assert(block_shared.answer.served == 1 && block_shared.answer.bytes == 4096 && block_buffer[0] == 0x6b);
    assert(!writes && !flushes && !firmware_busy);
    assert(!backing.grant || backing.release >= backing.grant);
}
int main(void) {
    reset(); block_service_turn(); completed_read(); assert(!grants);
    block_service_turn(); assert(reads == 1); /* no repeated request */
    reset(); backing.request = 1; release_after = 8; block_service_turn(); completed_read(); assert(grants == 1 && yields == 8);
    reset(); backing.request = backing.grant = 1; release_after = 3; block_service_turn(); completed_read(); assert(yields == 3);
    reset(); backing.request = backing.release = 1; block_service_turn(); completed_read(); assert(!grants && !yields);
    reset(); backing.request = 1; late_cancel = true; block_service_turn(); completed_read(); assert(grants == 1 && !yields);
    reset(); backing.request = 1; stop_parked = true; block_service_turn(); assert(!reads && !backing.grant && other_cpu_done);
    reset(); other_cpu_done = 1; block_service_turn(); assert(!reads);
    reset(); run_active = 0; block_service_turn(); assert(!reads && !grants);
    reset(); block_enabled = 0; block_service_turn(); assert(!reads && !grants);
    reset(); touch_shared = NULL; block_service_turn(); assert(!reads);
    reset(); touch_live_enabled = 0; touch_shared = NULL; block_service_turn(); completed_read(); assert(!grants);
    reset(); request_before_read = true; block_service_turn(); completed_read(); assert(grants == 1 && last_grant == 1);
    reset(); request_during_read = true; block_service_turn(); completed_read(); assert(grants == 1 && last_grant == 1);
    reset(); cancel_during_read = true; block_service_turn(); completed_read(); assert(!grants && backing.release == 1);
    reset(); stop_during_read = true; block_service_turn(); completed_read(); assert(other_cpu_done && !backing.grant);
    block_shared.ask.request++; block_service_turn(); assert(reads == 1);
    reset(); read_fails = request_during_read = true; block_service_turn();
    assert(reads == 1 && grants == 1 && backing.release == 1 && block_shared.answer.answered == 1);
    assert(block_shared.answer.failed == 1 && block_shared.answer.status == (int64_t)ERROR_STATUS);
    reset(); next_block_during_read = true; block_service_turn();
    assert(reads == 1 && block_shared.ask.request == 2 && block_shared.answer.answered == 1);
    block_service_turn(); assert(reads == 2 && block_shared.answer.answered == 2);
    reset(); controller_owned = false; block_service_turn(); assert(!reads && block_shared.answer.status && hc_refused_reads == 1);
    for (unsigned op = BLOCK_OP_WRITE; op <= BLOCK_OP_FLUSH; op++) {
        reset(); block_shared.ask.op = op; block_service_turn(); assert(!reads && !writes && !flushes && block_shared.answer.status);
    }
    reset(); block_shared.ask.count = 0; block_service_turn(); assert(!reads && block_shared.answer.status);
    reset(); block_shared.ask.part = 1; block_service_turn(); assert(!reads && block_shared.answer.status);
    reset(); block_shared.ask.lba = 64; block_service_turn(); assert(!reads && block_shared.answer.status);
    reset(); block_shared.ask.count = 65; block_service_turn(); assert(!reads && block_shared.answer.status);
    reset(); usb_requeues = true; usb_complete_after = 8;
    assert(usb_wait(7) == 32 && usb_turns == 8 && reads == 8 && grants == 7 && !aborts);
    assert(block_shared.answer.served == 8 && !writes && !flushes);
    reset(); usb_requeues = true; usb_complete_after = 4; read_fails = true;
    assert(usb_wait(7) == 32 && usb_turns == 4 && reads == 4 && block_shared.answer.failed == 4);
    reset(); run_active = 0; assert(usb_wait(7) == 32 && usb_turns == 1 && !reads);
    reset(); block_enabled = 0; backing.request = 1;
    assert(usb_wait(7) == 32 && usb_turns == 1 && !reads && grants == 1);
    puts("storage-turn-test: actual lease, mailbox and USB helpers passed 29 scheduling/I/O cases; no writes");
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='loader-storage-turn-') as directory:
        tmp = Path(directory)
        unit = tmp / 'test.c'
        unit.write_text('\n'.join((PREFIX, constants, abi, STUBS, lease, storage, usb_wait, CASES)))
        target = tmp / 'test'
        subprocess.run(['cc', '-std=gnu11', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        '-I', str(ROOT / 'drivers/input'), str(unit), '-o', str(target)], check=True)
        subprocess.run([str(target)], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
        print('storage-turn-test: watch loop retains the 64-operation burst only without live touch')


if __name__ == '__main__':
    main()
