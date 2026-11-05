#ifndef SPACEWAR_SCANOUT_H
#define SPACEWAR_SCANOUT_H

#include <stdint.h>

static inline bool
spacewarScanoutBacking(uint64_t physical, uint64_t contiguous, uint32_t span)
{
    return span != 0 && span <= 32u * 1024 * 1024 && physical >= 0x80000000ULL &&
        physical <= 0x100000000ULL - span && !(physical & 0x3fff) && contiguous >= span;
}

/* Only fully rendered buffers are submitted. CTL's pending bit must clear and
 * its hardware frame counter must advance before the previous front is reused. */
template <typename Ops>
static bool
spacewarScanoutLatch(Ops& ops, uint32_t address)
{
    if (!ops.owned() || !ops.complete() || ops.pending() != 0) return false;
    uint32_t frame = ops.frame();
    uint64_t began = ops.now();
    ops.address(address);
    ops.flush();
    do {
        if (!ops.owned()) return false;
        if (ops.pending() == 0 && ops.frame() != frame) return true;
        ops.pause();
    } while (ops.now() - began < 60000);
    return false;
}

/* Validate a measured row-counter step before using it as a scanout clock.
 * A burst-only transfer counter or a stopped counter cannot pace row copies. */
static inline bool
spacewarScanoutCounterStep(uint32_t before, uint32_t after, uint64_t elapsed,
    uint32_t lines, uint64_t period)
{
    if (period == 0 || lines == 0 || before >= lines || after >= lines ||
        elapsed < period / 32 || elapsed > period / 2) return false;
    uint64_t expected = elapsed * lines / period;
    uint32_t advanced = (after + lines - before) % lines;
    uint64_t difference = advanced > expected ? advanced - expected : expected - advanced;
    return difference <= expected / 10 + 4;
}

/* Read-only walk of the retained display's AArch64, 4K, three-level stage-1
 * mapping. A successful walk does not authorize changing firmware's tables. */
static inline bool
spacewarScanoutTranslate(uint64_t ttbr, uint32_t tcr, uint32_t cba2r,
    uint64_t iova, uint64_t *physical, bool (*readEntry)(uint64_t, uint64_t *))
{
    unsigned t0sz = tcr & 63;
    if (!(cba2r & 1) || (tcr & (3u << 14)) || (tcr & (1u << 7)) ||
        t0sz < 25 || t0sz > 32 || iova >= (1ULL << (64 - t0sz))) return false;
    uint64_t table = ttbr & 0x0000fffffffff000ULL;
    for (unsigned level = 1; level <= 3; level++) {
        unsigned shift = 39 - 9 * level;
        uint64_t entry = 0;
        uint64_t at = table + ((iova >> shift) & 511) * 8;
        if (table < 0x80000000ULL || table >= 0x280000000ULL ||
            !readEntry(at, &entry) || !(entry & 1)) return false;
        if (level < 3 && (entry & 3) == 3) {
            table = entry & 0x0000fffffffff000ULL;
            continue;
        }
        if (!(entry & (1u << 10)) || (level == 3 && (entry & 3) != 3)) return false;
        uint64_t mask = (1ULL << shift) - 1;
        *physical = (entry & 0x0000fffffffff000ULL & ~mask) | (iova & mask);
        return true;
    }
    return false;
}

#endif
