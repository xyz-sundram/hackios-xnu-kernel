#!/usr/bin/env python3
"""Test the actual U-Boot UFS OPP lookup helper with ASAN/UBSAN.

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
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdbool.h>
typedef uint32_t u32;typedef uint64_t u64;typedef int ofnode;
#define U32_MAX UINT32_MAX
#define UNIPRO_CORE_CLK_FREQ_150_MHZ 150
struct udevice {int unused;};struct ufs_hba{struct udevice *dev;};struct ufs_qcom_priv{struct ufs_hba *hba;};
struct ofnode_phandle_args{ofnode node;};
static struct udevice dev;static struct ufs_hba hba={&dev};static struct ufs_qcom_priv priv={&hba};
static int position=3,table_present=1,compatible=1,node_count=3;
static u64 rates[8];static int node_error[8],legacy_error=1;static u32 legacy;
static void *dev_get_priv(struct udevice *d){assert(d==&dev);return &priv;}
static ofnode dev_ofnode(struct udevice *d){assert(d==&dev);return 0;}
static int ofnode_stringlist_search(ofnode n,const char *p,const char *s){assert(n==0&&!strcmp(p,"clock-names")&&!strcmp(s,"core_clk_unipro"));return position;}
static int ofnode_parse_phandle_with_args(ofnode n,const char *p,const char *cells,int a,int b,struct ofnode_phandle_args *out){assert(n==0&&!cells&&!a&&!b);if(!table_present)return -1;out->node=!strcmp(p,"operating-points-v2")?1:9;return 0;}
static bool ofnode_device_is_compatible(ofnode n,const char *s){assert(!strcmp(s,"operating-points-v2"));return n==1&&compatible;}
#define ofnode_for_each_subnode(n,p) for(assert((p)==1),(n)=2;(n)<2+node_count;(n)++)
static int ofnode_read_u64_index(ofnode n,const char *p,int index,u64 *value){assert(n>=2&&n<2+node_count&&!strcmp(p,"opp-hz")&&index==position);*value=rates[n-2];return node_error[n-2];}
static int ofnode_read_u32_index(ofnode n,const char *p,int index,u32 *value){assert(n==0&&!strcmp(p,"freq-table-hz")&&index==position*2+1);*value=legacy;return legacy_error;}
'''

C_CASES = r'''
static void reset(void){position=3;table_present=compatible=1;node_count=3;rates[0]=75000000;rates[1]=150000000;rates[2]=300000000;memset(node_error,0,sizeof(node_error));legacy_error=1;legacy=0;}
int main(int argc,char **argv){(void)argv;reset();assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==300000000);if(argc>1)return 0;
 unsigned count=1;
 position=1;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==300000000);count++;
 reset();position=-1;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==150000000);count++;
 reset();table_present=0;legacy_error=0;legacy=300000000;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==300000000);count++;
 reset();compatible=0;legacy_error=0;legacy=75000000;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==75000000);count++;
 reset();table_present=0;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==150000000);count++;
 reset();rates[2]=1ULL<<32;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==150000000);count++;
 reset();node_error[1]=node_error[2]=-1;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==75000000);count++;
 reset();rates[0]=rates[1]=rates[2]=0;legacy_error=0;legacy=300000000;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==300000000);count++;
 reset();node_count=0;assert(ufs_qcom_get_core_clk_unipro_max_freq(&hba)==150000000);count++;
 printf("PASS: %u actual OPP-helper cases; clock index, max300MHz, legacy/default fallback, incompatible/empty/malformed and >32bit entries\n",count);return 0;}
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


def helper(source):
    start = source.index("static u32 ufs_qcom_get_core_clk_unipro_max_freq(")
    end = source.index("\n};", start) + 3
    return source[start:end] + "\n"


def main():
    repository, tree, cc = arguments(__doc__)
    patch = repository / "devices/spacewar/u-boot/ufs-opp-table.patch"
    with tempfile.TemporaryDirectory(prefix="spacewar-ufs-opp-") as temporary:
        work = Path(temporary)
        original, fixed = apply_patch_to_copy(
            tree, work, "drivers/ufs/ufs-qcom.c", patch,
        )
        regression = compile_helper(
            cc, work, "original", C_PREFIX + helper(original) + C_CASES,
        )
        run_helper(regression, regression=True, arguments=("reproduce",))
        print("PASS: original helper reproduces the wrong 150 MHz fallback.")
        binary = compile_helper(
            cc, work, "fixed", C_PREFIX + helper(fixed) + C_CASES,
        )
        run_helper(binary)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
