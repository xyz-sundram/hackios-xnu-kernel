/*
 * spacewar: derived from the live Spacewar DTB. FB base overlaps redwood, so the run payload must
 * include platform=spacewar; loader.c uses that to choose this profile after it receives the payload.
 * Remaining SoC window values follow redwood's SM7325 profile and need hardware validation.
 */
static const struct platform platform_spacewar = {
	.name = "spacewar",
	.io_base = 0x00400000, .io_size = 0x3FC00000,
	.gic_dist = 0x17a00000, .gic_redist = 0x17a60000,
	.uart = 0,                                      /* phones: no PL011 */
	.log_other_cpu = 1,
	.cores = 8, .core_shift = 8,
	.small_cores = 0x0f,
	.fb_base = 0xE1000000,
};
