import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('timings', Path(__file__).parents[1] / 'tools/summarize_frame_timings.py')
timings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timings)


class FrameTimingsTests(unittest.TestCase):
    def summarize(self, text, seconds=30):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frames.csv'
            path.write_text('sealed_s,submitted_s,presented_s,gpu_ms,drops,repeat\n' + text)
            return timings.summarize(path, seconds)

    def test_repeats_do_not_inflate_new_frame_fps(self):
        result = self.summarize('1000,1000,1000.01,1,0,0\n1000,1000,1000.015,1,0,1\n1000.02,1000.02,1000.03,1,2,0\n')
        self.assertAlmostEqual(result['new_completion_fps'], 50)
        self.assertAlmostEqual(result['new_intervals_ms']['p99'], 20)
        self.assertAlmostEqual(result['sealed_to_completion_ms']['p50'], 10)
        self.assertEqual(result['drops_delta'], 2)
        self.assertEqual(result['new_frames'], 2)

    def test_partial_nonfinite_and_backwards_records_are_rejected(self):
        result = self.summarize('1,1,1.01,1,0,0\n1,1,nan,1,0,0\n0,0,0.5,1,0,0\n2,2,2.01,1,0,0\n3,3')
        self.assertEqual(result['rejected_records'], 3)
        self.assertEqual(result['new_frames'], 2)

    def test_window_uses_csv_clock_domain_including_epoch_seconds(self):
        result = self.summarize('1790000000,1790000000,1790000000.01,1,0,0\n1790000040,1790000040,1790000040.01,1,0,0\n1790000041,1790000041,1790000041.01,1,0,0\n', 2)
        self.assertEqual(result['new_frames'], 2)
        self.assertAlmostEqual(result['new_completion_fps'], 1)


if __name__ == '__main__':
    unittest.main()
