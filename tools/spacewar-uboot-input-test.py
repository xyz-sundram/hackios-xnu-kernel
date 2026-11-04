#!/usr/bin/env python3
"""Host regression for U-Boot input registration before jump-table setup.

Extract the real registration and console functions from a patched U-Boot tree.
Stub the device registry, hardware I/O and environment, then check deferred and
late assignment through console_init_r(). Removing the guard must reproduce the
original null-pointer write under UBSan. Requires a host C compiler with UBSan.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


HOST_STUBS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#define stdin 0
#define stdout 1
#define stderr 2
#define MAX_FILES 3
#define DEV_FLAGS_INPUT 1
#define DEV_FLAGS_OUTPUT 2
#define GD_FLG_DEVINIT 1
#define CONFIG_SYS_CONSOLE_INFO_QUIET 1
#define CONFIG_SYS_CONSOLE_ENV_OVERWRITE 0
#define CONFIG_ENV_SUPPORT 1
#define CONFIG_IS_ENABLED(x) CONFIG_##x
#define IS_ENABLED(x) x
#define PRE_CONSOLE_FLUSHPOINT1_SERIAL 0
#define PRE_CONSOLE_FLUSHPOINT2_EVERYTHING_BUT_SERIAL 1
#define STDIO_DEV_ASSIGN_FLUSH(...) do { } while (0)
#define INPUTSTDIO_TRACE(...) do { } while (0)
#define OVERWRITE_CONSOLE overwrite

struct jt_funcs {
    int (*getc)(void);
    int (*tstc)(void);
    void (*putc)(char);
    void (*puts)(const char *);
    int (*printf)(const char *, ...);
};

struct global_data {
    struct jt_funcs *jt;
    unsigned long flags;
};

struct stdio_dev {
    int flags;
    char name[32];
    int (*start)(struct stdio_dev *);
};

static struct global_data global;
static struct global_data *gd = &global;
static struct stdio_dev *stdio_devices[MAX_FILES];
static struct stdio_dev *registry[8];
static char *stdio_names[MAX_FILES] = { "stdin", "stdout", "stderr" };
static unsigned registered, keyboard_starts, registration_calls;
static int register_error, start_error, overwrite;
static char *env_stdin = "button-kbd";

int console_assign(int file, const char *name);

static bool console_needs_start_stop(int file, struct stdio_dev *dev)
{
    (void)file;
    (void)dev;
    return true;
}

static int getchar(void) { return 1; }
static int tstc(void) { return 1; }
static void putc(char c) { (void)c; }
static void puts(const char *s) { (void)s; }
static int printf(const char *fmt, ...) { (void)fmt; return 0; }
static int serial_getc(void) { return -1; }
static int serial_tstc(void) { return 0; }
static void serial_putc(char c) { (void)c; }
static void serial_puts(const char *s) { (void)s; }
static int serial_printf(const char *fmt, ...) { (void)fmt; return 0; }

static char *env_get(const char *name)
{
    return !strcmp(name, "stdin") ? env_stdin : "vidconsole";
}

static int env_set(const char *name, const char *value)
{
    (void)name;
    (void)value;
    return 0;
}

static int stdio_register(struct stdio_dev *dev)
{
    registration_calls++;
    if (register_error)
        return register_error;
    assert(registered < sizeof(registry) / sizeof(registry[0]));
    registry[registered++] = dev;
    return 0;
}

static struct stdio_dev *stdio_get_by_name(const char *name)
{
    for (unsigned i = 0; i < registered; i++) {
        if (name && !strcmp(name, registry[i]->name))
            return registry[i];
    }
    return NULL;
}

static int stdio_file_to_flags(int file)
{
    if (file == stdin)
        return DEV_FLAGS_INPUT;
    if (file == stdout || file == stderr)
        return DEV_FLAGS_OUTPUT;
    return -1;
}

static bool console_update_silent(void) { return false; }
static void stdio_print_current_devices(void) { }
static void print_pre_console_buffer(int phase) { (void)phase; }

static int keyboard_start(struct stdio_dev *dev)
{
    (void)dev;
    keyboard_starts++;
    return start_error;
}

/* These tests select one device per stream. Both initialization branches
 * exercise the real console_assign() and console_setfile() below.
 */
static int iomux_doenv(int file, const char *name)
{
    return console_assign(file, name);
}
"""

CONSOLE_DOENV_STUB = r"""
static void console_doenv(int file, struct stdio_dev *dev)
{
    console_assign(file, dev->name);
}
"""

TEST_CASES = r"""
static struct jt_funcs jt;
static struct stdio_dev keyboard = {
    DEV_FLAGS_INPUT, "button-kbd", keyboard_start
};
static struct stdio_dev video = {
    DEV_FLAGS_OUTPUT, "vidconsole", NULL
};

static void reset(void)
{
    memset(&global, 0, sizeof(global));
    memset(&jt, 0, sizeof(jt));
    memset(registry, 0, sizeof(registry));
    memset(stdio_devices, 0, sizeof(stdio_devices));
    registered = keyboard_starts = registration_calls = 0;
    register_error = start_error = overwrite = 0;
    env_stdin = "button-kbd";
}

int main(int argc, char **argv)
{
    (void)argv;
    reset();
    if (argc > 1) {
        /* The original code must fault in the real console_setfile(). */
        assert(input_stdio_register(&keyboard) == 0);
        return 0;
    }

    /* 1: stdio_add_devices() runs with no jump table. */
    assert(input_stdio_register(&keyboard) == 0);
    assert(registration_calls == 1 && registered == 1);
    assert(gd->jt == NULL && keyboard_starts == 0);
    assert(stdio_devices[stdin] == NULL);

    /* 2: console_init_r() activates it after jumptable_init(). */
    gd->jt = &jt;
    assert(stdio_register(&video) == 0);
    assert(console_init_r() == 0);
    assert(keyboard_starts == 1 && stdio_devices[stdin] == &keyboard);
    assert(jt.getc == getchar && jt.tstc == tstc);
    assert(gd->flags & GD_FLG_DEVINIT);

    /* 3: Late registration still assigns the selected keyboard. */
    reset();
    gd->jt = &jt;
    assert(input_stdio_register(&keyboard) == 0);
    assert(keyboard_starts == 1 && stdio_devices[stdin] == &keyboard);
    assert(jt.getc == getchar);

    /* 4: Another selected input does not start this keyboard. */
    reset();
    gd->jt = &jt;
    env_stdin = "nulldev";
    assert(input_stdio_register(&keyboard) == 0);
    assert(registered == 1 && keyboard_starts == 0);
    assert(stdio_devices[stdin] == NULL);

    /* 5: Missing stdin preserves registration without assignment. */
    reset();
    gd->jt = &jt;
    env_stdin = NULL;
    assert(input_stdio_register(&keyboard) == 0);
    assert(registered == 1 && keyboard_starts == 0);

    /* 6: Preserve the existing console-start failure result. */
    reset();
    gd->jt = &jt;
    start_error = -EIO;
    assert(input_stdio_register(&keyboard) == -1);
    assert(keyboard_starts == 1 && stdio_devices[stdin] == NULL);
    assert(jt.getc == NULL);

    /* 7: Preserve the existing overwrite short-circuit result. */
    reset();
    gd->jt = &jt;
    overwrite = 1;
    assert(input_stdio_register(&keyboard) == -1);
    assert(registered == 1 && keyboard_starts == 0);
    assert(stdio_devices[stdin] == NULL);

    /* 8: Preserve the existing registration-failure return behavior. */
    reset();
    register_error = -ENOMEM;
    assert(input_stdio_register(&keyboard) == 0);
    assert(registered == 0 && keyboard_starts == 0);
    return 0;
}
"""


def extract_function(source: str, name: str, occurrence: int = 0) -> str:
    """Extract a function whose closing brace starts a line, as in U-Boot."""
    pattern = (
        r"^(?:static\s+)?(?:inline\s+)?(?:[\w]+\s+)*(?:\*\s*)?"
        + re.escape(name)
        + r"\([^;]*?\)\n\{"
    )
    matches = list(re.finditer(pattern, source, re.MULTILINE))
    if len(matches) <= occurrence:
        raise ValueError(f"cannot find function {name}")
    match = matches[occurrence]
    end = source.index("\n}", match.end()) + 2
    return source[match.start():end] + "\n"


def console_functions(source: str) -> str:
    names = ("console_start", "console_setfile", "console_search_dev", "console_assign")
    functions = "".join(extract_function(source, name) for name in names)
    functions += CONSOLE_DOENV_STUB
    # The first definition is the SYS_CONSOLE_IS_IN_ENV implementation.
    return functions + extract_function(source, "console_init_r")


def run_tests(tree: Path, compiler: list[str]) -> None:
    console_source = (tree / "common/console.c").read_text()
    input_source = (tree / "drivers/input/input.c").read_text()
    fixed_input = extract_function(input_source, "input_stdio_register")
    guard = "gd->jt && cstdin"
    if fixed_input.count(guard) != 1:
        raise ValueError("apply input-console-init-order.patch before running this test")
    original_input = fixed_input.replace(guard, "cstdin", 1)
    functions = console_functions(console_source)

    with tempfile.TemporaryDirectory(prefix="spacewar-input-order-") as temporary:
        work = Path(temporary)
        for mux in (0, 1):
            for version, registration in (("original", original_input), ("fixed", fixed_input)):
                name = f"{version}-mux{mux}"
                source = work / f"{name}.c"
                binary = work / name
                source.write_text(HOST_STUBS + functions + registration + TEST_CASES)
                subprocess.run(
                    compiler + [
                        "-std=gnu11", "-O1", "-g", "-fno-builtin",
                        f"-DCONFIG_CONSOLE_MUX={mux}",
                        "-fsanitize=undefined", "-fno-sanitize-recover=all",
                        str(source), "-o", str(binary),
                    ],
                    check=True,
                )
                command = [str(binary)]
                if version == "original":
                    command.append("reproduce")
                result = subprocess.run(command, capture_output=True, text=True)
                if version == "original":
                    if result.returncode == 0 or "null pointer" not in result.stderr:
                        raise RuntimeError(f"{name} did not reproduce the null write:\n{result.stderr}")
                elif result.returncode != 0:
                    raise RuntimeError(f"{name} failed:\n{result.stderr}")

    print(
        "PASS: original reproduces the NULL jump-table write; fixed passes "
        "16 registration and late console cases (CONSOLE_MUX=0 and =1)"
    )


def main() -> None:
    repository = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree", type=Path,
        default=Path(os.environ.get("U_BOOT", str(repository / "u-boot"))),
        help="patched U-Boot tree (default: U_BOOT or repository/u-boot)",
    )
    args = parser.parse_args()
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    if not compiler:
        parser.error("HOSTCC must name a host C compiler")
    run_tests(args.tree.resolve(), compiler)


if __name__ == "__main__":
    main()
