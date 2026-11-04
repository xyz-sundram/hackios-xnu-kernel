/* SPDX-License-Identifier: GPL-2.0-only
 * Explicit Nothing Phone (1) service probe profile shared by loader and XNU.
 * These are bounded driver modes, never register addresses or charger controls.
 */
#ifndef HAKIOS_SPACEWAR_SERVICES_H
#define HAKIOS_SPACEWAR_SERVICES_H
#include <stdint.h>
#include <stddef.h>
#define SW_SERVICES_DRIVERS_COUNT 18
#define SW_SERVICES_AUDIO_DRIVERS_COUNT 19
#define SW_SERVICES_AUDIO_DRIVERS_LIST \
    "adsp speaker sound virtio_sound socinfo adreno haptic virtio_input goodix buttons qmi virtio_net battery cpufreq thermal pmic ufs virtio_blk wifi"
#define SW_SERVICES_DRIVERS_LIST \
    "adsp speaker virtio_sound socinfo adreno haptic virtio_input goodix buttons qmi virtio_net battery cpufreq thermal pmic ufs virtio_blk wifi"
struct sw_services_bound { const char *name; uint32_t minimum, maximum, profile; };
static const struct sw_services_bound sw_services_bounds[] = {
    { "adsp", 0, 1, 1 }, { "adsp_step", 1, 11, 1 }, { "adsp_pause", 0, 5000, 1 },
    { "adsp_new", 0, 12, 1 }, { "adsp_wait", 0, 30, 1 }, { "adsp_listen", 0, 10, 1 },
    { "adsp_keep", 0, 1, 1 }, { "battmgr", 0, 2, 1 },
    { "haptic", 0, 5, 1 }, { "pmic", 0, 1, 1 }, { "pmic_list", 0, 1, 1 },
    { "speaker_probe", 0, 2, 1 }, { "sound", 0, 4, 2 }, { "sound_cont", 0, 1, 2 },
    { "wifi", 0, 1, 1 }, { "wifi_step", 1, 16, 1 }, { "wifi_pause", 0, 5000, 1 },
    { "wifi_new", 0, 17, 1 }, { "wifi_watch", 0, 60, 1 }, { "wifi_lx", 1, 2, 1 },
    { "wifi_smmu", 0, 2, 1 }, { "wifi_loud", 0, 1, 1 }, { "wifi_file", 0, 1, 1 }
};
#define SW_SERVICES_BOUND_COUNT (sizeof(sw_services_bounds) / sizeof(sw_services_bounds[0]))
#ifdef SW_SERVICES_KERNEL_POLICY
/* Included only after PE_parse_boot_argn and strcmp have been declared. */
static inline bool
sw_services_args_valid(uint32_t profile)
{
    if (profile != 1 && profile != 2) return false;
    static const char *const inactive[] = {
        "battery", "battguard", "mic", "modem", "bt", "cam", "camera", "torch"
    };
    for (size_t i = 0; i < sizeof(inactive) / sizeof(inactive[0]); i++) {
        uint32_t value = 0;
        if (PE_parse_boot_argn(inactive[i], &value, sizeof(value)) && value != 0) return false;
    }
    for (size_t i = 0; i < SW_SERVICES_BOUND_COUNT; i++) {
        uint32_t value = 0;
        if (!PE_parse_boot_argn(sw_services_bounds[i].name, &value, sizeof(value))) continue;
        if (sw_services_bounds[i].profile > profile) { if (value != 0) return false; }
        else if (value < sw_services_bounds[i].minimum || value > sw_services_bounds[i].maximum) return false;
    }
    return true;
}
static inline bool
sw_services_arg_known(const char *name, uint32_t profile)
{
    for (size_t i = 0; i < SW_SERVICES_BOUND_COUNT; i++)
        if (sw_services_bounds[i].profile <= profile && !strcmp(name, sw_services_bounds[i].name)) return true;
    return false;
}
#endif
#endif
