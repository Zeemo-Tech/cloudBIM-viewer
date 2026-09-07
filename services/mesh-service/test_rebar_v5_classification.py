import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from algorithms.rebar_base import RebarAnalysis
from algorithms.rebar_v5.contracts import FIXTURE, REBAR, Params
from algorithms.rebar_v5.pipeline import classify, finalize_raw_ownership, projection_entries


def _face():
    return {
        "origin": [-0.1, 0.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
        "axes": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "halfExtent": [0.2, 0.1],
        "gridSize": 0.25,
        "occupiedCells": [[0, 0]],
        "confidence": 0.8,
    }


def _analysis(instances, faces=None):
    # These tests exercise opt-in competitive ownership, not debug stage masks.
    params = Params(ownership_review_enabled=True)
    return RebarAnalysis({
        "algorithmDetails": {
            "parameters": vars(params),
            "plane": None,
            "fixture": {"surfaces": faces or [_face()], "bolts": []},
            "projection": {"instances": projection_entries(instances, params)},
        }
    })


def _instance(identifier, start, end, radius):
    return {
        "id": identifier,
        "directionId": 1,
        "radius": radius,
        "centerline": [start, end],
        "observedSegments": [{"points": [start, end]}],
    }


class V5ClassificationTests(unittest.TestCase):
    def test_finite_fixture_face_beats_poor_tube_fit_but_not_true_nearby_bar(self):
        # Both points lie on the same observed finite face.  The first is a
        # planar square-tube strip only half-way through the allowed cylinder
        # residual; the second is an actual cylinder surface observation.
        points = np.array([[-0.04, 0.006, 0.0], [0.04, 0.006, 0.0]])
        features = {
            "surface_planarity": np.ones(2),
            "surface_valid": np.ones(2, dtype=np.uint8),
            "surface_normal": np.array([[0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]),
            "surface_normal_valid": np.ones(2, dtype=np.uint8),
            "axis_linearity": np.ones(2),
            "axis_valid": np.ones(2, dtype=np.uint8),
            "axis_tangent": np.tile([1.0, 0.0, 0.0], (2, 1)),
        }
        instances = [
            _instance(1, [-0.08, 0.0, 0.0], [-0.01, 0.0, 0.0], 0.004),
            _instance(2, [0.01, 0.01, 0.0], [0.08, 0.01, 0.0], 0.004),
        ]

        for offset in (.006, .004):
            # An exact radial residual is still insufficient when the measured
            # surface normal disagrees with the cylinder's radial normal.
            points[0, 1] = offset
            for ordered in (instances, list(reversed(instances))):
                attrs, _ = classify(points, _analysis(ordered), features)
                self.assertEqual(attrs.scene_class.tolist(), [FIXTURE, REBAR])
                self.assertEqual(attrs.rebar_instance.tolist(), [0, 2])

    def test_face_selection_uses_valid_normal_evidence_not_input_order(self):
        point = np.array([[-0.04, 0.006, 0.0]])
        features = {
            "surface_planarity": np.ones(1),
            "surface_valid": np.ones(1, dtype=np.uint8),
            "surface_normal": np.array([[0.0, 0.0, 1.0]]),
            "surface_normal_valid": np.ones(1, dtype=np.uint8),
            "axis_linearity": np.zeros(1),
            "axis_valid": np.zeros(1, dtype=np.uint8),
            "axis_tangent": np.zeros((1, 3)),
        }
        z_face = _face()
        x_face = {
            "origin": [-0.04, 0.0, 0.0],
            "normal": [1.0, 0.0, 0.0],
            "axes": [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "halfExtent": [0.1, 0.1],
            "gridSize": 0.25,
            "occupiedCells": [[0, 0]],
            "confidence": 0.8,
        }
        for faces in ([z_face, x_face], [x_face, z_face]):
            attrs, _ = classify(point, _analysis([], faces), features)
            self.assertEqual(attrs.scene_class.tolist(), [FIXTURE])
            self.assertAlmostEqual(float(attrs.class_confidence[0]), .914, places=6)

    def test_raw_ownership_records_confirmed_and_candidate_support_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw-labels.npz"
            np.savez_compressed(
                path,
                rebar_instance=np.array([1] * 7 + [0] * 3, dtype=np.uint32),
                candidate_instance_ids=np.array([1] * 3, dtype=np.uint32),
            )
            runtime = SimpleNamespace(
                feature_chunks=[path], label_chunks={0: path},
                store=SimpleNamespace(count=10), p=Params(),
            )
            analysis = RebarAnalysis({"instances": [{"id": 1, "rawSupportCount": 10}]}, resources=runtime)
            diagnostic = finalize_raw_ownership(analysis)
            item = analysis.data["instances"][0]
            self.assertEqual(diagnostic["removedInstanceCount"], 0)
            self.assertEqual(item["confirmedSupportCount"], 7)
            self.assertEqual(item["candidateSupportCount"], 3)
            self.assertEqual(item["semanticSupportCount"], 10)


if __name__ == "__main__":
    unittest.main()
