/* USB power granted by the resident loader's configured gadget. CPU0 is the
 * sole writer. This occupies an independent line in the existing shared page;
 * it changes neither the touch lease nor the backlight ABI. */
#ifndef HAKIOS_SPACEWAR_POWER_H
#define HAKIOS_SPACEWAR_POWER_H
#include <stdint.h>
#define SW_USB_POWER_OFFSET 8192u
#define SW_USB_POWER_MAGIC 0x3152575055425753ULL
#define SW_USB_POWER_VERSION 1u
#define SW_USB_POWER_BYTES 64u
#define SW_USB_POWER_UA 500000u
struct sw_usb_power {
    uint64_t magic;
    uint32_t version, size;
    volatile uint32_t sequence;
    uint32_t configured, max_current_ua, generation;
    uint64_t heartbeat;
    uint64_t reserved[3];
} __attribute__((aligned(64)));
_Static_assert(sizeof(struct sw_usb_power) == SW_USB_POWER_BYTES, "USB power ABI");
_Static_assert(SW_USB_POWER_OFFSET + SW_USB_POWER_BYTES <= 16384u, "USB power fits shared page");
static inline int
sw_usb_power_valid(const struct sw_usb_power *p)
{
    return p->magic == SW_USB_POWER_MAGIC && p->version == SW_USB_POWER_VERSION &&
        p->size == sizeof(*p) && p->sequence != 0 && !(p->sequence & 1) &&
        p->configured <= 1 && p->max_current_ua == SW_USB_POWER_UA &&
        p->heartbeat != 0 && p->reserved[0] == 0 && p->reserved[1] == 0 && p->reserved[2] == 0;
}
/* SDP needs the actual configured USB budget. Dedicated/PD sources retain
 * their firmware-negotiated limits; they must not inherit a guessed USB vote. */
static inline uint32_t
sw_usb_input_vote(const struct sw_usb_power *p, int fresh, uint32_t online, uint32_t adapter)
{
    return fresh && sw_usb_power_valid(p) && p->configured && online == 1 && adapter == 1 ?
        p->max_current_ua : 0;
}
/* Do not override suspension, an unknown value, a firmware reduction below
 * USB's unconfigured 100mA, or an already sufficient input limit. */
static inline uint32_t
sw_usb_limit_raise(const struct sw_usb_power *p, int fresh, uint32_t online,
    uint32_t adapter, uint32_t current_limit)
{
    uint32_t vote = sw_usb_input_vote(p, fresh, online, adapter);
    return current_limit >= 100000u && current_limit < vote ? vote : 0;
}
#endif
