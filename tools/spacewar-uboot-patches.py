#!/usr/bin/env python3
"""Apply cumulative U-Boot patches while preserving unrelated local edits.

Reconstruct each patched file from HEAD and accept only the base, a known patch
prefix, or the final result. Later patches may change earlier patch context;
reverse-applying one patch against the final tree is therefore not reliable.
"""
from pathlib import Path
import argparse
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    parser.add_argument('patches', type=Path, nargs='+')
    args = parser.parse_args()
    tree = args.tree.resolve()
    patches = [p.resolve() for p in args.patches]
    names = set()
    for patch in patches:
        names.update(re.findall(r'^diff --git a/(\S+) b/\S+$', patch.read_text(), re.M))
    if any(Path(n).is_absolute() or '..' in Path(n).parts for n in names):
        parser.error('unsafe patch path')
    versions = {}
    with tempfile.TemporaryDirectory(prefix='spacewar-uboot-patches-') as temp:
        expected = Path(temp)
        for name in sorted(names):
            r = subprocess.run(['git', '-C', str(tree), 'show', 'HEAD:' + name], capture_output=True)
            data = r.stdout if r.returncode == 0 else None
            versions[name] = {data}
            if data is not None:
                path = expected / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        for patch in patches:
            subprocess.run(['git', 'apply', str(patch)], cwd=expected, check=True)
            for name in names:
                p = expected / name
                versions[name].add(p.read_bytes() if p.is_file() else None)
        for name in sorted(names):
            actual = tree / name
            # A symlink is a local edit even when its target contains a known
            # revision. Never follow it when replacing source files.
            current = tree
            for part in Path(name).parts:
                current = current / part
                if current.is_symlink():
                    parser.error(f'{current} is a symlink; preserving it')
                if current != actual and current.exists() and not current.is_dir():
                    parser.error(f'{current} is not a directory; preserving it')
            if actual.exists() and not actual.is_file():
                parser.error(f'{actual} is not a regular file; preserving it')
            data = actual.read_bytes() if actual.is_file() else None
            if data not in versions[name]:
                parser.error(f'{actual} has edits outside the known patch sequence; preserving it')
        changed = 0
        for name in sorted(names):
            actual, desired = tree / name, expected / name
            if desired.is_file():
                data = desired.read_bytes()
                if not actual.is_file() or actual.read_bytes() != data:
                    actual.parent.mkdir(parents=True, exist_ok=True)
                    actual.write_bytes(data)
                    changed += 1
            elif actual.exists():
                actual.unlink()
                changed += 1
        print(f'spacewar-uboot-patches: {len(names)} files verified, {changed} updated')


if __name__ == '__main__':
    main()
