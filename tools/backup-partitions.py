#!/usr/bin/env python3
"""Copy the partitions that cannot be made again off the phone, and prove the copies: READ ONLY.

usage: tools/backup-partitions.py OUTDIR [--yes] [--only NAME,NAME] [--require NAME,NAME] [--with-gpt] [--with-bootchain]

Rule 1 of docs/install.md: before anything is ever written to a phone, what could not be made again is copied off it and
checked. This is that copy. It never writes to the phone: the only things it runs there are `ls`, `blockdev`, `dd if=`
(a read) and `sha256sum`. Without --yes it makes no copy at all and says what it would read and how much room that needs.

What is copied (names come from /dev/block/by-name; --require makes a missing selected name fatal):
  the small partitions that decide whether it boots   boot_a boot_b init_boot_a init_boot_b vendor_boot_a vendor_boot_b dtbo_a dtbo_b
                                                       vbmeta_a vbmeta_b vbmeta_system_a vbmeta_system_b misc devinfo
  the ones that hold THIS phone's identity and radio  persist modemst1 modemst2 fsg (the calibration, the IMEI, the Wi-Fi and
                                                       Bluetooth addresses: Android cannot make them again)
  the partition tables                                protective MBR, both headers and complete entry arrays of each parent disk
                                                       (sda ... sdf), using validated GPT geometry and CRCs
  with --with-bootchain: xbl xbl_config abl aop aop_config tz hyp devcfg qupfw uefisecapp shrm cpucp keymaster featenabler imagefv
                                                       (small; what the phone starts from; useful if it ever has to be revived)
Never copied: userdata, super and the other big ones (they are Android's, and their size is the problem).
With --only, tables are omitted unless --with-gpt is supplied. Requested GPT capture fails if a parent disk lacks a valid
matching primary/backup GPT; it never silently skips a non-GPT disk. Logical sectors must be 512 or 4096 bytes, and each
entry array is limited to 16 MiB before it is read. Separate ranges preserve nonadjacent arrays without copying large gaps.

How a copy is proven. The partition is read over adb (exec-out, so the bytes are not changed on the way), its SHA-256 is made as
it arrives, and the phone is then asked for the SHA-256 of the same partition, once BEFORE and once AFTER the copy: the three must
be the same, or the partition changed while it was read (then it is read again, up to three times) or the copy is wrong. The copy
is kept only if they agree. manifest.json says what was copied, its size and hash, and when; restoring is NOT done by this tool:
`restore.txt` says which fastboot command would put each back, for a person to run.

Output uses a new or empty private directory (0700), files are 0600, and only verified temporary copies are retained.
These files are the phone's own and some hold its identity: keep them off the network and out of source control.

Needs root on the phone (`su`): the partitions are not readable by the shell user.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import struct
import uuid
import zlib
import shlex

BOOT = ["boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b", "dtbo_a", "dtbo_b", "vbmeta_a", "vbmeta_b",
        "vbmeta_system_a", "vbmeta_system_b", "misc", "devinfo"]
IDENTITY = ["persist", "modemst1", "modemst2", "fsg"]
BOOTCHAIN = ["xbl", "xbl_config", "abl", "aop", "aop_config", "tz", "hyp", "devcfg", "qupfw", "uefisecapp", "shrm", "cpucp", "keymaster",
             "featenabler", "imagefv"]
BLOCK = 4096
MAX_GPT_ARRAY = 16 * 1024 * 1024
TRIES = 3
ENOUGH_FREE = 1.5           # the room that has to be free on the host, as a multiple of what is copied


class Failure(Exception):
    pass


def device(adb, command, binary=False):
    """Run `command` as root on the phone; its output, or a Failure."""
    r = subprocess.run(adb + ["exec-out", "su", "-c", command], capture_output=True)
    if r.returncode != 0:
        raise Failure("on the phone `%s` failed (%d): %s" % (command, r.returncode, r.stderr.decode(errors="replace").strip()[:200]))
    return r.stdout if binary else r.stdout.decode(errors="replace")


def by_name(adb):
    """{partition name: block device} from /dev/block/by-name, resolved to the device the link points at."""
    out = device(adb, "ls -l /dev/block/by-name")
    parts = {}
    for line in out.splitlines():
        m = re.search(r"\s(\S+) -> (/dev/block/\S+)\s*$", line.strip())
        if m:
            if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", m.group(1)) or not re.fullmatch(r"/dev/block/[A-Za-z0-9_./-]+", m.group(2)):
                raise Failure("unsupported partition name or block-device path")
            parts[m.group(1)] = m.group(2)
    if not parts:
        raise Failure("the phone shows no partitions under /dev/block/by-name (is the shell root? `adb shell su -c id`)")
    return parts


def size_of(adb, node):
    out = device(adb, "blockdev --getsize64 %s" % node).strip()
    if not out.isdigit():
        raise Failure("%s: the size is %r" % (node, out))
    size = int(out)
    if size <= 0:
        raise Failure("%s: invalid zero-sized block device" % node)
    return size


def gpt_plan(adb, node):
    """Validate both GPT copies before deriving bounded ranges to preserve."""
    size = size_of(adb, node)
    value = device(adb, "blockdev --getss %s" % shlex.quote(node)).strip()
    if value not in ("512", "4096"):
        raise Failure("%s: unsupported logical sector size %r" % (node, value))
    block = int(value)
    if size % block or size // block < 6:
        raise Failure("%s: invalid disk geometry" % node)
    blocks = size // block

    def read(first, count):
        if first < 0 or count <= 0 or first >= blocks or count > blocks - first:
            raise Failure("%s: GPT read outside disk" % node)
        command = "dd if=%s bs=%d skip=%d count=%d 2>/dev/null"
        data = device(adb, command % (shlex.quote(node), block, first, count), binary=True)
        if len(data) != count * block:
            raise Failure("%s: truncated GPT read" % node)
        return data

    def header(data, here, other):
        def u32(offset):
            return struct.unpack_from("<I", data, offset)[0]

        def u64(offset):
            return struct.unpack_from("<Q", data, offset)[0]

        length = u32(12)
        if (data[:8] != b"EFI PART" or u32(8) != 0x10000 or
                not 92 <= length <= block or u32(20) or
                u64(24) != here or u64(32) != other):
            raise Failure("%s: invalid/missing GPT header at LBA %d" % (node, here))
        checked = bytearray(data[:length])
        struct.pack_into("<I", checked, 16, 0)
        if zlib.crc32(checked) & 0xffffffff != u32(16):
            raise Failure("%s: GPT header CRC mismatch" % node)
        count, entry_size = u32(80), u32(84)
        if not count or entry_size < 128 or entry_size % 8 or count > MAX_GPT_ARRAY // entry_size:
            raise Failure("%s: GPT entry array exceeds supported bounds" % node)
        first, last = u64(40), u64(48)
        if first < 2 or first > last or last >= blocks - 1:
            raise Failure("%s: invalid GPT usable bounds" % node)
        return {"header_lba": here, "header_crc32": u32(16),
                "first_usable": first, "last_usable": last,
                "disk_guid": str(uuid.UUID(bytes_le=data[56:72])),
                "entries_lba": u64(72), "entry_count": count,
                "entry_size": entry_size, "entries_crc32": u32(88)}

    primary_bytes, backup_bytes = read(1, 1), read(blocks - 1, 1)
    primary = header(primary_bytes, 1, blocks - 1)
    backup = header(backup_bytes, blocks - 1, 1)
    shared = ("first_usable", "last_usable", "disk_guid", "entry_count", "entry_size", "entries_crc32")
    if any(primary[key] != backup[key] for key in shared):
        raise Failure("%s: primary and backup GPT metadata disagree" % node)
    array_bytes = primary["entry_count"] * primary["entry_size"]
    sectors = (array_bytes + block - 1) // block
    first, last = primary["entries_lba"], backup["entries_lba"]
    if (first < 2 or first > primary["first_usable"] or
            sectors > primary["first_usable"] - first or
            last <= primary["last_usable"] or last >= blocks - 1 or
            sectors > blocks - 1 - last):
        raise Failure("%s: GPT arrays overlap usable data or exceed disk" % node)
    entries, mirror = read(first, sectors), read(last, sectors)
    if (entries[:array_bytes] != mirror[:array_bytes] or
            zlib.crc32(entries[:array_bytes]) & 0xffffffff != primary["entries_crc32"]):
        raise Failure("%s: GPT entry arrays differ or fail CRC" % node)
    pieces = sorted([(0, read(0, 1)), (1, primary_bytes), (first, entries),
                     (last, mirror), (blocks - 1, backup_bytes)])
    merged = []
    for start, data in pieces:
        if merged and merged[-1][0] + len(merged[-1][1]) // block == start:
            merged[-1] = (merged[-1][0], merged[-1][1] + data)
        else:
            merged.append((start, data))
    ranges = []
    for start, data in merged:
        label = "head" if start == 0 else "tail" if start + len(data) // block == blocks else "entries-%d" % start
        ranges.append({"part": label, "skip_blocks": start, "blocks": len(data) // block,
                       "bytes": len(data), "expected_sha256": hashlib.sha256(data).hexdigest()})
    return {"disk": node, "bytes": size, "block_size": block, "blocks": blocks,
            "primary": primary, "backup": backup, "ranges": ranges}


def hash_on_phone(adb, node, count=None, skip=None, block=BLOCK):
    """SHA-256 of the whole device or an explicitly bounded logical-block range."""
    if count is None:
        cmd = "sha256sum %s" % shlex.quote(node)
    else:
        cmd = "dd if=%s bs=%d skip=%d count=%d 2>/dev/null | sha256sum" % (shlex.quote(node), block, skip, count)
    out = device(adb, cmd).split()
    if not out or not re.fullmatch(r"[0-9a-f]{64}", out[0]):
        raise Failure("%s: the phone's sha256sum said %r" % (node, out[:1]))
    return out[0]


def copy_one(adb, node, path, size, skip=None, count=None, block=BLOCK):
    """Read the device over adb into `path`; the SHA-256 of what arrived."""
    if count is None:
        cmd = "dd if=%s bs=1048576 skip=0 count=%d 2>/dev/null" % (shlex.quote(node), (size + (1 << 20) - 1) // (1 << 20))
    else:
        cmd = "dd if=%s bs=%d skip=%d count=%d 2>/dev/null" % (shlex.quote(node), block, skip, count)
    h = hashlib.sha256()
    total = 0
    with subprocess.Popen(adb + ["exec-out", "su", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p, open(path, "xb") as out:
        os.chmod(path, 0o600)
        for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
            total += len(chunk)
            if total > size:
                p.kill()
                raise Failure("%s: output exceeds the bounded copy size" % node)
            out.write(chunk)
            h.update(chunk)
        p.wait()
        err = p.stderr.read().decode(errors="replace").strip()
    if p.returncode != 0:
        raise Failure("reading %s failed (%d): %s" % (node, p.returncode, err[:200]))
    if total != size:
        raise Failure("%s: %d bytes arrived, %d were expected" % (node, total, size))
    return h.hexdigest()


def proven_copy(adb, node, path, size, skip=None, count=None, block=BLOCK):
    """A copy that three hashes agree on (the phone's before, the host's, the phone's after), or a Failure."""
    if os.path.exists(path):
        raise Failure("refusing to overwrite existing backup %s" % path)
    temporary = path + ".partial"
    if os.path.exists(temporary):
        raise Failure("refusing to overwrite existing partial backup %s" % temporary)
    last = ""
    for attempt in range(1, TRIES + 1):
        try:
            before = hash_on_phone(adb, node, count, skip, block)
            mine = copy_one(adb, node, temporary, size, skip, count, block)
            after = hash_on_phone(adb, node, count, skip, block)
        except Exception:
            if os.path.exists(temporary):
                os.remove(temporary)
            raise
        if before == mine == after:
            os.replace(temporary, path)
            return mine
        os.remove(temporary)
        last = "phone before %s.., host %s.., phone after %s.." % (before[:12], mine[:12], after[:12])
        print("    attempt %d: the hashes differ (%s)" % (attempt, last))
    raise Failure("%s: the copy could not be proven after %d tries (%s); it was not kept" % (node, TRIES, last))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir")
    ap.add_argument("--yes", action="store_true", help="really copy (without it: say what would be)")
    ap.add_argument("--only", help="only these partitions, comma separated (and their disks' tables are not copied)")
    ap.add_argument("--require", help="these selected partition names must exist, comma separated")
    ap.add_argument("--with-gpt", action="store_true", help="validate and copy GPTs even with --only")
    ap.add_argument("--with-bootchain", action="store_true")
    ap.add_argument("--adb", default="adb", help="the adb program (tests use a stand-in)")
    ap.add_argument("--adb-arg", action="append", default=[], help="an argument to put after the adb program (tests: the stand-in's script)")
    a = ap.parse_args(argv)
    adb = [a.adb] + a.adb_arg
    try:
        parts = by_name(adb)
        wanted = [n.strip() for n in a.only.split(",")] if a.only else BOOT + IDENTITY + (BOOTCHAIN if a.with_bootchain else [])
        wanted = list(dict.fromkeys(wanted))
        required = [n.strip() for n in a.require.split(",")] if a.require else []
        if any(not name or name not in wanted or name not in parts for name in required):
            raise Failure("required partitions must exist and be selected: %s" % ", ".join(required))
        if not wanted or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", n) for n in wanted):
            raise Failure("invalid partition selection")
        plan, missing = [], []
        for name in wanted:
            if name in parts:
                plan.append((name, parts[name], size_of(adb, parts[name])))
            else:
                missing.append(name)
        disks = {}
        if not a.only or a.with_gpt:
            for node in sorted({re.sub(r"p?\d+$", "", n) for n in parts.values() if re.search(r"/sd[a-z]\d+$", n)}):
                disks[node] = gpt_plan(adb, node)
            if not disks:
                raise Failure("GPT capture requested but no supported parent disks were found")
        total = sum(s for _n, _d, s in plan) + sum(r["bytes"] for d in disks.values() for r in d["ranges"])
        print("backup-partitions: %d partitions to copy, %d disks' tables, %.1f MB in all" % (len(plan), len(disks), total / 1e6))
        for name, node, size in plan:
            print("    %-18s %-26s %10d bytes" % (name, node, size))
        for node, disk in disks.items():
            print("    %-18s %-26s %d-byte sectors; GPT ranges %s" %
                  ("table of " + os.path.basename(node), node, disk["block_size"],
                   ", ".join("%d+%d" % (r["skip_blocks"], r["blocks"]) for r in disk["ranges"])))
        for name in missing:
            print("    %-18s the phone does not have it: skipped" % name)
        if not a.yes:
            print("backup-partitions: nothing was copied (add --yes). This tool only reads from the phone.")
            return 0
        if os.path.exists(a.outdir) and (not os.path.isdir(a.outdir) or os.listdir(a.outdir)):
            raise Failure("use a new or empty private output directory; existing backups are preserved")
        os.makedirs(a.outdir, mode=0o700, exist_ok=True)
        os.chmod(a.outdir, 0o700)
        free = __import__("shutil").disk_usage(a.outdir).free
        if free < total * ENOUGH_FREE:
            raise Failure("%.1f MB free in %s, and %.1f MB is %.1f times what is copied: not enough" % (free / 1e6, a.outdir, total * ENOUGH_FREE / 1e6, ENOUGH_FREE))
        manifest = {"made": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "note": "read only; the hashes were checked on the phone before and after each copy", "partitions": [], "tables": [],
                    "gpt_geometry": list(disks.values())}
        restore = ["# What would put these back. NOT run by backup-partitions.py: a person runs a line, knowing why.",
                   "# Never write a bootchain partition (xbl abl tz hyp ...) unless you are certain; a wrong one cannot be undone without EDL.", ""]
        for name, node, size in plan:
            path = os.path.join(a.outdir, name + ".img")
            print("backup-partitions: %s ..." % name, flush=True)
            digest = proven_copy(adb, node, path, size)
            manifest["partitions"].append({"name": name, "node": node, "bytes": size, "sha256": digest, "file": name + ".img"})
            restore.append("fastboot flash %s %s     # sha256 %s" % (name, name + ".img", digest))
        for node, disk in disks.items():
            for region in disk["ranges"]:
                path = os.path.join(a.outdir, "gpt-%s-%s.bin" % (os.path.basename(node), region["part"]))
                digest = proven_copy(adb, node, path, region["bytes"], region["skip_blocks"], region["blocks"], disk["block_size"])
                if digest != region["expected_sha256"]:
                    os.remove(path)
                    raise Failure("%s: GPT changed after validation; copied range discarded" % node)
                manifest["tables"].append({**region, "disk": node, "block_size": disk["block_size"],
                                           "sha256": digest, "file": os.path.basename(path)})
        with open(os.path.join(a.outdir, "manifest.json"), "w", newline="\n") as f:
            os.chmod(f.name, 0o600)
            json.dump(manifest, f, indent=2)
            f.write("\n")
        with open(os.path.join(a.outdir, "restore.txt"), "w", newline="\n") as f:
            os.chmod(f.name, 0o600)
            f.write("\n".join(restore) + "\n")
        print("backup-partitions: %d partitions and %d table copies proven and kept in %s (manifest.json, restore.txt)" %
              (len(manifest["partitions"]), len(manifest["tables"]), a.outdir))
        return 0
    except Failure as e:
        print("backup-partitions: STOPPED: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
