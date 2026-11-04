/*
 * Spacewar touch shares CPU0's firmware services through an explicit lease.
 * The loader grants only after leaving firmware calls and masking its IRQs.
 * Each writer owns separate 64-byte cache lines; no peer-owned line is dirtied.
 * Include after spacewar_power_mpidr()/sleep_ms(), before the touch guards.
 */
#include "hakios.spacewar.lease.h"
#define SW_SERVICES_KERNEL_POLICY
#include "hakios.spacewar.services.h"
#undef SW_SERVICES_KERNEL_POLICY
#include <vm/memory_types.h>

extern vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, unsigned int flags);

static uint32_t touch_live;
static volatile struct sw_touch_lease *spacewar_live_page;
static uint64_t spacewar_live_sequence, spacewar_live_pending;
static uint32_t spacewar_live_depth, spacewar_live_cache_bytes = 64;
static bool spacewar_live_ready, spacewar_live_dead, spacewar_live_stopping;

static uint64_t
spacewar_live_ctr(void)
{
	return __builtin_arm_rsr64("CTR_EL0");
}

static bool
spacewar_live_irqs_masked(void)
{
	/* DAIF readback: debug, abort, IRQ and FIQ masks, bits9..6. */
	return (__builtin_arm_rsr64("DAIF") & 0x3c0) == 0x3c0;
}

static void
spacewar_live_barrier(void)
{
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
spacewar_live_clean_line(const volatile void *address)
{
	uintptr_t first = (uintptr_t)address & ~(uintptr_t)63;
	for (uintptr_t p = first; p < first + 64; p += spacewar_live_cache_bytes) {
		__asm__ volatile("dc cvac, %0" : : "r"(p) : "memory");
	}
	spacewar_live_barrier();
}

static void
spacewar_live_invalidate_line(const volatile void *address)
{
	uintptr_t first = (uintptr_t)address & ~(uintptr_t)63;
	for (uintptr_t p = first; p < first + 64; p += spacewar_live_cache_bytes) {
		__asm__ volatile("dc ivac, %0" : : "r"(p) : "memory");
	}
	spacewar_live_barrier();
}

static bool
spacewar_live_profile(void)
{
	uint32_t cpus = 0, bootcpu = 0, buttons = 0, value = 0, gpu = 0, speed = 0, frequency = 0, install = 0, services = 0;
	static const char *const inactive[] = {
		"adsp", "sound", "wifi", "haptic", "modem", "ufs_native",
		"battery", "pmic", "battguard", "bt", "cam", "camera", "mic", "torch", "speaker_probe"
	};

	PE_parse_boot_argn("gpu", &gpu, sizeof(gpu));
	PE_parse_boot_argn("cpufreq", &frequency, sizeof(frequency));
	bool installed = PE_parse_boot_argn("spacewar_install", &install, sizeof(install));
	bool service_profile = PE_parse_boot_argn("spacewar_services", &services, sizeof(services));
	if (service_profile && ((services != 1 && services != 2) || !installed || !sw_services_args_valid(services))) return false;
	if (installed) {
		char data[64] = {0}, forbidden[64];
		if (install != 1 || gpu != 1 || frequency != 2 ||
		    !PE_parse_boot_argn("spacewar_storage", &value, sizeof(value)) || value != 1 ||
		    !PE_parse_boot_argn("ufs", &value, sizeof(value)) || value != 2 ||
		    !PE_parse_boot_argn("ufs_write", &value, sizeof(value)) || value != 1 ||
		    !PE_parse_boot_argn("var_hfs", data, sizeof(data) - 1) || strcmp(data, "ufs_darwin_data") ||
		    PE_parse_boot_argn("root_apfs", forbidden, sizeof(forbidden)) ||
		    PE_parse_boot_argn("var_store", forbidden, sizeof(forbidden)) ||
		    PE_parse_boot_argn("ufs_native", &value, sizeof(value))) return false;
	} else if (PE_parse_boot_argn("ufs", &value, sizeof(value)) && value != 0) return false;
	if (gpu > 1 || (frequency != 0 && frequency != 2) || (frequency && gpu != 1)) return false;
	if (gpu == 1) {
		const char *expected = service_profile ? (services == 2 ? SW_SERVICES_AUDIO_DRIVERS_LIST : SW_SERVICES_DRIVERS_LIST) : installed ?
		    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal ufs virtio_blk" : frequency == 2 ?
		    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal virtio_blk" :
		    "virtio_sound socinfo adreno virtio_input goodix buttons virtio_net thermal virtio_blk";
		if (HAKIOS_DRIVERS_COUNT != (service_profile ? (services == 2 ? SW_SERVICES_AUDIO_DRIVERS_COUNT : SW_SERVICES_DRIVERS_COUNT) : installed ? 11 : frequency == 2 ? 10 : 9) || strcmp(HAKIOS_DRIVERS_LIST, expected) != 0 ||
		    !PE_parse_boot_argn("adreno_speed", &speed, sizeof(speed)) || speed < 1 || speed > 3) return false;
	} else if (HAKIOS_DRIVERS_COUNT != 8 || strcmp(HAKIOS_DRIVERS_LIST,
	    "virtio_sound socinfo virtio_input goodix buttons virtio_net thermal virtio_blk") != 0 ||
	    PE_parse_boot_argn("adreno_speed", &speed, sizeof(speed))) return false;
	if (!HAKIOS_DEVICE_SPACEWAR ||
	    !PE_parse_boot_argn("cpus", &cpus, sizeof(cpus)) || cpus != 7 ||
	    !PE_parse_boot_argn("bootcpu", &bootcpu, sizeof(bootcpu)) || bootcpu != 7 ||
	    !PE_parse_boot_argn("buttons", &buttons, sizeof(buttons)) || buttons != 1 ||
	    PE_parse_boot_argn("cpumask", &value, sizeof(value))) {
		return false;
	}
	for (size_t i = 0; i < sizeof(inactive) / sizeof(inactive[0]); i++) {
		if (service_profile && sw_services_arg_known(inactive[i], services)) continue;
		value = 0;
		if (PE_parse_boot_argn(inactive[i], &value, sizeof(value)) && value != 0) return false;
	}
	return true;
}

static bool
spacewar_live_on_xnu_cpu(void)
{
	uint64_t affinity = spacewar_power_mpidr() & 0x000000ff00ffffffULL;
	return affinity >= 0x100 && affinity <= 0x700 && (affinity & 0xff) == 0;
}

static bool
spacewar_live_header(void)
{
	if (spacewar_live_page == NULL) return false;
	spacewar_live_invalidate_line(spacewar_live_page);
	if (spacewar_live_page->magic != SW_TOUCH_LEASE_MAGIC ||
	    spacewar_live_page->version != SW_TOUCH_LEASE_VERSION ||
	    spacewar_live_page->size != SW_TOUCH_LEASE_BYTES ||
	    spacewar_live_page->capabilities != SW_TOUCH_LEASE_CAP_EXCLUSIVE) return false;
	for (size_t i = 0; i < sizeof(spacewar_live_page->reserved); i++) {
		if (spacewar_live_page->reserved[i] != 0) return false;
	}
	return true;
}

static bool
spacewar_live_cpu_safe(void)
{
	if (touch_live != 1 || !spacewar_live_ready || spacewar_live_dead) return false;
	/* Only stop_acquire() can enter this mode, after its caller stopped all peers. */
	if (spacewar_live_stopping) {
		return (spacewar_power_mpidr() & 0x000000ff00ffffffULL) == 0x700;
	}
	return processor_count == 7 && processor_avail_count == 7 && spacewar_live_on_xnu_cpu();
}

/* Write directly to the live stream so a console printf cannot hide which
 * startup operation returned. The existing debug flag controls every marker.
 */
static void
spacewar_live_wait_trace(const char *step, uint64_t deadline, uint64_t absolute)
{
	char line[224];
	if (!hakios_debug) return;
	snprintf(line, sizeof(line),
	    "spacewar-touch wait: %s cpu=%#llx daif=%#llx raw=%#llx abs=%#llx deadline=%#llx online=%u/%u\n",
	    step, (unsigned long long)spacewar_power_mpidr(),
	    (unsigned long long)__builtin_arm_rsr64("DAIF"),
	    (unsigned long long)__builtin_arm_rsr64("CNTVCT_EL0"),
	    (unsigned long long)absolute, (unsigned long long)deadline, processor_avail_count, processor_count);
	loader_log_line(line);
}

static bool
spacewar_live_init(void)
{
	uint64_t pa = 0, deadline;
	vm_offset_t mapped;

	/* boot-arg touch_live=1: coordinate CPU7/SMP7 physical touch with the USB loader. */
	PE_parse_boot_argn("touch_live", &touch_live, sizeof(touch_live));
	if (!touch_live) return true;             /* existing CPU0 probe keeps its own guard */
	spacewar_live_ready = false;
	spacewar_live_dead = true;
	spacewar_live_page = NULL;
	spacewar_live_depth = 0;
	spacewar_live_stopping = false;
	spacewar_live_pending = spacewar_live_sequence = 0;
	if (touch_live != 1 || !spacewar_live_profile() || !spacewar_live_on_xnu_cpu()) {
		printf("spacewar-touch: live touch requires the approved Spacewar CPU7/SMP7/buttons-on profile\n");
		return false;
	}
	spacewar_live_cache_bytes = 4u << ((spacewar_live_ctr() >> 16) & 15);
	if (spacewar_live_cache_bytes > 64) {
		printf("spacewar-touch: live lease refuses a cache line larger than its ownership separation\n");
		return false;
	}
	/* boot-arg touch_shared_pa=PA: loader-injected, aligned shared lease RAM; never a touch register. */
	if (!PE_parse_boot_argn("touch_shared_pa", &pa, sizeof(pa)) || pa == 0 ||
	    (pa & (SW_TOUCH_LEASE_PAGE_BYTES - 1)) != 0 || pa > UINT64_MAX - SW_TOUCH_LEASE_PAGE_BYTES) {
		printf("spacewar-touch: compatible loader did not provide an aligned touch lease page\n");
		return false;
	}
	/* Match UEFI's normal cacheable RAM and explicitly maintain peer-owned cache lines. */
	printf("spacewar-touch: mapping loader lease pa %#llx\n", (unsigned long long)pa);
	mapped = ml_io_map_unmappable((vm_offset_t)pa, SW_TOUCH_LEASE_PAGE_BYTES, VM_WIMG_DEFAULT);
	if (mapped == 0) {
		printf("spacewar-touch: could not map the loader touch lease page\n");
		return false;
	}
	spacewar_live_page = (volatile struct sw_touch_lease *)mapped;
	printf("spacewar-touch: loader lease mapped; checking ABI\n");
	if (!spacewar_live_header()) {
		printf("spacewar-touch: incompatible loader touch lease ABI/capability; no touch writes\n");
		return false;
	}
	spacewar_live_invalidate_line(&spacewar_live_page->request);
	spacewar_live_invalidate_line(&spacewar_live_page->grant);
	spacewar_live_invalidate_line(&spacewar_live_page->release);
	if (spacewar_live_page->request != 0 || spacewar_live_page->grant != 0 ||
	    spacewar_live_page->release != 0) {
		printf("spacewar-touch: loader touch lease is not fresh for this run; no touch writes\n");
		return false;
	}
	/* GenericARMPE starts the slower cores after 28 seconds by default.
	 * Include that delay and CPU startup time before requiring all seven. */
	spacewar_live_wait_trace("before wait message", 0, 0);
	printf("spacewar-touch: waiting for seven XNU CPUs: registered=%u online=%u, up to60s\n",
	    processor_count, processor_avail_count);
	spacewar_live_wait_trace("before deadline", 0, 0);
	clock_interval_to_deadline(60000, NSEC_PER_MSEC, &deadline);
	spacewar_live_wait_trace("deadline set", deadline, 0);
	uint32_t iteration = 0;
	while (processor_count != 7 || processor_avail_count != 7) {
		bool trace_wait = hakios_debug && (iteration < 3 || !(iteration & (iteration - 1)));
		if (trace_wait) spacewar_live_wait_trace("before clock check", deadline, 0);
		uint64_t now = mach_absolute_time();
		if (trace_wait) spacewar_live_wait_trace("after clock check", deadline, now);
		if (!spacewar_live_on_xnu_cpu() || processor_count > 7 || processor_avail_count > 7 ||
		    now >= deadline) {
			printf("spacewar-touch: live touch waited for seven XNU CPUs without acquiring hardware\n");
			return false;
		}
		if (trace_wait) spacewar_live_wait_trace("before sleep", deadline, now);
		sleep_ms(10);
		if (trace_wait) spacewar_live_wait_trace("after sleep", deadline, 0);
		iteration++;
	}
	spacewar_live_ready = true;
	spacewar_live_dead = false;
	printf("spacewar-touch: live loader lease ready at pa %#llx; CPU0 USB and physical buttons retained\n",
	    (unsigned long long)pa);
	return true;
}

static bool
spacewar_live_held(void)
{
	if (!touch_live) return true;             /* legacy CPU0 owns firmware execution */
	if (!spacewar_live_cpu_safe() || spacewar_live_depth == 0 || !spacewar_live_header()) return false;
	spacewar_live_invalidate_line(&spacewar_live_page->grant);
	return spacewar_live_page->grant == spacewar_live_pending &&
	       spacewar_live_page->request == spacewar_live_pending &&
	       spacewar_live_page->release < spacewar_live_pending;
}

static void
spacewar_live_release(void)
{
	if (!touch_live || spacewar_live_page == NULL || spacewar_live_depth == 0) return;
	if (--spacewar_live_depth != 0) return;
	/* Release even after a CPU/profile failure so the parked loader can continue. */
	spacewar_live_barrier();
	spacewar_live_page->release = spacewar_live_pending;
	spacewar_live_clean_line(&spacewar_live_page->release);
	spacewar_live_pending = 0;
	spacewar_live_stopping = false;
}

static bool
spacewar_live_acquire(void)
{
	uint64_t deadline, quick_deadline;

	if (!touch_live) return true;
	if (!spacewar_live_cpu_safe() || !spacewar_live_header()) return false;
	if (spacewar_live_depth != 0) {
		if (!spacewar_live_held() || spacewar_live_depth == UINT32_MAX) return false;
		spacewar_live_depth++;
		return true;
	}
	if (spacewar_live_sequence == UINT64_MAX ||
	    spacewar_live_page->request != spacewar_live_sequence ||
	    spacewar_live_page->release != spacewar_live_sequence) {
		spacewar_live_dead = true;
		return false;
	}
	spacewar_live_pending = ++spacewar_live_sequence;
	spacewar_live_page->request = spacewar_live_pending;
	spacewar_live_clean_line(&spacewar_live_page->request);
	clock_interval_to_deadline(2, NSEC_PER_MSEC, &quick_deadline);
	clock_interval_to_deadline(5000, NSEC_PER_MSEC, &deadline);
	for (;;) {
		spacewar_live_invalidate_line(&spacewar_live_page->grant);
		if (!spacewar_live_cpu_safe() || !spacewar_live_header()) break;
		if (spacewar_live_page->grant == spacewar_live_pending &&
		    spacewar_live_page->request == spacewar_live_pending &&
		    spacewar_live_page->release < spacewar_live_pending) {
			spacewar_live_depth = 1;
			return true;
		}
		if (spacewar_live_page->grant > spacewar_live_pending ||
		    spacewar_live_page->request != spacewar_live_pending ||
		    spacewar_live_page->release >= spacewar_live_pending || mach_absolute_time() >= deadline) break;
		/* Quick leases take microseconds; yield during a slow/in-flight USB request. */
		if (mach_absolute_time() < quick_deadline) sleep_us(10);
		else sleep_ms(1);
	}
	/* Cancel this nonce. A late grant can never authorize this or the next transaction. */
	spacewar_live_page->release = spacewar_live_pending;
	spacewar_live_clean_line(&spacewar_live_page->release);
	spacewar_live_pending = 0;
	spacewar_live_dead = true;
	printf("spacewar-touch: loader touch lease unavailable or ownership changed; request cancelled, no touch writes\n");
	return false;
}

/*
 * Shutdown only: the caller must prove every other XNU CPU has stopped and
 * must keep scheduling/interrupts disabled. A stopped thread may have left a
 * complete grant (and nested depth) behind; boot CPU7 takes that grant over
 * solely to put the owned GPIOs into their return state. Normal runtime
 * acquire/held calls cannot enter the reduced-CPU mode themselves.
 */
static bool
spacewar_live_stop_acquire(void)
{
	if (!touch_live) return true;
	if (touch_live != 1 || !spacewar_live_ready || spacewar_live_dead || spacewar_live_stopping ||
	    (spacewar_power_mpidr() & 0x000000ff00ffffffULL) != 0x700 ||
	    !spacewar_live_irqs_masked() || !spacewar_live_profile() || !spacewar_live_header()) return false;
	spacewar_live_stopping = true;
	if (spacewar_live_depth != 0) {
		if (!spacewar_live_held()) {
			spacewar_live_stopping = false;
			return false;
		}
		/* The old owners are stopped; one shutdown release completes all nesting. */
		spacewar_live_depth = 1;
		return true;
	}
	if (!spacewar_live_acquire()) {
		spacewar_live_stopping = false;
		return false;
	}
	return true;
}
