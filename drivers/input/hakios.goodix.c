/*
 * The touch screen of the POCO X5 Pro (redwood): either a Goodix GT9916 ("Berlin-D") or a
 * FocalTech FT3680 on the SPI bus of the SoC's serial engine 13, as a source of touches for
 * /dev/hakios-touch (sys/hakios.input.h). Only with boot-arg touchscreen=1: every address
 * below belongs to this phone.
 *
 * There is no clock, pin, regulator or SPI framework in this kernel, so the driver does all of
 * it itself, in the order the vendor's Linux drivers do:
 *   power    the chip's I/O supply is LDO2 of the PM8350C, switched through RPMh (one request
 *            written into the "always on" processor's command slots); the analog supply is
 *            behind a GPIO
 *   clocks   the serial engine's three clocks, by vote in the global clock controller
 *   pins     four GPIOs to the serial engine, plus reset, interrupt and the analog supply switch
 *   SPI      the serial engine ("GENI") in FIFO mode, polled; or, if the engine cannot be used,
 *            the same four pins driven by hand
 *   panel    GPIO96=1 and GPIO68=0 identify the FocalTech variant; everything else is Goodix,
 *            the same discriminator the vendor sm8350-kernel-OKX uses
 *   chip     Goodix: find where it keeps the touch report, then read it when the IRQ line signals;
 *            FocalTech: read the report at register 0x01 with FTS 5.x SPI framing (cmd 0xC1 for
 *            read, 0xC0 for write, two-byte CRC-16/Kermit)
 * No interrupt is used: a thread looks at the interrupt line 125 times a second (boot-arg
 * touch_poll_us=N: every N microseconds instead of 8000). With touch_lat=1 a line for every report
 * read ("tl: c before US seen US read US": the look before, the look that found the line low, the
 * report read), for measuring how long a touch takes to show (tools/latency.py).
 *
 * Register layouts: Linux drivers/spi/spi-geni-qcom.c, drivers/soc/qcom/{qcom-geni-se,rpmh-rsc,
 * cmd-db}.c, drivers/clk/qcom/gcc-sc7280.c, drivers/pinctrl/qcom/pinctrl-sc7280.c,
 * drivers/input/touchscreen/{goodix_berlin_*,focaltech_touch/focaltech_core}.c, and the phone's
 * own device tree.
 */
#include <kern/clock.h>
#include <kern/locks.h>
#include <kern/processor.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <libkern/crypto/sha2.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/hakios.input.h>
#include <sys/systm.h>
#include "hakios.drivers.h"
#ifndef HAKIOS_DEVICE_SPACEWAR
#error Regenerate hakios.drivers.h with tools/hakios-config.py kernel before building touch support
#endif

extern void hakios_tl(const char *format, ...) __printflike(1, 2);
extern void loader_log_line(const char *line);
extern int hakios_debug;

/* printf's message buffer is appended only on return. Keep Spacewar bring-up
 * and failure diagnostics in the live loader stream as complete lines too. */
static int goodix_log(const char *format, ...) __printflike(1, 2);

static int
goodix_log(const char *format, ...)
{
	if (!hakios_debug) return 0;
	char line[512];
	va_list args;

	va_start(args, format);
	vsnprintf(line, sizeof(line), format, args);
	va_end(args);
	if (HAKIOS_DEVICE_SPACEWAR) loader_log_line(line);
	return printf("%s", line);
}
#define printf goodix_log

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);
/* Mach's online count is linked here but its declaration is hidden from BSD headers. */
extern uint32_t processor_avail_count;

/* RPMh's sequencer (TCS 0 of the RSC): the audio DSP's and the modem's drivers write the same slots, so every request goes through this lock */
extern lck_mtx_t hakios_rsc_lock;

static vm_offset_t tlmm, gcc, se, rsc, cmd_db;

/* Explicit Spacewar identity probe; each stage is a separate bounded phone run. */
static uint32_t touch_spacewar, touch_steps = 1;

static inline uint32_t
rd(vm_offset_t address)
{
	return *(volatile uint32_t *)address;
}

static inline void
wr(vm_offset_t address, uint32_t value)
{
	*(volatile uint32_t *)address = value;
}

static void
sleep_us(uint32_t us)
{
	delay_for_interval(us, NSEC_PER_USEC);
}

static void
sleep_ms(uint32_t ms)
{
	delay_for_interval(ms, NSEC_PER_MSEC);
}

/* ---- pins (TLMM) ------------------------------------------------------------------------------- */

#define TLMM_PHYS               0x0f100000UL
#define TLMM_SIZE               0x100000UL
#define GPIO_CFG(n)             (tlmm + 0x1000UL * (n))         /* [1:0] pull, [5:2] function, [8:6] drive, [9] output */
#define GPIO_IO(n)              (tlmm + 0x1000UL * (n) + 4)     /* bit 0 in, bit 1 out */
#define PULL_NONE               0
#define PULL_UP                 3

#define PIN_MISO                52
#define PIN_MOSI                53
#define PIN_CLK                 54
#define PIN_CS                  55
#define PIN_INT                 81      /* low = the chip has a report */
#define PIN_RESET               105     /* low = in reset */
#define PIN_AVDD                107     /* high = analog supply on */
#define PIN_FOCALTECH_ID        68      /* FocalTech if this reads 0 ... */
#define PIN_GOODIX_ID           96      /* ... and this reads 1 (sm8350-kernel-OKX convention) */
#define PIN_FUNCTION_SPI        1       /* "qup15" */

static void
gpio_configure(unsigned pin, unsigned function, unsigned pull, unsigned ma, bool output)
{
	wr(GPIO_CFG(pin), pull | function << 2 | (ma / 2 - 1) << 6 | (output ? 1u << 9 : 0));
}

static void
gpio_set(unsigned pin, bool high)
{
	wr(GPIO_IO(pin), high ? 2 : 0);
}

static bool
gpio_get(unsigned pin)
{
	return rd(GPIO_IO(pin)) & 1;
}

static void
gpio_output(unsigned pin, bool high, unsigned pull, unsigned ma)
{
	gpio_set(pin, high);            /* the level first, so that the pin never drives the wrong one */
	gpio_configure(pin, 0, pull, ma, true);
}

/* ---- the I/O supply (RPMh) ----------------------------------------------------------------------- */

#define CMD_DB_PHYS             0x80860000UL
#define CMD_DB_SIZE             0x20000UL
#define RSC_PHYS                0x18220000UL    /* the application processor's "DRV 2" */
#define RSC_SIZE                0x10000UL
#define TCS(reg)                (rsc + 0xd00 + (reg))           /* command slot set 0, an "active" one */
#define TCS_IRQ_ENABLE          0x00
#define TCS_IRQ_STATUS          0x04
#define TCS_IRQ_CLEAR           0x08
#define TCS_WAIT_FOR_COMPLETION 0x10
#define TCS_CONTROL             0x14
#define TCS_CONTROL_ENABLE      (1u << 16)
#define TCS_CONTROL_TRIGGER     (1u << 24)
#define TCS_STATUS              0x18            /* 0 while busy */
#define TCS_CMD_ENABLE          0x1c
#define TCS_CMD_MSGID(n)        (0x30 + 20 * (n))
#define TCS_CMD_ADDR(n)         (0x34 + 20 * (n))
#define TCS_CMD_DATA(n)         (0x38 + 20 * (n))
#define MSGID_WRITE_4_BYTES     0x10008
#define MSGID_RESPONSE          0x100

#define VRM_VOLTAGE             0               /* a regulator's registers, from its address: millivolts */
#define VRM_ENABLE              4
#define VRM_MODE                8
#define VRM_MODE_LDO_HPM        7

struct cmd_db_slave {
	uint16_t id, header_offset, data_offset, count, version, reserved[3];
};
struct cmd_db_header {
	uint32_t version;
	uint8_t magic[4];
	struct cmd_db_slave slave[8];
	uint32_t checksum, reserved;
	uint8_t data[];
};
struct cmd_db_entry {
	char id[8];
	uint32_t priority[2];
	uint32_t address;
	uint16_t length, offset;
};

/* The RPMh address of a resource by its name in the command database the boot loader left; 0 = none. */
static uint32_t
rpmh_address(const char *name)
{
	const struct cmd_db_header *db = (const struct cmd_db_header *)cmd_db;
	char id[8] = { 0 };

	if (db->magic[0] != 0xdb || db->magic[1] != 0x30 || db->magic[2] != 0x03 || db->magic[3] != 0x0c) {
		printf("goodix: no command database (magic %02x %02x %02x %02x)\n", db->magic[0], db->magic[1], db->magic[2],
		    db->magic[3]);
		return 0;
	}
	strlcpy(id, name, sizeof(id));
	for (unsigned s = 0; s < 8; s++) {
		const struct cmd_db_slave *slave = &db->slave[s];
		const struct cmd_db_entry *entry = (const struct cmd_db_entry *)(db->data + slave->header_offset);

		if (slave->id == 0 || slave->count > 512 || slave->header_offset > CMD_DB_SIZE - sizeof(*db) ||
		    slave->count > (CMD_DB_SIZE - sizeof(*db) - slave->header_offset) / sizeof(*entry)) {
			continue;
		}
		for (unsigned i = 0; i < slave->count; i++) {
			if (memcmp(entry[i].id, id, sizeof(id)) == 0) {
				return entry[i].address;
			}
		}
	}
	return 0;
}

static bool
rpmh_wait(uint32_t reg, uint32_t mask, uint32_t value, const char *what)
{
	for (int i = 0; i < 2000; i++) {
		if ((rd(TCS(reg)) & mask) == value) {
			return true;
		}
		sleep_us(100);
	}
	printf("goodix: RPMh: timeout waiting for %s (register %#x = %#x)\n", what, reg, rd(TCS(reg)));
	return false;
}

/* Send up to three register writes to RPMh and wait until they are done. */
static bool
rpmh_write(const uint32_t *address, const uint32_t *data, unsigned count)
{
	uint32_t irq_enable;
	bool done;

	/* the other drivers that write this slot set take the same lock, so that two requests never mix their address and data words */
	lck_mtx_lock(&hakios_rsc_lock);
	irq_enable = rd(TCS(TCS_IRQ_ENABLE));
	/* the slot set must be idle, and switched off the way the hardware wants it: trigger, then enable */
	for (int i = 0; rd(TCS(TCS_STATUS)) == 0; i++) {
		if (i > 2000) {
			printf("goodix: RPMh: the command slots stay busy\n");
			lck_mtx_unlock(&hakios_rsc_lock);
			return false;
		}
		sleep_us(100);
	}
	wr(TCS(TCS_CONTROL), rd(TCS(TCS_CONTROL)) & ~TCS_CONTROL_TRIGGER);
	wr(TCS(TCS_CONTROL), rd(TCS(TCS_CONTROL)) & ~TCS_CONTROL_ENABLE);
	wr(TCS(TCS_IRQ_CLEAR), 1);
	for (unsigned i = 0; i < count; i++) {
		wr(TCS(TCS_CMD_MSGID(i)), MSGID_WRITE_4_BYTES | MSGID_RESPONSE);
		wr(TCS(TCS_CMD_ADDR(i)), address[i]);
		wr(TCS(TCS_CMD_DATA(i)), data[i]);
	}
	wr(TCS(TCS_WAIT_FOR_COMPLETION), (1u << count) - 1);
	wr(TCS(TCS_CMD_ENABLE), (1u << count) - 1);
	wr(TCS(TCS_IRQ_ENABLE), irq_enable | 1);        /* the status bit follows the enable; the interrupt itself stays masked */
	wr(TCS(TCS_CONTROL), TCS_CONTROL_ENABLE);
	wr(TCS(TCS_CONTROL), TCS_CONTROL_ENABLE | TCS_CONTROL_TRIGGER);

	done = rpmh_wait(TCS_IRQ_STATUS, 1, 1, "the request");
	wr(TCS(TCS_CONTROL), rd(TCS(TCS_CONTROL)) & ~TCS_CONTROL_TRIGGER);
	wr(TCS(TCS_CONTROL), rd(TCS(TCS_CONTROL)) & ~TCS_CONTROL_ENABLE);
	wr(TCS(TCS_CMD_ENABLE), 0);
	wr(TCS(TCS_WAIT_FOR_COMPLETION), 0);
	wr(TCS(TCS_IRQ_CLEAR), 1);
	wr(TCS(TCS_IRQ_ENABLE), irq_enable);
	lck_mtx_unlock(&hakios_rsc_lock);
	return done;
}

/* The chip's I/O supply: 1.8 V from LDO2 of the PM8350C ("ldoc2"). Nothing else is connected to it. */
static bool
io_supply_on(void)
{
	uint32_t base = rpmh_address("ldoc2");
	uint32_t address[3] = { base + VRM_VOLTAGE, base + VRM_ENABLE, base + VRM_MODE };
	uint32_t data[3] = { 1800, 1, VRM_MODE_LDO_HPM };

	/* addresses of regulators ("VRM") have 4 in bits 19:16; anything else is not what we are looking for */
	if (base == 0 || (base >> 16 & 0xf) != 4) {
		printf("goodix: RPMh: no regulator \"ldoc2\" (address %#x)\n", base);
		return false;
	}
	printf("goodix: RPMh: ldoc2 is at %#x; slot status %#x, control %#x, irq enable %#x status %#x\n", base,
	    rd(TCS(TCS_STATUS)), rd(TCS(TCS_CONTROL)), rd(TCS(TCS_IRQ_ENABLE)), rd(TCS(TCS_IRQ_STATUS)));
	if (!rpmh_write(address, data, 3)) {
		return false;
	}
	printf("goodix: I/O supply on (1.8 V)\n");
	return true;
}

/* ---- clocks (GCC) -------------------------------------------------------------------------------- */

#define GCC_PHYS                0x00100000UL
#define GCC_SIZE                0x60000UL
#define GCC_VOTE                (gcc + 0x52008)         /* the application processor's votes */
#define VOTE_QUP1_CORE_2X       (1u << 18)
#define VOTE_QUP1_CORE          (1u << 19)
#define VOTE_QUP1_M_AHB         (1u << 20)
#define VOTE_QUP1_S_AHB         (1u << 21)
#define VOTE_QUP1_SE5           (1u << 27)             /* serial engine 13 is the 6th of the second group */
#define GCC_QUP1_M_AHB_CBCR     (gcc + 0x18004)         /* bit 31: the clock is off */
#define GCC_QUP1_S_AHB_CBCR     (gcc + 0x18008)
#define GCC_QUP1_SE5_CBCR       (gcc + 0x185fc)
#define GCC_QUP1_SE5_CMD        (gcc + 0x18600)         /* bit 0: take the new configuration */
#define GCC_QUP1_SE5_CFG        (gcc + 0x18604)         /* [4:0] 2*divider-1, [10:8] source, [13:12] mode */
#define GCC_QUP1_SE5_DFSR       (gcc + 0x18614)         /* bit 0: the engine chooses among preset rates itself */
#define GCC_QUP1_SE5_PERF_CFG(l) (gcc + 0x1861c + 4 * (l))
#define GCC_QUP1_SE5_PERF_M(l)  (gcc + 0x1865c + 4 * (l))
#define GCC_QUP1_SE5_PERF_N(l)  (gcc + 0x1869c + 4 * (l))
#define CLOCK_OFF               (1u << 31)
#define XO_HZ                   19200000

static uint32_t se_source_hz, se_clock_select;

static uint32_t
rcg_rate(uint32_t cfg, uint32_t m, uint32_t not_n_minus_m)
{
	static const uint32_t parent[8] = { XO_HZ, 600000000, 0, 0, 0, 0, 300000000, 0 };
	uint64_t hz = parent[cfg >> 8 & 7];
	uint32_t divider = cfg & 0x1f, mode = cfg >> 12 & 3;

	if (divider) {
		hz = hz * 2 / (divider + 1);
	}
	if (mode) {
		uint32_t n = (~not_n_minus_m & 0xffff) + (m & 0xffff);

		if (n) {
			hz = hz * (m & 0xffff) / n;
		}
	}
	return (uint32_t)hz;
}

static bool
clocks_on(void)
{
	wr(GCC_VOTE, rd(GCC_VOTE) | VOTE_QUP1_CORE_2X | VOTE_QUP1_CORE | VOTE_QUP1_M_AHB | VOTE_QUP1_S_AHB);
	if (rd(GCC_QUP1_SE5_DFSR) & 1) {
		/* preset rates: the lowest that is at least 4 MHz */
		for (uint32_t level = 0; level < 8; level++) {
			uint32_t hz = rcg_rate(rd(GCC_QUP1_SE5_PERF_CFG(level)), rd(GCC_QUP1_SE5_PERF_M(level)),
			    rd(GCC_QUP1_SE5_PERF_N(level)));

			if (hz >= 4000000 && (se_source_hz == 0 || hz < se_source_hz)) {
				se_source_hz = hz;
				se_clock_select = level;
			}
		}
		if (se_source_hz == 0) {
			printf("goodix: no usable preset clock rate\n");
			return false;
		}
	} else {
		/* the crystal, undivided: nothing else has to be running */
		wr(GCC_QUP1_SE5_CFG, (rd(GCC_QUP1_SE5_CFG) & ~0x371fu) | 1);
		wr(GCC_QUP1_SE5_CMD, rd(GCC_QUP1_SE5_CMD) | 1);
		for (int i = 0; i < 500 && (rd(GCC_QUP1_SE5_CMD) & 1); i++) {
			sleep_us(10);
		}
		if (rd(GCC_QUP1_SE5_CMD) & 1) {
			printf("goodix: the serial engine's clock does not take its configuration (%#x)\n", rd(GCC_QUP1_SE5_CMD));
			return false;
		}
		se_source_hz = XO_HZ;
		se_clock_select = 0;
	}
	wr(GCC_VOTE, rd(GCC_VOTE) | VOTE_QUP1_SE5);
	for (int i = 0; i < 1000; i++) {
		if (!((rd(GCC_QUP1_M_AHB_CBCR) | rd(GCC_QUP1_S_AHB_CBCR) | rd(GCC_QUP1_SE5_CBCR)) & CLOCK_OFF)) {
			return true;
		}
		sleep_us(10);
	}
	printf("goodix: clocks stay off: %#x %#x %#x\n", rd(GCC_QUP1_M_AHB_CBCR), rd(GCC_QUP1_S_AHB_CBCR),
	    rd(GCC_QUP1_SE5_CBCR));
	return false;
}

/* ---- SPI through the serial engine (GENI) ---------------------------------------------------------- */

#define SE_PHYS                 0x00a94000UL
#define SE_SIZE                 0x4000UL
#define GENI_FORCE_DEFAULT      (se + 0x020)
#define GENI_OUTPUT_CTRL        (se + 0x024)
#define GENI_CGC_CTRL           (se + 0x028)
#define GENI_STATUS             (se + 0x040)    /* bit 0: a command is running */
#define GENI_SER_M_CLK_CFG      (se + 0x048)    /* bit 0 enable, [15:4] divider */
#define GENI_IF_DISABLE_RO      (se + 0x064)    /* bit 0: no FIFO interface */
#define GENI_FW_REVISION_RO     (se + 0x068)    /* [15:8] protocol: 1 = SPI */
#define GENI_CLK_SEL            (se + 0x07c)
#define SPI_CPHA                (se + 0x224)
#define SPI_LOOPBACK            (se + 0x22c)
#define SPI_CPOL                (se + 0x230)
#define SPI_DEMUX_OUTPUT_INV    (se + 0x24c)
#define SPI_DEMUX_SEL           (se + 0x250)
#define GENI_BYTE_GRAN          (se + 0x254)
#define GENI_DMA_MODE_EN        (se + 0x258)
#define SPI_TRANS_CFG           (se + 0x25c)
#define GENI_TX_PACKING_CFG0    (se + 0x260)
#define GENI_TX_PACKING_CFG1    (se + 0x264)
#define SPI_WORD_LEN            (se + 0x268)
#define SPI_TX_TRANS_LEN        (se + 0x26c)
#define SPI_RX_TRANS_LEN        (se + 0x270)
#define SPI_DELAY_COUNTERS      (se + 0x278)
#define GENI_RX_PACKING_CFG0    (se + 0x284)
#define GENI_RX_PACKING_CFG1    (se + 0x288)
#define GENI_M_CMD0             (se + 0x600)    /* [31:27] operation */
#define GENI_M_CMD_CTRL         (se + 0x604)    /* bit 2 cancel, bit 1 abort */
#define GENI_M_IRQ_STATUS       (se + 0x610)
#define GENI_M_IRQ_EN           (se + 0x614)
#define GENI_M_IRQ_CLEAR        (se + 0x618)
#define GENI_S_IRQ_CLEAR        (se + 0x648)
#define GENI_TX_FIFO            (se + 0x700)
#define GENI_RX_FIFO            (se + 0x780)
#define GENI_TX_FIFO_STATUS     (se + 0x800)    /* [27:0] words */
#define GENI_RX_FIFO_STATUS     (se + 0x804)    /* bit 31 last, [30:28] bytes of the last word, [24:0] words */
#define GENI_TX_WATERMARK       (se + 0x80c)
#define GENI_RX_WATERMARK       (se + 0x810)
#define GENI_RX_RFR_WATERMARK   (se + 0x814)
#define SE_DMA_TX_IRQ_CLR       (se + 0xc44)
#define SE_DMA_RX_IRQ_CLR       (se + 0xd44)
#define SE_GSI_EVENT_EN         (se + 0xe18)
#define SE_IRQ_EN               (se + 0xe1c)
#define SE_HW_PARAM_0           (se + 0xe24)    /* [21:16] FIFO depth in words */
#define SE_DMA_GENERAL_CFG      (se + 0xe30)

#define M_CMD_DONE              (1u << 0)
#define M_CMD_CANCEL            (1u << 4)
#define M_CMD_ABORT             (1u << 5)
#define M_ERRORS                ((1u << 1) | (1u << 2) | (1u << 3) | (1u << 24) | (1u << 25) | (1u << 28) | (1u << 29))
#define SPI_FULL_DUPLEX         (3u << 27)

static uint32_t fifo_depth;

/* 0 = ready; otherwise why the engine cannot be used */
static const char *
geni_spi_init(uint32_t hz)
{
	uint32_t divider;

	if ((rd(GENI_FW_REVISION_RO) >> 8 & 0xff) != 1) {
		return "the serial engine has no SPI firmware";
	}
	if (rd(GENI_IF_DISABLE_RO) & 1) {
		return "the serial engine's FIFO interface is disabled";
	}
	fifo_depth = rd(SE_HW_PARAM_0) >> 16 & 0x3f;
	if (fifo_depth < 4) {
		return "the serial engine reports no FIFO";
	}
	wr(SE_GSI_EVENT_EN, 0);
	wr(GENI_M_IRQ_CLEAR, 0xffffffff);
	wr(GENI_S_IRQ_CLEAR, 0xffffffff);
	wr(SE_DMA_TX_IRQ_CLR, 0xffffffff);
	wr(SE_DMA_RX_IRQ_CLR, 0xffffffff);
	wr(GENI_CGC_CTRL, rd(GENI_CGC_CTRL) | 0x7f);
	wr(SE_DMA_GENERAL_CFG, rd(SE_DMA_GENERAL_CFG) | 0xf);
	wr(GENI_OUTPUT_CTRL, 0x7f);
	wr(GENI_FORCE_DEFAULT, 1);
	wr(GENI_DMA_MODE_EN, rd(GENI_DMA_MODE_EN) & ~1u);
	wr(GENI_RX_WATERMARK, fifo_depth - 3);
	wr(GENI_RX_RFR_WATERMARK, fifo_depth - 2);
	wr(GENI_TX_WATERMARK, 0);
	/* the status bits we poll follow these enables; the interrupt itself stays masked at the GIC */
	wr(SE_IRQ_EN, rd(SE_IRQ_EN) | 0xf);
	wr(GENI_M_IRQ_EN, rd(GENI_M_IRQ_EN) | 0x7fc0007f);

	/* mode 0, chip select 0, 8 bits, most significant first, four bytes in a FIFO word */
	wr(SPI_LOOPBACK, 0);
	wr(SPI_DEMUX_SEL, 0);
	wr(SPI_CPHA, 0);
	wr(SPI_CPOL, 0);
	wr(SPI_DEMUX_OUTPUT_INV, 0);
	wr(SPI_TRANS_CFG, rd(SPI_TRANS_CFG) & ~2u);
	wr(SPI_DELAY_COUNTERS, 0);
	wr(GENI_TX_PACKING_CFG0, 0x0007f8fe);
	wr(GENI_TX_PACKING_CFG1, 0x000ffefe);
	wr(GENI_RX_PACKING_CFG0, 0x0007f8fe);
	wr(GENI_RX_PACKING_CFG1, 0x000ffefe);
	wr(GENI_BYTE_GRAN, 0);
	wr(SPI_WORD_LEN, 8 - 4);

	divider = (se_source_hz + hz - 1) / hz;
	wr(GENI_CLK_SEL, se_clock_select & 7);
	wr(GENI_SER_M_CLK_CFG, (divider ? divider : 1) << 4 | 1);
	return NULL;
}

static void
geni_drain(uint8_t *rx, uint32_t length, uint32_t *received)
{
	uint32_t status = rd(GENI_RX_FIFO_STATUS), words = status & 0x01ffffff, bytes = words * 4;

	if (status & 0x80000000u) {
		uint32_t valid = status >> 28 & 7;

		if (valid && valid < 4) {
			bytes -= 4 - valid;
		}
	}
	for (uint32_t i = 0; i < words; i++) {
		uint32_t word = rd(GENI_RX_FIFO);

		for (int j = 0; j < 4 && bytes; j++, bytes--) {
			if (*received < length) {
				if (rx) {
					rx[*received] = (uint8_t)(word >> (8 * j));
				}
				(*received)++;
			}
		}
	}
}

static void
geni_recover(void)
{
	wr(GENI_M_CMD_CTRL, 1u << 2);
	for (int i = 0; i < 2000 && !(rd(GENI_M_IRQ_STATUS) & M_CMD_CANCEL); i++) {
		sleep_us(100);
	}
	if (!(rd(GENI_M_IRQ_STATUS) & M_CMD_CANCEL)) {
		wr(GENI_M_CMD_CTRL, 1u << 1);
		for (int i = 0; i < 2000 && !(rd(GENI_M_IRQ_STATUS) & M_CMD_ABORT); i++) {
			sleep_us(100);
		}
	}
	for (int i = 0; i < 64 && (rd(GENI_RX_FIFO_STATUS) & 0x01ffffff); i++) {
		(void)rd(GENI_RX_FIFO);
	}
	wr(GENI_M_IRQ_CLEAR, 0xffffffff);
}

/* One transfer with the chip selected throughout: `length` bytes out (zeros without tx) and as many in. */
static bool
geni_transfer(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	static unsigned complaints;
	uint32_t sent = 0, received = 0, status = 0;
	uint64_t deadline;

	for (int i = 0; rd(GENI_STATUS) & 1; i++) {
		if (i > 2000) {
			geni_recover();
			break;
		}
		sleep_us(100);
	}
	wr(GENI_M_IRQ_CLEAR, 0xffffffff);
	wr(SPI_TX_TRANS_LEN, length);
	wr(SPI_RX_TRANS_LEN, length);
	wr(GENI_M_CMD0, SPI_FULL_DUPLEX);
	clock_interval_to_deadline(200, NSEC_PER_MSEC, &deadline);
	while (mach_absolute_time() < deadline) {
		while (sent < length && (rd(GENI_TX_FIFO_STATUS) & 0x0fffffff) + 1 < fifo_depth) {
			uint32_t word = 0;

			for (int j = 0; j < 4 && sent < length; j++, sent++) {
				word |= (uint32_t)(tx ? tx[sent] : 0) << (8 * j);
			}
			wr(GENI_TX_FIFO, word);
		}
		status = rd(GENI_M_IRQ_STATUS);
		geni_drain(rx, length, &received);
		if (status & M_ERRORS) {
			break;
		}
		if (status & M_CMD_DONE) {
			geni_drain(rx, length, &received);
			wr(GENI_M_IRQ_CLEAR, status);
			if (received == length) {
				return true;
			}
			break;
		}
	}
	if (complaints++ < 8) {
		printf("goodix: SPI transfer of %u bytes failed: status %#x, sent %u, received %u\n", length, status, sent,
		    received);
	}
	geni_recover();
	return false;
}

/* ---- SPI by hand, on the same pins ----------------------------------------------------------------- */

static unsigned bitbang_in = PIN_MISO, bitbang_out = PIN_MOSI;

static void
bitbang_init(void)
{
	gpio_output(PIN_CS, true, PULL_NONE, 6);
	gpio_output(PIN_CLK, false, PULL_NONE, 6);
	gpio_output(bitbang_out, false, PULL_NONE, 6);
	gpio_configure(bitbang_in, 0, PULL_NONE, 2, false);
}

static bool
bitbang_transfer(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	gpio_set(PIN_CS, false);
	for (uint32_t i = 0; i < length; i++) {
		uint8_t out = tx ? tx[i] : 0, in = 0;

		for (int bit = 7; bit >= 0; bit--) {
			gpio_set(bitbang_out, out >> bit & 1);
			gpio_set(PIN_CLK, true);
			in = (uint8_t)(in << 1 | gpio_get(bitbang_in));
			gpio_set(PIN_CLK, false);
		}
		if (rx) {
			rx[i] = in;
		}
	}
	gpio_set(PIN_CS, true);
	return true;
}

static bool (*spi_transfer)(const uint8_t *tx, uint8_t *rx, uint32_t length);

/* ---- the Goodix chip ------------------------------------------------------------------------------- */

#define GOODIX_FW_VERSION       0x10014         /* 28 bytes */
#define GOODIX_IC_INFO          0x10070         /* a 16-bit length, then that many bytes */
#define GOODIX_MAX_READ         1024
#define GOODIX_MAX_TOUCHES      10
#define GOODIX_STATUS_TOUCH     0x80
#define GOODIX_STATUS_REQUEST   0x40
#define GOODIX_REQUEST_RESET    3

static uint8_t spi_tx[8 + GOODIX_MAX_READ], spi_rx[8 + GOODIX_MAX_READ];
static uint32_t touch_address, command_address;
static const char *report_rate = "240";        /* what the status line says (touch_rate=480) */
static uint32_t max_x = 10800, max_y = 24000;   /* the device tree's; the chip may say otherwise */

static bool
goodix_read(uint32_t address, uint8_t *data, uint32_t length)
{
	if (length > GOODIX_MAX_READ) {
		return false;
	}
	spi_tx[0] = 0xf1;
	spi_tx[1] = (uint8_t)(address >> 24);
	spi_tx[2] = (uint8_t)(address >> 16);
	spi_tx[3] = (uint8_t)(address >> 8);
	spi_tx[4] = (uint8_t)address;
	spi_tx[5] = spi_tx[6] = spi_tx[7] = 0xff;
	memset(spi_tx + 8, 0, length);
	if (!spi_transfer(spi_tx, spi_rx, 8 + length)) {
		return false;
	}
	memcpy(data, spi_rx + 8, length);
	return true;
}

static bool
goodix_write(uint32_t address, const uint8_t *data, uint32_t length)
{
	if (length > GOODIX_MAX_READ) {
		return false;
	}
	spi_tx[0] = 0xf0;
	spi_tx[1] = (uint8_t)(address >> 24);
	spi_tx[2] = (uint8_t)(address >> 16);
	spi_tx[3] = (uint8_t)(address >> 8);
	spi_tx[4] = (uint8_t)address;
	memcpy(spi_tx + 5, data, length);
	return spi_transfer(spi_tx, NULL, 5 + length);
}

/* The chip's blocks end in the 16-bit sum of their bytes; all zeros or all ones is no answer at all. */
static bool
goodix_checksum_ok(const uint8_t *data, uint32_t length)
{
	uint32_t sum = 0;
	bool blank = true;

	if (length < 2) {
		return false;
	}
	for (uint32_t i = 0; i < length - 2; i++) {
		sum += data[i];
		blank &= data[i] == 0 || data[i] == 0xff;
	}
	return !blank && (uint16_t)sum == (uint16_t)(data[length - 2] | data[length - 1] << 8);
}

static uint16_t
le16(const uint8_t *p)
{
	return (uint16_t)(p[0] | p[1] << 8);
}

static uint32_t
le32(const uint8_t *p)
{
	return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24;
}

static void
touch_reset(void)
{
	gpio_set(PIN_RESET, false);
	sleep_ms(2);
	gpio_set(PIN_RESET, true);
	sleep_ms(100);
}

/* Is the chip there and running its firmware? Finds where it keeps the touch report. */
static bool
goodix_probe(bool verbose)
{
	static uint8_t info[GOODIX_MAX_READ];
	uint8_t version[28];
	uint32_t length, offset;

	if (!goodix_read(GOODIX_FW_VERSION, version, sizeof(version))) {
		return false;
	}
	if (!goodix_checksum_ok(version, sizeof(version))) {
		if (verbose) {
			printf("goodix: no valid firmware version: %02x %02x %02x %02x %02x %02x %02x %02x ... %02x %02x\n",
			    version[0], version[1], version[2], version[3], version[4], version[5], version[6], version[7],
			    version[26], version[27]);
		}
		return false;
	}
	printf("goodix: chip \"%.6s\", firmware \"%.8s\" %02x.%02x.%02x.%02x, sensor %u\n", (const char *)version,
	    (const char *)version + 10, version[18], version[19], version[20], version[21], version[23]);

	if (!goodix_read(GOODIX_IC_INFO, info, 2)) {
		return false;
	}
	length = le16(info);
	if (length < 40 || length >= GOODIX_MAX_READ || !goodix_read(GOODIX_IC_INFO, info, length) ||
	    !goodix_checksum_ok(info, length)) {
		printf("goodix: bad chip description (length %u)\n", length);
		return false;
	}
	/* the length, the version (16 bytes), the features (10), four counts, then five lists of 16-bit numbers */
	offset = 2 + 16 + 10 + 4;
	for (int i = 0; i < 5; i++) {
		if (offset >= length) {
			return false;
		}
		offset += 1 + 2u * info[offset];
	}
	if (offset + 48 + 2 > length) {
		printf("goodix: chip description too short (%u, lists end at %u)\n", length, offset);
		return false;
	}
	touch_address = le32(info + offset + 44);
	command_address = le32(info + offset + 0);      /* (the "misc" part: the command buffer comes first) */
	if (offset + 122 + 20 + 2 <= length) {
		uint16_t x = le16(info + offset + 122 + 16), y = le16(info + offset + 122 + 18);

		if (x && y) {
			max_x = x;
			max_y = y;
		}
	}
	printf("goodix: touch report at %#x, coordinates up to %u x %u\n", touch_address, max_x, max_y);
	return touch_address != 0;
}

/*
 * A command to the chip (Android's brl_send_cmd(): goodix_brl_hw.c of the phone's kernel): sixteen
 * bytes at the command address -- state, acknowledgement, length, command, its data, and the sum of
 * the bytes from the length on -- then the acknowledgement looked for, 0x80 when done.
 */
#define GOODIX_CMD_HIGH_RATE    0xc0            /* data 1: report 480 times a second instead of 240 (Xiaomi's game mode) */
#define GOODIX_ACK_OK           0x80
#define GOODIX_ACK_BUSY         0x02

static bool
goodix_command(uint8_t command, uint8_t data)
{
	uint8_t buffer[16], answer[16];
	uint32_t sum;

	if (command_address == 0) {
		return false;
	}
	memset(buffer, 0, sizeof(buffer));
	buffer[2] = 5;
	buffer[3] = command;
	buffer[4] = data;
	sum = buffer[2] + buffer[3] + buffer[4];
	buffer[5] = (uint8_t)sum;
	buffer[6] = (uint8_t)(sum >> 8);
	for (int attempt = 0; attempt < 6; attempt++) {
		if (!goodix_write(command_address, buffer, sizeof(buffer))) {
			return false;
		}
		for (int look = 0; look < 6; look++) {
			if (!goodix_read(command_address, answer, sizeof(answer))) {
				return false;
			}
			if (answer[1] == GOODIX_ACK_OK) {
				return true;
			}
			sleep_ms(1);
			if (answer[1] != GOODIX_ACK_BUSY && answer[1] != 0) {
				break;
			}
		}
	}
	return false;
}

/* What has been seen so far: said once a minute, so that the end of the log always tells. */
static struct {
	uint32_t polls, reports, touches, bad, last_x, last_y;
} seen;

/* Which fingers were down in the last report, and where. */
static struct {
	bool down, seen;
	uint32_t x, y;
} contact[GOODIX_MAX_TOUCHES];
static uint32_t flip;           /* boot-arg touch_flip: 1 = mirror left-right, 2 = mirror top-bottom */

static void
goodix_report(const uint8_t *touches, uint32_t count)
{
	static unsigned shown;

	for (int i = 0; i < GOODIX_MAX_TOUCHES; i++) {
		contact[i].seen = false;
	}
	for (uint32_t i = 0; i < count; i++) {
		const uint8_t *t = touches + 8 * i;
		uint32_t id = t[0] >> 4, kind = t[0] & 0xf;
		uint32_t x = le16(t + 2), y = le16(t + 4);

		if (id >= GOODIX_MAX_TOUCHES || kind == 1 || kind == 3) {       /* 1 and 3: a stylus */
			continue;
		}
		if (shown < 40) {
			shown++;
			printf("goodix: finger %u at %u, %u (of %u x %u)\n", id, x, y, max_x, max_y);
		}
		seen.touches++;
		seen.last_x = x;
		seen.last_y = y;
		x = x >= max_x ? 65535 : x * 65535 / max_x;
		y = y >= max_y ? 65535 : y * 65535 / max_y;
		if (flip & 1) {
			x = 65535 - x;
		}
		if (flip & 2) {
			y = 65535 - y;
		}
		contact[id].seen = true;
		if (!contact[id].down) {
			hakios_touch_post(HAKIOS_TOUCH_DOWN, id, x, y);
		} else if (contact[id].x != x || contact[id].y != y) {
			hakios_touch_post(HAKIOS_TOUCH_MOVE, id, x, y);
		}
		contact[id].down = true;
		contact[id].x = x;
		contact[id].y = y;
	}
	/* a finger that is no longer in the report has lifted */
	for (uint32_t id = 0; id < GOODIX_MAX_TOUCHES; id++) {
		if (contact[id].down && !contact[id].seen) {
			contact[id].down = false;
			hakios_touch_post(HAKIOS_TOUCH_UP, id, contact[id].x, contact[id].y);
		}
	}
}

/* Read the report if there is one, and tell the chip it has been read. */
static void
goodix_poll(void)
{
	static uint8_t report[8 + 8 * GOODIX_MAX_TOUCHES + 2];
	static unsigned bad;
	const uint8_t zero = 0;
	const uint32_t first = 8 + 8 * 2 + 2;           /* the header and two fingers: all there is, most of the time */
	uint32_t count;

	seen.polls++;
	if (!goodix_read(touch_address, report, first) || report[0] == 0) {
		return;
	}
	seen.reports++;
	count = report[2] & 0xf;
	if ((report[0] & GOODIX_STATUS_TOUCH) && count > 2 && count <= GOODIX_MAX_TOUCHES &&
	    !goodix_read(touch_address + 8, report + 8, 8 * count + 2)) {
		return;
	}
	goodix_write(touch_address, &zero, 1);
	if (!goodix_checksum_ok(report, 8)) {
		seen.bad++;
		if (bad++ < 8) {
			printf("goodix: bad report header: %02x %02x %02x %02x %02x %02x %02x %02x\n", report[0], report[1],
			    report[2], report[3], report[4], report[5], report[6], report[7]);
		}
		return;
	}
	if (report[0] & GOODIX_STATUS_REQUEST) {
		if ((report[2] & 0xf) == GOODIX_REQUEST_RESET || report[2] == GOODIX_REQUEST_RESET) {
			printf("goodix: the chip asks to be reset\n");
			touch_reset();
		}
		return;
	}
	if (!(report[0] & GOODIX_STATUS_TOUCH)) {
		return;
	}
	if (count > GOODIX_MAX_TOUCHES || (count && !goodix_checksum_ok(report + 8, count * 8 + 2))) {
		seen.bad++;
		if (bad++ < 8) {
			printf("goodix: bad report (%u touches)\n", count);
		}
		return;
	}
	goodix_report(report + 8, count);
}

/* ---- the FocalTech FT3680 chip --------------------------------------------------------------------- */

/*
 * FTS 5.x SPI framing from sm8350-kernel-OKX drivers/input/touchscreen/focaltech_3680/
 * focaltech_spi.c (verbatim):
 *   TX[0] = register address (writebuf[0] / cmd[0])
 *   TX[1] = FOCALTECH_READ_CTRL (0xA0 = READ_CMD, CRC_EN set) or FOCALTECH_WRITE_CTRL (0x00)
 *   TX[2..3] = data length BE (for read: datalen; for write: writelen - 1)
 *   TX[4..6] = three dummy bytes (SPI_DUMMY_BYTE)
 *   TX[7..7+len-1] = write data (empty on read; chip drives MISO here)
 *   TX[last-1..last] = CRC-16/Kermit of the data bytes, little-endian (only when READ_CMD or
 *                     when a vendor WRITE op needs CRC; vendor WRITE_CMD = 0x00 has no CRC)
 * SPI_HEADER_LENGTH = 4 + SPI_DUMMY_BYTE = 7.
 */
#define FOCALTECH_READ_CTRL         0xa0		/* READ_CMD = 0x80 | DATA_CRC_EN(0x20) */
#define FOCALTECH_WRITE_CTRL        0x00		/* WRITE_CMD, no CRC on TX */
#define FOCALTECH_CHIP_ID_H         0xa3
#define FOCALTECH_CHIP_ID_L         0x9f
#define FOCALTECH_TOUCH_DATA        0x01		/* the touch report lives at this address */
#define FOCALTECH_MAX_TOUCHES       10
#define FOCALTECH_TOUCH_REPORT_LEN  (2 + 6 * FOCALTECH_MAX_TOUCHES)
#define FOCALTECH_MAX_TRANSFER      (7 + FOCALTECH_TOUCH_REPORT_LEN + 2)

static uint8_t focaltech_tx[FOCALTECH_MAX_TRANSFER], focaltech_rx[FOCALTECH_MAX_TRANSFER];

/* CRC-16/Kermit (polynomial 0x8408 reflected, initial 0xffff) -- same as vendor rckit_crc16(). */
static uint16_t
focaltech_crc(const uint8_t *data, uint32_t length)
{
	uint16_t crc = 0xffff;

	for (uint32_t i = 0; i < length; i++) {
		crc ^= data[i];
		for (int bit = 0; bit < 8; bit++) {
			crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0x8408) : (uint16_t)(crc >> 1);
		}
	}
	return crc;
}

static bool
focaltech_read(uint8_t address, uint8_t *data, uint32_t length)
{
	const uint32_t data_offset = 7;
	uint32_t transfer_length = data_offset + length + 2;

	if (length == 0 || transfer_length > sizeof(focaltech_tx)) {
		return false;
	}
	for (int attempt = 0; attempt < 3; attempt++) {
		memset(focaltech_tx, 0, transfer_length);
		memset(focaltech_rx, 0, transfer_length);
		focaltech_tx[0] = address;		/* vendor: TX[0] = cmd[0] = reg address */
		focaltech_tx[1] = FOCALTECH_READ_CTRL;	/* vendor: TX[1] = ctrl = 0xA0 */
		focaltech_tx[2] = (uint8_t)(length >> 8);
		focaltech_tx[3] = (uint8_t)length;
		if (!spi_transfer(focaltech_tx, focaltech_rx, transfer_length)) {
			sleep_us(150);
			continue;
		}
		/* vendor: bit 7 of status (rx[3]) is BUSY, bit 5 is CRC error */
		if (!(focaltech_rx[3] & 0xa0)) {
			uint16_t expected = focaltech_crc(focaltech_rx + data_offset, length);
			uint16_t received = (uint16_t)(focaltech_rx[data_offset + length] |
			    focaltech_rx[data_offset + length + 1] << 8);
			if (expected == received) {
				memcpy(data, focaltech_rx + data_offset, length);
				sleep_us(150);		/* minimum CS-high time in the vendor driver */
				return true;
			}
		}
		sleep_us(150);
	}
	return false;
}

/*
 * Vendor fts_write: no CRC on TX. For a 1-byte "command" (length == 0) the frame is just the
 * four header bytes { addr, 0x00, 0x00, 0x00 } with no dummy and no data. For length > 0 the
 * three dummy bytes precede the data: { addr, 0x00, lenH, lenL, 0, 0, 0, data... }. The chip
 * drives the "status" byte on rx[3] -- bits 7 and 5 set mean busy or CRC error (vendor retries
 * SPI_RETRY_NUMBER = 3 times on failure before giving up).
 */
static bool
focaltech_write(uint8_t address, const uint8_t *data, uint32_t length)
{
	uint32_t transfer_length;

	if (length == 0) {
		transfer_length = 4;
	} else {
		transfer_length = 7 + length;
	}
	if (transfer_length > sizeof(focaltech_tx)) {
		return false;
	}
	memset(focaltech_tx, 0, transfer_length);
	focaltech_tx[0] = address;
	focaltech_tx[1] = FOCALTECH_WRITE_CTRL;
	focaltech_tx[2] = (uint8_t)(length >> 8);
	focaltech_tx[3] = (uint8_t)length;
	if (data && length > 0) {
		memcpy(focaltech_tx + 7, data, length);
	}
	for (int attempt = 0; attempt < 3; attempt++) {
		if (spi_transfer(focaltech_tx, focaltech_rx, transfer_length) &&
		    !(focaltech_rx[3] & 0xa0)) {
			sleep_us(150);
			return true;
		}
		sleep_us(150);
	}
	return false;
}

/*
 * Vendor fts_fwupg_get_boot_state() reads the chip's "boot ID" via the FT boot-ROM protocol:
 *   TX = { 0x55, 0xAA }                 (FTS_CMD_START1, FTS_CMD_START2 -- enter boot)
 *   wait FTS_CMD_START_DELAY = 12 ms
 *   TX = { 0x90 }, RX = 2 bytes         (FTS_CMD_READ_ID, incell cmd length)
 * If the two RX bytes are the chip's boot ID (0x56 0x62 for FT3680), firmware is present and
 * valid in flash but has not yet been started; the host must issue FTS_CMD_FIRMWARE_START
 * (0x08) to make the boot ROM jump to it.
 */
#define FTS_CMD_START1              0x55
#define FTS_CMD_START2              0xaa
#define FTS_CMD_START_DELAY         12		/* ms, between enter-boot and first command */
#define FTS_CMD_READ_BOOT_ID        0x90

/* Boot-ROM commands for firmware (pramboot) upload, from focaltech_3658u/focaltech_flash.h. */
#define FTS_ROMBOOT_CMD_SET_PRAM_ADDR   0xad
#define FTS_ROMBOOT_CMD_WRITE           0xae
#define FTS_ROMBOOT_CMD_START_APP       0x08
#define FTS_FLASH_PACKET_LENGTH         32		/* bytes per PRAM write */

#include "hakios.focaltech_fw.h"

/*
 * Vendor fts_read_bootid(): send { 0x55, 0xAA } as a 2-byte framed write (treated as a write of
 * one data byte 0xAA to register 0x55), wait 12 ms, then do a framed read of 2 bytes at
 * register 0x90. The chip answers with its ROM ID (0x56 0x62 for FT3680) if it is sitting in
 * the boot ROM waiting for a firmware download.
 */
static bool
focaltech_read_boot_id(uint8_t *high, uint8_t *low)
{
	uint8_t aa = FTS_CMD_START2;
	uint8_t out[2] = { 0 };
	bool ok;

	if (!focaltech_write(FTS_CMD_START1, &aa, 1)) {
		return false;
	}
	sleep_ms(12);			/* FTS_CMD_START_DELAY */
	ok = focaltech_read(FTS_CMD_READ_BOOT_ID, out, 2);
	*high = out[0];
	*low  = out[1];
	return ok;
}

/* Vendor fts_wait_tp_to_valid: poll chip ID for up to TIMEOUT_READ_REG (1 s) at 200 ms steps. */
static bool
focaltech_wait_valid(uint8_t *high, uint8_t *low)
{
	for (int attempt = 0; attempt < 5; attempt++) {
		if (focaltech_read(FOCALTECH_CHIP_ID_H, high, 1) &&
		    focaltech_read(FOCALTECH_CHIP_ID_L, low, 1) &&
		    *high == 0x56 && *low == 0x62) {
			printf("focaltech: FT3680 runtime ID valid after %d attempt(s)\n", attempt + 1);
			return true;
		}
		sleep_ms(200);			/* vendor INTERVAL_READ_REG */
	}
	return false;
}

/*
 * Reset the chip and send 0x55/0xAA to put it in boot ROM, as vendor fts_get_ic_information
 * does. Try up to 3 times with escalating settle delays (12, 20, 28 ms). Returns true if the
 * chip is sitting in its boot ROM, with { 0x56, 0x62 } read back from the boot-ID register.
 */
static bool
focaltech_enter_boot_rom(void)
{
	uint8_t boot_high = 0, boot_low = 0;

	for (int cnt = 0; cnt < 3; cnt++) {
		gpio_set(PIN_RESET, false);
		sleep_ms(1);
		gpio_set(PIN_RESET, true);
		sleep_ms(FTS_CMD_START_DELAY + cnt * 8);
		if (focaltech_read_boot_id(&boot_high, &boot_low) &&
		    boot_high == 0x56 && boot_low == 0x62) {
			printf("focaltech: in boot ROM after reset %d\n", cnt);
			return true;
		}
		printf("focaltech: boot ID attempt %d: %02x %02x (not 56 62)\n", cnt, boot_high, boot_low);
	}
	return false;
}

/*
 * Download the embedded firmware (pramboot + application) into the chip's PRAM via the boot
 * ROM's SPI_V2 upload protocol. Each 32-byte packet is sent as two framed writes: first set
 * the PRAM destination address (SET_PRAM_ADDR + 3 BE address bytes), then WRITE + data bytes.
 */
static bool
focaltech_write_pram(const uint8_t *buf, uint32_t len)
{
	uint8_t addr_bytes[3];

	for (uint32_t offset = 0; offset < len; offset += FTS_FLASH_PACKET_LENGTH) {
		uint32_t chunk = FTS_FLASH_PACKET_LENGTH;
		if (offset + chunk > len) {
			chunk = len - offset;
		}
		addr_bytes[0] = (uint8_t)(offset >> 16);
		addr_bytes[1] = (uint8_t)(offset >> 8);
		addr_bytes[2] = (uint8_t)offset;
		if (!focaltech_write(FTS_ROMBOOT_CMD_SET_PRAM_ADDR, addr_bytes, 3)) {
			printf("focaltech: PRAM address write failed at offset %u\n", offset);
			return false;
		}
		if (!focaltech_write(FTS_ROMBOOT_CMD_WRITE, buf + offset, chunk)) {
			printf("focaltech: PRAM data write failed at offset %u\n", offset);
			return false;
		}
	}
	return true;
}

static bool
focaltech_start_app(void)
{
	uint8_t cmd = FTS_ROMBOOT_CMD_START_APP;

	return focaltech_write(cmd, NULL, 0);
}

/*
 * Full chip bring-up: look for an already-running firmware; if none, reset to boot ROM and
 * download the embedded firmware into PRAM; then start it and wait for runtime to come up.
 */
static bool
focaltech_probe(bool verbose)
{
	uint8_t high = 0, low = 0;

	/* Maybe firmware is already running (previous session, warm boot). */
	if (focaltech_wait_valid(&high, &low)) {
		return true;
	}

	if (!focaltech_enter_boot_rom()) {
		if (verbose) {
			printf("focaltech: chip not in boot ROM; last read runtime %02x %02x\n", high, low);
		}
		return false;
	}

	printf("focaltech: downloading %u bytes of firmware to PRAM...\n", FOCALTECH_FW_LENGTH);
	if (!focaltech_write_pram(focaltech_fw, FOCALTECH_FW_LENGTH)) {
		printf("focaltech: PRAM download failed\n");
		return false;
	}
	printf("focaltech: PRAM download complete; starting application\n");
	if (!focaltech_start_app()) {
		printf("focaltech: start-app command failed\n");
		return false;
	}
	sleep_ms(300);			/* firmware needs time to initialize all peripherals */

	if (focaltech_wait_valid(&high, &low)) {
		return true;
	}
	if (verbose) {
		printf("focaltech: firmware did not come up (last runtime read %02x %02x)\n", high, low);
	}
	return false;
}

/* ---- FocalTech touch reports --------------------------------------------------------------------- */

/*
 * Per-chip report layout for FT3680 (vendor focaltech_core.c, FTS_TOUCH_OFF_* offsets):
 *   report[0] = gesture (0 outside gesture mode)
 *   report[1] = (chip_type << 4) | finger_count
 *   report[2 + 6*i + 0] = E_XH:  bits 7..6 = event (0 = down, 1 = up, 2 = contact)
 *                                bits 3..0 = x bits 14..11
 *   report[2 + 6*i + 1] = XL:    x bits 10..3
 *   report[2 + 6*i + 2] = ID_YH: bits 7..4 = finger id, bits 3..0 = y bits 14..11
 *   report[2 + 6*i + 3] = YL:    y bits 10..3
 *   report[2 + 6*i + 4] = PRE:   bits 7..5 = x bits 2..0, bits 4..2 = y bits 2..0
 *                                (also the pressure byte -- the lowest bits of each 15-bit axis
 *                                 are packed in with it)
 *   report[2 + 6*i + 5] = AREA:  touch area / major
 * Coordinates are 15-bit "super-high resolution" sub-pixel values; the touch panel's actual
 * extent is reported through the device tree as x_max/y_max (10800 x 24000 for redwood).
 */
#define FOCALTECH_EVENT_DOWN        0
#define FOCALTECH_EVENT_UP          1
#define FOCALTECH_EVENT_CONTACT     2

static struct {
	bool down, seen;
	uint32_t x, y;
} focaltech_contact[FOCALTECH_MAX_TOUCHES];

static void
focaltech_contact_update(uint32_t id, uint32_t event, uint32_t x, uint32_t y,
    uint32_t max_report_x, uint32_t max_report_y)
{
	static unsigned shown;

	if (id >= FOCALTECH_MAX_TOUCHES || max_report_x == 0 || max_report_y == 0) {
		return;
	}
	if (event == FOCALTECH_EVENT_UP) {
		focaltech_contact[id].seen = true;
		if (focaltech_contact[id].down) {
			focaltech_contact[id].down = false;
			hakios_touch_post(HAKIOS_TOUCH_UP, id, focaltech_contact[id].x, focaltech_contact[id].y);
		}
		return;
	}
	seen.touches++;
	/* the chip's 15-bit values can exceed the panel: keep them inside it, so the mirroring below cannot underflow */
	if (x > max_report_x - 1) {
		x = max_report_x - 1;
	}
	if (y > max_report_y - 1) {
		y = max_report_y - 1;
	}
	switch (flip) {
	case 1: x = max_report_x - 1 - x; break;
	case 2: y = max_report_y - 1 - y; break;
	case 3: x = max_report_x - 1 - x; y = max_report_y - 1 - y; break;
	}
	x = (uint32_t)(((uint64_t)x * 65535) / (max_report_x - 1));
	y = (uint32_t)(((uint64_t)y * 65535) / (max_report_y - 1));
	seen.last_x = x;
	seen.last_y = y;
	focaltech_contact[id].seen = true;
	if (!focaltech_contact[id].down) {
		focaltech_contact[id].down = true;
		hakios_touch_post(HAKIOS_TOUCH_DOWN, id, x, y);
	} else if (focaltech_contact[id].x != x || focaltech_contact[id].y != y) {
		hakios_touch_post(HAKIOS_TOUCH_MOVE, id, x, y);
	}
	focaltech_contact[id].x = x;
	focaltech_contact[id].y = y;
	if (shown++ < 8) {
		printf("focaltech: finger %u %s at %u, %u\n", id,
		    event == FOCALTECH_EVENT_DOWN ? "down" : "move", x, y);
	}
}

/* Panel size in the FT3680 super-high-resolution reports (device tree x_max/y_max on redwood). */
static uint32_t focaltech_max_x = 10800, focaltech_max_y = 24000;

static void
focaltech_report(const uint8_t *report)
{
	uint32_t count = report[1] & 0x0f;

	for (uint32_t i = 0; i < FOCALTECH_MAX_TOUCHES; i++) {
		focaltech_contact[i].seen = false;
	}
	for (uint32_t i = 0; i < FOCALTECH_MAX_TOUCHES; i++) {
		const uint8_t *touch = report + 2 + 6 * i;
		uint32_t id = (touch[2] >> 4) & 0x0f;
		uint32_t event, x, y;

		if (id >= FOCALTECH_MAX_TOUCHES) {
			break;			/* no more valid slots in this report */
		}
		event = (touch[0] >> 6) & 0x3;
		x = ((uint32_t)(touch[0] & 0x0f) << 11) | ((uint32_t)touch[1] << 3) |
		    ((uint32_t)touch[4] >> 5);
		y = ((uint32_t)(touch[2] & 0x0f) << 11) | ((uint32_t)touch[3] << 3) |
		    (((uint32_t)touch[4] & 0x1c) >> 2);
		focaltech_contact_update(id, event, x, y, focaltech_max_x, focaltech_max_y);
	}
	(void)count;
	/* Fingers not in this report are up (vendor driver uses a bitmap; we remember explicitly). */
	for (uint32_t i = 0; i < FOCALTECH_MAX_TOUCHES; i++) {
		if (!focaltech_contact[i].seen && focaltech_contact[i].down) {
			focaltech_contact[i].down = false;
			hakios_touch_post(HAKIOS_TOUCH_UP, i, focaltech_contact[i].x, focaltech_contact[i].y);
		}
	}
}

static void
focaltech_poll(void)
{
	static uint8_t report[FOCALTECH_TOUCH_REPORT_LEN];
	static unsigned bad;

	seen.polls++;
	if (!focaltech_read(FOCALTECH_TOUCH_DATA, report, sizeof(report))) {
		seen.bad++;
		if (bad++ < 8) {
			printf("focaltech: SPI read failed for touch report\n");
		}
		return;
	}
	/* (nothing to report: what the vendor's driver takes for a report that is not one; a finger would seem to have lifted) */
	if (report[1] == 0xff && report[2] == 0xff && report[3] == 0xff) {
		return;
	}
	seen.reports++;
	focaltech_report(report);
}

/* Which touch chip is on this board. */
enum touch_chip { TOUCH_GOODIX, TOUCH_FOCALTECH };
static enum touch_chip touch_chip;

static enum touch_chip
touch_chip_detect(void)
{
	gpio_configure(PIN_FOCALTECH_ID, 0, PULL_NONE, 2, false);
	gpio_configure(PIN_GOODIX_ID, 0, PULL_NONE, 2, false);
	if (gpio_get(PIN_GOODIX_ID) && !gpio_get(PIN_FOCALTECH_ID)) {
		return TOUCH_FOCALTECH;
	}
	return TOUCH_GOODIX;
}

static bool
chip_probe(bool verbose)
{
	if (touch_chip == TOUCH_FOCALTECH) {
		return focaltech_probe(verbose);
	}
	touch_reset();
	return goodix_probe(verbose);
}

static void
chip_poll(void)
{
	if (touch_chip == TOUCH_FOCALTECH) {
		focaltech_poll();
	} else {
		goodix_poll();
	}
}

/* ---- all of it together ---------------------------------------------------------------------------- */

static void
show_state(const char *when)
{
	printf("goodix: %s: votes %#x, clocks %#x %#x %#x, rate cfg %#x dfs %#x; pins cs/clk/mosi/miso cfg %#x %#x %#x %#x,"
	    " reset %#x/%u, int %#x/%u, avdd %#x/%u, straps %u %u\n", when, rd(GCC_VOTE), rd(GCC_QUP1_M_AHB_CBCR),
	    rd(GCC_QUP1_S_AHB_CBCR), rd(GCC_QUP1_SE5_CBCR), rd(GCC_QUP1_SE5_CFG), rd(GCC_QUP1_SE5_DFSR), rd(GPIO_CFG(PIN_CS)),
	    rd(GPIO_CFG(PIN_CLK)), rd(GPIO_CFG(PIN_MOSI)), rd(GPIO_CFG(PIN_MISO)), rd(GPIO_CFG(PIN_RESET)),
	    rd(GPIO_IO(PIN_RESET)), rd(GPIO_CFG(PIN_INT)), rd(GPIO_IO(PIN_INT)), rd(GPIO_CFG(PIN_AVDD)), rd(GPIO_IO(PIN_AVDD)),
	    gpio_get(68), gpio_get(96));
}

/* Power the chip up from nothing, in the vendor driver's order. False if the I/O supply could not be switched on. */
static bool
power_on(void)
{
	gpio_output(PIN_RESET, false, PULL_UP, 8);
	gpio_configure(PIN_INT, 0, PULL_UP, 8, false);
	/* without its I/O rail the chip must not get the analog supply or be driven: stop before AVDD */
	if (!io_supply_on()) {
		return false;
	}
	sleep_ms(3);
	gpio_output(PIN_AVDD, true, PULL_NONE, 2);
	sleep_ms(15);
	return true;
}

static void
use_engine_pins(void)
{
	gpio_configure(PIN_MISO, PIN_FUNCTION_SPI, PULL_NONE, 6, false);
	gpio_configure(PIN_MOSI, PIN_FUNCTION_SPI, PULL_NONE, 6, false);
	gpio_configure(PIN_CLK, PIN_FUNCTION_SPI, PULL_NONE, 6, false);
	gpio_configure(PIN_CS, PIN_FUNCTION_SPI, PULL_NONE, 6, false);
}

static const char *
chip_name(void)
{
	return touch_chip == TOUCH_FOCALTECH ? "focaltech" : "goodix";
}

static bool
bring_up(void)
{
	const char *problem = "its clocks cannot be turned on";

	show_state("as found");
	touch_chip = touch_chip_detect();
	printf("goodix: ID gpio96=%u gpio68=%u: selected %s\n", gpio_get(PIN_GOODIX_ID),
	    gpio_get(PIN_FOCALTECH_ID), chip_name());
	if (!power_on()) {
		printf("goodix: the I/O supply is not on; the chip is left alone\n");
		return false;
	}

	if (clocks_on()) {
		printf("goodix: serial engine: firmware %#x, interface %#x, parameters %#x, source clock %u Hz (select %u)\n",
		    rd(GENI_FW_REVISION_RO), rd(GENI_IF_DISABLE_RO), rd(SE_HW_PARAM_0), se_source_hz, se_clock_select);
		uint32_t spi_hz = 1000000;

		/* boot-arg touch_spi_hz=N: the bus at N Hz (Android's device tree gives the chip 5 MHz) */
		PE_parse_boot_argn("touch_spi_hz", &spi_hz, sizeof(spi_hz));
		if (spi_hz < 100000 || spi_hz > 10000000) {
			spi_hz = 1000000;
		}
		printf("goodix: the bus at %u Hz\n", spi_hz);
		problem = geni_spi_init(spi_hz);
	}
	if (problem == NULL) {
		use_engine_pins();
		spi_transfer = geni_transfer;
		show_state("powered, on the serial engine");
		if (chip_probe(true)) {
			return true;
		}
		printf("goodix: %s did not answer through the serial engine; trying the pins by hand\n", chip_name());
	} else {
		printf("goodix: %s; driving the pins by hand\n", problem);
	}

	/* by hand; which of the two data pins is the input is not written down anywhere, so try both */
	spi_transfer = bitbang_transfer;
	for (int attempt = 0; attempt < 2; attempt++) {
		bitbang_in = attempt == 0 ? PIN_MISO : PIN_MOSI;
		bitbang_out = attempt == 0 ? PIN_MOSI : PIN_MISO;
		bitbang_init();
		if (chip_probe(true)) {
			printf("goodix: %s answers on pins driven by hand (data in on gpio%u)\n", chip_name(), bitbang_in);
			return true;
		}
	}
	show_state("no answer");
	return false;
}

/*
 * Spacewar stages 1 and 2 inspect existing state without writes. Stage 3
 * separately guards CPU0-only supply votes through an adopted idle ACTIVE1,
 * holding reset low. No stage reaches SPI, firmware upload or report polling.
 */
#define SW_RSC_ID                0x00u
#define SW_RSC_CONFIG            0x0cu
#define SW_RSC_TCS_BASE          0x0d00u
#define SW_RSC_TCS_STRIDE        0x02a0u
#define SW_RSC_ACTIVE_COUNT      2u
#define SW_RSC_DT_TCS_COUNT      8u
#define SW_RSC_MAX_COMMANDS      16u

struct spacewar_rsc_layout {
	const char *name;
	uint32_t command_stride, wait, control, status, enable;
	uint32_t message, address, data, command_status;
};

static const struct spacewar_rsc_layout spacewar_rsc_v2 = {
	"v2", 0x14, 0x10, 0x14, 0x18, 0x1c, 0x30, 0x34, 0x38, 0x3c
};
static const struct spacewar_rsc_layout spacewar_rsc_v3 = {
	"v3", 0x18, 0x20, 0x24, 0x28, 0x2c, 0x34, 0x38, 0x3c, 0x40
};

static bool
spacewar_rsc_read(uint32_t offset, uint32_t *value)
{
	if ((offset & 3) != 0 || offset > RSC_SIZE - sizeof(*value)) {
		printf("spacewar-touch: RSC read outside mapped window: offset %#x\n", offset);
		return false;
	}
	*value = rd(rsc + offset);
	return true;
}

static bool
spacewar_rsc_inspect(void)
{
	const struct spacewar_rsc_layout *layout;
	uint32_t id, major, minor, config, max_tcs, commands, irq_enable, irq_status;

	printf("spacewar-touch: step 2: read AP RSC version at %#lx; inspection only\n", RSC_PHYS + SW_RSC_ID);
	if (!spacewar_rsc_read(SW_RSC_ID, &id)) {
		return false;
	}
	major = (id >> 16) & 0xff;
	minor = (id >> 8) & 0xff;
	printf("spacewar-touch: RSC id %#x, version %u.%u\n", id, major, minor);
	if (id == 0 || id == 0xffffffffu || (major != 2 && major != 3)) {
		printf("spacewar-touch: unsupported or invalid RSC version; no layout-dependent reads\n");
		return false;
	}
	layout = major == 3 ? &spacewar_rsc_v3 : &spacewar_rsc_v2;
	printf("spacewar-touch: next: read RSC parent/child configuration at %#lx\n", RSC_PHYS + SW_RSC_CONFIG);
	if (!spacewar_rsc_read(SW_RSC_CONFIG, &config)) {
		return false;
	}
	max_tcs = (config >> 12) & 0x3f;       /* DTB qcom,drv-id=2, six bits per DRV */
	commands = (config >> 27) & 0x1f;
	printf("spacewar-touch: RSC config %#x, DRV2 capacity %u TCS, %u commands each; layout %s stride %#x\n",
	    config, max_tcs, commands, layout->name, layout->command_stride);
	if (max_tcs < SW_RSC_DT_TCS_COUNT || max_tcs > 32 || commands < 3 || commands > SW_RSC_MAX_COMMANDS) {
		printf("spacewar-touch: RSC capacity does not match the reviewed DTB/layout; stopping\n");
		return false;
	}
	printf("spacewar-touch: next: read shared RSC IRQ state at %#lx\n", RSC_PHYS + SW_RSC_TCS_BASE);
	if (!spacewar_rsc_read(SW_RSC_TCS_BASE, &irq_enable) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE + 4, &irq_status)) {
		return false;
	}
	printf("spacewar-touch: RSC IRQ enable %#x status %#x; DTB ACTIVE slots 0,1\n", irq_enable, irq_status);
	for (uint32_t slot = 0; slot < SW_RSC_ACTIVE_COUNT; slot++) {
		uint32_t base = SW_RSC_TCS_BASE + slot * SW_RSC_TCS_STRIDE;
		uint32_t control, status, enabled, wait, valid_mask = (1u << commands) - 1;

		printf("spacewar-touch: next: read ACTIVE slot %u at %#lx\n", slot, RSC_PHYS + base);
		if (!spacewar_rsc_read(base + layout->control, &control) ||
		    !spacewar_rsc_read(base + layout->status, &status) ||
		    !spacewar_rsc_read(base + layout->enable, &enabled) ||
		    !spacewar_rsc_read(base + layout->wait, &wait)) {
			return false;
		}
		printf("spacewar-touch: ACTIVE %u control %#x status %#x command-enable %#x wait %#x\n",
		    slot, control, status, enabled, wait);
		if ((enabled & ~valid_mask) != 0) {
			printf("spacewar-touch: ACTIVE %u has command bits beyond reported capacity; leave unchanged\n", slot);
			return false;
		}
		if (enabled != 0 || (control & (TCS_CONTROL_ENABLE | TCS_CONTROL_TRIGGER)) != 0) {
			printf("spacewar-touch: ACTIVE %u contains enabled state; leave unchanged\n", slot);
		}
		for (uint32_t command = 0; command < commands; command++) {
			uint32_t offset, message, address, data, command_status;

			/* First three slots describe the proposed vote; also show any enabled slot. */
			if (command >= 3 && !(enabled & (1u << command))) {
				continue;
			}
			offset = base + command * layout->command_stride;
			if (!spacewar_rsc_read(offset + layout->message, &message) ||
			    !spacewar_rsc_read(offset + layout->address, &address) ||
			    !spacewar_rsc_read(offset + layout->data, &data) ||
			    !spacewar_rsc_read(offset + layout->command_status, &command_status)) {
				return false;
			}
			printf("spacewar-touch: ACTIVE %u CMD%u message %#x address %#x data %#x status %#x\n",
			    slot, command, message, address, data, command_status);
		}
	}
	printf("spacewar-touch: step 2 snapshot complete; inspection made no writes\n");
	return true;
}

/* One power command at a time through ACTIVE1. Live SMP touch holds the
 * exclusive CPU0 firmware lease; the legacy probe runs on CPU0 alone.
 */
#define SW_POWER_SLOT           1u
#define SW_POWER_IRQ            (1u << SW_POWER_SLOT)
#define SW_POWER_BASE           (SW_RSC_TCS_BASE + SW_RSC_TCS_STRIDE * SW_POWER_SLOT)
#define SW_POWER_TIMEOUT_US     20000u
#define SW_POWER_POLLS          2000u
#define SW_POWER_CMD_DONE       ((1u << 8) | (1u << 16))

static bool spacewar_power_poisoned;

struct spacewar_slot1_state {
	uint32_t control, message, address, data;
	bool irq_enabled;
};

struct spacewar_slot0_state {
	uint32_t control, status, wait, enable, command[SW_RSC_MAX_COMMANDS][3];
	uint32_t irq_enable, irq_status;
};

static uint64_t
spacewar_power_mpidr(void)
{
	return __builtin_arm_rsr64("MPIDR_EL1");
}

static void
spacewar_power_io_barrier(void)
{
	__asm__ volatile("dsb sy" ::: "memory");
}

#include "hakios.spacewar.lease.inc"

static bool
spacewar_power_cpu_safe(void)
{
	if (touch_live) {
		return spacewar_live_held() && spacewar_live_cpu_safe();
	}
	uint32_t cpus = 0, bootcpu = 0, mask = 0, frequency = 0, buttons = 1;
	uint64_t mpidr = spacewar_power_mpidr();
	bool have_cpus = PE_parse_boot_argn("cpus", &cpus, sizeof(cpus));
	/* boot-arg cpumask=MASK: select loader CPU cores; the power probe rejects this override. */
	bool have_mask = PE_parse_boot_argn("cpumask", &mask, sizeof(mask));

	PE_parse_boot_argn("bootcpu", &bootcpu, sizeof(bootcpu));
	PE_parse_boot_argn("cpufreq", &frequency, sizeof(frequency));
	/* boot-arg buttons=1: enable physical key polling; power probes require buttons=0. */
	PE_parse_boot_argn("buttons", &buttons, sizeof(buttons));
	printf("spacewar-touch: power CPU guard MPIDR=%#llx, processors=%u online=%u, cpus=%u bootcpu=%u\n",
	    (unsigned long long)mpidr, processor_count, processor_avail_count, cpus, bootcpu);
	if (!HAKIOS_DEVICE_SPACEWAR || (mpidr & 0x000000ff00ffffffULL) != 0 ||
	    processor_count != 1 || processor_avail_count != 1 || !have_cpus || cpus != 1 ||
	    bootcpu != 0 || have_mask || frequency != 0 || buttons != 0) {
		printf("spacewar-touch: power requires this board on CPU0 alone, cpus=1, buttons=0, no mask/frequency override\n");
		return false;
	}
	return true;
}

static bool
spacewar_power_wait(uint32_t offset, uint32_t mask, uint32_t expected, const char *what)
{
	uint64_t deadline;
	uint32_t value = 0;

	clock_interval_to_deadline(SW_POWER_TIMEOUT_US, NSEC_PER_USEC, &deadline);
	for (uint32_t poll = 0; poll < SW_POWER_POLLS; poll++) {
		if (!spacewar_rsc_read(offset, &value)) {
			return false;
		}
		if ((value & mask) == expected) {
			return true;
		}
		if (mach_absolute_time() >= deadline) {
			break;
		}
		sleep_us(10);
	}
	printf("spacewar-touch: power timeout %s offset %#x value %#x mask %#x expected %#x\n",
	    what, offset, value, mask, expected);
	return false;
}

static bool
spacewar_power_write(uint32_t offset, uint32_t value, const char *what)
{
	bool slot1 = offset == SW_POWER_BASE + 0x10 || offset == SW_POWER_BASE + 0x14 ||
	    offset == SW_POWER_BASE + 0x1c || offset == SW_POWER_BASE + 0x30 ||
	    offset == SW_POWER_BASE + 0x34 || offset == SW_POWER_BASE + 0x38;

	if (!slot1) {
		printf("spacewar-touch: refused power write outside owned slot1 offset %#x\n", offset);
		return false;
	}
	wr(rsc + offset, value);
	return spacewar_power_wait(offset, 0xffffffffu, value, what);
}

static bool
spacewar_power_irq_enable(bool enabled)
{
	uint32_t before, after, value;

	if (!spacewar_rsc_read(SW_RSC_TCS_BASE, &before)) {
		return false;
	}
	value = (before & ~SW_POWER_IRQ) | (enabled ? SW_POWER_IRQ : 0);
	wr(rsc + SW_RSC_TCS_BASE, value);
	if (!spacewar_power_wait(SW_RSC_TCS_BASE, SW_POWER_IRQ, enabled ? SW_POWER_IRQ : 0, "owned IRQ enable") ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE, &after) || ((before ^ after) & ~SW_POWER_IRQ) != 0) {
		printf("spacewar-touch: IRQ enable changed outside owned slot1 bit\n");
		return false;
	}
	return true;
}

static bool
spacewar_power_preflight(struct spacewar_slot1_state *saved)
{
	uint32_t id, config, control, status, enabled, wait, irq_enable, irq_status;

	if (!spacewar_rsc_read(SW_RSC_ID, &id)) {
		return false;
	}
	if (spacewar_power_poisoned || id != 0x00020300u) {
		printf("spacewar-touch: power requires the observed RSC v2.3/configuration; no slot access\n");
		return false;
	}
	if (!spacewar_rsc_read(SW_RSC_CONFIG, &config) || config != 0x800c8104u) {
		printf("spacewar-touch: power requires the observed RSC v2.3/configuration; no slot access\n");
		return false;
	}
	if (!spacewar_rsc_read(SW_POWER_BASE + 0x14, &control) ||
	    !spacewar_rsc_read(SW_POWER_BASE + 0x18, &status) ||
	    !spacewar_rsc_read(SW_POWER_BASE + 0x1c, &enabled) ||
	    !spacewar_rsc_read(SW_POWER_BASE + 0x10, &wait) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE, &irq_enable) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE + 4, &irq_status)) {
		return false;
	}
	printf("spacewar-touch: power slot1 preflight id=%#x cfg=%#x ctrl=%#x status=%#x enable=%#x wait=%#x irq=%#x/%#x\n",
	    id, config, control, status, enabled, wait, irq_enable, irq_status);
	/* U-Boot enables both active TCS IRQs; Mu leaves slot1's IRQ disabled.
	 * An enabled but idle slot is usable only while the live lease parks
	 * firmware on CPU0. Pending or busy work must never be adopted.
	 */
	if ((control & ~TCS_CONTROL_ENABLE) != 0 || status != 1 || enabled != 0 || wait != 0 ||
	    (irq_status & SW_POWER_IRQ) != 0 ||
	    ((irq_enable & SW_POWER_IRQ) != 0 && (!touch_live || !spacewar_power_cpu_safe()))) {
		printf("spacewar-touch: power slot1 is not the reviewed idle v2.3 state; no request sent\n");
		return false;
	}
	saved->control = control;
	saved->irq_enabled = (irq_enable & SW_POWER_IRQ) != 0;
	if (!spacewar_rsc_read(SW_POWER_BASE + 0x30, &saved->message) ||
	    !spacewar_rsc_read(SW_POWER_BASE + 0x34, &saved->address) ||
	    !spacewar_rsc_read(SW_POWER_BASE + 0x38, &saved->data)) {
		return false;
	}
	if (saved->message != 0 || saved->address != 0 || saved->data != 0) {
		printf("spacewar-touch: power slot1 CMD0 has unexpected retained data; no request sent\n");
		return false;
	}
	return true;
}

static bool
spacewar_power_slot0_snapshot(struct spacewar_slot0_state *saved)
{
	if (!spacewar_rsc_read(SW_RSC_TCS_BASE + 0x14, &saved->control) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE + 0x18, &saved->status) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE + 0x10, &saved->wait) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE + 0x1c, &saved->enable) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE, &saved->irq_enable) ||
	    !spacewar_rsc_read(SW_RSC_TCS_BASE + 4, &saved->irq_status)) {
		return false;
	}
	for (uint32_t command = 0; command < SW_RSC_MAX_COMMANDS; command++) {
		uint32_t base = SW_RSC_TCS_BASE + command * 0x14;

		if (!spacewar_rsc_read(base + 0x30, &saved->command[command][0]) ||
		    !spacewar_rsc_read(base + 0x34, &saved->command[command][1]) ||
		    !spacewar_rsc_read(base + 0x38, &saved->command[command][2])) {
			return false;
		}
	}
	return true;
}

static bool
spacewar_power_slot0_unchanged(const struct spacewar_slot0_state *saved)
{
	struct spacewar_slot0_state now;

	if (!spacewar_power_slot0_snapshot(&now) || now.control != saved->control || now.status != 1 ||
	    saved->status != 1 || now.wait != saved->wait ||
	    now.enable != saved->enable || ((now.irq_enable ^ saved->irq_enable) & ~SW_POWER_IRQ) != 0 ||
	    ((now.irq_status ^ saved->irq_status) & ~SW_POWER_IRQ) != 0 ||
	    memcmp(now.command, saved->command, sizeof(now.command)) != 0) {
		printf("spacewar-touch: retained slot0 or foreign IRQ state changed; stopping without restoring it\n");
		return false;
	}
	return true;
}

static bool
spacewar_power_slot0_no_conflict(const struct spacewar_slot0_state *saved, uint32_t address)
{
	if (saved->status != 1 || (saved->enable & 0xffff0000u) != 0) {
		printf("spacewar-touch: retained slot0 is not the reviewed idle/capacity state; leave unchanged\n");
		return false;
	}
	for (uint32_t command = 0; command < SW_RSC_MAX_COMMANDS; command++) {
		if ((saved->enable & (1u << command)) != 0 && saved->command[command][1] == address) {
			printf("spacewar-touch: retained slot0 CMD%u already targets %#x; no conflicting request sent\n", command, address);
			return false;
		}
	}
	return true;
}

/* Caller holds hakios_rsc_lock. Any uncertainty poisons this probe; no cleanup or retry follows it. */
static bool
spacewar_power_command(uint32_t address, uint32_t data, const struct spacewar_slot0_state *slot0)
{
	struct spacewar_slot1_state saved;
	uint32_t irq_status, command_status = 0;

	if (!spacewar_power_preflight(&saved) || !spacewar_power_slot0_unchanged(slot0) ||
	    !spacewar_power_slot0_no_conflict(slot0, address)) {
		spacewar_power_poisoned = true;
		printf("spacewar-touch: power transaction guard changed; probe poisoned, no retry or rail rollback\n");
		return false;
	}
	printf("spacewar-touch: power slot1 request address=%#x data=%#x\n", address, data);
	if (!spacewar_power_write(SW_POWER_BASE + 0x14, saved.control & ~TCS_CONTROL_TRIGGER, "clear trigger") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x14, 0, "clear enable") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x10, 1, "command wait") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x30, MSGID_WRITE_4_BYTES | MSGID_RESPONSE, "message") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x34, address, "address") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x38, data, "data") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x1c, 1, "command enable") ||
	    !spacewar_power_irq_enable(true) || !spacewar_rsc_read(SW_RSC_TCS_BASE + 4, &irq_status) ||
	    (irq_status & SW_POWER_IRQ) != 0 ||
	    !spacewar_power_write(SW_POWER_BASE + 0x14, TCS_CONTROL_ENABLE, "AMC enable")) {
		goto uncertain;
	}
	spacewar_power_io_barrier();
	if (!spacewar_power_write(SW_POWER_BASE + 0x14, TCS_CONTROL_ENABLE | TCS_CONTROL_TRIGGER, "AMC trigger") ||
	    !spacewar_power_wait(SW_RSC_TCS_BASE + 4, SW_POWER_IRQ, SW_POWER_IRQ, "completion IRQ") ||
	    !spacewar_power_wait(SW_POWER_BASE + 0x3c, SW_POWER_CMD_DONE, SW_POWER_CMD_DONE, "issued and completed") ||
	    !spacewar_power_wait(SW_POWER_BASE + 0x18, 0xffffffffu, 1, "idle status") ||
	    !spacewar_rsc_read(SW_POWER_BASE + 0x3c, &command_status)) {
		goto uncertain;
	}
	printf("spacewar-touch: power slot1 acknowledged address=%#x data=%#x status=%#x\n", address, data, command_status);
	if (!spacewar_power_write(SW_POWER_BASE + 0x14, TCS_CONTROL_ENABLE, "clear completed trigger") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x14, 0, "disable completed slot") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x1c, 0, "disable completed command") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x10, 0, "clear owned wait")) {
		goto uncertain;
	}
	/* This is our fresh, validated completion. Never acknowledge any other slot. */
	wr(rsc + SW_RSC_TCS_BASE + 8, SW_POWER_IRQ);
	if (!spacewar_power_wait(SW_RSC_TCS_BASE + 4, SW_POWER_IRQ, 0, "acknowledge owned IRQ") ||
	    !spacewar_power_irq_enable(false) ||
	    !spacewar_power_write(SW_POWER_BASE + 0x30, saved.message, "restore message") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x34, saved.address, "restore address") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x38, saved.data, "restore data") ||
	    !spacewar_power_write(SW_POWER_BASE + 0x14, saved.control, "restore idle control") ||
	    !spacewar_power_irq_enable(saved.irq_enabled) ||
	    !spacewar_power_slot0_unchanged(slot0)) {
		goto uncertain;
	}
	return true;
uncertain:
	spacewar_power_poisoned = true;
	{
		uint32_t control = 0, status = 0, enabled = 0, wait = 0, irq = 0, cmd = 0;

		(void)spacewar_rsc_read(SW_POWER_BASE + 0x14, &control);
		(void)spacewar_rsc_read(SW_POWER_BASE + 0x18, &status);
		(void)spacewar_rsc_read(SW_POWER_BASE + 0x1c, &enabled);
		(void)spacewar_rsc_read(SW_POWER_BASE + 0x10, &wait);
		(void)spacewar_rsc_read(SW_RSC_TCS_BASE + 4, &irq);
		(void)spacewar_rsc_read(SW_POWER_BASE + 0x3c, &cmd);
		printf("spacewar-touch: power failure snapshot ctrl=%#x status=%#x enable=%#x wait=%#x irq=%#x cmd-status=%#x\n",
		    control, status, enabled, wait, irq, cmd);
	}
	printf("spacewar-touch: power slot1 state uncertain; probe poisoned, no retry or rail rollback; reset remains low\n");
	return false;
}

static bool
spacewar_power_probe(uint32_t io, uint32_t analog)
{
	struct spacewar_slot1_state idle;
	struct spacewar_slot0_state slot0;
	bool success = false;

	printf("spacewar-touch: step 3: power votes only; reset will stay low, no clocks or SPI\n");
	if (!spacewar_power_cpu_safe() || io != 0x42500u || analog != 0x40e00u) {
		printf("spacewar-touch: power CPU/resource guard refused; no power or pin writes\n");
		return false;
	}
	lck_mtx_lock(&hakios_rsc_lock);
	if (!spacewar_power_cpu_safe() || !spacewar_power_preflight(&idle) || !spacewar_power_slot0_snapshot(&slot0)) {
		goto out;
	}
	const uint32_t addresses[] = { io + VRM_VOLTAGE, io + VRM_MODE, io + VRM_ENABLE,
		analog + VRM_VOLTAGE, analog + VRM_MODE, analog + VRM_ENABLE };
	for (uint32_t i = 0; i < sizeof(addresses) / sizeof(addresses[0]); i++) {
		if (!spacewar_power_slot0_no_conflict(&slot0, addresses[i])) {
			goto out;
		}
	}
	printf("spacewar-touch: power preflight passed; preserve slot0, adopt idle slot1\n");
	gpio_output(PIN_RESET, false, PULL_UP, 8);
	sleep_ms(1);
	if ((rd(GPIO_CFG(PIN_RESET)) & 0x23f) != 0x203 || (rd(GPIO_IO(PIN_RESET)) & 3) != 0) {
		printf("spacewar-touch: reset105 low could not be verified; no regulator request sent\n");
		goto out;
	}
	if (!spacewar_power_command(io + VRM_VOLTAGE, 1800, &slot0) ||
	    !spacewar_power_command(io + VRM_MODE, VRM_MODE_LDO_HPM, &slot0) ||
	    !spacewar_power_command(io + VRM_ENABLE, 1, &slot0)) {
		goto out;
	}
	printf("spacewar-touch: L8 I/O 1.8 V votes acknowledged; reset low\n");
	sleep_ms(1);
	if (!spacewar_power_command(analog + VRM_VOLTAGE, 3300, &slot0) ||
	    !spacewar_power_command(analog + VRM_MODE, VRM_MODE_LDO_HPM, &slot0) ||
	    !spacewar_power_command(analog + VRM_ENABLE, 1, &slot0)) {
		goto out;
	}
	if (!spacewar_power_slot0_unchanged(&slot0) || (rd(GPIO_IO(PIN_RESET)) & 3) != 0) {
		goto out;
	}
	printf("spacewar-touch: L3 analog 3.3 V votes acknowledged; slot0 retained state preserved\n");
	printf("spacewar-touch: step 3 complete; reset105 low; clocks/SPI not configured yet\n");
	success = true;
out:
	lck_mtx_unlock(&hakios_rsc_lock);
	return success;
}


static bool spacewar_spi_gates_probe(void);
static void spacewar_spi_identity_probe(void);

static void
spacewar_touch_probe(void)
{
	uint32_t io, analog;

	printf("spacewar-touch: step 1: read existing state; inspection only, steps=%u\n", touch_steps);
	io = rpmh_address("ldoc8");
	analog = rpmh_address("ldoc3");
	printf("spacewar-touch: reset gpio105 cfg=%#x io=%#x; irq gpio81 cfg=%#x io=%#x\n",
	    rd(GPIO_CFG(PIN_RESET)), rd(GPIO_IO(PIN_RESET)), rd(GPIO_CFG(PIN_INT)), rd(GPIO_IO(PIN_INT)));
	printf("spacewar-touch: clock votes=%#x gates=%#x/%#x/%#x; ldoc8=%#x ldoc3=%#x\n",
	    rd(GCC_VOTE), rd(GCC_QUP1_M_AHB_CBCR), rd(GCC_QUP1_S_AHB_CBCR), rd(GCC_QUP1_SE5_CBCR), io, analog);
	if (io == 0 || analog == 0 || (io >> 16 & 0xf) != 4 || (analog >> 16 & 0xf) != 4 || io == analog) {
		printf("spacewar-touch: expected two distinct VRM resources ldoc8 and ldoc3; stopping\n");
		return;
	}
	if (touch_steps == 1) {
		printf("spacewar-touch: stopping after step 1; no pin, clock or regulator writes\n");
		return;
	}
	/* The live profile excludes firmware from the complete power/clock setup.
	 * Legacy CPU0 probes retain their original execution path. */
	if (!spacewar_live_acquire()) return;
	bool ready = spacewar_rsc_inspect() && touch_steps >= 3 && spacewar_power_probe(io, analog) &&
	    touch_steps >= 4 && spacewar_spi_gates_probe() && touch_steps >= 5;
	spacewar_live_release();
	if (ready) {
		spacewar_spi_identity_probe();
	}
}


/* Access clocks only: no serial-engine, root-rate, pinmux or FIFO writes. */
#define SW_SPI_GCC_MASK (VOTE_QUP1_CORE_2X | VOTE_QUP1_CORE | VOTE_QUP1_M_AHB | VOTE_QUP1_S_AHB | VOTE_QUP1_SE5)
static bool spacewar_spi_clocked, spacewar_spi_poisoned;

static bool
spacewar_spi_gcc_read(uint32_t offset, uint32_t *value)
{
	if ((offset & 3) != 0 || offset > GCC_SIZE - sizeof(*value)) {
		printf("spacewar-touch: SPI GCC offset outside mapping %#x\n", offset);
		return false;
	}
	*value = rd(gcc + offset);
	return true;
}

static bool
spacewar_spi_read(uint32_t offset, uint32_t *value)
{
	if (!spacewar_spi_clocked || (offset & 3) != 0 || offset > SE_SIZE - sizeof(*value)) {
		printf("spacewar-touch: refused unclocked/out-of-range SPI read %#x\n", offset);
		return false;
	}
	*value = rd(se + offset);
	return true;
}

struct spacewar_spi_snapshot {
	uint32_t status, m_control, s_control, m_command, s_command, m_clock, s_clock;
	uint32_t m_irq, s_irq, dma_tx_irq, dma_rx_irq, dma_debug, dma_mode, gsi;
	uint32_t tx_fifo, rx_fifo, dma_tx_len, dma_tx_in, dma_rx_len, dma_rx_in;
	uint32_t irq_enable, m_irq_enable, s_irq_enable, dma_tx_enable, dma_rx_enable;
	uint32_t protocol, fifo_disable, tx_parameter, rx_parameter, slave_enable;
};

static bool
spacewar_spi_snapshot(struct spacewar_spi_snapshot *state)
{
	static const uint32_t offsets[] = { 0x40, 0x604, 0x634, 0x600, 0x630, 0x48, 0x4c,
		0x610, 0x640, 0xc40, 0xd40, 0xe40, 0x258, 0xe18, 0x800, 0x804,
		0xc3c, 0xc54, 0xd3c, 0xd54, 0xe1c, 0x614, 0x644, 0xc48, 0xd48,
		0x68, 0x64, 0xe24, 0xe28, 0x2bc };
	uint32_t words[sizeof(offsets) / sizeof(offsets[0])];

	_Static_assert(sizeof(*state) == sizeof(offsets), "SPI snapshot must match its register table");
	for (uint32_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++) {
		printf("spacewar-touch: reading GENI offset=%#x\n", offsets[i]);
		if (!spacewar_spi_read(offsets[i], &words[i])) {
			return false;
		}
	}
	memcpy(state, words, sizeof(*state));
	printf("spacewar-touch: GENI status=%#x M/Scontrol=%#x/%#x commands=%#x/%#x serial-clocks=%#x/%#x slave=%#x\n",
	    state->status, state->m_control, state->s_control, state->m_command, state->s_command,
	    state->m_clock, state->s_clock, state->slave_enable);
	printf("spacewar-touch: GENI DMAdebug=%#x mode=%#x GSI=%#x FIFO=%#x/%#x IRQ=%#x/%#x DMAIRQ=%#x/%#x\n",
	    state->dma_debug, state->dma_mode, state->gsi, state->tx_fifo, state->rx_fifo,
	    state->m_irq, state->s_irq, state->dma_tx_irq, state->dma_rx_irq);
	printf("spacewar-touch: GENI DMA lengths=%#x/%#x/%#x/%#x IRQen=%#x/%#x/%#x/%#x/%#x FW=%#x FIFO-disable=%#x parameters=%#x/%#x\n",
	    state->dma_tx_len, state->dma_tx_in, state->dma_rx_len, state->dma_rx_in,
	    state->irq_enable, state->m_irq_enable, state->s_irq_enable, state->dma_tx_enable, state->dma_rx_enable,
	    state->protocol, state->fifo_disable, state->tx_parameter, state->rx_parameter);
	return true;
}


/* Nothing clk-branch.c skips HALT for declared HWCG branches; only m/s AHB have hwcg_bit1 here. */
static bool
spacewar_spi_gate_ready(uint32_t index, uint32_t value)
{
	if ((index == 2 || index == 3) && (value & (1u << 1))) {
		return true;
	}
	uint32_t masked = value & 0xf0000000u;
	return (masked & CLOCK_OFF) == 0 || masked == 0x20000000u;
}

static bool
spacewar_spi_gates_probe(void)
{
	static const uint32_t gates[] = { 0x23140, 0x23138, 0x18004, 0x18008, 0x185fc };
	struct spacewar_spi_snapshot state;
	uint32_t votes, after = 0, command, cfg, dfs, after_command = 0, after_cfg = 0, after_dfs = 0;
	uint32_t before_gates[5] = { 0 }, after_gates[5] = { 0 };
	bool success = false;

	printf("spacewar-touch: step 4: access clocks and read-only GENI state; reset held low\n");
	spacewar_spi_clocked = false;
	if (spacewar_spi_poisoned || !spacewar_power_cpu_safe() || (rd(GPIO_IO(PIN_RESET)) & 3) != 0 ||
	    (rd(GPIO_CFG(PIN_RESET)) & 0x23f) != 0x203) {
		printf("spacewar-touch: SPI CPU/reset guard refused\n");
		return false;
	}
	lck_mtx_lock(&hakios_rsc_lock);
	if (!spacewar_power_cpu_safe() || !spacewar_spi_gcc_read(0x52008, &votes) ||
	    !spacewar_spi_gcc_read(0x18600, &command) || !spacewar_spi_gcc_read(0x18604, &cfg) ||
	    !spacewar_spi_gcc_read(0x18614, &dfs) || (command & 1) != 0) {
		printf("spacewar-touch: SPI GCC preflight refused; no access-clock votes\n");
		goto out;
	}
	printf("spacewar-touch: SPI GCC preflight votes=%#x RCG command=%#x config=%#x DFS=%#x\n", votes, command, cfg, dfs);
	for (uint32_t i = 0; i < sizeof(gates) / sizeof(gates[0]); i++) {
		if (!spacewar_spi_gcc_read(gates[i], &before_gates[i])) {
			goto out;
		}
	}
	printf("spacewar-touch: SPI access gates before core2x/core/mAHB/sAHB/SE5=%#x/%#x/%#x/%#x/%#x\n",
	    before_gates[0], before_gates[1], before_gates[2], before_gates[3], before_gates[4]);
	/* Keep the inherited root rate, pinmux and all serial-engine state unchanged. */
	wr(GCC_VOTE, votes | SW_SPI_GCC_MASK);
	if (!spacewar_spi_gcc_read(0x52008, &after) || ((after ^ votes) & ~SW_SPI_GCC_MASK) != 0 ||
	    (after & SW_SPI_GCC_MASK) != SW_SPI_GCC_MASK) {
		goto uncertain;
	}
	for (uint32_t poll = 0; poll < 2000; poll++) {
		bool ready = true;
		for (uint32_t i = 0; i < sizeof(gates) / sizeof(gates[0]); i++) {
			uint32_t value;
			if (!spacewar_spi_gcc_read(gates[i], &value)) {
				goto uncertain;
			}
			after_gates[i] = value;
			ready &= spacewar_spi_gate_ready(i, value);
		}
		if (ready) {
			spacewar_spi_clocked = true;
			break;
		}
		sleep_us(10);
	}
	if (!spacewar_spi_clocked) {
		printf("spacewar-touch: SPI access-clock readiness failed; no SE reads\n");
		goto uncertain;
	}
	if (!spacewar_spi_gcc_read(0x52008, &after) || !spacewar_spi_gcc_read(0x18600, &after_command) ||
	    !spacewar_spi_gcc_read(0x18604, &after_cfg) || !spacewar_spi_gcc_read(0x18614, &after_dfs)) {
		goto uncertain;
	}
	printf("spacewar-touch: SPI GCC ready votes=%#x RCG command=%#x config=%#x DFS=%#x\n", after, after_command, after_cfg, after_dfs);
	spacewar_power_io_barrier();
	printf("spacewar-touch: access clocks prepared (HWCG-aware) core2x/core/mAHB/sAHB/SE5=%#x/%#x/%#x/%#x/%#x; first GENI read is SE+0x40\n",
	    after_gates[0], after_gates[1], after_gates[2], after_gates[3], after_gates[4]);
	if (!spacewar_spi_snapshot(&state) || !spacewar_spi_gcc_read(0x52008, &after) ||
	    !spacewar_spi_gcc_read(0x18600, &after_command) || !spacewar_spi_gcc_read(0x18604, &after_cfg) ||
	    !spacewar_spi_gcc_read(0x18614, &after_dfs)) {
		goto uncertain;
	}
	printf("spacewar-touch: SPI GCC after votes=%#x RCG command=%#x config=%#x DFS=%#x\n", after, after_command, after_cfg, after_dfs);
	/* Root-off and DFS level/config may change in hardware when access clocks run. */
	if (after != (votes | SW_SPI_GCC_MASK) || ((after_command ^ command) & 3) != 0 ||
	    ((after_dfs ^ dfs) & 1) != 0 ||
	    (rd(GPIO_IO(PIN_RESET)) & 3) != 0 || (rd(GPIO_CFG(PIN_RESET)) & 0x23f) != 0x203) {
		goto uncertain;
	}
	printf("spacewar-touch: GENI snapshot collected; SPI ownership and setup remain unverified\n");
	printf("spacewar-touch: step 4 complete; reset105 low; RCG, pinmux and SE not configured yet\n");
	success = true;
	goto out;
uncertain:
	/* Preserve exact post-vote evidence, including valid idle HWCG HALT states. */
	(void)spacewar_spi_gcc_read(0x52008, &after);
	(void)spacewar_spi_gcc_read(0x18600, &after_command);
	(void)spacewar_spi_gcc_read(0x18604, &after_cfg);
	(void)spacewar_spi_gcc_read(0x18614, &after_dfs);
	for (uint32_t i = 0; i < sizeof(gates) / sizeof(gates[0]); i++) {
		(void)spacewar_spi_gcc_read(gates[i], &after_gates[i]);
	}
	printf("spacewar-touch: SPI GCC stop votes=%#x RCG command=%#x config=%#x DFS=%#x\n", after, after_command, after_cfg, after_dfs);
	printf("spacewar-touch: SPI gates stop core2x/core/mAHB/sAHB/SE5=%#x/%#x/%#x/%#x/%#x\n",
	    after_gates[0], after_gates[1], after_gates[2], after_gates[3], after_gates[4]);
	spacewar_spi_poisoned = true;
	printf("spacewar-touch: SPI access probe stopped; no retry or clock rollback; reset stays low\n");
out:
	lck_mtx_unlock(&hakios_rsc_lock);
	return success;
}

/* First functional Spacewar transport: GPIO SPI, with the entire GENI state preserved. */
#include "hakios.spacewar.fw.h"
#include "hakios.spacewar.fw.inc"
#include "hakios.spacewar.ft12.h"
#include "hakios.spacewar.ft12.inc"
#include "hakios.spacewar.focaltech_fw.h"

/* DTB static touch GPII0: A00000 - EE layout offset10000 + context20000. Read only. */
#define SW_TOUCH_GPI_PHYS 0x00a10000UL
#define SW_TOUCH_GPI_SIZE 0x4000UL
static vm_offset_t spacewar_touch_gpi;
struct spacewar_gpi_snapshot {
	uint32_t channel[2][12]; /* config,length,base/RP/WP low/high,scratch0..3 */
	uint32_t event[14];
	uint32_t irq[6];
};
static struct spacewar_gpi_snapshot spacewar_gpi_expected;
static bool spacewar_gpi_checked;

static bool
spacewar_gpi_read32(uint32_t offset, uint32_t *value)
{
	if (!spacewar_touch_gpi || !spacewar_spi_clocked || (offset & 3) || offset > SW_TOUCH_GPI_SIZE - sizeof(*value)) {
		printf("spacewar-touch: refused unmapped/unclocked/out-of-range GPI read offset=%#x\n", offset);
		return false;
	}
	*value = rd(spacewar_touch_gpi + offset);
	return true;
}

static bool
spacewar_gpi_pair_stable(uint32_t offset, uint32_t low, uint32_t high)
{
	uint32_t first, current_low, second;
	if (!spacewar_gpi_read32(offset + 4, &first) || !spacewar_gpi_read32(offset, &current_low) ||
	    !spacewar_gpi_read32(offset + 4, &second) || first != second || first != high || current_low != low) {
		printf("spacewar-touch: GPI64 pointer changed/torn at offset=%#x\n", offset);
		return false;
	}
	return true;
}

static bool
spacewar_gpi_read(struct spacewar_gpi_snapshot *state, bool verbose)
{
	static const uint32_t channel_offsets[] = { 0, 4, 8, 0xc, 0x10, 0x14, 0x18, 0x1c, 0x60, 0x64, 0x68, 0x6c };
	static const uint32_t irq_offsets[] = { 0x3080, 0x3090, 0x3094, 0x30b0, 0x3100, 0x3118 };
	for (uint32_t channel = 0; channel < 2; channel++) {
		for (uint32_t index = 0; index < 12; index++) {
			uint32_t offset = 0x80 * channel + channel_offsets[index];
			if (verbose) printf("spacewar-touch: GPII0 reading channel%u offset=%#x\n", channel, offset);
			if (!spacewar_gpi_read32(offset, &state->channel[channel][index])) return false;
		}
		for (uint32_t index = 2; index <= 6; index += 2) {
			if (!spacewar_gpi_pair_stable(0x80 * channel + channel_offsets[index],
			    state->channel[channel][index], state->channel[channel][index + 1])) return false;
		}
	}
	for (uint32_t index = 0; index < 14; index++) {
		if (verbose) printf("spacewar-touch: GPII0 reading event offset=%#x\n", 0x1000 + 4 * index);
		if (!spacewar_gpi_read32(0x1000 + 4 * index, &state->event[index])) return false;
	}
	for (uint32_t index = 2; index <= 6; index += 2) {
		if (!spacewar_gpi_pair_stable(0x1000 + 4 * index, state->event[index], state->event[index + 1])) return false;
	}
	for (uint32_t index = 0; index < 6; index++) {
		if (verbose) printf("spacewar-touch: GPII0 reading IRQ offset=%#x\n", irq_offsets[index]);
		if (!spacewar_gpi_read32(irq_offsets[index], &state->irq[index])) return false;
	}
	return true;
}

static uint64_t
spacewar_gpi_pointer(const uint32_t *words)
{
	return (uint64_t)words[0] | (uint64_t)words[1] << 32;
}

static void
spacewar_gpi_report(const struct spacewar_gpi_snapshot *state)
{
	for (uint32_t channel = 0; channel < 2; channel++) {
		const uint32_t *c = state->channel[channel];
		printf("spacewar-touch: GPII0 CH%u cfg=%#x state=%u EE=%u length=%#x base=%#llx RP=%#llx WP=%#llx scratch=%#x/%#x/%#x/%#x\n",
		    channel, c[0], (c[0] >> 20) & 0xf, (c[0] >> 4) & 0xf, c[1],
		    (unsigned long long)spacewar_gpi_pointer(c + 2), (unsigned long long)spacewar_gpi_pointer(c + 4),
		    (unsigned long long)spacewar_gpi_pointer(c + 6), c[8], c[9], c[10], c[11]);
	}
	printf("spacewar-touch: GPII0 EVENT cfg=%#x state=%u EE=%u length=%#x base=%#llx RP=%#llx WP=%#llx\n",
	    state->event[0], (state->event[0] >> 20) & 0xf, (state->event[0] >> 4) & 0xf, state->event[1],
	    (unsigned long long)spacewar_gpi_pointer(state->event + 2), (unsigned long long)spacewar_gpi_pointer(state->event + 4),
	    (unsigned long long)spacewar_gpi_pointer(state->event + 6));
	printf("spacewar-touch: GPII0 IRQ type/channel/event/IEOB/global/general=%#x/%#x/%#x/%#x/%#x/%#x\n",
	    state->irq[0], state->irq[1], state->irq[2], state->irq[3], state->irq[4], state->irq[5]);
}

static bool
spacewar_gpi_ring_valid(const uint32_t *context)
{
	uint64_t base = spacewar_gpi_pointer(context + 2), rp = spacewar_gpi_pointer(context + 4), wp = spacewar_gpi_pointer(context + 6);
	uint32_t length = context[1];
	return length >= 32 && length <= 0xffff && (length & (length - 1)) == 0 &&
	       base && (base & (length - 1)) == 0 && base <= UINT64_MAX - length &&
	       rp >= base && rp < base + length && wp >= base && wp < base + length &&
	       (rp & 15) == 0 && (wp & 15) == 0;
}

static bool
spacewar_gpi_idle(const struct spacewar_gpi_snapshot *state)
{
	for (uint32_t channel = 0; channel < 2; channel++) {
		const uint32_t *c = state->channel[channel];
		uint32_t status = (c[0] >> 20) & 0xf;
		if (status != 0 && status != 1 && status != 3) {
			printf("spacewar-touch: GPII0 CH%u state%u is running/stopping/error/unknown; no GPIO claim\n", channel, status);
			return false;
		}
		if (status == 0) continue; /* NOT_ALLOCATED cannot consume retained ring fields without a new allocation/start. */
		uint32_t expected_scratch = (channel == 0 ? 0x10000u : 0) | 0x15;
		uint32_t identity = (c[0] >> 8) & 0x1f;
		if ((c[0] >> 24) != 16 || (c[0] & 7) != 2 || ((c[0] >> 3) & 1) != (channel == 0) ||
		    ((c[0] >> 14) & 0x1f) != 0 || ((c[0] >> 4) & 0xf) != 0 || c[8] != expected_scratch ||
		    (identity != 0 && identity != channel)) {
			printf("spacewar-touch: GPII0 CH%u allocated owner/layout mismatch (requires touch SE5/proto1/pair, GPI16/EE0/event0)\n", channel);
			return false;
		}
		if (!spacewar_gpi_ring_valid(c) || spacewar_gpi_pointer(c + 4) != spacewar_gpi_pointer(c + 6)) {
			printf("spacewar-touch: GPII0 CH%u invalid ring or queued TREs (RP!=WP); no GPIO claim\n", channel);
			return false;
		}
	}
	uint32_t event_state = (state->event[0] >> 20) & 0xf;
	if (event_state > 1 || (event_state == 1 &&
	    ((state->event[0] >> 24) != 16 || (state->event[0] & 0xf) != 2 ||
	    ((state->event[0] >> 4) & 0xf) != 0 || ((state->event[0] >> 8) & 0xff) != 0 || !spacewar_gpi_ring_valid(state->event)))) {
		printf("spacewar-touch: GPII0 event ring state/owner/layout unsupported\n");
		return false;
	}
	/* Event WP returns free credits; unlike transfer rings, event RP==WP is not an emptiness test. */
	for (uint32_t index = 0; index < 6; index++) {
		if (state->irq[index]) {
			printf("spacewar-touch: GPII0 pending IRQ class%u=%#x; no clears or GPIO claim\n", index, state->irq[index]);
			return false;
		}
	}
	return true;
}

static bool
spacewar_gpi_adopt(void)
{
	struct spacewar_gpi_snapshot after = { 0 };
	spacewar_gpi_checked = false;
	memset(&spacewar_gpi_expected, 0, sizeof(spacewar_gpi_expected));
	printf("spacewar-touch: inspect DT static GPII0 window %#lx/%#lx, first readonly CH0 context; no commands or doorbells\n", SW_TOUCH_GPI_PHYS, SW_TOUCH_GPI_SIZE);
	if (!spacewar_touch_gpi) spacewar_touch_gpi = ml_io_map(SW_TOUCH_GPI_PHYS, SW_TOUCH_GPI_SIZE);
	if (!spacewar_touch_gpi || !spacewar_gpi_read(&spacewar_gpi_expected, true)) return false;
	spacewar_gpi_report(&spacewar_gpi_expected);
	if (!spacewar_gpi_idle(&spacewar_gpi_expected)) return false;
	sleep_ms(1);
	if (!spacewar_gpi_read(&after, false) || memcmp(&after, &spacewar_gpi_expected, sizeof(after)) != 0) {
		spacewar_gpi_report(&after);
		printf("spacewar-touch: GPII0 context changed during idle stability interval\n");
		return false;
	}
	spacewar_gpi_checked = true;
	printf("spacewar-touch: GPII0 nonrunning and transfer queues empty; freeze all44 words before/after every GPIO frame\n");
	return true;
}

static bool
spacewar_gpi_unchanged(void)
{
	struct spacewar_gpi_snapshot now = { 0 };
	if (!spacewar_gpi_checked || !spacewar_gpi_read(&now, false) || memcmp(&now, &spacewar_gpi_expected, sizeof(now)) != 0) {
		if (spacewar_gpi_checked) spacewar_gpi_report(&now);
		printf("spacewar-touch: GPII0 state changed; no further GPIO transaction\n");
		return false;
	}
	return true;
}

static struct spacewar_spi_snapshot spacewar_gpio_se_state;
static bool spacewar_gpio_configured, spacewar_gpio_cpu_validated;
static uint32_t spacewar_fw_written;

static bool
spacewar_gpio_cpu_safe(void)
{
	/* Boot arguments were checked by power/setup and remain immutable; hardware counts stay fresh. */
	if (touch_live) return spacewar_gpio_cpu_validated && spacewar_live_cpu_safe();
	return spacewar_gpio_cpu_validated && HAKIOS_DEVICE_SPACEWAR &&
	       (spacewar_power_mpidr() & 0x000000ff00ffffffULL) == 0 &&
	       processor_count == 1 && processor_avail_count == 1;
}

static bool
spacewar_gpio_se_read(struct spacewar_spi_snapshot *state)
{
	static const uint32_t offsets[] = { 0x40, 0x604, 0x634, 0x600, 0x630, 0x48, 0x4c,
		0x610, 0x640, 0xc40, 0xd40, 0xe40, 0x258, 0xe18, 0x800, 0x804,
		0xc3c, 0xc54, 0xd3c, 0xd54, 0xe1c, 0x614, 0x644, 0xc48, 0xd48,
		0x68, 0x64, 0xe24, 0xe28, 0x2bc };
	uint32_t words[sizeof(offsets) / sizeof(offsets[0])];
	_Static_assert(sizeof(*state) == sizeof(offsets), "GPIO SPI guard must cover the complete SE snapshot");
	for (uint32_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++) {
		if (!spacewar_spi_read(offsets[i], &words[i])) {
			return false;
		}
	}
	memcpy(state, words, sizeof(*state));
	return true;
}

static bool
spacewar_gpio_quiescent(const struct spacewar_spi_snapshot *s)
{
	uint32_t protocol = (s->protocol >> 8) & 0xff;
	if ((s->protocol != 0 && protocol != 1) || s->slave_enable != 0 ||
	    (s->m_clock & ~0xfff1u) != 0 || s->s_clock != 0) {
		printf("spacewar-touch: GPIO SPI refused: protocol/slave/serial-clock owner (FW=%#x slave=%#x clocks=%#x/%#x)\n",
		    s->protocol, s->slave_enable, s->m_clock, s->s_clock);
		return false;
	}
	/* Mode/routing is configuration; actual SE and external GPI activity are checked independently. */
	if (!((s->dma_mode == 0 && s->gsi == 0) || (s->dma_mode == 1 && (s->gsi == 0 || s->gsi == 0xf)))) {
		printf("spacewar-touch: GPIO SPI refused: unsupported retained DMA/GSI mode=%#x routes=%#x\n", s->dma_mode, s->gsi);
		return false;
	}
	if ((s->status & 0x1001) != 0 || s->m_control != 0 || s->s_control != 0 ||
	    (s->m_command != 0 && s->m_command != SPI_FULL_DUPLEX) || s->s_command != 0) {
		printf("spacewar-touch: GPIO SPI refused: active/unowned engine status=%#x controls=%#x/%#x commands=%#x/%#x\n",
		    s->status, s->m_control, s->s_control, s->m_command, s->s_command);
		return false;
	}
	if (s->dma_debug != 0 || s->tx_fifo != 0 || (s->rx_fifo & 0x0fffffffu) != 0 ||
	    s->dma_tx_len != 0 || s->dma_tx_in != 0 || s->dma_rx_len != 0 || s->dma_rx_in != 0) {
		printf("spacewar-touch: GPIO SPI refused: DMA/FIFO activity debug=%#x FIFO=%#x/%#x lengths=%#x/%#x/%#x/%#x\n",
		    s->dma_debug, s->tx_fifo, s->rx_fifo, s->dma_tx_len, s->dma_tx_in, s->dma_rx_len, s->dma_rx_in);
		return false;
	}
	if (s->m_irq || s->s_irq || s->dma_tx_irq || s->dma_rx_irq) {
		printf("spacewar-touch: GPIO SPI refused: pending SE/DMA IRQ=%#x/%#x/%#x/%#x\n", s->m_irq, s->s_irq, s->dma_tx_irq, s->dma_rx_irq);
		return false;
	}
	if (((s->irq_enable & 4) && (s->m_irq_enable & ~0x3300003fu)) ||
	    ((s->irq_enable & 8) && (s->s_irq_enable & ~0x0300003fu))) {
		printf("spacewar-touch: routed asynchronous GENI events have an unowned IRQ handler\n");
		return false;
	}
	printf("spacewar-touch: SE idle; preserve DMA mode=%#x GSI routes=%#x FIFO-disable=%#x; external GPI ownership still required\n",
	    s->dma_mode, s->gsi, s->fifo_disable);
	return true;
}

static bool
spacewar_gpio_setup(void)
{
	struct spacewar_spi_snapshot current;
	uint32_t pin_config[4], pin_value[4];
	printf("spacewar-touch: step5: guarded dedicated-GPIO SPI mode0, no SE initialization or firmware assumptions\n");
	for (uint32_t pin = PIN_MISO; pin <= PIN_CS; pin++) {
		uint32_t index = pin - PIN_MISO;
		pin_config[index] = rd(GPIO_CFG(pin)); pin_value[index] = rd(GPIO_IO(pin));
		printf("spacewar-touch: GPIO SPI pin%u config=%#x value=%#x\n", pin, pin_config[index], pin_value[index]);
	}
	bool previous_owned = pin_config[0] == 0 && pin_config[1] == 0x280 && pin_config[2] == 0x280 &&
	    pin_config[3] == 0x280 && (pin_value[3] & 2) != 0 && (pin_value[2] & 2) == 0;
	if (spacewar_spi_poisoned || !spacewar_spi_clocked || !spacewar_power_cpu_safe() ||
	    (rd(GPIO_CFG(PIN_RESET)) & 0x23f) != 0x203 || (rd(GPIO_IO(PIN_RESET)) & 3) != 0 ||
	    !spacewar_spi_snapshot(&spacewar_gpio_se_state) || !spacewar_gpio_quiescent(&spacewar_gpio_se_state)) {
		return false;
	}
	for (uint32_t pin = PIN_MISO; pin <= PIN_CS; pin++) {
		uint32_t config = pin_config[pin - PIN_MISO], function = (config >> 2) & 0xf;
		if (function > PIN_FUNCTION_SPI || (function == 0 && (config & (1u << 9)) != 0 && !previous_owned)) {
			printf("spacewar-touch: dedicated SPI pin has an unexplained owner\n");
			return false;
		}
	}
	if (!spacewar_gpi_adopt() || !spacewar_gpio_se_read(&current) ||
	    memcmp(&current, &spacewar_gpio_se_state, sizeof(current)) != 0 || !spacewar_gpi_unchanged()) {
		return false;
	}
	if (previous_owned) printf("spacewar-touch: exact prior owned GPIO mode0 pin pattern accepted after fresh SE/GPI guards\n");
	/* Latch output levels before enabling GPIO output, so CS starts high and CLK low. */
	gpio_output(PIN_CS, true, PULL_NONE, 6);
	gpio_output(PIN_CLK, false, PULL_NONE, 6);
	gpio_output(PIN_MOSI, false, PULL_NONE, 6);
	gpio_configure(PIN_MISO, 0, PULL_NONE, 2, false);
	spacewar_power_io_barrier();
	if (rd(GPIO_CFG(PIN_CS)) != 0x280 || (rd(GPIO_IO(PIN_CS)) & 2) == 0 ||
	    rd(GPIO_CFG(PIN_CLK)) != 0x280 || (rd(GPIO_IO(PIN_CLK)) & 2) != 0 ||
	    rd(GPIO_CFG(PIN_MOSI)) != 0x280 || rd(GPIO_CFG(PIN_MISO)) != 0 ||
	    !spacewar_gpio_se_read(&current) || memcmp(&current, &spacewar_gpio_se_state, sizeof(current)) != 0 ||
	    !spacewar_gpi_unchanged()) {
		return false;
	}
	spacewar_gpio_cpu_validated = spacewar_gpio_configured = true;
	printf("spacewar-touch: GPIO SPI ready, <=500kHz, retained master clock=%#x; all30 SE registers unchanged\n", current.m_clock);
	return true;
}

static bool
spacewar_gpio_transfer(const uint8_t *tx, uint8_t *rx, uint32_t length)
{
	struct spacewar_spi_snapshot current;
	/* Exclude the loader's opaque firmware calls for one complete wire frame.
	 * Release between frames so USB can make progress during firmware upload. */
	if (!spacewar_live_acquire()) {
		spacewar_spi_poisoned = true;
		printf("spacewar-touch: loader lease unavailable; no GPIO transaction\n");
		return false;
	}
	if (spacewar_spi_poisoned || !spacewar_gpio_configured || !spacewar_gpio_cpu_safe() ||
	    !tx || !rx || !length || length > 80 || (rd(GPIO_IO(PIN_RESET)) & 2) == 0 ||
	    (rd(GPIO_CFG(PIN_RESET)) & 0x23f) != 0x203 ||
	    rd(GPIO_CFG(PIN_CS)) != 0x280 || rd(GPIO_CFG(PIN_CLK)) != 0x280 ||
	    rd(GPIO_CFG(PIN_MOSI)) != 0x280 || rd(GPIO_CFG(PIN_MISO)) != 0 ||
	    (rd(GPIO_IO(PIN_CS)) & 2) == 0 || (rd(GPIO_IO(PIN_CLK)) & 2) != 0 ||
	    !spacewar_gpio_se_read(&current) || memcmp(&current, &spacewar_gpio_se_state, sizeof(current)) != 0 ||
	    !spacewar_gpi_unchanged()) {
		goto failed;
	}
	gpio_set(PIN_CS, false);
	for (uint32_t i = 0; i < length; i++) {
		uint8_t value = 0;
		for (int bit = 7; bit >= 0; bit--) {
			gpio_set(PIN_MOSI, (tx[i] >> bit) & 1);
			sleep_us(1);
			gpio_set(PIN_CLK, true);
			sleep_us(1);
			value = (uint8_t)(value << 1 | gpio_get(PIN_MISO));
			gpio_set(PIN_CLK, false);
		}
		rx[i] = value;
	}
	gpio_set(PIN_CS, true);
	sleep_us(150);
	if ((rd(GPIO_IO(PIN_CS)) & 2) == 0 || (rd(GPIO_IO(PIN_CLK)) & 2) != 0 ||
	    !spacewar_gpio_cpu_safe() || !spacewar_gpio_se_read(&current) ||
	    memcmp(&current, &spacewar_gpio_se_state, sizeof(current)) != 0 || !spacewar_gpi_unchanged()) {
		goto failed;
	}
	spacewar_live_release();
	return true;
failed:
	spacewar_spi_poisoned = true;
	spacewar_live_release();
	printf("spacewar-touch: GPIO SPI state changed; no retry, SE recovery or further command\n");
	return false;
}

static bool
spacewar_fts_read(uint8_t address, uint8_t *data, uint32_t length)
{
	uint8_t tx[80] = { 0 }, rx[80] = { 0 };
	if (!data || !length || length > sizeof(tx) - 9) {
		return false;
	}
	tx[0] = address; tx[1] = 0xa0; tx[2] = (uint8_t)(length >> 8); tx[3] = (uint8_t)length;
	for (uint32_t attempt = 0; attempt < 3; attempt++) {
		if (!spacewar_gpio_transfer(tx, rx, length + 9)) {
			return false;
		}
		uint16_t expected = focaltech_crc(rx + 7, length), actual = (uint16_t)(rx[7 + length] | rx[8 + length] << 8);
		if (!(rx[3] & 0xa0) && actual == expected) {
			memcpy(data, rx + 7, length);
			return true;
		}
		printf("spacewar-touch: FTS read%#x retry%u status=%#x CRC=%#x/%#x\n", address, attempt + 1, rx[3], actual, expected);
	}
	return false;
}

static bool
spacewar_fts_write(uint8_t address, const uint8_t *data, uint32_t length)
{
	uint8_t tx[80] = { 0 }, rx[80] = { 0 };
	uint32_t frame_length = length ? length + 7 : 4;
	bool allowed = (address == 0x55 && length == 1 && data && data[0] == 0xaa) ||
	    (address == 0xad && length == 3) || (address == 0xae && length > 0 && length <= 32) ||
	    (address == 0xcc && length == 6) || (address == 0x08 && length == 0);
	if (!allowed || frame_length > sizeof(tx) || (length && !data)) {
		return false;
	}
	tx[0] = address; tx[1] = 0; tx[2] = (uint8_t)(length >> 8); tx[3] = (uint8_t)length;
	if (length) {
		memcpy(tx + 7, data, length);
	}
	/* Mutating commands, including app start08, are never retried after uncertain acknowledgement. */
	if (!spacewar_gpio_transfer(tx, rx, frame_length) || (rx[3] & 0xa0)) {
		spacewar_spi_poisoned = true;
		printf("spacewar-touch: FTS write%#x not acknowledged; poisoned, no automatic retry\n", address);
		return false;
	}
	return true;
}

static int
spacewar_fw_read(void *context, uint8_t address, uint8_t *data, size_t length)
{
	(void)context;
	if (length > 71 || !spacewar_fts_read(address, data, (uint32_t)length)) {
		return spacewar_spi_poisoned ? SWFW_XFER_TRANSPORT : SWFW_XFER_PROTOCOL;
	}
	return SWFW_XFER_OK;
}

static int
spacewar_fw_write(void *context, uint8_t address, const uint8_t *data, size_t length)
{
	(void)context;
	if (length > 32 || !spacewar_fts_write(address, data, (uint32_t)length)) {
		return spacewar_spi_poisoned ? SWFW_XFER_TRANSPORT : SWFW_XFER_PROTOCOL;
	}
	if (address == 0xae) {
		spacewar_fw_written += (uint32_t)length;
		if ((spacewar_fw_written % 16384) == 0 || spacewar_fw_written == SPACEWAR_M146_FW_LENGTH) {
			printf("spacewar-touch: RAM firmware %u/%u bytes written\n", spacewar_fw_written, SPACEWAR_M146_FW_LENGTH);
		}
	}
	return SWFW_XFER_OK;
}

static void
spacewar_fw_delay(void *context, unsigned milliseconds)
{
	(void)context;
	sleep_ms(milliseconds);
}

static int
spacewar_fw_digest(const uint8_t *data, size_t length, uint8_t digest[32])
{
	SHA256_CTX context;
	SHA256_Init(&context);
	SHA256_Update(&context, data, length);
	SHA256_Final(digest, &context);
	return 0;
}

static void
spacewar_ft12_post(const struct sw_ft12_update *update, uint32_t *events)
{
	for (uint32_t i = 0; i < update->count; i++) {
		const struct sw_ft12_event *event = &update->events[i];
		uint32_t type = event->action == SW_FT12_BEGIN ? HAKIOS_TOUCH_DOWN :
		    event->action == SW_FT12_MOVE ? HAKIOS_TOUCH_MOVE : HAKIOS_TOUCH_UP;
		uint32_t x = (uint32_t)event->contact.x * 65535u / 1080u;
		uint32_t y = (uint32_t)event->contact.y * 65535u / 2400u;
		if (flip & 1) x = 65535 - x;
		if (flip & 2) y = 65535 - y;
		hakios_touch_post(type, event->id, x, y);
		uint32_t number = (*events)++;
		if (type != HAKIOS_TOUCH_MOVE || number < 40 || (number % 32) == 0) {
			printf("spacewar-touch: FT12 %s id=%u raw=%u,%u normalized=%u,%u active=%#x\n",
			    type == HAKIOS_TOUCH_DOWN ? "DOWN" : type == HAKIOS_TOUCH_MOVE ? "MOVE" : "UP",
			    event->id, event->contact.x, event->contact.y, x, y, update->active_mask);
		}
	}
}

static void
spacewar_ft12_dump(const uint8_t *report, const char *reason)
{
	static const char hex[] = "0123456789abcdef";
	char bytes[SW_FT12_REPORT_BYTES * 3 + 1];
	for (uint32_t i = 0; i < SW_FT12_REPORT_BYTES; i++) {
		bytes[i * 3] = hex[report[i] >> 4];
		bytes[i * 3 + 1] = hex[report[i] & 15];
		bytes[i * 3 + 2] = ' ';
	}
	bytes[sizeof(bytes) - 1] = 0;
	printf("spacewar-touch: FT12 raw %s IRQ81=%u: %s\n", reason, gpio_get(PIN_INT), bytes);
}

static bool
spacewar_ft12_state_probe(const char *reason, bool details)
{
	uint8_t high = 0xff, low = 0xff, mode = 0xff, power = 0xff, version = 0xff;
	uint32_t read_mask = 0;
	if (spacewar_spi_poisoned || !spacewar_gpio_cpu_safe()) return false;
	if (spacewar_fts_read(0xa3, &high, 1)) read_mask |= 1;
	if (!spacewar_spi_poisoned && spacewar_fts_read(0x9f, &low, 1)) read_mask |= 2;
	bool ready = read_mask == 3 && high == 0x56 && low == 0x62;
	if (ready && details) {
		if (spacewar_fts_read(0x00, &mode, 1)) read_mask |= 4;
		if (!spacewar_spi_poisoned && spacewar_fts_read(0xa5, &power, 1)) read_mask |= 8;
		if (!spacewar_spi_poisoned && spacewar_fts_read(0xa6, &version, 1)) read_mask |= 16;
	}
	printf("spacewar-touch: FT12 state %s runtime=%02x:%02x IRQ81=%u read-mask=%#x mode00=%02x powerA5=%02x versionA6=%02x\n",
	    reason, high, low, gpio_get(PIN_INT), read_mask, mode, power, version);
	return ready && !spacewar_spi_poisoned;
}

static bool
spacewar_ft12_wait_runtime(const char *reason, bool details)
{
	/* Match the vendor's bounded 1s readiness wait, without reloading firmware. */
	for (uint32_t attempt = 0; attempt < 5; attempt++) {
		if (spacewar_ft12_state_probe(reason, details)) return true;
		if (spacewar_spi_poisoned || !spacewar_gpio_cpu_safe()) break;
		if (attempt < 4) sleep_ms(200);
	}
	return false;
}

static void
spacewar_ft12_poll(void)
{
	struct sw_ft12_state state = { 0 };
	struct sw_ft12_update update;
	const struct sw_ft12_limits limits = { 10, 1080, 2400 };
	uint8_t report[SW_FT12_REPORT_BYTES];
	uint64_t deadline = 0, next_fallback = 0, next_health = 0, next_summary = 0;
	uint32_t seconds = 20, frames = 0, bad = 0, consecutive = 0, events = 0;
	uint32_t initializing = 0, idle = 0, valid = 0, irq_edges = 0, irq_low_reads = 0;
	uint32_t bad_dumps = 0, valid_dumps = 0;
	bool previous_irq = gpio_get(PIN_INT);
	const char *stop_reason = "window expired";
	/* boot-arg touch_seconds=20: bounded Spacewar report polling after verified runtime firmware.
	 * Explicit touch_seconds=0 keeps polling until an error or the kernel returns. */
	PE_parse_boot_argn("touch_seconds", &seconds, sizeof(seconds));
	if (seconds > 600) seconds = 20;
	/* Vendor's successful download worker waits another 50ms before querying runtime. */
	sleep_ms(50);
	if (!spacewar_ft12_wait_runtime("after app settle", true)) {
		stop_reason = "runtime unavailable after settle";
		goto cleanup;
	}
	/* The vendor reads on a falling IRQ. A 100ms fallback also catches a missed pulse. */
	if (seconds) {
		printf("spacewar-touch: TOUCH WINDOW: verified runtime, FT12 %us on1080x2400; touch/swipe now, IRQ81=%u\n", seconds, previous_irq);
		clock_interval_to_deadline(seconds * 1000, NSEC_PER_MSEC, &deadline);
	} else {
		printf("spacewar-touch: TOUCH WINDOW: verified runtime, continuous FT12 on1080x2400; touch/swipe now, IRQ81=%u\n", previous_irq);
	}
	clock_interval_to_deadline(5000, NSEC_PER_MSEC, &next_summary);
	while (!seconds || mach_absolute_time() < deadline) {
		if (spacewar_spi_poisoned || !spacewar_gpio_cpu_safe()) {
			stop_reason = "bus or CPU ownership changed";
			break;
		}
		uint64_t now = mach_absolute_time();
		if (now >= next_summary) {
			printf("spacewar-touch: FT12 progress frames=%u valid=%u init=%u idle=%u rejected=%u events=%u active=%#x\n",
			    frames, valid, initializing, idle, bad, events, state.active_mask);
			clock_interval_to_deadline(5000, NSEC_PER_MSEC, &next_summary);
		}
		bool irq = gpio_get(PIN_INT);
		if (irq != previous_irq) {
			irq_edges++;
			if (irq_edges <= 12) printf("spacewar-touch: FT12 IRQ81=%u transition%u\n", irq, irq_edges);
			previous_irq = irq;
		}
		if (irq && now < next_fallback) {
			sleep_ms(8);
			continue;
		}
		clock_interval_to_deadline(100, NSEC_PER_MSEC, &next_fallback);
		if (!irq) irq_low_reads++;
		if (!spacewar_fts_read(0x01, report, sizeof(report))) {
			bad++; consecutive++;
		} else {
			frames++;
			if (report[1] == 0xff && report[2] == 0xff && report[3] == 0xff && report[4] == 0xff) {
				/* Vendor TOUCH_FW_INIT: release contacts and wait for valid application state. */
				initializing++;
				consecutive = 0;
				sw_ft12_release_all(&state, &update);
				spacewar_ft12_post(&update, &events);
				if (initializing == 1) spacewar_ft12_dump(report, "FF init marker");
				if (initializing == 1 || now >= next_health) {
					printf("spacewar-touch: FT12 waiting after FF init: frames=%u init=%u IRQ-edges=%u IRQ-low-reads=%u\n",
					    frames, initializing, irq_edges, irq_low_reads);
					if (!spacewar_ft12_wait_runtime("FF init wait", false)) {
						stop_reason = "runtime changed during FF init";
						break;
					}
					clock_interval_to_deadline(5000, NSEC_PER_MSEC, &next_health);
				}
			} else if (report[1] == 0xef && report[2] == 0xef && report[3] == 0xef) {
				spacewar_ft12_dump(report, "EF firmware recovery marker");
				(void)spacewar_ft12_state_probe("EF report", false);
				stop_reason = "firmware recovery requested; no automatic reload";
				break;
			} else {
				enum sw_ft12_status status = sw_ft12_process(report, sizeof(report), &limits, &state, &update);
				if (status == SW_FT12_OK) {
					if (valid++ == 0) printf("spacewar-touch: TOUCH READY: first valid FT12 frame, type=%u count=%u IRQ81=%u\n",
					    report[1] >> 4, report[1] & 15, irq);
					if (valid_dumps++ < 2) spacewar_ft12_dump(report, "valid");
					consecutive = 0;
					spacewar_ft12_post(&update, &events);
				} else if (status == SW_FT12_NO_RECORDS && report[1] == 0) {
					/* A fallback read without a pending report must not invent contact updates. */
					idle++;
					consecutive = 0;
				} else {
					if (bad_dumps++ < 3) spacewar_ft12_dump(report, "rejected");
					if (bad++ < 8) printf("spacewar-touch: FT12 rejected frame status=%u header=%02x,%02x; contacts preserved\n", status, report[0], report[1]);
					consecutive++;
				}
			}
		}
		if (consecutive >= 8) {
			stop_reason = "eight consecutive invalid transfers/reports";
			break;
		}
		sleep_ms(8);
	}
	if (!spacewar_spi_poisoned && spacewar_gpio_cpu_safe()) (void)spacewar_ft12_state_probe("end of report window", true);
cleanup:
	sw_ft12_release_all(&state, &update);
	spacewar_ft12_post(&update, &events);
	printf("spacewar-touch: FT12 stopped reason=%s frames=%u valid=%u init=%u idle=%u rejected=%u events=%u IRQ-edges=%u IRQ-low-reads=%u; owned contacts released, no firmware reload\n",
	    stop_reason, frames, valid, initializing, idle, bad, events, irq_edges, irq_low_reads);
}

static void
spacewar_spi_identity_probe(void)
{
	uint8_t high = 0, low = 0, id[2] = { 0 }, aa = 0xaa;
	bool identified = false, runtime = false;
	struct swfw_result result;
	const struct swfw_io io = { NULL, spacewar_fw_read, spacewar_fw_write, spacewar_fw_delay, spacewar_fw_digest, 32 };
	lck_mtx_lock(&hakios_rsc_lock);
	if (!spacewar_live_acquire()) {
		spacewar_spi_poisoned = true;
		goto out;
	}
	if (!spacewar_gpio_setup()) {
		spacewar_spi_poisoned = true;
		spacewar_live_release();
		goto out;
	}
	printf("spacewar-touch: releasing reset105; waiting200ms for runtime ID\n");
	gpio_set(PIN_RESET, true);
	spacewar_live_release();
	sleep_ms(200);
	if ((rd(GPIO_IO(PIN_RESET)) & 2) == 0) { spacewar_spi_poisoned = true; goto out; }
	if (spacewar_fts_read(0xa3, &high, 1) && spacewar_fts_read(0x9f, &low, 1)) {
		printf("spacewar-touch: runtime ID=%02x:%02x expected56:62\n", high, low);
		identified = runtime = high == 0x56 && low == 0x62;
		if (!identified && !swfw_runtime_is_rom_candidate(high, low)) {
			printf("spacewar-touch: unexpected nonblank runtime ID; refusing ROM/RAM commands\n");
			goto out;
		}
	}
	if (!identified && !spacewar_spi_poisoned && high == 0xef && low == 0xef) {
		/* The vendor recovery path queries 90 before resetting an already waiting ROM. */
		printf("spacewar-touch: EF:EF recovery candidate; query ROM ID without reset or writes\n");
		if (spacewar_fts_read(0x90, id, 2)) {
			printf("spacewar-touch: recovery ROM ID=%02x:%02x (CRC verified), expected56:62\n", id[0], id[1]);
			identified = id[0] == 0x56 && id[1] == 0x62;
			if (!identified && !swfw_runtime_is_rom_candidate(id[0], id[1])) {
				printf("spacewar-touch: unexpected ROM identity; refusing reset and RAM commands\n");
				goto out;
			}
		}
	}
	if (!identified && !spacewar_spi_poisoned) {
		if (!spacewar_live_acquire()) { spacewar_spi_poisoned = true; goto out; }
		gpio_set(PIN_RESET, false); sleep_ms(1); gpio_set(PIN_RESET, true); sleep_ms(12);
		spacewar_live_release();
		if (spacewar_fts_write(0x55, &aa, 1)) {
			sleep_ms(12);
			if (spacewar_fts_read(0x90, id, 2)) {
				printf("spacewar-touch: ROM ID=%02x:%02x expected56:62\n", id[0], id[1]);
				identified = id[0] == 0x56 && id[1] == 0x62;
			}
		}
	}
	if (!identified || spacewar_spi_poisoned || touch_steps == 5) goto out;
	if (!runtime) {
		int status = swfw_m146_ensure_runtime(&io, spacewar_m146_fw, sizeof(spacewar_m146_fw), &result);
		printf("spacewar-touch: RAM firmware status=%d stage=%u bytes=%u packets=%u ECC=%#x/%#x start=%u/%u runtime=%02x:%02x\n",
		    status, result.stage, result.bytes_written, result.packets_written, result.host_ecc, result.device_ecc,
		    result.start_attempted, result.start_acknowledged, result.runtime_high, result.runtime_low);
		if (status != SWFW_READY && status != SWFW_ALREADY_RUNNING) {
			identified = false;
			spacewar_spi_poisoned = true;
			goto out;
		}
	}
	spacewar_ft12_poll();
out:
	/* Cleanup must not write pins when the loader did not grant ownership. */
	if (spacewar_live_acquire()) {
		if (spacewar_spi_poisoned) (void)spacewar_spi_snapshot(&(struct spacewar_spi_snapshot){ 0 });
		gpio_set(PIN_RESET, false);
		spacewar_live_release();
		printf("spacewar-touch: combined probe complete ID=%s poisoned=%u reset105 low; no SE writes or NVM programming\n", identified ? "56:62" : "unconfirmed", spacewar_spi_poisoned);
	} else {
		printf("spacewar-touch: combined probe stopped; no cleanup pin write without loader lease\n");
	}
	lck_mtx_unlock(&hakios_rsc_lock);
}

/* Called only after the boot CPU proves all secondary work has been abandoned. */
void hakios_goodix_quiesce(void);

void
hakios_goodix_quiesce(void)
{
	if (!touch_live || !spacewar_gpio_configured) return;
	if (!spacewar_live_stop_acquire()) {
		printf("spacewar-touch: return cleanup skipped without exclusive loader ownership\n");
		return;
	}
	if (rd(GPIO_CFG(PIN_CS)) == 0x280 && rd(GPIO_CFG(PIN_CLK)) == 0x280 &&
	    rd(GPIO_CFG(PIN_MOSI)) == 0x280 && rd(GPIO_CFG(PIN_MISO)) == 0 &&
	    (rd(GPIO_CFG(PIN_RESET)) & 0x23f) == 0x203) {
		gpio_set(PIN_CS, true);
		gpio_set(PIN_CLK, false);
		gpio_set(PIN_RESET, false);
		spacewar_gpio_configured = false;
		printf("spacewar-touch: return cleanup: CS high, CLK low, reset105 low\n");
	} else {
		printf("spacewar-touch: return cleanup refused changed pin ownership\n");
	}
	spacewar_live_release();
}

static uint64_t
micros_now(void)
{
	uint64_t ns;

	absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
	return ns / 1000;
}

/* Is a finger on the screen, as far as the reports have said? */
static bool
chip_touching(void)
{
	for (uint32_t i = 0; i < FOCALTECH_MAX_TOUCHES; i++) {
		if (touch_chip == TOUCH_FOCALTECH && focaltech_contact[i].down) {
			return true;
		}
	}
	for (uint32_t i = 0; touch_chip != TOUCH_FOCALTECH && i < sizeof(contact) / sizeof(contact[0]); i++) {
		if (contact[i].down) {
			return true;
		}
	}
	return false;
}

static void
goodix_thread(__unused void *parameter, __unused wait_result_t result)
{
	unsigned idle = 0, idle_looks;
	uint32_t poll_us = 8000, latency = 0, reads = 0;
	uint64_t before = 0, reading = 0;

	PE_parse_boot_argn("touch_poll_us", &poll_us, sizeof(poll_us));
	PE_parse_boot_argn("touch_lat", &latency, sizeof(latency));
	if (touch_spacewar) printf("spacewar-touch: driver thread started; preparing live coordination\n");
	if (touch_spacewar && !spacewar_live_init()) {
		printf("spacewar-touch: live profile refused before hardware access\n");
		thread_terminate(current_thread());
		return;
	}
	if (poll_us < 250 || poll_us > 100000) {
		poll_us = 8000;
	}
	idle_looks = 16000 / poll_us;   /* a report read every 16 ms even with the line high, in case it is missed */

	tlmm = ml_io_map(TLMM_PHYS, TLMM_SIZE);
	gcc = ml_io_map(GCC_PHYS, GCC_SIZE);
	se = ml_io_map(SE_PHYS, SE_SIZE);
	rsc = ml_io_map(RSC_PHYS, RSC_SIZE);
	cmd_db = ml_io_map_wcomb(CMD_DB_PHYS, CMD_DB_SIZE);      /* memory, not registers: no alignment rules */

	if (tlmm == 0 || gcc == 0 || se == 0 || rsc == 0 || cmd_db == 0) {
		printf("goodix: required mapping failed: TLMM=%#lx GCC=%#lx SE=%#lx RSC=%#lx CMD_DB=%#lx\n",
		    tlmm, gcc, se, rsc, cmd_db);
		thread_terminate(current_thread());
		return;
	}
	if (touch_spacewar) {
		spacewar_touch_probe();
		thread_terminate(current_thread());
		return;
	}

	if (!bring_up()) {
		printf("goodix: no %s touch screen\n", chip_name());
		thread_terminate(current_thread());
		return;
	}
	printf("goodix: %s touch screen ready; the interrupt line is looked at every %u us\n", chip_name(), poll_us);
	{
		uint32_t rate = 0;

		/* boot-arg touch_rate=480: the chip reports twice as often (Xiaomi's game mode; 240 otherwise) */
		if (PE_parse_boot_argn("touch_rate", &rate, sizeof(rate)) && rate == 480 && touch_chip == TOUCH_GOODIX) {
			bool done = goodix_command(GOODIX_CMD_HIGH_RATE, 1);

			report_rate = done ? "480 (acknowledged)" : "240 (480 NOT ACKNOWLEDGED)";
			printf("goodix: reports 480 times a second (command %#x at %#x): %s\n", GOODIX_CMD_HIGH_RATE, command_address,
			    done ? "acknowledged" : "NOT ACKNOWLEDGED");
		}
	}
	for (unsigned tick = 1;; tick++) {
		uint64_t looked = micros_now(), read_at;

		/*
		 * The interrupt line is low while a report waits, but on the phone's FocalTech not for long: looked at every 4 ms it
		 * was seen to miss reports, and a finger's lift came 88 to 169 ms after its last move, by the look of every 80 ms
		 * (4 November, real fingers, tl: p): the content stood still and then jumped at the end of each swipe, whatever the
		 * frame rate. So with a finger down every look reads, and with none a report is read every 16 ms anyway.
		 */
		if (!gpio_get(PIN_INT) || chip_touching()) {
			idle = 0;
			chip_poll();
			read_at = micros_now();
			reads++;
			reading += read_at - looked;
			if (latency) {
				hakios_tl("tl: c before %llu seen %llu read %llu\n", before, looked, read_at);
			}
		} else if (++idle >= idle_looks) {
			idle = 0;
			chip_poll();
			reads++;
			reading += micros_now() - looked;
		}
		before = looked;
		if (tick % (60 * (1000000 / poll_us)) == 0) {
			printf("%s: so far %u reports in %u looks (%u bad), %u finger positions, the last at %u, %u; "
			    "interrupt line %s; a report takes %llu us to read; the line looked at every %u us, reports %s a second\n",
			    chip_name(), seen.reports, seen.polls, seen.bad, seen.touches, seen.last_x, seen.last_y,
			    gpio_get(PIN_INT) ? "high" : "low", reads ? reading / reads : 0, poll_us, report_rate);
		}
		sleep_us(poll_us);
	}
}

void
hakios_goodix_init(void)
{
	uint32_t enabled = 0, profile = 0;
	thread_t thread;
	kern_return_t status;
	bool profile_present;

	if (!PE_parse_boot_argn("touchscreen", &enabled, sizeof(enabled)) || !enabled) {
		return;
	}
	/* boot-arg touch_spacewar=1: use the Spacewar inspection profile; forced by a Spacewar build. */
	profile_present = PE_parse_boot_argn("touch_spacewar", &profile, sizeof(profile));
#if HAKIOS_DEVICE_SPACEWAR
	/* This board must never fall through to Redwood's pins, supplies or firmware. */
	touch_spacewar = 1;
	if (profile_present && profile != 1) {
		printf("spacewar-touch: this build requires the Spacewar profile; refusing override\n");
		return;
	}
#else
	(void)profile_present;
	touch_spacewar = profile;
#endif
	if (touch_spacewar) {
		/* boot-arg touch_steps=1: inspect touch; 2 inspects RSC; 3 CPU0 power; 4 access clocks; 5 guarded GPIO SPI/ID; 6 verified ROM RAM firmware and FT12 reports. */
		PE_parse_boot_argn("touch_steps", &touch_steps, sizeof(touch_steps));
		/* Stages3..6 require the compiled Spacewar guard and fresh CPU/hardware checks. */
		if (touch_steps < 1 || touch_steps > 6 || (!HAKIOS_DEVICE_SPACEWAR && touch_steps >= 3)) {
			printf("spacewar-touch: touch_steps supports inspections1/2 and guarded Spacewar power3/access4/identity5/runtime6 only\n");
			return;
		}
	}
	PE_parse_boot_argn("touch_flip", &flip, sizeof(flip));
	if (touch_spacewar) printf("spacewar-touch: starting driver thread, steps=%u\n", touch_steps);
	status = kernel_thread_start(goodix_thread, NULL, &thread);
	if (status == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("goodix: cannot start touch probe thread (%d)\n", status);
	}
}
