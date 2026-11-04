/* SPDX-License-Identifier: GPL-2.0-only
 * Pure Phone (1) PRI_MI2S/TFA9873 profile validation; no MMIO or device access.
 * NXP container layout: matching Nothing techpack/audio/asoc/codecs/tfa98xx.
 */
#ifndef HAKIOS_SPACEWAR_AUDIO_H
#define HAKIOS_SPACEWAR_AUDIO_H
#include <stdint.h>
#include <stdbool.h>
#define SW_AUDIO_PORT 0x1000u
#define SW_AUDIO_CLOCK 0x100u
#define SW_AUDIO_I2S_PARAM 0x1020du
#define SW_AUDIO_CONTAINER_BYTES 3745u
#define SW_AUDIO_CONTAINER_CRC 0x8e4ae995u
#define SW_AUDIO_MAX_OPS 64u
struct sw_audio_i2s {
    uint32_t version;
    uint16_t bits,channel_mode,stereo,ws_source;
    uint32_t rate;
    uint16_t format,reserved;
} __attribute__((packed));
_Static_assert(sizeof(struct sw_audio_i2s)==20,"AFE I2S v1 ABI");
static const struct sw_audio_i2s sw_audio_i2s_config __attribute__((unused))={1,32,2,1,1,48000,0,0};
struct sw_tfa_op { uint16_t field,value; };
struct sw_tfa_profile { uint8_t address;unsigned count;struct sw_tfa_op op[SW_AUDIO_MAX_OPS]; };
static inline uint16_t sw_audio_u16(const uint8_t *p) { return (uint16_t)p[0]|(uint16_t)p[1]<<8; }
static inline uint32_t sw_audio_u32(const uint8_t *p) { return (uint32_t)sw_audio_u16(p)|(uint32_t)sw_audio_u16(p+2)<<16; }
static inline uint32_t sw_audio_crc32(const uint8_t*p,uint32_t n)
{
    uint32_t crc=~0u;
    while(n--) { crc^=*p++;for(unsigned bit=0;bit<8;bit++)crc=(crc>>1)^((0u-(crc&1))&0xedb88320u); }
    return ~crc;
}
static inline bool sw_audio_range(uint32_t n,uint32_t at,uint32_t bytes) { return at<=n && bytes<=n-at; }
static inline bool sw_audio_string(const uint8_t*b,uint32_t n,uint32_t descriptor,const char*s)
{
    if((descriptor>>24)!=3)return false;
    uint32_t at=descriptor&0xffffffu;
    for(unsigned i=0;i<64;i++) {
        if(at>=n || b[at]!=(uint8_t)s[i])return false;
        if(!s[i])return true;
        at++;
    }
    return false;
}
static inline bool sw_tfa_field_allowed(uint16_t field,uint16_t value)
{
    unsigned pos=(field>>4)&15,width=(field&15)+1;
    if(pos+width>16 || (width<16 && value>=(1u<<width)))return false;
    /* No OTP/MTP-copy, calibration-start, amplifier-enable, reset, direct PLL
     * or arbitrary register descriptors are accepted from the container. */
    switch(field) {
    case 0x20c3:return value==2;
    case 0x2144:case 0x2224:return value==31;
    case 0x2013:return value==1;
    case 0x0203:return value==8; /*48kHz*/
    case 0xb190:return value==0; /*calibration disabled*/
    case 0xf910:return value==1; /*volatile PLL LUT shadow, never copied to MTP*/
    case 0x2330:case 0x2340:case 0x2603:case 0x2643:case 0x26c3:case 0x2703:
    case 0x6802:case 0x6831:case 0x6851:case 0x6f50:case 0x8510:case 0x54a1:
    case 0x7371:case 0x5f63:case 0x7444:case 0x7584:case 0x7695:case 0x7003:
    case 0x71c1:case 0x0255:case 0x0040:case 0x64e1:case 0x60e1:case 0x62e1:return true;
    default:return false;
    }
}
static inline bool sw_tfa_append(const uint8_t*b,uint32_t n,uint32_t ref,struct sw_tfa_profile*p)
{
    uint32_t at=ref&0xffffffu;
    if((ref>>24)!=16 || !sw_audio_range(n,at,4) || p->count>=SW_AUDIO_MAX_OPS)return false;
    uint16_t value=sw_audio_u16(b+at),field=sw_audio_u16(b+at+2);
    if(!sw_tfa_field_allowed(field,value))return false;
    p->op[p->count++]=(struct sw_tfa_op){field,value};return true;
}
static inline bool
sw_tfa_parse(const uint8_t*b,uint32_t n,uint32_t expected_crc,struct sw_tfa_profile out[2])
{
    if(!b || n<54 || n>65536 || b[0]!='P' || b[1]!='M' || b[2]!='3' || b[3]!='_' ||
        b[4]!='0' || b[5]!='1' || sw_audio_u32(b+6)!=n || sw_audio_u32(b+10)!=expected_crc ||
        sw_audio_crc32(b+14,n-14)!=expected_crc || sw_audio_u16(b+40)!=2)return false;
    out[0].count=out[1].count=0;
    for(unsigned d=0;d<2;d++) {
        uint32_t ref=sw_audio_u32(b+46+4*d),at=ref&0xffffffu;bool speaker=false;
        if((ref>>24)!=0 || !sw_audio_range(n,at,12))return false;
        unsigned count=b[at];uint8_t address=b[at+2];
        if(address!=(0x34+d) || count>48 || !sw_audio_range(n,at+12,4*count))return false;
        struct sw_tfa_profile*p=&out[d];p->address=address;
        for(unsigned i=0;i<count;i++) {
            uint32_t item=sw_audio_u32(b+at+12+4*i),pos=item&0xffffffu;unsigned type=item>>24;
            if(type==16) { if(!sw_tfa_append(b,n,item,p))return false; }
            else if(type==5) { /* no-DSP dummy patch, validated but never uploaded */
                if(!sw_audio_range(n,pos,8) || !sw_audio_range(n,pos+8,sw_audio_u32(b+pos+4)))return false;
            } else if(type==1) {
                if(!sw_audio_range(n,pos,8))return false;
                unsigned length=b[pos];
                if(!length || length>48 || !sw_audio_range(n,pos+8,(length-1)*4))return false;
                if(!sw_audio_string(b,n,sw_audio_u32(b+pos+4),"speaker"))continue;
                if(speaker || sw_audio_u16(b+pos+2)!=0x1234)return false;
                speaker=true;
                for(unsigned j=0;j<length-1;j++) {
                    uint32_t op=sw_audio_u32(b+pos+8+4*j);
                    if((op>>24)==17)break; /* default/leave-profile operations */
                    if(!sw_tfa_append(b,n,op,p))return false;
                }
            } else return false;
        }
        if(!speaker || !p->count)return false;
        bool slot=false;unsigned format=0;
        for(unsigned i=0;i<p->count;i++) {
            uint16_t field=p->op[i].field;
            if(field==0x2603) { if(slot || p->op[i].value!=d)return false;slot=true; }
            if(field==0x20c3)format|=1;
            if(field==0x2144)format|=2;
            if(field==0x2224)format|=4;
            if(field==0x2013)format|=8;
            if(field==0x0203)format|=16;
        }
        if(!slot || format!=31)return false;
    }
    return true;
}
/* A hard30dB attenuation before DSP DMA bounds the initial NXP output even if
 * user PCM or software volume reaches full scale. It cannot be raised by a
 * boot argument, container register, mixer or Darwin volume setting. */
static inline void sw_audio_limit_pcm(int16_t *samples,unsigned count)
{
    for(unsigned i=0;i<count;i++)samples[i]=(int16_t)(samples[i]/32);
}
/* Additional eighth-dB attenuation is independent of that fixed ceiling.
 * Q31 arithmetic avoids floating point in the kernel; saturating before the
 * exponent prevents a wrapped volume request from increasing the level. */
static inline uint32_t sw_audio_gain_q31(uint32_t below,uint32_t extra)
{
    uint64_t total=(uint64_t)below+extra;
    if(total<240)total=240;
    if(total>720)total=720;
    uint32_t steps=(uint32_t)total-240;
    uint64_t gain=1ull<<31,step=2116800189ull; /* 10^(-0.125 / 20) */
    while(steps) {
        if(steps&1)gain=gain*step/(1ull<<31);
        step=step*step/(1ull<<31);
        steps>>=1;
    }
    return (uint32_t)gain;
}
static inline void sw_audio_limit_pcm_at(int16_t *samples,unsigned count,uint32_t gain)
{
    if(gain>(1u<<31))gain=1u<<31;
    for(unsigned i=0;i<count;i++) {
        int64_t bounded=samples[i]/32;
        samples[i]=(int16_t)(bounded*gain/(1ll<<31));
    }
}
#endif
