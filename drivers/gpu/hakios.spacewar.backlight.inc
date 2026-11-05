/* IOMobileFramebuffer's native BrightnessControl.framework property interface.
 * Firmware calls run only on the resident loader's CPU0, between touch leases.
 * Included after the framebuffer class definition.
 */
#include <vm/memory_types.h>
#include <libkern/c++/OSString.h>
extern "C" vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned int);

static void
backlight_invalidate(const volatile void *line)
{
    __asm__ volatile("dc ivac, %0\ndsb sy" :: "r"((uintptr_t)line & ~(uintptr_t)63) : "memory");
}

static void
backlight_clean(const volatile void *line)
{
    __asm__ volatile("dc cvac, %0\ndsb sy" :: "r"((uintptr_t)line & ~(uintptr_t)63) : "memory");
}

static OSDictionary *
backlight_parameter(uint32_t minimum, uint32_t maximum, uint32_t value)
{
    OSDictionary *dict = OSDictionary::withCapacity(3);
    if (!dict) return NULL;
    const char *keys[] = {"min", "max", "value"};
    uint32_t values[] = {minimum, maximum, value};
    for (unsigned i = 0; i < 3; i++) {
        OSNumber *number = OSNumber::withNumber(values[i], 32);
        if (!number) { dict->release(); return NULL; }
        dict->setObject(keys[i], number);
        number->release();
    }
    return dict;
}

bool
IOMobileFramebuffer::backlightPublish(void)
{
    OSDictionary *parameters = OSDictionary::withCapacity(2);
    OSDictionary *user = backlight_parameter(10, 1000, fBacklightApplied * 10);
    OSDictionary *nits = backlight_parameter(SW_BACKLIGHT_MAX_MILLINITS / 100,
        SW_BACKLIGHT_MAX_MILLINITS, fBacklightApplied * (SW_BACKLIGHT_MAX_MILLINITS / 100));
    bool ok = parameters && user && nits;
    if (ok) {
        parameters->setObject("brightness", user);
        parameters->setObject("BrightnessMilliNits", nits);
        ok = setProperty("IODisplayParameters", parameters);
        setProperty("brightness-nits", (uint64_t)fBacklightApplied * 5 * 65536, 32);
        setProperty("HakiosBacklightFirmwarePercent", (uint64_t)fBacklightApplied, 32);
    }
    if (user) user->release();
    if (nits) nits->release();
    if (parameters) parameters->release();
    return ok;
}

bool
IOMobileFramebuffer::backlightStart(void)
{
    fBacklightReady = false;
    fBacklightPage = NULL;
    fBacklightLock = NULL;
    uint32_t enabled = 0, phone = 0, live = 0, cpu = 0;
    uint64_t pa = 0;
    PE_parse_boot_argn("backlight", &enabled, sizeof(enabled));
    PE_parse_boot_argn("touch_spacewar", &phone, sizeof(phone));
    PE_parse_boot_argn("touch_live", &live, sizeof(live));
    PE_parse_boot_argn("bootcpu", &cpu, sizeof(cpu));
    if (enabled != 1 || phone != 1 || live != 1 || cpu != 7) return false;
    if (!PE_parse_boot_argn("touch_shared_pa", &pa, sizeof(pa)) || !pa || (pa & 0x3fff) ||
        pa > UINT64_MAX - 16384 || (4u << ((__builtin_arm_rsr64("CTR_EL0") >> 16) & 15)) > 64) return false;
    vm_offset_t mapped = ml_io_map_unmappable((vm_offset_t)pa, 16384, VM_WIMG_DEFAULT);
    if (!mapped) return false;
    volatile sw_backlight *b = (volatile sw_backlight *)(mapped + SW_BACKLIGHT_OFFSET);
    backlight_invalidate(b);
    backlight_invalidate(&b->request);
    backlight_invalidate(&b->answered);
    if (b->magic != SW_BACKLIGHT_MAGIC || b->version != SW_BACKLIGHT_VERSION ||
        b->size != sizeof(*b) || b->capabilities != SW_BACKLIGHT_CAP_FIRMWARE ||
        b->initial_percent > 100 || b->max_millinits != SW_BACKLIGHT_MAX_MILLINITS ||
        b->request || b->answered || b->status) {
        IOLog("backlight: missing/incompatible firmware capability; controls remain unavailable\n");
        return false;
    }
    fBacklightLock = IOLockAlloc();
    if (!fBacklightLock) return false;
    fBacklightPage = b;
    fBacklightApplied = b->initial_percent;
    fBacklightSequence = 0;
    fBacklightReady = backlightPublish();
    if (!fBacklightReady) { IOLockFree(fBacklightLock); fBacklightLock = NULL; return false; }
    uint8_t force = 1;
    // BCAppleBacklightBrtControl reads this flag as one byte of CFData.
    if (!setProperty("force-brightness-control", &force, sizeof(force))) {
        fBacklightReady = false;
        return false;
    }
    setProperty("HakiosBacklightNominalScale", "uncalibrated SDR; firmware percent");
    // IOMFB's modern brightness monitor associates its embedded display with
    // BCAppleBacklightBrtControl through BuiltIn, rather than a USB container ID.
    if (!setProperty("BuiltIn", kOSBooleanTrue)) {
        fBacklightReady = false;
        return false;
    }
    setProperty("backlight-control", kOSBooleanTrue);
    IOLog("backlight: native BrightnessControl property interface ready at %u percent\n", fBacklightApplied);
    return true;
}

IOReturn
IOMobileFramebuffer::backlightSet(uint32_t percent)
{
    if (percent > 100 || !fBacklightReady) return kIOReturnNotReady;
    IOLockLock(fBacklightLock);
    if (!fBacklightReady) { IOLockUnlock(fBacklightLock); return kIOReturnNotReady; }
    volatile sw_backlight *b = fBacklightPage;
    backlight_invalidate(&b->answered);
    if (b->answered != fBacklightSequence || fBacklightSequence == UINT64_MAX) {
        IOLockUnlock(fBacklightLock);
        return kIOReturnBusy;
    }
    if (percent == fBacklightApplied) { IOLockUnlock(fBacklightLock); return kIOReturnSuccess; }
    uint64_t request = ++fBacklightSequence;
    b->percent = percent;
    b->request_reserved = 0;
    __asm__ volatile("dmb ishst" ::: "memory");
    b->request = request;
    backlight_clean(&b->request);
    IOReturn result = kIOReturnTimeout;
    for (unsigned retry = 0; retry < 2000; retry++) {
        backlight_invalidate(&b->answered);
        if (b->answered == request) {
            __asm__ volatile("dmb ishld" ::: "memory");
            if (!b->status && b->applied_percent == percent) {
                fBacklightApplied = percent;
                result = backlightPublish() ? kIOReturnSuccess : kIOReturnNoMemory;
            } else result = kIOReturnIOError;
            break;
        }
        IOSleep(1);
    }
    IOLog("backlight: native set %u percent, applied=%u request=%llu result=%#x firmware=%#llx\n",
        percent, fBacklightApplied, request, result, b->status);
    if (result != kIOReturnSuccess) {
        fBacklightReady = false;
        setProperty("backlight-control", kOSBooleanFalse);
    }
    IOLockUnlock(fBacklightLock);
    return result;
}

IOReturn
IOMobileFramebuffer::setProperties(OSObject *properties)
{
    OSDictionary *dict = OSDynamicCast(OSDictionary, properties);
    if (!dict || !fBacklightReady) return IOService::setProperties(properties);
    OSNumber *nits = OSDynamicCast(OSNumber, dict->getObject("brightness-nits"));
    OSDictionary *parameters = OSDynamicCast(OSDictionary, dict->getObject("IODisplayParameters"));
    OSNumber *user = OSDynamicCast(OSNumber, parameters ? parameters->getObject("brightness") : dict->getObject("brightness"));
    if (nits && user) return kIOReturnBadArgument;
    if (nits) {
        uint64_t value = nits->unsigned64BitValue();
        const uint64_t maximum = (uint64_t)(SW_BACKLIGHT_MAX_MILLINITS / 1000) << 16;
        if (value > maximum) return kIOReturnBadArgument;
        uint32_t percent = (uint32_t)((value * 100 + maximum / 2) / maximum);
        if (value && !percent) percent = 1;
        return backlightSet(percent);
    }
    if (user) {
        uint64_t value = user->unsigned64BitValue();
        if (value > 1000) return kIOReturnBadArgument;
        uint32_t percent = (uint32_t)((value + 5) / 10);
        if (value && !percent) percent = 1;
        return backlightSet(percent);
    }
    return IOService::setProperties(properties);
}
