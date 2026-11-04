#include "hakios.spacewar.fw.h"

static const uint8_t expected_sha256[32] = {
    0x04,0x20,0x54,0x03,0xdd,0x3f,0xab,0x89,0xed,0xc9,0xe3,0x61,0xe3,0xb9,0xcd,0x8a,
    0xbb,0xda,0xc5,0x74,0xe2,0xb9,0xe7,0xca,0xfc,0x9c,0xab,0x31,0x0c,0x51,0x47,0x26
};

/* Private: called only after the exact even image size has been checked. */
static uint16_t
host_ecc(const uint8_t *bytes, size_t length)
{
    uint16_t ecc = 0;
    for (size_t i = 0; i < length; i += 2) {
        ecc ^= (uint16_t)((uint16_t)bytes[i] << 8 | bytes[i + 1]);
        for (unsigned bit = 0; bit < 16; bit++)
            ecc = ecc & 1 ? (uint16_t)((ecc >> 1) ^ 0x8408) : (uint16_t)(ecc >> 1);
    }
    return ecc;
}

static int
transfer_status(int status, struct swfw_result *result)
{
    if (status == SWFW_XFER_OK) return 0;
    if (status == SWFW_XFER_PROTOCOL) return SWFW_PROTOCOL_ERROR;
    result->transport_poisoned = 1;
    return SWFW_TRANSPORT_ERROR;
}

static int
read_command(const struct swfw_io *io, struct swfw_result *result,
    uint8_t command, uint8_t *bytes, size_t length)
{
    result->last_command = command;
    return transfer_status(io->read(io->context, command, bytes, length), result);
}

static int
write_command(const struct swfw_io *io, struct swfw_result *result,
    uint8_t command, const uint8_t *bytes, size_t length)
{
    result->last_command = command;
    return transfer_status(io->write(io->context, command, bytes, length), result);
}

/* A clean protocol rejection may mean the application is absent; a poisoned bus never does. */
static int
runtime_id(const struct swfw_io *io, struct swfw_result *result)
{
    uint8_t high = 0, low = 0;
    int status = read_command(io, result, 0xa3, &high, 1);
    if (status != 0) return status;
    status = read_command(io, result, 0x9f, &low, 1);
    if (status != 0) return status;
    result->runtime_high = high;
    result->runtime_low = low;
    if (high == 0x56 && low == 0x62) return SWFW_ALREADY_RUNNING;
    if (swfw_runtime_is_rom_candidate(high, low)) return SWFW_READY;
    return SWFW_WRONG_RUNTIME;
}

int
swfw_m146_ensure_runtime(const struct swfw_io *io, const uint8_t *firmware,
    size_t length, struct swfw_result *result)
{
    uint8_t digest[32], id[2] = {0, 0}, address[3], ecc_request[6];
    uint16_t code_length, inverse;
    size_t chunk;
    int status;

    if (!result) return SWFW_BAD_ARGUMENT;
    *result = (struct swfw_result){0};
    result->stage = SWFW_VALIDATE;
    if (!io || !io->read || !io->write || !io->delay_ms || !io->sha256 || !firmware)
        return SWFW_BAD_ARGUMENT;
    if (length != 119284) return SWFW_BAD_IMAGE;
    code_length = (uint16_t)((uint16_t)firmware[0x100] << 8 | firmware[0x101]);
    inverse = (uint16_t)((uint16_t)firmware[0x102] << 8 | firmware[0x103]);
    if ((uint32_t)code_length + inverse != 0xffff || (size_t)code_length * 4 != length ||
        firmware[0x10e] != 0x27 || (unsigned)firmware[0x10e] + firmware[0x10f] != 0xff)
        return SWFW_BAD_HEADER;
    if (io->sha256(firmware, length, digest) != 0) return SWFW_BAD_IMAGE;
    for (unsigned i = 0; i < 32; i++)
        if (digest[i] != expected_sha256[i]) return SWFW_BAD_IMAGE;
    result->host_ecc = host_ecc(firmware, length);
    if (result->host_ecc != 0x22ed) return SWFW_BAD_IMAGE;

    result->stage = SWFW_RUNTIME;
    status = runtime_id(io, result);
    if (status == SWFW_ALREADY_RUNNING) {
        result->stage = SWFW_DONE;
        return SWFW_ALREADY_RUNNING;
    }
    if (status == SWFW_TRANSPORT_ERROR || status == SWFW_WRONG_RUNTIME) return status;

    /* The longest non-data command below is the six-byte CC ECC request. */
    if (io->max_write_payload < sizeof(ecc_request)) return SWFW_TRANSPORT_TOO_SMALL;
    chunk = io->max_write_payload < 32 ? io->max_write_payload : 32;
    chunk &= ~(size_t)3; /* Conservative four-byte-aligned writes, like the selected vendor path. */
    result->chunk_bytes = (uint8_t)chunk;
    result->stage = SWFW_ROM_ID;
    status = read_command(io, result, 0x90, id, 2);
    if (status != 0) return status;
    result->rom_high = id[0]; result->rom_low = id[1];
    if (id[0] != 0x56 || id[1] != 0x62) return SWFW_WRONG_ROM;

    result->stage = SWFW_RAM_WRITE;
    for (size_t offset = 0; offset < length; offset += chunk) {
        size_t count = length - offset < chunk ? length - offset : chunk;
        address[0] = (uint8_t)(offset >> 16);
        address[1] = (uint8_t)(offset >> 8);
        address[2] = (uint8_t)offset;
        status = write_command(io, result, 0xad, address, sizeof(address));
        if (status != 0) return status;
        status = write_command(io, result, 0xae, firmware + offset, count);
        if (status != 0) return status;
        result->bytes_written += (uint32_t)count;
        result->packets_written++;
    }

    result->stage = SWFW_RAM_ECC;
    ecc_request[0] = 0; ecc_request[1] = 0; ecc_request[2] = 0;
    ecc_request[3] = (uint8_t)(length >> 16);
    ecc_request[4] = (uint8_t)(length >> 8);
    ecc_request[5] = (uint8_t)length;
    status = write_command(io, result, 0xcc, ecc_request, sizeof(ecc_request));
    if (status != 0) return status;
    io->delay_ms(io->context, 2);
    io->delay_ms(io->context, 5); /* Selected row uses fixed delay; no CE polling. */
    status = read_command(io, result, 0xcd, id, 2);
    if (status != 0) return status;
    result->device_ecc = (uint16_t)((uint16_t)id[0] << 8 | id[1]);
    if (result->device_ecc != result->host_ecc) return SWFW_ECC_MISMATCH;

    result->stage = SWFW_START_APP;
    result->start_attempted = 1;
    status = write_command(io, result, 0x08, NULL, 0);
    if (status != 0) return status;
    result->start_acknowledged = 1;
    io->delay_ms(io->context, 10);
    result->stage = SWFW_POST_START;
    for (unsigned attempt = 0; attempt < 5; attempt++) {
        status = runtime_id(io, result);
        if (status == SWFW_ALREADY_RUNNING) {
            result->stage = SWFW_DONE;
            return SWFW_READY;
        }
        if (status == SWFW_TRANSPORT_ERROR) return status;
        io->delay_ms(io->context, 200);
    }
    return SWFW_APP_NOT_READY;
}
