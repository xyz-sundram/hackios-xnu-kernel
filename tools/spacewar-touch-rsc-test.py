#!/usr/bin/env python3
"""Host-test actual Spacewar RSC power helpers with simulated register I/O.

Completion IRQ status is gated by IRQ_ENABLE. Exercises Mu and U-Boot handoffs,
restoration of their distinct IRQ masks, refusal without writes, and poisoning
after an uncertain transaction. Requires a host compiler with ASan and UBSan.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


MOCK = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t registers[RSC_SIZE / 4];
static uintptr_t rsc = 0x10000000;
static uint32_t touch_live;
static bool spacewar_power_poisoned;
static bool lease_held, no_completion, mutate_foreign_irq;
static bool restoring_irq;
static unsigned writes, triggers, irq_acks;
static uint32_t pending, original_control;
static uint64_t now;

#define REG(offset) registers[(offset) / 4]
#define NSEC_PER_USEC 1000

static bool spacewar_power_cpu_safe(void) { return lease_held; }
static void spacewar_power_io_barrier(void) { }
static uint64_t mach_absolute_time(void) { return now; }
static void clock_interval_to_deadline(unsigned interval, unsigned unit, uint64_t *deadline)
{
    *deadline = now + (uint64_t)interval * unit;
}
static void sleep_us(unsigned us) { now += (uint64_t)us * NSEC_PER_USEC; }

static uint32_t rd(uintptr_t address)
{
    assert(address >= rsc && address - rsc < RSC_SIZE);
    uint32_t offset = address - rsc;
    assert(!(offset & 3));
    if (offset == SW_RSC_TCS_BASE + 4)
        return pending & REG(SW_RSC_TCS_BASE);
    return REG(offset);
}

static void wr(uintptr_t address, uint32_t value)
{
    assert(address >= rsc && address - rsc < RSC_SIZE);
    uint32_t offset = address - rsc;
    assert(!(offset & 3));
    writes++;
    if (offset == SW_RSC_TCS_BASE) {
        /* The implementation may change only its own IRQ bit. */
        assert(!((value ^ REG(offset)) & ~SW_POWER_IRQ));
        if (restoring_irq && !(REG(offset) & SW_POWER_IRQ) && (value & SW_POWER_IRQ)) {
            /* Re-enable the inherited bit only after restoring configuration. */
            assert(REG(SW_POWER_BASE + 0x14) == original_control);
            assert(REG(SW_POWER_BASE + 0x10) == 0);
            assert(REG(SW_POWER_BASE + 0x1c) == 0);
            assert(REG(SW_POWER_BASE + 0x30) == 0);
            assert(REG(SW_POWER_BASE + 0x34) == 0);
            assert(REG(SW_POWER_BASE + 0x38) == 0);
        }
        if (!(value & SW_POWER_IRQ) && triggers && irq_acks == triggers)
            restoring_irq = true;
        REG(offset) = value;
        if (mutate_foreign_irq) {
            REG(offset) ^= 1;
            mutate_foreign_irq = false;
        }
        return;
    }
    if (offset == SW_RSC_TCS_BASE + 8) {
        assert(value == SW_POWER_IRQ);
        pending &= ~value;
        irq_acks++;
        return;
    }
    assert(offset == SW_POWER_BASE + 0x10 || offset == SW_POWER_BASE + 0x14 ||
           offset == SW_POWER_BASE + 0x1c || offset == SW_POWER_BASE + 0x30 ||
           offset == SW_POWER_BASE + 0x34 || offset == SW_POWER_BASE + 0x38);
    REG(offset) = value;
    if (offset == SW_POWER_BASE + 0x10 && value == 1)
        restoring_irq = false;
    if (offset == SW_POWER_BASE + 0x14 && (value & TCS_CONTROL_TRIGGER)) {
        triggers++;
        if (!no_completion) {
            pending |= SW_POWER_IRQ;
            REG(SW_POWER_BASE + 0x3c) = SW_POWER_CMD_DONE;
        }
    }
}

static int capture_printf(const char *format, ...) { (void)format; return 0; }
#define printf capture_printf
"""

CASES = r"""
#undef printf
static unsigned cases;

static void reset(uint32_t irq_mask, uint32_t control)
{
    memset(registers, 0, sizeof(registers));
    REG(SW_RSC_ID) = 0x20300;
    REG(SW_RSC_CONFIG) = 0x800c8104;
    REG(SW_RSC_TCS_BASE) = irq_mask;
    REG(SW_RSC_TCS_BASE + 0x18) = 1;
    REG(SW_POWER_BASE + 0x18) = 1;
    REG(SW_POWER_BASE + 0x14) = control;
    original_control = control;
    /* Keep realistic retained command data in the foreign slot. */
    REG(SW_RSC_TCS_BASE + 0x30) = 0x10108;
    REG(SW_RSC_TCS_BASE + 0x34) = 0x40d08;
    REG(SW_RSC_TCS_BASE + 0x38) = 4;
    touch_live = 1;
    lease_held = true;
    spacewar_power_poisoned = no_completion = mutate_foreign_irq = false;
    restoring_irq = false;
    writes = triggers = irq_acks = pending = 0;
    now = 0;
}

static void success(uint32_t irq_mask, uint32_t control)
{
    struct spacewar_slot1_state saved;
    struct spacewar_slot0_state slot0;
    reset(irq_mask, control);
    assert(spacewar_power_preflight(&saved));
    assert(writes == 0);
    assert(spacewar_power_slot0_snapshot(&slot0));
    /* Repeated votes must each restore the inherited handoff state. */
    for (unsigned i = 0; i < 6; i++) {
        assert(spacewar_power_command(0x42500 + i * 4, 1800 + i, &slot0));
        assert(REG(SW_RSC_TCS_BASE) == irq_mask);
        assert(REG(SW_POWER_BASE + 0x14) == control);
        assert(REG(SW_POWER_BASE + 0x10) == 0);
        assert(REG(SW_POWER_BASE + 0x1c) == 0);
        assert(REG(SW_POWER_BASE + 0x30) == 0);
        assert(REG(SW_POWER_BASE + 0x34) == 0);
        assert(REG(SW_POWER_BASE + 0x38) == 0);
        assert(spacewar_power_slot0_unchanged(&slot0));
        assert(!pending && !spacewar_power_poisoned);
    }
    assert(triggers == 6 && irq_acks == 6);
    cases++;
}

static void refused(unsigned condition)
{
    struct spacewar_slot1_state saved;
    struct spacewar_slot0_state slot0;
    reset(3, 0);
    assert(spacewar_power_slot0_snapshot(&slot0));
    switch (condition) {
    case 0: pending = SW_POWER_IRQ; break;
    case 1: REG(SW_POWER_BASE + 0x18) = 0; break;
    case 2: REG(SW_POWER_BASE + 0x14) = TCS_CONTROL_TRIGGER; break;
    case 3: REG(SW_POWER_BASE + 0x1c) = 1; break;
    case 4: REG(SW_POWER_BASE + 0x10) = 1; break;
    case 5: REG(SW_POWER_BASE + 0x30) = 1; break;
    case 6: REG(SW_POWER_BASE + 0x34) = 1; break;
    case 7: REG(SW_POWER_BASE + 0x38) = 1; break;
    case 8: lease_held = false; break;
    case 9: touch_live = 0; break;
    case 10: REG(SW_RSC_ID) = 0x30300; break;
    case 11: REG(SW_RSC_CONFIG) ^= 1; break;
    default: assert(false);
    }
    uint32_t before[sizeof(registers) / sizeof(registers[0])];
    memcpy(before, registers, sizeof(before));
    assert(!spacewar_power_preflight(&saved));
    assert(!spacewar_power_command(0x42500, 1800, &slot0));
    assert(spacewar_power_poisoned && writes == 0);
    assert(!memcmp(before, registers, sizeof(before)));
    assert(!spacewar_power_command(0x42500, 1800, &slot0));
    assert(writes == 0);
    cases++;
}

static void foreign_change(bool status)
{
    struct spacewar_slot0_state slot0;
    reset(3, 0);
    assert(spacewar_power_slot0_snapshot(&slot0));
    if (status)
        pending |= 1;
    else
        REG(SW_RSC_TCS_BASE) ^= 1;
    assert(!spacewar_power_command(0x42500, 1800, &slot0));
    assert(writes == 0 && spacewar_power_poisoned);
    cases++;
}

static void uncertain(bool foreign_mutation)
{
    struct spacewar_slot0_state slot0;
    reset(3, 0);
    assert(spacewar_power_slot0_snapshot(&slot0));
    no_completion = !foreign_mutation;
    mutate_foreign_irq = foreign_mutation;
    assert(!spacewar_power_command(0x42500, 1800, &slot0));
    assert(spacewar_power_poisoned && writes > 0);
    if (!foreign_mutation) {
        assert(triggers == 1 && irq_acks == 0);
        assert(now >= (uint64_t)SW_POWER_TIMEOUT_US * NSEC_PER_USEC);
    } else {
        assert(triggers == 0);
    }
    unsigned stopped_at = writes;
    assert(!spacewar_power_command(0x42500, 1800, &slot0));
    assert(writes == stopped_at);
    cases++;
}

int main(void)
{
    success(1, TCS_CONTROL_ENABLE); /* Existing Mu handoff. */
    success(3, 0);                  /* Observed U-Boot handoff. */
    success(1, 0);
    success(3, TCS_CONTROL_ENABLE);
    for (unsigned condition = 0; condition < 12; condition++)
        refused(condition);
    foreign_change(false);
    foreign_change(true);
    uncertain(false);
    uncertain(true);
    /* Prove the mock gates a raw completion by IRQ_ENABLE. */
    reset(1, 0);
    pending = SW_POWER_IRQ;
    assert(!(rd(rsc + SW_RSC_TCS_BASE + 4) & SW_POWER_IRQ));
    REG(SW_RSC_TCS_BASE) = 3;
    assert(rd(rsc + SW_RSC_TCS_BASE + 4) & SW_POWER_IRQ);
    printf("PASS: %u actual RSC helper cases; Mu IRQ1/U-Boot IRQ3 restored; "
           "24 complete votes; pending/busy/stale/no-lease refusals have no writes; "
           "foreign-state checks and timeout poisoning prevent retries\n", cases);
    return 0;
}
"""


def function(source: str, name: str) -> str:
    pattern = r"^static bool\s+" + re.escape(name) + r"\([^;]*?\)\n\{"
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        raise ValueError(f"cannot find actual helper {name}")
    end = source.index("\n}", match.end()) + 2
    return source[match.start():end] + "\n"


def harness(source: str) -> str:
    names = (
        "RSC_SIZE", "SW_RSC_ID", "SW_RSC_CONFIG", "SW_RSC_TCS_BASE",
        "SW_RSC_TCS_STRIDE", "SW_RSC_MAX_COMMANDS", "SW_POWER_SLOT",
        "SW_POWER_IRQ", "SW_POWER_BASE", "SW_POWER_TIMEOUT_US",
        "SW_POWER_POLLS", "SW_POWER_CMD_DONE", "TCS_CONTROL_ENABLE",
        "TCS_CONTROL_TRIGGER", "MSGID_WRITE_4_BYTES", "MSGID_RESPONSE",
    )
    defines = []
    for name in names:
        match = re.search(r"^#define\s+" + name + r"\s+[^\n]+", source, re.MULTILINE)
        if not match:
            raise ValueError(f"cannot find source constant {name}")
        defines.append(match.group(0))
    start = source.index("struct spacewar_slot1_state {")
    end = source.index("\nstatic uint64_t", start)
    structs = source[start:end]
    names = (
        "spacewar_rsc_read", "spacewar_power_wait", "spacewar_power_write",
        "spacewar_power_irq_enable", "spacewar_power_preflight",
        "spacewar_power_slot0_snapshot", "spacewar_power_slot0_unchanged",
        "spacewar_power_slot0_no_conflict", "spacewar_power_command",
    )
    functions = "".join(function(source, name) for name in names)
    return "\n".join(defines) + "\n" + MOCK + structs + functions + CASES


def main() -> None:
    repository = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path,
        default=repository / "drivers/input/hakios.goodix.c",
        help="Spacewar driver source containing the actual power helpers",
    )
    args = parser.parse_args()
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    if not compiler:
        parser.error("HOSTCC must name a host C compiler")
    with tempfile.TemporaryDirectory(prefix="spacewar-rsc-test-") as temporary:
        work = Path(temporary)
        source = work / "test.c"
        binary = work / "test"
        source.write_text(harness(args.source.read_text()))
        subprocess.run(
            compiler + [
                "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
                str(source), "-o", str(binary),
            ],
            check=True,
        )
        subprocess.run(
            [str(binary)], check=True,
            env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"},
        )


if __name__ == "__main__":
    main()
