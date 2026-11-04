/* Spacewar CPU-only performance mode. EPSS selects voltage from firmware's
 * own LUT. Domain0 (including the USB loader's CPU0) and DDR stay untouched. */
#define SW_SERVICES_KERNEL_POLICY
#include "hakios.spacewar.services.h"
#undef SW_SERVICES_KERNEL_POLICY
static struct {
	vm_offset_t base;
	uint32_t saved, requested;
	bool changed;
} spacewar_cpu_perf[2];

static bool
spacewar_cpu_perf_start(void)
{
	static const struct { const char *name; uint32_t value; } required[] = {
		{ "cpufreq", 2 }, { "bootcpu", 7 }, { "cpus", 7 }, { "buttons", 1 },
		{ "gpu", 1 }, { "touch_live", 1 }
	};
	if (!HAKIOS_DEVICE_SPACEWAR) return false;
	uint32_t install = 0, services = 0;
	bool service_profile = PE_parse_boot_argn("spacewar_services", &services, sizeof(services));
	if (service_profile && ((services != 1 && services != 2) || !sw_services_args_valid(services))) return false;
	if (PE_parse_boot_argn("spacewar_install", &install, sizeof(install))) {
		uint32_t value = 0;
		char volume[32] = { 0 }, forbidden[64];
		if (install != 1 || HAKIOS_DRIVERS_COUNT != (service_profile ? (services == 2 ? SW_SERVICES_AUDIO_DRIVERS_COUNT : SW_SERVICES_DRIVERS_COUNT) : 11) ||
		    strcmp(HAKIOS_DRIVERS_LIST, service_profile ? (services == 2 ? SW_SERVICES_AUDIO_DRIVERS_LIST : SW_SERVICES_DRIVERS_LIST) :
		    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal ufs virtio_blk") != 0 ||
		    !PE_parse_boot_argn("spacewar_storage", &value, sizeof(value)) || value != 1 ||
		    !PE_parse_boot_argn("ufs", &value, sizeof(value)) || value != 2 ||
		    !PE_parse_boot_argn("ufs_write", &value, sizeof(value)) || value != 1 ||
		    !PE_parse_boot_argn("var_hfs", volume, sizeof(volume)) ||
		    !memchr(volume, 0, sizeof(volume)) || strcmp(volume, "ufs_darwin_data") != 0 ||
		    PE_parse_boot_argn("var_store", forbidden, sizeof(forbidden)) ||
		    PE_parse_boot_argn("root_apfs", forbidden, sizeof(forbidden)) ||
		    PE_parse_boot_argn("ufs_native", forbidden, sizeof(forbidden))) return false;
	} else if (service_profile || HAKIOS_DRIVERS_COUNT != 10 || strcmp(HAKIOS_DRIVERS_LIST,
	    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal virtio_blk") != 0) return false;
	uint32_t mask = 0;
	if (PE_parse_boot_argn("cpumask", &mask, sizeof(mask))) return false;
	uint32_t gpu_speed = 0;
	if (!PE_parse_boot_argn("adreno_speed", &gpu_speed, sizeof(gpu_speed)) || gpu_speed < 1 || gpu_speed > 3) return false;
	for (unsigned i = 0; i < sizeof(required) / sizeof(required[0]); i++) {
		uint32_t value = 0;
		if (!PE_parse_boot_argn(required[i].name, &value, sizeof(value)) || value != required[i].value) return false;
	}
	for (unsigned index = 0; index < 2; index++) {
		unsigned domain = index + 1;
		vm_offset_t base = ml_io_map(EPSS_PHYS + domain * EPSS_DOMAIN_SIZE, EPSS_DOMAIN_SIZE);
		if (!base) return false;
		uint32_t was = *(volatile uint32_t *)(base + EPSS_PERF_STATE);
		uint32_t khz[EPSS_TABLE_ROWS] = { 0 }, count = 0, target = 0;
		for (unsigned row = 0; row < EPSS_TABLE_ROWS; row++) {
			uint32_t word = *(volatile uint32_t *)(base + EPSS_FREQ_TABLE + 4 * row);
			uint32_t rate = (word >> 30) ? XO_KHZ * (word & 255) : ALTERNATE_KHZ;
			if (row && rate <= khz[row - 1]) break;
			if (rate < 300000 || rate > 3000000) return false;
			khz[count++] = rate;
			if (rate <= 1920000) target = row;
		}
		if (count < 2 || was >= count || khz[target] > 1920000) return false;
		/* Never lower a request the firmware already left higher. */
		if (target <= was) {
			tell("cpu-perf: domain%u retains row%u (%u MHz), no write\n", domain, was, khz[was] / 1000);
			continue;
		}
		spacewar_cpu_perf[index].base = base;
		spacewar_cpu_perf[index].saved = was;
		spacewar_cpu_perf[index].requested = target;
		spacewar_cpu_perf[index].changed = true;
		*(volatile uint32_t *)(base + EPSS_PERF_STATE) = target;
		__asm__ volatile("dsb sy" ::: "memory");
		delay_for_interval(20, NSEC_PER_MSEC);
		if (*(volatile uint32_t *)(base + EPSS_PERF_STATE) != target) return false;
		tell("cpu-perf: domain%u row%u (%u MHz) -> row%u (%u MHz); firmware LUT, cap1920MHz\n",
		    domain, was, khz[was] / 1000, target, khz[target] / 1000);
	}
	return true;
}

void
hakios_cpufreq_quiesce(void)
{
	for (unsigned index = 0; index < 2; index++) {
		if (!spacewar_cpu_perf[index].changed) continue;
		volatile uint32_t *state = (volatile uint32_t *)(spacewar_cpu_perf[index].base + EPSS_PERF_STATE);
		/* Do not overwrite a request changed by another owner. */
		if (*state == spacewar_cpu_perf[index].requested) {
			*state = spacewar_cpu_perf[index].saved;
			__asm__ volatile("dsb sy" ::: "memory");
			tell("cpu-perf: domain%u restored row%u before loader return\n", index + 1, spacewar_cpu_perf[index].saved);
		}
		spacewar_cpu_perf[index].changed = false;
	}
}
