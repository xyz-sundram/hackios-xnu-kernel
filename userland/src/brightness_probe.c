/* Read the brightness interface used by Control Center and inspect the service
 * CoreBrightness matches. Saves bounded evidence on the existing HFS data
 * volume; no setters, injected hooks or display register writes.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <math.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define LOG_CAP (2u * 1024u * 1024u)
static FILE *capture;
static size_t saved;
static int log_failed;
extern int proc_kmsgbuf(void *buffer, uint32_t size);

static void
save(const char *text, size_t size)
{
    if (!capture || log_failed) return;
    if (size > LOG_CAP - saved) { log_failed = 1; return; }
    if (fwrite(text, 1, size, capture) != size) log_failed = 1;
    else saved += size;
}

static void
report(const char *format, ...)
{
    char line[2048];
    va_list ap;
    va_start(ap, format);
    int count = vsnprintf(line, sizeof line, format, ap);
    va_end(ap);
    if (count <= 0) return;
    size_t size = (size_t)count < sizeof line ? (size_t)count : sizeof line - 1;
    (void)fwrite(line, 1, size, stdout);
    save(line, size);
}

static void
kernel_snapshot(long elapsed)
{
    const unsigned cap = 1024u * 1024u;
    char *buffer = malloc(cap + 1);
    if (!buffer) return;
    int size = proc_kmsgbuf(buffer, cap);
    if (size > 0 && (unsigned)size <= cap) {
        buffer[size] = 0;
        char heading[100];
        int n = snprintf(heading, sizeof heading, "\n== brightness kernel snapshot +%lds ==\n", elapsed);
        if (n > 0) save(heading, (size_t)n);
        size_t limit = strnlen(buffer, (size_t)size), budget = 96u * 1024u;
        for (size_t at = 0; at < limit && budget;) {
            char *line = buffer + at;
            size_t length = strcspn(line, "\n");
            char end = line[length];
            line[length] = 0;
            if (!strstr(line, "brightness_probe:") && (strstr(line, "MiniIOMobileFramebuffer") ||
                strstr(line, "CoreBrightness") || strstr(line, "Backlight") ||
                strstr(line, "brightness") || strstr(line, "Brightness") || strstr(line, "backlight:") || strstr(line, "panic("))) {
                if (length + 1 <= budget) { save(line, length); save("\n", 1); budget -= length + 1; }
            }
            line[length] = end;
            at += length + (end == '\n');
        }
    }
    free(buffer);
}

static void
describe(CFTypeRef value)
{
    if (!value) { report("absent"); return; }
    CFStringRef text = CFCopyDescription(value);
    char buffer[1024];
    if (text && CFStringGetCString(text, buffer, sizeof buffer, kCFStringEncodingUTF8))
        report("%s", buffer);
    else report("description unavailable");
    if (text) CFRelease(text);
}

static void
services(const char *class_name)
{
    io_iterator_t iterator = IO_OBJECT_NULL;
    kern_return_t result = IOServiceGetMatchingServices(0,
        IOServiceMatching(class_name), &iterator);
    report("brightness_probe: matching %s result=%#x\n", class_name, result);
    if (result != KERN_SUCCESS) return;
    io_service_t service;
    unsigned count = 0;
    while (count < 16 && (service = IOIteratorNext(iterator))) {
        io_name_t name = "";
        IORegistryEntryGetName(service, name);
        report("brightness_probe: service %s\n", name);
        const CFStringRef keys[] = { CFSTR("IOClass"), CFSTR("backlight-control"),
            CFSTR("IODisplayParameters"), CFSTR("IOUserClientClass"), CFSTR("HakiosBacklightFirmwarePercent") };
        for (unsigned i = 0; i < sizeof keys / sizeof keys[0]; i++) {
            report("  "); describe(keys[i]); report("=");
            CFTypeRef value = IORegistryEntryCreateCFProperty(service, keys[i],
                kCFAllocatorDefault, 0);
            describe(value); report("\n");
            if (value) CFRelease(value);
        }
        count++;
        IOObjectRelease(service);
    }
    IOObjectRelease(iterator);
    report("brightness_probe: %s services=%u%s\n", class_name, count,
        count == 16 ? " (capped)" : "");
}

int
main(int argc, char **argv)
{
    char *end = NULL;
    long seconds = argc == 2 ? strtol(argv[1], &end, 10) : 180;
    if (argc > 2 || (argc == 2 && (!argv[1][0] || *end)) || seconds < 1 || seconds > 300) {
        fputs("usage: brightness_probe [seconds:1..300]\n", stderr);
        return 2;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    struct statfs fs;
    if (statfs("/private/var", &fs) || strcmp(fs.f_fstypename, "hfs") || (fs.f_flags & MNT_RDONLY)) {
        fputs("brightness_probe: writable persistent HFS data volume required\n", stderr);
        return 1;
    }
    char path[] = "/private/var/log/hakios-debug-XXXXXX";
    int fd = mkstemp(path);
    if (fd < 0) { perror("brightness_probe log"); return 1; }
    if (fchmod(fd, 0600) || !(capture = fdopen(fd, "w"))) {
        close(fd); unlink(path); return 1;
    }
    report("brightness_probe: private capture %s seconds=%ld pid=%d uid=%u euid=%u\n",
        path, seconds, getpid(), (unsigned)getuid(), (unsigned)geteuid());
    fflush(capture);
    (void)fsync(fd);
    void *framework = dlopen("/System/Library/PrivateFrameworks/BackBoardServices.framework/BackBoardServices",
        RTLD_LAZY | RTLD_LOCAL);
    if (!framework) { report("brightness_probe: %s\n", dlerror()); fclose(capture); return 3; }
    float (*current)(void) = dlsym(framework, "BKSDisplayBrightnessGetCurrent");
    bool (*available)(void) = dlsym(framework, "BKSDisplayBrightnessIsBrightnessLevelControlAvailable");
    if (!current || !available) { report("brightness_probe: required BKS API absent\n"); fclose(capture); return 4; }
    report("brightness_probe: read-only BKS/IORegistry capture, %ld seconds\n", seconds);
    float last = -1;
    int last_available = -1;
    for (long elapsed = 0; elapsed < seconds; elapsed++) {
        if (elapsed == 10 || elapsed == 90) {
            services("IOMobileFramebuffer");
            services("AppleBacklightDisplay");
        }
        float value = current();
        int can_adjust = available();
        if (value != last || can_adjust != last_available || elapsed % 30 == 0) {
            report("brightness_probe: epoch=%lld elapsed=%ld available=%d current=%.6f finite=%d\n",
                (long long)time(NULL), elapsed, can_adjust, (double)value, isfinite(value) != 0);
            last = value;
            last_available = can_adjust;
        }
        if (elapsed % 30 == 0) kernel_snapshot(elapsed);
        if (fflush(capture) || fsync(fd)) log_failed = 1;
        if (log_failed) { fputs("brightness_probe: persistent capture write failed\n", stderr); break; }
        sleep(1);
    }
    report("brightness_probe: finished bytes=%zu log_failed=%d\n", saved, log_failed);
    if (fflush(capture) || fsync(fd)) log_failed = 1;
    if (fclose(capture)) log_failed = 1;
    return log_failed ? 1 : 0;
}
