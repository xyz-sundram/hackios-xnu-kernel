#!/usr/bin/env python3
"""Execute actual optional CA link setup with mocked dependency builder/linker."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'userland/build.sh').read_text()
start = source.index('\t\t\tM2V_OBJS=')
end = source.index('\n\t\telse', start)
block = source[start:end]
checks = 0
for state in ['fresh', 'cached', 'frontend-only', 'builder-incomplete', 'builder-fails', 'disabled']:
    with tempfile.TemporaryDirectory(prefix='spacewar-m2v-order-') as temp:
        base = Path(temp)
        work = base / 'userland'
        work.mkdir()
        (base / 'tools').mkdir()
        out = base / 'xnu-deps/build/m2v/out'
        out.mkdir(parents=True)
        if state in ['cached', 'frontend-only']:
            (out / 'frontend.o').touch()
        if state == 'cached':
            (out / 'libadreno_metal_bridge.a').touch()
        builder = base / 'tools/mk-m2v.sh'
        builder.write_text('''#!/bin/sh
set -e
echo build >> ../trace
[ "$TEST_STATE" != builder-fails ] || exit 23
mkdir -p ../xnu-deps/build/m2v/out
touch ../xnu-deps/build/m2v/out/frontend.o
[ "$TEST_STATE" = builder-incomplete ] || touch ../xnu-deps/build/m2v/out/libadreno_metal_bridge.a
''')
        linker = base / 'linker'
        linker.write_text('''#!/bin/sh
set -e
if [ "$TEST_STATE" != disabled ]; then
 test -f ../xnu-deps/build/m2v/out/frontend.o
 test -f ../xnu-deps/build/m2v/out/libadreno_metal_bridge.a
fi
echo link >> ../trace
printf '%s\\n' "$@" > ../link-arguments
''')
        builder.chmod(0o755)
        linker.chmod(0o755)
        env = {**os.environ, 'TEST_STATE': state, 'DYN': str(linker), 'HM': '/unused',
               'METAL_GPU': '', 'TURNIP': '1', 'METAL_PLUGIN': 'ca', 'M2V': '' if state == 'disabled' else '1'}
        result = subprocess.run(['bash', '-e', '-c', block], cwd=work, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        trace = (base / 'trace').read_text().splitlines()
        if state in ['builder-incomplete', 'builder-fails']:
            assert result.returncode != 0 and trace == ['build'], (state, trace, result.stderr)
            assert not (base / 'link-arguments').exists()
        else:
            assert result.returncode == 0, (state, result.stderr)
            assert trace == (['link'] if state in ['cached', 'disabled'] else ['build', 'link'])
            args = (base / 'link-arguments').read_text()
            assert ('libadreno_metal_bridge.a' in args) == (state != 'disabled')
        checks += 1
print(f'PASS: {checks} actual optional M2V build/link-order cases')
