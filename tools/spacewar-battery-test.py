#!/usr/bin/env python3
"""Exercise actual battery bridge/protocol functions on the host; no phone access."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
C = (ROOT / 'drivers/power/hakios.battery.c').read_text()
CPP = (ROOT / 'drivers/power/hakios.battery.cpp').read_text()
ADSP = (ROOT / 'drivers/audio/hakios.adsp.c').read_text()
INPUT = (ROOT / 'xnu/files/bsd/sys/hakios.input.h').read_text()

def block(text, start):
    at = text.index(start)
    first = text.index('{', at)
    depth = 0
    for end in range(first, len(text)):
        if text[end] == '{': depth += 1
        elif text[end] == '}':
            depth -= 1
            if depth == 0: return text[at:end + 1]
    raise ValueError('unterminated source block: ' + start)

HEAD = '''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
'''
STATE = block(INPUT, 'struct hakios_battery_state {') + ';\n'
CSTATE = r'''
static int battery_soc_percent=-1,battery_voltage_mv=-1,battery_current_ma;
static int battery_temperature_c=-273,battery_present,battery_charger_present;
static int battery_charging,battery_soc_estimated,battery_charger_action=-1;
static uint64_t battery_updates,battery_failures;
static int battery_full_uah,battery_design_uah,battery_cycles,battery_source_dsp;
static unsigned reports;
static struct hakios_battery_state last;
static struct loader_battery cache;
static bool snapshot_ok=true;
static bool battery_snapshot(struct loader_battery *out) { *out=cache;return snapshot_ok; }
static void HakiosBatteryReport(const void *state,unsigned size) {
 assert(size==sizeof(last));last=*(const struct hakios_battery_state *)state;reports++;
}
'''
CTEST = r'''
int main(void) {
 struct hakios_battery_state b={.size=sizeof(b),.percent_hundredths=10000,.voltage_uv=4490892,
  .current_ua=846569,.temperature_hundredths=3700,.present=1,.external=1,.status=1,
  .charge_uah=4357000,.full_uah=4357000,.design_uah=4357000,.usb_type=6,
  .usb_voltage_uv=4720000,.usb_current_ua=1366000};
 hakios_battery_report(&b);
 assert(reports==1 && battery_soc_percent==100 && battery_temperature_c==37);
 assert(battery_voltage_mv==4490 && battery_current_ma==846 && battery_source_dsp==1);
 assert(battery_full_uah==4357000 && battery_design_uah==4357000 && last.percent_hundredths==10000);
 b.percent_hundredths=0;hakios_battery_report(&b);assert(reports==2 && battery_soc_percent==0);
 b.percent_hundredths=3350;b.temperature_hundredths=-50;hakios_battery_report(&b);
 assert(reports==3 && battery_soc_percent==34 && battery_temperature_c==-1);
 struct hakios_battery_state good=b;
 int badsoc[]={-1,10001,INT_MIN,INT_MAX};
 for(unsigned i=0;i<sizeof(badsoc)/sizeof(*badsoc);i++) {
  b=good;b.percent_hundredths=badsoc[i];hakios_battery_report(&b);assert(reports==3 && battery_soc_percent==34);
 }
 b=good;b.size--;hakios_battery_report(&b);hakios_battery_report(NULL);assert(reports==3);
 for(unsigned i=0;i<8;i++) {
  b=good;
  switch(i) {
   case 0:b.temperature_hundredths=-4001;break;case 1:b.temperature_hundredths=10001;break;
   case 2:b.present=2;break;case 3:b.external=-1;break;case 4:b.status=5;break;
   case 5:b.current_ua=INT_MAX;break;case 6:b.voltage_uv=INT_MAX;break;case 7:b.voltage_uv=-1;break;
  }
  hakios_battery_report(&b);assert(reports==3);
 }
 cache=(struct loader_battery){.magic=LOADER_BATTERY_MAGIC,.version=LOADER_BATTERY_VERSION,.size=sizeof(cache),
  .flags=LOADER_BATTERY_VALID|LOADER_BATTERY_PRESENT|LOADER_BATTERY_VOLTAGE_VALID|LOADER_BATTERY_CHARGER_PRESENT|LOADER_BATTERY_CHARGING,
  .soc_percent=73,.voltage_mv=4100,.current_ma=500,.temperature_c=32};
 battery_poll();assert(reports==4 && last.percent_hundredths==7300 && last.voltage_uv==4100000);
 assert(last.current_ua==500000 && last.temperature_hundredths==3200 && last.status==1);
 cache.flags|=LOADER_BATTERY_SOC_ESTIMATED;battery_poll();assert(reports==4);
 cache.flags&=~LOADER_BATTERY_SOC_ESTIMATED;
 cache.voltage_mv=INT_MAX;battery_poll();assert(reports==4);
 cache.voltage_mv=4100;cache.current_ma=INT_MIN;battery_poll();assert(reports==4);
 cache.current_ma=500;cache.temperature_c=INT_MAX;battery_poll();assert(reports==4);
 cache.temperature_c=32;cache.soc_percent=-1;battery_poll();assert(reports==4);
 cache.soc_percent=73;snapshot_ok=false;battery_poll();assert(reports==4);
 puts("PASS measured battery bridge: bounds, rounding, cache/UI publication, no voltage-estimate UI");
 return 0;
}
'''
CPPSTATE = r'''
static unsigned reports,created,registered;
static struct hakios_battery_state last;
class IOService {
public:
 static IOService *getPlatform() {static IOService p;return &p;}
};
class HakiosBattery {
public:
 HakiosBattery(){created++;}
 bool init(){return true;}
 void report(const struct hakios_battery_state *b){last=*b;reports++;}
 bool attach(IOService *p){return p!=nullptr;}
 bool start(IOService *p){return p!=nullptr;}
 void detach(IOService *){}
 void release(){delete this;}
 void setName(const char *p){assert(!strcmp(p,"HakiosBattery"));}
 void registerService(){registered++;}
};
static HakiosBattery *battery;
#define OSTypeAlloc(T) new T
#define OSSafeReleaseNULL(p) do {delete p;p=nullptr;} while(0)
static void IOLog(const char *,...){}
'''
CPPTEST = r'''
int main() {
 struct hakios_battery_state b={};b.size=sizeof(b);b.percent_hundredths=7350;
 b.voltage_uv=4100000;b.current_ua=-500000;b.temperature_hundredths=3200;b.present=1;b.status=2;
 HakiosBatteryReport(nullptr,sizeof(b));assert(!reports && !created);
 HakiosBatteryReport(&b,sizeof(b)-1);assert(!reports && !created);
 for(int p : {-1,10001,INT_MIN,INT_MAX}) {b.percent_hundredths=p;HakiosBatteryReport(&b,sizeof(b));assert(!reports && !created);}
 b.percent_hundredths=7350;HakiosBatteryReport(&b,sizeof(b));assert(reports==1 && created==1 && registered==1);
 assert(last.percent_hundredths==7350 && last.present==1);
 b.percent_hundredths=0;HakiosBatteryReport(&b,sizeof(b));assert(reports==2 && created==1 && registered==1);
 b.percent_hundredths=10000;HakiosBatteryReport(&b,sizeof(b));assert(reports==3 && last.percent_hundredths==10000);
 b.temperature_hundredths=INT_MAX;HakiosBatteryReport(&b,sizeof(b));assert(reports==3);
 b.temperature_hundredths=3200;b.current_ua=INT_MIN;HakiosBatteryReport(&b,sizeof(b));assert(reports==3);
 delete battery;
 puts("PASS IOPMPowerSource entry: rejects unknown SOC, publishes measured samples, registers once");
}
'''
PROJECTSTATE = r'''
#include <map>
#include <string>
using properties=std::map<std::string,long long>;
struct OSObject {virtual ~OSObject(){};void release(){delete this;}};
struct OSNumber:OSObject {
 unsigned long long value;
 static OSNumber *withNumber(unsigned long long v,unsigned bits){assert(bits==32);auto *n=new OSNumber;n->value=v;return n;}
};
struct OSBoolean:OSObject {bool value;explicit OSBoolean(bool v):value(v){}};
static OSBoolean falseValue(false);
static OSBoolean *kOSBooleanFalse=&falseValue;
struct OSDictionary:OSObject {
 properties values;
 static OSDictionary *withCapacity(unsigned n){assert(n==1 || n==6);return new OSDictionary;}
 void setObject(const char *key,OSObject *o){
  if(auto n=dynamic_cast<OSNumber *>(o))values[key]=(long long)n->value;
  else if(auto b=dynamic_cast<OSBoolean *>(o))values[key]=b->value;
  else assert(false);
 }
};
#define kIOPMPSAdapterDetailsWattsKey "Watts"
#define kIOPMPSAdapterDetailsAmperageKey "Amperage"
#define kIOPMPSAdapterDetailsVoltage "Voltage"
#define kIOPMPSAdapterDetailsFamilyKey "FamilyCode"
#define kIOPMPSAdapterDetailsKey "AdapterDetails"
#define OSSafeReleaseNULL(p) do {delete p;p=nullptr;} while(0)
class HakiosBattery {
public:
 properties values,adapter;unsigned updates=0;
 void number(const char *k,long long v){values[k]=v;}
 void flag(const char *k,bool v){values[k]=v;}
 void object(const char *k,OSObject *v){assert(!strcmp(k,"AdapterDetails"));adapter=dynamic_cast<OSDictionary *>(v)->values;}
 void setBatteryInstalled(bool v){flag("Installed",v);}
 void setExternalConnected(bool v){flag("External",v);}
 void setExternalChargeCapable(bool v){flag("ChargeCapable",v);}
 void setIsCharging(bool v){flag("Charging",v);}
 void setCurrentCapacity(unsigned v){number("Capacity",v);}
 void setMaxCapacity(unsigned v){number("MaxCapacity",v);}
 void setVoltage(unsigned v){number("Voltage",v);}
 void setAmperage(int v){number("Amperage",v);}
 void setCycleCount(unsigned v){number("Cycles",v);}
 void updateStatus(){updates++;}
 void report(const struct hakios_battery_state *);
};
'''
PROJECTTEST = r'''
int main() {
 struct hakios_battery_state b={};b.size=sizeof(b);b.percent_hundredths=10000;
 b.voltage_uv=4490892;b.current_ua=846569;b.temperature_hundredths=3700;b.present=1;b.external=1;b.status=1;
 b.charge_uah=b.full_uah=b.design_uah=4357000;b.usb_type=6;b.usb_voltage_uv=4720000;b.usb_current_ua=1366000;
 HakiosBattery battery;battery.report(&b);
 assert(battery.updates==1 && battery.values["Capacity"]==100 && battery.values["MaxCapacity"]==100);
 assert(battery.values["Installed"] && battery.values["External"] && battery.values["Charging"]);
 assert(battery.values["Voltage"]==4490 && battery.values["Amperage"]==846 && battery.values["Temperature"]==3700);
 assert(battery.values["AppleRawCurrentCapacity"]==4357 && battery.values["AppleRawMaxCapacity"]==4357 && battery.values["DesignCapacity"]==4357);
 assert(battery.adapter["FamilyCode"]==0xe000400a && battery.adapter["Voltage"]==4720 && battery.adapter["Amperage"]==1366);
 b.percent_hundredths=3350;b.external=0;b.status=2;b.current_ua=-500000;battery.report(&b);
 assert(battery.updates==2 && battery.values["Capacity"]==34 && battery.values["Amperage"]==-500);
 assert(!battery.values["External"] && !battery.values["Charging"] && !battery.values["FullyCharged"]);
 assert(battery.adapter["FamilyCode"]==0);
 b.percent_hundredths=0;battery.report(&b);assert(battery.values["Capacity"]==0);
 puts("PASS power-source projection: Android reference SOC/voltage/current/temp/capacity/USB-PD, discharge transition");
}
'''
PROTOSTATE = r'''
#define __unused __attribute__((unused))
#define printf(...) ((void)0)
#define bzero(p,n) memset((p),0,(n))
#define release 0
#define acquire 0
#define os_atomic_inc(p,order) (++*(p))
#define os_atomic_load(p,order) (*(p))
static void tell(const char *fmt,...){(void)fmt;}
static unsigned sends,kind;
struct {int channel[1];} glink;
#define CHANNEL_PMIC 0
static int glink_send(void *,const void *,unsigned);
static void sleep_ms(unsigned n){assert(n==1);}
'''
PROTOSEND = r'''
static int glink_send(void *channel,const void *payload,unsigned length) {
 (void)channel;assert(length==sizeof(struct battmgr_message));
 const struct battmgr_message *q=payload;assert(q->owner==32778 && q->type==1);
 assert(q->opcode==0x30 || q->opcode==0x32);assert(q->words[0]==0 && q->words[2]==0);sends++;
 if(kind==3)return -1;
 struct battmgr_message m={.owner=32778,.type=kind==1?0:1,.opcode=q->opcode,.words={q->words[1],3700,kind==2?1:0}};
 battmgr_input((const uint8_t *)&m,sizeof(m));return 0;
}
'''
PROTOTEST = r'''
int main(void) {
#if HAKIOS_DEVICE_SPACEWAR
 assert(BATT_TEMP==12 && BATT_CHG_COUNTER==14 && BATT_CYCLE_COUNT==15 && BATT_CHG_FULL_DESIGN==16 && BATT_CHG_FULL==17 && USB_TYPE==7);
#else
 assert(BATT_TEMP==13 && BATT_CHG_COUNTER==15 && BATT_CYCLE_COUNT==16 && BATT_CHG_FULL_DESIGN==17 && BATT_CHG_FULL==18 && USB_TYPE==6);
#endif
 uint32_t value=0;assert(battmgr_get(0x30,4,&value) && value==3700 && sends==1);
 assert(battmgr_get(0x32,USB_TYPE,&value) && sends==2);
 for(unsigned op=0;op<256;op++)if(op!=0x30 && op!=0x32)assert(!battmgr_get(op,4,&value));
 assert(!battmgr_get(0x30,4,NULL) && sends==2);
 for(kind=1;kind<=3;kind++)assert(!battmgr_get(0x30,4,&value));
 unsigned before=battmgr.answers;
 struct battmgr_message m={.owner=32778,.type=1,.opcode=0x30,.words={4,10000,0}};
 for(unsigned len=0;len<sizeof(m);len++)battmgr_input((const uint8_t *)&m,len);
 assert(battmgr.answers==before);
 uint8_t model[140]={0};memcpy(model,&m,sizeof(m));battmgr_input(model,sizeof(model));assert(battmgr.answers==before);
 m.type=2;battmgr_input((const uint8_t *)&m,sizeof(m));assert(battmgr.answers==before);
 m.type=1;m.opcode=0x31;battmgr_input((const uint8_t *)&m,sizeof(m));assert(battmgr.answers==before);
 m.opcode=0x30;m.owner=1;battmgr_input((const uint8_t *)&m,sizeof(m));assert(battmgr.answers==before);
 m.owner=32778;battmgr_input((const uint8_t *)&m,sizeof(m));assert(battmgr.answers==before+1);
 puts("PASS battery profile/protocol: exact property IDs, GET-only, strict replies");
}
'''

def run(cc, suffix, data, flags=()):
    with tempfile.TemporaryDirectory(prefix='spacewar-battery-') as tmp:
        path=Path(tmp)/('test'+suffix);path.write_text(data)
        binary=Path(tmp)/'test'
        subprocess.run([cc,'-std=c++20' if suffix=='.cpp' else '-std=c11','-Wall','-Wextra',
                        '-Werror','-Wno-unused-function','-Wno-unused-variable','-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer',*flags,str(path),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})

if __name__=='__main__':
    cache_def=C[C.index('#define LOADER_BATTERY_MAGIC'):C.index('static volatile struct loader_battery')]
    cache_def=cache_def[:cache_def.index('};')+2]+'\n'
    run(os.environ.get('CC','clang'),'.c',HEAD+STATE+cache_def+CSTATE+
        block(C,'static void\nbattery_poll(void)')+'\n'+
        block(C,'void\nhakios_battery_report(')+'\n'+CTEST)
    run(os.environ.get('CXX','clang++'),'.cpp',HEAD+'#include <initializer_list>\n'+STATE+CPPSTATE+
        block(CPP,'void\nHakiosBatteryReport(')+'\n'+CPPTEST)
    run(os.environ.get('CXX','clang++'),'.cpp',HEAD+STATE+PROJECTSTATE+
        block(CPP,'static unsigned\nfamily_code(')+'\n'+
        block(CPP,'void\nHakiosBattery::report(')+'\n'+PROJECTTEST)
    protocol=ADSP[ADSP.index('#define BATTMGR_OWNER'):ADSP.index('static void\nbattmgr_input(')]
    for board in (0,1):
        run(os.environ.get('CC','clang'),'.c',HEAD+PROTOSTATE+protocol+
            block(ADSP,'static void\nbattmgr_input(')+'\n'+
            block(ADSP,'static bool\nbattmgr_get(')+'\n'+PROTOSEND+PROTOTEST,
            ('-DHAKIOS_DEVICE_SPACEWAR='+str(board),))
