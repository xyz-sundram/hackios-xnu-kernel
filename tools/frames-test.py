#!/usr/bin/env python3
"""Regression cases for retained stalls and independent Metal timing attribution."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('frames', Path(__file__).with_name('frames.py'))
frames = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frames)


def display(sid, done):
    return (f'tl: s {sid} at {done - 3000}\n'
            f'tl: v {sid} ready {done - 2000} shown {done - 1000} done {done}\n')


def report(log):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'run.log'
        path.write_text(log)
        out = io.StringIO()
        # Repeated snapshots must not double-count the same touch, CB, or frame.
        with patch.object(frames.sys, 'argv', ['frames.py', str(path), str(path)]), contextlib.redirect_stdout(out):
            frames.main()
        return out.getvalue()


class TimingTests(unittest.TestCase):
    def test_short_distribution_reports_true_median_and_nearest_rank_tail(self):
        self.assertEqual(frames.distribution([9000, 1000]), 'median 5.00, p95 9.00, max 9.00 ms')

    def test_contact_stall_retained_and_inactive_gap_disclosed(self):
        log = ('tl: p 1 0 32768 32768 at 1000000\n'
               'tl: p 2 0 32768 30000 at 1100000\n'
               'tl: p 3 0 32768 30000 at 1270000\n')
        log += ''.join(display(i, t) for i, t in enumerate([1010000, 1260000, 1270000, 1600000]))
        out = report(log)
        self.assertIn('2 copy intervals at  7.7 a second', out)
        self.assertIn('contact-active gaps >=100 ms: 1 (max 250.0 ms, counted)', out)
        self.assertIn('max 330.0 ms, excluded; idle or animation stalls unknown', out)
        self.assertIn('all gestures: 2 copy intervals, over 18.0 ms 50%, over 34.5 ms 50%', out)

    def test_two_frame_gesture_does_not_hide_stall(self):
        out = report('tl: p 1 0 0 0 at 1000000\n' + display(1, 1010000) + display(2, 1210000))
        self.assertIn('contact-active gaps >=100 ms: 1 (max 200.0 ms, counted)', out)
        self.assertIn('all gestures: 1 copy intervals', out)

    def test_contacts_track_fingers_and_bound_missing_up(self):
        contacts = frames.contact_intervals([(10, 1, 0), (20, 1, 1), (30, 3, 0), (40, 3, 2)], 50)
        self.assertEqual(sorted(contacts), [(10, 30), (20, 50)])
        self.assertEqual(frames.contact_intervals([(10, 2, 0), (20, 3, 0)], 30), [])

    def test_metal_is_separate_and_startup_is_excluded_from_gesture_summary(self):
        log = ('tl: M made 1000 committed 2000 done 62000 gpu 50000\n'
               'tl: p 1 0 32768 32768 at 1000000\n'
               'tl: p 3 0 32768 32768 at 1300000\n'
               'tl: M made 1000000 committed 1007000 done 1020000 gpu 11000\n')
        log += display(1, 1010000) + display(2, 1060000)
        out = report(log)
        self.assertIn('draw/idle unknown (no GLES draw trace)', out)
        self.assertIn('including startup if present): 2; independent of display frames', out)
        gesture = out.split('Metal command buffers (callbacks in gesture windows;')[1]
        self.assertIn('): 1; independent of display frames', gesture)
        self.assertIn('lifetime before commit: median 7.00', gesture)
        self.assertIn('commit to callback: median 13.00', gesture)
        self.assertIn('completion observation included): median 11.00', gesture)
        self.assertIn('deferral/dispatch/callback residual): median 2.00', gesture)

    def test_truncated_and_impossible_timestamps_are_not_reported(self):
        out = report('tl: \n'
                     'tl: M made 100 committed 200 done\n'
                     'tl: M made 100 committed 200 done 300 gpu 200\n'
                     'tl: M made 100 committed 90 done 300 gpu 10\n')
        self.assertNotIn('Metal command buffers', out)
        self.assertIn('0 gestures, 0 completed framebuffer copies', out)

    def test_copy_stages_use_gesture_windows_and_actual_thresholds(self):
        log = (display(0, 500000) + 'tl: p 1 0 0 0 at 1000000\n' +
               display(1, 1010000) + display(2, 1027000) + display(3, 1046000))
        out = report(log)
        self.assertIn('4 completed framebuffer copies', out)
        self.assertNotIn('frames on the panel', out)
        self.assertIn('all gestures: 2 copy intervals, over 18.0 ms 50%, over 34.5 ms 0%', out)
        self.assertIn('Framebuffer stages (3 completed copies in gesture windows;', out)
        self.assertIn('SwapEnd to ready (queue/GPU wait): median 1.00, p95 1.00, max 1.00 ms', out)
        self.assertIn('ready to copy start (pacing): median 1.00', out)
        self.assertIn('copy start to completion: median 1.00', out)


if __name__ == '__main__':
    unittest.main()
