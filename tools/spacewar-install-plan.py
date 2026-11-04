#!/usr/bin/env python3
"""Prepare private Phone (1) install files from verified host backups only.

No phone connection, block-device access, flashing, or shell commands are used.
The output is a new private directory containing a reviewable plan, two install
maps, and changed GPT metadata ranges. It never overwrites an existing output.

The caller supplies the reviewed backup manifest hash and disk identity. All
backup files, both GPT copies, and the preserved bytes are checked independently.
The ROOT-only install.map is for the first test; install-persist.map additionally
authorizes the exact planned data extent. Neither file installs anything.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import stat
import struct
import sys
import uuid
import zlib

MIB = 1 << 20
U64_MAX = (1 << 64) - 1
MAX_GPT_BYTES = 4 * MIB
APFS_TYPE = uuid.UUID("7c3457ef-0000-11aa-aa11-00306543ecac")
HFS_TYPE = uuid.UUID("48465300-0000-11aa-aa11-00306543ecac")
MAGIC, VERSION, MAP_SIZE, ROOT, DATA = 0x31495753, 1, 152, 1, 2
DISK = "/dev/block/sde"   # manifest identity only; this path is never opened
LUN = 4
ROOT_ENTRY, DATA_ENTRY = 69, 70  # one-based GPT entry numbers
ROOT_BYTES, DATA_BYTES = 2560 * MIB, 1536 * MIB


class Failure(Exception):
    """An input did not satisfy the planner's fixed bounds or identity."""


def need(condition, message):
    if not condition:
        raise Failure(message)


def integer(value, name, low=0, high=U64_MAX):
    need(type(value) is int and low <= value <= high, "invalid " + name)
    return value


def digest(value, name):
    need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
         "invalid SHA-256 for " + name)
    return value


def regular(path):
    path = Path(path)
    need(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode),
         "input must be a regular host file: " + str(path))
    return path


def hash_file(path, with_crc=False):
    path = regular(path)
    before = path.stat()
    h, crc, total = hashlib.sha256(), 0, 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(MIB), b""):
            h.update(chunk)
            if with_crc:
                crc = zlib.crc32(chunk, crc)
            total += len(chunk)
    after = path.stat()
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
          before.st_ctime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
          after.st_ctime_ns) and total == before.st_size,
         "input changed while hashing: " + str(path))
    return total, h.hexdigest(), crc & 0xffffffff


def backup_path(directory, filename):
    need(isinstance(filename, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename),
         "invalid backup filename")
    path = directory / filename
    need(path.resolve().parent == directory, "backup file escapes its directory")
    return regular(path)


def crc(data):
    return zlib.crc32(data) & 0xffffffff


def header(data, here, other, blocks, block_size):
    need(len(data) == block_size, "truncated GPT header")
    get32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
    get64 = lambda offset: struct.unpack_from("<Q", data, offset)[0]
    length = get32(12)
    need(data[:8] == b"EFI PART" and get32(8) == 0x10000 and
         92 <= length <= block_size and get32(20) == 0 and
         get64(24) == here and get64(32) == other, "invalid GPT header location or fields")
    checked = bytearray(data[:length])
    struct.pack_into("<I", checked, 16, 0)
    need(crc(checked) == get32(16), "GPT header CRC mismatch")
    count, entry_size = get32(80), get32(84)
    need(entry_size == 128 and 0 < count <= MAX_GPT_BYTES // entry_size,
         "unsupported or oversized GPT entry array")
    first, last = get64(40), get64(48)
    need(2 <= first <= last < blocks - 1, "invalid GPT usable bounds")
    disk_guid = uuid.UUID(bytes_le=data[56:72])
    need(disk_guid.int != 0, "zero GPT disk GUID")
    return {"header_lba": here, "header_crc32": get32(16),
            "first_usable": first, "last_usable": last, "disk_guid": str(disk_guid),
            "entries_lba": get64(72), "entry_count": count, "entry_size": entry_size,
            "entries_crc32": get32(88)}


def read_range(pieces, block, first, count):
    need(count > 0, "empty metadata read")
    for piece in pieces:
        start = piece["skip_blocks"]
        length = piece["blocks"]
        if first >= start and first - start <= length and count <= length - (first - start):
            at = (first - start) * block
            return piece["content"][at:at + count * block]
    raise Failure("GPT metadata is missing from backed-up ranges")


def metadata_ranges(primary, backup, block, blocks):
    sectors = (primary["entry_count"] * primary["entry_size"] + block - 1) // block
    first, last = primary["entries_lba"], backup["entries_lba"]
    need(2 <= first <= primary["first_usable"] and
         sectors <= primary["first_usable"] - first and
         primary["last_usable"] < last < blocks - 1 and
         sectors <= blocks - 1 - last, "GPT array overlaps usable data or headers")
    merged = []
    for start, length in sorted([(0, 1), (1, 1), (first, sectors),
                                 (last, sectors), (blocks - 1, 1)]):
        if merged and merged[-1][0] + merged[-1][1] == start:
            merged[-1] = (merged[-1][0], merged[-1][1] + length)
        else:
            need(not merged or merged[-1][0] + merged[-1][1] < start,
                 "overlapping GPT metadata ranges")
            merged.append((start, length))
    return merged


def verify_gpt(geometry, pieces):
    block = integer(geometry["block_size"], "block size")
    need(block in (512, 4096), "unsupported logical block size")
    blocks = integer(geometry["blocks"], "disk blocks", 6)
    size = integer(geometry["bytes"], "disk bytes", 1)
    need(blocks <= U64_MAX // block and size == blocks * block, "disk geometry overflow or mismatch")
    for piece in pieces:
        start = integer(piece["skip_blocks"], "range start")
        length = integer(piece["blocks"], "range blocks", 1)
        need(start < blocks and length <= blocks - start and
             piece["bytes"] == length * block and len(piece["content"]) == length * block,
             "backup metadata range exceeds disk")
    primary_bytes = read_range(pieces, block, 1, 1)
    backup_bytes = read_range(pieces, block, blocks - 1, 1)
    primary = header(primary_bytes, 1, blocks - 1, blocks, block)
    backup = header(backup_bytes, blocks - 1, 1, blocks, block)
    need(primary == geometry["primary"] and backup == geometry["backup"],
         "GPT header does not match reviewed manifest geometry")
    shared = ("first_usable", "last_usable", "disk_guid", "entry_count", "entry_size", "entries_crc32")
    need(all(primary[key] == backup[key] for key in shared), "primary/backup GPT metadata disagree")
    expected = metadata_ranges(primary, backup, block, blocks)
    actual = sorted((piece["skip_blocks"], piece["blocks"]) for piece in pieces)
    need(actual == expected, "backup ranges do not exactly cover GPT metadata")
    length = primary["entry_count"] * primary["entry_size"]
    sectors = (length + block - 1) // block
    entries = read_range(pieces, block, primary["entries_lba"], sectors)[:length]
    mirror = read_range(pieces, block, backup["entries_lba"], sectors)[:length]
    need(entries == mirror and crc(entries) == primary["entries_crc32"],
         "GPT entry arrays differ or fail CRC")
    mbr = read_range(pieces, block, 0, 1)
    need(mbr[510:512] == b"\x55\xaa" and mbr[450] == 0xee, "missing protective MBR")
    return {"geometry": geometry, "pieces": pieces, "primary": primary,
            "backup": backup, "entries": entries}


def verify_backup(directory, manifest_sha256):
    directory = Path(directory).resolve()
    need(directory.is_dir(), "backup directory does not exist")
    manifest_path = backup_path(directory, "manifest.json")
    raw = manifest_path.read_bytes()
    actual_manifest_hash = hashlib.sha256(raw).hexdigest()
    need(actual_manifest_hash == digest(manifest_sha256, "reviewed manifest"),
         "reviewed manifest SHA-256 mismatch")
    manifest = json.loads(raw)
    need(isinstance(manifest.get("partitions"), list) and manifest["partitions"] and
         isinstance(manifest.get("tables"), list) and manifest["tables"] and
         isinstance(manifest.get("gpt_geometry"), list) and manifest["gpt_geometry"],
         "manifest lacks verified partitions or GPT geometry")
    proofs, seen, table_files = [], set(), {}
    for item in manifest["partitions"] + manifest["tables"]:
        filename = item["file"]
        need(filename not in seen, "duplicate backup filename")
        seen.add(filename)
        path = backup_path(directory, filename)
        expected_size = integer(item["bytes"], "backup size", 1)
        expected_hash = digest(item["sha256"], filename)
        need(path.stat().st_size == expected_size, "backup size mismatch: " + filename)
        size, got, _ = hash_file(path)
        need(size == expected_size and got == expected_hash, "backup SHA-256 mismatch: " + filename)
        proofs.append({"file": filename, "bytes": size, "sha256": got})
        if "disk" in item:
            need(item.get("expected_sha256") == expected_hash,
                 "before/after backup hash disagrees: " + filename)
            key = (item["disk"], item["part"])
            need(key not in table_files, "duplicate backup GPT range")
            table_files[key] = item
    disks, consumed = {}, set()
    for geometry in manifest["gpt_geometry"]:
        disk = geometry["disk"]
        need(re.fullmatch(r"/dev/block/sd[a-z]", disk) and disk not in disks,
             "invalid or duplicate manifest disk")
        pieces = []
        for spec in geometry["ranges"]:
            key = (disk, spec["part"])
            need(key in table_files and key not in consumed, "missing or duplicated GPT range file")
            item = table_files[key]
            need(all(item.get(field) == spec.get(field) for field in
                     ("skip_blocks", "blocks", "bytes", "expected_sha256")) and
                 item.get("block_size") == geometry["block_size"], "GPT range differs from manifest")
            consumed.add(key)
            content = backup_path(directory, item["file"]).read_bytes()
            need(hashlib.sha256(content).hexdigest() == item["sha256"], "GPT input changed after hashing")
            pieces.append({**item, "content": content})
        disks[disk] = verify_gpt(geometry, pieces)
    need(consumed == set(table_files), "unmatched GPT backup ranges")
    return manifest, actual_manifest_hash, disks, proofs


def decode_entry(raw):
    return {"type_guid": str(uuid.UUID(bytes_le=raw[:16])),
            "unique_guid": str(uuid.UUID(bytes_le=raw[16:32])),
            "first_lba": struct.unpack_from("<Q", raw, 32)[0],
            "blocks": struct.unpack_from("<Q", raw, 40)[0] - struct.unpack_from("<Q", raw, 32)[0] + 1,
            "name": raw[56:128].decode("utf-16le").split("\0", 1)[0]}


def allocate(disk, root_size=ROOT_BYTES, data_size=DATA_BYTES):
    geometry, h, entries = disk["geometry"], disk["primary"], disk["entries"]
    block = geometry["block_size"]
    need(h["entry_count"] >= DATA_ENTRY, "GPT has too few entries")
    for number in (ROOT_ENTRY, DATA_ENTRY):
        need(entries[(number - 1) * 128:number * 128] == bytes(128),
             "new GPT entry is not entirely empty: " + str(number))
    used, guids, last_used = [], {uuid.UUID(h["disk_guid"])}, h["first_usable"] - 1
    for number in range(h["entry_count"]):
        raw = entries[number * 128:(number + 1) * 128]
        entry = decode_entry(raw)
        need(entry["name"] not in ("hakios_system", "hakios_data"), "install partition name already exists")
        unique = uuid.UUID(entry["unique_guid"])
        if unique.int:
            need(unique not in guids, "duplicate existing GPT unique GUID")
            guids.add(unique)
        if raw[:16] == bytes(16):
            continue   # including the byte-preserved zero-type last_parti placeholder
        start, count = entry["first_lba"], entry["blocks"]
        need(unique.int and h["first_usable"] <= start <= h["last_usable"] and
             count > 0 and count <= h["last_usable"] - start + 1,
             "existing GPT partition exceeds usable bounds")
        used.append((start, start + count - 1))
        last_used = max(last_used, start + count - 1)
    used.sort()
    need(all(left[1] < right[0] for left, right in zip(used, used[1:])), "existing GPT partitions overlap")
    placeholder = decode_entry(entries[67 * 128:68 * 128])
    need(entries[67 * 128:67 * 128 + 16] == bytes(16) and
         placeholder["name"] == "last_parti" and placeholder["first_lba"] == last_used + 1 and
         placeholder["blocks"] == h["last_usable"] - last_used,
         "entry 68 is not the expected unused tail placeholder")
    align = MIB // block
    start = (last_used + 1 + align - 1) // align * align
    extents = []
    for number, name, size, typ in [(ROOT_ENTRY, "hakios_system", root_size, APFS_TYPE),
                                    (DATA_ENTRY, "hakios_data", data_size, HFS_TYPE)]:
        integer(size, name + " size", MIB)
        need(size % MIB == 0 and size % block == 0, "partition size is not aligned")
        count = size // block
        need(start <= h["last_usable"] and count <= h["last_usable"] - start + 1,
             "insufficient unused tail space")
        unique = uuid.uuid4()
        while unique in guids:
            unique = uuid.uuid4()
        guids.add(unique)
        extents.append({"entry": number, "name": name, "type_guid": str(typ),
                        "unique_guid": str(unique), "first_lba": start, "blocks": count,
                        "last_lba": start + count - 1, "bytes": size})
        start += count
    return extents


def encode_extent(extent):
    return struct.pack("<16s16sQQ", uuid.UUID(extent["type_guid"]).bytes_le,
                       uuid.UUID(extent["unique_guid"]).bytes_le,
                       extent["first_lba"], extent["blocks"])


def make_map(disk_guid, block, root, data, root_bytes, root_crc32, flags):
    need(type(flags) is int and flags in (ROOT, ROOT | DATA), "invalid install-map flags")
    need(block == 4096, "Phone (1) install maps require 4096-byte logical blocks")
    need(root["type_guid"] == str(APFS_TYPE) and data["type_guid"] == str(HFS_TYPE),
         "incorrect install-map partition types")
    need(root_bytes > 0 and root_bytes <= root["blocks"] * block, "root image exceeds map extent")
    result = (struct.pack("<IIII16sII", MAGIC, VERSION, MAP_SIZE, flags,
                          uuid.UUID(disk_guid).bytes_le, LUN, block)
              + encode_extent(root) + (encode_extent(data) if flags & DATA else bytes(48))
              + struct.pack("<QII", root_bytes, root_crc32, 0))
    need(len(result) == MAP_SIZE, "install-map ABI size mismatch")
    return result


def whitelist_changes(before, after, allowed):
    need(len(before) == len(after), "metadata range length changed")
    changed = 0
    for offset, (old, new) in enumerate(zip(before, after)):
        if old != new:
            need(any(first <= offset < end for first, end in allowed),
                 "unexpected metadata byte change at offset " + str(offset))
            changed += 1
    return changed


def patch_gpt(disk, extents):
    geometry, primary, backup = disk["geometry"], disk["primary"], disk["backup"]
    block = geometry["block_size"]
    entries = bytearray(disk["entries"])
    for extent in extents:
        offset = (extent["entry"] - 1) * 128
        raw = (uuid.UUID(extent["type_guid"]).bytes_le + uuid.UUID(extent["unique_guid"]).bytes_le
               + struct.pack("<QQQ", extent["first_lba"], extent["last_lba"], 0)
               + extent["name"].encode("utf-16le").ljust(72, b"\0"))
        need(len(raw) == 128, "new GPT entry size mismatch")
        entries[offset:offset + 128] = raw
    expected_crc = crc(entries)
    result = []
    for piece in disk["pieces"]:
        content = bytearray(piece["content"])
        base = piece["skip_blocks"] * block
        allowed = []
        for h in (primary, backup):
            for extent in extents:
                offset = h["entries_lba"] * block + (extent["entry"] - 1) * 128 - base
                if 0 <= offset and offset + 128 <= len(content):
                    at = (extent["entry"] - 1) * 128
                    content[offset:offset + 128] = entries[at:at + 128]
                    allowed.append((offset, offset + 128))
            offset = h["header_lba"] * block - base
            if 0 <= offset and offset + block <= len(content):
                length = struct.unpack_from("<I", content, offset + 12)[0]
                struct.pack_into("<I", content, offset + 88, expected_crc)
                struct.pack_into("<I", content, offset + 16, 0)
                struct.pack_into("<I", content, offset + 16, crc(content[offset:offset + length]))
                allowed.extend([(offset + 16, offset + 20), (offset + 88, offset + 92)])
        changes = whitelist_changes(piece["content"], content, allowed)
        result.append({**piece, "content": bytes(content), "allowed_changes": allowed,
                       "changed_bytes": changes})
    updated_geometry = json.loads(json.dumps(geometry))
    for key in ("primary", "backup"):
        h = updated_geometry[key]
        raw = read_range(result, block, h["header_lba"], 1)
        h["header_crc32"] = struct.unpack_from("<I", raw, 16)[0]
        h["entries_crc32"] = expected_crc
    checked = verify_gpt(updated_geometry, result)
    need(checked["entries"] == entries, "patched GPT did not round-trip")
    # Independent whole-array comparison catches missing or excessive whitelist construction.
    for number in range(primary["entry_count"]):
        if number + 1 not in (ROOT_ENTRY, DATA_ENTRY):
            at = number * 128
            need(entries[at:at + 128] == disk["entries"][at:at + 128], "existing GPT entry changed")
    return result


def prepare(backup, system, expected_manifest, expected_guid, expected_bytes, expected_block):
    manifest, manifest_hash, disks, proofs = verify_backup(backup, expected_manifest)
    need(DISK in disks, "target sde/LUN4 is absent from backup")
    disk = disks[DISK]
    geometry = disk["geometry"]
    wanted_guid = str(uuid.UUID(expected_guid))
    need(wanted_guid == disk["primary"]["disk_guid"] and
         geometry["bytes"] == integer(expected_bytes, "expected disk bytes", 1) and
         geometry["block_size"] == integer(expected_block, "expected block size", 1),
         "target disk identity or geometry differs from expected values")
    need(expected_block == 4096, "Phone (1) installed boot requires 4096-byte logical blocks")
    extents = allocate(disk)
    system = regular(system).resolve()
    need(0 < system.stat().st_size <= extents[0]["bytes"], "root image exceeds planned APFS partition")
    with system.open("rb") as source:
        initial = source.read(64)
    need(len(initial) == 64 and initial[32:36] == b"NXSB", "root image has no APFS superblock")
    apfs_block, apfs_blocks = struct.unpack_from("<IQ", initial, 36)
    need(apfs_block == 4096 and apfs_blocks > 0 and
         apfs_blocks <= system.stat().st_size // apfs_block, "invalid APFS image geometry")
    root_bytes, root_sha, root_crc = hash_file(system, with_crc=True)
    maps = {"install.map": make_map(wanted_guid, expected_block, *extents, root_bytes, root_crc, ROOT),
            "install-persist.map": make_map(wanted_guid, expected_block, *extents, root_bytes, root_crc, ROOT | DATA)}
    patched = patch_gpt(disk, extents)
    outputs, ranges = dict(maps), []
    for piece in patched:
        filename = "gpt-sde-" + piece["part"] + ".new.bin"
        outputs[filename] = piece["content"]
        ranges.append({"file": filename, "source_file": piece["file"],
                       "first_lba": piece["skip_blocks"], "blocks": piece["blocks"],
                       "bytes": len(piece["content"]), "before_sha256": piece["sha256"],
                       "sha256": hashlib.sha256(piece["content"]).hexdigest(),
                       "changed_bytes": piece["changed_bytes"],
                       "allowed_changed_byte_ranges": [list(pair) for pair in piece["allowed_changes"]],
                       "copy": "primary" if piece["skip_blocks"] == 0 else "backup"})
    ranges.sort(key=lambda item: (item["copy"] == "primary", item["first_lba"]))
    plan = {"format": "spacewar-install-plan-v1", "status": "HOST FILES ONLY; NOT INSTALLED",
            "backup_manifest_sha256": manifest_hash, "backup_made": manifest.get("made"),
            "verified_backup_files": proofs, "disk": DISK, "lun": LUN,
            "disk_guid": wanted_guid, "block_size": expected_block,
            "disk_bytes": expected_bytes, "disk_blocks": geometry["blocks"],
            "gpt_entry_count": disk["primary"]["entry_count"], "alignment_bytes": MIB,
            "partitions": extents, "system_image": {"path": str(system), "bytes": root_bytes,
                        "sha256": root_sha, "crc32": root_crc},
            "maps": [{"file": name, "flags": ROOT if name == "install.map" else ROOT | DATA,
                      "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                     for name, content in maps.items()],
            "gpt_metadata_ranges_backup_first": ranges,
            "preservation": {"unchanged_existing_entries": "all except empty entries 69 and 70",
                             "entry_68": "byte-identical zero-type last_parti",
                             "unchanged_other_luns": True, "slot_attributes_unchanged": True},
            "unallocated_tail_bytes": (disk["primary"]["last_usable"] - extents[-1]["last_lba"]) * expected_block,
            "notes": ["No ESP; loader/payload belongs in a separately reviewed boot image.",
                      "install.map has ROOT only and an entirely zero DATA extent.",
                      "install-persist.map has ROOT|DATA and the exact planned DATA extent.",
                      "No phone command or partition write is performed by this tool."]}
    outputs["plan.json"] = (json.dumps(plan, indent=2) + "\n").encode()
    return plan, outputs


def save_output(output, backup, system, files):
    output = Path(output).absolute()
    backup = Path(backup).resolve()
    need(output.parent.is_dir() and not output.exists() and not output.is_symlink(),
         "output must be a new directory beneath an existing host directory")
    parent = output.parent.resolve()
    need(parent != backup and backup not in parent.parents and
         not str(parent).startswith(("/dev", "/proc", "/sys")), "unsafe output directory")
    need(output.resolve() != Path(system).resolve(), "output overlaps system image")
    output.mkdir(mode=0o700)
    for name, content in files.items():
        need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name), "invalid output filename")
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backup", type=Path, help="verified backup directory containing manifest.json")
    parser.add_argument("--system", type=Path, required=True, help="working system.apfs; read only")
    parser.add_argument("--output", type=Path, required=True, help="new private host output directory")
    parser.add_argument("--manifest-sha256", required=True, help="reviewed manifest SHA-256")
    parser.add_argument("--expected-disk-guid", required=True)
    parser.add_argument("--expected-disk-bytes", type=int, required=True)
    parser.add_argument("--expected-block-size", type=int, required=True, choices=(512, 4096))
    args = parser.parse_args(argv)
    try:
        plan, files = prepare(args.backup, args.system, args.manifest_sha256,
                              args.expected_disk_guid, args.expected_disk_bytes, args.expected_block_size)
        output = save_output(args.output, args.backup, args.system, files)
        print("Prepared private host files:", output)
        for partition in plan["partitions"]:
            print("  entry %d %s: LBA %d..%d (%d bytes)" %
                  (partition["entry"], partition["name"], partition["first_lba"],
                   partition["last_lba"], partition["bytes"]))
        print("  install.map: ROOT only; install-persist.map: ROOT|DATA; both 152 bytes")
        print("No device was opened or modified.")
        return 0
    except (Failure, OSError, ValueError, KeyError, TypeError, struct.error) as error:
        print("spacewar-install-plan: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
