/* SPDX-License-Identifier: GPL-2.0-only
 * Explicit Phone (1) install identity, carried in the private XNUP payload.
 * GUIDs use GPT byte order. No default disk/partition is authorized.
 */
#ifndef HAKIOS_SPACEWAR_INSTALL_H
#define HAKIOS_SPACEWAR_INSTALL_H
#include <stdint.h>
#define SW_INSTALL_MAGIC 0x31495753U /* SWI1 */
#define SW_INSTALL_VERSION 1U
#define SW_INSTALL_ROOT 1U
#define SW_INSTALL_DATA 2U
#define SW_INSTALL_CAP_MAGIC 0x3150414357495355ULL
struct sw_install_extent {
    uint8_t type_guid[16], unique_guid[16];
    uint64_t first_lba, blocks;
};
struct sw_install_map {
    uint32_t magic, version, size, flags;
    uint8_t disk_guid[16];
    uint32_t lun, block_size;
    struct sw_install_extent root, data;
    uint64_t root_bytes;
    uint32_t root_crc32, reserved;
};
struct sw_install_cap {
    uint64_t magic;
    uint8_t disk_guid[16], data_guid[16];
    uint64_t first_lba, blocks;
    uint32_t lun, block_size;
};
_Static_assert(sizeof(struct sw_install_map) == 152, "install map ABI");
_Static_assert(sizeof(struct sw_install_cap) == 64, "install capability ABI");
#endif
