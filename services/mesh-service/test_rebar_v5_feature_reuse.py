import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.features import pca_features, prepare_pca_context
from algorithms.rebar_v5.scene import _consistent_fixture_normals, table_mask, _adaptive_grid


def plane(x, y):
    xx, yy = np.meshgrid(x, y)
    return np.column_stack((xx.ravel(), yy.ravel(), np.zeros(xx.size)))


def legacy_table_mask(points, model, p):
    """The prefilter-free table mask retained as an exact test oracle."""
    points = np.asarray(points, dtype=np.float64)
    origin = np.asarray(model["origin"], dtype=np.float64)
    normal = np.asarray(model["normal"], dtype=np.float64)
    axes = np.asarray(model["axes"], dtype=np.float64)
    grid = float(model["gridSize"])
    cells = {tuple(cell) for cell in model["occupiedCells"]}
    with np.errstate(invalid="ignore"):
        finite = np.isfinite(points).all(axis=1)
        local3 = points - origin
        local2 = np.column_stack((local3 @ axes[0], local3 @ axes[1]))
        keys = np.floor(local2 / grid).astype(np.int64)
        observed = np.fromiter((tuple(key) in cells for key in keys), bool, count=len(points))
        return finite & observed & (np.abs(local3 @ normal) <= float(model.get("distance", p.table_distance)))


class FeatureReuseTests(unittest.TestCase):
    def test_adaptive_density_shortcut_preserves_exact_grid_and_sparse_fallback(self):
        dense = plane(np.arange(64)*.001, np.arange(64)*.001)[:, :2]
        sparse = np.random.default_rng(52).uniform(-20, 20, (4096, 2))
        duplicate = np.zeros((4096, 2))
        mixed = np.vstack((dense[:2048], sparse[:2048]))
        for local in (dense, sparse, duplicate, mixed):
            nearest = cKDTree(local).query(local, k=2, workers=1)[0][:, 1]
            nearest = nearest[np.isfinite(nearest) & (nearest > 1e-9)]
            expected = max(.012, float(3*np.median(nearest))) if len(nearest) else .012
            self.assertEqual(_adaptive_grid(local, .012), expected)

    def test_bounded_parallel_queries_preserve_all_features(self):
        support = plane(np.linspace(-.1, .1, 41), np.linspace(-.1, .1, 41))
        p = Params(query_batch_size=2048)
        for radius in (p.surface_radius, p.axis_radius):
            with patch('algorithms.rebar_v5.features._PCA_QUERY_WORKERS', 1):
                serial = pca_features(support, support, radius, p)
            with patch('algorithms.rebar_v5.features._PCA_QUERY_WORKERS', 4):
                parallel = pca_features(support, support, radius, p)
            for name in serial:
                np.testing.assert_array_equal(serial[name], parallel[name], err_msg=name)

    def setUp(self):
        self.p = Params.from_value({"query_batch_size": 64})
        self.support = np.vstack((
            plane(np.arange(-.10, .11, .01), np.arange(-.10, .11, .01)),
            [[np.nan, 0., 0.]],
        ))
        self.query = np.vstack((self.support[::7], [[np.inf, 0., 0.]]))

    def test_prepared_context_preserves_every_pca_feature_exactly(self):
        direct = pca_features(self.support, self.query, self.p.surface_radius, self.p)
        context = prepare_pca_context(self.support)
        reused = pca_features(
            self.support, self.query, self.p.surface_radius, self.p, context=context,
        )
        self.assertEqual(set(reused), set(direct))
        for name in direct:
            np.testing.assert_array_equal(reused[name], direct[name])

    def test_empty_or_invalid_queries_do_not_build_a_tree(self):
        for query in (np.empty((0, 3)), np.array([[np.nan, 0., 0.]])):
            with patch("algorithms.rebar_v5.features.cKDTree", wraps=cKDTree) as built:
                result = pca_features(self.support, query, self.p.surface_radius, self.p)
            self.assertEqual(built.call_count, 0)
            self.assertFalse(result["valid"].any())

    def test_consistent_faces_builds_one_tree_for_shared_support(self):
        support = self.support[:-1]
        faces = [{"normal": [0., 0., 1.]} for _ in range(20)]
        with patch("algorithms.rebar_v5.scene.fixture_mask", return_value=np.ones(len(support), bool)), \
             patch("algorithms.rebar_v5.features.cKDTree", wraps=cKDTree) as built:
            accepted = _consistent_fixture_normals(support, faces, self.p)
        self.assertEqual(len(accepted), 20)
        self.assertEqual(built.call_count, 1)

    def test_table_mask_distance_prefilter_matches_original_reference(self):
        angle = np.deg2rad(31.)
        axes = np.array([
            [np.cos(angle), np.sin(angle), 0.],
            [-np.sin(angle), np.cos(angle), 0.],
        ])
        model = {
            "origin": [.7, -.4, .2], "normal": [0., 0., 1.],
            "axes": axes.tolist(), "gridSize": .05,
            "occupiedCells": [[0, 0], [2, 2]], "distance": .01,
        }
        rng = np.random.default_rng(20260907)
        points = rng.normal(size=(4096, 3))
        local = np.array([
            [0., 0., 0.],
            [np.nextafter(.10, 0.), np.nextafter(.10, 0.), .01],
            [.10, .10, -.01],
            [np.nextafter(.15, 0.), np.nextafter(.15, 0.), 0.],
            [.50, .50, 4.],
        ])
        origin = np.asarray(model["origin"])
        points = np.vstack((points, origin + local[:, :1] * axes[0] + local[:, 1:2] * axes[1] + local[:, 2:] * [0., 0., 1.],
                            [[np.nan, 0., 0.], [np.inf, 0., 0.]]))
        np.testing.assert_array_equal(
            table_mask(points, model, self.p), legacy_table_mask(points, model, self.p),
        )


if __name__ == "__main__":
    unittest.main()
