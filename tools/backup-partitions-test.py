#!/usr/bin/env python3
"""tools/backup-partitions.py against a stand-in adb: a "phone" that is a folder of files.

What is checked: the dry run copies nothing; a real run copies exactly the bytes, proves them with three hashes and writes the
manifest and the restore notes (which are text, never run); a partition the phone lacks is skipped and said; a partition that
changes while it is read is read again, and one that never holds still is not kept; a phone without root stops the tool before
a byte is copied; and the stand-in saw only reads (no `of=`, no write).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "backup-partitions.py")
failures = []

# the stand-in adb: `adb exec-out su -c "<command>"` on a phone that is the folder $FAKE_PHONE (parts/NAME, disk_sda)
FAKE = r'''
import hashlib, os, re, sys
root = os.environ["FAKE_PHONE"]
mode = os.environ.get("FAKE_MODE", "")
args = sys.argv[1:]
log = open(os.path.join(root, "commands.log"), "a")
if args[:3] != ["exec-out", "su", "-c"]:
    sys.exit(2)
cmd = args[3]
log.write(cmd + "\n"); log.close()
if mode == "noroot":
    sys.stderr.write("su: not allowed\n"); sys.exit(1)
names = sorted(os.listdir(os.path.join(root, "parts")))
node_file = {"/dev/block/sda%d" % (i + 1): os.path.join(root, "parts", n) for i, n in enumerate(names)}
node_file["/dev/block/sda"] = os.path.join(root, "disk_sda")
counter = os.path.join(root, "hashcount")

def data(node, skip=None, count=None, bs=1048576):
    b = open(node_file[node], "rb").read()
    if skip is not None:
        b = b[skip * bs: (skip + count) * bs]
    return b

def sha(b):
    return hashlib.sha256(b).hexdigest()

m = re.fullmatch(r"ls -l /dev/block/by-name", cmd)
if m:
    for i, n in enumerate(names):
        print("lrwxrwxrwx 1 root root 16 2024-01-01 00:00 %s -> /dev/block/sda%d" % (n, i + 1))
    sys.exit(0)
m = re.fullmatch(r"blockdev --getss (\S+)", cmd)
if m:
    print(open(os.path.join(root,"sector_size")).read()); sys.exit(0)
m = re.fullmatch(r"blockdev --getsize64 (\S+)", cmd)
if m:
    print(os.path.getsize(node_file[m.group(1)])); sys.exit(0)
m = re.fullmatch(r"sha256sum (\S+)", cmd)
if m:
    b = data(m.group(1))
    if mode == "change":                       # the partition changes under the copy: the first hash is of the old content
        n = int(open(counter).read()) if os.path.exists(counter) else 0
        open(counter, "w").write(str(n + 1))
        if n == 0:                             # only the first look: the second attempt finds it holding still
            with open(node_file[m.group(1)], "r+b") as f:
                f.seek(0); f.write(bytes([(b[0] + 1) % 256]))
    print("%s  %s" % (sha(b), m.group(1))); sys.exit(0)
m = re.fullmatch(r"dd if=(\S+) bs=(\d+) skip=(\d+) count=(\d+) 2>/dev/null \| sha256sum", cmd)
if m:
    content=data(m.group(1), int(m.group(3)), int(m.group(4)), int(m.group(2)))
    if mode == "gptchange" and not os.path.exists(counter):
        open(counter,"w").write("changed")
        with open(node_file[m.group(1)],"r+b") as f:
            f.seek(512); value=f.read(1); f.seek(512); f.write(bytes([value[0]^1]))
    print("%s  -" % sha(content)); sys.exit(0)
m = re.fullmatch(r"dd if=(\S+) bs=(\d+) skip=(\d+) count=(\d+) 2>/dev/null", cmd)
if m:
    b=data(m.group(1), int(m.group(3)), int(m.group(4)), int(m.group(2)))
    if mode == "overflow": b += b"excess"
    sys.stdout.buffer.write(b)
    if mode == "never" and "/parts/" in node_file[m.group(1)]:
        with open(node_file[m.group(1)], "r+b") as f:
            f.seek(0); f.write(bytes([(b[0]+1)%256]))
    sys.exit(0)
m = re.fullmatch(r"dd if=(\S+) bs=1048576 2>/dev/null", cmd)
if m:
    sys.stdout.buffer.write(data(m.group(1)))
    if mode == "never":                         # one that never holds still: every look is different
        with open(node_file[m.group(1)], "r+b") as f:
            f.seek(0); f.write(os.urandom(1))
    sys.exit(0)
sys.stderr.write("stand-in adb: no such command: %s\n" % cmd); sys.exit(3)
'''


def check(label, ok, detail=""):
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        failures.append(label)
        if detail:
            print("     " + str(detail).strip().replace("\n", "\n     ")[:500])


def gpt(block=4096, count=256, gap=0):
    blocks=256;data=bytearray(block*blocks);data[510:512]=b"\x55\xaa"
    entries=bytes(count*128);sectors=(len(entries)+block-1)//block
    first=2+gap;last=blocks-1-sectors-gap
    assert first+sectors<=last
    data[first*block:first*block+len(entries)]=entries
    data[last*block:last*block+len(entries)]=entries
    for here,other,array in ((1,blocks-1,first),(blocks-1,1,last)):
        h=bytearray(block)
        struct.pack_into("<8sIIIIQQQQ16sQIII",h,0,b"EFI PART",0x10000,92,0,0,here,other,
                         first+sectors,last-1,b"D"*16,array,count,128,zlib.crc32(entries))
        struct.pack_into("<I",h,16,zlib.crc32(h[:92]))
        data[here*block:(here+1)*block]=h
    return data


def phone(root, parts, block=4096, gap=0):
    os.makedirs(os.path.join(root, "parts"))
    for name, content in parts.items():
        open(os.path.join(root, "parts", name), "wb").write(content)
    open(os.path.join(root, "disk_sda"), "wb").write(gpt(block,gap=gap))
    open(os.path.join(root,"sector_size"),"w").write(str(block))
    open(os.path.join(root, "fake_adb.py"), "w").write(FAKE)


def run(root, out, *flags, mode=""):
    env = dict(os.environ, FAKE_PHONE=root, FAKE_MODE=mode)
    return subprocess.run([sys.executable, TOOL, out, "--adb", sys.executable, "--adb-arg", os.path.join(root, "fake_adb.py")] + list(flags),
                          capture_output=True, text=True, env=env)


work = tempfile.mkdtemp(prefix="backup-test-")
try:
    boot = os.urandom(3000)
    persist = os.urandom(70000)
    parts = {"boot_a": boot, "persist": persist, "userdata": os.urandom(200000), "vbmeta_a": b"AVB0" + os.urandom(100)}
    root = os.path.join(work, "phone")
    phone(root, parts)

    out = os.path.join(work, "out-dry")
    r = run(root, out)
    check("dry run: says what it would read, copies nothing", r.returncode == 0 and not os.path.exists(out) and "boot_a" in r.stdout and "nothing was copied" in r.stdout, r.stdout + r.stderr)
    check("dry run: the partitions the phone lacks are said, not invented", "boot_b" in r.stdout and "the phone does not have it" in r.stdout, r.stdout)
    check("dry run: userdata is never in the plan", "userdata" not in r.stdout, r.stdout)

    out = os.path.join(work, "out")
    r = run(root, out, "--yes")
    check("a real run succeeds", r.returncode == 0, r.stdout + r.stderr)
    same = all(open(os.path.join(out, n + ".img"), "rb").read() == parts[n] for n in ("boot_a", "persist", "vbmeta_a"))
    check("the copies are the bytes of the partitions", same)
    check("userdata was not copied", not os.path.exists(os.path.join(out, "userdata.img")))
    manifest = json.load(open(os.path.join(out, "manifest.json")))
    by = {p["name"]: p for p in manifest["partitions"]}
    check("the manifest has each hash", by["boot_a"]["sha256"] == hashlib.sha256(boot).hexdigest() and by["persist"]["bytes"] == len(persist), by)
    disk = open(os.path.join(root, "disk_sda"), "rb").read()
    heads = [t for t in manifest["tables"] if t["part"] == "head"]
    check("complete GPT arrays exceed the old eight-block capture",
          len(manifest["tables"]) == 2 and heads and open(os.path.join(out, "gpt-sda-head.bin"), "rb").read() == disk[:10 * 4096]
          and open(os.path.join(out, "gpt-sda-tail.bin"), "rb").read() == disk[-9 * 4096:], manifest["tables"])
    restore = open(os.path.join(out, "restore.txt")).read()
    check("restore.txt names the fastboot lines and is only text", "fastboot flash persist persist.img" in restore and restore.startswith("# What would put these back"), restore)
    log = open(os.path.join(root, "commands.log")).read()
    check("the stand-in saw only reads: no of=, no write, no flash", "of=" not in log and "write" not in log and "flash" not in log and ">" not in log.replace("2>/dev/null", ""), log[:400])

    # a partition that changes while it is read is read again and kept once it holds still
    shutil.rmtree(root); phone(root, parts)
    out = os.path.join(work, "out-change")
    r = run(root, out, "--yes", "--only", "boot_a", mode="change")
    check("a partition that changes under the copy is read again", r.returncode == 0 and "the hashes differ" in r.stdout, r.stdout + r.stderr)
    check("... and what was kept matches the partition as it is now", open(os.path.join(out, "boot_a.img"), "rb").read() == open(os.path.join(root, "parts", "boot_a"), "rb").read())

    # one that never holds still is not kept
    shutil.rmtree(root); phone(root, parts)
    out = os.path.join(work, "out-never")
    r = run(root, out, "--yes", "--only", "boot_a", mode="never")
    check("a partition that never holds still stops the tool, with the reason", r.returncode == 1 and "could not be proven" in r.stderr, r.stdout + r.stderr)
    check("... and its copy is not left behind", not os.path.exists(os.path.join(out, "boot_a.img")))

    # no root: stopped before a byte is copied
    shutil.rmtree(root); phone(root, parts)
    out = os.path.join(work, "out-noroot")
    r = run(root, out, "--yes", mode="noroot")
    check("a phone without root stops the tool before anything is copied", r.returncode == 1 and "STOPPED" in r.stderr and not os.path.exists(out), r.stdout + r.stderr)
    # Both real logical-sector sizes, including separated arrays/headers.
    for block in (512,4096):
        for gap in (0,3):
            shutil.rmtree(root);phone(root,parts,block=block,gap=gap)
            out=os.path.join(work,"geometry-%d-%d"%(block,gap))
            r=run(root,out,"--yes","--only","boot_a","--require","boot_a","--with-gpt")
            check("validated GPT copy geometry %d gap %d"%(block,gap),r.returncode==0,r.stdout+r.stderr)
            if r.returncode==0:
                m=json.load(open(os.path.join(out,"manifest.json")));whole=open(os.path.join(root,"disk_sda"),"rb").read()
                check("all actual GPT ranges exactly preserved %d/%d"%(block,gap),all(
                    open(os.path.join(out,t["file"]),"rb").read()==whole[t["skip_blocks"]*block:(t["skip_blocks"]+t["blocks"])*block]
                    for t in m["tables"]) and m["gpt_geometry"][0]["block_size"]==block)
                check("private output modes %d/%d"%(block,gap),os.stat(out).st_mode&0o777==0o700 and all(os.stat(os.path.join(out,f)).st_mode&0o777==0o600 for f in os.listdir(out)))
    shutil.rmtree(root);phone(root,parts)
    for flags in (("--only","boot_a","--require","boot_a,boot_b"),("--only","persist","--require","boot_a")):
        out=os.path.join(work,"required-missing")
        r=run(root,out,"--yes",*flags)
        check("required missing/excluded stops before copying",r.returncode==1 and not os.path.exists(out),r.stdout+r.stderr)
    # Corrupt metadata must be rejected during planning, before output exists.
    def reseal(data,offset,block):
        struct.pack_into("<I",data,offset+16,0)
        struct.pack_into("<I",data,offset+16,zlib.crc32(data[offset:offset+92]))
    for kind in ("signature","header-crc","array-crc","backup-array","overflow-lba","huge-count","zero-entry-size","array-in-data","backup-guid","truncated"):
        shutil.rmtree(root);phone(root,parts);data=gpt();block=4096;head=block;tail=255*block
        if kind=="signature":data[head]=0
        elif kind=="header-crc":data[head+16]^=1
        elif kind=="array-crc":data[2*block]^=1
        elif kind=="backup-array":data[247*block]^=1
        elif kind=="overflow-lba":struct.pack_into("<Q",data,head+72,2**64-1);reseal(data,head,block)
        elif kind=="huge-count":struct.pack_into("<I",data,head+80,2**32-1);reseal(data,head,block)
        elif kind=="zero-entry-size":struct.pack_into("<I",data,head+84,0);reseal(data,head,block)
        elif kind=="array-in-data":struct.pack_into("<Q",data,head+72,10);reseal(data,head,block)
        elif kind=="backup-guid":data[tail+56]^=1;reseal(data,tail,block)
        elif kind=="truncated":data=data[:-1]
        open(os.path.join(root,"disk_sda"),"wb").write(data)
        out=os.path.join(work,"bad-"+kind);r=run(root,out,"--yes")
        check("invalid GPT %s refuses before copies"%kind,r.returncode==1 and not os.path.exists(out),r.stdout+r.stderr)
    shutil.rmtree(root);phone(root,parts)
    out=os.path.join(work,"too-much-output");r=run(root,out,"--yes","--only","boot_a",mode="overflow")
    check("oversized stream stops and leaves no image",r.returncode==1 and not os.path.exists(os.path.join(out,"boot_a.img")) and not os.path.exists(os.path.join(out,"boot_a.img.partial")),r.stdout+r.stderr)
    shutil.rmtree(root);phone(root,parts)
    out=os.path.join(work,"gpt-changed-after-plan");r=run(root,out,"--yes","--only","boot_a","--with-gpt",mode="gptchange")
    check("GPT change after validation discards the changed range",r.returncode==1 and "GPT changed after validation" in r.stderr and not os.path.exists(os.path.join(out,"gpt-sda-head.bin")),r.stdout+r.stderr)
    shutil.rmtree(root);phone(root,parts);open(os.path.join(root,"sector_size"),"w").write("1024")
    out=os.path.join(work,"bad-sector-size");r=run(root,out,"--yes")
    check("unsupported sector size refuses before copying",r.returncode==1 and not os.path.exists(out),r.stdout+r.stderr)
    shutil.rmtree(root);phone(root,parts)
    out=os.path.join(work,"existing");os.mkdir(out);open(os.path.join(out,"boot_a.img"),"wb").write(b"keep")
    r=run(root,out,"--yes","--only","boot_a")
    check("existing backup is never overwritten",r.returncode==1 and open(os.path.join(out,"boot_a.img"),"rb").read()==b"keep")
finally:
    shutil.rmtree(work, ignore_errors=True)

if failures:
    sys.exit("backup-partitions-test: %d FAILED" % len(failures))
print("backup-partitions-test: all passed")
