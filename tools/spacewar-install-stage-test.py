#!/usr/bin/env python3
"""Host-only fake-device checks for the staged Phone (1) installation writer."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import re
import shlex
import struct
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

stage = module("stage", "spacewar-install-stage.py")
fixtures = module("fixtures", "spacewar-install-plan-test.py")
planner = stage.planner


class MemoryLog:
    def __init__(self):
        self.events = []
    def event(self, kind, **fields):
        self.events.append((kind, fields))


class FakeDevice:
    def __init__(self, context, *, mode=""):
        self.context, self.mode = context, mode
        self.commands, self.pushes, self.writes, self.removals = [], [], [], []
        self.operations, self.owners, self.modes = [], {}, {}
        self.staging_dir, self.directory_owner, self.frozen = None, None, False
        self.locked_hashes = set()
        self.staged, self.changed_ranges, self.physical_hashes = {}, {}, {}
        self.range_contents = {(item["skip_blocks"], item["blocks"]): item["content"]
                               for item in context["disk"]["pieces"]}
        self.files = {Path(item["path"]).name: item for item in context["images"]}
        for item in context["ranges"]:
            self.files[item["file"]] = {**item, "path": context["plan_file"].parent / item["file"]}
        self.preflights = 0

    def root(self, command, binary=False):
        self.commands.append(command)
        self.operations.append(("root", command))
        values = {"id -u": "0", "getprop ro.product.device": "Spacewar", "getprop sys.boot_completed": "1",
                  "blockdev --getss /dev/block/sde": "4096", "blockdev --getsize64 /dev/block/sde": "6442450944",
                  "readlink -f /dev/block/by-name/boot_a": "/dev/block/sde11",
                  "readlink -f /dev/block/by-name/boot_b": "/dev/block/sde35"}
        checks = {"get-number-slots": "2", "get-current-slot": "0", "get-active-boot-slot": "0",
                  "is-slot-bootable 0": "1", "is-slot-marked-successful 0": "1",
                  "is-slot-bootable 1": "1", "is-slot-marked-successful 1": "1",
                  "get-snapshot-merge-status": "MergeStatus::NONE"}
        values.update({stage.BOOTCTL + " " + key: value for key, value in checks.items()})
        bad = {"notroot": ("id -u", "2000"), "wrongdevice": ("getprop ro.product.device", "redwood"),
               "slot": (stage.BOOTCTL + " get-current-slot", "1"),
               "active": (stage.BOOTCTL + " get-active-boot-slot", "1"),
               "unsuccessful": (stage.BOOTCTL + " is-slot-marked-successful 0", "0"),
               "unbootable": (stage.BOOTCTL + " is-slot-bootable 1", "0"),
               "snapshot": (stage.BOOTCTL + " get-snapshot-merge-status", "MergeStatus::MERGING"),
               "geometry": ("blockdev --getsize64 /dev/block/sde", "6442455040"),
               "sector": ("blockdev --getss /dev/block/sde", "512"),
               "byname": ("readlink -f /dev/block/by-name/boot_b", "/dev/block/sda35")}
        if command == "id -u":
            self.preflights += 1
        if self.mode in bad and command == bad[self.mode][0]:
            return bad[self.mode][1]
        if command in values:
            return values[command]
        if command == "df -k /data/local/tmp":
            available = 1 if self.mode == "space" else 80000000
            return "Filesystem 1K-blocks Used Available Use%% Mounted on\n/dev/sda 100000000 20000000 %d 20%% /data" % available
        words = shlex.split(command)
        if words[:2] == [stage.BUSYBOX, "dd"]:
            fields = dict(word.split("=", 1) for word in words[2:] if "=" in word and word != "2>/dev/null")
            assert fields["bs"] == "4096"
            count = int(fields["count"])
            if "of" in fields:
                assert self.frozen and len(self.locked_hashes) == len(self.staged)
                assert all(self.owners.get(path) == "0:0" and self.modes.get(path) == "600"
                           for path in self.staged)
                assert fields["of"] == stage.DISK and fields["conv"] == "notrunc,fsync"
                first = int(fields["seek"])
                remote = fields["if"]
                assert remote in self.staged
                item = self.staged[remote]
                assert item["bytes"] == count * 4096
                self.writes.append((Path(remote).name, first, count))
                self.physical_hashes[(first, count)] = item["sha256"]
                if "source_file" in item:
                    content = Path(item["path"]).read_bytes()
                    self.range_contents[(first, count)] = content
                return ""
            assert fields["if"] == stage.DISK
            first = int(fields["skip"])
            if "| sha256sum" in command:
                value = self.physical_hashes.get((first, count))
                if value is None:
                    value = hashlib.sha256(self.range_contents[(first, count)]).hexdigest()
                if self.mode == "readback" and self.writes:
                    value = "0" * 64
                if self.mode == "gpt-readback" and self.writes and self.writes[-1][0].startswith("gpt-"):
                    value = "0" * 64
                return value + "  -"
            content = self.range_contents[(first, count)]
            if self.mode == "gpt" or (self.mode == "gpt-later" and self.preflights > 1):
                content = bytes([content[0] ^ 1]) + content[1:]
            assert binary
            return content
        if words[0] == "mkdir":
            assert words[:3] == ["mkdir", "-m", "700"]
            self.staging_dir, self.directory_owner = words[3], "0:0"
            return ""
        if words[0] == "chown":
            assert words[1] in ("2000:2000", "0:0")
            if words[2] == self.staging_dir:
                self.directory_owner = words[1]
            else:
                assert words[2] in self.staged and words[1] == "0:0"
                self.owners[words[2]] = words[1]
            return ""
        if words[0] == "chmod":
            if words[2] == self.staging_dir:
                assert words[1] == "700" and self.directory_owner == "0:0"
                self.frozen = True
            else:
                assert words[1] == "600" and self.owners[words[2]] == "0:0"
                self.modes[words[2]] = words[1]
            return ""
        if words[0] == "sha256sum":
            value = self.staged[words[1]]["sha256"]
            if self.frozen:
                self.locked_hashes.add(words[1])
            if self.mode == "staged-hash" or (self.mode == "postlock-hash" and self.frozen):
                value = "0" * 64
            return value + "  " + words[1]
        if words[0] == "test":
            assert command == "test -d " + self.staging_dir + " && test ! -L " + self.staging_dir
            assert self.frozen and self.directory_owner == "0:0"
            return ""
        if words[0] == "stat":
            assert words[1:3] == ["-c", "%u:%g:%a"] and words[3] == self.staging_dir
            return "0:0:700" if self.frozen and self.directory_owner == "0:0" else "2000:2000:700"
        if words[0] in ("rm", "rmdir"):
            self.removals.append(words[1])
            return ""
        if words == ["sgdisk", "--verify", stage.DISK, "2>&1"]:
            return "No problems found."
        raise AssertionError("unexpected fake-device command: " + command)

    def push(self, source, remote):
        assert self.directory_owner == "2000:2000" and not self.frozen
        self.operations.append(("push", remote))
        self.pushes.append((str(source), remote))
        name = Path(source).name
        assert name in self.files
        self.staged[remote] = self.files[name]


class StageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="spacewar-stage-test-")
        cls.root = Path(cls.temporary.name)
        cls.fixture = fixtures.make_fixture(cls.root)
        # Match the actual phone's fixed 96 MiB boot extents, preserving their entry numbers.
        manifest = cls.fixture["manifest"]
        disk = planner.verify_backup(cls.fixture["directory"], cls.fixture["manifest_sha"])[2][stage.DISK]
        entries = bytearray(disk["entries"])
        # Keep the small entries non-overlapping and move the two boot ranges into an unused gap.
        for number, first in ((11, 4096), (35, 32768)):
            struct.pack_into("<QQ", entries, (number - 1) * 128 + 32, first, first + 24576 - 1)
        for piece in disk["pieces"]:
            content = bytearray(piece["content"])
            start = piece["skip_blocks"] * 4096
            for h in (disk["primary"], disk["backup"]):
                at = h["entries_lba"] * 4096 - start
                if 0 <= at and at + len(entries) <= len(content):
                    content[at:at + len(entries)] = entries
                at = h["header_lba"] * 4096 - start
                if 0 <= at and at + 4096 <= len(content):
                    struct.pack_into("<I", content, at + 88, planner.crc(entries))
                    struct.pack_into("<I", content, at + 16, 0)
                    struct.pack_into("<I", content, at + 16, planner.crc(content[at:at + 92]))
                    key = "primary" if h["header_lba"] == 1 else "backup"
                    manifest["gpt_geometry"][0][key]["header_crc32"] = struct.unpack_from("<I", content, at + 16)[0]
                    manifest["gpt_geometry"][0][key]["entries_crc32"] = planner.crc(entries)
            (cls.fixture["directory"] / piece["file"]).write_bytes(content)
        boot_parts = []
        for name, number in (("boot_a", 11), ("boot_b", 35)):
            path = cls.fixture["directory"] / (name + ".img")
            with path.open("wb") as target:
                target.truncate(96 << 20)
            boot_parts.append({"file": path.name, "name": name, "node": stage.DISK + str(number),
                               "bytes": 96 << 20, "sha256": planner.hash_file(path)[1]})
        manifest["partitions"] = boot_parts
        fixtures.refresh(cls.fixture, rehash_files=True)
        plan, files = fixtures.prepare(cls.fixture)
        cls.output = cls.root / "plan"
        planner.save_output(cls.output, cls.fixture["directory"], cls.fixture["system"], files)
        cls.plan_file = cls.output / "plan.json"
        cls.plan_sha = hashlib.sha256(cls.plan_file.read_bytes()).hexdigest()
        cls.data = cls.root / "data.hfs"
        size = plan["partitions"][1]["bytes"]
        h = bytearray(512)
        struct.pack_into(">HHIII", h, 0, 0x4858, 5, 0x2000, 0, 16)
        struct.pack_into(">III", h, 40, 4096, size // 4096, size // 4096 - 1024)
        with cls.data.open("wb") as data:
            data.truncate(size)
            data.seek(1024)
            data.write(h)
            data.seek(size - 1024)
            data.write(h)
        cls.context = stage.validate_host(cls.plan_file, cls.plan_sha, cls.fixture["directory"],
                                          cls.fixture["manifest_sha"], cls.fixture["system"], cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_default_dry_run_has_no_phone_mutations(self):
        fake, log = FakeDevice(self.context), MemoryLog()
        stage.stage(self.context, fake, log)
        self.assertFalse(fake.pushes or fake.writes or fake.removals)
        self.assertFalse(any(command.startswith(("mkdir", "chmod", "rm ", "rmdir")) for command in fake.commands))
        self.assertEqual(log.events[-1][0], "dry-run-complete")

    def test_all_fresh_identity_guards_stop_before_mutation(self):
        for mode in ("notroot", "wrongdevice", "slot", "active", "unsuccessful", "unbootable", "snapshot",
                     "geometry", "sector", "byname", "gpt", "space"):
            with self.subTest(mode=mode):
                fake = FakeDevice(self.context, mode=mode)
                with self.assertRaises(stage.Failure):
                    stage.stage(self.context, fake, MemoryLog(), apply=True)
                self.assertFalse(fake.pushes or fake.writes or fake.removals)

    def test_success_images_first_then_backup_gpt_and_primary_last(self):
        fake, log = FakeDevice(self.context), MemoryLog()
        stage.stage(self.context, fake, log, apply=True)
        self.assertEqual([item[0] for item in fake.writes],
                         ["system.apfs", "data.hfs", "gpt-sde-entries-1572859.new.bin", "gpt-sde-tail.new.bin", "gpt-sde-head.new.bin"])
        self.assertEqual(fake.preflights, 3)
        self.assertEqual(len(fake.removals), len(fake.pushes) + 1)
        self.assertEqual(log.events[-1][0], "staging-cleaned")
        forbidden = ("set-active", "mark-boot", "reboot", "of=/dev/block/sde11", "of=/dev/block/sde35")
        self.assertFalse(any(word in command for word in forbidden for command in fake.commands))

    def test_staged_hash_mismatch_never_writes_raw_disk(self):
        fake = FakeDevice(self.context, mode="staged-hash")
        with self.assertRaises(stage.Failure):
            stage.stage(self.context, fake, MemoryLog(), apply=True)
        self.assertFalse(fake.writes or fake.removals)
        self.assertTrue(fake.pushes)

    def test_staging_freezes_after_upload_then_rehashes_before_block_writes(self):
        fake = FakeDevice(self.context)
        stage.stage(self.context, fake, MemoryLog(), apply=True)
        freeze = fake.operations.index(("root", "chmod 700 " + fake.staging_dir))
        last_push = max(index for index, operation in enumerate(fake.operations) if operation[0] == "push")
        first_write = next(index for index, operation in enumerate(fake.operations)
                           if operation[0] == "root" and " of=/dev/block/sde " in operation[1])
        self.assertLess(last_push, freeze)
        self.assertLess(freeze, first_write)
        hashes = [operation[1] for operation in fake.operations[freeze + 1:first_write]
                  if operation[0] == "root" and operation[1].startswith("sha256sum ")]
        self.assertEqual(set(hashes), {"sha256sum " + path for path in fake.staged})

    def test_postlock_hash_mismatch_never_writes_blocks_or_cleans_artifacts(self):
        fake = FakeDevice(self.context, mode="postlock-hash")
        with self.assertRaises(stage.Failure):
            stage.stage(self.context, fake, MemoryLog(), apply=True)
        self.assertTrue(fake.frozen)
        self.assertEqual(len(fake.pushes), len(fake.files))
        self.assertFalse(fake.writes or fake.removals)

    def test_changed_gpt_after_push_never_writes_raw_disk(self):
        fake = FakeDevice(self.context, mode="gpt-later")
        with self.assertRaises(stage.Failure):
            stage.stage(self.context, fake, MemoryLog(), apply=True)
        self.assertFalse(fake.writes or fake.removals)
        self.assertTrue(fake.pushes)

    def test_image_readback_mismatch_stops_before_data_or_gpt(self):
        fake = FakeDevice(self.context, mode="readback")
        with self.assertRaises(stage.Failure):
            stage.stage(self.context, fake, MemoryLog(), apply=True)
        self.assertEqual([item[0] for item in fake.writes], ["system.apfs"])
        self.assertFalse(fake.removals)

    def test_gpt_readback_mismatch_stops_before_primary(self):
        fake = FakeDevice(self.context, mode="gpt-readback")
        with self.assertRaises(stage.Failure):
            stage.stage(self.context, fake, MemoryLog(), apply=True)
        self.assertEqual([item[0] for item in fake.writes],
                         ["system.apfs", "data.hfs", "gpt-sde-entries-1572859.new.bin"])
        self.assertFalse(fake.removals)

    def completed_device(self):
        fake = FakeDevice(self.context)
        for item in self.context["ranges"]:
            fake.range_contents[(item["first_lba"], item["blocks"])] = (self.context["plan_file"].parent / item["file"]).read_bytes()
        for image in self.context["images"]:
            fake.physical_hashes[(image["first_lba"], image["bytes"] // 4096)] = image["sha256"]
        fake.staging_dir = stage.staging_directory(self.context)
        fake.directory_owner, fake.frozen = "0:0", True
        return fake

    def test_verify_completed_reads_new_gpt_and_images_without_mutation(self):
        fake, log = self.completed_device(), MemoryLog()
        stage.stage(self.context, fake, log, verify_staged=True)
        self.assertFalse(fake.writes or fake.pushes or fake.removals)
        self.assertEqual(log.events[-1][0], "completed-install-verified")
        self.assertIn("sgdisk --verify /dev/block/sde 2>&1", fake.commands)
        self.assertFalse(any(" of=" in command for command in fake.commands))

    def test_verify_completed_rejects_bad_new_gpt_or_data_without_cleanup(self):
        for bad in ("gpt", "data"):
            fake = self.completed_device()
            if bad == "gpt":
                key = next(iter(fake.range_contents))
                raw = fake.range_contents[key]
                fake.range_contents[key] = bytes([raw[0] ^ 1]) + raw[1:]
            else:
                image = self.context["images"][1]
                fake.physical_hashes[(image["first_lba"], image["bytes"] // 4096)] = "0" * 64
            with self.subTest(bad=bad), self.assertRaises(stage.Failure):
                stage.stage(self.context, fake, MemoryLog(), verify_staged=True, cleanup=True)
            self.assertFalse(fake.writes or fake.pushes or fake.removals)

    def test_verify_completed_cleanup_only_after_all_checks(self):
        fake, log = self.completed_device(), MemoryLog()
        stage.stage(self.context, fake, log, verify_staged=True, cleanup=True)
        self.assertFalse(fake.writes or fake.pushes)
        self.assertEqual(len(fake.removals), len(self.context["images"]) + len(self.context["ranges"]) + 1)
        verified = next(i for i, event in enumerate(log.events) if event[0] == "completed-install-verified")
        cleaned = next(i for i, event in enumerate(log.events) if event[0] == "staging-cleaned")
        self.assertLess(verified, cleaned)
        self.assertEqual(fake.commands[-1], "rmdir " + fake.staging_dir)

    def test_raw_write_and_read_bounds_and_paths(self):
        fake = FakeDevice(self.context)
        for first, count in ((-1, 4096), (stage.DISK_BYTES // 4096, 4096),
                             (stage.DISK_BYTES // 4096 - 1, 8192), (1 << 64, 4096), (0, 1), (0, 0)):
            with self.subTest(first=first, count=count), self.assertRaises(stage.Failure):
                stage.bounded_write(fake, "/data/local/tmp/spacewar-install-" + "0" * 64 + "/source", first, count)
        with self.assertRaises(stage.Failure):
            stage.bounded_write(fake, "/tmp/arbitrary", 1, 4096)
        with self.assertRaises(stage.Failure):
            stage.read_command(stage.DISK_BYTES // 4096 - 1, 2)
        self.assertFalse(fake.commands)

    def test_host_plan_pin_rejected_before_phone(self):
        with self.assertRaises(stage.Failure):
            stage.validate_host(self.plan_file, "0" * 64, self.fixture["directory"],
                                self.fixture["manifest_sha"], self.fixture["system"], self.data)
        with self.assertRaises(stage.Failure):
            stage.validate_host(self.plan_file, self.plan_sha, self.fixture["directory"],
                                "0" * 64, self.fixture["system"], self.data)

    def test_invalid_hfs_header_alt_geometry_journal_and_size(self):
        h = self.data.open("rb")
        h.seek(1024)
        original = h.read(512)
        h.close()
        try:
            for offset, value in ((0, 0), (4, 0), (40, 0), (44, 0)):
                header = bytearray(original)
                struct.pack_into(">I" if offset else ">H", header, offset, value)
                with self.data.open("r+b") as target:
                    target.seek(1024); target.write(header)
                    target.seek(self.data.stat().st_size - 1024); target.write(header)
                with self.subTest(offset=offset), self.assertRaises(stage.Failure):
                    stage.validate_data(self.data, self.data.stat().st_size)
            with self.data.open("r+b") as target:
                target.seek(1024); target.write(original)
            with self.assertRaises(stage.Failure):
                stage.validate_data(self.data, self.data.stat().st_size)
            with self.assertRaises(stage.Failure):
                stage.validate_data(self.data, self.data.stat().st_size - 4096)
        finally:
            with self.data.open("r+b") as target:
                target.seek(1024); target.write(original)
                target.seek(self.data.stat().st_size - 1024); target.write(original)

    def test_explicit_serial_and_fixed_transport(self):
        with self.assertRaises(stage.Failure):
            stage.Adb("adb", "", MemoryLog())
        with self.assertRaises(stage.Failure):
            stage.Adb("adb", "serial;evil", MemoryLog())
        adb = stage.Adb("adb.exe", "phone-serial", MemoryLog())
        run_local = stage.subprocess.run
        def stand_in(arguments, **kwargs):
            self.assertEqual(arguments[:6], ["adb.exe", "-s", "phone-serial", "exec-out", "su", "-c"])
            self.assertIn("sw_stage_rc=$?", arguments[6])
            return run_local(["sh", "-c", arguments[6]], **kwargs)
        with mock.patch.object(stage.subprocess, "run", side_effect=stand_in):
            self.assertEqual(adb.root("printf '0\\n'"), "0")
            self.assertEqual(adb.root("printf '\\000\\377'", binary=True), bytes([0, 255]))
            # The wrapper itself returns 0, reproducing exec-out's observed behavior;
            # the remote trailer must still reject the failed command.
            with self.assertRaises(stage.Failure):
                adb.root("exit 1")
            with self.assertRaises(stage.Failure):
                adb.root("false && printf 'must-not-run'")
        missing = type("Completed", (), {"returncode": 0, "stdout": b"0\n", "stderr": b""})()
        with mock.patch.object(stage.subprocess, "run", return_value=missing), self.assertRaises(stage.Failure):
            adb.root("id -u")

    def test_adb_push_stdout_error_is_logged_and_reported(self):
        log = MemoryLog()
        adb = stage.Adb("adb.exe", "phone-serial", log)
        result = type("Completed", (), {"returncode": 1, "stdout": b"adb: Permission denied\n", "stderr": b""})()
        with mock.patch.object(stage.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(stage.Failure, "Permission denied"):
                adb.push("source", "/private/destination")
        self.assertEqual(log.events[-1][0], "push")
        self.assertIn("Permission denied", log.events[-1][1]["stdout"])

    def test_tampered_layout_and_system_hash_rejected_before_adb(self):
        original = json.loads(self.plan_file.read_text())
        mutations = [lambda p: p["partitions"][0].update(first_lba=4096),
                     lambda p: p["partitions"][1].update(last_lba=(1 << 64) - 1),
                     lambda p: p["system_image"].update(sha256="0" * 64),
                     lambda p: p.update(disk="/dev/block/sda")]
        for mutate in mutations:
            with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                plan = copy.deepcopy(original)
                mutate(plan)
                file = Path(temporary) / "plan.json"
                raw = json.dumps(plan).encode()
                file.write_bytes(raw)
                with mock.patch.object(stage.subprocess, "run", side_effect=AssertionError("no ADB before host validation")), self.assertRaises(stage.Failure):
                    stage.validate_host(file, hashlib.sha256(raw).hexdigest(), self.fixture["directory"],
                                        self.fixture["manifest_sha"], self.fixture["system"], self.data)

    def test_sgdisk_accepts_exact_factory_warnings_and_rejects_unknowns(self):
        factory = """Warning: There is a gap between the main partition table (ending sector 4)
and the first usable sector (6). This is helpful in some exotic configurations,
but is unusual. The util-linux fdisk program often creates disks like this.
Using 'j' on the experts' menu can adjust this gap.

Warning: The size of the partition table (12288 bytes) is less than the minimum
required by the GPT specification. Most OSes and tools seem to work fine on
such disks, but this is a violation of the GPT specification and so may cause
problems.

No problems found. 1113067 free sectors (4.2 GiB) available in 1
segments, the largest of which is 1113067 (4.2 GiB) in size.
"""
        primary = self.context["disk"]["primary"]
        stage.validate_sgdisk(factory, primary)
        stage.validate_sgdisk("No problems found.", primary)
        for text in (factory + "Warning: corrupt backup GPT.", factory.replace("ending sector 4", "ending sector 5"),
                     "Problem: CRC mismatch. No problems found."):
            with self.assertRaises(stage.Failure):
                stage.validate_sgdisk(text, primary)
        with self.assertRaises(stage.Failure):
            stage.validate_sgdisk(factory, {**primary, "entry_count": 128})



if __name__ == "__main__":
    unittest.main(verbosity=2)
