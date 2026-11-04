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
static const char *sw_tfa_stage="idle";
static void sw_tfa_failure(unsigned dev,const char*operation,unsigned reg,unsigned wanted,unsigned actual)
{
    say("speaker: Phone (1) NXP FAIL stage=%s addr=%#x op=%s reg=%#x want=%#x actual=%#x fault=%u leaving=%u\n",
        sw_tfa_stage,0x34+dev,operation,reg,wanted,actual,sw_tfa_fault,leaving);
}
static bool sw_tfa_read(unsigned dev,uint8_t reg,uint16_t*value)
{
    if(register_read((uint8_t)(0x34+dev),reg,value))return true;
    sw_tfa_failure(dev,"read-io",reg,0,0);return false;
}
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
        !sw_tfa_write_allowed(reg,value,force)) {
        sw_tfa_failure(dev,"write-refused",reg,value,0);return false;
    }
    uint8_t address=(uint8_t)(0x34+dev);bool answered;
    if(!bus_take()){sw_tfa_failure(dev,"bus-not-idle",reg,value,0);return false;}
    i2c_start();
    answered=i2c_write(address<<1) && i2c_write(reg) && i2c_write(value>>8) && i2c_write(value);
    i2c_stop();bus_give_back();
    uint16_t actual=0;
    /* Keys and the self-clearing I2CR bit do not have stable readback. The
     * protected initialization writes below verify that unlocking succeeded. */
    bool transient=reg==0x0f || reg==0xa0 || reg==0xa1 || (reg==0 && (value&2));
    if(!answered)sw_tfa_failure(dev,"write-no-ack",reg,value,0);
    if(answered && !transient) {
        if(!sw_tfa_read(dev,reg,&actual))answered=false;
        else if(actual!=value){sw_tfa_failure(dev,"write-readback",reg,value,actual);answered=false;}
    }
    if(!answered)sw_tfa_fault=true;
    return answered;
}
static bool
sw_tfa_mask(unsigned dev,uint16_t field,uint16_t value)
{
    unsigned pos=(field>>4)&15,width=(field&15)+1;uint16_t before;
    if(pos+width>16 || (width<16 && value>=(1u<<width))) {
        sw_tfa_failure(dev,"field-refused",field,value,0);return false;
    }
    if(!sw_tfa_read(dev,field>>8,&before))return false;
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
            (void)sw_tfa_read(dev,0,&control);
            off=sw_tfa_write(dev,0,(control|1)&~0x18u,true);
        }
        /* Close keys independently even when a preceding transfer failed. */
        (void)sw_tfa_write(dev,0xa1,0,true);(void)sw_tfa_write(dev,0x0f,0,true);
        if(!off){sw_tfa_failure(dev,"powerdown-unverified",0,1,control);sw_tfa_fault=true;}
    }
    sw_tfa_started=false;
}
static bool
sw_tfa_prepare(const uint8_t*container,uint64_t size)
{
    if(size!=SW_AUDIO_CONTAINER_BYTES || !sw_tfa_parse(container,(uint32_t)size,SW_AUDIO_CONTAINER_CRC,sw_tfa_profiles)) {
        say("speaker: Phone (1) exact NXP container/CRC/profile invalid; no amplifier writes\n");return false;
    }
    sw_tfa_stage="prepare-identify";
    if(!tlmm)tlmm=ml_io_map(TLMM_PHYS,TLMM_SIZE);
    if(!tlmm || sw_tfa_fault || leaving) {
        sw_tfa_failure(0,"prepare-refused",0,tlmm!=0,sw_tfa_fault);return false;
    }
    /* Positive IDs on BOTH existing chips before any reset/configuration. */
    for(unsigned dev=0;dev<2;dev++) {
        uint16_t revision;
        if(!sw_tfa_read(dev,3,&revision))return false;
        if(revision!=0x0b73){sw_tfa_failure(dev,"revision",3,0x0b73,revision);return false;}
        sw_tfa_identified[dev]=true;
    }
    sw_tfa_reset_cfg=rd(GPIO_CFG(101));sw_tfa_reset_io=rd(GPIO_IO(101));sw_tfa_reset_saved=true;
    /* Official reset-polarity isHIGH: assert10ms, deassert10ms. */
    wr(GPIO_IO(101),2);wr(GPIO_CFG(101),CFG_OUTPUT);wait_ms(10);
    wr(GPIO_IO(101),0);wait_ms(10);
    sw_tfa_prepared=true;
    sw_tfa_stage="prepare-profile";
    for(unsigned dev=0;dev<2;dev++) {
        uint16_t control,key;
        if(!sw_tfa_read(dev,0,&control) ||
            !sw_tfa_write(dev,0,(control|1)&~0x18u,false) ||
            !sw_tfa_write(dev,0,((control|1)&~0x18u)|2,false))goto fail;
        wait_ms(2);
        if(!sw_tfa_write(dev,0x0f,0x5a6b,false) || !sw_tfa_read(dev,0xfb,&key) ||
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
    sw_tfa_stage="prepare-powerdown";sw_tfa_stop();
    if(sw_tfa_fault){say("speaker: Phone (1) NXP preparation powerdown failed; start refused\n");return false;}
    say("speaker: Phone (1) both TFA9873B0 configured from exact ROM speaker profile, powered down\n");
    return true;
fail:
    sw_tfa_fault=true;sw_tfa_stop();return false;
}
struct sw_tfa_native_state {
    uint16_t status,adc_status,manager,control,protection,direct,low_power,temp;
};
static bool sw_tfa_unknown_temperature[2];
static void sw_tfa_native_log(unsigned dev,const struct sw_tfa_native_state*s)
{
    say("speaker: Phone (1) NXP addr=%#x native status10=%#x status13=%#x manager14=%#x ctl00=%#x protectB0=%#x directC0=%#x lp6e=%#x temp16=%#x ADCready=%u LP0=%u LP1=%u\n",
        0x34+dev,s->status,s->adc_status,s->manager,s->control,s->protection,s->direct,s->low_power,s->temp,
        !!(s->adc_status&0x400),s->low_power&1,(s->low_power>>1)&1);
}
static bool
sw_tfa_health(bool startup)
{
    sw_tfa_stage=startup?"startup-health":"health";
    if(!sw_tfa_started || !sw_tfa_prepared || !sw_tfa_identified[0] || !sw_tfa_identified[1] || sw_tfa_fault) {
        sw_tfa_failure(0,"health-refused",0,sw_tfa_started,sw_tfa_fault);return false;
    }
    uint32_t mode=0,requested_seconds=3;
    PE_parse_boot_argn("sound",&mode,sizeof mode);
    PE_parse_boot_argn("sound_secs",&requested_seconds,sizeof requested_seconds);
    for(unsigned dev=0;dev<2;dev++) {
        struct sw_tfa_native_state s;
        if(!sw_tfa_read(dev,0x10,&s.status) || !sw_tfa_read(dev,0x13,&s.adc_status) ||
            !sw_tfa_read(dev,0x14,&s.manager) || !sw_tfa_read(dev,0x00,&s.control) ||
            !sw_tfa_read(dev,0xb0,&s.protection) || !sw_tfa_read(dev,0xc0,&s.direct) ||
            !sw_tfa_read(dev,0x6e,&s.low_power) || !sw_tfa_read(dev,0x16,&s.temp))goto fail;
        if(startup)sw_tfa_native_log(dev,&s);
        /* Exact B0 names: CLKS, flag_otpok, flag_bst_ocpok, flag_uvpok are
         * healthy HIGH. OCDS/manager/lost-clock/TDM flags are faults HIGH.
         * The matching vendor status code also checks !OTDS as an error.
         * These checks never clear a fault by retrying or changing a bypass. */
        if((s.status&0x56)!=0x56 || (s.status&0x528)) {
            if(!startup)sw_tfa_native_log(dev,&s);
            sw_tfa_failure(dev,"native-status",0x10,0x56,s.status);goto fail;
        }
        if(!(s.adc_status&0x100)) {
            sw_tfa_failure(dev,"overvoltage",0x13,0x100,s.adc_status);goto fail;
        }
        if(((s.manager>>3)&15)!=9) {
            sw_tfa_failure(dev,"manager-not-operating",0x14,9,s.manager);goto fail;
        }
        if((s.control&0x19)!=0x18 || (s.control&0x1800)) {
            sw_tfa_failure(dev,"ocp-test-or-enable",0,0x18,s.control);goto fail;
        }
        /* B0: OCPcounter/OVP/UVP/OTP/lost-clock BYPASS bits must all be0.
         * C0 direct/test control must be0; sensor/ADC test controls are never
         * written. Numeric TEMPS is not a stock TFA9873 startup prerequisite. */
        if((s.protection&0x7a) || (s.direct&1)) {
            sw_tfa_failure(dev,"protection-bypassed",s.protection&0x7a?0xb0:0xc0,0,
                s.protection&0x7a?s.protection:s.direct);goto fail;
        }
        int c=s.temp&0x1ff;if(c&0x100)c-=512;
        if((s.temp&0x1ff)==0x100 && !(s.adc_status&0x400) && mode==3 && requested_seconds<=3) {
            /* Observed0x100 is UNKNOWN, not a fabricated Celsius value.
             * This exception is only the bounded3s tone with the immutable
             * /32 PCM ceiling. sound=4 still requires a usable temperature.
             * IC OTP is not stock speaker-coil SpeakerBoost protection. */
            if(!sw_tfa_unknown_temperature[dev]) {
                if(!startup)sw_tfa_native_log(dev,&s);
                say("speaker: Phone (1) NXP addr=%#x temperature UNKNOWN raw0x100 ADCready=0; native protections verified, sound3 requested=%us bounded /32 diagnostic only\n",
                    0x34+dev,requested_seconds);
            }
            sw_tfa_unknown_temperature[dev]=true;
            continue;
        }
        if(c < -40 || c>=80){sw_tfa_failure(dev,"temperature",0x16,80,s.temp);goto fail;}
        if(sw_tfa_unknown_temperature[dev])say("speaker: Phone (1) NXP addr=%#x numeric temperature raw=%#x decoded=%d ADCready=%u\n",0x34+dev,s.temp,c,!!(s.adc_status&0x400));
        sw_tfa_unknown_temperature[dev]=false;
    }
    return true;
fail:
    sw_tfa_fault=true;sw_tfa_stop();return false;
}
static bool sw_tfa_healthy(void){return sw_tfa_health(false);}
static bool
sw_tfa_start(void)
{
    sw_tfa_stage="start-entry";
    if(!sw_tfa_prepared || sw_tfa_fault || leaving) {
        sw_tfa_failure(0,"start-refused",0,sw_tfa_prepared,sw_tfa_fault);return false;
    }
    say("speaker: Phone (1) NXP start: PWDN=0, MANSCONF=1, stable clock, then guarded converter/amplifier enable\n");
    sw_tfa_started=true; /*cleanup remains active if an enable lands but readback fails*/
    for(unsigned dev=0;dev<2;dev++) {
        sw_tfa_stage="start-powerup";
        if(!sw_tfa_mask(dev,0x0000,0))goto fail;
        sw_tfa_stage="start-mansconf";
        if(!sw_tfa_mask(dev,0x0120,1))goto fail;
        sw_tfa_stage="start-clock";
        bool stable=false;uint16_t status=0;
        for(unsigned waited=0;waited<50;waited++) {
            if(!sw_tfa_read(dev,0x10,&status))goto fail;
            if(status&0x40){stable=true;break;}
            wait_ms(1);
        }
        if(!stable){sw_tfa_failure(dev,"clock-timeout-50ms",0x10,0x40,status);goto fail;}
        say("speaker: Phone (1) NXP addr=%#x clock stable: status10=%#x\n",0x34+dev,status);
        sw_tfa_stage="start-dca";
        if(!sw_tfa_mask(dev,0x0040,1))goto fail;
        sw_tfa_stage="start-ampe";
        if(!sw_tfa_mask(dev,0x0030,1))goto fail;
    }
    if(!sw_tfa_health(true))return false;
    say("speaker: Phone (1) NXP both amplifiers started; native protection gates passed (temperature availability logged)\n");
    return true;
fail:
    sw_tfa_fault=true;sw_tfa_stop();return false;
}
static bool sw_tfa_volume(uint32_t below)
{
    /* Attenuation is applied in the PCM producer, never by an Awinic volume
     * register. Initial hardware level is fixed; refuse requests above ceiling. */
    if(sw_tfa_prepared && !sw_tfa_fault && below>=240)return true;
    say("speaker: Phone (1) NXP volume refused: prepared=%u fault=%u below=%u (minimum240)\n",
        sw_tfa_prepared,sw_tfa_fault,below);return false;
}
static void sw_tfa_quiesce(void)
{
    sw_tfa_stop();
    if(sw_tfa_reset_saved && tlmm) {
        wr(GPIO_IO(101),sw_tfa_reset_io&2);wr(GPIO_CFG(101),sw_tfa_reset_cfg);
        sw_tfa_reset_saved=false;
    }
}
