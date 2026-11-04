/* Cooperative exclusion between the Spacewar touch thread and UEFI's CPU0.
 * Every mutable line has one writer. Both sides explicitly maintain caches.
 * No acknowledged grant may be revoked while the kernel can still use it. */
#ifndef HAKIOS_SPACEWAR_LEASE_H
#define HAKIOS_SPACEWAR_LEASE_H

#include <stddef.h>
#include <stdint.h>

#define SW_TOUCH_LEASE_MAGIC          0x31455341454c5753ULL /* "SWLEASE1" */
#define SW_TOUCH_LEASE_VERSION        1u
#define SW_TOUCH_LEASE_BYTES          256u
#define SW_TOUCH_LEASE_PAGE_BYTES     16384u
#define SW_TOUCH_LEASE_CAP_EXCLUSIVE  1ULL

struct sw_touch_lease {
	uint64_t magic;
	uint32_t version, size;
	uint64_t capabilities;
	uint8_t reserved[40];
	volatile uint64_t request;       /* kernel: monotonically increasing nonce */
	uint8_t request_pad[56];
	volatile uint64_t grant;         /* loader: nonce acknowledged while parked */
	uint8_t grant_pad[56];
	volatile uint64_t release;       /* kernel: completed/cancelled through nonce */
	uint8_t release_pad[56];
} __attribute__((aligned(64)));

_Static_assert(sizeof(struct sw_touch_lease) == SW_TOUCH_LEASE_BYTES, "touch lease ABI size");
_Static_assert(offsetof(struct sw_touch_lease, request) == 64, "kernel request cache line");
_Static_assert(offsetof(struct sw_touch_lease, grant) == 128, "loader grant cache line");
_Static_assert(offsetof(struct sw_touch_lease, release) == 192, "kernel release cache line");

enum sw_touch_lease_action {
	SW_TOUCH_LEASE_IDLE,
	SW_TOUCH_LEASE_GRANT,
	SW_TOUCH_LEASE_PARK,
	SW_TOUCH_LEASE_STOP
};

/* Pure protocol decision, shared with host tests. A cancellation may precede
 * its late grant: release >= request makes that generation harmless. */
static inline enum sw_touch_lease_action
sw_touch_lease_action(uint64_t request, uint64_t grant, uint64_t release, int stopped)
{
	if (stopped) return SW_TOUCH_LEASE_STOP;
	if (grant != 0 && release < grant) return SW_TOUCH_LEASE_PARK;
	if (request > grant && release < request) return SW_TOUCH_LEASE_GRANT;
	return SW_TOUCH_LEASE_IDLE;
}

#endif
