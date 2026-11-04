/* SPDX-License-Identifier: GPL-2.0-only
 * Nothing Phone (1) PM8350B HV haptics, independent of the Redwood AW86927.
 * Wiring and limits: Phone (1)'s qcom,hv-haptics DT: SID3 F000/F100/F200,
 * PMK8350 SDAM9D00 (SID0), LRA period5880us, effect VMAX3600mV.
 * Register semantics: NothingOSS android_kernel_msm-5.4_nothing_sm7325,
 * sm7325/s drivers/input/misc/qcom-hv-haptics.c. Direct-play needs no foreign
 * waveform firmware, SoundWire takeover or persistent calibration writes.
 * haptic=1/2 only inspect. 3 configures without playing, 4 one bounded pulse,
 * 5 consumes the existing Darwin Actuator PCM endpoint. Every output has a
 * 250ms cap followed by at least250ms rest, independent of user writes.
 */
#define SW_HAP_CFG 0
#define SW_HAP_PTN 1
#define SW_HAP_BOOST 2
#define SW_HAP_SDAM 3
#define SW_HAP_MAX_US 250000ULL
#define SW_HAP_REST_US 250000ULL
#define SW_HAP_SETTLE_US 8000ULL
#define SW_HAP_TAP_US 40000ULL
#define SW_HAP_MIN_AMPLITUDE 0x20u /* physically verified 40ms pulse */
#define SW_HAP_AMPLITUDE 0x40u /* <=25% direct-play scale; first phone test */
#define SW_HAP_VMAX_CODE 72u /*3600mV /50mV, Android's effect ceiling */
#define SW_HAP_TLRA_CODE (5880u /5u)

struct sw_hap_peripheral { uint16_t id, channel; bool found, writable; vm_offset_t write; };
static struct sw_hap_peripheral sw_hap_peripherals[4] = {
    { .id=0x3f0 }, { .id=0x3f1 }, { .id=0x3f2 }, { .id=0x09d }
};
static vm_offset_t sw_hap_core, sw_hap_cnfg, sw_hap_observer;
static unsigned sw_hap_phase;
static bool sw_hap_active, sw_hap_changed, sw_hap_ready, sw_hap_fault;
static uint8_t sw_hap_saved[10];
static const uint8_t sw_hap_save_regs[10]={0x46,0x48,0x49,0x50,0x5c,0x5d,0x63,0x68,0x72,0x4e};

static bool
sw_hap_wait(vm_offset_t channel)
{
    uint64_t began=now_us();uint32_t status;
    do { status=rd(channel+8); } while (!(status&1) && now_us()-began<2000);
    return (status&0xf)==1;
}

static bool
sw_hap_read(unsigned peripheral,uint8_t reg,uint8_t *value)
{
    struct sw_hap_peripheral *p=&sw_hap_peripherals[peripheral];
    if (!p->found) return false;
    vm_offset_t channel=sw_hap_observer+0x80UL*p->channel;
    bool ok=false;
    for(unsigned retry=0;retry<3;retry++) {
        if (!leaving) hakios_spmi_lock();
        wr(channel,(1u<<27)|((uint32_t)reg<<4));
        ok=sw_hap_wait(channel);
        if(ok) *value=(uint8_t)rd(channel+0x18);
        uint32_t status=rd(channel+8);
        if (!leaving) hakios_spmi_unlock();
        if(ok || !(status&8)) break; /* only dropped requests may be retried */
    }
    if(!ok)say("haptic: PM8350B read failed peripheral=%#x reg=%#x\n",p->id,reg);
    return ok;
}

static bool
sw_hap_allowed(unsigned peripheral,uint8_t reg)
{
    if(peripheral==SW_HAP_PTN) return reg==0x26;
    if(peripheral==SW_HAP_SDAM) return reg==0x42 || reg==0xe5;
    if(peripheral!=SW_HAP_CFG) return false;
    switch(reg) {
    case 0x46:case 0x48:case 0x49:case 0x4c:case 0x4e:case 0x50:
    case 0x5c:case 0x5d:case 0x63:case 0x66:case 0x68:case 0x72:return true;
    default:return false;
    }
}

static bool
sw_hap_write(unsigned peripheral,uint8_t reg,uint8_t value,bool force)
{
    struct sw_hap_peripheral *p=&sw_hap_peripherals[peripheral];uint8_t actual=0;
    if ((!force && (sw_hap_fault || leaving)) || !sw_hap_ready || !p->writable || !p->write ||
        !sw_hap_allowed(peripheral,reg)) return false;
    /* Force only means allow stop/restore after a previous fault; it never
     * bypasses identity, ownership or the hardware-register whitelist. */
    if(force && !((peripheral==SW_HAP_CFG && (reg==0x4c || reg==0x46)) ||
        (peripheral==SW_HAP_SDAM && (reg==0x42 || reg==0xe5)) || sw_hap_changed)) return false;
    bool ok=false,readback=false;uint32_t status=0;
    for(unsigned retry=0;retry<3;retry++) {
        if (!leaving) hakios_spmi_lock();
        wr(p->write+0x10,value);wr(p->write,(uint32_t)reg<<4);
        ok=sw_hap_wait(p->write);status=rd(p->write+8);
        if (!leaving) hakios_spmi_unlock();
        if(ok || !(status&8))break;
    }
    /* Trigger and W1C registers have no stable readback value. */
    if(ok && !(peripheral==SW_HAP_SDAM && reg==0xe5) && !(peripheral==SW_HAP_CFG && reg==0x66))
        { readback=sw_hap_read(peripheral,reg,&actual);ok=readback && actual==value; }
    if(!ok) {
        say("haptic: PM8350B write failed peripheral=%#x reg=%#x want=%#x status=%#x readback=%u actual=%#x force=%u\n",
            p->id,reg,value,status,readback,actual,force);
        sw_hap_fault=true;
    }
    return ok;
}

static bool
sw_hap_mask(uint8_t reg,uint8_t mask,uint8_t value)
{
    uint8_t before;
    return sw_hap_read(SW_HAP_CFG,reg,&before) &&
        sw_hap_write(SW_HAP_CFG,reg,(before&~mask)|(value&mask),false);
}

static bool
sw_hap_stop(void)
{
    if(!sw_hap_ready || !sw_hap_changed)return true;
    /* Do not short-circuit: even a failed PLAY clear must attempt module off
     * and the matching PBS boost-off request. */
    bool play=sw_hap_write(SW_HAP_CFG,0x4c,1,true);
    /* Nothing's enabled RichTap variant clears FORCE_VREG_RDY on every stop.
     * Preserve the other VSET bits even during terminal-fault cleanup. */
    uint8_t vset=0;
    bool power=sw_hap_read(SW_HAP_CFG,0x68,&vset) && sw_hap_write(SW_HAP_CFG,0x68,vset&~1u,true);
    bool module=sw_hap_write(SW_HAP_CFG,0x46,0,true);
    bool arg=sw_hap_write(SW_HAP_SDAM,0x42,2,true);
    bool trigger=sw_hap_write(SW_HAP_SDAM,0xe5,1,true);
    sw_hap_active=false;
    return play && power && module && arg && trigger;
}

static bool
sw_hap_healthy(void)
{
    uint8_t fault=0xff;
    if(!sw_hap_read(SW_HAP_CFG,0x0c,&fault) || (fault&7)) {
        say("haptic: PM8350B hardware fault/status=%#x phase=%u; output refused\n",fault,sw_hap_phase);
        sw_hap_fault=true;(void)sw_hap_stop();return false;
    }
    return true;
}

static bool
sw_hap_configure(void)
{
    uint8_t play,swr;
    if(!sw_hap_read(SW_HAP_CFG,0x4c,&play) || !sw_hap_read(SW_HAP_CFG,0x4e,&swr) ||
        (play&0x80)) {
        say("haptic: PM8350B already playing; ownership not taken\n");return false;
    }
    for(unsigned i=0;i<10;i++)if(!sw_hap_read(SW_HAP_CFG,sw_hap_save_regs[i],&sw_hap_saved[i]))return false;
    sw_hap_changed=true;
    /* Keep boost follow/OTP/calibration/NVM defaults. Only explicit volatile
     * haptics controls named by the matching driver are configured here. */
    /* Firmware leaves SoundWire input enabled even with no waveform. HLOS
     * owns this CFG block: exclude SoundWire input during SPMI playback and
     * restore its original value only after the module is stopped. */
    return sw_hap_stop() && sw_hap_mask(0x4e,0x40,0) && sw_hap_write(SW_HAP_CFG,0x48,SW_HAP_VMAX_CODE,false) &&
        sw_hap_mask(0x49,0x13,1) && sw_hap_mask(0x50,0xcf,0x41) &&
        sw_hap_write(SW_HAP_CFG,0x5c,(SW_HAP_TLRA_CODE>>8)&0xf,false) &&
        sw_hap_write(SW_HAP_CFG,0x5d,SW_HAP_TLRA_CODE&0xff,false) &&
        sw_hap_mask(0x63,0x80,0x80) && sw_hap_mask(0x72,0xc,4);
}

static bool
sw_hap_start(uint8_t amplitude)
{
    if(!sw_hap_changed || sw_hap_fault || leaving || !sw_hap_healthy())return false;
    /* The matching driver waits up to20ms for boost standby before a new
     * request; never overlap an asynchronous PBS off request with PLAY. */
    bool boost_ready=false,open_loop=false;uint8_t enable=0,status=0,follow=0;
    for(unsigned retry=0;retry<100 && !leaving;retry++) {
        if(!sw_hap_read(SW_HAP_BOOST,0x41,&follow) || !sw_hap_read(SW_HAP_BOOST,0x46,&enable))break;
        /* Matching PM8350B driver accepts an already-enabled open-loop boost;
         * it does not wait for that valid state to become disabled. */
        if(!(follow&0x80) && (enable&0x80)) { open_loop=true;boost_ready=true;break; }
        if(!(enable&0x80)) {
            if(!sw_hap_read(SW_HAP_BOOST,0x0b,&status))break;
            if(!(status&1)) { boost_ready=true;break; }
        }
        spin_us(200);
    }
    if(!boost_ready) {
        (void)sw_hap_read(SW_HAP_BOOST,0x0b,&status);
        say("haptic: PM8350B boost not ready after20ms follow=%#x enable=%#x status4=%#x; output refused\n",follow,enable,status);
        sw_hap_fault=true;(void)sw_hap_stop();return false;
    }
    /* Set active BEFORE any enable write; a write that lands but whose
     * readback fails still needs all of the off sequence. */
    sw_hap_active=true;
    /* Nothing's qcom-hv-haptics.c enables RICHTAP_FOR_PMIC_ENABLE: FORCE_VREG_RDY
     * applies to every play. Its PM8350B TOGGLE_CAL_RC_CLK workaround applies
     * only when boost was already enabled in open loop and RC_CAL is AUTO. */
    bool power=sw_hap_mask(0x68,1,1);
    if(power && open_loop) {
        uint8_t cal=0;
        power=sw_hap_read(SW_HAP_CFG,0x72,&cal);
        if(power && (cal&0xc)==4)
            power=sw_hap_mask(0x72,0xc,0) && sw_hap_mask(0x72,0xc,4);
    }
    bool ok=power && sw_hap_write(SW_HAP_PTN,0x26,amplitude>SW_HAP_AMPLITUDE?SW_HAP_AMPLITUDE:amplitude,false) &&
        sw_hap_write(SW_HAP_CFG,0x46,0x80,false) &&
        sw_hap_write(SW_HAP_CFG,0x66,7,false) &&
        sw_hap_write(SW_HAP_CFG,0x4c,0x81,false) &&
        sw_hap_write(SW_HAP_SDAM,0x42,1,false) &&
        sw_hap_write(SW_HAP_SDAM,0xe5,1,false);
    if(!ok) { sw_hap_fault=true;(void)sw_hap_stop(); }
    return ok;
}

static void
sw_hap_restore(void)
{
    if(!sw_hap_changed)return;
    (void)sw_hap_stop();
    /* Restore control defaults only after PLAY is off; never re-enable a
     * module that was enabled at entry or replay a saved PLAY command. */
    for(unsigned i=1;i<10;i++)(void)sw_hap_write(SW_HAP_CFG,sw_hap_save_regs[i],sw_hap_saved[i],true);
}

static void
sw_hap_serve(void)
{
    int major=cdevsw_add(-1,&haptic_cdevsw);
    if(major<0 || devfs_make_node(makedev(major,0),DEVFS_CHAR,UID_ROOT,GID_WHEEL,0666,"hakios-haptic")==NULL) {
        say("haptic: PM8350B endpoint creation failed\n");return;
    }
    say("haptic: PM8350B serves Darwin Actuator; 3600mV/25%% ceiling, 250ms pulse/rest caps\n");
    uint64_t started=0,rest_until=0;uint8_t applied=0;unsigned starts=0,stops=0,updates=0;
    while(!leaving && !sw_hap_fault) {
        /* A keyboard waveform can finish before the8ms settling interval.
         * Retain its recent onset through that interval, as the legacy
         * endpoint does, and give a started tap the verified40ms duration. */
        uint64_t now=now_us();bool active=heard_us && now-heard_us<ACTIVE_US;
        uint8_t amplitude=(uint8_t)(heard_peak>32768?SW_HAP_AMPLITUDE:heard_peak*SW_HAP_AMPLITUDE/32768);
        if(active && amplitude<SW_HAP_MIN_AMPLITUDE)amplitude=SW_HAP_MIN_AMPLITUDE;
        if(sw_hap_active && ((!active && now-started>=SW_HAP_TAP_US) || now-started>=SW_HAP_MAX_US)) {
            if(!sw_hap_stop())break;
            sw_hap_phase=7;stops++;
            if(stops<=TOLD_EVENTS)say("haptic: PM8350B runtime stop%u after%lluus; peak%u\n",stops,now-started,heard_peak);
            rest_until=now+SW_HAP_REST_US;applied=0;
        } else if(!sw_hap_active && active && now>=rest_until && onset_us && now-onset_us>=SW_HAP_SETTLE_US) {
            if(!sw_hap_start(amplitude))break;
            sw_hap_phase=6;started=now;applied=amplitude;starts++;
            if(starts<=TOLD_EVENTS)say("haptic: PM8350B runtime start%u amplitude%u peak%u onset_age%lluus\n",
                starts,amplitude,heard_peak,now-onset_us);
        } else if(sw_hap_active && amplitude!=applied) {
            if(amplitude<SW_HAP_MIN_AMPLITUDE)amplitude=SW_HAP_MIN_AMPLITUDE;
            if(!sw_hap_write(SW_HAP_PTN,0x26,amplitude,false))break;
            applied=amplitude;updates++;
        }
        if(sw_hap_active && !sw_hap_healthy())break;
        wait_ms(4);
    }
    (void)sw_hap_stop();
    say("haptic: PM8350B runtime ends; starts%u stops%u updates%u PCMframes%llu fault%u\n",
        starts,stops,updates,written_frames,sw_hap_fault);
}

static void
sw_haptic_thread(__unused void *parameter,__unused wait_result_t result)
{
    sw_hap_phase=1;
    uint32_t channels,version;uint8_t revisions[4]={0},fault=0;
    uint8_t en=0,vmax=0,play=0,swr=0,boost_low=0;
    sw_hap_core=ml_io_map(0x0c440000,0x1100);sw_hap_cnfg=ml_io_map(0x0c40a000,0x1000);
    sw_hap_observer=ml_io_map(0x0e600000,0x10000);
    if(!sw_hap_core || !sw_hap_cnfg || !sw_hap_observer)goto out;
    version=rd(sw_hap_core);channels=rd(sw_hap_core+4)&0x7ff;
    if(version<0x50000000 || version>=0x70000000 || !channels || channels>512)goto out;
    sw_hap_phase=2;
    for(unsigned p=0;p<4;p++)for(unsigned n=0;n<channels;n++) {
        if(((rd(sw_hap_core+0x900+4*n)>>8)&0xfff)!=sw_hap_peripherals[p].id)continue;
        bool ours=(rd(sw_hap_cnfg+0x700+4*n)&7)==0;
        if(!sw_hap_peripherals[p].found || ours) {
            sw_hap_peripherals[p].found=true;sw_hap_peripherals[p].writable=ours;sw_hap_peripherals[p].channel=n;
        }
    }
    for(unsigned p=0;p<4;p++) {
        say("haptic: PM8350B peripheral %#x channel%u found%u writable%u\n",sw_hap_peripherals[p].id,
            sw_hap_peripherals[p].channel,sw_hap_peripherals[p].found,sw_hap_peripherals[p].writable);
        if(!sw_hap_read(p,1,&revisions[p]))goto out;
    }
    if(!sw_hap_read(SW_HAP_CFG,0x0c,&fault) || !sw_hap_read(SW_HAP_BOOST,0,&boost_low))goto out;
    say("haptic: PM8350B cfg/ptn/boost revisions %u/%u/%u, fault=%#x; level%u\n",
        revisions[0],revisions[1],((unsigned)revisions[2]<<8)|boost_low,fault,level);
    if(sw_hap_read(SW_HAP_CFG,0x46,&en) && sw_hap_read(SW_HAP_CFG,0x48,&vmax) &&
        sw_hap_read(SW_HAP_CFG,0x4c,&play) && sw_hap_read(SW_HAP_CFG,0x4e,&swr))
        say("haptic: PM8350B enable=%#x vmax_code=%u play=%#x swr=%#x\n",en,vmax,play,swr);
    sw_hap_phase=3;
    if(level<=2)goto out;
    /* Phone (1) observed CFG2/PTN2/BOOST0001 only. Runtime revision is
     * collected by haptic=1 before an output test enables these writes. */
    if(revisions[0]!=2 || revisions[1]!=2 || revisions[2]!=0 || boost_low!=1 || (fault&7))goto out;
    for(unsigned p=0;p<4;p++) {
        if(p==SW_HAP_BOOST)continue; /* AOP owns it; this backend never writes it. */
        if(!sw_hap_peripherals[p].writable)goto out;
        sw_hap_peripherals[p].write=ml_io_map(0x0c600000+0x10000UL*sw_hap_peripherals[p].channel,0x1000);
        if(!sw_hap_peripherals[p].write)goto out;
    }
    sw_hap_ready=true;sw_hap_phase=4;
    if(!sw_hap_configure())goto out;
    sw_hap_phase=5;
    say("haptic: PM8350B configured; SPMI owns input; nothing played yet\n");
    if(level==4) {
        if(sw_hap_start(0x20)) {
            sw_hap_phase=6;
            say("haptic: PM8350B pulse started,40ms amplitude32 VMAX3600mV\n");
            wait_ms(40);
            bool stopped=sw_hap_stop();sw_hap_phase=7;
            say("haptic: PM8350B pulse complete; off=%u\n",stopped);
        }
    } else if(level==5)sw_hap_serve();
out:
    sw_hap_restore();
    say("haptic: PM8350B stopped; phase=%u fault=%u\n",sw_hap_phase,sw_hap_fault);
    thread_terminate(current_thread());
}
