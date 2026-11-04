#ifndef SPACEWAR_FW_CANDIDATE_H
#define SPACEWAR_FW_CANDIDATE_H

#include <stddef.h>
#include <stdint.h>

/* An absent/recovery application is only a reason to query ROM, never a chip ID.
 * Nothing's FocalTech driver uses repeated EF data to request boot recovery.
 * Mixed EF/other values and other nonblank IDs are deliberately not accepted.
 */
static inline int
swfw_runtime_is_rom_candidate(uint8_t high, uint8_t low)
{
    return ((high == 0 || high == 0xff) && (low == 0 || low == 0xff)) ||
           (high == 0xef && low == 0xef);
}

/* Decoded FTS commands. Framing, CRC/status checks and bus locking belong to the adapter. */
enum swfw_transfer { SWFW_XFER_OK = 0, SWFW_XFER_PROTOCOL = -1, SWFW_XFER_TRANSPORT = -2 };
struct swfw_io {
    void *context;
    int (*read)(void *, uint8_t command, uint8_t *data, size_t length);
    int (*write)(void *, uint8_t command, const uint8_t *data, size_t length);
    void (*delay_ms)(void *, unsigned milliseconds);
    int (*sha256)(const uint8_t *, size_t, uint8_t digest[32]); /* 0 means success; no bus access */
    size_t max_write_payload; /* Excludes command and FTS framing; must fit the 6-byte ECC request. */
};

enum swfw_status {
    SWFW_READY = 0, SWFW_ALREADY_RUNNING = 1,
    SWFW_BAD_ARGUMENT = -1, SWFW_BAD_IMAGE = -2, SWFW_BAD_HEADER = -3,
    SWFW_PROTOCOL_ERROR = -4, SWFW_TRANSPORT_ERROR = -5,
    SWFW_WRONG_ROM = -6, SWFW_ECC_MISMATCH = -7, SWFW_APP_NOT_READY = -8,
    SWFW_TRANSPORT_TOO_SMALL = -9, SWFW_WRONG_RUNTIME = -10
};
enum swfw_stage { SWFW_VALIDATE, SWFW_RUNTIME, SWFW_ROM_ID, SWFW_RAM_WRITE,
    SWFW_RAM_ECC, SWFW_START_APP, SWFW_POST_START, SWFW_DONE };
struct swfw_result {
    enum swfw_stage stage;
    uint32_t bytes_written;
    uint32_t packets_written;
    uint16_t host_ecc, device_ecc;
    uint8_t runtime_high, runtime_low, rom_high, rom_low;
    uint8_t last_command;
    uint8_t transport_poisoned;
    uint8_t start_attempted;
    uint8_t start_acknowledged;
    uint8_t chunk_bytes;
};

int swfw_m146_ensure_runtime(const struct swfw_io *, const uint8_t *firmware,
    size_t length, struct swfw_result *);

/* Preconditions: Spacewar supplies/SPI are established; caller owns reset/ROM entry.
 * The function never resets, sends 0x55, programs clocks, writes flash or retries a RAM upload.
 * It skips uploading if runtime A3/9F already match, otherwise rechecks ROM 90 exactly 56:62.
 */
#endif
