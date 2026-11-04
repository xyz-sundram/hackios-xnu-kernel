/* SPDX-License-Identifier: GPL-2.0-only
 * Phone (1) firmware aliases. Board selection is made from this phone's private
 * firmware inventory, never by falling back to another manufacturer's files.
 * Kept independent of XNU headers for the host regression test.
 */
#ifndef HAKIOS_SPACEWAR_WIFI_H
#define HAKIOS_SPACEWAR_WIFI_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SPACEWAR_WIFI_BOARD "wifi-board.elf"
#define SPACEWAR_WIFI_REGDB "wifi-regdb.bin"
#define SPACEWAR_WIFI_MAC   "wifi-mac"

/* SMP2P survives a return to the loader. A change to the separate "wlan"
 * entry must not revalidate the previous slave-kernel READY/STOP_ACK word.
 * Accept a new running state only after that word changes, a restart-done
 * toggle identifies a new generation, or there was no previous kernel word. */
struct spacewar_wifi_start_state {
	uint32_t previous_state, previous_flags;
	bool fresh;
};

static inline struct spacewar_wifi_start_state
spacewar_wifi_start_begin(bool had_state, uint32_t state, uint32_t flags)
{
	return (struct spacewar_wifi_start_state) { state, flags, !had_state };
}

static inline bool
spacewar_wifi_start_observe(struct spacewar_wifi_start_state *start, uint32_t state, uint32_t flags)
{
	/* slave-kernel: fatal bit0, error-ready bit1, stop-ack bit3;
	 * SMP2P flags: restart-done bit0 (toggled, rather than always set). */
	start->fresh |= state != start->previous_state || ((flags ^ start->previous_flags) & 1u) != 0;
	start->previous_state = state;
	start->previous_flags = flags;
	return start->fresh && (state & 2u) != 0 && (state & 9u) == 0;
}

static inline const char *
spacewar_wifi_alias(bool spacewar, const char *base)
{
	if (strcmp(base, "board.bin") == 0)
		return spacewar ? SPACEWAR_WIFI_BOARD : "bd_m20gl.elf";
	if (strcmp(base, "regdb.bin") == 0)
		return spacewar ? SPACEWAR_WIFI_REGDB : "regdb_xiaomi.bin";
	return NULL;
}

static inline int
spacewar_wifi_hex(uint8_t c)
{
	return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
	    c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

/* Only an exact six-byte, unicast station address, with optional text newline. */
static inline bool
spacewar_wifi_mac(const uint8_t *text, size_t size, uint8_t address[6])
{
	uint8_t parsed[6];
	if (!text || !address) return false;
	if (size == 19 && text[17] == '\r' && text[18] == '\n') size = 17;
	else if (size == 18 && text[17] == '\n') size = 17;
	if (size != 17) return false;
	for (unsigned i = 0; i < 6; i++) {
		int a = spacewar_wifi_hex(text[3 * i]), b = spacewar_wifi_hex(text[3 * i + 1]);
		if (a < 0 || b < 0 || (i < 5 && text[3 * i + 2] != ':')) return false;
		parsed[i] = (uint8_t)(a * 16 + b);
	}
	if ((parsed[0] & 1) || !(parsed[0] | parsed[1] | parsed[2] | parsed[3] | parsed[4] | parsed[5])) return false;
	memcpy(address, parsed, 6);
	return true;
}

static inline uint32_t
spacewar_wifi_le32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* The selected Phone (1) BDF is the ARM ELF container shipped in qca6750/. */
static inline bool
spacewar_wifi_board(const uint8_t *p, uint64_t size)
{
	if (!p || size < 84 || size > 1024 * 1024 || memcmp(p, "\177ELF\1\1\1", 7) ||
	    p[16] != 2 || p[17] || p[18] != 40 || p[19] || p[40] != 52 || p[41] ||
	    p[42] != 32 || p[43] || p[45] || !p[44] || p[44] > 16) return false;
	uint32_t at = spacewar_wifi_le32(p + 28), count = p[44], loads = 0;
	if (at < 52 || at > size || 32 * count > size - at) return false;
	for (unsigned i = 0; i < count; i++) {
		const uint8_t *s = p + at + i * 32;
		if (spacewar_wifi_le32(s) != 1) continue;
		uint32_t offset = spacewar_wifi_le32(s + 4), bytes = spacewar_wifi_le32(s + 16);
		if (!bytes || offset > size || bytes > size - offset || bytes > spacewar_wifi_le32(s + 20)) return false;
		loads++;
	}
	return loads != 0;
}
#endif
