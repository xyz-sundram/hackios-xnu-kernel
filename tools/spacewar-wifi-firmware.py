#!/usr/bin/env python3
"""Prepare private Phone (1) Wi-Fi firmware from its verified Android image.

No phone access. The caller supplies an extracted firmware/image directory and
the board ELF actually selected by Android. Files from another device are not
substituted. Output is a new directory suitable for FW_WIFI_DIR in userland/build.sh.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import struct

WPSS_BASE = 0x9AE00000
WPSS_BYTES = 0x01900000


def need(ok, message):
    if not ok:
        raise ValueError(message)


def read_file(path):
    path = Path(path)
    need(path.is_file() and not path.is_symlink(), "missing or linked input: " + str(path))
    return path.read_bytes()


def validate_wpss(image):
    header = read_file(image / "wpss.mdt")
    need(116 <= len(header) <= 16384 and header[:7] == b"\x7fELF\x01\x01\x01",
         "WPSS header is not the expected bounded ELF32 header")
    need(struct.unpack_from("<HH", header, 40) == (52, 32), "unexpected WPSS ELF header sizes")
    count = struct.unpack_from("<H", header, 44)[0]
    need(3 <= count <= 16 and 52 + 32 * count <= len(header), "invalid WPSS segment count")
    need(struct.unpack_from("<I", header, 28)[0] == 52, "unexpected WPSS program-header offset")
    files = {"wpss.mdt": header}
    spans, hash_bytes = [], 0
    for i in range(count):
        typ, offset, va, pa, file_bytes, memory_bytes, flags, align = struct.unpack_from("<8I", header, 52 + i * 32)
        if flags >> 24 & 7 == 2:
            need(hash_bytes == 0, "multiple WPSS hash segments")
            hash_bytes = file_bytes
            continue
        if typ != 1 or memory_bytes == 0:
            continue
        need(WPSS_BASE <= pa and file_bytes <= memory_bytes and memory_bytes <= WPSS_BASE + WPSS_BYTES - pa,
             "WPSS load segment exceeds Phone (1) reservation")
        spans.append((pa, pa + memory_bytes))
        if file_bytes:
            name = "wpss.b%02d" % i
            data = read_file(image / name)
            need(len(data) == file_bytes, "WPSS segment length mismatch: " + name)
            files[name] = data
    spans.sort()
    need(spans and spans[0][0] == WPSS_BASE and all(a[1] <= b[0] for a, b in zip(spans, spans[1:])),
         "WPSS load segments overlap or start outside the reservation")
    need(hash_bytes and 52 + 32 * count + hash_bytes == len(header), "WPSS signed metadata length mismatch")
    return files


def validate_board(data):
    need(84 <= len(data) <= 1024 * 1024 and data[:7] == b"\x7fELF\x01\x01\x01",
         "selected board file is not an ELF32 container")
    need(struct.unpack_from("<HH", data, 16) == (2, 40), "selected board ELF is not the ARM executable format")
    need(struct.unpack_from("<HH", data, 40) == (52, 32), "unexpected board ELF header sizes")
    start, count = struct.unpack_from("<I", data, 28)[0], struct.unpack_from("<H", data, 44)[0]
    need(1 <= count <= 16 and 52 <= start <= len(data) and 32 * count <= len(data) - start,
         "board program-header table exceeds file")
    loads = 0
    for i in range(count):
        typ, offset, va, pa, size, memory, flags, align = struct.unpack_from("<8I", data, start + i * 32)
        if typ == 1:
            need(0 < size <= memory and offset <= len(data) and size <= len(data) - offset,
                 "board ELF load segment exceeds file")
            loads += 1
    need(loads > 0, "board ELF has no load segment")


def station_address(data):
    # Android's private WCNSS file may use Intf0MacAddress=<12 hex digits>.
    # Do not log either this source or the normalized address.
    text = data.decode("ascii").strip()
    if re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", text):
        raw = bytes.fromhex(text.replace(":", ""))
    else:
        matches = re.findall(r"(?m)^Intf0MacAddress=([0-9A-Fa-f]{12})\r?$", text)
        need(len(matches) == 1, "private station-address file has no single Intf0 address")
        raw = bytes.fromhex(matches[0])
    need(not raw[0] & 1 and any(raw), "private station address is not unicast")
    return (":".join("%02x" % b for b in raw) + "\n").encode()


def prepare(image, board_file, mac_file, output):
    image, output = Path(image).resolve(), Path(output)
    need(not output.exists() and not output.is_symlink(), "output already exists")
    need(re.fullmatch(r"[A-Za-z0-9_.-]+", board_file) is not None, "board filename must be a basename")
    files = validate_wpss(image)
    board = read_file(image / "qca6750" / board_file)
    validate_board(board)
    regdb = read_file(image / "qca6750/regdb.bin")
    need(0 < len(regdb) <= 1024 * 1024, "regulatory file size is invalid")
    files["qca6750/wifi-board.elf"] = board
    files["qca6750/wifi-regdb.bin"] = regdb
    if mac_file:
        files["mac"] = station_address(read_file(mac_file))
    manifest = {"format": "spacewar-wifi-private-v1", "private": True,
                "board_source": "qca6750/" + board_file, "board_selection": "explicit Android runtime selection",
                "wpss_reservation": {"base": WPSS_BASE, "bytes": WPSS_BYTES},
                "has_station_address": bool(mac_file), "phone_commands": 0,
                "files": [{"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(files.items())]}
    output.mkdir(mode=0o700, parents=True)
    for name, data in files.items():
        path = output / name
        path.parent.mkdir(exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(output / "manifest.json", 0o600)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-dir", required=True, type=Path)
    parser.add_argument("--board-file", required=True, help="filename selected by this phone's Android, e.g. bdwlan.elf")
    parser.add_argument("--mac-file", type=Path, help="private address file; omitted only for steps 1-14 diagnostics")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = prepare(args.image_dir, args.board_file, args.mac_file, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, "spacewar-wifi-firmware: " + str(error) + "\n")
    print("Prepared %d private firmware files; no phone access. Station-address file: %s." %
          (len(manifest["files"]), "included" if manifest["has_station_address"] else "absent; diagnostics only"))


if __name__ == "__main__":
    main()
