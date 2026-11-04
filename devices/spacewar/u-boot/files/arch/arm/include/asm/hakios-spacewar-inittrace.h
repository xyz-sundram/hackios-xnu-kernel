/* SPDX-License-Identifier: GPL-2.0+ */
/* Optional Phone (1) pre-console initcall names.
 * Enable with KCFLAGS=-DHAKIOS_SPACEWAR_INIT_TRACE. Normal builds emit neither
 * code nor strings. No heap, stdio, GD, or BSS is used: the font is const data,
 * names are the caller's string literals, and stores go directly to ABL's
 * retained 1080x2400 A8R8G8B8 framebuffer at E1000000 (4320-byte stride).
 *
 * At Y192 the white line names __func__; at Y224 the yellow line names the
 * call/event about to run. Red means that call returned a failure. Names are
 * bounded to 67 characters. The two rows are below the early assembly bands.
 */
#ifndef HAKIOS_SPACEWAR_INITTRACE_H
#define HAKIOS_SPACEWAR_INITTRACE_H

#ifdef HAKIOS_SPACEWAR_INIT_TRACE
#if !defined(__aarch64__)
#error HAKIOS_SPACEWAR_INIT_TRACE is only for the Phone (1) AArch64 build
#endif

#include <video_font_data.h>
/* Reuse U-Boot's GPL font without changing its normal declaration or include
 * guard for the rest of this translation unit. In this private inclusion the
 * identifier macro adds a const qualifier to the array's declaration. */
#ifdef _VIDEO_FONT_TER_16X32_
#define HAKIOS_SPACEWAR_FONT_WAS_INCLUDED
#undef _VIDEO_FONT_TER_16X32_
#endif
#define video_fontdata_16x32 const hakios_spacewar_init_font
#include <video_font_ter16x32.h>
#undef video_fontdata_16x32
#ifndef HAKIOS_SPACEWAR_FONT_WAS_INCLUDED
#undef _VIDEO_FONT_TER_16X32_
#else
#undef HAKIOS_SPACEWAR_FONT_WAS_INCLUDED
#endif

static inline __attribute__((always_inline)) void
hakios_spacewar_inittrace_line(const char *text, unsigned int y, unsigned int color)
{
	volatile unsigned int *row = (volatile unsigned int *)(0xe1000000UL + y * 4320UL);
	/* Volatile stores prevent a synthesized memset/console helper. */
	for (unsigned int r = 0; r < 32; r++) {
		for (unsigned int x = 0; x < 1080; x++)
			row[r * 1080 + x] = 0xff000000U;
	}
	for (unsigned int c = 0; c < 67 && text && text[c]; c++) {
		unsigned int character = (unsigned char)text[c];
		const unsigned char *glyph = &hakios_spacewar_init_font[character * 64];
		for (unsigned int r = 0; r < 32; r++) {
			unsigned int bits = ((unsigned int)glyph[r * 2] << 8) | glyph[r * 2 + 1];
			for (unsigned int x = 0; x < 16; x++)
				row[r * 1080 + 8 + c * 16 + x] = bits & (0x8000U >> x) ? color : 0xff000000U;
		}
	}
}

static inline __attribute__((always_inline)) void
hakios_spacewar_inittrace(const char *caller, const char *call, int failed)
{
	hakios_spacewar_inittrace_line(caller, 192, 0xffffffffU);
	hakios_spacewar_inittrace_line(call, 224, failed ? 0xffff3030U : 0xffffd000U);
	__asm__ volatile("dsb sy" : : : "memory");
	/* Both row offsets and the final address are 64-byte aligned. */
	for (unsigned long p = 0xe1000000UL + 192 * 4320UL;
	     p < 0xe1000000UL + 256 * 4320UL; p += 64)
		__asm__ volatile("dc cvac, %0" : : "r"(p) : "memory");
	__asm__ volatile("dsb sy" : : : "memory");
}
#else
#define hakios_spacewar_inittrace(caller, call, failed) do { } while (0)
#endif
#endif
