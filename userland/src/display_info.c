/* Display identity diagnostics and opt-in repair of a retained scale cache.
 * Run before launchd: persistent MobileGestalt answers can outlive a canvas
 * change even when the framebuffer and device tree already agree. */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <unistd.h>

extern CFTypeRef MGCopyAnswer(CFStringRef key);

static const char cache_path[] = "/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist";

/* Keep the complete original, including its mode and owner. A private unique
 * destination prevents overwriting an earlier backup. Reject links and files
 * outside the bounded cache format before changing a directory entry. */
static bool
backup_scale_cache(const char *path, char *backup, size_t capacity)
{
	struct stat st;
	if (lstat(path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 0 || st.st_size > 2 * 1024 * 1024) return false;
	int n = snprintf(backup, capacity, "%s.hakios-display.XXXXXX", path);
	if (n < 0 || (size_t)n >= capacity) return false;
	int fd = mkstemp(backup);
	if (fd < 0) return false;
	close(fd);
	if (rename(path, backup) == 0) return true;
	unlink(backup);
	return false;
}

static uint32_t
tree_word(const char *path, CFStringRef key)
{
	uint32_t word = 0;
	io_registry_entry_t node = IORegistryEntryFromPath(MACH_PORT_NULL, path);
	CFTypeRef data = node ? IORegistryEntryCreateCFProperty(node, key, NULL, 0) : NULL;
	if (data && CFGetTypeID(data) == CFDataGetTypeID() && CFDataGetLength(data) == sizeof(word)) {
		CFDataGetBytes(data, CFRangeMake(0, sizeof(word)), (UInt8 *)&word);
	}
	if (data) CFRelease(data);
	if (node) IOObjectRelease(node);
	return word;
}

/* A fresh image cannot inherit a cache mapped by a library constructor.
 * Bound a missing-helper wait before launchd is available. */
static unsigned
gestalt_scale(void)
{
	fflush(NULL);
	pid_t child = fork();
	if (child < 0) return 0;
	if (child == 0) {
		execl("/redwood/bin/display_info", "display_info", "--query-scale", (char *)NULL);
		_exit(1);
	}
	for (unsigned i = 0; i < 50; i++) {
		int status = 0;
		if (waitpid(child, &status, WNOHANG) == child) return WIFEXITED(status) ? (unsigned)WEXITSTATUS(status) : 0;
		usleep(100000);
	}
	kill(child, SIGKILL);
	waitpid(child, NULL, 0);
	return 0;
}

static bool
canvas_matches(unsigned scale)
{
	io_service_t service = IOServiceGetMatchingService(MACH_PORT_NULL, IOServiceMatching("IOMobileFramebuffer"));
	io_connect_t connection = 0;
	bool matches = false;
	if (service && IOServiceOpen(service, mach_task_self(), 0, &connection) == KERN_SUCCESS) {
		uint64_t size[2] = {0, 0};
		uint32_t count = 2;
		matches = IOConnectCallScalarMethod(connection, 8, NULL, 0, size, &count) == KERN_SUCCESS &&
		    count == 2 && ((size[0] == 375 * scale && size[1] == 812 * scale) ||
                (scale == 2 && tree_word("IODeviceTree:/product", CFSTR("artwork-device-subtype")) == 1792 &&
                size[0] == 828 && size[1] == 1792));
		IOServiceClose(connection);
	}
	if (service) IOObjectRelease(service);
	return matches;
}

static bool
refresh_scale_cache(void)
{
	char arguments[1024] = "";
	size_t bytes = sizeof(arguments) - 1;
	if (sysctlbyname("kern.bootargs", arguments, &bytes, NULL, 0) != 0) return false;
	char framed[1028];
	snprintf(framed, sizeof(framed), " %s ", arguments);
	if (!strstr(framed, " touch_spacewar=1 ")) return false;
	unsigned scale = tree_word("IODeviceTree:/chosen", CFSTR("display-scale"));
	if ((scale != 2 && scale != 3) || tree_word("IODeviceTree:/product", CFSTR("artwork-scale-factor")) != scale ||
	    !canvas_matches(scale)) return false;
	unsigned cached = gestalt_scale();
	printf("display_info: scale cache %u, device tree %u\n", cached, scale);
	/* XR also changes screen class: retire retained geometry with the scale. */
	if (cached == scale && tree_word("IODeviceTree:/product", CFSTR("artwork-device-subtype")) != 1792) return true;
	if (cached == 4) {
		printf("display_info: no saved scale answer; awaiting MobileGestaltHelper\n");
		return true;
	}
	if (cached != 2 && cached != 3) return false;
	char backup[1024];
	if (!backup_scale_cache(cache_path, backup, sizeof(backup))) return false;
	printf("display_info: retained stale scale cache at %s\n", backup);
	unsigned refreshed = gestalt_scale();
	if (refreshed == 4) {
		printf("display_info: stale cache retired; awaiting MobileGestaltHelper\n");
		return true;
	}
	printf("display_info: refreshed main-screen-scale = %u (expected %u)\n", refreshed, scale);
	return refreshed == scale;
}

/* Read-only after launchd starts its cache helper. Each query uses a fresh
 * process; a pending first answer must not be retained by this observer. */
static bool
verify_scale(void)
{
	unsigned scale = tree_word("IODeviceTree:/chosen", CFSTR("display-scale"));
	if ((scale != 2 && scale != 3) || !canvas_matches(scale)) return false;
	for (unsigned i = 0; i < 30; i++) {
		if (gestalt_scale() == scale) {
			printf("display_info: verified canvas and %ux scale\n", scale);
			return true;
		}
		sleep(1);
	}
	fprintf(stderr, "display_info: startup scale did not match the canvas\n");
	return false;
}

static void
number(const char *label, CFTypeRef value)
{
	double n = 0;
	if (value != NULL && CFGetTypeID(value) == CFNumberGetTypeID() &&
	    CFNumberGetValue(value, kCFNumberDoubleType, &n)) {
		printf("display_info: %s = %.6g\n", label, n);
	} else if (value != NULL && CFGetTypeID(value) == CFDataGetTypeID() && CFDataGetLength(value) == 4) {
		uint32_t word;
		CFDataGetBytes(value, CFRangeMake(0, 4), (UInt8 *)&word);
		printf("display_info: %s = %u (DT data)\n", label, word);
	} else {
		printf("display_info: %s = %s\n", label, value != NULL ? "other type" : "absent");
	}
}

static void
property(const char *path, const char *name)
{
	io_registry_entry_t node = IORegistryEntryFromPath(MACH_PORT_NULL, path);
	CFStringRef key = CFStringCreateWithCString(NULL, name, kCFStringEncodingUTF8);
	CFTypeRef value = node ? IORegistryEntryCreateCFProperty(node, key, NULL, 0) : NULL;
	number(name, value);
	if (value != NULL) CFRelease(value);
	CFRelease(key);
	if (node) IOObjectRelease(node);
}

static void
cache(const char *path)
{
	struct stat st;
	FILE *file = fopen(path, "rb");
	printf("display_info: cache %s: %s\n", path, file != NULL ? "present" : "absent");
	if (file == NULL) return;
	if (fstat(fileno(file), &st) != 0 || st.st_size <= 0 || st.st_size > 2 * 1024 * 1024) {
		fclose(file);
		return;
	}
	uint8_t *bytes = malloc((size_t)st.st_size);
	if (bytes != NULL && fread(bytes, 1, (size_t)st.st_size, file) == (size_t)st.st_size) {
		CFDataRef data = CFDataCreate(NULL, bytes, st.st_size);
		CFPropertyListRef plist = CFPropertyListCreateWithData(NULL, data, kCFPropertyListImmutable, NULL, NULL);
		if (plist != NULL && CFGetTypeID(plist) == CFDictionaryGetTypeID()) {
			CFTypeRef extra = CFDictionaryGetValue(plist, CFSTR("CacheExtra"));
			printf("display_info: cache bytes %lld; CacheExtra %s\n", (long long)st.st_size,
			    extra && CFGetTypeID(extra) == CFDictionaryGetTypeID() ? "dictionary" : "absent/other");
			if (extra && CFGetTypeID(extra) == CFDictionaryGetTypeID()) {
				number("cached main-screen-scale", CFDictionaryGetValue(extra, CFSTR("SNfDJgQFV2Xj7+WnozcJPw")));
				number("cached ArtworkTraitScale", CFDictionaryGetValue(extra, CFSTR("ZDdFKhqysHoerajbxzQcqA")));
			}
		}
		if (plist != NULL) CFRelease(plist);
		CFRelease(data);
	}
	free(bytes);
	fclose(file);
}

int
main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[1], "--query-scale") == 0) {
		CFTypeRef answer = MGCopyAnswer(CFSTR("main-screen-scale"));
		if (answer == NULL) return 4;
		double scale = 0;
		if (answer && CFGetTypeID(answer) == CFNumberGetTypeID()) CFNumberGetValue(answer, kCFNumberDoubleType, &scale);
		return scale == 2 ? 2 : scale == 3 ? 3 : 1;
	}
	if (argc == 2 && strcmp(argv[1], "--verify-scale") == 0) return verify_scale() ? 0 : 1;
	if (argc == 2 && strcmp(argv[1], "--refresh-scale-cache") == 0) {
		if (!refresh_scale_cache()) {
			fprintf(stderr, "display_info: scale cache repair could not be verified\n");
			return 1;
		}
		return 0;
	} else if (argc != 1) return 2;
	property("IODeviceTree:/chosen", "display-scale");
	property("IODeviceTree:/product", "artwork-scale-factor");
	property("IODeviceTree:/product", "artwork-device-subtype");
	io_service_t service = IOServiceGetMatchingService(MACH_PORT_NULL, IOServiceMatching("IOMobileFramebuffer"));
	io_connect_t connection = 0;
	if (service && IOServiceOpen(service, mach_task_self(), 0, &connection) == KERN_SUCCESS) {
		uint64_t size[2] = {0, 0};
		uint32_t count = 2;
		kern_return_t result = IOConnectCallScalarMethod(connection, 8, NULL, 0, size, &count);
		printf("display_info: framebuffer size %llu x %llu (result %d)\n", size[0], size[1], result);
		IOServiceClose(connection);
	}
	if (service) IOObjectRelease(service);
	cache(cache_path);
	cache("/var/mobile/Library/Caches/com.apple.MobileGestalt.plist");
	static const char *const keys[] = {"main-screen-scale", "main-screen-width", "main-screen-height", "ArtworkTraitScale", "ArtworkDeviceSubType"};
	for (unsigned i = 0; i < sizeof(keys) / sizeof(keys[0]); i++) {
		CFStringRef key = CFStringCreateWithCString(NULL, keys[i], kCFStringEncodingUTF8);
		CFTypeRef value = MGCopyAnswer(key);
		number(keys[i], value);
		if (value != NULL) CFRelease(value);
		CFRelease(key);
	}
	return 0;
}
