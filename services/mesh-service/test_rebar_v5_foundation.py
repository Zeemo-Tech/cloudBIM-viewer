"""Focused executable contracts for the bounded V5 foundation helpers."""
from __future__ import annotations

import copy
import tempfile
import unittest

import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.contracts import REBAR, TABLE
from algorithms.rebar_v5.features import denoise, pca_features
from algorithms.rebar_v5.intersections import compute_intersections
from algorithms.rebar_v5.spatial import SpatialStore
from rebar_validation import make_truth_scene


def params(**changes):
    defaults = {
        "block_size": 0.1,
        "block_point_limit": 4,
        "neighbourhood_point_limit": 1000,
        "query_batch_size": 2,
        "noise_radius": 0.025,
    }
    defaults.update(changes)
    return Params.from_value(defaults)


class SpatialFoundationTest(unittest.TestCase):
    def test_source_indices_chunks_and_dense_cores_are_bounded(self):
        p = params()
        xyz = np.array([
            [0.001, 0.001, 0.0], [np.nan, 0.0, 0.0], [0.021, 0.001, 0.0],
            [0.041, 0.001, 0.0], [0.061, 0.001, 0.0], [0.081, 0.001, 0.0],
            [0.091, 0.002, 0.0], [0.095, 0.003, 0.0], [0.099, 0.004, 0.0],
        ])
        with tempfile.TemporaryDirectory() as directory:
            store = SpatialStore(directory, p).build([
                (np.arange(0, 3), xyz[:3]), (np.arange(3, 9), xyz[3:]),
            ])
            self.assertEqual(store.query(np.array([-1, -1, -1]), np.array([1, 1, 1]))["source_index"].tolist(), [0, 2, 3, 4, 5, 6, 7, 8])
            cores = list(store.cores())
            self.assertGreater(len(cores), 1)
            for _, lo, hi in cores:
                self.assertLessEqual(len(store.query(lo, hi)), p.block_point_limit)

    def test_halo_support_matches_full_cloud_across_cell_boundary(self):
        p = params(block_point_limit=8)
        # Plane samples cross x=0.1, so a core on either side needs its halo.
        grid = np.array([[x, y, 0.0] for x in np.arange(0.07, 0.14, 0.01) for y in np.arange(-0.03, 0.04, 0.01)])
        with tempfile.TemporaryDirectory() as directory:
            store = SpatialStore(directory, p).build([(np.arange(len(grid)), grid)])
            for _, lo, hi in store.cores():
                core = store.query(lo, hi)["xyz"]
                halo = store.query(lo - p.halo, hi + p.halo)["xyz"]
                expected = pca_features(grid, core, p.axis_radius, p)
                actual = pca_features(halo, core, p.axis_radius, p)
                np.testing.assert_array_equal(actual["neighbor_count"], expected["neighbor_count"])
                np.testing.assert_array_equal(actual["valid"], expected["valid"])


class FeatureFoundationTest(unittest.TestCase):
    def test_denoise_removes_compact_clusters_but_retains_short_line_and_hook(self):
        p = params()
        compact = np.array([[1.0, 0, 0], [1.004, 0, 0], [1.0, .004, 0], [1.004, .004, 0]])
        short_line = np.array([[2.0, 0, 0], [2.005, 0, 0], [2.010, 0, 0], [2.015, 0, 0]])
        hook = np.array([[3.0, 0, 0], [3.005, 0, 0], [3.010, 0, 0], [3.010, .005, 0]])
        support = np.vstack([compact, short_line, hook])
        noise, suspect = denoise(support, support, p)
        self.assertTrue(noise[:len(compact)].all())
        self.assertFalse(noise[len(compact):].any())
        self.assertFalse(suspect[noise].any())

    def test_denoise_uses_local_density_and_all_queries_are_batched(self):
        p = params(query_batch_size=2)
        dense = np.array([[i * .001, 0, 0] for i in range(16)])
        query = np.vstack([dense, [[.015, .010, 0], [np.nan, 0, 0]]])
        noise, suspect = denoise(dense, query, p)
        self.assertFalse(noise[-2])
        self.assertTrue(suspect[-2])  # gap is large relative to dense local spacing
        self.assertTrue(noise[-1])

    def test_features_ignore_invalid_support_and_keep_invalid_queries_invalid(self):
        p = params()
        plane = np.array([[x, y, 0.0] for x in np.arange(-.02, .03, .01) for y in np.arange(-.02, .03, .01)])
        support = np.vstack([plane, [[np.nan, 0, 0]]])
        query = np.vstack([plane[:4], [[np.inf, 0, 0]]])
        features = pca_features(support, query, .04, p)
        self.assertEqual(len(features["valid"]), len(query))
        self.assertTrue(features["valid"][:-1].any())
        self.assertEqual(features["valid"][-1], 0)
        self.assertEqual(features["neighbor_count"][-1], 0)

    def test_sparse_physical_plane_and_truth_scene_survive_but_fly_points_do_not(self):
        p = params()
        sparse_plane = np.array([[x, y, 0.0] for x in np.arange(-.09, .10, .036) for y in np.arange(-.09, .10, .036)])
        noise, _ = denoise(sparse_plane, sparse_plane, p)
        self.assertFalse(noise.any())
        surface = pca_features(sparse_plane, sparse_plane, p.surface_radius, p)
        self.assertTrue(surface["valid"].all())
        self.assertTrue(np.allclose(surface["neighborhood_radius"], p.halo))

        truth = make_truth_scene()
        flies = np.array([[8.0 + i, 8.0, 8.0] for i in range(12)])
        support = np.vstack([truth.points, flies])
        truth_noise, _ = denoise(support, support, p)
        retained = ~truth_noise[:len(truth.points)]
        for scene in (TABLE, REBAR):
            mask = truth.scene == scene
            self.assertGreaterEqual(retained[mask].mean(), .99)
        self.assertTrue(truth_noise[-len(flies):].all())

    def test_axis_tangent_allows_rank_one_line_and_records_adaptive_radius(self):
        p = params()
        line = np.array([[i * .01, 0, 0] for i in range(7)])
        features = pca_features(line, line, p.axis_radius, p)
        self.assertTrue(features["valid"].all())
        self.assertTrue(np.all(features["linearity"] > .99))
        self.assertTrue(np.allclose(features["normal"], 0))
        self.assertTrue(np.all(features["neighborhood_radius"] <= p.halo))
        self.assertTrue(np.any(np.isclose(features["neighborhood_radius"], p.halo)))


class IntersectionFoundationTest(unittest.TestCase):
    @staticmethod
    def instance(identifier, points, observed=True):
        value = {"id": identifier, "centerline": points}
        if observed:
            value["observedSegments"] = [{"points": points}]
        return value

    def test_multiway_observed_intersection_is_finite_read_only_and_exact(self):
        instances = [
            self.instance(1, [[0, 0, 0], [.1, 0, 0]]),
            self.instance(2, [[.05, -.1, 0], [.05, .1, 0]]),
            self.instance(3, [[.05, 0, -.1], [.05, 0, .1]]),
        ]
        before = copy.deepcopy(instances)
        result = compute_intersections(instances, tolerance=.001)
        self.assertEqual(instances, before)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["instanceIds"], [1, 2, 3])
        self.assertEqual(result[0]["evidence"], "observed-finite-centerlines")
        self.assertTrue(np.isfinite(result[0]["position"]).all())
        self.assertEqual(result[0]["residual"], 0.0)

    def test_no_chain_merge_and_no_centerline_or_over_tolerance_inference(self):
        instances = [
            self.instance(1, [[-.1, 0, 0], [.1, 0, 0]]),
            self.instance(2, [[0, -.1, 0], [0, .1, 0]]),
            self.instance(3, [[.0009, -.1, 0], [.0009, .1, 0]]),
            self.instance(4, [[.0018, -.1, 0], [.0018, .1, 0]]),
        ]
        self.assertEqual(len(compute_intersections(instances, tolerance=.001)), 2)
        invisible = [self.instance(1, [[0, 0, 0], [.1, 0, 0]], observed=False), self.instance(2, [[.05, -.1, 0], [.05, .1, 0]], observed=False)]
        self.assertEqual(compute_intersections(invisible), [])
        offset = [self.instance(1, [[0, 0, 0], [.1, 0, 0]]), self.instance(2, [[.05, -.1, .0010001], [.05, .1, .0010001]])]
        self.assertEqual(compute_intersections(offset, tolerance=.001), [])


if __name__ == "__main__":
    unittest.main()
