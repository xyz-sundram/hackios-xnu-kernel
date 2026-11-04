#!/usr/bin/env python3
"""Extend a Phone (1) injection list to visible stock apps using their own Info.plists.

Reads an injection list on stdin, emits it on stdout. Existing non-UI hooks stay
in place. Preferences uses libhakios.sb, which also opens the tweak loader.
"""
import argparse
from pathlib import Path
import plistlib
import re
import sys


def extend(text, app_roots):
    rows = [line.split() for line in text.splitlines() if line.strip()]
    names = {'Preferences'}
    for app_root in app_roots:
        for info in Path(app_root).glob('*.app/Info.plist'):
            try:
                data = plistlib.loads(info.read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            name = data.get('CFBundleExecutable', '')
            if (data.get('CFBundlePackageType') == 'APPL' and
                    'hidden' not in data.get('SBAppTags', []) and
                    not data.get('LSUIElement') and isinstance(name, str) and
                    re.fullmatch(r'[A-Za-z0-9_.+-]+', name)):
                names.add(name)
    used = {name for row in rows for name in row[1:] if name != 'Preferences'}
    extra = sorted(names - used)
    target = next((row for row in rows if row[0] == '/redwood/lib/libhakios.sb.dylib'), None)
    if target is None:
        target = ['/redwood/lib/libhakios.sb.dylib']
        rows.append(target)
    for row in rows:
        if row is not target:
            row[1:] = [n for n in row[1:] if n != 'Preferences']
    target.extend(name for name in extra if name not in target[1:])
    return '\n'.join(' '.join(row) for row in rows if len(row) > 1) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app_roots', nargs='+', type=Path)
    args = parser.parse_args()
    sys.stdout.write(extend(sys.stdin.read(), args.app_roots))


if __name__ == '__main__':
    main()
