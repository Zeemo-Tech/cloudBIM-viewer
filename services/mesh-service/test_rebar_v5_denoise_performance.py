"""Denoising must only fit covariance for compact, low-count candidates."""
import unittest
from unittest import mock

import numpy as np

from algorithms.rebar_v5 import features
from algorithms.rebar_v5.contracts import Params


class DenoisePerformanceTests(unittest.TestCase):
    def test_dense_support_skips_irrelevant_eigenproblems(self):
        line = np.column_stack((np.arange(10_000) * .001, np.zeros((10_000, 2))))
        compact = np.array([[20., 0., 0.], [20.004, 0., 0.], [20., .004, 0.], [20.004, .004, 0.]])
        support = np.vstack((line, compact, [[30., 0., 0.]]))
        original = np.linalg.eigvalsh
        fitted_rows = []

        def count_rows(covariance):
            fitted_rows.append(len(covariance))
            return original(covariance)

        with mock.patch.object(features.np.linalg, 'eigvalsh', side_effect=count_rows):
            noise, suspect = features.denoise(support, support, Params())
        self.assertFalse(noise[:len(line)].any())
        self.assertTrue(noise[len(line):].all())
        self.assertFalse(suspect.any())
        self.assertLessEqual(sum(fitted_rows), 5, 'dense points cannot be compact-small noise; do not fit their covariance')


if __name__ == '__main__':
    unittest.main()
