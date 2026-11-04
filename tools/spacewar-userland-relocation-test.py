#!/usr/bin/env python3
"""Execute build.sh's real relocation block with sparse libraries, no images/phone."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'userland/build.sh').read_text()
start = source.index('# Programs and libraries of more than 4 MB')
end = source.index('# the image: zlib', start)
block = source[start:end]
cases = [
    ('spacewar', {}, False),
    ('spacewar', {'METAL_PLUGIN': 'ca'}, False),
    ('spacewar', {'TURNIP': '1'}, True),
    ('spacewar', {'M2V': '1'}, True),
    ('spacewar', {'TURNIP': '1', 'M2V': '1'}, True),
    ('redwood', {}, True),
    (None, {}, True),
]
checks = 0
for device, selected, relocated in cases:
    with tempfile.TemporaryDirectory(prefix='spacewar-relocate-') as temp:
        base = Path(temp)
        work = base / 'userland'
        (work / 'root/lib').mkdir(parents=True)
        (work / 'root/bin').mkdir()
        if device:
            (base / '.config').write_text(f'CONFIG_DEVICE="{device}"\n')
        libraries = {'libOSMesa.8.dylib': 14_822_264, 'exact-boundary.dylib': 4 * 1024 * 1024}
        if selected.get('TURNIP') or selected.get('M2V'):
            libraries['libvulkan_freedreno.dylib'] = 11 * 1024 * 1024
        for name, size in libraries.items():
            with (work / 'root/lib' / name).open('wb') as f:
                f.truncate(size)
        (work / 'root/bin/small').write_bytes(b'baseline')
        (work / 'big').mkdir()
        (work / 'big/stale').write_bytes(b'old-build')
        env = {k: v for k, v in os.environ.items() if k not in {'TURNIP', 'M2V', 'METAL_PLUGIN', 'SYSTEM'}}
        env.update(selected)
        subprocess.run(['bash', '-e', '-c', 'SYSTEM=\n' + block + '\nprintf "%s" "$SYSTEM" > system-result'],
                       cwd=work, env=env, check=True, stdout=subprocess.PIPE)
        for name, size in libraries.items():
            local = work / 'root/lib' / name
            big = work / 'big/lib' / name
            moved = relocated and size > 4 * 1024 * 1024
            if moved:
                assert local.is_symlink() and os.readlink(local) == f'/usr/local/redwood/lib/{name}'
                assert big.is_file() and big.stat().st_size == size
            else:
                assert local.is_file() and not local.is_symlink() and local.stat().st_size == size
                assert not big.exists()
            checks += 1
        assert (work / 'root/bin/small').read_bytes() == b'baseline'
        assert not (work / 'big/stale').exists()
        assert (work / 'system-result').read_text() == ('1' if relocated else '')
        checks += 3
print(f'PASS: {checks} actual userland relocation/path/system-rebuild checks across {len(cases)} profiles')
