/* iOS 16.5 (20F66) CoreAnimation registration for the native Spacewar backlight.
 * The iPhone X device identity selects the legacy AppleARMBacklight path.
 * Enable CA's existing BrightnessControl transaction path only when our real,
 * acknowledged native backend is present. No ALS/touch timing hooks, persistent
 * preferences, BKS availability overrides or brightness setters live here.
 * This does not promise atomic frame/brightness presentation on Spacewar.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <objc/runtime.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
#include <unistd.h>

extern const void *_dyld_get_shared_cache_range(size_t *length);
extern int proc_name(int pid, void *buffer, uint32_t size);

static int
argument(const char *line, const char *word)
{
    size_t n = strlen(word);
    for (const char *p = line; (p = strstr(p, word)); p += n)
        if ((p == line || p[-1] == ' ') && (!p[n] || p[n] == ' ')) return 1;
    return 0;
}

static BOOL (*original_find_backlight)(id, SEL);
static Ivar proxy_ivar;
static ptrdiff_t service_offset, backlight_offset, registry_offset, built_in_offset;

static io_service_t
native_service(void)
{
    io_service_t service = IOServiceGetMatchingService(0, IOServiceMatching("IOMobileFramebuffer"));
    if (!service) return 0;
    CFTypeRef ready = IORegistryEntryCreateCFProperty(service, CFSTR("backlight-control"), NULL, 0);
    CFTypeRef built_in = IORegistryEntryCreateCFProperty(service, CFSTR("BuiltIn"), NULL, 0);
    CFTypeRef native = IORegistryEntryCreateCFProperty(service, CFSTR("HakiosBacklightFirmwarePercent"), NULL, 0);
    int valid = ready == kCFBooleanTrue && built_in == kCFBooleanTrue && native &&
        CFGetTypeID(native) == CFNumberGetTypeID();
    if (ready) CFRelease(ready);
    if (built_in) CFRelease(built_in);
    if (native) CFRelease(native);
    if (!valid) { IOObjectRelease(service); return 0; }
    return service;
}

static BOOL
find_native_backlight(id self, SEL selector)
{
    uint8_t *object = (uint8_t *)self;
    // Legacy containers keep the stock matcher. The modern proxy already owns
    // brightness transactions, but 20F66 still asks for AppleARMBacklight to
    // obtain its calibration/property service. Supply our real service there.
    if (!object_getIvar(self, proxy_ivar) || *(io_service_t *)(object + service_offset) ||
        *(io_service_t *)(object + backlight_offset)) return original_find_backlight(self, selector);
    io_service_t service = native_service();
    uint64_t registry = 0;
    if (!service) return original_find_backlight(self, selector);
    if (IORegistryEntryGetRegistryEntryID(service, &registry) || IOObjectRetain(service)) {
        IOObjectRelease(service);
        return original_find_backlight(self, selector);
    }
    // Each stock field owns a separate reference, released by the stock dealloc.
    *(io_service_t *)(object + service_offset) = service;
    *(io_service_t *)(object + backlight_offset) = service;
    *(uint64_t *)(object + registry_offset) = registry;
    *(BOOL *)(object + built_in_offset) = YES;
    fprintf(stderr, "backlight: CoreBrightness modern container bound to native service %llu\n", registry);
    return YES;
}

__attribute__((constructor)) static void
native_brightness_registration(void)
{
    char name[64] = {0}, args[1024] = {0};
    size_t size = sizeof args;
    if (!proc_name(getpid(), name, sizeof name) || strcmp(name, "backboardd") ||
        sysctlbyname("kern.bootargs", args, &size, NULL, 0) ||
        !argument(args, "backlight=1") || !argument(args, "touch_spacewar=1")) return;
    io_service_t service = native_service();
    if (!service) { fprintf(stderr, "backlight: CA bridge refused: native backend unavailable\n"); return; }
    IOObjectRelease(service);

    size_t length = 0;
    uintptr_t base = (uintptr_t)_dyld_get_shared_cache_range(&length);
    if (!base || length < 0x5cf3fe09UL) return;
    uintptr_t slide = base - 0x180000000UL;
    Class cls = objc_getClass("CAWindowServerDisplay");
    Method method = cls ? class_getInstanceMethod(cls, sel_registerName("brightnessAvailable")) : NULL;
    const uint32_t *code = (const uint32_t *)(0x1884c2b88UL + slide);
    const uintptr_t *block = (const uintptr_t *)(0x1d5b55368UL + slide);
    if (!method || (uintptr_t)method_getImplementation(method) != 0x1884df7c4UL + slide ||
        code[0] != 0xd100c3ff || code[1] != 0xa9014ff4 ||
        block[2] != (uintptr_t)code) {
        fprintf(stderr, "backlight: CA bridge refused: unsupported shared cache\n"); return;
    }
    Class container = objc_getClass("CBDisplayContaineriOS");
    Method find = container ? class_getInstanceMethod(container, sel_registerName("findBacklight")) : NULL;
    const char *names[] = {"_displayService", "_armBacklightDisplayService", "_registryID", "_builtIn", "_brtCtl"};
    const ptrdiff_t expected[] = {0x88, 0xb8, 0x98, 0x8d, 0xb0};
    Ivar fields[5];
    if (!find || (uintptr_t)method_getImplementation(find) != 0x1af068224UL + slide) return;
    for (unsigned i = 0; i < 5; i++) {
        fields[i] = class_getInstanceVariable(container, names[i]);
        if (!fields[i] || ivar_getOffset(fields[i]) != expected[i]) return;
    }
    service_offset = expected[0]; backlight_offset = expected[1];
    registry_offset = expected[2]; built_in_offset = expected[3]; proxy_ivar = fields[4];
    original_find_backlight = (BOOL (*)(id, SEL))method_setImplementation(find, (IMP)find_native_backlight);
    // Run the stock once-only feature/preference initialization before applying
    // the per-process port policy; it cannot overwrite the policy afterwards.
    dispatch_once((dispatch_once_t *)(0x1dcf3cc60UL + slide), (dispatch_block_t)block);
    volatile uint8_t *enabled = (volatile uint8_t *)(0x1dcf3fe08UL + slide);
    unsigned previous = *enabled;
    *enabled = 1;
    fprintf(stderr, "backlight: CA native transaction registration enabled (stock=%u)\n", previous);
}
