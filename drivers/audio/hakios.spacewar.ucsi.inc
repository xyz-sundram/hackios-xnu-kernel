/* Spacewar's UCSI PPM transport on PMIC_RTR_ADSP_APPS. Wire layout and
 * initialization follow NothingOSS 1d557de4, ucsi_glink.c and ucsi.c.
 * This initializes the interface and reads connector state. It cannot send
 * power/data-role changes, connector resets, PDO writes or charger limits. */
#define SW_UCSI_OWNER 32779u
#define SW_UCSI_READ 0x11u
#define SW_UCSI_WRITE 0x12u
#define SW_UCSI_NOTICE 0x13u
#define SW_UCSI_COMPLETE (1u << 31)
#define SW_UCSI_ERROR (1u << 30)
#define SW_UCSI_ACK_COMPLETE (1u << 29)
#define SW_UCSI_BUSY (1u << 28)
#define SW_UCSI_RESET_COMPLETE (1u << 27)
#define SW_UCSI_UNSUPPORTED (1u << 25)

static LCK_MTX_DECLARE(spacewar_ucsi_lock, &adsp_lock_group);
static struct {
	bool pending;
	uint32_t expected, answers, result, changes, observed;
	uint8_t buffer[48];
	uint64_t last_poll;
} spacewar_ucsi;

static bool
spacewar_ucsi_allowed(uint64_t control)
{
	return control == 1 ||                         /* PPM interface reset */
	    control == 0x20004 || control == 0x30004 || /* ACK command / connector */
	    control == 0x80010005 || control == 0xdbe70005 || /* basic / all notifications */
	    control == 6 || control == 0x10007 || control == 0x10012; /* capabilities / status */
}

static void
spacewar_ucsi_input(const uint8_t *data, uint32_t length)
{
	uint32_t header[3];
	if (length < sizeof(header)) return;
	memcpy(header, data, sizeof(header));
	if (header[0] != SW_UCSI_OWNER) return;
	static unsigned traced;
	if (traced++ < 8) tell("adsp: ucsi: wire type=%u opcode=%#x bytes=%u\n", header[1], header[2], length);
	if (header[2] == SW_UCSI_NOTICE && length == 24 && (header[1] == 1 || header[1] == 2)) {
		uint32_t notice[3]; memcpy(notice, data + 12, sizeof(notice));
		if (notice[1] == 0 && (notice[0] & 0xfe)) {
			lck_mtx_lock(&spacewar_ucsi_lock);
			spacewar_ucsi.changes++;
			lck_mtx_unlock(&spacewar_ucsi_lock);
		}
		return;
	}
	if (header[1] != 1 || !((header[2] == SW_UCSI_READ && length == 64) ||
	    (header[2] == SW_UCSI_WRITE && length == 16))) return;
	lck_mtx_lock(&spacewar_ucsi_lock);
	if (spacewar_ucsi.pending && spacewar_ucsi.expected == header[2]) {
		memcpy(&spacewar_ucsi.result, data + length - 4, 4);
		if (header[2] == SW_UCSI_READ) memcpy(spacewar_ucsi.buffer, data + 12, 48);
		spacewar_ucsi.pending = false;
		spacewar_ucsi.answers++;
	}
	lck_mtx_unlock(&spacewar_ucsi_lock);
}

static bool
spacewar_ucsi_exchange(uint32_t opcode, uint64_t control, uint8_t *buffer)
{
	if (!HAKIOS_DEVICE_SPACEWAR || !battmgr.wanted ||
	    (opcode != SW_UCSI_READ && opcode != SW_UCSI_WRITE) ||
	    (opcode == SW_UCSI_READ && (control != 0 || buffer == NULL)) ||
	    (opcode == SW_UCSI_WRITE && !spacewar_ucsi_allowed(control))) return false;
	struct { uint32_t owner, type, opcode; uint8_t buffer[48]; uint32_t reserved; } q = {
		.owner = SW_UCSI_OWNER, .type = 1, .opcode = opcode
	};
	_Static_assert(sizeof(q) == 64, "UCSI GLINK buffer message");
	if (opcode == SW_UCSI_WRITE) memcpy(q.buffer + 8, &control, 8);
	lck_mtx_lock(&spacewar_ucsi_lock);
	if (spacewar_ucsi.pending) { lck_mtx_unlock(&spacewar_ucsi_lock); return false; }
	uint32_t before = spacewar_ucsi.answers;
	spacewar_ucsi.expected = opcode;
	spacewar_ucsi.pending = true;
	lck_mtx_unlock(&spacewar_ucsi_lock);
	bool sent = glink_send(&glink.channel[CHANNEL_PMIC], &q, opcode == SW_UCSI_READ ? 12 : sizeof(q)) == 0;
	for (unsigned ms = 0; sent && ms < 1000; ms++) {
		lck_mtx_lock(&spacewar_ucsi_lock);
		if (spacewar_ucsi.answers != before) {
			bool ok = spacewar_ucsi.result == 0;
			uint32_t result = spacewar_ucsi.result;
			if (ok && opcode == SW_UCSI_READ) memcpy(buffer, spacewar_ucsi.buffer, 48);
			lck_mtx_unlock(&spacewar_ucsi_lock);
			if (!ok) tell("adsp: ucsi: transport opcode=%#x result=%u\n", opcode, result);
			return ok;
		}
		bool live = battmgr.wanted && glink.up && !glink.broken && glink.channel[CHANNEL_PMIC].open;
		lck_mtx_unlock(&spacewar_ucsi_lock);
		if (!live) break;
		sleep_ms(1);
	}
	lck_mtx_lock(&spacewar_ucsi_lock);
	spacewar_ucsi.pending = false;
	lck_mtx_unlock(&spacewar_ucsi_lock);
	return false;
}

static bool
spacewar_ucsi_wait(uint32_t completion, uint8_t *buffer)
{
	uint64_t until = now_us() + 5000000;
	do {
		if (!spacewar_ucsi_exchange(SW_UCSI_READ, 0, buffer)) return false;
		uint32_t cci; memcpy(&cci, buffer + 4, 4);
		if (cci & (SW_UCSI_ERROR | SW_UCSI_UNSUPPORTED)) return false;
		if (!(cci & SW_UCSI_BUSY) && (cci & completion)) return true;
		if (!battmgr.wanted) return false;
		spacewar_pan_poll();
		sleep_ms(20);
	} while (now_us() < until);
	return false;
}

static bool
spacewar_ucsi_ack(uint32_t cci)
{
	uint8_t buffer[48];
	uint64_t ack = 0x20004 | ((cci & 0xfe) ? 0x10000 : 0);
	return spacewar_ucsi_exchange(SW_UCSI_WRITE, ack, NULL) &&
	    spacewar_ucsi_wait(SW_UCSI_ACK_COMPLETE, buffer);
}

static bool
spacewar_ucsi_command(uint64_t control, void *output, unsigned bytes)
{
	if (bytes > 16 || (bytes && output == NULL) || !spacewar_ucsi_allowed(control)) return false;
	uint8_t buffer[48] = { 0 };
	if (!spacewar_ucsi_exchange(SW_UCSI_WRITE, control, NULL) ||
	    !spacewar_ucsi_wait(SW_UCSI_COMPLETE, buffer)) {
		uint32_t cci; memcpy(&cci, buffer + 4, 4);
		/* A completed error still needs acknowledgement to release the PPM.
		 * A transport timeout has no verified completion to acknowledge. */
		if ((cci & SW_UCSI_COMPLETE) && (cci & (SW_UCSI_ERROR | SW_UCSI_UNSUPPORTED)))
			(void)spacewar_ucsi_ack(cci);
		tell("adsp: ucsi: command %#llx failed\n", control);
		return false;
	}
	uint32_t cci; memcpy(&cci, buffer + 4, 4);
	unsigned length = (cci >> 8) & 255;
	bool ok = length >= bytes && length <= 16;
	if (ok && bytes) memcpy(output, buffer + 16, bytes);
	return spacewar_ucsi_ack(cci) && ok;
}

static bool
spacewar_ucsi_status(void)
{
	uint8_t status[9];
	if (!spacewar_ucsi_command(0x10012, status, sizeof(status))) return false;
	uint16_t change, flags; uint32_t rdo;
	memcpy(&change, status, 2); memcpy(&flags, status + 2, 2); memcpy(&rdo, status + 4, 4);
	tell("adsp: ucsi: connector1 change=%#x flags=%#x connected=%u source=%u mode=%u partner=%u USB=%u RDO=%#x charging=%u\n",
	    change, flags, !!(flags & 8), !!(flags & 16), flags & 7, (flags >> 13) & 7, !!(flags & 32), rdo, status[8] & 7);
	return true;
}

static bool
spacewar_ucsi_start(void)
{
	uint8_t buffer[48], capability[16], connector[2]; uint16_t version;
	if (!spacewar_ucsi_exchange(SW_UCSI_READ, 0, buffer)) {
		tell("adsp: ucsi: PPM buffer unavailable\n"); return false;
	}
	memcpy(&version, buffer, 2);
	tell("adsp: ucsi: PPM version %#x\n", version);
	if (version != 0x100 && version != 0x110 && version != 0x120) return false;
	if (!spacewar_ucsi_exchange(SW_UCSI_WRITE, 1, NULL) ||
	    !spacewar_ucsi_wait(SW_UCSI_RESET_COMPLETE, buffer) ||
	    !spacewar_ucsi_command(0x80010005, NULL, 0) ||
	    !spacewar_ucsi_command(6, capability, sizeof(capability))) return false;
	if (capability[4] != 1) { tell("adsp: ucsi: unexpected connector count %u\n", capability[4]); return false; }
	if (!spacewar_ucsi_command(0x10007, connector, sizeof(connector)) || !spacewar_ucsi_status() ||
	    !spacewar_ucsi_command(0xdbe70005, NULL, 0)) return false;
	spacewar_ucsi.last_poll = now_us();
	tell("adsp: ucsi: interface initialized; capability=%02x %02x; firmware retains PD policy\n", connector[0], connector[1]);
	return true;
}

static void
spacewar_ucsi_poll(void)
{
	lck_mtx_lock(&spacewar_ucsi_lock);
	uint32_t changes = spacewar_ucsi.changes;
	lck_mtx_unlock(&spacewar_ucsi_lock);
	if (changes == spacewar_ucsi.observed && now_us() - spacewar_ucsi.last_poll < 10000000) return;
	spacewar_ucsi.observed = changes;
	spacewar_ucsi.last_poll = now_us();
	(void)spacewar_ucsi_status();
}
