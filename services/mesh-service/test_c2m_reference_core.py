"""Automated tests for C2M computation contracts and reference geometry core."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from unittest import mock

import numpy as np
import open3d as o3d
from pydantic import ValidationError

import main as mesh_main

from algorithms.c2m_distance import (
    colorize_mesh_by_signed_distance,
    compute_signed_scan_to_mesh_distances,
    compute_statistics,
)
from main import (
    C2MParams,
    C2MRecolorRequest,
    C2MRequest,
    _HEAVY_TASK_GATE,
    _c2m_visualization,
    _single_heavy_task,
    c2m_compute,
    c2m_recolor,
)


def _make_upward_plane(global_origin: np.ndarray) -> o3d.geometry.TriangleMesh:
    vertices = np.array(
        [
            [-1.0, -1.0, 0.0],
            [1.0, -1.0, 0.0],
            [1.0, 1.0, 0.0],
            [-1.0, 1.0, 0.0],
        ],
        dtype=np.float64,
    ) + global_origin
    triangles = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    return o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(vertices),
        o3d.utility.Vector3iVector(triangles),
    )


class ReferenceDistanceCoreTests(unittest.TestCase):
    def test_signed_scan_to_triangle_distance_is_chunked_and_rebased(self):
        origin = np.array([100_000.0, 200_000.0, 300_000.0], dtype=np.float64)
        mesh = _make_upward_plane(origin)
        points = origin + np.array(
            [
                [0.0, 0.0, 0.020],
                [0.5, 0.5, -0.030],
                [-0.5, 0.25, 0.0],
            ],
            dtype=np.float64,
        )

        result = compute_signed_scan_to_mesh_distances(mesh, points, chunk_size=1)

        self.assertEqual(result.dtype, np.float64)
        np.testing.assert_allclose(result, [0.020, -0.030, 0.0], atol=1e-6)

    def test_rejects_degenerate_triangles(self):
        mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(np.zeros((3, 3), dtype=np.float64)),
            o3d.utility.Vector3iVector(np.array([[0, 1, 2]], dtype=np.int32)),
        )
        with self.assertRaisesRegex(ValueError, "degenerate"):
            compute_signed_scan_to_mesh_distances(mesh, np.zeros((1, 3)))


class StatisticsTests(unittest.TestCase):
    def test_absolute_metrics_do_not_cancel_symmetric_deviation(self):
        distances = np.array([-0.1, -0.05, 0.0, 0.05, 0.1], dtype=np.float64)

        result = compute_statistics(distances, 1.0, 20, tolerance=0.05)["stats"]

        self.assertAlmostEqual(result["mean"], 0.0)
        self.assertAlmostEqual(result["meanAbs"], 0.06)
        self.assertAlmostEqual(result["rmse"], np.sqrt(0.005))
        self.assertAlmostEqual(result["p95Abs"], 0.1)
        self.assertAlmostEqual(result["withinToleranceRatio"], 0.6)

    def test_histogram_reports_values_outside_its_symmetric_range(self):
        distances = np.array([-2.0, -1.0, 0.0, 1.0, 2.0], dtype=np.float64)

        histogram = compute_statistics(distances, 1.0, 4)["histogram"]

        self.assertEqual(sum(histogram["counts"]), 3)
        self.assertEqual(histogram["overflowCount"], 2)

    def test_histogram_and_overflow_account_for_every_raw_distance(self):
        distances = np.array([-0.3, -0.1, 0.0, 0.1, 0.3], dtype=np.float64)

        result = compute_statistics(distances, 0.1, 10, tolerance=0.1)

        self.assertEqual(sum(result["histogram"]["counts"]), 3)
        self.assertEqual(result["histogram"]["overflowCount"], 2)
        self.assertAlmostEqual(result["stats"]["withinToleranceRatio"], 3 / 5)
        self.assertAlmostEqual(result["stats"]["meanAbs"], 0.16)

    def test_statistics_reject_non_finite_distances(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            compute_statistics(np.array([0.0, np.nan]), 0.1, 10)


class ColorContractTests(unittest.TestCase):
    def test_zero_is_green_and_out_of_range_is_exact_dark_gray(self):
        mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(
                np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
            ),
            o3d.utility.Vector3iVector(np.array([[0, 1, 2]], dtype=np.int32)),
        )

        colorize_mesh_by_signed_distance(mesh, np.array([0.0, -0.2, 0.2]), 0.1, 0.05)

        colors = np.asarray(mesh.vertex_colors)
        np.testing.assert_allclose(colors[0], np.array([0x00, 0xC8, 0x53]) / 255.0)
        np.testing.assert_allclose(colors[1:], np.full((2, 3), 0x3A / 255.0))

    def test_equal_tolerance_and_color_limit_is_supported_without_hidden_clamp(self):
        mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(
                np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
            ),
            o3d.utility.Vector3iVector(np.array([[0, 1, 2]], dtype=np.int32)),
        )

        colorize_mesh_by_signed_distance(mesh, np.array([-0.1, 0.0, 0.1]), 0.1, 0.1)

        np.testing.assert_allclose(
            np.asarray(mesh.vertex_colors),
            np.array([[0x00, 0xBC, 0xD4], [0x00, 0xC8, 0x53], [0xFF, 0xD6, 0x00]]) / 255.0,
        )

    def test_colorization_rejects_distance_vertex_length_mismatch(self):
        mesh = _make_upward_plane(np.zeros(3))
        with self.assertRaisesRegex(ValueError, "vertex count"):
            colorize_mesh_by_signed_distance(mesh, np.array([0.0]), 0.1, 0.05)


class VisualizationContractTests(unittest.TestCase):
    def test_default_histogram_matches_default_color_range(self):
        params = C2MParams()

        self.assertEqual(params.max_colormap_distance, 0.10)
        self.assertEqual(params.max_histogram_distance, 0.10)
        self.assertEqual(params.smoothing_iterations, 0)
        self.assertFalse(params.normal_constraint_enabled)
        self.assertTrue(params.normal_half_space_only)
        self.assertEqual(_c2m_visualization(params), {
            "maxColormapDistance": 0.10,
            "maxHistogramDistance": 0.10,
            "histogramBins": 50,
            "toleranceLimit": 0.05,
            "colorDistanceField": "raw",
            "smoothingIterations": 0,
            "smoothingStrength": 0.5,
        })

    def test_tolerance_cannot_exceed_color_range(self):
        with self.assertRaises(ValidationError):
            C2MRecolorRequest(
                distances_path="/tmp/distances.bin",
                mesh_path="/tmp/mesh.ply",
                max_colormap_distance=0.05,
                tolerance_limit=0.06,
            )

    def test_histogram_bins_are_bounded(self):
        with self.assertRaises(ValidationError):
            C2MParams(histogram_bins=1000)

    def test_non_finite_and_unsafe_dormant_parameters_are_rejected(self):
        for kwargs in (
            {"voxel_size": np.nan},
            {"max_colormap_distance": np.inf},
            {"smoothing_iterations": 1},
            {"normal_constraint_enabled": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                C2MParams(**kwargs)

    def test_alignment_matrix_has_exact_finite_length(self):
        common = {"scan_path": "/tmp/scan.las", "mesh_path": "/tmp/mesh.ply"}
        with self.assertRaises(ValidationError):
            C2MRequest(**common, alignment_matrix=[1.0] * 15)
        with self.assertRaises(ValidationError):
            C2MRequest(**common, alignment_matrix=[1.0] * 15 + [np.nan])

    def test_recolor_recomputes_histogram_and_tolerance_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mesh_path = f"{temp_dir}/mesh.ply"
            distances_path = f"{temp_dir}/distances.bin"
            mesh = o3d.geometry.TriangleMesh(
                o3d.utility.Vector3dVector(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)),
                o3d.utility.Vector3iVector(np.array([[0, 1, 2]], dtype=np.int32)),
            )
            self.assertTrue(o3d.io.write_triangle_mesh(mesh_path, mesh))
            np.array([-0.2, 0.0, 0.2], dtype=np.float32).tofile(distances_path)
            previous_output_dir = mesh_main.C2M_OUTPUT_DIR
            mesh_main.C2M_OUTPUT_DIR = temp_dir
            try:
                result = c2m_recolor(C2MRecolorRequest(
                    distances_path=distances_path,
                    mesh_path=mesh_path,
                    max_colormap_distance=0.1,
                    max_histogram_distance=0.1,
                    histogram_bins=10,
                    tolerance_limit=0.05,
                    smoothing_iterations=0,
                ))
            finally:
                mesh_main.C2M_OUTPUT_DIR = previous_output_dir

            self.assertIsInstance(result, dict)
            self.assertEqual(result["histogram"]["overflowCount"], 2)
            self.assertAlmostEqual(result["stats"]["withinToleranceRatio"], 1 / 3)
            self.assertEqual(result["visualization"]["maxHistogramDistance"], 0.1)
            self.assertEqual(result["visualization"]["colorDistanceField"], "raw")
            self.assertEqual(result["visualization"]["smoothingIterations"], 0)
            self.assertTrue(os.path.isfile(result["coloredPlyPath"]))
            self.assertEqual(stat.S_IMODE(os.stat(result["coloredPlyPath"]).st_mode), 0o660)

    def test_recolor_rejects_malformed_or_non_finite_distance_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mesh_path = f"{temp_dir}/mesh.ply"
            distances_path = f"{temp_dir}/distances.bin"
            mesh = _make_upward_plane(np.zeros(3))
            self.assertTrue(o3d.io.write_triangle_mesh(mesh_path, mesh))

            with open(distances_path, "wb") as distances_file:
                distances_file.write(b"bad")
            response = c2m_recolor(C2MRecolorRequest(
                distances_path=distances_path,
                mesh_path=mesh_path,
            ))
            self.assertEqual(response.status_code, 400)

            np.array([0.0, 0.0, np.nan, 0.0], dtype=np.float32).tofile(distances_path)
            response = c2m_recolor(C2MRecolorRequest(
                distances_path=distances_path,
                mesh_path=mesh_path,
            ))
            self.assertEqual(response.status_code, 400)

    def test_recolor_write_failure_leaves_old_ply_and_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mesh_path = f"{temp_dir}/mesh.ply"
            distances_path = f"{temp_dir}/distances.bin"
            old_colored_path = f"{temp_dir}/colored_old.ply"
            mesh = _make_upward_plane(np.zeros(3))
            self.assertTrue(o3d.io.write_triangle_mesh(mesh_path, mesh))
            np.zeros(4, dtype=np.float32).tofile(distances_path)
            with open(old_colored_path, "wb") as old_colored_file:
                old_colored_file.write(b"old-ply")

            previous_output_dir = mesh_main.C2M_OUTPUT_DIR
            mesh_main.C2M_OUTPUT_DIR = temp_dir
            try:
                with mock.patch("open3d.io.write_triangle_mesh", return_value=False):
                    response = c2m_recolor(C2MRecolorRequest(
                        distances_path=distances_path,
                        mesh_path=mesh_path,
                    ))
            finally:
                mesh_main.C2M_OUTPUT_DIR = previous_output_dir

            self.assertEqual(response.status_code, 500)
            with open(old_colored_path, "rb") as old_colored_file:
                self.assertEqual(old_colored_file.read(), b"old-ply")
            self.assertEqual(
                sorted(name for name in os.listdir(temp_dir) if name.startswith(".colored_")),
                [],
            )
            self.assertEqual(
                sorted(name for name in os.listdir(temp_dir) if name.startswith("colored_") and name != "colored_old.ply"),
                [],
            )


class ProfileContractTests(unittest.TestCase):
    def test_reference_profile_is_explicitly_unavailable(self):
        response = c2m_compute(C2MRequest(
            scan_path="/does/not/exist.las",
            mesh_path="/does/not/exist.ply",
            alignment_matrix=list(np.eye(4).T.flatten()),
            params={"profile": "reference"},
        ))

        self.assertEqual(response.status_code, 501)
        payload = json.loads(response.body)
        self.assertEqual(payload["profile"], "reference")
        self.assertEqual(payload["implementedProfiles"], ["quick"])

    def test_unknown_profile_is_rejected(self):
        response = c2m_compute(C2MRequest(
            scan_path="/does/not/exist.las",
            mesh_path="/does/not/exist.ply",
            alignment_matrix=list(np.eye(4).T.flatten()),
            params={"profile": "unexpected"},
        ))

        self.assertEqual(response.status_code, 400)


class HeavyTaskGateTests(unittest.TestCase):
    def test_busy_gate_returns_429_without_waiting(self):
        @_single_heavy_task("test-task")
        def guarded():
            return "completed"

        self.assertTrue(_HEAVY_TASK_GATE.acquire(blocking=False))
        try:
            response = guarded()
        finally:
            _HEAVY_TASK_GATE.release()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "5")

    def test_recolor_endpoint_uses_shared_busy_gate(self):
        self.assertTrue(_HEAVY_TASK_GATE.acquire(blocking=False))
        try:
            response = c2m_recolor(C2MRecolorRequest(
                distances_path="/does/not/exist.bin",
                mesh_path="/does/not/exist.ply",
            ))
        finally:
            _HEAVY_TASK_GATE.release()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "5")

    def test_gate_is_released_after_failure(self):
        @_single_heavy_task("failing-task")
        def failing():
            raise RuntimeError("expected")

        @_single_heavy_task("following-task")
        def following():
            return "completed"

        with self.assertRaisesRegex(RuntimeError, "expected"):
            failing()
        self.assertEqual(following(), "completed")


if __name__ == "__main__":
    unittest.main()
