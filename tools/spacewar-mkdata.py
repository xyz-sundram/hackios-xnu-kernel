#!/usr/bin/env python3
"""Create a standalone Phone (1) HFSX data image on the host; never opens a device.

Unlike the historical mkdatavol.py, this image has no partition prefix and no
fixed 20 GiB layout. The reviewed install plan supplies its exact byte size.
"""
import argparse
import os
from pathlib import Path
import struct
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('output', type=Path)
p.add_argument('--bytes', type=int, required=True)
p.add_argument('--mkfs', default='mkfs.hfsplus')
a = p.parse_args()
if a.bytes < 256 * 1024**2 or a.bytes > 16 * 1024**3 or a.bytes % 4096:
    p.error('size must be 256 MiB..16 GiB in whole 4096-byte blocks')
if a.output.exists() or a.output.is_symlink():
    p.error('output already exists; refusing to empty an existing image')
a.output.parent.mkdir(parents=True, exist_ok=True)
try:
    with a.output.open('xb') as f:
        f.truncate(a.bytes)
    subprocess.run([a.mkfs, '-s', '-J', '-b', '4096', '-U', '0', '-G', '0',
                    '-M', '755', '-v', 'HakiOS Data', str(a.output)], check=True)
    with a.output.open('rb') as f:
        f.seek(1024)
        header = f.read(512)
        f.seek(a.bytes - 1024)
        alternate = f.read(512)
    signature, version, attributes = struct.unpack_from('>HHI', header)
    block_size, blocks = struct.unpack_from('>II', header, 40)
    if signature != 0x4858 or version != 5 or not attributes & (1 << 13) or \
            block_size != 4096 or blocks * block_size != a.bytes or alternate != header:
        raise ValueError('formatter did not produce the exact journaled HFSX geometry')
    os.chmod(a.output, 0o600)
    print(f'{a.output}: {a.bytes} bytes, journaled HFSX, {blocks} blocks of 4096')
except BaseException:
    a.output.unlink(missing_ok=True)
    raise
