"""Synthetic contract tests for the training-free rebar geometry PoC."""

from __future__ import annotations

import json
import unittest

import numpy as np

from algorithms.rebar_segmentation import (
    InvalidPointCloudError,
    ScaleValidationError,
    segment_rebar_points,
)


HORIZONTAL_OFFSETS = (-0.24, 0.0, 0.24)
VERTICAL_OFFSETS = (-0.30, -0.10, 0.10, 0.30)


def _synthetic_grid(
    *,
    seed: int = 7,
    add_outliers: bool = False,
    add_high_device_rod: bool = False,
    occlude_middle_horizontal: bool = False,
) -> np.ndarray:
    rng = np.random.default_rng(seed)

    plane_x, plane_y = np.meshgrid(
        np.linspace(-0.52, 0.52, 43),
        np.linspace(-0.36, 0.36, 31),
    )
    plane = np.column_stack(
        (
            plane_x.ravel(),
            plane_y.ravel(),
            rng.normal(0.0, 0.00025, plane_x.size),
        )
    )

    bars: list[np.ndarray] = []
    x_samples = np.linspace(-0.46, 0.46, 63)
    for offset in HORIZONTAL_OFFSETS:
        samples = x_samples
        if occlude_middle_horizontal and offset == 0.0:
            samples = samples[np.abs(samples) >= 0.055]
        for cross_offset in (-0.002, 0.0, 0.002):
            bars.append(
                np.column_stack(
                    (
                        samples + rng.normal(0.0, 0.00035, samples.size),
                        np.full(samples.size, offset + cross_offset)
                        + rng.normal(0.0, 0.00025, samples.size),
                        np.full(samples.size, 0.030)
                        + rng.normal(0.0, 0.00035, samples.size),
                    )
                )
            )

    y_samples = np.linspace(-0.34, 0.34, 47)
    for offset in VERTICAL_OFFSETS:
        for cross_offset in (-0.002, 0.0, 0.002):
            bars.append(
                np.column_stack(
                    (
                        np.full(y_samples.size, offset + cross_offset)
                        + rng.normal(0.0, 0.00025, y_samples.size),
                        y_samples + rng.normal(0.0, 0.00035, y_samples.size),
                        np.full(y_samples.size, 0.030)
                        + rng.normal(0.0, 0.00035, y_samples.size),
                    )
                )
            )

    parts = [plane, *bars]
    if add_outliers:
        # Sparse height-band clutter and fully out-of-band RANSAC outliers.
        parts.append(
            np.column_stack(
                (
                    rng.uniform(-0.55, 0.55, 80),
                    rng.uniform(-0.39, 0.39, 80),
                    rng.uniform(0.012, 0.070, 80),
                )
            )
        )
    if add_high_device_rod:
        # A highly linear temporary rod would be a tempting false positive,
        # but the explicit layer-height gate must exclude it from this run.
        samples = np.linspace(-0.50, 0.50, 90)
        for cross_offset in (-0.002, 0.0, 0.002):
            parts.append(
                np.column_stack(
                    (
                        samples,
                        np.full(samples.size, 0.33 + cross_offset),
                        np.full(samples.size, 0.12),
                    )
                )
            )
        parts.append(
            np.column_stack(
                (
                    rng.uniform(-0.70, 0.70, 35),
                    rng.uniform(-0.50, 0.50, 35),
                    rng.uniform(-0.12, 0.16, 35),
                )
            )
        )
    points = np.vstack(parts)
    return points[rng.permutation(points.shape[0])]


def _params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "random_seed": 13,
        "plane_ransac_iterations": 350,
        "min_plane_inliers": 500,
        "min_plane_inlier_ratio": 0.30,
        "pca_radius": 0.034,
        "pca_min_neighbors": 7,
        "min_linearity": 0.52,
        "min_direction_votes": 30,
        "min_axis_directional_support": 20,
        "min_line_support": 30,
        "min_line_length": 0.25,
    }
    params.update(overrides)
    return params


class RebarGridSegmentationTests(unittest.TestCase):
    def test_extracts_orthogonal_parallel_bars_and_spacing(self):
        result = segment_rebar_points(_synthetic_grid(), _params())

        self.assertEqual(result["schema_version"], "rebar-geometric-poc-v1")
        self.assertEqual(len(result["directions"]), 2)
        self.assertEqual(len(result["instances"]), 7)
        self.assertEqual(
            sorted(direction["axis_count"] for direction in result["directions"]),
            [3, 4],
        )
        observed_spacings = sorted(
            spacing
            for direction in result["directions"]
            for spacing in direction["spacings"]
        )
        self.assertEqual(len(observed_spacings), 5)
        np.testing.assert_allclose(observed_spacings[:3], 0.20, atol=0.005)
        np.testing.assert_allclose(observed_spacings[3:], 0.24, atol=0.005)
        json.dumps(result, allow_nan=False)

    def test_noise_and_outliers_are_deterministic(self):
        points = _synthetic_grid(add_outliers=True)

        first = segment_rebar_points(points, _params())
        second = segment_rebar_points(points, _params())

        self.assertEqual(first, second)
        self.assertEqual(len(first["instances"]), 7)
        self.assertGreater(first["plane"]["support_ratio"], 0.45)
        self.assertLess(
            first["diagnostics"]["rebar_support_point_count"],
            first["diagnostics"]["height_band_point_count"],
        )

    def test_height_band_rejects_a_high_temporary_device_rod(self):
        result = segment_rebar_points(
            _synthetic_grid(add_high_device_rod=True),
            _params(max_rebar_height=0.08),
        )

        self.assertEqual(len(result["instances"]), 7)
        self.assertEqual(
            sorted(direction["axis_count"] for direction in result["directions"]),
            [3, 4],
        )

    def test_axial_bridge_keeps_an_occluded_bar_as_one_instance(self):
        result = segment_rebar_points(
            _synthetic_grid(occlude_middle_horizontal=True),
            _params(bridge_gap=0.15),
        )

        self.assertEqual(len(result["instances"]), 7)
        middle = [
            instance
            for instance in result["instances"]
            if abs(instance["axis_offset"]) < 0.01
        ]
        self.assertTrue(middle)
        self.assertTrue(any(instance["length"] > 0.88 for instance in middle))

    def test_crossings_are_soft_assigned_without_merging_directions(self):
        result = segment_rebar_points(_synthetic_grid(), _params())

        self.assertEqual(len(result["instances"]), 7)
        self.assertGreater(result["diagnostics"]["soft_assigned_point_count"], 0)
        first_support = {
            instance["instance_id"]: set(instance["support_point_indices"])
            for instance in result["instances"]
        }
        cross_direction_overlap = any(
            first_support[left["instance_id"]] & first_support[right["instance_id"]]
            for left in result["instances"]
            for right in result["instances"]
            if left["direction_index"] != right["direction_index"]
        )
        self.assertTrue(cross_direction_overlap)


class RebarInputGuardTests(unittest.TestCase):
    def test_rejects_too_sparse_cloud(self):
        sparse_x, sparse_y = np.meshgrid(
            np.linspace(-1.0, 1.0, 15),
            np.linspace(-1.0, 1.0, 15),
        )
        sparse = np.column_stack((sparse_x.ravel(), sparse_y.ravel(), np.zeros(sparse_x.size)))

        with self.assertRaisesRegex(ScaleValidationError, "too sparse"):
            segment_rebar_points(sparse, _params())

    def test_rejects_probable_millimetre_units(self):
        millimetres_mislabelled_as_metres = _synthetic_grid() * 1000.0

        with self.assertRaisesRegex(ScaleValidationError, "verify metre units"):
            segment_rebar_points(millimetres_mislabelled_as_metres, _params())

    def test_rejects_non_finite_coordinates(self):
        points = _synthetic_grid()
        points[0, 2] = np.nan

        with self.assertRaises(InvalidPointCloudError):
            segment_rebar_points(points, _params())

    def test_rejects_an_inverted_point_count_contract(self):
        with self.assertRaisesRegex(
            InvalidPointCloudError,
            "min_point_count must not exceed max_point_count",
        ):
            segment_rebar_points(
                _synthetic_grid(),
                _params(min_point_count=1_000, max_point_count=999),
            )


if __name__ == "__main__":
    unittest.main()
