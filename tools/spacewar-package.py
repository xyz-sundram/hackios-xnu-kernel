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
    parser.add_argument('--loader', type=Path, default=ROOT / 'xnuloader/loader.efi')
    parser.add_argument('--uefi-mode', choices=['DEBUG', 'RELEASE'], default='DEBUG')
    parser.add_argument('--copy-system', action='store_true')
    parser.add_argument('--scale', type=int, choices=[2, 3], default=3, help='Spacewar render scale; scale 2 repairs the saved MobileGestalt scale before launchd')
    parser.add_argument('--sdk', type=Path, default=Path.home() / 'theos/sdks/iPhoneOS16.5.sdk')
    parser.add_argument('--unpaced', action='store_true', help='avoid the extra framebuffer presentation beat')
    parser.add_argument('--fb-threads', type=int, choices=range(1, 9), help='framebuffer copy workers (1 to 8)')
    parser.add_argument('--ca-120', action='store_true', help='tell CoreAnimation 120 Hz; the panel clock remains at its selected fb_hz')
    parser.add_argument('--fb-scan-sync', action='store_true', help='try guarded Spacewar scanline synchronization')
    parser.add_argument('--native-scanout', action='store_true', help='try ownership-qualified Spacewar buffer flips')
    parser.add_argument('--panel-120', action='store_true', help='experimental measured panel 120 Hz switch; requires native scanout')
    parser.add_argument('--xr', action='store_true', help='828x1792 iPhone XR canvas at 2x scale')
    parser.add_argument('--compact-gestures', action='store_true', help='750x1624 at 2x with virtual-home gesture navigation')
    args = parser.parse_args()
    if args.compact_gestures:
        args.scale = 2
    if args.compact_gestures and args.xr:
        parser.error('choose either --xr or --compact-gestures')
    if args.xr:
        args.scale = 2
    if args.panel_120 and not args.native_scanout:
        parser.error('--panel-120 requires --native-scanout')
    out = args.out.resolve()
    required = [ROOT / 'hakios-kernel', args.loader.resolve(), ROOT / 'userland/root.apfs',
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
               'thermal_tm0=0xc263000 thermal_tm1=0xc265000 thermal_log=5 '
               f'gpu=1 adreno_speed={speed} touchscreen=1 touch_spacewar=1 touch_steps=6 '
               f'touch_seconds=0 touch_live=1 touch_lat=0 hakios_debug=0 cpu_probe=1 cpufreq=2 fbscale={args.scale}'
               + (' fb_beat=0' if args.unpaced else '')
               + (f' fb_threads={args.fb_threads}' if args.fb_threads else '')
               + (' fb_ca_hz=240' if args.panel_120 else ' fb_ca_hz=120' if args.ca_120 else '')
               + (' fb_scan_sync=1' if args.fb_scan_sync else '')
               + (' fb_native=1' if args.native_scanout else '')
               + (' fb_panel_hz=120' if args.panel_120 else '')
               + (' fbwidth=828 fbheight=1792 fbsubtype=1792' if args.xr else '')
               + (' fbwidth=750 fbheight=1624 fbsubtype=1792 fb_gestures=1' if args.compact_gestures else ''))
    # Default thermal polling/stop values are omitted to leave room for display controls.
    if args.compact_gestures:
        command = command.replace(' socinfo=1 soc_smem=0x80900000 soc_smem_size=0x200000', '')
    payload = out / ('spacewar-userland-gpu-touch-quiet-payload.bin' if args.gpu_mhz == 315 else
                     f'spacewar-userland-gpu{args.gpu_mhz}-touch-quiet-payload.bin')
    def run(*arguments):
        argv = [str(x) for x in arguments]
        if Path(argv[0]).suffix == '.py':
            argv.insert(0, sys.executable)
        subprocess.run(argv, cwd=ROOT, check=True)
    ramdisk = required[2]
    if args.scale in (2, 3):
        scale_root = out / f'scale{args.scale}-root'
        shutil.copytree(ROOT / 'userland/root', scale_root, symlinks=True)
        helper = scale_root / 'bin/display_info'
        helper.parent.mkdir(parents=True, exist_ok=True)
        run(ROOT / 'xnu-deps/host/bin/xnu-clang', '-arch', 'arm64', '-isysroot', args.sdk.resolve(),
            '-O1', '-Wall', '-Wextra', '-Werror', '-framework', 'CoreFoundation', '-framework', 'IOKit',
            '-lMobileGestalt', '-o', helper, ROOT / 'userland/src/display_info.c')
        if args.compact_gestures:
            wallpaper = ROOT / 'sysimage/rootfs/System/Library/PrivateFrameworks/Settings/DisplayAndBrightnessSettings.framework/ContinuousExposeLayout-Light-LTR.ca/assets/SydneyWallpaper.jpg'
            if not wallpaper.is_file():
                parser.error('compact wallpaper requires the stock SydneyWallpaper.jpg from the local IPSW')
            (scale_root / 'share').mkdir(exist_ok=True)
            shutil.copy2(wallpaper, scale_root / 'share/wallpaper-default.jpg')
            run(ROOT / 'xnu-deps/host/bin/xnu-clang', '-arch', 'arm64', '-isysroot', args.sdk.resolve(), '-O1',
                '-dynamiclib', '-install_name', '/redwood/lib/libhakios.sb.dylib', '-framework', 'CoreFoundation',
                '-framework', 'CoreGraphics', '-framework', 'IOKit', '-framework', 'IOSurface', '-lobjc',
                '-o', scale_root / 'lib/libhakios.sb.dylib', ROOT / 'userland/src/inject_sb.c')
        rc_path = scale_root / 'etc/rc'
        rc = rc_path.read_text()
        marker = 'exec /sbin/launchd.darwin'
        if marker not in rc:
            parser.error('scale-2 root has no launchd handoff to guard')
        rc = '/redwood/bin/display_info --refresh-scale-cache\n' + rc.replace(
            marker, '/redwood/bin/display_info --verify-scale &\n' + marker, 1)
        rc_path.write_text(rc)
        ramdisk = out / f'spacewar-root-scale{args.scale}.apfs'
        run(ROOT / 'tools/apfs/mkapfs.py', '-z', '--plain', 'fw', '-o', ramdisk,
            f'redwood={scale_root}')
    run(ROOT / 'xnuloader/mkpayload.py', '-o', payload, f'kernel={required[0]}', f'ramdisk={ramdisk}', '--cmdline', command)
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
    (out / 'MANIFEST.json').write_text(json.dumps({'gpu_mhz': args.gpu_mhz, 'scale': args.scale, 'cmdline': command, 'files': inventory}, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(f"{p['sha256']}  {p['path']}\n" for p in inventory))
    print(f'Phone (1) files ready in {out}; add Google Windows platform-tools when using PowerShell.')


if __name__ == '__main__':
    main()
