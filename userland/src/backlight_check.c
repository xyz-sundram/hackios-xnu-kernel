/* Bounded hardware-path check, separate from CoreBrightness's slider handling.
 * For the explicit backlight diagnostic RAM root only. Restore the original
 * firmware percentage after two relative levels; never change preferences.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <stdio.h>
#include <unistd.h>

static int
level(io_service_t service)
{
    CFTypeRef value = IORegistryEntryCreateCFProperty(service,
        CFSTR("HakiosBacklightFirmwarePercent"), kCFAllocatorDefault, 0);
    int result = -1;
    if (value && CFGetTypeID(value) == CFNumberGetTypeID())
        (void)CFNumberGetValue(value, kCFNumberIntType, &result);
    if (value) CFRelease(value);
    return result;
}

static bool
set_level(io_service_t service, unsigned percent)
{
    int fixed = (int)(percent * 5u * 65536u);
    CFNumberRef number = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &fixed);
    const void *key = CFSTR("brightness-nits"), *value = number;
    CFDictionaryRef dict = number ? CFDictionaryCreate(kCFAllocatorDefault, &key, &value, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks) : NULL;
    if (!dict) { if (number) CFRelease(number); return false; }
    kern_return_t (*set_properties)(io_registry_entry_t, CFTypeRef) =
        dlsym(RTLD_DEFAULT, "IORegistryEntrySetCFProperties");
    if (!set_properties) { CFRelease(dict); CFRelease(number); return false; }
    kern_return_t result = set_properties(service, dict);
    CFRelease(dict); CFRelease(number);
    int applied = level(service);
    printf("backlight: self-test requested=%u applied=%d IOKit=%#x\n", percent, applied, result);
    return result == KERN_SUCCESS && applied == (int)percent;
}

int
main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    sleep(10);
    io_service_t service = IOServiceGetMatchingService(0, IOServiceMatching("IOMobileFramebuffer"));
    if (!service) { puts("backlight: self-test no framebuffer"); return 1; }
    int original = level(service);
    if (original < 0 || original > 100) {
        puts("backlight: self-test firmware backend unavailable; no changes");
        IOObjectRelease(service); return 1;
    }
    printf("backlight: self-test original=%d percent\n", original);
    bool ok = set_level(service, 25);
    sleep(2);
    if (ok) ok = set_level(service, 65);
    sleep(2);
    bool restored = set_level(service, (unsigned)original);
    printf("backlight: self-test result=%s restored=%d\n", ok && restored ? "PASS" : "FAILED", restored);
    IOObjectRelease(service);
    return ok && restored ? 0 : 1;
}
