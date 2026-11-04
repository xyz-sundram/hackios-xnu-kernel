/* Isolated candidate for future integration; no hardware access. */
#ifndef SPACEWAR_FT12_H
#define SPACEWAR_FT12_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { SW_FT12_REPORT_BYTES = 62, SW_FT12_MAX_CONTACTS = 10 };

enum sw_ft12_status {
	SW_FT12_OK = 0,
	SW_FT12_ARGUMENT,
	SW_FT12_LENGTH,
	SW_FT12_FRAME_TYPE,
	SW_FT12_COUNT,
	SW_FT12_ID,
	SW_FT12_DUPLICATE_ID,
	SW_FT12_EVENT,
	SW_FT12_BOUNDS,
	SW_FT12_NO_RECORDS,
};

enum sw_ft12_action { SW_FT12_BEGIN, SW_FT12_MOVE, SW_FT12_END };

struct sw_ft12_limits {
	uint8_t max_contacts;
	/* Inclusive Linux ABS limits, from the vendor driver's DTB parsing. */
	uint16_t max_x, max_y;
};

struct sw_ft12_contact {
	uint16_t x, y;
	uint8_t pressure, area;
};

struct sw_ft12_state {
	uint16_t active_mask;
	struct sw_ft12_contact contacts[SW_FT12_MAX_CONTACTS];
};

struct sw_ft12_event {
	uint8_t id, action, raw_event;
	bool inferred_release;
	struct sw_ft12_contact contact;
};

struct sw_ft12_update {
	uint8_t count, records, header_fingers;
	uint16_t active_mask;
	struct sw_ft12_event events[SW_FT12_MAX_CONTACTS];
};

/*
 * Decode only the vendor's TOUCH_DEFAULT type 0 at register 0x01.
 * The caller passes the 62 returned bytes, without a prepended register byte.
 * Rejected frames leave both state and output untouched. No scaling/rotation.
 */
enum sw_ft12_status sw_ft12_process(const uint8_t *report, size_t length,
    const struct sw_ft12_limits *limits, struct sw_ft12_state *state,
    struct sw_ft12_update *output);

/* Explicit cleanup by a future driver's stop/recovery path, not on bad reads. */
void sw_ft12_release_all(struct sw_ft12_state *state, struct sw_ft12_update *output);

#endif
