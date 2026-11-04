/* Runtime diagnostics switch; absence preserves the original debug behavior. */
#ifndef HAKIOS_DEBUG_H
#define HAKIOS_DEBUG_H
#include <pthread.h>
#include <string.h>
#include <sys/sysctl.h>

static pthread_once_t hakios_debug_once = PTHREAD_ONCE_INIT;
static int hakios_debug_on = 1;

static void
hakios_debug_read(void)
{
    char arguments[1024] = "";
    size_t size = sizeof(arguments) - 1;
    if (sysctlbyname("kern.bootargs", arguments, &size, NULL, 0) != 0) return;
    arguments[sizeof(arguments) - 1] = 0;
    for (const char *p = arguments; (p = strstr(p, "hakios_debug=")) != NULL; p++) {
        if ((p == arguments || p[-1] == ' ') && p[13] == '0' && (p[14] == 0 || p[14] == ' ')) {
            hakios_debug_on = 0;
            return;
        }
    }
}

static int
hakios_debug_enabled(void)
{
    pthread_once(&hakios_debug_once, hakios_debug_read);
    return hakios_debug_on;
}
#endif
