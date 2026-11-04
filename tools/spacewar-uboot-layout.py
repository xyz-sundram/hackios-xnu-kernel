#!/usr/bin/env python3
"""Validate Phone (1) U-Boot's ELF/BSS, initial stack and ARM64 image layout.

No phone calls or third-party dependencies. The BSS intentionally reuses the
relocation-table area after relocation, but must never overlap copied text/data.
An LLD build with a misinterpreted GNU OVERLAY can otherwise pass the relocation
type check while placing BSS at zero and the initial stack inside the control DTB.
"""
import argparse
from pathlib import Path
import struct
import sys


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def span(data, offset, size, what):
    require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset,
            f"{what} lies outside its file")
    return data[offset:offset + size]


def cstring(data, offset):
    require(0 <= offset < len(data), "ELF string offset is outside its string table")
    end = data.find(b"\0", offset)
    require(end >= 0, "ELF string is not terminated")
    return data[offset:end].decode("utf-8", errors="replace")


def elf_layout(data):
    require(len(data) >= 64, "ELF header is truncated")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    require(header[0][:7] == b"\x7fELF\x02\x01\x01", "expected a little-endian ELF64 file")
    require(header[2] == 183, "ELF machine is not AArch64")
    shoff, shentsize, shnum, shstrndx = header[6], header[11], header[12], header[13]
    require(shnum > 0 and shentsize >= 64, "ELF has no supported section table")
    span(data, shoff, shnum * shentsize, "ELF section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize)
                for i in range(shnum)]
    require(0 < shstrndx < shnum, "ELF section-name table is missing")
    names_section = sections[shstrndx]
    names = span(data, names_section[4], names_section[5], "ELF section-name table")
    by_name = {cstring(names, section[0]): section for section in sections}
    symbols = {}
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB, including the linker-defined symbols
            continue
        require(section[9] >= 24 and section[5] % section[9] == 0,
                "ELF symbol table has an invalid entry size")
        require(section[6] < shnum, "ELF symbol string table is missing")
        strings_section = sections[section[6]]
        strings = span(data, strings_section[4], strings_section[5], "ELF symbol string table")
        span(data, section[4], section[5], "ELF symbol table")
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, _info, _other, index, value, _size = struct.unpack_from("<IBBHQQ", data, offset)
            if name and index:  # skip undefined symbols
                symbols[cstring(strings, name)] = value
    for name in ("_start", "_end", "__image_copy_end", "__bss_start", "__bss_end"):
        require(name in symbols, f"ELF linker symbol {name} is missing")
    require(".bss" in by_name, "ELF .bss section is missing")
    return symbols, by_name[".bss"], header[4]


def fdt_size(data, offset):
    header = span(data, offset, 40, "appended control DTB header")
    fields = struct.unpack(">10I", header)
    require(fields[0] == 0xD00DFEED, "appended control tree has no FDT magic")
    size, off_struct, off_strings, off_reserve = fields[1:5]
    require(size >= 40, "appended control DTB has an invalid total size")
    span(data, offset, size, "appended control DTB")
    require(40 <= off_reserve <= size - 16, "control DTB memory-reservation offset is invalid")
    require(40 <= off_struct <= size and fields[9] <= size - off_struct,
            "control DTB structure block is outside the tree")
    require(40 <= off_strings <= size and fields[8] <= size - off_strings,
            "control DTB string block is outside the tree")
    return size


def validate(elf, binary, stack_offset=0x180000, control_dtb=None):
    require(stack_offset > 0, "initial stack offset must be positive")
    symbols, bss, entry = elf_layout(elf)
    start, end = symbols["_start"], symbols["_end"]
    copy_end = symbols["__image_copy_end"]
    bss_start, bss_end = symbols["__bss_start"], symbols["__bss_end"]
    require(entry == start, "ELF entry does not point to _start")
    require(start < copy_end <= end, "ELF copied text/data bounds are invalid")
    require(bss_start > start and bss_start >= copy_end,
            f"BSS starts at {bss_start:#x}, before copied text/data ends at {copy_end:#x}")
    require(bss_end > bss_start and bss_start % 8 == 0 and bss_end % 8 == 0,
            "BSS bounds are empty, reversed or not 8-byte aligned")
    require(bss[2] & 2, "ELF .bss lacks SHF_ALLOC; the linker did not allocate its runtime memory")
    require(bss[3] <= bss_start and bss_end <= bss[3] + bss[5],
            "BSS linker symbols do not lie inside the ELF .bss section")
    require(len(binary) >= 64 and binary[0x38:0x3c] == b"ARM\x64",
            "binary is missing the ARM64 Linux image header")
    text_offset, image_size, flags = struct.unpack_from("<QQQ", binary, 8)
    require(image_size >= len(binary),
            f"ARM64 image_size {image_size:#x} is smaller than binary size {len(binary):#x}")
    initial_sp = bss_start - start + stack_offset
    require(initial_sp <= image_size,
            f"initial SP offset {initial_sp:#x} exceeds advertised image_size {image_size:#x}")
    dtb_offset = end - start
    require(dtb_offset <= len(binary), "ELF _end extends beyond the flat binary")
    dtb_end = dtb_offset
    if control_dtb is not None:
        require(fdt_size(control_dtb, 0) == len(control_dtb),
                "supplied control DTB has trailing or missing bytes")
        require(binary[dtb_offset:] == control_dtb,
                "flat binary does not append the supplied control DTB exactly at ELF _end")
        dtb_end += len(control_dtb)
    elif dtb_offset < len(binary):
        dtb_end += fdt_size(binary, dtb_offset)
        require(dtb_end == len(binary), "bytes after the appended control DTB are unaccounted for")
    require(not dtb_offset <= initial_sp < dtb_end,
            f"initial SP offset {initial_sp:#x} lies inside control DTB [{dtb_offset:#x}, {dtb_end:#x})")
    if "_TEXT_BASE" in symbols:
        preferred = struct.unpack("<Q", span(binary, symbols["_TEXT_BASE"] - start, 8,
                                            "preferred image base"))[0]
        require(preferred == start, "embedded preferred image base does not match ELF _start")
    return {"bss_start": bss_start, "bss_end": bss_end, "initial_sp_offset": initial_sp,
            "control_dtb_offset": dtb_offset, "control_dtb_end": dtb_end,
            "binary_size": len(binary), "image_size": image_size,
            "text_offset": text_offset, "flags": flags}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--stack-offset", type=lambda value: int(value, 0), default=0x180000)
    parser.add_argument("--dtb", type=Path, help="assert this exact control DTB is appended at ELF _end")
    args = parser.parse_args(argv)
    try:
        layout = validate(args.elf.read_bytes(), args.binary.read_bytes(), args.stack_offset,
                          args.dtb.read_bytes() if args.dtb else None)
    except (Invalid, OSError, struct.error) as error:
        print(f"spacewar-uboot-layout: REFUSED: {error}", file=sys.stderr)
        return 1
    print("spacewar-uboot-layout: PASS: "
          f"BSS {layout['bss_start']:#x}..{layout['bss_end']:#x}; "
          f"initial SP +{layout['initial_sp_offset']:#x}; "
          f"control DTB +{layout['control_dtb_offset']:#x}..+{layout['control_dtb_end']:#x}; "
          f"image_size {layout['image_size']:#x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
