#!/usr/bin/env python3
"""Boot an idle debug loader, run XNUP payloads, and retain live USB evidence.

Run on the USB host with native fastboot and native output paths (Windows
Python with fastboot.exe on a Windows USB host). No flash or slot commands.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import time


def private_write(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(data)


class Fastboot:
    def __init__(self, executable, serial, journal=None):
        self.executable, self.serial, self.journal = executable, serial, journal

    def run(self, *args, timeout=8, discovery=False):
        command = [self.executable] + ([] if discovery else ["-s", self.serial]) + list(args)
        started = time.monotonic()
        try:
            r = subprocess.run(command, capture_output=True, timeout=timeout)
            text = (r.stdout + r.stderr).decode(errors="replace")
            if r.returncode:
                raise RuntimeError(text.strip())
            return text
        finally:
            if self.journal:
                with self.journal.open("a", encoding="utf-8") as log:
                    log.write(json.dumps({"time": time.time(), "command": list(args),
                        "elapsed": round(time.monotonic() - started, 3)}) + "\n")

    def devices(self):
        return [row.split()[0] for row in self.run("devices", discovery=True).splitlines()
                if len(row.split()) == 2 and row.split()[1] == "fastboot"]


def check_loader(fb):
    if fb.serial not in fb.devices():
        raise RuntimeError("XNU loader is not visible; boot loader-debug.img and select HakiOS first")
    if "product: xnuloader" not in fb.run("getvar", "product"):
        raise RuntimeError("selected device is not the XNU loader")
    if "spacewar-v1" not in fb.run("getvar", "touch-lease"):
        raise RuntimeError("loader lacks the Spacewar live ownership interface")
    if "start-trace=1 raw-log=0" not in fb.run("getvar", "debug"):
        raise RuntimeError("boot the supplied debug loader before using this bundle")


def validate_payload(path):
    blob = path.read_bytes()
    if len(blob) < 32 or len(blob) > 0x3f00000 or struct.unpack_from("<II", blob) != (0x50554e58, len(blob) - 8):
        raise ValueError("payload must be a bounded XNUP file")
    audit = json.loads(path.with_suffix(".audit.json").read_text())
    if (audit.get("format") != "spacewar-debug-payload-v1" or audit.get("result") != "PASS" or
            audit.get("file") != path.name or audit.get("sha256") != hashlib.sha256(blob).hexdigest()):
        raise ValueError("payload differs from its debug-package audit")
    return audit


def result_state(text):
    for line in text.splitlines():
        if "result:" in line:
            words = line.split("result:", 1)[1].split()
            if words:
                return words[0]
    return "unknown"


# These results already have their final log staged by run_kernel(). Asking
# an idle loader for oem log replaces that evidence with loader-only text.
FINAL_RESULTS = frozenset(("returned", "early-exception", "checkpoint", "hung",
                           "not-started", "no-such-core"))


def before_run(fb, out):
    state = result_state(fb.run("getvar", "result"))
    if state not in ("idle", "returned", "not-started"):
        raise RuntimeError(f"kernel state is {state}; retain logs and use stop or a fresh loader before staging")
    # A completed stop stages the full kernel log, including the message
    # buffer. An idle oem log replaces it with only the loader's own text.
    # Retain that evidence before asking for the next loader snapshot.
    if state == "returned":
        returned = out / "kernel-returned.private.log"
        fb.run("get_staged", str(returned.absolute()), timeout=12)
        if (returned.is_file() and 1 <= returned.stat().st_size <= 2 * 1024 * 1024
                and b"== KERNEL LOG ==" in returned.read_bytes()):
            os.chmod(returned, 0o600)
        else:
            returned.unlink(missing_ok=True)
    # oem run resets the loader log. Preserve initialization/previous-run
    # evidence before the next payload starts.
    path = out / "loader-before-run.private.log"
    fb.run("oem", "log")
    fb.run("get_staged", str(path.absolute()), timeout=12)
    if not path.is_file() or not 1 <= path.stat().st_size <= 2 * 1024 * 1024:
        raise RuntimeError("could not retain the loader log before starting the next run")
    os.chmod(path, 0o600)


def capture(args, fb, recovery=False):
    out = args.out
    spec = importlib.util.spec_from_file_location("log_recovery", Path(__file__).with_name("log-recovery.py"))
    decoder = module = None
    if recovery:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        decoder = module.Decoder(args.name)
    deadline = time.monotonic() + args.seconds
    count = failures = 0
    recovered = False
    marker = getattr(args, "until", None)
    reached_marker = False
    terminal_result = None
    while time.monotonic() < deadline:
        snapshot = out / "snapshot.partial.log"
        try:
            if fb.serial not in fb.devices():
                raise RuntimeError("loader USB absent; reconnect the cable or use a fresh loader boot")
            status = fb.run("getvar", "result")
            state = result_state(status)
            if state not in FINAL_RESULTS:
                fb.run("oem", "log")
            snapshot.unlink(missing_ok=True)
            fb.run("get_staged", str(snapshot.absolute()), timeout=12)
            if not snapshot.is_file() or not 1 <= snapshot.stat().st_size <= 2 * 1024 * 1024:
                raise RuntimeError("missing, empty or oversized loader log")
            data = snapshot.read_bytes()
            if marker and marker.encode() in data:
                reached_marker = True
            name = out / f"snapshot-{count:05d}.private.log"
            private_write(name, data)
            latest = out / "latest.private.log"
            latest.write_bytes(data)
            os.chmod(latest, 0o600)
            if count >= 32:
                (out / f"snapshot-{count - 32:05d}.private.log").unlink(missing_ok=True)
            with (out / "snapshots.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"index": count, "time": time.time(), "bytes": len(data), "result": status.strip()}) + "\n")
            count += 1
            if state in FINAL_RESULTS:
                terminal_result = state
            if decoder:
                new = decoder.feed(data.decode(errors="replace"))
                if new:
                    with (out / "frames.private.log").open("a", encoding="utf-8") as log:
                        log.write("\n".join(new) + "\n")
                if decoder.completed():
                    recovered = module.finish(decoder, out)
                    break
                # The replay helper first reports its read-only refusal and
                # then replays/remounts before exporting. Only the final
                # unavailable message means it cannot produce a capture.
                if any("capture unavailable" in error for error in decoder.errors):
                    private_write(out / "recovery-errors.json", (json.dumps(decoder.errors, indent=2) + "\n").encode())
                    print("Recovery helper refused the capture: " + "; ".join(decoder.errors), flush=True)
                    break
            if count == 1 or count % 10 == 0:
                print(f"Saved {count} USB snapshots; latest: {latest}", flush=True)
            if reached_marker:
                print(f"Capture marker reached: {marker}", flush=True)
                break
            if terminal_result is not None:
                print(f"Kernel result is {terminal_result}; retained its staged final log.", flush=True)
                break
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            failures += 1
            with (out / "errors.private.log").open("a", encoding="utf-8") as log:
                log.write(f"{time.time()}: {error}\n")
            if failures == 1 or failures % 10 == 0:
                print(f"USB capture retry {failures}; saved snapshots are retained.", flush=True)
        finally:
            snapshot.unlink(missing_ok=True)
        time.sleep(args.poll)
    report = {"snapshots": count, "transport_failures": failures, "recovery_requested": recovery,
              "capture_recovered": recovered, "kernel_stopped_by_tool": False,
              "until": marker, "reached_marker": reached_marker,
              "terminal_result": terminal_result}
    private_write(out / "session.json", (json.dumps(report, indent=2) + "\n").encode())
    print(f"Capture ended: {count} complete snapshots, {failures} USB failures. {out}")
    if marker and not reached_marker:
        print(f"Capture marker was not reached: {marker}", flush=True)
    return 0 if count and (not recovery or recovered) and (not marker or reached_marker) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fastboot", default="fastboot")
    p.add_argument("--serial", default="xnuloader", help="XNU loader serial, not stock fastboot's serial")
    sub = p.add_subparsers(dest="action", required=True)
    boot = sub.add_parser("boot", help="RAM-boot the idle loader from stock Spacewar fastboot")
    boot.add_argument("--image", type=Path, default=Path(__file__).with_name("loader-debug.img"))
    boot.add_argument("--stock-serial")
    run = sub.add_parser("run", help="stage an audited payload, run it, and capture live logs")
    run.add_argument("payload", type=Path)
    live = sub.add_parser("capture", help="capture an already running kernel without restarting it")
    live.add_argument("--recovery", action="store_true")
    sub.add_parser("stop", help="request orderly return to the loader and wait for confirmation")
    for part in (run, live):
        part.add_argument("--out", type=Path, required=True, help="new private directory on the USB host")
        part.add_argument("--seconds", type=int, default=360)
        part.add_argument("--poll", type=float, default=1)
        part.add_argument("--name", help="optional exact saved capture name during recovery")
        part.add_argument("--until", help="finish capture when this text appears; leave the kernel running")
    args = p.parse_args()
    if args.action in ("run", "capture"):
        if not 1 <= args.seconds <= 1800 or not 0.25 <= args.poll <= 5:
            p.error("duration must be 1..1800 s and poll 0.25..5 s")
        if args.until is not None and (not 1 <= len(args.until) <= 256 or "\n" in args.until or "\r" in args.until):
            p.error("--until must be 1..256 characters on one line")
        if args.out.exists() or args.out.is_symlink():
            p.error("--out must be a new directory")
        args.out.mkdir(parents=True, mode=0o700)
        os.chmod(args.out, 0o700)
    fb = Fastboot(args.fastboot, args.serial, args.out / "commands.jsonl" if hasattr(args, "out") else None)
    try:
        if args.action == "boot":
            bundle = json.loads(args.image.with_name("bundle.json").read_text())
            if (bundle["bootstrap"]["file"] != args.image.name or
                    bundle["bootstrap"]["sha256"] != hashlib.sha256(args.image.read_bytes()).hexdigest()):
                raise ValueError("bootstrap differs from its bundle audit")
            devices = fb.devices()
            candidates = [s for s in devices if s != args.serial]
            if args.stock_serial:
                candidates = [s for s in candidates if s == args.stock_serial]
            if len(candidates) != 1:
                raise RuntimeError("connect one Spacewar in stock fastboot, or pass --stock-serial")
            stock = Fastboot(args.fastboot, candidates[0])
            if "product: Spacewar" not in stock.run("getvar", "product"):
                raise RuntimeError("stock bootloader is not Spacewar")
            print(stock.run("boot", str(args.image.absolute()), timeout=120))
            if "Mu" in bundle.get("boot_provider", ""):
                print("Mu-Silicium starts the debug loader directly; wait for its USB connection.")
            else:
                print("Select HakiOS in the RAM menu. The debug loader will wait for a payload.")
            return 0
        check_loader(fb)
        if args.action == "stop":
            fb.run("oem", "stop")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                result = fb.run("getvar", "result")
                state = result_state(result)
                if state in ("idle", "returned", "not-started"):
                    print(result.strip())
                    return 0
                if state != "running":
                    raise RuntimeError(f"kernel state is {state}; use a fresh loader boot")
                time.sleep(1)
            raise RuntimeError("kernel has not confirmed its return; do not stage another payload")
        recovery = getattr(args, "recovery", False)
        if args.action == "run":
            audit = validate_payload(args.payload)
            before_run(fb, args.out)
            recovery = audit["kind"] == "recovery"
            print(f"Staging {args.payload.name}; {audit['mount_policy']}", flush=True)
            fb.run("stage", str(args.payload.absolute()), timeout=120)
            fb.run("oem", "run", timeout=15)
        return capture(args, fb, recovery)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        p.exit(1, f"spacewar-debug: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
