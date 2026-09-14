import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location('speed_benchmark', Path(__file__).with_name('pointcloud-speed-benchmark.py'))
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkComparisonTests(unittest.TestCase):
    def scene(self, directory, labels, *, source='same-source', instance_count=2, elapsed=1.):
        directory.mkdir()
        np.save(directory/'complete_class.npy', np.asarray(labels, np.uint8))
        np.save(directory/'positions.npy', np.arange(len(labels)*3, dtype=np.float64).reshape(-1, 3))
        manifest = {'source': {'sha256': source}, 'parameters': {'workers': 1},
                    'attributes': {'columns': {'complete_class': 'complete_class.npy', 'positions': 'positions.npy'}},
                    'completeRebar': {'instanceCount': instance_count, 'elapsedS': elapsed, 'timings': {'fitS': elapsed}}}
        (directory/'manifest.json').write_text(json.dumps(manifest))

    def test_counts_cannot_hide_row_permutations(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp)/'a', Path(temp)/'b'
            self.scene(a, [3, 3, 4, 4])
            self.scene(b, [3, 4, 3, 4])
            result = benchmark.compare_runs(a, b)
            self.assertFalse(result['passed'])
            self.assertEqual(result['columns']['complete_class']['changedRows'], 2)
            matrix = result['columns']['complete_class']['confusionBaselineRowsCandidateColumns']
            self.assertEqual(matrix[3][4], 1)
            self.assertEqual(matrix[4][3], 1)

    def test_timing_is_ignored_but_identity_and_report_decisions_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b, c, d = [Path(temp)/name for name in 'abcd']
            self.scene(a, [3, 4])
            self.scene(b, [3, 4], elapsed=.5)
            self.assertTrue(benchmark.compare_runs(a, b)['passed'])
            self.scene(c, [3, 4], source='different-source')
            self.assertFalse(benchmark.compare_runs(a, c)['passed'])
            self.scene(d, [3, 4], instance_count=1)
            self.assertFalse(benchmark.compare_runs(a, d)['passed'])


if __name__ == '__main__':
    unittest.main()
