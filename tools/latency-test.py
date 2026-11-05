#!/usr/bin/env python3
"""Output regressions for the boundaries of latency measurements."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('latency', Path(__file__).with_name('latency.py'))
latency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latency)


def zigzag_log():
    lines = ['tl: p 1 0 1000 1000 at 1000000']
    for i in range(1, 35):
        lines.append(f'tl: p 2 0 1000 {1000 + (i % 2) * 1000} at {1000000 + i * 10000}')
    lines += ['tl: p 3 0 1000 1000 at 1350000',
              'tl: i 2 posted 1010000 read 1010100 sent 1010400',
              'tl: s 1 at 1100000',
              'tl: v 1 ready 1102000 shown 1105000 done 1109000',
              # Startup data must not contribute to the gesture-stage means.
              'tl: g 0 began 1000 ended 101000 3',
              'tl: s 0 at 200000',
              'tl: v 0 ready 250000 shown 300000 done 400000']
    return '\n'.join(lines) + '\n'


def report(log, fitted=False):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'run.log'
        path.write_text(log)
        out = io.StringIO()
        args = ['latency.py', str(path)] + (['recording.raw'] if fitted else [])
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(latency.sys, 'argv', args))
            stack.enter_context(contextlib.redirect_stdout(out))
            if fitted:
                stack.enter_context(patch.object(latency, 'recording', return_value=([(1000000, None)], 100, 100)))
                stack.enter_context(patch.object(latency, 'content_offsets', return_value=[(i, i) for i in range(12)]))
                stack.enter_context(patch.object(latency, 'fit_lag', return_value=(25000, 4, 1, 12)))
            latency.main()
        return out.getvalue()


class LatencyOutputTests(unittest.TestCase):
    def test_missing_render_trace_and_recording_preserve_available_stages(self):
        out = report(zigzag_log())
        self.assertIn('recorded-content lag: unknown', out)
        self.assertIn('draw duration: unknown (no GLES draw trace in this window', out)
        self.assertIn('kernel post to window-server read: 0.10 ms', out)
        self.assertIn('read to event dispatch: 0.30 ms', out)
        self.assertIn('SwapEnd to ready (queue/GPU wait): 2.00 ms', out)
        self.assertIn('ready to copy start (pacing): 3.00 ms', out)
        self.assertIn('copy start to completion: 4.00 ms (1 completed framebuffer copies)', out)
        self.assertIn('physical touch acquisition and panel scanout delay: unmeasured', out)
        self.assertNotIn('120 Hz', out)

    def test_fitted_lag_does_not_claim_physical_latency_or_app_residual(self):
        out = report(zigzag_log(), fitted=True)
        self.assertIn('relative to kernel-posted coordinates: 25.0 ms', out)
        self.assertIn('not physical end-to-end latency', out)
        self.assertIn('application/CA scheduling and render-to-SwapEnd latency: unknown', out)
        self.assertNotIn('END TO END:', out)
        self.assertNotIn('what is left', out)
        self.assertNotIn('behind the finger', out)

    def test_gles_draw_mean_is_independent_of_copy_matching(self):
        out = report(zigzag_log() + 'tl: g 1 began 1110000 ended 1117000 3\n', fitted=True)
        self.assertIn('GLES draw duration: 7.00 ms (1 draws, not paired to display copies)', out)
        self.assertIn('not an additive latency budget', out)
        self.assertIn('render-to-SwapEnd latency: unknown', out)

    def test_truncated_trace_lines_are_ignored(self):
        parsed = latency.parse(['tl:', 'tl: p', 'tl: p 1 0 1 2 at bad',
                                'tl: v 1 ready 1 shown', 'tl: p 1 0 1 2 at 123'])
        self.assertEqual(parsed[0], [(123, 1, 0, 1, 2)])
        self.assertEqual(parsed[1:], ([], [], {}, {}, []))


if __name__ == '__main__':
    unittest.main()
