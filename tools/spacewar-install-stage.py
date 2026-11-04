#!/usr/bin/env python3
"""Review or stage the Phone (1) free-tail layout; default is read-only.

The reviewed plan and verified backup identities must be pinned explicitly.
Without --apply this validates host files and reads fresh phone state only.
With --apply it stages files, writes only the two planned free-tail extents,
then updates and verifies the backup GPT before the primary GPT. It never writes
boot partitions, switches slots, reboots, or changes Android's userdata layout.
--verify-staged checks the already-written images and new GPT without block
writes; optional --cleanup-staging removes known files only after those checks.
Failure retains the private host log and remote staging files for review.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shlex
import struct
import subprocess
import sys
from datetime import datetime, timezone
import uuid

SPEC = importlib.util.spec_from_file_location("spacewar_install_plan", Path(__file__).with_name("spacewar-install-plan.py"))
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)
Failure, need = planner.Failure, planner.need
DISK = "/dev/block/sde"
BLOCK, DISK_BYTES, LUN = 4096, 6442450944, 4
BOOTCTL = "/data/adb/magisk/bootctl"
BUSYBOX = "/data/adb/magisk/busybox"


def validate_data(path, expected_bytes):
    path = planner.regular(path).resolve()
    need(path.stat().st_size == expected_bytes, "data image size differs from planned HFS+ extent")
    with path.open("rb") as image:
        image.seek(1024)
        primary = image.read(512)
        image.seek(expected_bytes - 1024)
        alternate = image.read(512)
    need(len(primary) == 512 and len(alternate) == 512 and primary == alternate,
         "HFSX primary and alternate volume headers differ")
    signature, version, attributes, _, journal = struct.unpack_from(">HHIII", primary)
    allocation, blocks, free_blocks = struct.unpack_from(">III", primary, 40)
    need(signature == 0x4858 and version == 5 and attributes & 0x2000 and
         journal > 0 and allocation == BLOCK and blocks == expected_bytes // BLOCK and
         free_blocks < blocks and journal < blocks, "invalid, unjournaled, or incorrectly sized HFSX image")
    size, sha, _ = planner.hash_file(path)
    return {"path": str(path), "bytes": size, "sha256": sha}


def validate_host(plan_file, plan_sha, backup, backup_sha, system, data):
    plan_file = planner.regular(plan_file).resolve()
    raw = plan_file.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == planner.digest(plan_sha, "reviewed plan"),
         "reviewed plan SHA-256 mismatch")
    plan = json.loads(raw)
    need(plan["format"] == "spacewar-install-plan-v1" and plan["disk"] == DISK and
         plan["lun"] == LUN and plan["block_size"] == BLOCK and
         plan["disk_bytes"] == DISK_BYTES and plan["disk_blocks"] == DISK_BYTES // BLOCK,
         "plan is not the fixed Phone (1) 4 KiB/6 GiB LUN4 layout")
    need(plan["backup_manifest_sha256"] == planner.digest(backup_sha, "reviewed backup manifest"),
         "plan and reviewed backup manifest differ")
    _, manifest_sha, disks, proofs = planner.verify_backup(backup, backup_sha)
    need(DISK in disks, "verified backup lacks sde/LUN4")
    disk = disks[DISK]
    need(disk["geometry"]["block_size"] == BLOCK and disk["geometry"]["bytes"] == DISK_BYTES and
         disk["primary"]["disk_guid"] == plan["disk_guid"] and
         plan["verified_backup_files"] == proofs and plan["gpt_entry_count"] == disk["primary"]["entry_count"],
         "plan geometry or file proofs differ from verified backup")
    extents = plan["partitions"]
    need(isinstance(extents, list) and len(extents) == 2, "plan requires exactly two new partitions")
    # Derive every bound independently; only the two previously generated GUIDs are reused.
    expected = planner.allocate(disk)
    for actual, canonical in zip(extents, expected):
        need(set(actual) == set(canonical) and all(actual[key] == canonical[key]
             for key in canonical if key != "unique_guid"), "plan partition bounds, name, type or entry changed")
        guid = uuid.UUID(actual["unique_guid"])
        need(guid.int and str(guid) == actual["unique_guid"] and str(guid) != plan["disk_guid"],
             "invalid planned partition GUID")
        for at in range(0, len(disk["entries"]), 128):
            need(guid.bytes_le != disk["entries"][at + 16:at + 32], "new GUID conflicts with existing entry")
    need(extents[0]["unique_guid"] != extents[1]["unique_guid"], "duplicate new partition GUID")
    system = planner.regular(system).resolve()
    need(system.stat().st_size == plan["system_image"]["bytes"] and
         0 < system.stat().st_size <= extents[0]["bytes"] and system.stat().st_size % BLOCK == 0,
         "system image has incorrect size or does not fit")
    root_size, root_sha, root_crc = planner.hash_file(system, with_crc=True)
    need(root_sha == plan["system_image"]["sha256"] and root_crc == plan["system_image"]["crc32"],
         "system image SHA-256 or CRC32 differs from plan")
    with system.open("rb") as image:
        superblock = image.read(64)
    need(superblock[32:36] == b"NXSB" and struct.unpack_from("<I", superblock, 36)[0] == BLOCK and
         0 < struct.unpack_from("<Q", superblock, 40)[0] <= root_size // BLOCK, "invalid APFS image")
    data_info = validate_data(data, extents[1]["bytes"])
    expected_maps = {"install.map": planner.make_map(plan["disk_guid"], BLOCK, *extents, root_size, root_crc, 1),
                     "install-persist.map": planner.make_map(plan["disk_guid"], BLOCK, *extents, root_size, root_crc, 3)}
    need(len(plan["maps"]) == 2 and {item["file"] for item in plan["maps"]} == set(expected_maps),
         "unexpected install-map list")
    for item in plan["maps"]:
        need(item["flags"] == (1 if item["file"] == "install.map" else 3) and item["bytes"] == 152 and
             item["sha256"] == hashlib.sha256(expected_maps[item["file"]]).hexdigest() and
             planner.backup_path(plan_file.parent, item["file"]).read_bytes() == expected_maps[item["file"]],
             "install map does not match reviewed plan")
    patched = planner.patch_gpt(disk, extents)
    expected_ranges = []
    for piece in patched:
        filename = "gpt-sde-" + piece["part"] + ".new.bin"
        expected_ranges.append({"file": filename, "source_file": piece["file"],
            "first_lba": piece["skip_blocks"], "blocks": piece["blocks"], "bytes": len(piece["content"]),
            "before_sha256": piece["sha256"], "sha256": hashlib.sha256(piece["content"]).hexdigest(),
            "changed_bytes": piece["changed_bytes"],
            "allowed_changed_byte_ranges": [list(pair) for pair in piece["allowed_changes"]],
            "copy": "primary" if piece["skip_blocks"] == 0 else "backup"})
        need(planner.backup_path(plan_file.parent, filename).read_bytes() == piece["content"],
             "patched GPT file differs from independently generated whitelist")
    expected_ranges.sort(key=lambda item: (item["copy"] == "primary", item["first_lba"]))
    need(plan["gpt_metadata_ranges_backup_first"] == expected_ranges, "GPT range/order/whitelist differs from plan")
    boot = {}
    for number, name in ((11, "boot_a"), (35, "boot_b")):
        raw_entry = disk["entries"][(number - 1) * 128:number * 128]
        entry = planner.decode_entry(raw_entry)
        need(raw_entry[:16] != bytes(16) and entry["name"] == name and entry["blocks"] * BLOCK == 96 << 20,
             "unexpected boot A/B GPT identity or bounds")
        matches = [item for item in json.loads((Path(backup) / "manifest.json").read_text())["partitions"]
                   if item.get("name") == name]
        need(len(matches) == 1 and matches[0]["node"] == DISK + str(number) and
             matches[0]["bytes"] == entry["blocks"] * BLOCK, "boot backup node/size differs from GPT")
        boot[name] = {**entry, "node": DISK + str(number)}
    return {"plan": plan, "plan_file": plan_file, "plan_sha256": plan_sha, "backup": Path(backup).resolve(),
            "backup_manifest_sha256": manifest_sha, "disk": disk, "ranges": expected_ranges,
            "boot": boot, "images": [{"path": str(system), "bytes": root_size, "sha256": root_sha,
                         "first_lba": extents[0]["first_lba"], "name": "system.apfs"},
                        {**data_info, "first_lba": extents[1]["first_lba"], "name": "data.hfs"}]}


class Log:
    def __init__(self, path):
        self.directory = Path(path)
        need(not self.directory.exists(), "log directory already exists")
        self.directory.mkdir(mode=0o700)
        self.events = self.directory / "events.jsonl"
        self.descriptor = os.open(self.events, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.file = os.fdopen(self.descriptor, "w", buffering=1)

    def event(self, kind, **fields):
        self.file.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": kind, **fields}) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


class Adb:
    def __init__(self, executable, serial, log):
        need(serial and re.fullmatch(r"[A-Za-z0-9._:-]+", serial), "an explicit ADB serial is required")
        self.command = [executable, "-s", serial]
        self.log = log

    def root(self, command, binary=False):
        # Some Android exec-out/su combinations return ADB status 0 even when
        # the remote command failed. A fresh trailer authenticates completion
        # and carries the remote status; it also works after binary GPT output.
        marker = "SW_STAGE_" + uuid.uuid4().hex
        wrapped = "(" + command + "); sw_stage_rc=$?; printf '\\n" + marker + ":%d\\n' \"$sw_stage_rc\""
        result = subprocess.run(self.command + ["exec-out", "su", "-c", wrapped], capture_output=True)
        output, separator, status = result.stdout.rpartition(("\n" + marker + ":").encode())
        need(result.returncode == 0 and separator and re.fullmatch(rb"[0-9]+\n", status),
             "missing remote completion status: " + command)
        remote_status = int(status.strip())
        self.log.event("root-command", command=command, status=result.returncode,
                       remote_status=remote_status, stdout_bytes=len(output),
                       stderr=result.stderr.decode(errors="replace")[:2000])
        need(remote_status == 0, "remote command failed (%d): %s: %s" %
             (remote_status, command, result.stderr.decode(errors="replace")[:500]))
        return output if binary else output.decode(errors="strict").strip()

    def push(self, source, destination):
        result = subprocess.run(self.command + ["push", str(source), destination], capture_output=True)
        stdout = result.stdout.decode(errors="replace")
        stderr = result.stderr.decode(errors="replace")
        self.log.event("push", source=str(source), destination=destination, status=result.returncode,
                       stdout=stdout[:2000], stderr=stderr[:2000])
        need(result.returncode == 0, "ADB push failed: " + destination + ": " +
             (stdout + "\n" + stderr).strip()[:1000])


def read_command(first, count, hashed=False):
    need(type(first) is int and type(count) is int and 0 <= first < DISK_BYTES // BLOCK and
         0 < count <= DISK_BYTES // BLOCK - first, "device read exceeds fixed disk")
    command = "%s dd if=%s bs=%d skip=%d count=%d 2>/dev/null" % (BUSYBOX, DISK, BLOCK, first, count)
    return command + " | sha256sum" if hashed else command


def remote_hash(adb, path):
    value = adb.root("sha256sum " + shlex.quote(path)).split()
    need(value and re.fullmatch(r"[0-9a-f]{64}", value[0]), "invalid remote file hash")
    return value[0]


def range_hash(adb, first, count):
    value = adb.root(read_command(first, count, hashed=True)).split()
    need(value and re.fullmatch(r"[0-9a-f]{64}", value[0]), "invalid readback hash")
    return value[0]


def phone_identity(context, adb):
    need(adb.root("id -u") == "0", "ADB shell is not root")
    need(adb.root("getprop ro.product.device").lower() == "spacewar", "phone is not Spacewar")
    need(adb.root("getprop sys.boot_completed") == "1", "Android has not completed boot")
    checks = (("get-number-slots", "2"), ("get-current-slot", "0"), ("get-active-boot-slot", "0"),
              ("is-slot-bootable 0", "1"), ("is-slot-marked-successful 0", "1"),
              ("is-slot-bootable 1", "1"), ("is-slot-marked-successful 1", "1"),
              ("get-snapshot-merge-status", "MergeStatus::NONE"))
    for operation, expected in checks:
        need(adb.root(BOOTCTL + " " + operation) == expected, "unexpected boot HAL state: " + operation)
    need(adb.root("blockdev --getss " + DISK) == str(BLOCK) and
         adb.root("blockdev --getsize64 " + DISK) == str(DISK_BYTES), "live sde geometry differs")
    for name in ("boot_a", "boot_b"):
        need(adb.root("readlink -f /dev/block/by-name/" + name) == context["boot"][name]["node"],
             "live boot partition node differs: " + name)


def preflight(context, adb):
    phone_identity(context, adb)
    fresh = []
    for item in context["disk"]["pieces"]:
        content = adb.root(read_command(item["skip_blocks"], item["blocks"]), binary=True)
        need(len(content) == item["bytes"] and hashlib.sha256(content).hexdigest() == item["sha256"],
             "live GPT no longer matches reviewed backup: " + item["part"])
        fresh.append({**item, "content": content})
    live = planner.verify_gpt(context["disk"]["geometry"], fresh)
    for number, name in ((11, "boot_a"), (35, "boot_b")):
        need(planner.decode_entry(live["entries"][(number - 1) * 128:number * 128]) ==
             {key: value for key, value in context["boot"][name].items() if key != "node"},
             "live boot GPT identity differs: " + name)


def bounded_write(adb, remote, first, byte_count):
    need(type(first) is int and type(byte_count) is int and byte_count > 0 and byte_count % BLOCK == 0 and
         0 <= first < DISK_BYTES // BLOCK and byte_count // BLOCK <= DISK_BYTES // BLOCK - first,
         "device write exceeds fixed disk")
    need(re.fullmatch(r"/data/local/tmp/spacewar-install-[0-9a-f]{64}/[A-Za-z0-9._-]+", remote),
         "source is outside this install's private staging directory")
    adb.root("%s dd if=%s of=%s bs=%d seek=%d count=%d conv=notrunc,fsync && %s sync" %
             (BUSYBOX, shlex.quote(remote), DISK, BLOCK, first, byte_count // BLOCK, BUSYBOX))



def validate_sgdisk(output, primary):
    text = " ".join(output.split())
    # These exact paragraphs occur on the backed-up factory 96-entry table.
    # CRC-validated raw GPT bytes remain authoritative. No other diagnostic
    # is accepted by the final, secondary sgdisk check.
    factory = primary["entry_count"] == 96 and primary["entry_size"] == 128 and primary["first_usable"] == 6 and primary["entries_lba"] == 2
    allowed = (
        "Warning: There is a gap between the main partition table (ending sector 4) "
        "and the first usable sector (6). This is helpful in some exotic configurations, "
        "but is unusual. The util-linux fdisk program often creates disks like this. "
        "Using 'j' on the experts' menu can adjust this gap.",
        "Warning: The size of the partition table (12288 bytes) is less than the minimum "
        "required by the GPT specification. Most OSes and tools seem to work fine on "
        "such disks, but this is a violation of the GPT specification and so may cause problems.",
    )
    if factory:
        for paragraph in allowed:
            if paragraph in text:
                text = text.replace(paragraph, "", 1).strip()
    need(re.fullmatch(r"No problems found\.(?: \d+ free sectors \([^)]*\) available in \d+ "
                      r"segments?, the largest of which is \d+ \([^)]*\) in size\.)?", text),
         "sgdisk reported an unexpected diagnostic: " + output)


def staging_directory(context):
    need(re.fullmatch(r"[0-9a-f]{64}", context["plan_sha256"]), "invalid staging identity")
    return "/data/local/tmp/spacewar-install-" + context["plan_sha256"]


def cleanup_staging(context, adb, log):
    directory = staging_directory(context)
    adb.root("test -d " + directory + " && test ! -L " + directory)
    need(adb.root("stat -c '%u:%g:%a' " + directory) == "0:0:700",
         "staging directory is not locked and owned by root")
    names = [image["name"] for image in context["images"]] + [item["file"] for item in context["ranges"]]
    for name in names:
        adb.root("rm " + directory + "/" + name)
    adb.root("rmdir " + directory)
    log.event("staging-cleaned")


def verify_completed(context, adb, log):
    """Verify a freshly completed installation without trusting the old GPT."""
    phone_identity(context, adb)
    fresh = []
    for item in context["ranges"]:
        content = adb.root(read_command(item["first_lba"], item["blocks"]), binary=True)
        need(len(content) == item["bytes"] and hashlib.sha256(content).hexdigest() == item["sha256"],
             "new live GPT differs from completed plan: " + item["file"])
        fresh.append({"skip_blocks": item["first_lba"], "blocks": item["blocks"],
                      "bytes": item["bytes"], "content": content})
    geometry = copy.deepcopy(context["disk"]["geometry"])
    for key, here, other in (("primary", 1, geometry["blocks"] - 1),
                             ("backup", geometry["blocks"] - 1, 1)):
        raw = planner.read_range(fresh, BLOCK, here, 1)
        parsed = planner.header(raw, here, other, geometry["blocks"], BLOCK)
        need(all(parsed[field] == value for field, value in geometry[key].items()
                 if field not in ("header_crc32", "entries_crc32")), "new GPT geometry differs from original")
        geometry[key] = parsed
    planner.verify_gpt(geometry, fresh)
    for image in context["images"]:
        need(range_hash(adb, image["first_lba"], image["bytes"] // BLOCK) == image["sha256"],
             "completed image readback SHA-256 mismatch: " + image["name"])
        log.event("completed-image-verified", name=image["name"], bytes=image["bytes"])
    validate_sgdisk(adb.root("sgdisk --verify " + DISK + " 2>&1"), geometry["primary"])
    log.event("completed-install-verified", plan_sha256=context["plan_sha256"])

def stage(context, adb, log, apply=False, verify_staged=False, cleanup=False):
    need(not (apply and verify_staged) and (not cleanup or verify_staged), "invalid staging operation")
    if verify_staged:
        verify_completed(context, adb, log)
        if cleanup:
            cleanup_staging(context, adb, log)
        return
    preflight(context, adb)
    plan = context["plan"]
    log.event("preflight-passed", apply=apply, plan_sha256=context["plan_sha256"])
    if not apply:
        log.event("dry-run-complete", images=context["images"], metadata=context["ranges"])
        return
    # Check space before the first phone mutation. All paths below are fixed by a hash.
    required = sum(item["bytes"] for item in context["images"]) + 16 * planner.MIB
    lines = adb.root("df -k /data/local/tmp").splitlines()
    need(len(lines) >= 2 and len(lines[-1].split()) >= 4 and lines[-1].split()[3].isdigit() and
         int(lines[-1].split()[3]) * 1024 >= required, "insufficient Android staging space")
    remote_dir = staging_directory(context)
    adb.root("mkdir -m 700 " + remote_dir)
    # adbd runs as shell (2000), independently of each su command. Give it a
    # private directory for upload; root takes it back before any block write.
    adb.root("chown 2000:2000 " + remote_dir)
    staged = []
    for image in context["images"]:
        staged.append({**image, "file": image["name"]})
    for item in context["ranges"]:
        staged.append({**item, "path": str(context["plan_file"].parent / item["file"])})
    for item in staged:
        remote = remote_dir + "/" + item["file"]
        adb.push(item["path"], remote)
        adb.root("chown 0:0 " + remote)
        adb.root("chmod 600 " + remote)
        need(remote_hash(adb, remote) == item["sha256"], "staged file SHA-256 mismatch: " + item["file"])
    # While shell owns the directory it can replace a file despite its root
    # ownership. Freeze the directory, then verify every staged file again.
    adb.root("chown 0:0 " + remote_dir)
    adb.root("chmod 700 " + remote_dir)
    for item in staged:
        remote = remote_dir + "/" + item["file"]
        need(remote_hash(adb, remote) == item["sha256"],
             "locked staged file SHA-256 mismatch: " + item["file"])
    log.event("staging-frozen-and-verified", files=len(staged))
    preflight(context, adb)  # refuse slot/GPT changes while the large files were pushed
    for image in context["images"]:
        bounded_write(adb, remote_dir + "/" + image["name"], image["first_lba"], image["bytes"])
        need(range_hash(adb, image["first_lba"], image["bytes"] // BLOCK) == image["sha256"],
             "image readback SHA-256 mismatch: " + image["name"])
        log.event("image-verified", name=image["name"], first_lba=image["first_lba"], bytes=image["bytes"])
    preflight(context, adb)  # image writes precede GPT publication; old GPT must still match
    for item in context["ranges"]:  # validated backup arrays/header first, primary range last
        bounded_write(adb, remote_dir + "/" + item["file"], item["first_lba"], item["bytes"])
        need(range_hash(adb, item["first_lba"], item["blocks"]) == item["sha256"],
             "GPT readback SHA-256 mismatch: " + item["file"])
        log.event("gpt-range-verified", **item)
    for item in context["ranges"]:
        need(range_hash(adb, item["first_lba"], item["blocks"]) == item["sha256"],
             "final GPT range changed: " + item["file"])
    verify = adb.root("sgdisk --verify " + DISK + " 2>&1")
    validate_sgdisk(verify, context["disk"]["primary"])
    log.event("complete", plan_sha256=context["plan_sha256"], slot="A unchanged; no boot image written")
    # Remove only this tool's exact staged files, after every write and verification passed.
    cleanup_staging(context, adb, log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--backup-manifest-sha256", required=True)
    parser.add_argument("--system", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--serial", required=True, help="explicit ADB device serial")
    parser.add_argument("--adb", default="adb", help="ADB executable; no shell arguments")
    parser.add_argument("--log-dir", type=Path, help="new private host directory (default: beside plan)")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--apply", action="store_true", help="perform the reviewed image/GPT writes")
    operations.add_argument("--verify-staged", action="store_true",
                            help="read-only verification of freshly written images and the NEW GPT")
    parser.add_argument("--cleanup-staging", action="store_true",
                        help="with --verify-staged: remove only known staging files after verification passes")
    args = parser.parse_args(argv)
    if args.cleanup_staging and not args.verify_staged:
        parser.error("--cleanup-staging requires --verify-staged")
    log = None
    try:
        context = validate_host(args.plan, args.plan_sha256, args.backup,
                                args.backup_manifest_sha256, args.system, args.data)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        log = Log(args.log_dir or args.plan.parent / ("stage-" + timestamp))
        log.event("host-validated", plan_sha256=args.plan_sha256,
                  backup_manifest_sha256=context["backup_manifest_sha256"], apply=args.apply,
                  verify_staged=args.verify_staged, cleanup_staging=args.cleanup_staging,
                  images=context["images"], metadata=context["ranges"])
        adb = Adb(args.adb, args.serial, log)
        stage(context, adb, log, apply=args.apply, verify_staged=args.verify_staged, cleanup=args.cleanup_staging)
        if args.verify_staged:
            print("Completed images and new GPT verified; no block writes performed.")
        elif args.apply:
            print("Staging and GPT verification completed; slot A unchanged.")
        else:
            print("Dry run passed; phone state was read only. Add --apply only after reviewing the plan and log.")
        print("Private host log:", log.directory)
        return 0
    except (Failure, OSError, ValueError, KeyError, TypeError, struct.error) as error:
        if log:
            log.event("failed", reason=str(error))
            print("Private host log retained:", log.directory, file=sys.stderr)
        print("spacewar-install-stage: " + str(error), file=sys.stderr)
        print("No cleanup was attempted after failure; preserve staged files and logs.", file=sys.stderr)
        return 1
    finally:
        if log:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
