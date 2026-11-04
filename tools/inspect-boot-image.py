#!/usr/bin/env python3
"""Read-only structural audit of a HakiOS Android boot image (BootShim + FD + XNUP).

Print section sizes and SHA-256 hashes; optionally compare the embedded build inputs.
This checks packaging, not whether firmware can run on the phone.
"""
import argparse
import hashlib
from pathlib import Path
import struct
import sys
import zlib


def require(condition, message):
    if not condition:
        raise ValueError(message)


def align(size, boundary):
    return (size + boundary - 1) & -boundary


def describe(name, data, detail=""):
    print(f"{name}: {len(data)} bytes{detail}; sha256={hashlib.sha256(data).hexdigest()}")


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def boot_sections(data):
    require(len(data) >= 44 and data[:8] == b"ANDROID!", "missing or truncated Android boot header")
    version = u32(data, 40)
    require(version <= 4, f"unsupported boot header version {version}")
    header_size = (1632, 1648, 1660, 1580, 1584)[version]
    require(len(data) >= header_size, "truncated boot header")
    page = 4096 if version >= 3 else u32(data, 36)
    require(2048 <= page <= 65536 and page & (page - 1) == 0, f"invalid page size {page}")
    require(page >= header_size and len(data) >= page, "truncated header page")
    if version:
        declared = u32(data, 20 if version >= 3 else 1644)
        require(declared == header_size, f"header size {declared}, expected {header_size}")
    kernel_size = u32(data, 8)
    ramdisk_size = u32(data, 12 if version >= 3 else 16)
    require(kernel_size > 0, "empty kernel section")
    print(f"Android boot v{version}: page={page}, header={header_size}, image={len(data)} bytes")
    sections = {}
    cursor = page

    def take(name, size):
        nonlocal cursor
        require(cursor + align(size, page) <= len(data), f"truncated {name} section or page padding")
        sections[name] = data[cursor:cursor + size]
        describe(name, sections[name], f", image offset={cursor:#x}")
        cursor += align(size, page)

    take("kernel", kernel_size)
    take("ramdisk", ramdisk_size)
    if version < 3:
        take("second", u32(data, 24))
        if version >= 1:
            size = u32(data, 1632)
            offset = struct.unpack_from("<Q", data, 1636)[0]
            require(not size or offset == cursor, f"recovery_dtbo offset {offset:#x}, expected {cursor:#x}")
            take("recovery_dtbo", size)
        if version == 2:
            take("header_dtb", u32(data, 1648))
    elif version == 4:
        take("boot_signature", u32(data, 1580))
    if cursor < len(data):
        print(f"Image trailer: {len(data) - cursor} bytes (outside declared boot sections)")
    return sections


def fdt_blobs(data):
    trees = []
    cursor = 0
    while cursor < len(data) and any(data[cursor:]):
        require(len(data) - cursor >= 40, "truncated appended FDT header")
        magic, total, tree, strings, reserve, version, compatible, _, string_size, tree_size = struct.unpack_from(
            ">10I", data, cursor)
        require(magic == 0xD00DFEED, f"unexpected data after gzip at offset {cursor:#x}: expected FDT")
        require(40 <= total <= len(data) - cursor, f"truncated appended FDT: declared {total} bytes")
        require(version >= 17 and compatible <= 17, f"unsupported FDT version {version}/{compatible}")
        require(40 <= tree <= total and tree % 4 == 0 and tree_size <= total - tree, "invalid FDT structure bounds")
        require(40 <= strings <= total and string_size <= total - strings, "invalid FDT strings bounds")
        require(40 <= reserve <= total - 16 and reserve % 8 == 0, "invalid FDT reservation-map offset")
        blob = data[cursor:cursor + total]
        trees.append(blob)
        describe(f"appended FDT[{len(trees) - 1}]", blob, f", version={version}")
        cursor += total
    if cursor < len(data):
        print(f"Appended FDT padding: {len(data) - cursor} zero bytes")
    return trees


def payload_entries(data):
    require(len(data) >= 8 and data[:4] == b"XNUP", "missing XNUP payload header")
    body_size = u32(data, 4)
    end = 8 + body_size
    require(24 <= body_size and end <= len(data), f"truncated XNUP: declared {body_size} body bytes")
    require(end <= 0x04000000 - 0x1000, "XNUP exceeds the boot shim payload limit")
    require(len(data) - end < 16 and not any(data[end:]), "unexpected data after XNUP")
    describe("XNUP", data[:end], f", body={body_size}")
    cursor = 8
    while cursor + 24 <= end:
        name, size, reserved = struct.unpack_from("<16sII", data, cursor)
        if not any(name):
            require(size == reserved == 0 and not any(data[cursor + 24:end]), "invalid XNUP terminator")
            return
        require(reserved == 0, f"nonzero XNUP reserved field at {cursor:#x}")
        name, _, suffix = name.partition(b"\0")
        require(not any(suffix) and all(32 <= c < 127 for c in name), "invalid XNUP entry name")
        start = cursor + 24
        following = start + align(size, 16)
        require(following <= end, f"XNUP entry {name!r} exceeds container bounds")
        require(not any(data[start + size:following]), f"nonzero XNUP padding for {name!r}")
        describe(f"  {name.decode('ascii')}", data[start:start + size], f", XNUP offset={start:#x}")
        cursor = following
    raise ValueError("XNUP has no complete terminating entry")


def inspect(args):
    data = Path(args.image).read_bytes()
    describe("image", data)
    sections = boot_sections(data)
    kernel = sections["kernel"]
    require(kernel[:2] == b"\x1f\x8b", "expected gzip(BootShim + FD + optional XNUP) in kernel section")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    # The shim's FD plus its 64 MiB payload fit comfortably below this limit.
    blob = decoder.decompress(kernel, 128 * 1024 * 1024 + 1)
    require(len(blob) <= 128 * 1024 * 1024 and not decoder.unconsumed_tail, "gzip expands beyond 128 MiB")
    require(decoder.eof, "truncated gzip stream")
    tail = decoder.unused_data
    describe("gzip", kernel[:len(kernel) - len(tail)], f", expanded={len(blob)}")
    trees = fdt_blobs(tail)
    if "header_dtb" in sections and sections["header_dtb"]:
        fdt_blobs(sections["header_dtb"])
    require(len(blob) >= 64 and blob[56:60] == b"ARM\x64", "missing ARM64 image header")
    base, image_size, flags = struct.unpack_from("<QQQ", blob, 8)
    print(f"ARM64: text_offset={base:#x}, image_size={image_size:#x}, flags={flags:#x}")
    # BootShim starts with ADR x1, _Payload; its immediate gives the FD's offset.
    instruction = u32(blob, 0)
    require(instruction & 0x9F00001F == 0x10000001, "expected BootShim ADR x1 instruction")
    shim_size = ((instruction >> 29) & 3) | (((instruction >> 5) & 0x7FFFF) << 2)
    if shim_size & (1 << 20):
        shim_size -= 1 << 21
    require(64 <= shim_size <= len(blob) - 72 and shim_size % 16 == 0, "invalid BootShim FD offset")
    require(blob[shim_size + 40:shim_size + 44] == b"_FVH", "missing firmware-volume header after BootShim")
    fd_size = struct.unpack_from("<Q", blob, shim_size + 32)[0]
    header_size = struct.unpack_from("<H", blob, shim_size + 48)[0]
    require(72 <= header_size <= fd_size and header_size % 2 == 0, "invalid firmware-volume header length")
    require(fd_size <= len(blob) - shim_size, "truncated UEFI FD")
    shim, fd = blob[:shim_size], blob[shim_size:shim_size + fd_size]
    checksum = sum(struct.unpack_from(f"<{header_size // 2}H", fd)) & 0xFFFF
    require(checksum == 0, "invalid firmware-volume header checksum")
    describe("BootShim", shim)
    describe("UEFI FD", fd, f", blob offset={shim_size:#x}")
    remaining = blob[shim_size + fd_size:]
    if remaining:
        payload_entries(remaining)
    else:
        print("XNUP: absent")
    for name, embedded, path in (("shim", shim, args.shim), ("fd", fd, args.fd),
                                 ("dtb", b"".join(trees), args.dtb)):
        if path:
            require(embedded == Path(path).read_bytes(), f"embedded {name} differs from {path}")
            print(f"Compare {name}: matches {path}")
    print("OK: boot image structure is valid; hardware boot is not verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Android boot image made by mkxnuimg.py")
    for name in ("fd", "shim", "dtb"):
        parser.add_argument("--" + name, help=f"require embedded {name} to match this file")
    args = parser.parse_args()
    try:
        inspect(args)
    except (OSError, ValueError, struct.error, zlib.error) as error:
        print(f"inspect-boot-image: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
