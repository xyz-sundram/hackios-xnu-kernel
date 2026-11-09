/* Native Phone (1) brightness on ABL's running display pipeline.
 * Register/packet reference: NothingOSS sm7325 commit
 * 1d557de4d904a76c0b679c746cc77cf9ae89b6c6, techpack/display/msm/dsi.
 * No controller/PHY reset, power, panel initialization or clock changes.
 */
#ifndef HAKIOS_SPACEWAR_DCS_H
#define HAKIOS_SPACEWAR_DCS_H
#include <stdint.h>
#include <stddef.h>
#define SW_DCS_BASE 0x0ae94000ULL
#define SW_DCS_MAX 4095u
enum { SW_DCS_OK, SW_DCS_PROFILE, SW_DCS_STATE, SW_DCS_BUSY,
       SW_DCS_TIMEOUT, SW_DCS_ERROR, SW_DCS_RESPONSE, SW_DCS_RANGE, SW_DCS_VERIFY };
struct sw_dcs_io {
    void *context;
    uint32_t (*read)(void *, uint32_t);
    void (*write)(void *, uint32_t, uint32_t);
    void (*delay)(void *, uint32_t);
    uint32_t version, ctrl, status, interrupt, ack, timeout, readback, count;
    uint32_t trigger, tpg, secure, clock, fifo;
    unsigned failed, use_lpm, verify_reads;
};
static uint32_t sw_dcs_be32(const uint8_t *p)
{ return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]; }
static int sw_dcs_string(const uint8_t *p, uint32_t n, const char *wanted)
{
    unsigned found=0;
    for (uint32_t at=0;at<n;) {
        uint32_t end=at;
        while (end<n && p[end]) end++;
        if (end==n) return 0;
        uint32_t i=0;
        while (at+i<end && wanted[i] && p[at+i]==(uint8_t)wanted[i]) i++;
        if (at+i==end && !wanted[i]) found=1;
        at=end+1;
    }
    return found;
}
/* Match U-Boot's phone and panel identity, not merely screen dimensions. */
static int sw_dcs_spacewar_fdt(const void *blob,uint32_t capacity)
{
    const uint8_t *b=(const uint8_t *)blob;
    if (!b || capacity<40 || sw_dcs_be32(b)!=0xd00dfeed) return 0;
    uint32_t total=sw_dcs_be32(b+4),so=sw_dcs_be32(b+8),no=sw_dcs_be32(b+12);
    uint32_t ns=sw_dcs_be32(b+32),ss=sw_dcs_be32(b+36);
    if (total<56 || total>capacity || total>2097152 || so<40 || no<40 ||
        so>total || no>total || ss>total-so || ns>total-no || (so&3) ||
        (so<no+ns && no<so+ss) || sw_dcs_be32(b+20)<17 || sw_dcs_be32(b+24)>17) return 0;
    uint32_t at=so,end=so+ss,depth=0,roots=0;
    unsigned phone=0,panel=0;
    while (end-at>=4) {
        uint32_t token=sw_dcs_be32(b+at);at+=4;
        if (token==1) {
            uint32_t name=at;
            while (at<end && b[at]) at++;
            if (at==end || at-name>255 || depth>=64 || (!depth && (++roots!=1 || at!=name))) return 0;
            uint64_t next=((uint64_t)at+4)&~3ULL;
            if (next>end) return 0;
            at=(uint32_t)next;depth++;
        } else if (token==2) {
            if (!depth) return 0;
            depth--;
        } else if (token==3) {
            if (!depth || end-at<8) return 0;
            uint32_t len=sw_dcs_be32(b+at),name=sw_dcs_be32(b+at+4);at+=8;
            uint64_t padded=((uint64_t)len+3)&~3ULL;
            if (name>=ns || padded>end-at) return 0;
            uint32_t last=name;
            while (last<ns && b[no+last]) last++;
            if (last==ns) return 0;
            if (sw_dcs_string(b+no+name,last-name+1,"compatible")) {
                if (depth==1) phone=sw_dcs_string(b+at,len,"nothing,spacewar");
                else if (sw_dcs_string(b+at,len,"nothing,rm692e5-spacewar")) panel=1;
            }
            at+=(uint32_t)padded;
        } else if (token==9) return !depth && roots==1 && phone && panel;
        else if (token!=4) return 0;
    }
    return 0;
}
static uint32_t sw_dcs_r(struct sw_dcs_io *d,uint32_t reg)
{ return d->read(d->context,reg); }
static void sw_dcs_w(struct sw_dcs_io *d,uint32_t reg,uint32_t value)
{ d->write(d->context,reg,value); }
static void sw_dcs_snapshot(struct sw_dcs_io *d)
{
    d->version=sw_dcs_r(d,0);d->ctrl=sw_dcs_r(d,4);d->status=sw_dcs_r(d,8);
    d->interrupt=sw_dcs_r(d,0x110);d->ack=sw_dcs_r(d,0x68);
    d->timeout=sw_dcs_r(d,0xc0);d->readback=sw_dcs_r(d,0x6c);
    d->count=sw_dcs_r(d,0x1d4)>>16;
    d->trigger=sw_dcs_r(d,0x84);d->tpg=sw_dcs_r(d,0x15c);
    d->secure=sw_dcs_r(d,0x2cc);d->clock=sw_dcs_r(d,0x120);d->fifo=sw_dcs_r(d,0xc);
}
static int sw_dcs_state(struct sw_dcs_io *d)
{
    sw_dcs_snapshot(d);
    if (d->failed || d->version<0x20020001 || d->version>0x2005ffff ||
        (d->ctrl&0x1f7)!=0x1f5 || (d->trigger&0x1000f)!=4 ||
        d->tpg!=0 || (d->secure&1)) return SW_DCS_STATE;
    if ((d->interrupt&(1u<<24)) || d->ack || (d->timeout&0x111) ||
        (d->clock&(1u<<16))) return SW_DCS_ERROR;
    return SW_DCS_OK;
}
/* Embedded command FIFO; preserve IRQ masks and acknowledge only DMA/BTA. */
static int sw_dcs_packet(struct sw_dcs_io *d,uint32_t header,uint32_t payload,
                         unsigned words,unsigned low_power,unsigned receive)
{
    int result=sw_dcs_state(d);
    if (result || words<1 || words>2) return result ? result : SW_DCS_RANGE;
    unsigned wait;
    for (wait=0;wait<10000 && (sw_dcs_r(d,8)&6);wait++) d->delay(d->context,2);
    if (sw_dcs_r(d,8)&6) return SW_DCS_BUSY;
    uint32_t dma=sw_dcs_r(d,0x3c),length=sw_dcs_r(d,0x4c);
    const uint32_t done=1u|(1u<<20);
    const uint32_t enables=(1u<<1)|(1u<<9)|(1u<<11)|(1u<<13)|(1u<<15)|
        (1u<<17)|(1u<<21)|(1u<<25)|(1u<<29)|(1u<<31);
    uint32_t masks=sw_dcs_r(d,0x110)&enables;
    sw_dcs_w(d,0x110,masks|done);
    if (receive) {
        sw_dcs_w(d,0x1d4,1);d->delay(d->context,1);sw_dcs_w(d,0x1d4,0);
    }
    sw_dcs_w(d,0x1ec,1);d->delay(d->context,1);sw_dcs_w(d,0x1ec,0);
    sw_dcs_w(d,0x15c,0x30006);
    sw_dcs_w(d,0x17c,header);sw_dcs_w(d,0x17c,words==2?payload:0);
    uint32_t command=(dma&~((1u<<31)|(1u<<30)|(1u<<26)))|(1u<<28);
    if (low_power) command|=1u<<26;
    sw_dcs_w(d,0x3c,command);sw_dcs_w(d,0x4c,words*4);
    sw_dcs_w(d,0x90,1);
    result=SW_DCS_TIMEOUT;
    for (wait=0;wait<20000;wait++) {
        uint32_t status=sw_dcs_r(d,0x110);
        if (status&(1u<<24)) {result=SW_DCS_ERROR;break;}
        if ((status&1) && (!receive || (sw_dcs_r(d,0x1d4)>>16))) {result=SW_DCS_OK;break;}
        d->delay(d->context,1);
    }
    sw_dcs_snapshot(d);
    if (d->ack || (d->timeout&0x111)) result=SW_DCS_ERROR;
    if (sw_dcs_r(d,8)&2) {
        /* Never rewrite parameters while hardware is still fetching. */
        d->failed=1;return result ? result : SW_DCS_BUSY;
    }
    sw_dcs_w(d,0x15c,0);sw_dcs_w(d,0x1ec,1);
    d->delay(d->context,1);sw_dcs_w(d,0x1ec,0);
    sw_dcs_w(d,0x3c,dma);sw_dcs_w(d,0x4c,length);
    sw_dcs_w(d,0x110,masks|done);
    if (result) d->failed=1;
    return result;
}
static int sw_dcs_read(struct sw_dcs_io *d,uint8_t address,unsigned bytes,uint16_t *value)
{
    if (bytes<1 || bytes>2 || !value) return SW_DCS_RANGE;
    int result=sw_dcs_packet(d,0x80370000u|bytes,0,1,1,0);
    if (result) return result;
    result=sw_dcs_packet(d,0xa0060000u|address,0,1,1,1);
    if (result) return result;
    uint32_t data=d->readback;
    if ((d->count!=4 && d->count!=8) || (data>>24)!=(bytes==2?0x22u:0x21u)) return SW_DCS_RESPONSE;
    *value=bytes==2?(uint16_t)((data>>8)&0xffff):(uint16_t)((data>>16)&0xff);
    return SW_DCS_OK;
}
static int sw_dcs_initial(struct sw_dcs_io *d,uint16_t *raw)
{
    uint16_t power=0;
    int result=sw_dcs_read(d,0x0a,1,&power);
    if (result) return result;
    if ((power&0x9c)!=0x9c) return SW_DCS_STATE;
    result=sw_dcs_read(d,0x52,2,raw);
    return result ? result : *raw>SW_DCS_MAX ? SW_DCS_RANGE : SW_DCS_OK;
}
static int sw_dcs_set_once(struct sw_dcs_io *d,uint16_t raw,unsigned low_power)
{
    uint32_t payload=0xff000051u|((uint32_t)(raw>>8)<<8)|((uint32_t)(raw&255)<<16);
    int result=sw_dcs_packet(d,0xc0390003,payload,2,low_power,0);
    if (result) return result;
    /* DBV may latch on the next panel scan. Poll fresh responses instead of
     * treating an immediate old value as failure or resending the write. */
    for (unsigned attempt=0;attempt<6;attempt++) {
        uint16_t actual=0;
        d->verify_reads++;
        result=sw_dcs_read(d,0x52,2,&actual);
        if (result) return result;
        if (actual==raw) return SW_DCS_OK;
        if (attempt<5) d->delay(d->context,10000);
    }
    return SW_DCS_VERIFY;
}
static int sw_dcs_set(struct sw_dcs_io *d,uint16_t raw)
{
    if (raw>SW_DCS_MAX) return SW_DCS_RANGE;
    d->verify_reads=0;
    int result=sw_dcs_set_once(d,raw,d->use_lpm);
    /* The live ABL pipeline may not issue HS command requests like Android's
     * fully initialized driver. This panel's stock init also writes DBV in
     * LP mode. Try that packet mode only after a completed, error-free HS
     * exchange whose fresh readbacks never matched; retain it only on proof. */
    if (result==SW_DCS_VERIFY && !d->use_lpm && !d->failed) {
        result=sw_dcs_set_once(d,raw,1);
        if (!result) d->use_lpm=1;
    }
    return result;
}
/* RM692E5 timing-switch packets from Spacewar ROM timing@0/@2.
 * The PHY and DSC descriptions are identical for these two command modes. */
static int sw_dcs_refresh(struct sw_dcs_io *d, unsigned hz)
{
    if (hz != 60 && hz != 120) return SW_DCS_RANGE;
    int result = sw_dcs_packet(d, 0x801540feu, 0, 1, 1, 0);
    if (result) return result;
    result = sw_dcs_packet(d, 0x801500bdu | ((hz == 120 ? 7u : 0u) << 8), 0, 1, 1, 0);
    if (result) return result;
    result = sw_dcs_packet(d, 0x801500feu, 0, 1, 1, 0);
    if (!result) d->delay(d->context, hz == 120 ? 17000 : 34000);
    return result;
}
/* Read the panel's selected refresh register, then restore the public page. */
static int sw_dcs_refresh_readback(struct sw_dcs_io *d, uint16_t *mode)
{
    int result = sw_dcs_packet(d, 0x801540feu, 0, 1, 1, 0);
    if (result) return result;
    result = sw_dcs_read(d, 0xbd, 1, mode);
    int restored = d->failed ? SW_DCS_STATE : sw_dcs_packet(d, 0x801500feu, 0, 1, 1, 0);
    return result ? result : restored;
}
#endif
