/* M146 firmware is a private build input, never source-controlled bytes.
 * Generate it using tools/spacewar-touch-firmware.py after xnu/apply.sh.
 * The relative include is resolved from the copied bsd/dev/ wrapper. */
#ifndef HAKIOS_SPACEWAR_M146_FW_H
#define HAKIOS_SPACEWAR_M146_FW_H
#if HAKIOS_DEVICE_SPACEWAR
#if !__has_include("../../BUILD/hakios.spacewar.focaltech_blob.h")
#error Run tools/spacewar-touch-firmware.py with your ROM-matched M146 firmware before building Spacewar
#endif
#include "../../BUILD/hakios.spacewar.focaltech_blob.h"
#else
/* Other boards cannot enter the guarded Spacewar firmware path. */
#define SPACEWAR_M146_FW_LENGTH 1u
#define SPACEWAR_M146_FW_ECC 0u
static const uint8_t spacewar_m146_fw[SPACEWAR_M146_FW_LENGTH] = { 0 };
#endif
#endif
