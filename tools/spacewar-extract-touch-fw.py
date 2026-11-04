#!/usr/bin/env python3
"""Extract the verified FocalTech blob from the supplied AOSPA factory ZIP.

Reads only boot.img from the ZIP; never mounts or writes a phone partition.
This extractor is pinned to the inspected 20240903 kernel and firmware hashes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zipfile


ap = argparse.ArgumentParser()
ap.add_argument("archive", type=Path)
ap.add_argument("output", type=Path)
args = ap.parse_args()
with zipfile.ZipFile(args.archive) as archive:
    info = archive.getinfo("boot.img")
    boot = archive.read(info)
assert boot[:8] == b"ANDROID!", "Not an Android boot image"
assert struct.unpack_from("<I", boot, 40)[0] == 3, "Expected boot header v3"
size = struct.unpack_from("<I", boot, 8)[0]
kernel = boot[4096:4096 + size]
assert len(kernel) == size
assert hashlib.sha256(kernel).hexdigest() == "94004b2076cbefcf21eae09336a577b6311f667a79a82c73edf1f5e82c9ee2f8", "Different ROM kernel"
offset, length = 0x2D88838, 119284
firmware = kernel[offset:offset + length]
digest = hashlib.sha256(firmware).hexdigest()
assert digest == "04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726", "Firmware does not match the inspected image"
code_length, inverse = struct.unpack_from(">HH", firmware, 0x100)
assert code_length + inverse == 0xFFFF and code_length * 4 == length
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_bytes(firmware)
print(json.dumps({"output": str(args.output), "bytes": length, "sha256": digest,
                  "boot_zip_crc32": f"{info.CRC:08x}", "kernel_offset": hex(offset)}, indent=2))
