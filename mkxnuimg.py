#!/usr/bin/env python3
"""
Pack a device's UEFI (with the hook that starts the XNU loader) and an optional payload
into an Android boot image for `fastboot boot`.

  kernel = gzip(BootShim + UEFI FD + ["XNUP" container]) + DTB

The boot shim copies the FD to its base and the payload to PAYLOAD_BASE.

The UEFI pieces are taken from a Mu-Silicium tree built here (Mu-Silicium/, see uefi/README.md)
or, when there is none, from devices/<device>/prebuilt/.

usage: mkxnuimg.py [-d DEVICE] [-p payload.bin] [-o out.img]
"""
import argparse, gzip, os, shutil, struct, subprocess, sys, tempfile, tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
MU = os.path.join(HERE, 'Mu-Silicium')
DEFAULT_DEVICE = os.environ.get('DEVICE', 'redwood')
FD_BASE, FD_SIZE = 0x9FD00000, 0x200000
PAYLOAD_BASE, PAYLOAD_MAX = 0xF0000000, 0x04000000

ap = argparse.ArgumentParser()
ap.add_argument('-d', '--device', default=DEFAULT_DEVICE)
ap.add_argument('-p', '--payload')
ap.add_argument('-o', '--output')
ap.add_argument('--mode', choices=('RELEASE', 'DEBUG'), default='RELEASE')
ap.add_argument('--dtb', help='append this DTB instead of the firmware tree/device prebuilt DTB')
ap.add_argument('--shim-output', help='also save the payload-aware shim for device prebuilts')
ap.add_argument('--header-version', type=int, choices=(0, 1, 3),
                help='override the device config Resources/Configs/DEVICE.toml boot_image.header_version')
args = ap.parse_args()

PREBUILT = os.path.join(HERE, 'devices', args.device, 'prebuilt')
output = args.output or os.path.join(HERE, f'xnu-{args.device}.img')
config_path = os.path.join(MU, 'Resources', 'Configs', f'{args.device}.toml')
if not os.path.isfile(config_path):
    config_path = os.path.join(PREBUILT, 'boot-config.toml')
config = {}
if os.path.isfile(config_path):
    with open(config_path, 'rb') as f:
        config = tomllib.load(f)
FD_BASE = config.get('uefi_fd', {}).get('base', FD_BASE)
FD_SIZE = config.get('uefi_fd', {}).get('size', FD_SIZE)
kernel_header = config.get('boot_shim', {}).get('requires_kernel_header', True)
if args.header_version is not None:
    header_version = args.header_version
else:
    header_version = config.get('boot_image', {}).get('header_version', 1)
if header_version not in (0, 1, 3):
    sys.exit(f'mkxnuimg.py: header v{header_version} is unsupported by this appended-DTB packer; use v0, v1 or v3')
fd_path = os.path.join(MU, f'Build/{args.device}Pkg/{args.mode}_CLANGPDB/FV/SILICIUM_UEFI.fd')
if os.path.isfile(fd_path):
    parts = {'shim': os.path.join(MU, 'BootShim/BootShim.bin'), 'fd': fd_path,
             'dtb': os.path.join(MU, f'Resources/DTBs/{args.device}.dtb'), 'ramdisk': os.path.join(MU, 'Resources/ramdisk'),
             'mkbootimg': os.path.join(MU, 'Resources/Scripts/mkbootimg.py')}
else:
    if args.mode != 'RELEASE':
        sys.exit(f'mkxnuimg.py: {fd_path} not found; build {args.mode} firmware first')
    parts = {'shim': os.path.join(PREBUILT, 'BootShim.bin'), 'fd': os.path.join(PREBUILT, 'SILICIUM_UEFI.fd'),
             'dtb': os.path.join(PREBUILT, 'device-tree.dtb'), 'ramdisk': os.path.join(PREBUILT, 'ramdisk'),
             'mkbootimg': os.path.join(PREBUILT, 'mkbootimg.py')}
if args.dtb:
    parts['dtb'] = args.dtb
# Build in isolation: Mu's regular build makes a stock shim in this shared directory,
# which has the Linux header but does not copy XNUP. Never reuse it for a payload.
shim_source = os.path.join(MU, 'BootShim/BootShim.S')
shim = None
if os.path.isfile(shim_source):
    with tempfile.TemporaryDirectory(prefix='hakios-shim-') as shim_dir:
        for name in ('BootShim.S', 'Makefile'):
            shutil.copyfile(os.path.join(MU, 'BootShim', name), os.path.join(shim_dir, name))
        subprocess.check_call(['make', f'REQUIRES_KERNEL_HEADER={int(kernel_header)}',
                               f'FD_BASE={FD_BASE:#x}', f'FD_SIZE={FD_SIZE:#x}',
                               f'CFLAGS=--target=aarch64-linux-gnu -DPAYLOAD_BASE={PAYLOAD_BASE:#x}'],
                              cwd=shim_dir, stdout=subprocess.DEVNULL)
        shim = open(os.path.join(shim_dir, 'BootShim.bin'), 'rb').read()
for name, path in parts.items():
    if name == 'shim' and shim is not None:
        continue
    if not os.path.isfile(path):
        hint = ''
        if name == 'fd':
            hint = ("\n  The UEFI image is not in the repository: it carries the phone vendor's own drivers, which are not\n"
                    "  redistributed here. Build it from your own phone's firmware: uefi/README.md")
        sys.exit(f'mkxnuimg.py: {path} not found{hint}')
if shim is None:
    shim = open(parts['shim'], 'rb').read()
if kernel_header and shim[0x38:0x3c] != b'ARM\x64':
    sys.exit('mkxnuimg.py: shim is missing the required ARM64 Linux image header')
if kernel_header and struct.unpack_from('<QQ', shim, 8) != (FD_BASE, FD_SIZE):
    sys.exit('mkxnuimg.py: shim FD base/size do not match this device configuration')
# HakiOS's shim compares w2 against XNUP with these two AArch64 instructions.
# Stock Mu shims lack this loop, even when they have a valid Linux header.
if (args.payload or args.shim_output) and struct.pack('<II', 0x5289cb03, 0x72aa0aa3) not in shim:
    sys.exit('mkxnuimg.py: shim has no XNUP copy loop; rebuild the HakiOS shim before packing a payload')
fd = open(parts['fd'], 'rb').read()
if len(fd) != FD_SIZE:
    sys.exit(f'mkxnuimg.py: FD size {len(fd):#x} does not match configured {FD_SIZE:#x}')

blob = shim + fd
if args.payload:
    data = open(args.payload, 'rb').read()
    # the payload file is a complete "XNUP" container (see xnuloader/mkpayload.py)
    if (len(data) < 8 or data[:4] != b'XNUP' or len(data) > PAYLOAD_MAX - 0x100000 or
            struct.unpack_from('<I', data, 4)[0] != len(data) - 8):
        sys.exit('mkxnuimg.py: bad, truncated or oversized XNUP payload (maximum 63 MiB)')
    blob += data
    blob += b'\0' * (-len(blob) % 16)
    print(f'payload: {len(data)} bytes, bytesum={sum(data) & 0xffffffff:08x}')

dtb = open(parts['dtb'], 'rb').read()
if len(dtb) < 40 or dtb[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', dtb, 4)[0] != len(dtb):
    sys.exit(f"mkxnuimg.py: {parts['dtb']} is not one complete DTB")
kernel = output + '.kernel~'
open(kernel, 'wb').write(gzip.compress(blob, 6) + dtb)
try:
    subprocess.check_call([sys.executable, parts['mkbootimg'], '--kernel', kernel, '--ramdisk', parts['ramdisk'],
                           '--header_version', str(header_version), '-o', output])
finally:
    os.unlink(kernel)
if args.shim_output:
    open(args.shim_output, 'wb').write(shim)
print(f'device: {args.device}, mode: {args.mode}, header: v{header_version}, DTB: {parts["dtb"]}')
print(f'{output}: {os.path.getsize(output)} bytes (shim {len(shim)}, uncompressed {len(blob)})')
