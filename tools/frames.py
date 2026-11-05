#!/usr/bin/env python3
"""
frames.py RUN.log [MORE.log ...]: how evenly the frames of a run's gestures came, and what held up the slow ones.
(More logs: the live ones of the same run, XL_FOLLOW=N XL_FOLLOW_KEEP=1, which keep what the run's log loses.)

Reads the "tl:" lines a run with boot-arg touch_lat=1 writes (see tools/latency.py for what each
says): the samples and buttons of rwtouch (tl: p), the window server's frames (tl: g: began, ended),
their hand-over (tl: s), the display driver's GPU wait and copy (tl: v), and every wait of a program
for the GPU of a millisecond or more (tl: f). Every gesture -- from a touch going down, or a button,
to the next one or 2.5 s later -- is a segment; for each:

  completed framebuffer copies a second; the share of copy-completion intervals over 18.0 and 34.5 ms;
  with boot-arg cpuload=MS (tl: u), how busy each processor core was meanwhile, in percent;
  and for copies more than 18.0 ms apart, associated stage durations (not an additive interval budget):
    draw      the window server drawing it (its first draw to its glFlush done: CPU, Mesa included)
    queued/GPU the display queue and GPU wait after hand-over (SwapEnd to ready)
    idle      the window server not drawing at all before it began (waiting for a commit, a fence, ...)
    copy      the display driver's copy into the panel's memory

The thresholds allow timing tolerance around nominal 60 Hz intervals (18.0/34.5 ms) and 120 Hz (9.0 ms).
These are memory-copy completion timestamps, not measurements of physical panel scanout or photons.

A window server drawing through Metal says no "tl: g" (that is eagl.c's, the OpenGL ES road): its frames are
counted all the same, by the swap that handed each over. Draw and idle are then unknown.
SwapEnd to ready includes the display queue as well as GPU waits; ready to shown is presentation pacing.
Metal command-buffer traces (tl: M) are summarized independently: they have no display-frame identifier.
Their "gpu" field is a host submit-to-fence-observation interval, including queueing and scheduling,
not a hardware execution timer. Creation to commit is buffer lifetime, not CPU time spent encoding.
Gaps of 100 ms or more remain in the cadence statistics while a contact is observed down. Others are
reported separately: without animation-state telemetry, they may be idle or animation stalls.
"""
import os
import statistics
import sys

OVER_ONE, OVER_TWO, SEGMENT_US = 18000, 34500, 2500000
OVER_120 = 9000
# the first cpu-id of a small core: the loader lists the boot core, then the big ones, then the small (xnuloader/loader.c);
# on the phone with cpus=7 the Cortex-A55s are 4, 5 and 6 (FRAMES_SMALL_FROM= for another layout)
SMALL_FROM = int(os.environ.get('FRAMES_SMALL_FROM', '4'))


def lines_of(paths):
    seen, out = set(), []
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
                out.append(line)
    return out


def contact_intervals(samples, trace_end):
    """Only observed DOWNs establish a contact; a missing UP ends at the trace boundary."""
    active, intervals = {}, []
    for at, kind, finger in sorted(samples):
        if kind == 1:
            if finger in active:
                intervals.append((active[finger], at))
            active[finger] = at
        elif kind == 3 and finger in active:
            intervals.append((active.pop(finger), at))
    intervals.extend((at, trace_end) for at in active.values())
    return intervals


def cadence_gaps(gaps, contacts):
    """Return counted gaps, contact-overlapping long gaps, and explicitly unclassified long gaps."""
    counted, active_long, unclassified = [], [], []
    for gap in gaps:
        duration, before, after = gap
        active = any(start < after['done'] and end > before['done'] for start, end in contacts)
        if duration < 100000 or active:
            counted.append(gap)
            if duration >= 100000:
                active_long.append(gap)
        else:
            unclassified.append(gap)
    return counted, active_long, unclassified


def distribution(values):
    values = sorted(values)
    return (f'median {statistics.median(values) / 1000:.2f}, '
            f'p95 {values[(95 * len(values) + 99) // 100 - 1] / 1000:.2f}, '
            f'max {values[-1] / 1000:.2f} ms')


def metal_summary(buffers, label):
    if not buffers:
        return
    print(f'Metal command buffers ({label}): {len(buffers)}; independent of display frames')
    print('  lifetime before commit: ' + distribution([commit - made for made, commit, done, gpu in buffers]))
    print('  commit to callback: ' + distribution([done - commit for made, commit, done, gpu in buffers]))
    print('  host submit/fence interval (queueing and completion observation included): ' +
          distribution([gpu for made, commit, done, gpu in buffers]))
    print('  outside that interval (deferral/dispatch/callback residual): ' +
          distribution([done - commit - gpu for made, commit, done, gpu in buffers]))


def copy_summary(copies):
    if not copies:
        return
    print(f'Framebuffer stages ({len(copies)} completed copies in gesture windows; '
          'not an additive end-to-end latency budget):')
    print('  SwapEnd to ready (queue/GPU wait): ' + distribution([f['ready'] - f['swap'] for f in copies]))
    print('  ready to copy start (pacing): ' + distribution([f['shown'] - f['ready'] for f in copies]))
    print('  copy start to completion: ' + distribution([f['done'] - f['shown'] for f in copies]))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    gestures, frames, swaps, shows, waits, loads = [], [], {}, {}, [], []
    samples, metal = [], []
    for line in lines_of(sys.argv[1:]):
        w = line.split()
        try:
            if len(w) < 2:
                continue
            if w[1] == 'p' and len(w) >= 8:
                at, kind, finger = int(w[7]), int(w[2]), int(w[3])
                samples.append((at, kind, finger))
                if kind in (1, 4):
                    gestures.append((at, kind, finger, int(w[4]), int(w[5])))
            elif w[1] == 'g' and len(w) >= 8:
                # (and the core it ended on, "cpu N", where eagl.c says it: -1 where it does not)
                frames.append((int(w[4]), int(w[6]), int(w[7]), int(w[9]) if len(w) >= 10 and w[8] == 'cpu' else -1))
            elif w[1] == 's' and len(w) >= 5:
                swaps[int(w[2])] = int(w[4])
            elif w[1] == 'v' and len(w) >= 9:
                shows[int(w[2])] = (int(w[4]), int(w[6]), int(w[8]))
            elif w[1] == 'f' and len(w) >= 6:
                waits.append((int(w[5]), w[2], int(w[3])))
            elif w[1] == 'u' and len(w) >= 4:
                loads.append((int(w[3]), {int(a): int(b) for a, b in (x.split(':') for x in w[4:])}))
            elif w[1] == 'M' and len(w) == 10 and w[2::2] == ['made', 'committed', 'done', 'gpu']:
                made, commit, done, gpu = (int(v) for v in w[3::2])
                if 0 <= made <= commit <= done and 0 <= gpu <= done - commit:
                    metal.append((made, commit, done, gpu))
        except ValueError:
            pass
    gestures.sort()
    frames.sort(key=lambda f: f[1])
    # every frame on the panel, with the drawing it came from
    timeline = []
    for sid in sorted(shows):
        if sid not in swaps:
            continue
        swap = swaps[sid]
        drawn = [f for f in frames if f[1] <= swap]
        if drawn:
            began, ended, draws, cpu = drawn[-1]
        elif not frames:
            began = ended = swap            # the Metal road: no "tl: g", the swap is where its frame ended
            cpu = -1
        else:
            continue
        ready, shown, done = shows[sid]
        timeline.append(dict(began=began, ended=ended, swap=swap, ready=ready, shown=shown, done=done, cpu=cpu))
    timeline.sort(key=lambda f: f['done'])
    trace_end = max([f['done'] for f in timeline] + [s[0] for s in samples] + [0])
    contacts = contact_intervals(samples, trace_end)
    windows = [(start[0], min(start[0] + SEGMENT_US, gestures[i + 1][0]
                            if i + 1 < len(gestures) else start[0] + SEGMENT_US))
               for i, start in enumerate(gestures)]
    all_counts = [0, 0, 0]
    all_active_long, all_unclassified = [], []
    kinds, previous = {}, ''          # the same gesture done more than once: its frames together
    print(f'frames.py: {len(gestures)} gestures, {len(timeline)} completed framebuffer copies, {len(waits)} waits for the GPU '
          f'of 1 ms or more')
    for i, (start, kind, button, x, y) in enumerate(gestures):
        end = min(gestures[i + 1][0] if i + 1 < len(gestures) else start + SEGMENT_US, start + SEGMENT_US)
        chosen = [f for f in timeline if start <= f['done'] < end]
        what = f'button {button}' if kind == 4 else f'touch at {x / 655.35:.0f}%,{y / 655.35:.0f}%'
        key = f'{what} after {previous}' if kind == 4 else what
        previous = what
        if len(chosen) < 2:
            print(f'{i + 1:2}. {start / 1e6:9.3f} s {what}: {len(chosen)} completed copies')
            continue
        gaps = [(b['done'] - a['done'], a, b) for a, b in zip(chosen, chosen[1:])]
        moving, active_long, unclassified = cadence_gaps(gaps, contacts)
        all_active_long.extend(active_long)
        all_unclassified.extend(unclassified)
        span = sum(g[0] for g in moving)
        over1 = [g for g in moving if g[0] > OVER_ONE]
        over2 = [g for g in moving if g[0] > OVER_TWO]
        over120 = [g for g in moving if g[0] > OVER_120]
        all_counts[0] += len(moving)
        all_counts[1] += len(over1)
        all_counts[2] += len(over2)
        k = kinds.setdefault(key, [0, 0, 0, 0, 0])
        k[0] += 1
        k[1] += len(moving)
        k[2] += span
        k[3] += len(over1)
        k[4] += len(over2)
        fps = len(moving) * 1e6 / span if span else 0
        line = (f'{i + 1:2}. {start / 1e6:9.3f} s {what}: {len(moving)} copy intervals at {fps:4.1f} a second; '
                f'over 9.0 ms {100 * len(over120) / max(1, len(moving)):3.0f}%, '
                f'over 18.0 ms {100 * len(over1) / max(1, len(moving)):3.0f}%, over 34.5 ms {100 * len(over2) / max(1, len(moving)):3.0f}%')
        if active_long:
            line += f'; contact-active gaps >=100 ms: {len(active_long)} (max {max(g[0] for g in active_long) / 1000:.1f} ms, counted)'
        if unclassified:
            line += (f'; gaps >=100 ms outside observed contacts: {len(unclassified)} '
                     f'(max {max(g[0] for g in unclassified) / 1000:.1f} ms, excluded; idle or animation stalls unknown)')
        if over1:
            # where the slow frames' time went (means over them)
            def mean(values):
                return sum(values) / len(values) / 1000 if values else 0
            draw = mean([b['ended'] - b['began'] for _, a, b in over1])
            gpu = mean([b['ready'] - b['swap'] for _, a, b in over1])
            beat = mean([b['shown'] - b['ready'] for _, a, b in over1])
            idle = mean([b['began'] - a['ready'] for _, a, b in over1])
            copy = mean([b['done'] - b['shown'] for _, a, b in over1])
            if frames:
                line += f'; the slow ones: draw {draw:4.1f}, idle before {idle:5.1f}, '
            else:
                line += '; the slow ones: draw/idle unknown (no GLES draw trace), '
            line += f'queued/GPU {gpu:4.1f}, pacing {beat:4.1f}, copy {copy:3.1f} ms'
            known = [b for _, a, b in over1 if b['cpu'] >= 0]
            if known:
                line += f', {100 * sum(b["cpu"] >= SMALL_FROM for b in known) / len(known):.0f}% drawn on small cores'
            stalls = [w for w in waits if start <= w[0] < end]
            if stalls:
                line += f'; {len(stalls)} waits for the GPU ({sum(w[2] for w in stalls) / 1000:.0f} ms)'
        # the load samples that end within the movement (each covers the cpuload=MS before it)
        within = [l for t, l in loads if chosen[0]['done'] < t <= chosen[-1]['done']]
        if within:
            cores = sorted({c for l in within for c in l}, reverse=True)
            line += '; cores busy ' + ' '.join(f'{c}:{sum(l.get(c, 0) for l in within) / len(within):.0f}%' for c in cores)
        print(line)
    if any(k[0] > 1 for k in kinds.values()):
        print('the same gestures together:')
        for key, (times, steps, span, over1, over2) in kinds.items():
            print(f'  {key:40} {times} times, {steps:4} copy intervals at {steps * 1e6 / span if span else 0:4.1f} a second; '
                  f'over 18.0 ms {100 * over1 / max(1, steps):3.0f}%, over 34.5 ms {100 * over2 / max(1, steps):3.0f}%')
    if all_counts[0]:
        print(f'all gestures: {all_counts[0]} copy intervals, over 18.0 ms {100 * all_counts[1] / all_counts[0]:.0f}%, '
              f'over 34.5 ms {100 * all_counts[2] / all_counts[0]:.0f}%')
    if all_active_long or all_unclassified:
        print(f'gaps >=100 ms: {len(all_active_long)} overlap observed contacts and are counted; '
              f'{len(all_unclassified)} outside observed contacts are unclassified and excluded')
    copy_summary([f for f in timeline if any(start <= f['done'] < end for start, end in windows)])
    metal_summary(metal, 'whole retained trace, including startup if present')
    if windows and metal:
        metal_summary([cb for cb in metal if any(start <= cb[2] < end for start, end in windows)],
                      'callbacks in gesture windows; overlapping buffers are not summed')
    # the window server's drawing by the kind of core it ended on (eagl.c's "cpu N")
    known = [f for f in timeline if f['cpu'] >= 0]
    if known:
        small = [f['ended'] - f['began'] for f in known if f['cpu'] >= SMALL_FROM]
        big = [f['ended'] - f['began'] for f in known if f['cpu'] < SMALL_FROM]
        def avg(values):
            return sum(values) / len(values) / 1000 if values else 0
        print(f'drawing: {len(big)} frames on big cores ({avg(big):.1f} ms each), {len(small)} on small ones '
              f'({avg(small):.1f} ms each)')


if __name__ == '__main__':
    main()
