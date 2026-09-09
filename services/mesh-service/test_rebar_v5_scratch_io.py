"""Resident PCA is reused without scratch I/O; small budgets still spill."""
import unittest
from types import SimpleNamespace

import numpy as np

from algorithms.rebar_v5.pipeline import prepare
from algorithms.rebar_v5.contracts import Params


class ScratchIOTests(unittest.TestCase):
    def test_base_features_remain_resident_until_persistence_is_needed(self):
        points = np.array([[x, y, 0.] for x in np.arange(0, .1, .01) for y in np.arange(0, .1, .01)])
        indices = np.arange(len(points), dtype=np.uint64)
        runtime, _, _, _ = prepare(SimpleNamespace(iter_chunks=lambda: iter([(indices, points)])), Params())
        try:
            for path in runtime.feature_chunks:
                self.assertFalse(path.exists(), 'resident features must not be written to scratch')
                with runtime.chunks.read(path) as data:
                    np.testing.assert_array_equal(data['xyz'], points[data['source_index']])
        finally:
            runtime.close()


if __name__ == '__main__':
    unittest.main()
