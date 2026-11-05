#!/usr/bin/env python3
"""Generate the private M146 touch array from the builder's own verified ROM blob."""
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = '04205403dd3fab89edc9e361e3b9cd8abbdac574e2b9e7cafc9cab310c514726'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('--tree', type=Path, default=ROOT / 'xnu-src/xnu-8796.121.2')
    args = parser.parse_args()
    data = args.firmware.read_bytes()
    if len(data) != 119284 or hashlib.sha256(data).hexdigest() != SHA:
        parser.error('expected the verified M146 V27 image (119284 bytes, SHA256 ' + SHA + ')')
    if not (args.tree / 'bsd/dev').is_dir():
        parser.error('create the kernel tree with xnu/apply.sh first')
    target = args.tree / 'BUILD/hakios.spacewar.focaltech_blob.h'
    target.parent.mkdir(parents=True, exist_ok=True)
    header = ('/* Private generated firmware; keep in ignored BUILD/. */\n'
              '#define SPACEWAR_M146_FW_LENGTH 119284u\n'
              '#define SPACEWAR_M146_FW_ECC 0x22edu\n'
              'static const uint8_t spacewar_m146_fw[SPACEWAR_M146_FW_LENGTH] = {\n')
    header += ''.join('    ' + ','.join('0x%02x' % b for b in data[i:i + 16]) + ',\n'
                      for i in range(0, len(data), 16)) + '};\n'
    if not target.exists() or target.read_text() != header:
        target.write_text(header)
    print(f'{target}: {len(data)} verified firmware bytes; generated privately')


if __name__ == '__main__':
    main()
