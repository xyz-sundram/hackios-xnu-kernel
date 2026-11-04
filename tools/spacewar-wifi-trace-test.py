#!/usr/bin/env python3
"""Execute real Wi-Fi trace helpers against strict CF/I/O mocks, no device."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'userland/src/inject_wifi.c').read_text()
code = source[source.index('static void\ntrace('):source.index('__attribute__((constructor))')]
# Mach-O interposition metadata is unavailable in the host ELF test binary;
# keep the actual wrapper/body and remove only the platform section attribute.
code = code.replace('section("__DATA,__interpose")', 'unused')
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/types.h>
typedef long CFIndex;
typedef unsigned long CFTypeID;
struct object;
typedef const struct object*CFTypeRef;
typedef CFTypeRef CFDictionaryRef;
typedef CFTypeRef CFArrayRef;
typedef CFTypeRef CFNumberRef;
enum { NUMBER=1,ARRAY,DICTIONARY,STRING,BOOLEAN,DATA };
enum { kCFNumberSInt32Type=3 };
#define CFSTR(x) (x)
struct entry { const char*key;CFTypeRef value; };
struct object { CFTypeID type;int64_t number;CFIndex count;CFTypeRef elements[32];struct entry entries[16]; };
static bool debug;
static unsigned checks,type_reads,dict_reads,array_reads,conversions,opens,writes,closes,forwarded;
static size_t used;
static char log_text[16384];
static void*seen_handle;static const void*seen_network,*seen_password;
static int original_result;
static int hakios_debug_enabled(void){return debug;}
static CFTypeID CFGetTypeID(CFTypeRef v){assert(v);type_reads++;return v->type;}
static CFTypeID CFNumberGetTypeID(void){return NUMBER;}
static CFTypeID CFArrayGetTypeID(void){return ARRAY;}
static CFTypeID CFDictionaryGetTypeID(void){return DICTIONARY;}
static bool CFNumberGetValue(CFNumberRef v,int type,void*out){
 assert(v && v->type==NUMBER && type==kCFNumberSInt32Type);conversions++;
 if(v->number<INT32_MIN || v->number>INT32_MAX)return false;
 *(int32_t*)out=(int32_t)v->number;return true;
}
static CFIndex CFArrayGetCount(CFArrayRef v){assert(v && v->type==ARRAY);return v->count;}
static CFTypeRef CFArrayGetValueAtIndex(CFArrayRef v,CFIndex at){
 assert(v && v->type==ARRAY && at>=0 && at<16 && at<v->count);array_reads++;return v->elements[at];
}
static CFTypeRef CFDictionaryGetValue(CFDictionaryRef v,const char*key){
 static const char*allowed[]={"RSN_IE","WPA_IE","IE_KEY_RSN_MCIPHER","IE_KEY_RSN_CAPS",
  "IE_KEY_RSN_UCIPHERS","IE_KEY_RSN_AUTHSELS","IE_KEY_WPA_MCIPHER","IE_KEY_WPA_UCIPHERS","IE_KEY_WPA_AUTHSELS"};
 assert(v && v->type==DICTIONARY);bool ok=false;
 for(unsigned i=0;i<sizeof allowed/sizeof allowed[0];i++)ok|=!strcmp(key,allowed[i]);
 assert(ok);dict_reads++;
 for(CFIndex i=0;i<v->count;i++)if(!strcmp(key,v->entries[i].key))return v->entries[i].value;
 return NULL;
}
static int capture_open(const char*path,int flags){assert(!strcmp(path,"/dev/console") && flags==(O_WRONLY|O_NOCTTY));opens++;return 37;}
static ssize_t capture_write(int fd,const void*bytes,size_t n){assert(fd==37 && n<256 && used+n<sizeof log_text);memcpy(log_text+used,bytes,n);used+=n;log_text[used]=0;writes++;return n;}
static int capture_close(int fd){assert(fd==37);closes++;return 0;}
#define open capture_open
#define write capture_write
#define close capture_close
static int Apple80211AssociateAsync(void*handle,const void*network,const void*password){
 forwarded++;seen_handle=handle;seen_network=network;seen_password=password;return original_result;
}
'''
main = r'''
static void reset(bool on){debug=on;used=0;log_text[0]=0;type_reads=dict_reads=array_reads=conversions=opens=writes=closes=forwarded=0;}
static unsigned lines(const char*needle){unsigned n=0;for(const char*p=log_text;(p=strstr(p,needle));p++)n++;return n;}
int main(void){
 struct object secret={.type=STRING},boolean={.type=BOOLEAN},data={.type=DATA},bad_dict={.type=DICTIONARY};
 struct object numeric={.type=NUMBER,.number=4},low={.type=NUMBER,.number=INT32_MIN},high={.type=NUMBER,.number=INT32_MAX};
 struct object overflow={.type=NUMBER,.number=INT64_MAX};
 struct object many={.type=ARRAY,.count=32};for(unsigned i=0;i<32;i++)many.elements[i]=&numeric;
 struct object rsn={.type=DICTIONARY,.count=6,.entries={{"IE_KEY_RSN_MCIPHER",&numeric},{"IE_KEY_RSN_CAPS",&high},
  {"IE_KEY_RSN_UCIPHERS",&many},{"IE_KEY_RSN_AUTHSELS",&many},{"SSID_STR",&secret},{"PASSWORD",&secret}}};
 struct object wpa={.type=DICTIONARY,.count=5,.entries={{"IE_KEY_WPA_MCIPHER",&low},
  {"IE_KEY_WPA_UCIPHERS",&many},{"IE_KEY_WPA_AUTHSELS",&many},{"BSSID",&data},{"WPA_PSK",&secret}}};
 struct object network={.type=DICTIONARY,.count=6,.entries={{"RSN_IE",&rsn},{"WPA_IE",&wpa},
  {"SSID_STR",&secret},{"PASSWORD",&secret},{"BSSID",&data},{"PRIVATE_ARBITRARY_VALUE",&secret}}};
 reset(true);security(&network);
 assert(dict_reads==9 && array_reads==64 && opens==68 && writes==opens && closes==opens);
 assert(lines("rsn-pairwise=")==16 && lines("rsn-akm=")==16 && lines("wpa-pairwise=")==16 && lines("wpa-akm=")==16);
 assert(strstr(log_text,"rsn-caps=2147483647") && strstr(log_text,"wpa-group=-2147483648"));
 assert(!strstr(log_text,"SSID") && !strstr(log_text,"PASSWORD") && !strstr(log_text,"BSSID") && !strstr(log_text,"PRIVATE"));checks++;
 /* Bad types cannot be printed or used as containers; numeric overflow is refused. */
 CFTypeRef bad[]={NULL,&secret,&boolean,&data,&bad_dict,&many,&overflow};
 for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){
  reset(true);number("fixed-label",bad[i]);assert(!used && !opens);checks++;
 }
 CFTypeRef wrong_arrays[]={NULL,&secret,&boolean,&numeric,&data,&bad_dict};
 for(unsigned i=0;i<sizeof wrong_arrays/sizeof wrong_arrays[0];i++){
  reset(true);numbers("fixed-label",wrong_arrays[i]);assert(!used && !array_reads && !opens);checks++;
 }
 many.count=32;for(unsigned i=0;i<16;i++)many.elements[i]=i&1?&numeric:&secret;
 reset(true);numbers("fixed-label",&many);assert(array_reads==16 && conversions==8 && lines("fixed-label=")==8);checks++;
 for(unsigned count=0;count<=16;count++){
  many.count=count;reset(true);numbers("fixed-label",&many);assert(array_reads==count && opens==count/2);checks++;
 }
 for(unsigned i=0;i<sizeof wrong_arrays/sizeof wrong_arrays[0];i++){
  reset(true);security(wrong_arrays[i]);
  if(wrong_arrays[i]==&bad_dict)assert(dict_reads==2 && opens==1 && strstr(log_text,"rsn=0 wpa=0"));
  else assert(!used && !dict_reads && !opens);
  checks++;
 }
 /* Wrong nested IE types yield only the fixed presence indicators. */
 network.entries[0].value=&secret;network.entries[1].value=&numeric;
 reset(true);security(&network);assert(dict_reads==2 && !array_reads && opens==1 && !strstr(log_text,"group="));checks++;
 reset(false);security((const void*)(uintptr_t)0xdeadbeef);assert(!type_reads && !opens && !used);checks++;
 number("fixed-label",&numeric);numbers("fixed-label",&many);trace("private string never formatted %s","PRIVATE_SENTINEL");
 assert(!opens && !used);checks++;
 /* The actual interposed wrapper forwards all pointers/results unchanged,
  * even with invalid opaque password/handle pointers and diagnostics off. */
 for(unsigned on=0;on<2;on++)for(unsigned i=0;i<4;i++){
  reset(on);original_result=(int[]){0,7,-1,INT32_MIN}[i];
  void*handle=(void*)(uintptr_t)0xabcd;const void*password=(const void*)(uintptr_t)0x1234;
  int result=trace_Apple80211AssociateAsync(handle,&network,password);
  assert(result==original_result && forwarded==1 && seen_handle==handle && seen_network==&network && seen_password==password);
  if(!on)assert(!used && !opens && !type_reads && !dict_reads);
  assert(!strstr(log_text,"1234") && !strstr(log_text,"abcd"));checks++;
 }
 printf("PASS: %u actual Wi-Fi trace privacy/type/cap/forwarding cases\n",checks);
}
'''
with tempfile.TemporaryDirectory(prefix='spacewar-wifi-trace-') as temp:
    p = Path(temp)
    (p / 'test.c').write_text(harness + code + main)
    subprocess.run([os.environ.get('CC', 'clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
