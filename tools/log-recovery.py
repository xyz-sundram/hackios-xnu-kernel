#!/usr/bin/env python3
"""Validate/decode LR1 recovery frames, offline or from live loader snapshots.

capture only reads loader USB logs; it never boots, stops, flashes or reboots a
phone. It retains a bounded latest snapshot and unique frames in a private host
directory. Overlapping snapshots and paced repeated rounds survive ring wrap.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import zlib

CHUNK = 768
FILE_CAP = 2 * 1024 * 1024
NAME = r'hakios-audio-[A-Za-z0-9]{6}'
BEGIN = re.compile(rf'LR1 BEGIN ([0-9a-f]{{8}}) ({NAME}) ([0-9]+) ([0-9]+) ([0-9a-f]{{8}}) (readonly|replayed)\Z')
DATA = re.compile(r'LR1 DATA ([0-9a-f]{8}) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9a-f]{8}) ([A-Za-z0-9+/=]+)\Z')
END = re.compile(r'LR1 END ([0-9a-f]{8}) ([0-9]+) ([0-9]+) ([0-9a-f]{8})\Z')


class Decoder:
    def __init__(self, name=None):
        if name is not None and re.fullmatch(NAME, name) is None:
            raise ValueError('invalid exact capture name')
        self.name = name
        self.sessions = {}
        self.rejected = 0
        self.errors = []

    def feed(self, text):
        """Return only newly accepted frame lines; retain no arbitrary log text."""
        new = []
        for raw in text.splitlines():
            at = raw.find('LR1 ')
            if at < 0 or len(raw) > 4096:
                continue
            line = raw[at:].strip()
            if line.startswith('LR1 ERROR'):
                if line not in self.errors and len(self.errors) < 16:
                    self.errors.append(line)
                continue
            match = BEGIN.fullmatch(line)
            if match:
                identity, name, size, count, crc, mode = match.groups()
                size, count = int(size), int(count)
                if self.name is not None and name != self.name:
                    continue
                if not 0 < size <= FILE_CAP or count != (size + CHUNK - 1) // CHUNK:
                    self.rejected += 1
                    continue
                info = (name, size, count, int(crc, 16), mode)
                old = self.sessions.get(identity)
                if old is not None:
                    if old['info'] != info:
                        old['conflict'] = True
                        self.rejected += 1
                    continue
                if len(self.sessions) >= 4:
                    self.rejected += 1
                    continue
                self.sessions[identity] = {'info': info, 'chunks': {}, 'end': False, 'conflict': False}
                new.append(line)
                continue
            match = DATA.fullmatch(line)
            if match:
                identity, seq, offset, length, crc, encoded = match.groups()
                state = self.sessions.get(identity)
                if state is None:
                    continue
                seq, offset, length = int(seq), int(offset), int(length)
                _, size, count, _, _ = state['info']
                if seq >= count or offset != seq * CHUNK or length != min(CHUNK, size - offset):
                    self.rejected += 1
                    continue
                try:
                    data = base64.b64decode(encoded, validate=True)
                except ValueError:
                    self.rejected += 1
                    continue
                if len(data) != length or zlib.crc32(data) != int(crc, 16):
                    self.rejected += 1
                    continue
                if seq in state['chunks']:
                    if state['chunks'][seq] != data:
                        state['conflict'] = True
                        self.rejected += 1
                    continue
                state['chunks'][seq] = data
                new.append(line)
                continue
            match = END.fullmatch(line)
            if match:
                identity, size, count, crc = match.groups()
                state = self.sessions.get(identity)
                if state is None:
                    continue
                if (int(size), int(count), int(crc, 16)) != state['info'][1:4]:
                    self.rejected += 1
                    continue
                if not state['end']:
                    state['end'] = True
                    new.append(line)
                continue
            if line.startswith(('LR1 BEGIN ', 'LR1 DATA ', 'LR1 END ')):
                self.rejected += 1
        return new

    def completed(self):
        result = []
        for identity, state in self.sessions.items():
            name, size, count, crc, mode = state['info']
            if state['conflict'] or not state['end'] or len(state['chunks']) != count:
                continue
            data = b''.join(state['chunks'][i] for i in range(count))
            calculated_id = zlib.crc32(data, zlib.crc32(name.encode()))
            if len(data) != size or zlib.crc32(data) != crc or calculated_id != int(identity, 16):
                continue
            result.append((identity, name, mode, data))
        return result


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def finish(decoder, directory):
    completed = decoder.completed()
    if not completed:
        return False
    manifest = {'format': 'hakios-log-recovery-v1', 'rejected_frames': decoder.rejected,
                'exporter_messages': decoder.errors, 'files': []}
    names = set()
    for identity, name, mode, data in completed:
        if name in names:
            raise ValueError('multiple complete versions of one capture; decode snapshots separately')
        names.add(name)
        private_write(directory / name, data)
        manifest['files'].append({'name': name, 'frame_id': identity, 'bytes': len(data),
                                  'crc32': f'{zlib.crc32(data):08x}', 'sha256': hashlib.sha256(data).hexdigest(),
                                  'mount_mode': mode})
    private_write(directory / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    print(f'Validated {len(completed)} private capture(s); {decoder.rejected} corrupt/malformed frame(s) rejected.')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    offline = sub.add_parser('decode', help='decode existing private loader logs or unique frame files')
    offline.add_argument('logs', nargs='+', type=Path)
    live = sub.add_parser('capture', help='read incremental logs from an already booted recovery loader')
    live.add_argument('--fastboot', default='fastboot')
    live.add_argument('--serial', help='optional transport serial; never printed')
    live.add_argument('--seconds', type=int, default=360)
    live.add_argument('--poll', type=float, default=0.5)
    for part in (offline, live):
        part.add_argument('--out', required=True, type=Path, help='new private output directory')
        part.add_argument('--name', help='optional exact audio_health basename')
    args = parser.parse_args()
    decoder = Decoder(args.name)
    if args.out.exists():
        parser.error('--out must be a new private directory')
    if args.command == 'capture' and (not 1 <= args.seconds <= 1800 or not 0.1 <= args.poll <= 5):
        parser.error('capture duration must be 1..1800 s and poll 0.1..5 s')
    args.out.mkdir(parents=True, mode=0o700)
    os.chmod(args.out, 0o700)
    if args.command == 'decode':
        for path in args.logs:
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('each input must be a local log no larger than 8 MiB')
            decoder.feed(path.read_text(errors='replace'))
        if finish(decoder, args.out):
            return 0
        raise ValueError('no complete capture passed size, sequence and CRC validation')
    fastboot = [args.fastboot] + (['-s', args.serial] if args.serial else [])
    def command(*items):
        return subprocess.run(fastboot + list(items), capture_output=True, timeout=10)
    deadline = time.monotonic() + args.seconds
    snapshot = args.out / 'latest-loader-snapshot.private.log'
    frames = args.out / 'frames.private.log'
    fd = os.open(frames, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'ab') as journal:
        while time.monotonic() < deadline:
            try:
                product = command('getvar', 'product')
                if product.returncode or b'product: xnuloader' not in product.stdout + product.stderr:
                    time.sleep(args.poll)
                    continue
                if command('oem', 'log').returncode:
                    time.sleep(args.poll)
                    continue
                snapshot.unlink(missing_ok=True)
                if command('get_staged', str(snapshot)).returncode or not snapshot.is_file():
                    time.sleep(args.poll)
                    continue
                os.chmod(snapshot, 0o600)
                if snapshot.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('loader snapshot exceeds its bounded log capacity')
                new = decoder.feed(snapshot.read_text(errors='replace'))
                if new:
                    journal.write(('\n'.join(new) + '\n').encode())
                    journal.flush()
                    os.fsync(journal.fileno())
                if finish(decoder, args.out):
                    return 0
                if any('capture unavailable' in error for error in decoder.errors):
                    raise ValueError('recovery helper refused the mount or requested log; see private snapshot')
            except subprocess.TimeoutExpired:
                pass
            time.sleep(args.poll)
    raise ValueError('capture timed out without a complete verified file; private frames allow later continuation')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('log recovery: ' + str(error))
