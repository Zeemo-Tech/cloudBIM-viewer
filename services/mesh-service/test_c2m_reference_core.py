"""Automated tests for C2M computation contracts and reference geometry core."""

from __future__ import annotations

import json
import unittest

import numpy as np
import open3d as o3d

from algorithms.c2m_distance import (
    compute_signed_scan_to_mesh_distances,
    compute_statistics,
)
from main import (
    C2MRecolorRequest,
    C2MRequest,
    _HEAVY_TASK_GATE,
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
