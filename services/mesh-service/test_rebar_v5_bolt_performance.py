"""Regression coverage for bounded V5 bolt masking work."""
from __future__ import annotations

import copy
import unittest
import warnings
from unittest import mock

import numpy as np

from algorithms.rebar_v5 import bolts


P = object()


def model(origin=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 2.0), axes=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))):
    return {
        "axisOrigin": origin, "axis": axis, "radialAxes": axes,
        "axialRange": [-0.004, 0.012], "shaftRadius": 0.004,
        "shaft": {"axialGrid": 0.004, "angleBins": 24,
                  "observedCells": [[0, 0], [1, 6], [2, 12]], "residualTolerance": 0.0015},
        # This deliberately occupied cell lies outside the nominal radius.  The
        # cell, rather than head.radius, defines the observed-head semantics.
        "head": {"axial": 0.012, "radius": 0.001, "planeTolerance": 0.0015,
                 "gridSize": 0.003, "occupiedCells": [[10, 0], [-1, -1]]},
    }


def reference_bolt_mask(points, models):
    """Frozen pre-optimization bolt_mask reference; keep its point semantics."""
    cloud = np.asarray(points, dtype=float)
    result = np.zeros(len(cloud), dtype=bool)
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return result
    finite = np.isfinite(cloud).all(axis=1)
    for model in models or []:
        origin, axis, axes = np.asarray(model["axisOrigin"], float), np.asarray(model["axis"], float), np.asarray(model["radialAxes"], float)
        lo, hi, radius = *map(float, model["axialRange"]), float(model["shaftRadius"])
        shaft, head = model["shaft"], model["head"]
        grid, angle_bins = float(shaft["axialGrid"]), int(shaft["angleBins"])
        shaft_cells = {tuple(cell) for cell in shaft["observedCells"]}
        head_cells, head_grid = {tuple(cell) for cell in head["occupiedCells"]}, float(head["gridSize"])
        axis /= max(np.linalg.norm(axis), 1e-12)
        delta = cloud - origin
        axial = delta @ axis
        local = np.column_stack((delta @ axes[0], delta @ axes[1]))
        radial = np.linalg.norm(local, axis=1)
        angle = np.mod(np.arctan2(local[:, 1], local[:, 0]), 2 * np.pi)
        shaft_keys = list(zip(np.floor(axial / grid).astype(int), np.floor(angle / (2 * np.pi) * angle_bins).astype(int)))
        observed_shaft = np.fromiter((key in shaft_cells for key in shaft_keys), bool, count=len(cloud))
        shaft_surface = (axial >= lo - grid) & (axial <= hi + grid) & (np.abs(radial - radius) <= float(shaft["residualTolerance"])) & observed_shaft
        head_keys = [tuple(cell) for cell in np.floor(local / head_grid).astype(int)]
        observed_head = np.fromiter((key in head_cells for key in head_keys), bool, count=len(cloud))
        head_surface = (np.abs(axial - float(head["axial"])) <= float(head["planeTolerance"])) & observed_head
        result |= finite & (shaft_surface | head_surface)
    return result


class BoltMaskPerformanceRegressionTests(unittest.TestCase):
    def test_matches_baseline_for_random_rotated_boundary_and_nonfinite_points(self):
        rng = np.random.default_rng(481516)
        theta = np.deg2rad(37)
        rotation = np.array([[np.cos(theta), 0, np.sin(theta)], [0, 1, 0], [-np.sin(theta), 0, np.cos(theta)]])
        current = model(origin=(0.23, -0.17, 0.41), axis=rotation @ np.array([0.0, 0.0, 2.0]), axes=(rotation[:, 0], rotation[:, 1]))
        local = rng.uniform(-0.04, 0.04, size=(4000, 3))
        boundary = np.array([[0.004, 0, 0.004], [0.004, 0, 0.016], [0.031, 0.001, 0.012], [0.0, 0.0, np.nan], [np.inf, 0, 0]])
        with np.errstate(invalid="ignore"):
            cloud = np.vstack((local @ rotation.T + np.array(current["axisOrigin"]), boundary @ rotation.T + np.array(current["axisOrigin"])))
        old_model = copy.deepcopy(current)
        with warnings.catch_warnings(), np.errstate(invalid="ignore"):
            warnings.simplefilter("ignore", RuntimeWarning)
            expected = reference_bolt_mask(cloud, [old_model])
        actual = bolts.bolt_mask(cloud, [current], P)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(np.asarray(current["axis"]), rotation @ np.array([0.0, 0.0, 2.0]))

    def test_head_observed_cell_is_not_limited_by_head_radius(self):
        current = model()
        point = np.array([[0.031, 0.001, 0.012]])
        self.assertTrue(bolts.bolt_mask(point, [current], P)[0])

    def test_unrelated_blocks_never_reach_point_level_work(self):
        # 100k remote points × 8 models previously made 1.6M Python `in` calls.
        cloud = np.full((100_000, 3), 1_000.0)
        models = [model(origin=(index * 0.1, 0.0, 0.0)) for index in range(8)]
        with mock.patch.object(bolts, "_model_candidates", wraps=bolts._model_candidates) as coarse, \
             mock.patch.object(bolts, "_shaft_cell_membership", wraps=bolts._shaft_cell_membership) as shaft, \
             mock.patch.object(bolts, "_head_cell_membership", wraps=bolts._head_cell_membership) as head:
            self.assertFalse(bolts.bolt_mask(cloud, models, P).any())
        self.assertEqual(coarse.call_count, 0)
        self.assertEqual(shaft.call_count, 0)
        self.assertEqual(head.call_count, 0)
        self.assertEqual(bolts.bolt_candidates(cloud, models, P), [])

    def test_world_aabb_keeps_exact_boundary_and_rotated_model(self):
        theta = np.deg2rad(37)
        rotation = np.array([[np.cos(theta), 0, np.sin(theta)], [0, 1, 0], [-np.sin(theta), 0, np.cos(theta)]])
        current = model(origin=(0.23, -0.17, 0.41), axis=rotation @ np.array([0.0, 0.0, 2.0]), axes=(rotation[:, 0], rotation[:, 1]))
        bounds = bolts._model_aabb(bolts._mask_model(current))
        self.assertIsNotNone(bounds)
        lower, upper = bounds
        self.assertEqual(bolts.bolt_candidates(np.array([lower, upper]), [current], P), [current])

    def test_nextafter_grid_edges_and_utm_translation_match_reference(self):
        local = np.array([
            [np.nextafter(0.030, -np.inf), 0.001, 0.012],
            [0.030, 0.001, 0.012],
            [np.nextafter(0.033, -np.inf), 0.001, 0.012],
            [0.033, 0.001, 0.012],
        ])
        for origin in ((0.0, 0.0, 0.0), (1_000_000.0, -1_000_000.0, 1_000_000.0)):
            current = model(origin=origin)
            cloud = local + np.asarray(origin)
            with warnings.catch_warnings(), np.errstate(invalid="ignore"):
                warnings.simplefilter("ignore", RuntimeWarning)
                expected = reference_bolt_mask(cloud, [copy.deepcopy(current)])
            actual = bolts.bolt_mask(cloud, [current], P)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(bolts.bolt_candidates(cloud, [current], P), [current])

    def test_skew_projection_falls_back_to_retaining_model(self):
        current = model(axis=(0.0, 0.0, 1.0), axes=((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        self.assertIsNone(bolts._model_aabb(bolts._mask_model(current)))
        self.assertEqual(bolts.bolt_candidates(np.full((4, 3), 1_000.0), [current], P), [current])


if __name__ == "__main__":
    unittest.main()
