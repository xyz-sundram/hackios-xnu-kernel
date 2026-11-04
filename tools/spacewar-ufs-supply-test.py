#!/usr/bin/env python3
"""Test UFS supply handling and the pinned RPMh mode-on-enable path on the host.

Applies the local supply patch to a temporary copy of U-Boot HEAD and compiles
actual helper functions with ASan/UBSan. No phone commands or source-tree writes.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

HOST_STUBS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define REGULATOR_TYPE_FIXED 3
#define REGULATOR_MODE_HPM 3
#define RPMH_REGULATOR_REG_ENABLE 4
#define RPMH_REGULATOR_REG_VRM_MODE 8
#define PMIC5_BOB_MODE_PASS 2
#define dev_err(...) do{}while(0)
#define debug(...) do{}while(0)
#define EPROBE_DEFER 517
struct udevice{const char *name;int index;};
struct dm_regulator_uclass_plat{int init_uV,min_uV,max_uV,type;bool force_off;};
struct dm_regulator_mode{int id,register_value;};
struct rpmh_vreg_hw_data{unsigned n_modes;struct dm_regulator_mode *pmic_mode_map;};
struct rpmh_vreg{struct udevice *dev;uint32_t addr;int mode,enabled,uv;bool bypassed;struct rpmh_vreg_hw_data *hw_data;};
struct tcs_cmd{uint32_t addr,data;};
static struct udevice host={"ufs-host",-1},regs[]={{"vcc",0},{"vccq",1},{"vccq2",2}};
static struct dm_regulator_uclass_plat plats[3];static struct rpmh_vreg vregs[3];
static struct dm_regulator_mode modes[]={{0,3},{1,4},{3,7}};static struct rpmh_vreg_hw_data hw={3,modes};
static bool present[3],null_reg,null_plat;static int failed_supply,lookup_error,value_error,enable_error,request_failure;
static unsigned lookups,value_calls,enable_calls,getters,requests;
static uint32_t sent_addr[32],sent_data[32];static int requested_uV;
static const char *const names[]={"vcc-supply","vccq-supply","vccq2-supply"};
static int index_for(const char *name){for(unsigned i=0;i<3;i++)if(!strcmp(name,names[i]))return i;assert(0);return-1;}
static const void *dev_read_prop(struct udevice *dev,const char *name,int *len){assert(dev==&host&&!len);int i=index_for(name);return present[i]?&present[i]:NULL;}
static int device_get_supply_regulator(struct udevice *dev,const char *name,struct udevice **out){assert(dev==&host);unsigned i=index_for(name);lookups++;assert(present[i]);if((int)i==failed_supply)return lookup_error;*out=null_reg?NULL:&regs[i];return 0;}
static void *dev_get_uclass_plat(struct udevice *dev){assert(dev&&dev->index>=0);return null_plat?NULL:&plats[dev->index];}
static void *dev_get_priv(struct udevice *dev){assert(dev&&dev->index>=0);return &vregs[dev->index];}
static int dev_read_u32_default(struct udevice *dev,const char *name,int fallback){assert(dev&&!strcmp(name,"regulator-initial-mode"));(void)fallback;return 3;}
static int rpmh_regulator_send_request(struct rpmh_vreg *vreg,struct tcs_cmd *cmd,bool wait){assert(vreg);(void)wait;assert(requests<ARRAY_SIZE(sent_addr));sent_addr[requests]=cmd->addr;sent_data[requests]=cmd->data;requests++;return requests==(unsigned)request_failure?-EIO:0;}
static int _rpmh_regulator_vrm_set_value(struct udevice *dev,int uv,bool wait){struct rpmh_vreg *v=dev_get_priv(dev);struct tcs_cmd cmd={v->addr,(uint32_t)uv/1000};return rpmh_regulator_send_request(v,&cmd,wait);}
static int regulator_set_value(struct udevice *dev,int uv){assert(dev);value_calls++;requested_uV=uv;if(value_error)return value_error;vregs[dev->index].uv=uv;return 0;}
static int regulator_set_enable(struct udevice *dev,bool enabled);
static int regulator_get_value(struct udevice *dev){assert(dev&&vregs[dev->index].enabled==1);getters++;return 2952000;}
static int regulator_get_mode(struct udevice *dev){assert(dev&&vregs[dev->index].enabled==1);getters++;return 3;}
static int regulator_get_enable(struct udevice *dev){assert(dev&&vregs[dev->index].enabled==1);getters++;return 1;}
static int silent_printf(const char *fmt,...){(void)fmt;return 0;}
#define printf silent_printf
"""

ENABLE_BRIDGE = r"""
static int regulator_set_enable(struct udevice *dev,bool enabled){assert(dev&&enabled);enable_calls++;if(enable_error)return enable_error;return rpmh_regulator_set_enable_state(dev,enabled);}
"""

TEST_CASES = r"""
#undef printf
static unsigned cases;
static void reset(void){memset(present,0,sizeof(present));memset(plats,0,sizeof(plats));memset(vregs,0,sizeof(vregs));memset(sent_addr,0,sizeof(sent_addr));lookups=value_calls=enable_calls=getters=requests=0;lookup_error=value_error=enable_error=request_failure=0;failed_supply=-1;null_reg=null_plat=false;requested_uV=0;
 for(unsigned i=0;i<3;i++){plats[i].min_uV=2400000;plats[i].max_uV=3544000;plats[i].init_uV=-ENODATA;vregs[i].dev=&regs[i];vregs[i].addr=0x40000+i*0x100;vregs[i].mode=3;vregs[i].enabled=-EINVAL;vregs[i].uv=-ENOTRECOVERABLE;vregs[i].hw_data=&hw;}}
int main(void){
 reset();assert(ufs_qcom_enable_supplies(&host)==0);assert(!lookups&&!requests);cases++;
 reset();present[0]=present[1]=true;assert(ufs_qcom_enable_supplies(&host)==0);assert(lookups==2&&requests==4&&!value_calls);assert(sent_addr[0]==vregs[0].addr+8&&sent_data[0]==7);assert(sent_addr[1]==vregs[0].addr+4&&sent_data[1]==1);assert(sent_addr[2]==vregs[1].addr+8&&sent_data[2]==7);cases++;
 reset();present[0]=true;plats[0].init_uV=2952000;assert(!ufs_qcom_enable_supplies(&host));assert(value_calls==1&&requested_uV==2952000&&requests==3);assert(sent_data[0]==7&&sent_addr[0]==vregs[0].addr+8);assert(sent_addr[1]==vregs[0].addr&&sent_data[1]==2952);assert(sent_addr[2]==vregs[0].addr+4);cases++;
 reset();present[0]=true;plats[0].min_uV=plats[0].max_uV=2952000;assert(!ufs_qcom_enable_supplies(&host));assert(value_calls==1&&requested_uV==2952000);cases++;
 reset();present[0]=true;plats[0].type=REGULATOR_TYPE_FIXED;plats[0].min_uV=plats[0].max_uV=2952000;assert(!ufs_qcom_enable_supplies(&host));assert(!value_calls);cases++;
 int errors[]={-ENOENT,-ENODEV,-EPROBE_DEFER,-EIO};
 for(unsigned i=0;i<ARRAY_SIZE(errors);i++){reset();present[0]=true;failed_supply=0;lookup_error=errors[i];assert(ufs_qcom_enable_supplies(&host)==errors[i]);assert(!value_calls&&!enable_calls&&!requests);cases++;}
 reset();present[0]=true;null_reg=true;assert(ufs_qcom_enable_supplies(&host)==-ENODEV);assert(!requests);cases++;
 reset();present[0]=true;null_plat=true;assert(ufs_qcom_enable_supplies(&host)==-EINVAL);assert(!requests);cases++;
 reset();present[0]=true;plats[0].force_off=true;assert(ufs_qcom_enable_supplies(&host)==-EACCES);assert(!requests);cases++;
 reset();present[0]=true;plats[0].init_uV=4000000;assert(ufs_qcom_enable_supplies(&host)==-EINVAL);assert(!requests&&!value_calls);cases++;
 reset();present[0]=true;plats[0].init_uV=2952000;value_error=-ERANGE;assert(ufs_qcom_enable_supplies(&host)==-ERANGE);assert(value_calls==1&&!enable_calls&&!requests);cases++;
 reset();present[0]=true;enable_error=-ENOSYS;assert(ufs_qcom_enable_supplies(&host)==-ENOSYS);assert(enable_calls==1&&!requests);cases++;
 reset();present[0]=true;request_failure=1;assert(ufs_qcom_enable_supplies(&host)==-EIO);assert(requests==1&&sent_addr[0]==vregs[0].addr+8&&sent_data[0]==7);assert(vregs[0].enabled==-EINVAL);cases++;
 reset();present[0]=true;request_failure=2;assert(ufs_qcom_enable_supplies(&host)==-EIO);assert(requests==2&&vregs[0].enabled==-EINVAL);cases++;
 reset();present[0]=present[1]=present[2]=true;failed_supply=1;lookup_error=-EIO;assert(ufs_qcom_enable_supplies(&host)==-EIO);assert(lookups==2&&enable_calls==1&&requests==2);cases++;
 printf("PASS: %u actual supply/RPMh mode-enable cases; absent optional, declared failures, no NULL calls, HPM7 before enable, retained variable voltage, explicit/fixed voltage, failures stop later rails\n",cases);return 0;}
"""


def function(source: str, name: str) -> str:
    pattern = r"^static int\s+" + re.escape(name) + r"\([^;]*?\)\n\{"
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        raise ValueError(f"cannot find actual helper {name}")
    end = source.index("\n}", match.end()) + 2
    return source[match.start():end] + "\n"


def from_head(tree: Path, name: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(tree), "show", "HEAD:" + name], text=True
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree", type=Path,
        default=Path(os.environ.get("U_BOOT", str(ROOT / "u-boot"))),
        help="U-Boot tree (default: U_BOOT or repository/u-boot)",
    )
    args = parser.parse_args()
    tree = args.tree.resolve()
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    if not compiler:
        parser.error("HOSTCC must name a host C compiler")
    with tempfile.TemporaryDirectory(prefix="spacewar-ufs-supplies-") as temporary:
        work = Path(temporary)
        source = work / "drivers/ufs/ufs-qcom.c"
        source.parent.mkdir(parents=True)
        source.write_text(from_head(tree, "drivers/ufs/ufs-qcom.c"))
        patch = ROOT / "devices/spacewar/u-boot/ufs-host-supplies.patch"
        subprocess.run(["git", "apply", str(patch)], cwd=work, check=True)
        regulator = from_head(tree, "drivers/power/regulator/qcom-rpmh-regulator.c")
        functions = function(regulator, "rpmh_regulator_vrm_set_mode_bypass")
        functions += function(regulator, "rpmh_regulator_set_enable_state")
        functions += ENABLE_BRIDGE
        functions += function(source.read_text(), "ufs_qcom_enable_supply")
        functions += function(source.read_text(), "ufs_qcom_enable_supplies")
        for trace in (False, True):
            test = work / "test.c"
            binary = work / "test"
            test.write_text(HOST_STUBS + functions + TEST_CASES)
            command = compiler + [
                "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all",
            ]
            if trace:
                command.append("-DHAKIOS_SPACEWAR_UFS_TRACE")
            subprocess.run(command + [str(test), "-o", str(binary)], check=True)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True,
                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"},
            )
            if result.returncode:
                raise RuntimeError(result.stderr)
            print(("trace-on: " if trace else "trace-off: ") + result.stdout.strip())


if __name__ == "__main__":
    main()
