/* Phone (1) firmware backlight mailbox within the existing touch shared page.
 * Header and completion lines belong to CPU0; requests belong to XNU.
 * Offsets deliberately leave the v1 touch lease ABI unchanged.
 */
#ifndef HAKIOS_SPACEWAR_BACKLIGHT_H
#define HAKIOS_SPACEWAR_BACKLIGHT_H
#include <stddef.h>
#include <stdint.h>
#define SW_BACKLIGHT_OFFSET 4096u
#define SW_BACKLIGHT_MAGIC 0x31544c424c5753ULL
#define SW_BACKLIGHT_VERSION 1u
#define SW_BACKLIGHT_BYTES 192u
#define SW_BACKLIGHT_CAP_FIRMWARE 1u
/* Nominal SDR range from Nothing's published 500-nit normal brightness.
 * This is a relative mapping, not measured luminance calibration or HDR. */
#define SW_BACKLIGHT_MAX_MILLINITS 500000u
struct sw_backlight {
    uint64_t magic;
    uint32_t version, size, capabilities, initial_percent, max_millinits, reserved;
    uint8_t header_pad[32];
    volatile uint64_t request;
    uint32_t percent, request_reserved;
    uint8_t request_pad[48];
    volatile uint64_t answered;
    uint64_t status;
    uint32_t applied_percent, operations;
    uint8_t answer_pad[40];
} __attribute__((aligned(64)));
#ifdef __cplusplus
static_assert(sizeof(sw_backlight) == SW_BACKLIGHT_BYTES, "backlight ABI");
static_assert(offsetof(sw_backlight, request) == 64 && offsetof(sw_backlight, answered) == 128, "backlight writers");
#else
_Static_assert(sizeof(struct sw_backlight) == SW_BACKLIGHT_BYTES, "backlight ABI");
_Static_assert(offsetof(struct sw_backlight, request) == 64 && offsetof(struct sw_backlight, answered) == 128, "backlight writers");
#endif
#endif
