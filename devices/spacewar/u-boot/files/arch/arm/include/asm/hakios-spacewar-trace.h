/* SPDX-License-Identifier: GPL-2.0+ */
/* Phone (1), retained ABL framebuffer: E1000000, 1080 pixels, 4320-byte stride.
 * Optional pre-console tracing: KAFLAGS=-DHAKIOS_SPACEWAR_EARLY_TRACE.
 * No stack, calls, literal loads or absolute symbol relocations. Only x10-x13
 * are clobbered; x0-x3 (ABL parameters), LR, SP, GD and NZCV are preserved.
 * Each marker paints 16 complete rows, then cleans every 64-byte cache line
 * to PoC and executes DSB SY. Without the define the macro emits no bytes.
 *
 * The bands accumulate from the top; the lowest visible band is the last
 * reached checkpoint (a later display driver can overwrite them):
 *   band 0 / rows   0-15:  red      reset, before save_boot_params
 *   band 1 / rows  16-31:  orange   save_boot_params_ret
 *   band 2 / rows  32-47:  yellow   pie_fixup_done
 *   band 3 / rows  48-63:  green    before lowlevel_init
 *   band 4 / rows  64-79:  cyan     after lowlevel_init
 *   band 5 / rows  80-95:  blue     _main entry
 *   band 6 / rows  96-111: magenta  before board_init_f
 *   band 7 / rows 112-127: white    after board_init_f
 *   band 8 / rows 128-143: grey     relocation_return
 */
#ifndef HAKIOS_SPACEWAR_TRACE_H
#define HAKIOS_SPACEWAR_TRACE_H

.macro hakios_spacewar_trace band, color
#ifdef HAKIOS_SPACEWAR_EARLY_TRACE
	mrs	x13, nzcv
	movz	x10, #((0xe1000000 + (\band) * 16 * 4320) & 0xffff)
	movk	x10, #(((0xe1000000 + (\band) * 16 * 4320) >> 16) & 0xffff), lsl #16
	movz	w11, #((\color) & 0xffff)
	movk	w11, #(((\color) >> 16) & 0xffff), lsl #16
	mov	x12, #(1080 * 16)
.Lhakios_trace_fill_\@:
	str	w11, [x10], #4
	subs	x12, x12, #1
	b.ne	.Lhakios_trace_fill_\@
	dsb	sy
	movz	x12, #((0xe1000000 + (\band) * 16 * 4320) & 0xffff)
	movk	x12, #(((0xe1000000 + (\band) * 16 * 4320) >> 16) & 0xffff), lsl #16
.Lhakios_trace_clean_\@:
	dc	cvac, x12
	add	x12, x12, #64
	cmp	x12, x10
	b.lo	.Lhakios_trace_clean_\@
	dsb	sy
	msr	nzcv, x13
#endif
.endm
#endif
