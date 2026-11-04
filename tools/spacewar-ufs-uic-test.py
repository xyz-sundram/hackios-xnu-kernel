#!/usr/bin/env python3
"""Test patched U-Boot UIC helpers with stubbed MMIO and ASAN/UBSAN.

Accepts a base or already-patched source tree. Only temporary source copies are
changed; no device or U-Boot build is needed. Requires a host C compiler and git.
"""
from pathlib import Path
import argparse
import os
import shlex
import subprocess
import sys
import tempfile

C_PREFIX = r'''
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;
#define REG_CONTROLLER_STATUS 0x30
#define REG_INTERRUPT_STATUS 0x20
#define REG_UIC_COMMAND 0x90
#define REG_UIC_COMMAND_ARG_1 0x94
#define REG_UIC_COMMAND_ARG_2 0x98
#define REG_UIC_COMMAND_ARG_3 0x9c
#define UIC_COMMAND_READY 8
#define MASK_UIC_COMMAND_RESULT 0xff
#define COMMAND_OPCODE_MASK 0xff
#define UFSHCD_UIC_MASK 0x400
#define UFSHCD_ERROR_MASK 4
#define UFS_UIC_CMD_TIMEOUT 10
#define UFS_UIC_COMMAND_RETRIES 3
#define UIC_CMD_DME_SET 2
#define UIC_CMD_DME_PEER_SET 3
#define UIC_CMD_DME_GET 1
#define UIC_CMD_DME_PEER_GET 4
#define UIC_ARG_ATTR_TYPE(x) ((u32)(x)<<16)
#define UIC_GET_ATTR_ID(x) ((x)>>16)
#define debug(...) do{}while(0)
#define dev_dbg(...) do{}while(0)
#define dev_err(...) do{}while(0)
struct udevice {int unused;};struct ufs_hba {struct udevice*dev;u32 intr_mask;};
struct uic_command {u32 command,argument1,argument2,argument3;};
static u32 raw[4],written[4][4],status_extra;static unsigned commands,polls,complete_after,write_count;static bool ready;static unsigned long tick;
static unsigned long get_timer(unsigned long base){return tick++-base;}
static u32 ufshcd_readl(struct ufs_hba*h,unsigned reg){(void)h;
 switch(reg){case REG_CONTROLLER_STATUS:return ready?UIC_COMMAND_READY:0;
 case REG_INTERRUPT_STATUS:polls++;return status_extra|(complete_after&&polls>=complete_after?UFSHCD_UIC_MASK:0);
 case REG_UIC_COMMAND_ARG_2:assert(commands);return raw[commands-1];
 case REG_UIC_COMMAND_ARG_3:return 0xabcdef01;default:assert(0);return 0;}}
static void ufshcd_writel(struct ufs_hba*h,u32 val,unsigned reg){(void)h;
 if(reg==REG_INTERRUPT_STATUS)return;
 assert(commands<4);unsigned i;
 switch(reg){case REG_UIC_COMMAND_ARG_1:i=0;break;case REG_UIC_COMMAND_ARG_2:i=1;break;case REG_UIC_COMMAND_ARG_3:i=2;break;
 case REG_UIC_COMMAND:i=3;break;default:assert(0);return;}
 written[commands][i]=val;write_count++;
 if(reg==REG_UIC_COMMAND){commands++;polls=0;}}
static int trace_printf(const char*fmt,...){(void)fmt;return 0;}
#define printf trace_printf
'''

C_CASES = r'''
#undef printf
static void reset(void){memset(raw,0,sizeof(raw));memset(written,0,sizeof(written));commands=polls=tick=write_count=status_extra=0;ready=true;complete_after=1;}
int main(void){struct ufs_hba h={0,UFSHCD_UIC_MASK|UFSHCD_ERROR_MASK};unsigned cases=0;
 u32 results[]={0,1,2,3,4,5,6,7,8,9,10,0x12345600,0x12345608,0xffffffff};
 for(unsigned i=0;i<sizeof(results)/sizeof(results[0]);i++){
  reset();raw[0]=results[i];struct uic_command c={0x16,0x11,0,0};
  assert(ufshcd_send_uic_cmd(&h,&c)==(results[i]&0xff));assert(c.argument2==(results[i]&0xff));assert(c.argument3==0xabcdef01);assert(commands==1&&write_count==4);cases++;
 }
 reset();complete_after=4;struct uic_command c={0x16,0,0,0};assert(ufshcd_send_uic_cmd(&h,&c)==0&&polls==4);cases++;
 reset();complete_after=0;c=(struct uic_command){0x16,0,0,0};assert(ufshcd_send_uic_cmd(&h,&c)==-ETIMEDOUT&&commands==1);cases++;
 reset();ready=false;assert(ufshcd_send_uic_cmd(&h,&c)==-EIO&&commands==0&&write_count==0);cases++;
 reset();status_extra=4;assert(ufshcd_send_uic_cmd(&h,&c)==-1&&commands==1);cases++;
 reset();raw[0]=8;raw[1]=9;u32 result=0;assert(ufshcd_dme_get_attr(&h,0x12340000,&result,1)==0&&commands==3&&result==0xabcdef01);
 for(unsigned i=0;i<commands;i++)assert(written[i][1]==0&&written[i][2]==0);cases++;
 reset();raw[0]=8;raw[1]=9;assert(ufshcd_dme_set_attr(&h,0x12340000,2,0x55,1)==0&&commands==3);
 for(unsigned i=0;i<commands;i++)assert(written[i][1]==UIC_ARG_ATTR_TYPE(2)&&written[i][2]==0x55);cases++;
 reset();raw[0]=8;raw[1]=9;raw[2]=10;result=0x55;assert(ufshcd_dme_get_attr(&h,0,&result,1)==10&&commands==3&&result==0x55);cases++;
 reset();raw[0]=8;assert(ufshcd_dme_set_attr(&h,0,0,1,0)==8&&commands==1);cases++;
 printf("PASS: %u actual-helper UIC cases (masked results, delayed completion, timeout, controller/error statuses, stable GET/SET retry arguments)\n",cases);
}
'''

def apply_patch_to_copy(tree: Path, work: Path, relative: str, patch: Path):
    """Normalize an unpatched or patched source, then apply our tracked patch."""
    source = work / relative
    source.parent.mkdir(parents=True)
    source.write_bytes((tree / relative).read_bytes())

    def git_apply(*options):
        return subprocess.run(
            ["git", "apply", *options, str(patch)], cwd=work,
            capture_output=True, text=True,
        )

    forward = git_apply("--check")
    if forward.returncode:
        reverse = git_apply("--reverse", "--check")
        if reverse.returncode:
            raise RuntimeError(
                "The source matches neither the base nor the patched version:\n"
                + forward.stderr + reverse.stderr
            )
        reversed_patch = git_apply("--reverse")
        if reversed_patch.returncode:
            raise RuntimeError(reversed_patch.stderr)
    original = source.read_text()
    applied = git_apply()
    if applied.returncode:
        raise RuntimeError(applied.stderr)
    return original, source.read_text()


def compile_helper(cc, work, name, code, trace=False):
    source = work / (name + ".c")
    binary = work / name
    source.write_text(code)
    command = cc + [
        "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined",
        "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
    ]
    if trace:
        command.append("-DHAKIOS_SPACEWAR_UFS_TRACE")
    compiled = subprocess.run(
        command + [str(source), "-o", str(binary)],
        capture_output=True, text=True,
    )
    if compiled.returncode:
        raise RuntimeError(compiled.stdout + compiled.stderr)
    return binary


def run_helper(binary, *, regression=False, arguments=()):
    result = subprocess.run(
        [str(binary), *arguments], capture_output=True, text=True,
        env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"},
    )
    if regression:
        if result.returncode == 0 or "Assertion" not in result.stderr:
            raise RuntimeError("Expected the original helper to fail its regression case:\n"
                               + result.stdout + result.stderr)
    elif result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    else:
        print(result.stdout.strip())


def arguments(description):
    repository = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--u-boot", type=Path, default=repository / "build/spacewar-uboot-src",
        help="U-Boot source tree (default: %(default)s); never modified",
    )
    args = parser.parse_args()
    cc = shlex.split(os.environ.get("HOSTCC", "cc"))
    if not cc:
        parser.error("HOSTCC must name a compiler")
    return repository, args.u_boot.resolve(), cc


def helpers(source):
    def between(first, last):
        start = source.index(first)
        return source[start:source.index(last, start)]

    return (between("static inline bool ufshcd_ready_for_uic_cmd(",
                    "/**\n * ufshcd_is_device_present")
            + between("static int ufshcd_send_uic_cmd(",
                      "int ufshcd_dme_configure_adapt(")
            + between("int ufshcd_dme_set_attr(",
                      "static int ufshcd_disable_tx_lcc("))


def main():
    repository, tree, cc = arguments(__doc__)
    patch = repository / "devices/spacewar/u-boot/ufs-uic-result.patch"
    with tempfile.TemporaryDirectory(prefix="spacewar-ufs-uic-") as temporary:
        work = Path(temporary)
        original, fixed = apply_patch_to_copy(
            tree, work, "drivers/ufs/ufs-uclass.c", patch,
        )
        regression = compile_helper(
            cc, work, "original", C_PREFIX + helpers(original) + C_CASES,
        )
        run_helper(regression, regression=True)
        print("PASS: original helper reproduces the lost UIC error result.")
        for trace in (False, True):
            binary = compile_helper(
                cc, work, "trace-on" if trace else "trace-off",
                C_PREFIX + helpers(fixed) + C_CASES, trace,
            )
            run_helper(binary)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
