#!/usr/bin/env python3
"""Run production native DCS/FDT code against a bounded controller provider.

No phone or private firmware needed. Exercises packet byte order, actual
readback, unchanged display/clock/PHY registers, state refusal and timeout.
"""
import os
import argparse
from pathlib import Path
import subprocess
import tempfile
from fdtbuild import fdt, compat

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("dtb", nargs="?", type=Path, help="also validate the actual U-Boot control DTB")
args = parser.parse_args()
harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>
#include "hakios.spacewar.dcs.h"
struct Device {
 uint32_t regs[256]={};std::vector<uint32_t> fifo;
 std::vector<uint32_t> short_commands;
 unsigned writes=0,triggers=0,brightness_writes=0,microseconds=0;
 unsigned panel_page=0,refresh_mode=0;
 uint16_t raw=1541;bool timeout=false,hung=false,error=false,mismatch=false;
 bool ignore_hs=false;unsigned lp_writes=0,hs_writes=0,apply_delay=0,apply_at=0;uint16_t pending=0;
 uint32_t response_type=0,count=4,power=0x9c;
 Device(){regs[0]=0x20050001;regs[1]=0x010001f5;regs[0x84/4]=4;
  regs[0x3c/4]=0x14000000;regs[0x4c/4]=64;regs[0x110/4]=(1<<8)|(1<<9);}
};
static uint32_t read_reg(void *context,uint32_t offset){
 assert(offset<0x400&&!(offset&3));return ((Device*)context)->regs[offset/4];
}
static void write_reg(void *context,uint32_t offset,uint32_t value){
 auto &d=*(Device*)context;d.writes++;
 switch(offset){
 case 0x110:d.regs[offset/4]=(d.regs[offset/4]&0x55555555u&~value)|(value&0xaaaaaaaau);break;
 case 0x1d4:if(value&1)d.regs[offset/4]=0;break;
 case 0x1ec:if(value)d.fifo.clear();d.regs[offset/4]=value;break;
 case 0x17c:d.fifo.push_back(value);break;
 case 0x15c:case 0x3c:case 0x4c:d.regs[offset/4]=value;break;
 case 0x90:{
  assert(value==1&&d.fifo.size()==2&&d.regs[0x15c/4]==0x30006);d.triggers++;
  if(d.timeout){d.regs[8/4]=d.hung?2:0;break;}
  d.regs[0x110/4]|=d.error?(1u<<24):1u;
  uint32_t h=d.fifo[0],type=(h>>16)&0x3f;
  if(type==0x39){
   assert(h==0xc0390003&&d.regs[0x4c/4]==8);
   uint32_t p=d.fifo[1];assert((p&0xff0000ff)==0xff000051);
   bool lp=(d.regs[0x3c/4]&(1u<<26))!=0;
   if(lp)d.lp_writes++;else d.hs_writes++;
   uint16_t wanted=((p>>8)&255)<<8|((p>>16)&255);d.brightness_writes++;
   if(lp||!d.ignore_hs){
    if(d.apply_delay){d.pending=wanted;d.apply_at=d.microseconds+d.apply_delay;}
    else d.raw=wanted;
   }
  } else if(type==0x15){
   assert(d.regs[0x4c/4]==4&&d.fifo[1]==0);d.short_commands.push_back(h);
   if((h&255)==0xfe)d.panel_page=(h>>8)&255;
   if((h&255)==0xbd&&d.panel_page==0x40)d.refresh_mode=(h>>8)&255;
  } else if(type==0x37){
   assert((h==0x80370001||h==0x80370002)&&d.regs[0x4c/4]==4&&d.fifo[1]==0);
  } else {
   assert(type==6&&(h==0xa006000a||h==0xa0060052||(h==0xa00600bd&&d.panel_page==0x40))&&d.regs[0x4c/4]==4&&d.fifo[1]==0);
   assert(d.regs[0x3c/4]&(1u<<26));
   if(d.apply_at&&d.microseconds>=d.apply_at){d.raw=d.pending;d.apply_at=0;}
   uint32_t response=h==0xa00600bd?(0x21u<<24)|(d.refresh_mode<<16):h==0xa006000a?(0x21u<<24)|(d.power<<16):
    (0x22u<<24)|((uint32_t)(d.raw+(d.mismatch?1:0))<<8);
   if(d.response_type)response=(response&0xffffff)|(d.response_type<<24);
   d.regs[0x6c/4]=response;d.regs[0x1d4/4]=d.count<<16;d.regs[0x110/4]|=1u<<20;
  }
  break;
 }
 default:assert(!"native brightness modified a display/clock/power/PHY register");
 }
}
static void delay(void *context,uint32_t microseconds){((Device*)context)->microseconds+=microseconds;}
static sw_dcs_io io(Device &d){sw_dcs_io b={};b.context=&d;b.read=read_reg;b.write=write_reg;b.delay=delay;return b;}
static void restored(Device &d){
 assert(d.regs[0x3c/4]==0x14000000&&d.regs[0x4c/4]==64&&!d.regs[0x15c/4]);
 assert(d.regs[0x110/4]==((1u<<8)|(1u<<9)));
}
int main(int argc,char **argv){
 assert(argc==2||argc==3);std::ifstream file(argv[1],std::ios::binary);assert(file);
 std::vector<uint8_t> tree((std::istreambuf_iterator<char>(file)),{});unsigned checks=0;
 assert(sw_dcs_spacewar_fdt(tree.data(),tree.size()));checks++;
 if(argc==3){std::ifstream real(argv[2],std::ios::binary);assert(real);
  std::vector<uint8_t> control((std::istreambuf_iterator<char>(real)),{});
  assert(sw_dcs_spacewar_fdt(control.data(),control.size()));checks++;}
 for(unsigned n=0;n<tree.size();n++){assert(!sw_dcs_spacewar_fdt(tree.data(),n));checks++;}
 for(unsigned n=0;n<tree.size();n++){
  tree[n]^=255;(void)sw_dcs_spacewar_fdt(tree.data(),tree.size());tree[n]^=255;checks++;
 }
 auto original=tree;
 for(const char *word:{"nothing,spacewar","nothing,rm692e5-spacewar"}){
  for(unsigned n=0;n+strlen(word)<tree.size();n++)if(!memcmp(tree.data()+n,word,strlen(word))){
   tree[n]='x';assert(!sw_dcs_spacewar_fdt(tree.data(),tree.size()));tree=original;checks++;break;
  }
 }
 {Device d;auto b=io(d);uint16_t raw=0;
  assert(!sw_dcs_initial(&b,&raw)&&raw==1541&&d.triggers==4&&!d.brightness_writes);restored(d);checks++;}
 {Device d;auto b=io(d);uint16_t mode=99;
  assert(!sw_dcs_refresh(&b,120)&&!sw_dcs_refresh_readback(&b,&mode)&&mode==7&&d.panel_page==0);checks++;
  assert(!sw_dcs_refresh(&b,60)&&!sw_dcs_refresh_readback(&b,&mode)&&mode==0&&d.panel_page==0);checks++;
 }
 for(unsigned hz:{60u,120u}) {
  Device d;auto b=io(d);assert(!sw_dcs_refresh(&b,hz));
  std::vector<uint32_t> expected={0x801540feu,hz==120?0x801507bdu:0x801500bdu,0x801500feu};
  assert(d.short_commands==expected&&d.triggers==3);restored(d);checks++;
 }
 {Device d;auto b=io(d);assert(sw_dcs_refresh(&b,90)==SW_DCS_RANGE&&!d.writes);checks++;}
 {Device d;d.timeout=true;auto b=io(d);assert(sw_dcs_refresh(&b,120)==SW_DCS_TIMEOUT&&d.triggers==1);checks++;}
 for(unsigned raw:{0u,1u,16u,255u,1024u,1541u,2662u,3515u,4095u}){
  Device d;auto b=io(d);assert(!sw_dcs_set(&b,raw)&&d.raw==raw&&d.brightness_writes==1&&d.triggers==3);
  restored(d);checks++;
 }
 for(unsigned raw:{4096u,65535u}){Device d;auto b=io(d);assert(sw_dcs_set(&b,raw)==SW_DCS_RANGE&&!d.writes);checks++;}
 for(unsigned mode=0;mode<8;mode++){
  Device d;auto b=io(d);
  if(mode==0)d.regs[0]=0;
  if(mode==1)d.regs[1]&=~4u;
  if(mode==2)d.regs[1]|=2u;
  if(mode==3)d.regs[0x84/4]=5;
  if(mode==4)d.regs[0x15c/4]=1;
  if(mode==5)d.regs[0x2cc/4]=1;
  if(mode==6)d.regs[0x68/4]=1;
  if(mode==7)d.regs[0xc0/4]=0x100;
  assert(sw_dcs_set(&b,100)&&!d.writes);checks++;
 }
 {Device d;auto b=io(d);d.regs[8/4]=6;
  assert(sw_dcs_set(&b,100)==SW_DCS_BUSY&&!d.writes&&d.microseconds==20000);checks++;}
 for(bool hung:{false,true}){
  Device d;auto b=io(d);d.timeout=true;d.hung=hung;
  assert(sw_dcs_set(&b,100)==SW_DCS_TIMEOUT&&b.failed&&d.microseconds>=20000);
  if(!hung)restored(d);else assert(d.regs[0x15c/4]==0x30006);
  unsigned writes=d.writes;assert(sw_dcs_set(&b,200)&&d.writes==writes);checks++;
 }
 {Device d;auto b=io(d);d.error=true;assert(sw_dcs_set(&b,100)==SW_DCS_ERROR&&b.failed);checks++;}
 {Device d;auto b=io(d);d.mismatch=true;assert(sw_dcs_set(&b,100)==SW_DCS_VERIFY);restored(d);checks++;}
 {Device d;auto b=io(d);d.apply_delay=35000;
  assert(!sw_dcs_set(&b,1024)&&d.raw==1024&&d.brightness_writes==1&&!b.use_lpm&&b.verify_reads==5);
  assert(d.microseconds>=35000&&d.microseconds<50000);restored(d);checks++;}
 {Device d;auto b=io(d);d.ignore_hs=true;
  assert(!sw_dcs_set(&b,1024)&&d.raw==1024&&b.use_lpm&&d.hs_writes==1&&d.lp_writes==1&&b.verify_reads==7);
  assert(!sw_dcs_set(&b,2662)&&d.raw==2662&&d.hs_writes==1&&d.lp_writes==2&&b.verify_reads==1);
  restored(d);checks++;}
 {Device d;auto b=io(d);d.response_type=2;uint16_t v=0;assert(sw_dcs_initial(&b,&v)==SW_DCS_RESPONSE&&!d.brightness_writes);checks++;}
 {Device d;auto b=io(d);d.power=0;uint16_t v=0;assert(sw_dcs_initial(&b,&v)==SW_DCS_STATE&&!d.brightness_writes);checks++;}
 {Device d;auto b=io(d);d.raw=4096;uint16_t v=0;assert(sw_dcs_initial(&b,&v)==SW_DCS_RANGE&&!d.brightness_writes);checks++;}
 for(unsigned count:{0u,3u,16u}){Device d;auto b=io(d);d.count=count;uint16_t v=0;assert(sw_dcs_initial(&b,&v));checks++;}
 {Device d;auto b=io(d);d.count=8;uint16_t v=0;assert(!sw_dcs_initial(&b,&v)&&v==1541);checks++;}
 printf("PASS %u production native DCS/FDT cases; only command registers written\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix="spacewar-dcs-") as temporary:
    folder = Path(temporary)
    tree = fdt({"": ({"compatible": compat("nothing,spacewar", "qcom,sm7325")},
        {"soc": ({}, {"panel": ({"compatible": compat("nothing,rm692e5-spacewar", "visionox,rm692e5")}, {})})})})
    (folder / "phone.dtb").write_bytes(tree)
    (folder / "test.cpp").write_text(harness)
    subprocess.run([os.environ.get("CXX", "clang++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-g", "-I"+str(ROOT/"drivers/gpu"),
        str(folder/"test.cpp"), "-o", str(folder/"test")], check=True)
    command = [str(folder/"test"), str(folder/"phone.dtb")]
    if args.dtb:
        command.append(str(args.dtb.resolve(strict=True)))
    subprocess.run(command, check=True,
        env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
