#!/usr/bin/env python3
"""Host-test debug payload integrity and real capture/recovery orchestration."""
import base64
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import zlib

spec = importlib.util.spec_from_file_location("debug", Path(__file__).with_name("spacewar-debug.py"))
debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(debug)


class Transport:
    serial = "xnuloader"

    def __init__(self, data=b"kernel evidence\n", snapshots=1, transient=False):
        self.data, self.snapshots, self.transient = data, snapshots, transient
        self.calls, self.count = [], 0

    def devices(self):
        if self.transient:
            self.transient = False
            raise subprocess.TimeoutExpired("fastboot devices", 8)
        return [self.serial]

    def run(self, *args, **kwargs):
        self.calls.append(args)
        if args == ("getvar", "product"):
            return "product: xnuloader"
        if args == ("getvar", "touch-lease"):
            return "touch-lease: spacewar-v1"
        if args == ("getvar", "debug"):
            return "debug: start-trace=1 raw-log=0"
        if args == ("getvar", "result"):
            return "result: returned 100 0" if self.count + 1 >= self.snapshots else "result: running 0 0"
        if args[0] == "get_staged":
            Path(args[1]).write_bytes(self.data)
            self.count += 1
        return "OKAY"


class DebugTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.args = SimpleNamespace(out=self.out, seconds=10, poll=0, name=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loader_checks_reject_stock_or_non_debug_loader(self):
        fb = Transport()
        debug.check_loader(fb)
        original = fb.run
        fb.run = lambda *a, **kw: "product: Spacewar" if a == ("getvar", "product") else original(*a, **kw)
        with self.assertRaises(RuntimeError):
            debug.check_loader(fb)
        fb.run = lambda *a, **kw: "debug: start-trace=0 raw-log=0" if a == ("getvar", "debug") else original(*a, **kw)
        with self.assertRaises(RuntimeError):
            debug.check_loader(fb)

    def test_capture_recovers_from_timeout_and_retains_complete_log(self):
        fb = Transport(transient=True)
        self.assertEqual(debug.capture(self.args, fb), 0)
        self.assertEqual((self.out / "latest.private.log").read_bytes(), fb.data)
        result = json.loads((self.out / "session.json").read_text())
        self.assertEqual(result["transport_failures"], 1)
        self.assertFalse(result["kernel_stopped_by_tool"])
        self.assertFalse(any(c[:2] == ("oem", "stop") for c in fb.calls))

    def test_loader_log_is_preserved_before_new_run(self):
        fb = Transport(b"startup: USB StartEx\n")
        debug.before_run(fb, self.out)
        self.assertEqual((self.out / "loader-before-run.private.log").read_bytes(), fb.data)
        self.assertEqual(fb.calls[-2][0:2], ("oem", "log"))
        self.assertEqual(fb.calls[-1][0], "get_staged")

    def test_marker_finishes_capture_without_stopping_kernel(self):
        fb = Transport(b"probe: 0 failures\n", snapshots=100)
        self.args.until = "probe: 0 failures"
        self.assertEqual(debug.capture(self.args, fb), 0)
        self.assertEqual(fb.count, 1)
        report = json.loads((self.out / "session.json").read_text())
        self.assertTrue(report["reached_marker"])
        self.assertFalse(report["kernel_stopped_by_tool"])
        self.assertFalse(any(c[:2] == ("oem", "stop") for c in fb.calls))

    def test_returned_kernel_evidence_is_saved_before_oem_log_replaces_it(self):
        fb = Transport(b"== KERNEL LOG ==\nmessage buffer evidence\n")
        debug.before_run(fb, self.out)
        self.assertEqual((self.out / "kernel-returned.private.log").read_bytes(), fb.data)
        staged = next(i for i, c in enumerate(fb.calls) if c[0] == "get_staged")
        self.assertLess(staged, fb.calls.index(("oem", "log")))

    def test_hung_or_unknown_kernel_cannot_be_replaced(self):
        fb = Transport()
        for text in ("result: running 0 0", "result: hung 100 0", "unknown state"):
            fb.run = lambda *a, **kw: text
            with self.assertRaises(RuntimeError):
                debug.before_run(fb, self.out)
        self.assertFalse((self.out / "loader-before-run.private.log").exists())

    def test_snapshot_retention_is_bounded(self):
        self.assertEqual(debug.capture(self.args, Transport(snapshots=35)), 0)
        saved = sorted(self.out.glob("snapshot-*.private.log"))
        self.assertEqual(len(saved), 32)
        self.assertEqual(saved[0].name, "snapshot-00003.private.log")
        self.assertEqual(saved[-1].name, "snapshot-00034.private.log")

    def test_oversized_snapshot_is_not_published(self):
        fb = Transport(b"x" * (2 * 1024 * 1024 + 1))
        self.args.seconds = 0.02
        self.assertEqual(debug.capture(self.args, fb), 1)
        self.assertFalse((self.out / "latest.private.log").exists())
        self.assertFalse((self.out / "snapshot.partial.log").exists())

    def test_actual_lr1_debug_capture_is_decoded_and_hashed(self):
        name, data = "hakios-debug-Ab12cD", b"brightness available=0\n"
        crc = zlib.crc32(data)
        identity = zlib.crc32(data, zlib.crc32(name.encode()))
        frames = (f"LR1 BEGIN {identity:08x} {name} {len(data)} 1 {crc:08x} readonly\n"
                  f"LR1 DATA {identity:08x} 0 0 {len(data)} {crc:08x} {base64.b64encode(data).decode()}\n"
                  f"LR1 END {identity:08x} {len(data)} 1 {crc:08x}\n").encode()
        self.args.name = name
        self.assertEqual(debug.capture(self.args, Transport(frames), recovery=True), 0)
        self.assertEqual((self.out / name).read_bytes(), data)
        self.assertTrue(json.loads((self.out / "session.json").read_text())["capture_recovered"])

    def test_dirty_journal_refusal_ends_capture_without_waiting_for_deadline(self):
        fb = Transport(b"LR1 ERROR read-only HFS mount refused errno=22\nLR1 ERROR capture unavailable\n", snapshots=100)
        self.assertEqual(debug.capture(self.args, fb, recovery=True), 1)
        self.assertEqual(fb.count, 1)
        self.assertIn("read-only HFS", (self.out / "recovery-errors.json").read_text())

    def test_replay_readonly_refusal_can_be_followed_by_valid_frames(self):
        name, data = "hakios-debug-Ab12cD", b"brightness replay evidence\n"
        crc = zlib.crc32(data)
        identity = zlib.crc32(data, zlib.crc32(name.encode()))
        frames = (f"LR1 ERROR read-only HFS mount refused errno=22\n"
                  f"LR1 BEGIN {identity:08x} {name} {len(data)} 1 {crc:08x} replayed\n"
                  f"LR1 DATA {identity:08x} 0 0 {len(data)} {crc:08x} {base64.b64encode(data).decode()}\n"
                  f"LR1 END {identity:08x} {len(data)} 1 {crc:08x}\n").encode()
        self.assertEqual(debug.capture(self.args, Transport(frames), recovery=True), 0)
        self.assertEqual((self.out / name).read_bytes(), data)
        self.assertFalse((self.out / "recovery-errors.json").exists())

    def test_payload_tamper_and_non_xnup_are_rejected(self):
        path = self.out / "debug.bin"
        data = struct.pack("<II", 0x50554e58, 24) + b"\0" * 24
        path.write_bytes(data)
        import hashlib
        path.with_suffix(".audit.json").write_text(json.dumps({"format": "spacewar-debug-payload-v1",
            "result": "PASS", "file": path.name, "sha256": hashlib.sha256(data).hexdigest()}))
        debug.validate_payload(path)
        path.write_bytes(data[:-1] + b"x")
        with self.assertRaises(ValueError):
            debug.validate_payload(path)
        path.write_bytes(b"ANDROID!" + data[8:])
        with self.assertRaises(ValueError):
            debug.validate_payload(path)


if __name__ == "__main__":
    unittest.main()
