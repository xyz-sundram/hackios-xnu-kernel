/*
 * Optional, diagnostic-only Wi-Fi framework boundary. Insert into wifid alone
 * to inspect a failure that occurs before the ASSOCIATE ioctl. It never reads
 * or logs a network name, address, password, derived key, or dictionary value.
 * The original framework functions receive their arguments unchanged.
 */
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>

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

#define ASSOC_TRACE(name) \
static int trace_##name(void *handle, const void *network, const void *password) \
{ \
	trace("hakios-wifi: %s entered\n", #name); \
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
