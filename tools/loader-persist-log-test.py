#!/usr/bin/env python3
"""Exercise the loader's actual log staging/persistence helpers without a phone."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'xnuloader/loader.c').read_text()
policy = source[source.index('#ifndef HAKIOS_PERSIST_LOG'):source.index('\n/* ------------------------------------------------------------------------ */')]
compose = source[source.index('static void\ncompose_logs('):source.index('\nstatic int hc_list_is_uefis(void);')]
write = source[source.index('static void\nwrite_logs('):source.index('\nstatic void\nflush_log(')]

STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t efi_status;
#define ERROR_STATUS (1ULL << 63)
#define EFI_ERROR(status) (((uint64_t)(status) & ERROR_STATUS) != 0)
typedef struct {uint64_t block_size; uint32_t media_id;} test_media;
typedef struct efi_block_io efi_block_io;
struct efi_block_io {
    test_media *media;
    efi_status (*write_blocks)(efi_block_io *, uint32_t, uint64_t, uint64_t, const void *);
    efi_status (*flush_blocks)(efi_block_io *);
    efi_status (*read_blocks)(efi_block_io *, uint32_t, uint64_t, uint64_t, void *);
};
static char log_buf[4096], out_buf[2 * 1024 * 1024];
static uint8_t log_verify[sizeof(out_buf) + 8192], disk[sizeof(log_verify)];
static const char *staged_data;
static uint64_t log_len, staged_len;
static unsigned lookups, controller_checks, writes, flushes, reads, cache_calls;
static unsigned prints, state_calls, hc_log_skips;
static bool have_partition, controller_owned, fail_write, fail_read;
static char console[4096];
static size_t console_len;
static test_media media = {4096, 7};
static efi_status write_blocks(efi_block_io *bio, uint32_t id, uint64_t lba, uint64_t len, const void *p) {
    writes++; assert(bio->media == &media && id == 7 && lba == 0);
    assert(len && len % media.block_size == 0 && len <= sizeof(disk));
    if (fail_write) return ERROR_STATUS;
    memcpy(disk, p, len); return 0;
}
static efi_status flush_blocks(efi_block_io *bio) {flushes++; assert(bio->media == &media); return 0;}
static efi_status read_blocks(efi_block_io *bio, uint32_t id, uint64_t lba, uint64_t len, void *p) {
    reads++; assert(bio->media == &media && id == 7 && lba == 0 && len <= sizeof(disk));
    if (fail_read) return ERROR_STATUS;
    memcpy(p, disk, len); return 0;
}
static efi_block_io bio = {&media, write_blocks, flush_blocks, read_blocks};
static efi_block_io *find_log_partition(void) {lookups++; return have_partition ? &bio : NULL;}
static int hc_list_is_uefis(void) {controller_checks++; return controller_owned;}
static void hc_print_state(const char *why) {assert(!strcmp(why, "log not written")); state_calls++;}
static void flush_dcache(uint64_t start, uint64_t end) {
    cache_calls++; assert(start == (uintptr_t)log_verify && end > start && end-start <= sizeof(log_verify));
}
static void print(const char *format, ...) {
    /* Firmware print has its own 64-bit format ABI. Retain a bounded diagnostic
     * marker without asking the host libc to interpret that unrelated ABI. */
    (void)format; prints++;
    const char marker[] = "<diagnostic>";
    assert(log_len + sizeof(marker) < sizeof(log_buf));
    memcpy(log_buf + log_len, marker, sizeof(marker)); log_len += sizeof(marker)-1;
}
static void put_char(char c) {assert(console_len + 1 < sizeof(console)); console[console_len++] = c;}
'''

CASES = r'''
static void reset(void) {
    memset(log_buf, 0, sizeof(log_buf)); strcpy(log_buf, "loader log\n"); log_len = strlen(log_buf);
    memset(out_buf, 0xa5, sizeof(out_buf)); memset(disk, 0x5a, sizeof(disk));
    memset(console, 0, sizeof(console)); console_len = 0;
    staged_data = NULL; staged_len = 0;
    lookups = controller_checks = writes = flushes = reads = cache_calls = prints = state_calls = hc_log_skips = 0;
    have_partition = controller_owned = true; fail_write = fail_read = false;
}
static void staged(const char *kernel) {
    assert(staged_data == out_buf && staged_len == strlen(out_buf));
    assert(strstr(out_buf, "loader log\n"));
    if (kernel) {assert(strstr(out_buf, "== KERNEL LOG ==\n")); assert(strstr(out_buf, kernel)); assert(strstr(out_buf, "== END OF LOG ==\n"));}
    else assert(!strstr(out_buf, "== KERNEL LOG =="));
}
static void no_storage(void) {assert(!lookups && !controller_checks && !writes && !flushes && !reads && !cache_calls);}
int main(void) {
    const char *kernel = "kernel log\n";
    reset(); write_logs(kernel, strlen(kernel)); staged(kernel);
    if (!HAKIOS_PERSIST_LOG) {
        no_storage(); assert(!prints && !console_len && disk[0] == 0x5a);
        reset(); have_partition = false; controller_owned = false;
        write_logs(NULL, 0); staged(NULL); no_storage(); assert(!prints && !console_len);
        reset(); fail_write = fail_read = true;
        write_logs(kernel, strlen(kernel)); staged(kernel); no_storage();
        puts("persist-log-test: disabled/default stages logs with zero storage calls (3 cases)");
        return 0;
    }
    assert(lookups == 1 && controller_checks == 1 && writes == 1 && flushes == 1 && reads == 1 && cache_calls == 1);
    assert(strstr((char *)disk, kernel) && prints == 1 && !console_len);
    reset(); have_partition = false; write_logs(kernel, strlen(kernel)); staged(kernel);
    assert(lookups == 1 && !controller_checks && !writes && !reads && !flushes);
    assert(console_len == strlen(kernel) && !memcmp(console, kernel, console_len));
    reset(); have_partition = false; write_logs(NULL, 0); staged(NULL);
    assert(lookups == 1 && !writes && !prints && !console_len);
    reset(); controller_owned = false; write_logs(kernel, strlen(kernel)); staged(kernel);
    assert(lookups == 1 && controller_checks == 1 && !writes && !reads && !flushes && state_calls == 1);
    reset(); fail_write = true; write_logs(kernel, strlen(kernel)); staged(kernel);
    assert(writes == 1 && !flushes && !reads && !cache_calls);
    reset(); fail_read = true; write_logs(kernel, strlen(kernel)); staged(kernel);
    assert(writes == 1 && flushes == 1 && reads == 1 && cache_calls == 1);
    puts("persist-log-test: enabled writes/readback, no-partition fallback, ownership refusal and failed I/O (6 cases)");
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='loader-persist-log-') as directory:
        tmp = Path(directory)
        unit = tmp / 'test.c'
        unit.write_text(policy + '\n' + STUBS + '\n' + compose + '\n' + write + '\n' + CASES)
        for flag in (None, 0, 1):
            target = tmp / ('test-default' if flag is None else f'test-{flag}')
            command = ['cc', '-std=gnu11', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
            if flag is not None:
                command.append(f'-DHAKIOS_PERSIST_LOG={flag}')
            subprocess.run(command + [str(unit), '-o', str(target)], check=True)
            subprocess.run([str(target)], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
        for flag in ('2', 'yes', '-1'):
            result = subprocess.run(['bash', str(ROOT / 'xnuloader/build.sh')], capture_output=True, text=True,
                                    env={**os.environ, 'HAKIOS_PERSIST_LOG': flag,
                                         'XNULOADER_OUT': str(tmp / 'invalid-output')})
            assert result.returncode != 0 and 'HAKIOS_PERSIST_LOG must be 0 or 1' in result.stderr, result
            assert not (tmp / 'invalid-output').exists()
        print('persist-log-test: build rejects invalid flag values before creating output (3 cases)')


if __name__ == '__main__':
    main()
