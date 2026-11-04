/*
 * Optional, diagnostic-only Wi-Fi framework boundary. Insert into wifid alone
 * to inspect a failure that occurs before the ASSOCIATE ioctl. Only named
 * numeric security properties are inspected. It never reads or logs a network
 * name, address, password, derived key, or arbitrary dictionary value.
 * The original framework functions receive their arguments unchanged.
 */
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>
#include <CoreFoundation/CoreFoundation.h>

#include "hakios.debug.h"

/* iOS16.5/20F66 wifid calls this with x0=handle, x1=network,
 * x2=password and consumes a 32-bit return. The local framework entry at
 * 0x1d30c71d4 forwards those three unchanged to the shared implementation.
 * Treat them as opaque pointers; diagnostics inspect none of them. */
extern int Apple80211AssociateAsync(void *, const void *, const void *);

static void
trace(const char *format, ...)
{
	char line[256];
	va_list args;
	int length, fd;

	if (!hakios_debug_enabled()) return;
	va_start(args, format);
	length = vsnprintf(line, sizeof(line), format, args);
	va_end(args);
	if (length <= 0) return;
	fd = open("/dev/console", O_WRONLY | O_NOCTTY);
	if (fd >= 0) {
		(void)write(fd, line, (size_t)(length < (int)sizeof(line) ? length : (int)sizeof(line) - 1));
		close(fd);
	}
}

/* These key names and numeric types are from the local iOS 16.5 framework.
 * Do not enumerate the dictionary: other fields contain private identifiers.
 * In particular, a string where a number is expected must never be printed. */
static void
number(const char *label, CFTypeRef value)
{
	int32_t n;
	if (value != NULL && CFGetTypeID(value) == CFNumberGetTypeID() &&
	    CFNumberGetValue((CFNumberRef)value, kCFNumberSInt32Type, &n)) {
		trace("hakios-wifi: security %s=%d\n", label, n);
	}
}

static void
numbers(const char *label, CFTypeRef value)
{
	if (value == NULL || CFGetTypeID(value) != CFArrayGetTypeID()) return;
	CFIndex count = CFArrayGetCount((CFArrayRef)value);
	for (CFIndex i = 0; i < count && i < 16; i++) {
		number(label, CFArrayGetValueAtIndex((CFArrayRef)value, i));
	}
}

static void
security(const void *network)
{
	if (!hakios_debug_enabled() || network == NULL ||
	    CFGetTypeID(network) != CFDictionaryGetTypeID()) return;
	CFDictionaryRef n = (CFDictionaryRef)network;
	CFTypeRef rsn = CFDictionaryGetValue(n, CFSTR("RSN_IE"));
	CFTypeRef wpa = CFDictionaryGetValue(n, CFSTR("WPA_IE"));
	trace("hakios-wifi: security rsn=%u wpa=%u\n", rsn != NULL, wpa != NULL);
	if (rsn != NULL && CFGetTypeID(rsn) == CFDictionaryGetTypeID()) {
		number("rsn-group", CFDictionaryGetValue(rsn, CFSTR("IE_KEY_RSN_MCIPHER")));
		number("rsn-caps", CFDictionaryGetValue(rsn, CFSTR("IE_KEY_RSN_CAPS")));
		numbers("rsn-pairwise", CFDictionaryGetValue(rsn, CFSTR("IE_KEY_RSN_UCIPHERS")));
		numbers("rsn-akm", CFDictionaryGetValue(rsn, CFSTR("IE_KEY_RSN_AUTHSELS")));
	}
	if (wpa != NULL && CFGetTypeID(wpa) == CFDictionaryGetTypeID()) {
		number("wpa-group", CFDictionaryGetValue(wpa, CFSTR("IE_KEY_WPA_MCIPHER")));
		numbers("wpa-pairwise", CFDictionaryGetValue(wpa, CFSTR("IE_KEY_WPA_UCIPHERS")));
		numbers("wpa-akm", CFDictionaryGetValue(wpa, CFSTR("IE_KEY_WPA_AUTHSELS")));
	}
}

#define ASSOC_TRACE(name) \
static int trace_##name(void *handle, const void *network, const void *password) \
{ \
	trace("hakios-wifi: %s entered\n", #name); \
	security(network); \
	int result = name(handle, network, password); \
	trace("hakios-wifi: %s returned %d\n", #name, result); \
	return result; \
} \
__attribute__((used, section("__DATA,__interpose"))) \
static const struct { const void *replacement, *original; } interpose_##name = { \
	(const void *)trace_##name, (const void *)name \
}

ASSOC_TRACE(Apple80211AssociateAsync);

__attribute__((constructor)) static void
loaded(void)
{
	trace("hakios-wifi: diagnostic association boundary loaded; original arguments and results preserved\n");
}
