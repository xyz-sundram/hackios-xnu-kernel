/*
 * Groundwork only. Pinned vendor layout:
 * NothingOSS/android_kernel_msm-5.4_nothing_sm7325 @
 * 1d557de4d904a76c0b679c746cc77cf9ae89b6c6
 * drivers/input/touchscreen/focaltech_touch/focaltech_core.{c,h}
 * See docs/spacewar-touch-reports.md for deliberate stricter frame checks.
 */
#include "hakios.spacewar.ft12.h"
#include <libkern/libkern.h>

struct raw_contact {
	uint8_t id, flag;
	struct sw_ft12_contact contact;
};

static void
emit(struct sw_ft12_update *update, uint8_t id, enum sw_ft12_action action,
    uint8_t raw_event, bool inferred, struct sw_ft12_contact contact)
{
	struct sw_ft12_event *event = &update->events[update->count++];
	event->id = id;
	event->action = (uint8_t)action;
	event->raw_event = raw_event;
	event->inferred_release = inferred;
	event->contact = contact;
}

enum sw_ft12_status
sw_ft12_process(const uint8_t *report, size_t length,
    const struct sw_ft12_limits *limits, struct sw_ft12_state *state,
    struct sw_ft12_update *output)
{
	struct raw_contact parsed[SW_FT12_MAX_CONTACTS];
	struct sw_ft12_state next;
	struct sw_ft12_update update;
	uint16_t seen = 0, active = 0;
	unsigned records = 0, fingers = 0;

	if (report == NULL || limits == NULL || state == NULL || output == NULL ||
	    limits->max_contacts == 0 || limits->max_contacts > SW_FT12_MAX_CONTACTS ||
	    limits->max_x == 0 || limits->max_y == 0 ||
	    limits->max_x > 4095 || limits->max_y > 4095 ||
	    (state->active_mask >> limits->max_contacts) != 0) {
		return SW_FT12_ARGUMENT;
	}
	if (length != SW_FT12_REPORT_BYTES) {
		return SW_FT12_LENGTH;
	}
	/* Header[0] is not interpreted by the vendor's default report parser. */
	if ((report[1] >> 4) != 0) {
		return SW_FT12_FRAME_TYPE;
	}
	if ((report[1] & 0x0f) > limits->max_contacts) {
		return SW_FT12_COUNT;
	}
	for (unsigned slot = 0; slot < limits->max_contacts; slot++) {
		const uint8_t *bytes = report + 2 + 6 * slot;
		uint8_t id = bytes[2] >> 4;
		uint8_t flag = bytes[0] >> 6;
		struct raw_contact *contact;

		/* Official default-mode sentinel: every ID >= FTS_MAX_ID (10). */
		if (id >= SW_FT12_MAX_CONTACTS) {
			break;
		}
		if (id >= limits->max_contacts) {
			return SW_FT12_ID;
		}
		if (seen & (1u << id)) {
			return SW_FT12_DUPLICATE_ID;
		}
		if (flag > 2) {
			return SW_FT12_EVENT;
		}
		seen |= (uint16_t)(1u << id);
		contact = &parsed[records++];
		contact->id = id;
		contact->flag = flag;
		contact->contact.x = (uint16_t)(((bytes[0] & 0x0f) << 8) | bytes[1]);
		contact->contact.y = (uint16_t)(((bytes[2] & 0x0f) << 8) | bytes[3]);
		contact->contact.pressure = bytes[4] ? bytes[4] : 0x3f;
		contact->contact.area = bytes[5] ? bytes[5] : 0x09;
		if (flag != 1) {
			if (contact->contact.x > limits->max_x || contact->contact.y > limits->max_y) {
				return SW_FT12_BOUNDS;
			}
			fingers++;
			active |= (uint16_t)(1u << id);
		}
	}
	if (records == 0) {
		/* Vendor rejects a default frame with no slot records, even count 0. */
		return SW_FT12_NO_RECORDS;
	}
	/* Reject incomplete/contradictory snapshots rather than release real contacts. */
	if (fingers != (report[1] & 0x0f)) {
		return SW_FT12_COUNT;
	}

	/* Nothing mutates until the complete frame is valid. One event per ID max. */
	next = *state;
	next.active_mask = active;
	memset(&update, 0, sizeof(update));
	update.records = (uint8_t)records;
	update.header_fingers = (uint8_t)fingers;
	update.active_mask = active;
	for (unsigned i = 0; i < records; i++) {
		const struct raw_contact *contact = &parsed[i];
		uint8_t id = contact->id;
		bool was_active = (state->active_mask & (1u << id)) != 0;

		if (contact->flag == 1) {
			if (was_active) {
				/* Vendor does not report UP coordinates/pressure/area. */
				emit(&update, id, SW_FT12_END, 1, false, state->contacts[id]);
			}
		} else {
			next.contacts[id] = contact->contact;
			emit(&update, id, was_active ? SW_FT12_MOVE : SW_FT12_BEGIN,
			    contact->flag, false, contact->contact);
		}
	}
	for (unsigned id = 0; id < limits->max_contacts; id++) {
		if ((state->active_mask & (1u << id)) && !(seen & (1u << id))) {
			emit(&update, (uint8_t)id, SW_FT12_END, 1, true, state->contacts[id]);
		}
	}
	*state = next;
	*output = update;
	return SW_FT12_OK;
}

void
sw_ft12_release_all(struct sw_ft12_state *state, struct sw_ft12_update *output)
{
	struct sw_ft12_update update;

	if (state == NULL || output == NULL) {
		return;
	}
	memset(&update, 0, sizeof(update));
	for (unsigned id = 0; id < SW_FT12_MAX_CONTACTS; id++) {
		if (state->active_mask & (1u << id)) {
			emit(&update, (uint8_t)id, SW_FT12_END, 1, true, state->contacts[id]);
		}
	}
	state->active_mask = 0;
	*output = update;
}
