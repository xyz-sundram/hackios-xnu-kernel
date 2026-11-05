#!/usr/bin/env python3
"""
latency.py RUN.log [MORE.log ...] [RECORDING.raw]: recorded framebuffer motion relative to posted touch samples.
(More logs: the live ones of the same run, XL_FOLLOW=N XL_FOLLOW_KEEP=1, which keep what the run's log loses.)

A run with boot-arg touch_lat=1 writes a line at every stage a touch passes on its way to the panel,
all in microseconds of mach_absolute_time (the kernel's clock, which user space reads too):

  tl: p TYPE FINGER X Y at US              the kernel posts a sample (drivers/input/hakios.input.c)
  tl: c before US seen US read US          the touch driver finds the interrupt line low and reads the
                                           report (drivers/input/hakios.goodix.c; only for a real finger)
  tl: i TYPE posted US read US sent US     the window server reads it and hands it to the event system
                                           (userland/src/inject.c)
  tl: g FRAME began US ended US DRAWS      the window server draws a frame (userland/src/eagl.c)
  tl: s ID at US                           it hands the frame over (SwapEnd)
  tl: v ID ready US shown US done US       the display driver: queue/GPU wait over, copy starting, and
                                           framebuffer copy complete (MiniIOMobileFramebuffer.cpp)

With rec_secs=... rec_ring=1 the frames themselves are in the screen recording, each with the time it
was copied into framebuffer memory. A "zigzag" of rwtouch (up and down over a scrolling list
without letting go) gives a fitted lag between recorded content and kernel-posted coordinates.
The time by which the list's movement lags the posted coordinates is fitted (a
constant offset -- the scroll view's slop before it starts to follow -- does not matter; the turns do).
This excludes physical touch acquisition before the kernel post and physical scanout after the copy.
Stage means cover the zigzag window, without a causal sample-to-frame mapping. They must not be added
or subtracted from the fit to infer application/CoreAnimation latency. Metal has no GLES draw trace;
its command-buffer timing is summarized separately by tools/frames.py.
"""
import re
import struct
import sys

try:
    import numpy as np
except ModuleNotFoundError:
    np = None                       # trace-only stage reporting does not need image analysis


def micros_lines(paths):
    """The tl: lines of the logs, once each (the kernel's own also come back in its log at the end)."""
    seen, lines = set(), []
    if isinstance(paths, str):
        paths = [paths]
    for path in paths:
      with open(path, 'rb') as f:
        for raw in f:
            text = raw.decode('utf-8', 'replace')
            at = text.find('tl: ')
            if at < 0:
                continue
            line = text[at:].strip()
            if line not in seen:
                seen.add(line)
                lines.append(line)
    return lines


def parse(lines):
    samples, reads, frames, swaps, shows, chip = [], [], [], {}, {}, []
    for line in lines:
        w = line.split()
        try:
            if len(w) < 2:
                continue
            if w[1] == 'p' and len(w) >= 8:
                samples.append((int(w[7]), int(w[2]), int(w[3]), int(w[4]), int(w[5])))
            elif w[1] == 'i' and len(w) >= 9:
                reads.append((int(w[4]), int(w[6]), int(w[8]), int(w[2])))
            elif w[1] == 'g' and len(w) >= 8:
                frames.append((int(w[4]), int(w[6]), int(w[7])))
            elif w[1] == 's' and len(w) >= 5:
                swaps[int(w[2])] = int(w[4])
            elif w[1] == 'v' and len(w) >= 9:
                shows[int(w[2])] = (int(w[4]), int(w[6]), int(w[8]))
            elif w[1] == 'c' and len(w) >= 8:
                chip.append((int(w[3]), int(w[5]), int(w[7])))
        except ValueError:
            pass
    samples.sort()
    reads.sort()
    frames.sort(key=lambda f: f[1])
    return samples, reads, frames, swaps, shows, chip


def recording(path):
    if np is None:
        sys.exit('latency.py: NumPy is required to analyze a .raw recording; omit it for trace-only stage reporting')
    data = open(path, 'rb').read()
    magic, width, height, count, oldest = struct.unpack_from('<8sIIII', data, 0)
    if magic != b'RWREC003':
        sys.exit('latency.py: not a recording of the RWREC003 kind')
    began = struct.unpack_from('<Q', data, 24)[0]
    size = 8 + width * height * 3
    out = []
    for k in range(count):
        i = (oldest + k) % count
        at = 32 + i * size
        t = struct.unpack_from('<Q', data, at)[0]
        pixels = np.frombuffer(data, np.uint8, width * height * 3, at + 8).reshape(height, width, 3)
        out.append(((began + t) // 1000, pixels))
    out.sort(key=lambda f: f[0])
    return out, width, height


def zigzags(samples):
    """Touches with many moves, finger 0: (down time, up time, [(t, y)])."""
    out, current = [], None
    for t, kind, finger, x, y in samples:
        if finger != 0:
            continue
        if kind == 1:
            current = [(t, y)]
        elif kind == 2 and current is not None:
            current.append((t, y))
        elif kind == 3 and current is not None:
            current.append((t, y))
            ys = [p[1] for p in current]
            turns = sum(1 for i in range(1, len(ys) - 1) if (ys[i] - ys[i - 1]) * (ys[i + 1] - ys[i]) < 0)
            if len(current) > 30 and turns >= 3:
                out.append((current[0][0], t, current))
            current = None
    return out


def shift(a, b, most):
    """How far b's content has moved down from a's, in rows: the shift with the least difference."""
    h = a.shape[0]
    best, best_cost = 0, None
    for s in range(-most, most + 1):
        if s >= 0:
            pa, pb = a[0:h - s], b[s:h]
        else:
            pa, pb = a[-s:h], b[0:h + s]
        cost = np.mean(np.abs(pa - pb))
        if best_cost is None or cost < best_cost:
            best, best_cost = s, cost
    return best


def content_offsets(frames, t0, t1, height):
    """The list's position in each frame between t0 and t1, in rows of the recording, relative to the first."""
    chosen = [(t, p) for t, p in frames if t0 <= t <= t1]
    out = []
    if not chosen:
        return out
    top, bottom = int(height * 0.15), int(height * 0.90)
    previous, offset = None, 0
    for t, pixels in chosen:
        grey = pixels[top:bottom, :, :].astype(np.float32).mean(axis=2)
        grey = grey[:, grey.shape[1] // 10: grey.shape[1] * 9 // 10]
        if previous is not None:
            offset += shift(previous, grey, 48)
        out.append((t, offset))
        previous = grey
    return out


def fit_lag(offsets, touch, height):
    """The lag (us) at which the list's position best follows the finger's: least squares with an offset and a gain."""
    tt = np.array([p[0] for p in touch], dtype=np.float64)
    ty = np.array([p[1] for p in touch], dtype=np.float64) / 65535.0 * height
    ft = np.array([o[0] for o in offsets], dtype=np.float64)
    fy = np.array([o[1] for o in offsets], dtype=np.float64)
    best = None
    for lag in range(0, 250001, 500):
        at = ft - lag
        keep = (at >= tt[0]) & (at <= tt[-1])
        if keep.sum() < 10:
            continue
        x = np.interp(at[keep], tt, ty)
        y = fy[keep]
        matrix = np.vstack([x, np.ones_like(x)]).T
        coefficients, residual, _, _ = np.linalg.lstsq(matrix, y, rcond=None)
        error = float(np.mean((matrix @ coefficients - y) ** 2))
        if best is None or error < best[1]:
            best = (lag, error, coefficients[0], int(keep.sum()))
    return best


def mean(values):
    return sum(values) / len(values) if values else float('nan')


def stage_summary(down, up, reads, frames, swaps, shows):
    """Windowed observations, not a decomposition of the fitted touch/content lag."""
    within = [r for r in reads if down <= r[0] <= up]
    drawn = [f for f in frames if down <= f[1] <= up + 400000]
    copies = [(swaps[sid], *shows[sid]) for sid in shows
              if sid in swaps and down <= shows[sid][2] <= up + 400000]
    print('  stage means in the zigzag window (+400 ms for rendering/copies); not an additive latency budget:')
    if within:
        print(f'    kernel post to window-server read: {mean([r[1] - r[0] for r in within]) / 1000:.2f} ms')
        print(f'    read to event dispatch: {mean([r[2] - r[1] for r in within]) / 1000:.2f} ms')
    else:
        print('    kernel post/read/dispatch latency: unknown (no input-dispatch trace in this window)')
    if drawn:
        print(f'    GLES draw duration: {mean([f[1] - f[0] for f in drawn]) / 1000:.2f} ms '
              f'({len(drawn)} draws, not paired to display copies)')
    else:
        print('    draw duration: unknown (no GLES draw trace in this window; Metal buffers are not paired to copies)')
    print('    application/CA scheduling and render-to-SwapEnd latency: unknown (no causal sample/frame mapping)')
    if copies:
        print(f'    SwapEnd to ready (queue/GPU wait): {mean([r[1] - r[0] for r in copies]) / 1000:.2f} ms')
        print(f'    ready to copy start (pacing): {mean([r[2] - r[1] for r in copies]) / 1000:.2f} ms')
        print(f'    copy start to completion: {mean([r[3] - r[2] for r in copies]) / 1000:.2f} ms '
              f'({len(copies)} completed framebuffer copies)')
    else:
        print('    framebuffer stages: unknown (no matched SwapEnd/copy trace in this window)')
    print('    physical touch acquisition and panel scanout delay: unmeasured')


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    logs = [a for a in sys.argv[1:] if not a.endswith('.raw')]
    raws = [a for a in sys.argv[1:] if a.endswith('.raw')]
    samples, reads, frames, swaps, shows, chip = parse(micros_lines(logs))
    print(f'latency.py: {len(samples)} samples, {len(reads)} read by the window server, {len(frames)} GLES draw records, '
          f'{len(swaps)} handed over, {len(shows)} completed framebuffer copies; {len(chip)} touch-chip trace records')
    if chip:
        found = [seen - before for before, seen, read in chip if before]
        took = [read - seen for before, seen, read in chip]
        print(f'  touch chip: mean interval between the preceding poll and IRQ observation {mean(found) / 1000:.2f} ms; '
              f'mean report-read duration {mean(took) / 1000:.2f} ms (physical touch time unmeasured)')
    pictures, width, height = recording(raws[0]) if raws else ([], 0, 0)
    totals = []
    for number, (down, up, touch) in enumerate(zigzags(samples)):
        moves = [p for p in touch[1:-1]]
        print(f'zigzag {number + 1}: {len(touch)} samples over {(up - down) / 1000:.0f} ms')
        result = None
        if pictures:
            offsets = content_offsets(pictures, down, up + 400000, height)
            result = fit_lag(offsets, moves, height) if len(offsets) > 10 else None
        if result is None:
            print('  recorded-content lag: unknown (no recording or insufficient usable frames for this zigzag)')
        else:
            lag, error, gain, used = result
            print(f'  fitted recorded-content lag relative to kernel-posted coordinates: {lag / 1000:.1f} ms '
                  f'(gain {gain:.2f}, misfit {error ** 0.5:.1f} rows, {used} frames; not physical end-to-end latency)')
            totals.append(lag)
        stage_summary(down, up, reads, frames, swaps, shows)
    if totals:
        print(f'all fitted zigzags: mean recorded-content lag relative to posted coordinates {mean(totals) / 1000:.1f} ms')


if __name__ == '__main__':
    main()
