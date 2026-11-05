#!/usr/bin/env python3
"""Package locally built Phone (1) kernel/root/loader; no phone commands or flashing."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'build/spacewar')
    parser.add_argument('--gpu-mhz', type=int, choices=[315, 450, 550], default=550)
    parser.add_argument('--dtb', type=Path, default=ROOT / 'spacewar.dtb')
    parser.add_argument('--uefi-mode', choices=['DEBUG', 'RELEASE'], default='DEBUG')
    parser.add_argument('--copy-system', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    required = [ROOT / 'hakios-kernel', ROOT / 'xnuloader/loader.efi', ROOT / 'userland/root.apfs',
                ROOT / 'userland/system.apfs', args.dtb.resolve(),
                ROOT / f'Mu-Silicium/Build/spacewarPkg/{args.uefi_mode}_CLANGPDB/FV/SILICIUM_UEFI.fd']
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        parser.error('build/provide these inputs first: ' + ', '.join(missing))
    header = ROOT / 'xnu-src/xnu-8796.121.2/bsd/dev/hakios.drivers.h'
    expected = 'virtio_sound socinfo adreno virtio_input goodix buttons virtio_net cpufreq thermal virtio_blk'
    if not header.is_file() or expected not in header.read_text() or '#define HAKIOS_DEVICE_SPACEWAR  1' not in header.read_text():
        parser.error('build the exact ten-driver Spacewar performance configuration first')
    if expected.encode() + b'\0' not in required[0].read_bytes():
        parser.error('hakios-kernel does not contain the ten-driver profile; run make kernel after selecting the configuration')
    out.mkdir(parents=True, exist_ok=True)
    speed = {315: 1, 450: 2, 550: 3}[args.gpu_mhz]
    command = (f'loader_return_after=0 epoch={int(time.time())} sb_delay=8 bootcpu=7 cpus=7 buttons=1 '
               'socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000 thermal=1 '
               'thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_poll=2000 thermal_log=5 thermal_stop=105 '
               f'gpu=1 adreno_speed={speed} touchscreen=1 touch_spacewar=1 touch_steps=6 '
               'touch_seconds=0 touch_live=1 touch_lat=0 hakios_debug=0 cpu_probe=1 cpufreq=2')
    payload = out / ('spacewar-userland-gpu-touch-quiet-payload.bin' if args.gpu_mhz == 315 else
                     f'spacewar-userland-gpu{args.gpu_mhz}-touch-quiet-payload.bin')
    def run(*arguments):
        subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT, check=True)
    run(ROOT / 'xnuloader/mkpayload.py', '-o', payload, f'kernel={required[0]}', f'ramdisk={required[2]}', '--cmdline', command)
    if payload.stat().st_size > 63 * 1024 * 1024:
        parser.error('kernel/root payload exceeds the loader limit')
    usb = out / 'loader-usb.bin'
    run(ROOT / 'xnuloader/mkpayload.py', '-o', usb, f'loader.efi={required[1]}', '--usb')
    image = out / 'xnu-spacewar-gpu-clocks.img'
    run(ROOT / 'mkxnuimg.py', '-d', 'spacewar', '--mode', args.uefi_mode, '--header-version', '3',
        '--dtb', args.dtb.resolve(), '-p', usb, '-o', image)
    run(ROOT / 'tools/inspect-boot-image.py', image, '--fd', required[-1], '--dtb', args.dtb.resolve())
    for name in ['spacewar-userland-gpu.ps1', 'spacewar-userland-touch.ps1']:
        shutil.copy2(ROOT / 'tools' / name, out / name)
    if args.copy_system and required[3].resolve() != (out / 'system.apfs').resolve():
        shutil.copy2(required[3], out / 'system.apfs')
    (out / 'cmdline.txt').write_text(command + '\n')
    files = [p for p in out.iterdir() if p.is_file() and p.name not in ['MANIFEST.json', 'SHA256SUMS']]
    inventory = []
    for path in sorted(files):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        inventory.append({'path': path.name, 'bytes': path.stat().st_size, 'sha256': digest.hexdigest()})
    (out / 'MANIFEST.json').write_text(json.dumps({'gpu_mhz': args.gpu_mhz, 'cmdline': command, 'files': inventory}, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(f"{p['sha256']}  {p['path']}\n" for p in inventory))
    print(f'Phone (1) files ready in {out}; add Google Windows platform-tools when using PowerShell.')


if __name__ == '__main__':
    main()
