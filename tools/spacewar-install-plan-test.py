#!/usr/bin/env python3
"""Exercise the host-only planner with independent 512/4096-byte GPT fixtures."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("spacewar_install_plan", HERE / "spacewar-install-plan.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_fixture(root, block=4096, *, conflict=False, name_conflict=False,
                 overlap=False, duplicate_guid=False, tiny=False, placeholder_change=None,
                 hole=None, data1_hole=None, last_used_extra=0):
    directory = root / "backup"
    directory.mkdir()
    disk_bytes = 6 << 30
    blocks = disk_bytes // block
    sectors = (96 * 128 + block - 1) // block
    first, last = 2 + sectors + 1, blocks - sectors - 3
    tail_array = last + 1
    entries = bytearray(96 * 128)
    last_used = ((disk_bytes - (1 << 30)) // block - 1) if tiny else (1796 << 20) // block - 1
    last_used += last_used_extra
    for index in range(67):
        start = first + index * 8 if index < 66 else last_used - 127
        end = start + 7 if index < 66 else last_used
        name = "boot_a" if index == 10 else "boot_b" if index == 34 else "rtice" if index == 66 else "old_%d" % index
        if name_conflict and index == 3:
            name = "hakios_system"
        unique = uuid.UUID(int=100 + (0 if duplicate_guid and index == 1 else index))
        if overlap and index == 1:
            start, end = first, first + 7
        typ = uuid.UUID(fields=(20 + index, 0, 0, 0, 0, 1)).bytes_le
        if data1_hole == index + 1:
            typ = bytes(4) + typ[4:]
            assert typ != bytes(16)
        raw = (typ + unique.bytes_le
               + struct.pack("<QQQ", start, end, index << 48)
               + name.encode("utf-16le").ljust(72, b"\0"))
        entries[index * 128:(index + 1) * 128] = raw
    entries[67 * 128:68 * 128] = (bytes(16) + uuid.UUID(int=1000).bytes_le
                                  + struct.pack("<QQQ", last_used + 1, last, 1 << 60)
                                  + "last_parti".encode("utf-16le").ljust(72, b"\0"))
    if placeholder_change:
        raw = bytearray(entries[67 * 128:68 * 128])
        placeholder_change(raw)
        entries[67 * 128:68 * 128] = raw
    if hole is not None:
        entries[(hole - 1) * 128:hole * 128] = bytes(128)
    if conflict:
        entries[68 * 128 + 112] = 1  # zero type but residual bytes: must not be overwritten
    disk_guid = uuid.UUID("12345678-1234-5678-9012-123456789012")

    def make_header(here, other, array):
        data = bytearray(block)
        struct.pack_into("<8sIIIIQQQQ16sQIII", data, 0, b"EFI PART", 0x10000, 92, 0, 0,
                         here, other, first, last, disk_guid.bytes_le, array, 96, 128,
                         zlib.crc32(entries))
        struct.pack_into("<I", data, 16, zlib.crc32(data[:92]))
        return bytes(data)

    primary = make_header(1, blocks - 1, 2)
    backup = make_header(blocks - 1, 1, tail_array)
    mbr = bytearray(block)
    mbr[:13] = b"preserve MBR!"
    mbr[450], mbr[510:512] = 0xee, b"\x55\xaa"
    ranges = [("head", 0, bytes(mbr) + primary + bytes(entries)),
              ("entries-%d" % tail_array, tail_array, bytes(entries)),
              ("tail", blocks - 1, backup)]
    tables, geometry_ranges = [], []
    for part, start, data in ranges:
        filename = "gpt-sde-" + part + ".bin"
        (directory / filename).write_bytes(data)
        item = {"part": part, "skip_blocks": start, "blocks": len(data) // block,
                "bytes": len(data), "expected_sha256": sha(data)}
        geometry_ranges.append(copy.deepcopy(item))
        tables.append({**item, "disk": planner.DISK, "block_size": block,
                       "sha256": sha(data), "file": filename})
    boot = b"original boot backup"
    (directory / "boot_b.img").write_bytes(boot)
    geometry = {"disk": planner.DISK, "bytes": disk_bytes, "block_size": block,
                "blocks": blocks,
                "primary": planner.header(primary, 1, blocks - 1, blocks, block),
                "backup": planner.header(backup, blocks - 1, 1, blocks, block),
                "ranges": geometry_ranges}
    manifest = {"made": "test-fixture", "partitions": [{"file": "boot_b.img",
                "name": "boot_b", "node": "/dev/block/sde35", "bytes": len(boot), "sha256": sha(boot)}],
                "tables": tables, "gpt_geometry": [geometry]}
    image = bytearray(8192)
    image[32:36] = b"NXSB"
    struct.pack_into("<IQ", image, 36, 4096, 2)
    system = root / "system.apfs"
    system.write_bytes(image)
    result = {"directory": directory, "manifest": manifest, "system": system,
              "guid": str(disk_guid), "bytes": disk_bytes, "block": block,
              "original": {item["file"]: (directory / item["file"]).read_bytes() for item in tables}}
    refresh(result)
    return result


def refresh(fixture, rehash_files=False):
    if rehash_files:
        manifest = fixture["manifest"]
        by_part = {entry["part"]: entry for entry in manifest["gpt_geometry"][0]["ranges"]}
        for item in manifest["tables"]:
            data = (fixture["directory"] / item["file"]).read_bytes()
            item["sha256"] = item["expected_sha256"] = sha(data)
            by_part[item["part"]]["expected_sha256"] = sha(data)
    raw = (json.dumps(fixture["manifest"]) + "\n").encode()
    (fixture["directory"] / "manifest.json").write_bytes(raw)
    fixture["manifest_sha"] = sha(raw)


def prepare(fixture, **overrides):
    args = {"backup": fixture["directory"], "system": fixture["system"],
            "expected_manifest": fixture["manifest_sha"], "expected_guid": fixture["guid"],
            "expected_bytes": fixture["bytes"], "expected_block": fixture["block"]}
    args.update(overrides)
    return planner.prepare(**args)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="spacewar-install-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, block=4096, **options):
        return make_fixture(self.root, block, **options)

    def test_both_sector_sizes_and_byte_preservation(self):
        for block in (512, 4096):
            with self.subTest(block=block), tempfile.TemporaryDirectory() as tmp:
                fixture = make_fixture(Path(tmp), block)
                if block == 512:
                    # The GPT engine handles long and short logical sectors;
                    # Phone (1)'s actual loader contract intentionally requires 4 KiB.
                    with self.assertRaises(planner.Failure):
                        prepare(fixture)
                    _, _, disks, _ = planner.verify_backup(fixture["directory"], fixture["manifest_sha"])
                    disk = disks[planner.DISK]
                    extents = planner.allocate(disk)
                    patched = planner.patch_gpt(disk, extents)
                    ranges = []
                    files = {}
                    for piece in patched:
                        filename = "new-" + piece["file"]
                        files[filename] = piece["content"]
                        ranges.append({"source_file": piece["file"], "file": filename,
                            "changed_bytes": piece["changed_bytes"],
                            "allowed_changed_byte_ranges": piece["allowed_changes"],
                            "sha256": sha(piece["content"]),
                            "copy": "primary" if piece["skip_blocks"] == 0 else "backup"})
                    ranges.sort(key=lambda item: item["copy"] == "primary")
                    plan = {"partitions": extents, "gpt_metadata_ranges_backup_first": ranges}
                else:
                    plan, files = prepare(fixture)
                self.assertEqual([p["entry"] for p in plan["partitions"]], [68, 69])
                self.assertEqual([p["bytes"] for p in plan["partitions"]], [2560 << 20, 1536 << 20])
                for partition in plan["partitions"]:
                    self.assertEqual(partition["first_lba"] * block % (1 << 20), 0)
                for item in plan["gpt_metadata_ranges_backup_first"]:
                    before, after = fixture["original"][item["source_file"]], files[item["file"]]
                    self.assertEqual(len(before), len(after))
                    self.assertGreater(item["changed_bytes"], 0)
                    for offset in range(len(before)):
                        if not any(a <= offset < b for a, b in item["allowed_changed_byte_ranges"]):
                            self.assertEqual(before[offset], after[offset])
                    self.assertEqual(sha(after), item["sha256"])
                    if item["copy"] == "primary":
                        self.assertEqual(before[:block], after[:block])
                        at = 2 * block
                        for number in range(96):
                            if number not in (67, 68):
                                self.assertEqual(before[at + number * 128:at + (number + 1) * 128],
                                                 after[at + number * 128:at + (number + 1) * 128])
                self.assertEqual(plan["gpt_metadata_ranges_backup_first"][-1]["copy"], "primary")
                self.assertEqual(fixture["original"], {n: (fixture["directory"] / n).read_bytes() for n in fixture["original"]})

    def test_contiguous_entries_survive_slot_switch_compaction_without_changing_extents(self):
        # Match the surveyed phone's unaligned last-used LBA; no data relocation
        # is needed when the two GPT records move from 69/70 to 68/69.
        fixture = self.fixture(last_used_extra=16)
        plan, files = prepare(fixture)
        root, data = plan["partitions"]
        self.assertEqual([(p["entry"], p["first_lba"], p["blocks"]) for p in (root, data)],
                         [(68, 460032, 655360), (69, 1115392, 393216)])
        before = fixture["original"]["gpt-sde-head.bin"][8192:8192 + 96 * 128]
        after = files["gpt-sde-head.new.bin"][8192:8192 + 96 * 128]
        self.assertEqual(before[:67 * 128], after[:67 * 128])
        self.assertEqual(after[69 * 128:], bytes(27 * 128))
        records = [after[at:at + 128] for at in range(0, len(after), 128)]
        used = [record for record in records if record[:16] != bytes(16)]
        self.assertEqual(len(used), 69)
        self.assertEqual(records[:69], used)
        self.assertEqual(after, b"".join(used).ljust(len(after), b"\0"))
        # Install-map identity is independent of GPT entry numbers: the previous
        # map's GUIDs, physical extents and image identity need no regeneration.
        for flags, name in ((1, "install.map"), (3, "install-persist.map")):
            previous = planner.make_map(fixture["guid"], 4096,
                {**root, "entry": 69}, {**data, "entry": 70}, 8192,
                zlib.crc32(fixture["system"].read_bytes()), flags)
            self.assertEqual(previous, files[name])

    def test_unexpected_placeholder_or_used_entry_hole_is_rejected(self):
        mutations = {
            "nonzero type": lambda raw: raw.__setitem__(0, 1),
            "zero unique GUID": lambda raw: raw.__setitem__(slice(16, 32), bytes(16)),
            "duplicate unique GUID": lambda raw: raw.__setitem__(slice(16, 32), uuid.UUID(int=100).bytes_le),
            "wrong attributes": lambda raw: struct.pack_into("<Q", raw, 48, 0),
            "extra attributes": lambda raw: struct.pack_into("<Q", raw, 48, (1 << 60) | 1),
            "wrong name": lambda raw: raw.__setitem__(56, ord("x")),
            "name padding": lambda raw: raw.__setitem__(127, 1),
            "tail start": lambda raw: struct.pack_into("<Q", raw, 32, 1),
            "tail end": lambda raw: struct.pack_into("<Q", raw, 40, (6 << 30) // 4096 - 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                fixture = make_fixture(Path(tmp), placeholder_change=mutate)
                with self.assertRaises(planner.Failure):
                    prepare(fixture)
        for hole in (1, 20, 67):
            with self.subTest(hole=hole), tempfile.TemporaryDirectory() as tmp:
                fixture = make_fixture(Path(tmp), hole=hole)
                with self.assertRaisesRegex(planner.Failure, "zero-type hole"):
                    prepare(fixture)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = make_fixture(Path(tmp), data1_hole=1)
            with self.assertRaisesRegex(planner.Failure, "zero-type hole"):
                prepare(fixture)

    def test_map_schema_flags_and_crc(self):
        fixture = self.fixture()
        plan, files = prepare(fixture)
        maps = [files["install.map"], files["install-persist.map"]]
        self.assertTrue(all(len(m) == 152 for m in maps))
        self.assertEqual(struct.unpack_from("<4I", maps[0]), (planner.MAGIC, 1, 152, 1))
        self.assertEqual(struct.unpack_from("<4I", maps[1]), (planner.MAGIC, 1, 152, 3))
        self.assertEqual(maps[0][88:136], bytes(48))
        self.assertEqual(maps[0][16:88], maps[1][16:88])
        self.assertEqual(maps[0][136:], maps[1][136:])
        self.assertEqual(struct.unpack_from("<QII", maps[0], 136),
                         (8192, zlib.crc32(fixture["system"].read_bytes()), 0))
        self.assertEqual(maps[0][16:32], uuid.UUID(fixture["guid"]).bytes_le)
        self.assertEqual(struct.unpack_from("<II", maps[0], 32), (4, 4096))
        self.assertEqual(maps[0][40:56], planner.APFS_TYPE.bytes_le)
        self.assertEqual(maps[1][88:104], planner.HFS_TYPE.bytes_le)
        for flags in (0, 2, 4, 0xffffffff):
            with self.assertRaises(planner.Failure):
                planner.make_map(fixture["guid"], 4096, *plan["partitions"], 8192, 0, flags)

    def test_shared_c_abi(self):
        fixture = self.fixture()
        _, files = prepare(fixture)
        (self.root / "map.bin").write_bytes(files["install-persist.map"])
        source = self.root / "abi.c"
        source.write_text('''#include <stddef.h>
#include <assert.h>
#include <stdio.h>
#include "hakios.spacewar.install.h"
int main(int argc,char **argv) {
 struct sw_install_map m; FILE *f=fopen(argv[1],"rb"); assert(argc==2&&f);
 assert(fread(&m,1,sizeof(m),f)==sizeof(m)); fclose(f);
 assert(sizeof(m)==152&&offsetof(struct sw_install_map,data)==88);
 assert(offsetof(struct sw_install_map,root_bytes)==136);
 assert(m.magic==SW_INSTALL_MAGIC&&m.version==SW_INSTALL_VERSION&&m.size==152);
 assert(m.flags==(SW_INSTALL_ROOT|SW_INSTALL_DATA)&&m.lun==4&&m.block_size==4096);
 assert(m.root_bytes==8192&&m.root.blocks==655360&&m.data.blocks==393216);
 return 0;
}
''')
        compiler = os.environ.get("HOSTCC", "cc")
        import shlex
        subprocess.run(shlex.split(compiler) + ["-std=c11", "-I", str(HERE.parent / "drivers/storage"),
                       str(source), "-o", str(self.root / "abi")], check=True)
        subprocess.run([str(self.root / "abi"), str(self.root / "map.bin")], check=True)

    def test_wrong_identity_manifest_geometry_and_overflow(self):
        fixture = self.fixture()
        for overrides in ({"expected_guid": str(uuid.uuid4())}, {"expected_bytes": fixture["bytes"] + 4096},
                          {"expected_block": 512}, {"expected_manifest": "0" * 64},
                          {"expected_bytes": 1 << 64}, {"expected_bytes": -1}):
            with self.subTest(overrides=overrides), self.assertRaises(planner.Failure):
                prepare(fixture, **overrides)
        fixture["manifest"]["gpt_geometry"][0]["blocks"] = 1 << 64
        refresh(fixture)
        with self.assertRaises(planner.Failure):
            prepare(fixture)

    def test_backup_hash_and_truncation(self):
        fixture = self.fixture()
        (fixture["directory"] / "boot_b.img").write_bytes(b"wrong")
        with self.assertRaises(planner.Failure):
            prepare(fixture)
        (fixture["directory"] / "boot_b.img").write_bytes(b"x" * len(b"original boot backup"))
        with self.assertRaises(planner.Failure):
            prepare(fixture)

    def test_gpt_header_and_array_corruption_after_rehashed_manifest(self):
        fixture = self.fixture()
        filename = fixture["directory"] / "gpt-sde-head.bin"
        original = filename.read_bytes()
        for offset in (4096 + 24, 2 * 4096 + 128):
            data = bytearray(original)
            data[offset] ^= 1
            filename.write_bytes(data)
            refresh(fixture, rehash_files=True)
            with self.subTest(offset=offset), self.assertRaises(planner.Failure):
                prepare(fixture)

    def test_conflicting_entries_names_overlap_guid_and_capacity(self):
        for options in ({"conflict": True}, {"name_conflict": True}, {"overlap": True},
                        {"duplicate_guid": True}, {"tiny": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                fixture = make_fixture(Path(tmp), **options)
                with self.assertRaises(planner.Failure):
                    prepare(fixture)

    def test_backup_ranges_must_match_and_cannot_escape(self):
        fixture = self.fixture()
        fixture["manifest"]["tables"][0]["skip_blocks"] = 1
        refresh(fixture)
        with self.assertRaises(planner.Failure):
            prepare(fixture)
        fixture["manifest"]["tables"][0]["file"] = "../system.apfs"
        refresh(fixture)
        with self.assertRaises(planner.Failure):
            prepare(fixture)

    def test_no_device_calls_private_outputs_and_no_overwrite(self):
        fixture = self.fixture()
        source_hashes = {n: sha((fixture["directory"] / n).read_bytes()) for n in fixture["original"]}
        with mock.patch("subprocess.run", side_effect=AssertionError("phone/process call")), \
             mock.patch("os.system", side_effect=AssertionError("shell call")):
            plan, files = prepare(fixture)
            output = planner.save_output(self.root / "private-plan", fixture["directory"], fixture["system"], files)
            with self.assertRaises(planner.Failure):
                planner.save_output(output, fixture["directory"], fixture["system"], files)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for name, data in files.items():
            self.assertEqual((output / name).read_bytes(), data)
            self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(source_hashes, {n: sha((fixture["directory"] / n).read_bytes()) for n in fixture["original"]})
        self.assertEqual(json.loads((output / "plan.json").read_text()), plan)

    def test_invalid_root_device_symlink_and_oversize(self):
        fixture = self.fixture()
        with self.assertRaises(planner.Failure):
            prepare(fixture, system=Path("/dev/null"))
        link = self.root / "alias.apfs"
        link.symlink_to(fixture["system"])
        with self.assertRaises(planner.Failure):
            prepare(fixture, system=link)
        with fixture["system"].open("r+b") as output:
            output.truncate(planner.ROOT_BYTES + 1)
        with self.assertRaises(planner.Failure):
            prepare(fixture)

    def test_changed_bytes_outside_whitelist_are_rejected(self):
        with self.assertRaises(planner.Failure):
            planner.whitelist_changes(b"old data", b"new data", [(4, 8)])
        with self.assertRaises(planner.Failure):
            planner.whitelist_changes(b"old", b"longer", [(0, 6)])

    def test_cli_failure_leaves_no_output(self):
        fixture = self.fixture()
        output = self.root / "should-not-exist"
        result = planner.main([str(fixture["directory"]), "--system", str(fixture["system"]),
                               "--output", str(output), "--manifest-sha256", "0" * 64,
                               "--expected-disk-guid", fixture["guid"],
                               "--expected-disk-bytes", str(fixture["bytes"]),
                               "--expected-block-size", "4096"])
        self.assertEqual(result, 1)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
