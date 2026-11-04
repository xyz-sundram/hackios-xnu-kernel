/* SPDX-License-Identifier: BSD-2-Clause
 * Small wire-format rules shared by the station and its host regression test.
 */
#ifndef LX_STATION_SECURITY_H
#define LX_STATION_SECURITY_H

static inline bool
lx_open_ap(uint16_t capability, bool has_rsn, bool has_wpa)
{
	/* A missing password is never permission to downgrade a protected BSS. */
	return !(capability & 0x10) && !has_rsn && !has_wpa;
}

static inline uint16_t
lx_assoc_capability(uint16_t ap_capability, bool protected_join)
{
	return (uint16_t)(1u | (protected_join ? 0x10u : 0u) | (ap_capability & 0x0520u));
}

static inline unsigned
lx_assoc_rsn(uint8_t rsn[22], unsigned group_cipher)
{
	static const uint8_t wpa2_ccmp[22] = {
		48, 20, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 4,
		1, 0, 0, 0x0f, 0xac, 2, 0, 0
	};
	if (group_cipher == 0) {
		memset(rsn, 0, 22);
		return 0;
	}
	memcpy(rsn, wpa2_ccmp, 22);
	rsn[7] = (uint8_t)group_cipher;
	return 22;
}

#endif
