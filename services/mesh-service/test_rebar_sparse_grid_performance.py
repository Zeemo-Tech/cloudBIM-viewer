"""Reference and performance regressions for sparse occupied-cell grouping."""
from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from algorithms import rebar_v4_geometry as geometry


class ReferenceDisjointSet:
    def __init__(self, count):
        self.parent = np.arange(count, dtype=np.intp)

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = int(self.parent[value])
        return value

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def reference_sparse_grid_components(values, cell, dimensions=2):
    """Frozen pre-optimization implementation for behavioural comparison."""
    if len(values) == 0:
        return []
    keys = np.floor(np.asarray(values)[:, :dimensions] / cell).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    lookup = {tuple(row): index for index, row in enumerate(unique.tolist())}
    dsu = ReferenceDisjointSet(len(unique))
    offsets = np.array(np.meshgrid(*([[-1, 0, 1]] * dimensions))).T.reshape(-1, dimensions)
    offsets = offsets[np.any(offsets != 0, axis=1)]
    for index, key in enumerate(unique):
        for offset in offsets:
            other = lookup.get(tuple((key + offset).tolist()))
            if other is not None and other > index:
                dsu.union(index, other)
    groups = {}
    for row, cell_index in enumerate(inverse.tolist()):
        groups.setdefault(dsu.find(cell_index), []).append(row)
    return [np.asarray(rows, dtype=np.intp) for _, rows in sorted(groups.items())]


class SparseGridComponentsRegressionTests(unittest.TestCase):
    def assert_reference_equal(self, values, cell=1.0, dimensions=2):
        expected = reference_sparse_grid_components(values, cell, dimensions)
        actual = geometry.sparse_grid_components(values, cell, dimensions)
        self.assertEqual(len(actual), len(expected))
        for got, want in zip(actual, expected):
            np.testing.assert_array_equal(got, want)

    def test_matches_reference_for_random_duplicates_negative_holes_and_3d(self):
        rng = np.random.default_rng(20260907)
        random = rng.uniform(-5, 5, size=(400, 3))
        duplicates = np.repeat(np.array([[-2.2, 1.1, 7.0], [3.9, -4.1, 2.0]]), 17, axis=0)
        # Four occupied 2D cells around an empty cell, plus a distant component.
        holes = np.array([[x + .1, y + .1, 0.] for x, y in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1), (7, 7))])
        values = np.vstack((random, duplicates, holes))
        self.assert_reference_equal(values, dimensions=2)
        self.assert_reference_equal(values, dimensions=3)

    def test_dense_duplicate_grouping_finds_once_per_occupied_cell(self):
        values = np.repeat(np.array([[1.25, -2.75, 9.0]]), 60_000, axis=0)
        with mock.patch.object(geometry._DisjointSet, "find", wraps=geometry._DisjointSet.find, autospec=True) as find:
            components = geometry.sparse_grid_components(values, 1.0)
        self.assertEqual(find.call_count, 1)
        self.assertEqual(len(components), 1)
        np.testing.assert_array_equal(components[0], np.arange(len(values), dtype=np.intp))


if __name__ == "__main__":
    unittest.main()
