"""Exact spatial grouping without NumPy's costly structured-record sort."""
import unittest
from unittest import mock

import numpy as np

from algorithms import rebar_v4_geometry as geometry
from algorithms.spatial_keys import integer_row_groups, unique_integer_rows


class IntegerKeyTests(unittest.TestCase):
    def test_unique_matches_numpy_including_first_rows_and_inverse(self):
        rng = np.random.default_rng(71)
        for dtype in (np.int64, np.uint64):
            limit = np.iinfo(dtype)
            for width in (1, 2, 3):
                random = rng.integers(0, 20, (1000, width)).astype(dtype)
                extreme = np.array([[limit.min] * width, [limit.max] * width], dtype=dtype)
                for keys in (np.empty((0, width), dtype=dtype), extreme,
                             np.concatenate((random, extreme, random))[::-1]):
                    expected = np.unique(keys, axis=0, return_index=True, return_inverse=True)
                    actual = unique_integer_rows(keys, return_index=True, return_inverse=True)
                    for got, want in zip(actual, expected):
                        np.testing.assert_array_equal(got, want)
                    np.testing.assert_array_equal(unique_integer_rows(keys), expected[0])

    def test_group_rows_preserve_input_order_and_do_not_merge_large_integers(self):
        keys = np.array([[2**63, 0], [2**63+1, 0], [2**63, 0], [0, 1]], np.uint64)
        groups = list(integer_row_groups(keys))
        self.assertEqual([tuple(key) for key, _ in groups], [(0, 1), (2**63, 0), (2**63+1, 0)])
        self.assertEqual([rows.tolist() for _, rows in groups], [[3], [0, 2], [1]])

    def test_rejects_float_keys_instead_of_changing_nan_semantics(self):
        with self.assertRaises(ValueError):
            unique_integer_rows(np.array([[np.nan, 1.]]))

    def test_spatial_keys_require_at_least_one_coordinate(self):
        for count in (0, 3):
            with self.assertRaises(ValueError):
                unique_integer_rows(np.empty((count, 0), np.int64))

    def test_dense_connectivity_does_not_sort_structured_records(self):
        values = np.repeat([[.1, .1], [.2, .2], [4.1, 4.1]], 20_000, axis=0)
        original = np.unique

        def forbid_record_sort(keys, *args, **kwargs):
            self.assertFalse(np.asarray(keys).ndim == 2 and kwargs.get('axis') == 0,
                             'dense spatial grouping must avoid structured-record sorting')
            return original(keys, *args, **kwargs)

        with mock.patch.object(geometry.np, 'unique', side_effect=forbid_record_sort):
            groups = geometry.sparse_grid_components(values, 1.)
        np.testing.assert_array_equal(groups[0], np.arange(40_000))
        np.testing.assert_array_equal(groups[1], np.arange(40_000, 60_000))


if __name__ == '__main__':
    unittest.main()
