/* Nothing's separate PMIC Type-C port-notification owner, alongside UCSI.
 * Registration and deferred ACK follow altmode-glink.c at 1d557de4.
 * No mux register, power role, voltage, PDO or charger limit is written. */
#define SW_PAN_OWNER 32780u
static LCK_MTX_DECLARE(spacewar_pan_lock, &adsp_lock_group);
static struct {
	bool pending;
	uint32_t answers, notices, handled, reply_bytes, reply_word;
	uint64_t notice_at;
	uint8_t payload[16];
} spacewar_pan;

static void
spacewar_pan_input(const uint8_t *data, uint32_t length)
{
	uint32_t header[3];
	if (length < 12) return;
	memcpy(header, data, 12);
	if (header[0] != SW_PAN_OWNER) return;
	if (header[2] == 0x15 && header[1] == 1 && length >= 12 && length <= 32 && !(length & 3)) {
		lck_mtx_lock(&spacewar_pan_lock);
		if (spacewar_pan.pending) {
			spacewar_pan.reply_bytes = length;
			spacewar_pan.reply_word = 0;
			if (length >= 16) memcpy(&spacewar_pan.reply_word, data + 12, 4);
			spacewar_pan.pending = false; spacewar_pan.answers++;
		}
		lck_mtx_unlock(&spacewar_pan_lock);
	} else if ((header[2] & 255) == 0x16 && (header[1] == 1 || header[1] == 2) && length == 32) {
		/* The board has one connector. Never acknowledge an unknown port. */
		if (data[12] != 0) { tell("adsp: pan: unsupported port %u\n", data[12]); return; }
		lck_mtx_lock(&spacewar_pan_lock);
		memcpy(spacewar_pan.payload, data + 12, 16);
		spacewar_pan.notice_at = now_us(); uint32_t notices = ++spacewar_pan.notices;
		lck_mtx_unlock(&spacewar_pan_lock);
		if (notices <= 8 || notices % 64 == 0) tell("adsp: pan: SVID=%#x port0 payload=%02x %02x %02x %02x %02x %02x %02x %02x\n",
		    header[2] >> 16, data[12], data[13], data[14], data[15], data[16], data[17], data[18], data[19]);
	}
}

static bool
spacewar_pan_exchange(uint32_t command, uint32_t port)
{
	if (!HAKIOS_DEVICE_SPACEWAR || !battmgr.wanted || port != 0 || (command != 0x10 && command != 0x11)) return false;
	struct { uint32_t owner, type, opcode, command, port, reserved; } q = { SW_PAN_OWNER, 1, 0x15, command, port, 0 };
	lck_mtx_lock(&spacewar_pan_lock);
	if (spacewar_pan.pending) { lck_mtx_unlock(&spacewar_pan_lock); return false; }
	uint32_t before = spacewar_pan.answers; spacewar_pan.pending = true;
	lck_mtx_unlock(&spacewar_pan_lock);
	bool sent = glink_send(&glink.channel[CHANNEL_PMIC], &q, sizeof(q)) == 0;
	for (unsigned ms = 0; sent && ms < 1000; ms++) {
		lck_mtx_lock(&spacewar_pan_lock);
		if (spacewar_pan.answers != before) {
			uint32_t bytes = spacewar_pan.reply_bytes, word = spacewar_pan.reply_word;
			lck_mtx_unlock(&spacewar_pan_lock);
			/* The vendor client treats this opcode as completion without a
			 * documented status layout. Record the response; do not infer
			 * an attachment, mux configuration or power contract from it. */
			tell("adsp: pan: command=%#x reply bytes=%u first_word=%#x\n", command, bytes, word);
			return true;
		}
		bool live = battmgr.wanted && glink.up && !glink.broken && glink.channel[CHANNEL_PMIC].open;
		lck_mtx_unlock(&spacewar_pan_lock);
		if (!live) break;
		sleep_ms(1);
	}
	lck_mtx_lock(&spacewar_pan_lock); spacewar_pan.pending = false; lck_mtx_unlock(&spacewar_pan_lock);
	tell("adsp: pan: command=%#x no reply\n", command);
	return false;
}

static bool
spacewar_pan_start(void)
{
	return spacewar_pan_exchange(0x10, 0);
}

static void
spacewar_pan_poll(void)
{
	lck_mtx_lock(&spacewar_pan_lock);
	bool acknowledge = !spacewar_pan.pending && spacewar_pan.notices != spacewar_pan.handled &&
	    now_us() - spacewar_pan.notice_at >= 20000;
	if (acknowledge) spacewar_pan.handled = spacewar_pan.notices;
	lck_mtx_unlock(&spacewar_pan_lock);
	if (acknowledge) (void)spacewar_pan_exchange(0x11, 0);
}

static void
spacewar_pan_reset(void)
{
	lck_mtx_lock(&spacewar_pan_lock);
	spacewar_pan.handled = spacewar_pan.notices;
	lck_mtx_unlock(&spacewar_pan_lock);
}
