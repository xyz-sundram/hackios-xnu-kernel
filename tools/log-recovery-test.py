#!/usr/bin/env python3
"""Execute the real static exporter and decoder, with no phone, mount or SDK."""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'tools/log-recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

HEADER = r'''
#define _DEFAULT_SOURCE
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>
#define MNT_RDONLY 1
#define MNT_UPDATE 0x10000
#define MTIME_SEC(s) ((s).st_mtim.tv_sec)
#define MTIME_NSEC(s) ((s).st_mtim.tv_nsec)
static ssize_t host_getdirentries(int,void*,size_t,off_t*);
static int host_mount(const char*,const char*,int,void*);
static int sysctlbyname(const char*,void*,size_t*,const void*,size_t);
#define getdirentries host_getdirentries
#define mount host_mount
'''
HARNESS = r'''
static unsigned malformed, mount_mode, mount_calls, mount_flags[8];
static const char *bootargs="var_hfs=ufs_darwin_data";
static ssize_t host_getdirentries(int fd,void *out,size_t cap,off_t *position) {
    if (malformed) {
        struct recovery_dirent e; memset(&e,0,sizeof(e));
        e.reclen=32;e.namlen=1;strcpy(e.name,".");
        if(malformed==1)return 1;
        if(malformed==2)e.reclen=0;
        if(malformed==3)e.reclen=8193;
        if(malformed==4)e.namlen=1024;
        if(malformed==5)e.name[1]='x';
        if(malformed==6)e.namlen=2;
        if(malformed==7 && *position>=4097)return 0;
        if(malformed==8)e.reclen=21;
        memcpy(out,&e,32);(*position)++;return 32;
    }
    if(*position)return 0;
    DIR *dir=fdopendir(dup(fd)); assert(dir);size_t used=0;
    struct dirent *item;
    while((item=readdir(dir))) {
        struct recovery_dirent e;memset(&e,0,sizeof(e));
        size_t n=strlen(item->d_name);assert(n<sizeof(e.name));
        e.namlen=(uint16_t)n;e.reclen=(uint16_t)((offsetof(struct recovery_dirent,name)+n+1+3)&~3u);
        strcpy(e.name,item->d_name);assert(used+e.reclen<=cap);
        memcpy((char*)out+used,&e,e.reclen);used+=e.reclen;
    }
    closedir(dir);*position=1;return (ssize_t)used;
}
static int host_mount(const char *type,const char *target,int flags,void *data) {
    assert(!strcmp(type,"hfs") && !strcmp(target,"/private/var"));
    assert(!strcmp(*(const char**)data,"/dev/ufs_darwin_data"));
    assert(mount_calls<8);mount_flags[mount_calls++]=(unsigned)flags;
    if(mount_mode && mount_calls==1){errno=EINVAL;return -1;}
    if(mount_mode==2 && mount_calls==2)return -1;
    if(mount_mode==3 && mount_calls==3)return -1;
    return 0;
}
static int sysctlbyname(const char *name,void *out,size_t *size,const void *in,size_t n) {
    assert(!strcmp(name,"kern.bootargs") && !in && !n);
    size_t bytes=strlen(bootargs)+1;if(bytes>*size)return -1;
    memcpy(out,bootargs,bytes);*size=bytes;return 0;
}
int main(int argc,char **argv) {
    assert(argc>=2);
    if(!strcmp(argv[1],"mount")) {
        mount_mode=argc>2?(unsigned)atoi(argv[2]):0;
        if(argc>3)bootargs=argv[3];
        int result=mount_capture_volume();
        printf("RESULT %d CALLS %u FLAGS",result,mount_calls);
        for(unsigned i=0;i<mount_calls;i++)printf(" %u",mount_flags[i]);
        printf(" REPLAY %d\n",did_replay);return 0;
    }
    if(argc>3)malformed=(unsigned)atoi(argv[3]);
    char name[20]="";unsigned length=0;
    if(read_capture(argv[1],argc>2?argv[2]:"",name,&length))return 3;
    return emit_round(name,length,0)?4:0;
}
'''


class RecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='log-recovery-test-')
        cls.base = Path(cls.temp.name)
        (cls.base / 'log_recover_host.h').write_text(HEADER)
        source = cls.base / 'harness.c'
        source.write_text('#define LOG_RECOVERY_HOST_TEST 1\n#define LOG_RECOVERY_NO_MAIN 1\n' +
                          '#include "' + str(ROOT / 'userland/src/log_recover.c') + '"\n' + HARNESS)
        cls.exe = []
        for replay in (0, 1):
            exe = cls.base / f'exporter-{replay}'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', f'-DLOG_RECOVERY_REPLAY={replay}',
                            '-I', str(cls.base), str(source), '-o', str(exe)], check=True)
            cls.exe.append(exe)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(self.temp_case.cleanup)
        self.path = Path(self.temp_case.name)
        self.logs = self.path / 'logs'
        self.logs.mkdir()

    def run_exporter(self, *args, replay=0):
        return subprocess.run([str(self.exe[replay]), *map(str, args)], capture_output=True,
                              env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})

    def fixture(self, length=2000):
        path = self.logs / 'hakios-audio-Ab12cD'
        data = bytes(i % 251 for i in range(length))
        path.write_bytes(data)
        result = self.run_exporter(self.logs, path.name)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return path, data, result.stdout.decode()

    def test_real_exporter_roundtrip_and_overlapping_snapshots(self):
        path, data, frames = self.fixture()
        decoder = recovery.Decoder(path.name)
        lines = frames.splitlines()
        decoder.feed('\n'.join('[ 1.2]: ' + x for x in lines[:3]))
        self.assertFalse(decoder.completed())
        decoder.feed('\n'.join(lines[:1] + lines[2:]))
        decoder.feed(frames)
        self.assertEqual(decoder.completed()[0][3], data)
        self.assertEqual(len(decoder.sessions), 1)

    def test_readonly_and_explicit_replay_mount_paths(self):
        for replay in (0, 1):
            result = self.run_exporter('mount', 0, replay=replay).stdout.decode()
            self.assertIn('RESULT 0 CALLS 1 FLAGS 1 REPLAY 0', result)
        result = self.run_exporter('mount', 1).stdout.decode()
        self.assertIn('RESULT -1 CALLS 1 FLAGS 1 REPLAY 0', result)
        result = self.run_exporter('mount', 1, replay=1).stdout.decode()
        self.assertIn('RESULT 0 CALLS 3 FLAGS 1 0 65537 REPLAY 1', result)
        for mode, calls in ((2, 2), (3, 3)):
            result = self.run_exporter('mount', mode, replay=1).stdout.decode()
            self.assertIn(f'RESULT -1 CALLS {calls}', result)
        for argument in ('', 'var_hfs=../bad', 'var_hfs=ufs_darwin_data var_hfs=ufs_darwin_data'):
            result = self.run_exporter('mount', 0, argument).stdout.decode()
            self.assertIn('RESULT -1 CALLS 0', result)

    def test_newest_exact_name_symlink_and_size_bounds(self):
        old = self.logs / 'hakios-audio-Ab12cD'; old.write_bytes(b'old')
        recent = self.logs / 'hakios-audio-Zy98xW'; recent.write_bytes(b'new')
        os.utime(old, ns=(100, 100)); os.utime(recent, ns=(200, 200))
        (self.logs / 'hakios-audio-Xx99zZ').symlink_to(recent)
        huge = self.logs / 'hakios-audio-Aa00bB'
        with huge.open('wb') as stream:
            stream.truncate(recovery.FILE_CAP + 1)
        decoder = recovery.Decoder()
        result = self.run_exporter(self.logs, '')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        decoder.feed(result.stdout.decode())
        self.assertEqual(decoder.completed()[0][3], b'new')
        for exact in (huge.name, 'hakios-audio-Xx99zZ', 'hakios-audio-Mi55in', '../escape'):
            self.assertEqual(self.run_exporter(self.logs, exact).returncode, 3)
        self.assertEqual(self.run_exporter(self.logs, old.name).returncode, 0)

    def test_actual_directory_record_guards(self):
        for malformed in range(1, 9):
            result = self.run_exporter(self.logs, '', malformed)
            self.assertEqual(result.returncode, 3, result.stderr.decode())

    def test_decoder_rejects_bad_crc_sequence_end_and_incomplete_data(self):
        path, data, frames = self.fixture()
        lines = frames.splitlines()
        bad_variants = [frames.replace(' 0 0 768 ', ' 1 0 768 ', 1),
                        frames.replace(' 0 0 768 ', ' 0 1 768 ', 1),
                        frames.replace(' 0 0 768 ', ' 0 0 767 ', 1),
                        '\n'.join(lines[:-1]), '\n'.join(lines[:1] + lines[2:])]
        words = lines[1].split(); words[6] = '00000000'
        bad_variants.append('\n'.join([lines[0], ' '.join(words), *lines[2:]]))
        words = lines[-1].split(); words[-1] = '00000000'
        bad_variants.append('\n'.join([*lines[:-1], ' '.join(words)]))
        for bad in bad_variants:
            decoder = recovery.Decoder(path.name); decoder.feed(bad)
            self.assertFalse(decoder.completed())
            decoder.feed(frames)  # a repeated correct round repairs missing/corrupt frames
            self.assertEqual(decoder.completed()[0][3], data)

    def test_fake_fastboot_incremental_ring_capture(self):
        path, data, frames = self.fixture(recovery.FILE_CAP)
        lines = frames.splitlines()
        chunks = lines[1:-1]
        snapshots = []
        for at in range(0, len(chunks), 550):
            snapshot = [lines[0], *chunks[at:at + 550]]
            if at + 550 >= len(chunks): snapshot.append(lines[-1])
            snapshots.append('\n'.join(snapshot) + '\n')
        import json
        fixture = self.path / 'snapshots.json'; fixture.write_text(json.dumps(snapshots))
        fastboot = self.path / 'fastboot-fixture'
        fastboot.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(f'''
            import json,sys
            from pathlib import Path
            base=Path({str(self.path)!r}); state=base/'counter'
            args=sys.argv[1:]
            if args==['getvar','product']: print('product: xnuloader',file=sys.stderr)
            elif args==['oem','log']: pass
            elif len(args)==2 and args[0]=='get_staged':
                snapshots=json.loads((base/'snapshots.json').read_text())
                n=int(state.read_text()) if state.exists() else 0
                Path(args[1]).write_text(snapshots[min(n,len(snapshots)-1)])
                state.write_text(str(n+1))
            else: raise SystemExit(9)
        '''))
        fastboot.chmod(0o700)
        output = self.path / 'recovered'
        result = subprocess.run(['python3', str(ROOT / 'tools/log-recovery.py'), 'capture', '--fastboot', str(fastboot),
                                 '--seconds', '20', '--poll', '0.1', '--name', path.name, '--out', str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((output / path.name).read_bytes(), data)
        self.assertLess((output / 'latest-loader-snapshot.private.log').stat().st_size, 1024 * 1024)
        self.assertLess((output / 'frames.private.log').stat().st_size, 4 * 1024 * 1024)
        self.assertEqual((output / path.name).stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
