from pathlib import Path
import subprocess,tempfile,os
root=Path(__file__).resolve().parents[1]
os.chdir(root)
def run(source,args=()):
 with tempfile.TemporaryDirectory(prefix='quiet-actual-') as d:
  p=Path(d);(p/'test.c').write_text(source)
  subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-O2','-pthread','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
  return subprocess.check_output([str(p/'test'),*args],text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'}).strip()
header=Path('userland/src/hakios.debug.h').read_text().replace('#include <sys/sysctl.h>','')
prefix=r'''
#include <stddef.h>
#include <stdio.h>
#include <string.h>
static const char *bootargs;
static int sysctlbyname(const char *n,void *out,size_t *size,void *in,size_t insize) {
 (void)n;(void)in;(void)insize;
 size_t len=strlen(bootargs)+1;if(len>*size)return -1;memcpy(out,bootargs,len);*size=len;return 0;
}
'''
source=prefix+header+'\nint main(int argc,char **argv){(void)argc;bootargs=argv[1];printf("%d",hakios_debug_enabled());}\n'
for args,wanted in [('',1),('hakios_debug=0',0),('gpu=1 hakios_debug=0 touch_live=1',0),('hakios_debug=1',1),('xhakios_debug=0',1),('hakios_debug=00',1),('hakios_debug=0x0',1),('hakios_debug=',1),('hakios_debug=2',1)]:
 assert run(source,[args])==str(wanted),(args,wanted)
print('PASS 9 actual user-space flag-parser cases: default/debug/quiet and exact token boundaries')
s=Path('xnu-src/xnu-8796.121.2/osfmk/console/video_console.c').read_text();a=s.index('void\nvcputc_options(');b=s.index('\n}\n',a)+3
source=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define __unused __attribute__((unused))
#define BCM2837 1
#define VCPUTC_LOCK_LOCK() locks++
#define VCPUTC_LOCK_UNLOCK() locks--
static int gc_initialized=1,gc_enabled=1,gc_x,gc_y,locks,draws;
int hakios_debug=1;
static void gc_hide_cursor(int a,int b){(void)a;(void)b;draws++;}
static void gc_show_cursor(int a,int b){(void)a;(void)b;draws++;}
static void gc_putchar(char c){(void)c;draws++;}
'''+s[a:b]+r'''
int main(void){vcputc_options('a',false);assert(draws==3&&!locks);hakios_debug=0;vcputc_options('b',false);assert(draws==3&&!locks);hakios_debug=1;vcputc_options('c',false);assert(draws==6&&!locks);puts("PASS actual video console: quiet draws nothing; debug output can be re-enabled");}
'''
print(run(source))
s=Path('drivers/core/hakios.socinfo.c').read_text();a=s.index('static void\nspacewar_cpu_probe(');b=s.index('\n}\n',a)+3
source=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef uintptr_t vm_offset_t;
static unsigned board=1,enabled=1,maps;
#define HAKIOS_DEVICE_SPACEWAR board
static struct {bool valid;unsigned id;}soc={true,475};
static uint32_t regs[3][1024],before[3][1024];
static vm_offset_t ml_io_map(vm_offset_t pa,unsigned size){assert(size==4096&&pa>=0x18591000&&pa<=0x18593000);maps++;return (vm_offset_t)regs[(pa-0x18591000)/4096];}
static bool PE_parse_boot_argn(const char *n,void *v,unsigned z){assert(!strcmp(n,"cpu_probe")&&z==4);*(unsigned*)v=enabled;return enabled!=0;}
static void say(const char *fmt,...){(void)fmt;}
'''+s[a:b]+r'''
int main(void){for(unsigned d=0;d<3;d++){for(unsigned i=0;i<40;i++)regs[d][0x100/4+i]=(1u<<30)|(i<3?20+10*i:40);regs[d][0x320/4]=d;}
memcpy(before,regs,sizeof(regs));spacewar_cpu_probe();assert(maps==3&&!memcmp(before,regs,sizeof(regs)));
maps=0;enabled=0;spacewar_cpu_probe();assert(!maps);enabled=1;board=0;spacewar_cpu_probe();assert(!maps);board=1;soc.id=0;spacewar_cpu_probe();assert(!maps);soc.id=475;soc.valid=false;spacewar_cpu_probe();assert(!maps);puts("PASS actual CPU probe: three domains read, no register writes, board/SoC/opt-in guards retained");}
'''
print(run(source))
