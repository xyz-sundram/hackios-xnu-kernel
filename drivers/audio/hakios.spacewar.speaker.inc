/* SPDX-License-Identifier: GPL-2.0-only
 * NXP TFA9873B0 (revision0b73), Phone (1) only. Register initialization from
 * Nothing's pinned Qualcomm audio tree, tfa_init.c:tfa9873_specific(). The
 * separate private ROM container supplies its own bounded per-speaker profile.
 * No MTP copy, OTP programming or factory calibration command is implemented.
 */
#include "hakios.spacewar.audio.h"
static struct sw_tfa_profile sw_tfa_profiles[2];
static bool sw_tfa_identified[2],sw_tfa_prepared,sw_tfa_started,sw_tfa_fault,sw_tfa_reset_saved;
static uint32_t sw_tfa_reset_cfg,sw_tfa_reset_io;
static const uint16_t sw_tfa_b0_init[][2]={
    {0x02,0x0628},{0x61,0x0183},{0x63,0x005a},{0x6f,0x0082},{0x70,0xa3eb},
    {0x73,0x0187},{0x83,0x071c},{0x85,0x0380},{0xd5,0x004d}
};
static bool sw_tfa_write_allowed(uint8_t reg,uint16_t value,bool force)
{
    if(force)return (reg==0 && (value&0x19)==1) || ((reg==0x0f || reg==0xa1) && value==0);
    switch(reg) {
    case 0x00:case 0x01:case 0x02:case 0x04:case 0x0f:case 0x20:case 0x21:case 0x22:case 0x23:
    case 0x26:case 0x27:case 0x54:case 0x5f:case 0x60:case 0x61:case 0x62:case 0x63:case 0x64:
    case 0x68:case 0x6f:case 0x70:case 0x71:case 0x73:case 0x74:case 0x75:case 0x76:case 0x83:
    case 0x85:case 0xa0:case 0xb1:case 0xd5:case 0xf9:return true;
    case 0xa1:return value==0 || value==0x5a; /* key only; MTP copy registerA3 is absent */
    default:return false;
    }
}
static bool
sw_tfa_write(unsigned dev,uint8_t reg,uint16_t value,bool force)
{
    if(dev>1 || !sw_tfa_identified[dev] || (!force && (sw_tfa_fault || leaving)) ||
        !sw_tfa_write_allowed(reg,value,force))return false;
    uint8_t address=(uint8_t)(0x34+dev);bool answered;
    if(!bus_take())return false;
    i2c_start();
    answered=i2c_write(address<<1) && i2c_write(reg) && i2c_write(value>>8) && i2c_write(value);
    i2c_stop();bus_give_back();
    uint16_t actual=0;
    /* Keys and the self-clearing I2CR bit do not have stable readback. The
     * protected initialization writes below verify that unlocking succeeded. */
    bool transient=reg==0x0f || reg==0xa0 || reg==0xa1 || (reg==0 && (value&2));
    if(answered && !transient)answered=register_read(address,reg,&actual) && actual==value;
    if(!answered)sw_tfa_fault=true;
    return answered;
}
static bool
sw_tfa_mask(unsigned dev,uint16_t field,uint16_t value)
{
    unsigned pos=(field>>4)&15,width=(field&15)+1;uint16_t before;
    if(pos+width>16 || (width<16 && value>=(1u<<width)) ||
        !register_read((uint8_t)(0x34+dev),field>>8,&before))return false;
    uint16_t mask=(uint16_t)((0xffffu>>(16-width))<<pos);
    return sw_tfa_write(dev,field>>8,(before&~mask)|((value<<pos)&mask),false);
}
static void
sw_tfa_stop(void)
{
    if(!tlmm)return;
    for(unsigned dev=0;dev<2;dev++)if(sw_tfa_identified[dev]) {
        bool off=false;uint16_t control=0x4201;
        for(unsigned tries=0;tries<3 && !off;tries++) {
            (void)register_read((uint8_t)(0x34+dev),0,&control);
            off=sw_tfa_write(dev,0,(control|1)&~0x18u,true);
        }
        /* Close keys independently even when a preceding transfer failed. */
        (void)sw_tfa_write(dev,0xa1,0,true);(void)sw_tfa_write(dev,0x0f,0,true);
        if(!off)sw_tfa_fault=true;
    }
    sw_tfa_started=false;
}
static bool
sw_tfa_prepare(const uint8_t*container,uint64_t size)
{
    if(size!=SW_AUDIO_CONTAINER_BYTES || !sw_tfa_parse(container,(uint32_t)size,SW_AUDIO_CONTAINER_CRC,sw_tfa_profiles)) {
        say("speaker: Phone (1) exact NXP container/CRC/profile invalid; no amplifier writes\n");return false;
    }
    if(!tlmm)tlmm=ml_io_map(TLMM_PHYS,TLMM_SIZE);
    if(!tlmm || sw_tfa_fault || leaving)return false;
    /* Positive IDs on BOTH existing chips before any reset/configuration. */
    for(unsigned dev=0;dev<2;dev++) {
        uint16_t revision;
        if(!register_read((uint8_t)(0x34+dev),3,&revision) || revision!=0x0b73)return false;
        sw_tfa_identified[dev]=true;
    }
    sw_tfa_reset_cfg=rd(GPIO_CFG(101));sw_tfa_reset_io=rd(GPIO_IO(101));sw_tfa_reset_saved=true;
    /* Official reset-polarity isHIGH: assert10ms, deassert10ms. */
    wr(GPIO_IO(101),2);wr(GPIO_CFG(101),CFG_OUTPUT);wait_ms(10);
    wr(GPIO_IO(101),0);wait_ms(10);
    sw_tfa_prepared=true;
    for(unsigned dev=0;dev<2;dev++) {
        uint16_t control,key;
        if(!register_read(0x34+dev,0,&control) ||
            !sw_tfa_write(dev,0,(control|1)&~0x18u,false) ||
            !sw_tfa_write(dev,0,((control|1)&~0x18u)|2,false))goto fail;
        wait_ms(2);
        if(!sw_tfa_write(dev,0x0f,0x5a6b,false) || !register_read(0x34+dev,0xfb,&key) ||
            !sw_tfa_write(dev,0xa0,key^0x5a,false) || !sw_tfa_write(dev,0xa1,0x5a,false))goto fail;
        for(unsigned i=0;i<sizeof(sw_tfa_b0_init)/sizeof(sw_tfa_b0_init[0]);i++)
            if(!sw_tfa_write(dev,sw_tfa_b0_init[i][0],sw_tfa_b0_init[i][1],false))goto fail;
        if(!sw_tfa_mask(dev,0x0480,0))goto fail; /*matching vendor init: FS sync off*/
        for(unsigned i=0;i<sw_tfa_profiles[dev].count;i++) {
            struct sw_tfa_op op=sw_tfa_profiles[dev].op[i];
            if(op.field==0x0040)continue; /*converter enabled only on start*/
            if(!sw_tfa_mask(dev,op.field,op.value))goto fail;
        }
        if(!sw_tfa_mask(dev,0x2000,1) || !sw_tfa_mask(dev,0x0101,0) ||
            !sw_tfa_write(dev,0xa1,0,false) || !sw_tfa_write(dev,0x0f,0,false))goto fail;
    }
    sw_tfa_stop();
    say("speaker: Phone (1) both TFA9873B0 configured from exact ROM speaker profile, powered down\n");
    return !sw_tfa_fault;
fail:
    sw_tfa_fault=true;sw_tfa_stop();return false;
}
static bool
sw_tfa_healthy(void)
{
    if(!sw_tfa_started || sw_tfa_fault)return false;
    for(unsigned dev=0;dev<2;dev++) {
        uint16_t status,temp;
        if(!register_read(0x34+dev,0x10,&status) || !register_read(0x34+dev,0x16,&temp) ||
            !(status&0x40) || (status&8))goto fail; /*clock not stable or OCP*/
        int c=temp&0x1ff;if(c&0x100)c-=512;
        if(c < -40 || c>=80)goto fail;
    }
    return true;
fail:
    sw_tfa_fault=true;sw_tfa_stop();return false;
}
static bool
sw_tfa_start(void)
{
    if(!sw_tfa_prepared || sw_tfa_fault || leaving)return false;
    sw_tfa_started=true; /*cleanup remains active if an enable lands but readback fails*/
    for(unsigned dev=0;dev<2;dev++) {
        if(!sw_tfa_mask(dev,0x0000,0) || !sw_tfa_mask(dev,0x0120,1))goto fail;
        bool stable=false;
        for(unsigned waited=0;waited<50;waited++) {
            uint16_t status;
            if(!register_read(0x34+dev,0x10,&status))goto fail;
            if(status&0x40){stable=true;break;}
            wait_ms(1);
        }
        if(!stable || !sw_tfa_mask(dev,0x0040,1) || !sw_tfa_mask(dev,0x0030,1))goto fail;
    }
    return sw_tfa_healthy();
fail:
    sw_tfa_fault=true;sw_tfa_stop();return false;
}
static bool sw_tfa_volume(uint32_t below)
{
    /* Attenuation is applied in the PCM producer, never by an Awinic volume
     * register. Initial hardware level is fixed; refuse requests above ceiling. */
    return sw_tfa_prepared && !sw_tfa_fault && below>=240;
}
static void sw_tfa_quiesce(void)
{
    sw_tfa_stop();
    if(sw_tfa_reset_saved && tlmm) {
        wr(GPIO_IO(101),sw_tfa_reset_io&2);wr(GPIO_CFG(101),sw_tfa_reset_cfg);
        sw_tfa_reset_saved=false;
    }
}
