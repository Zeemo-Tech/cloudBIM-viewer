from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from algorithms.rebar_base import RebarAnalysis
from algorithms.rebar_geometric_v3 import GeometricV3Adapter


def _analysis(*, noise=()):
    return RebarAnalysis({"algorithmDetails": {
        "parameters": {"plane_distance_threshold": 0.01},
        "plane": {"origin": [0, 0, 0], "normal": [0, 0, 1], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0]},
        "projection": {"instances": [
            {"instance": 5, "direction": 1, "worldStart": [0, 0, .03], "worldEnd": [1, 0, .03], "radius": .05},
            {"instance": 6, "direction": 2, "worldStart": [.5, -.5, .03], "worldEnd": [.5, .5, .03], "radius": .05},
        ]},
        "v3": {"planeFootprint": {"vertices": [[0, 0], [1, 0], [1, 1], [0, 1]]},
               "noise": {"worldPoints": list(noise), "projectionRadius": .1}},
    }})


class GeometricV3Tests(unittest.TestCase):
    def test_projection_has_cross_direction_sentinels_and_precedence(self):
        attrs = GeometricV3Adapter().project_points(np.array([
            [.5, 0, .03], [.2, 0, .03], [.8, .8, 0], [2, 0, 0], [1.5, 0, 0],
        ], float), _analysis(noise=[[2, 0, 0]]))
        self.assertEqual(attrs.rebar_class.tolist(), [2, 1, 0, 0, 0])
        self.assertEqual(attrs.rebar_direction.tolist(), [65535, 1, 0, 0, 0])
        self.assertEqual(attrs.rebar_instance.tolist(), [0xffffffff, 5, 0, 0, 0])
        self.assertEqual(attrs.rebar_flags.tolist(), [1, 0, 0, 0, 0])
        # Rebar wins over plane, plane wins over noise, and the bounded hull
        # prevents the infinite plane from classifying x=1.5.
        self.assertEqual(attrs.scene_class.tolist(), [2, 2, 1, 3, 0])

    def test_same_direction_overlap_selects_nearest_instance_not_intersection(self):
        data = _analysis().data
        data["algorithmDetails"]["projection"]["instances"].append(
            {"instance": 3, "direction": 1, "worldStart": [0, .02, .03], "worldEnd": [1, .02, .03], "radius": .05})
        attrs = GeometricV3Adapter().project_points(np.array([[.2, .018, .03]]), RebarAnalysis(data))
        self.assertEqual(attrs.rebar_class.tolist(), [1])
        self.assertEqual(attrs.rebar_direction.tolist(), [1])
        self.assertEqual(attrs.rebar_instance.tolist(), [3])
        self.assertEqual(attrs.rebar_flags.tolist(), [0])

    def test_descriptor_and_noise_parameters_are_v3_only(self):
        adapter = GeometricV3Adapter(); descriptor = adapter.descriptor
        self.assertEqual((descriptor["id"], descriptor["version"]), ("geometric-v3", "3"))
        visualization = descriptor["visualization"]
        self.assertEqual(visualization["schema"], "rebar-visualization-v1")
        self.assertEqual(visualization["attributes"]["sceneClass"], "SCENE_CLASS")
        self.assertEqual(visualization["values"]["flags"]["intersection"], 1)
        self.assertEqual(visualization["colors"]["directionA"], "#22d3ee")
        effective = adapter.normalize_parameters({"noise_neighbor_count": 4})
        self.assertEqual(effective["noise_neighbor_count"], 4)
        self.assertNotIn("noise_neighbor_count", adapter._v2.normalize_parameters({}))

    def test_noise_detection_is_deterministic_and_serialized(self):
        # Stub v2 so this test isolates v3's sample kNN rule rather than the
        # intentionally demanding geometric detector.
        base = RebarAnalysis({"algorithmDetails": {"plane": {"origin": [0,0,0], "normal": [0,0,1], "x_axis": [1,0,0], "y_axis": [0,1,0]},
            "point_sets": {"plane_inlier_indices": [0,1,2]}, "projection": {"instances": []}, "parameters": {"plane_distance_threshold": .01}}})
        adapter = GeometricV3Adapter()
        points = np.array([[0,0,0], [.01,0,0], [0,.01,0], [3,3,3]], float)
        params = {**adapter.normalize_parameters({"noise_neighbor_count": 2}), "plane_distance_threshold": .01}
        with mock.patch.object(adapter._v2, "analyze", return_value=base):
            first = adapter.analyze(points, params).data["algorithmDetails"]["v3"]["noise"]
            second = adapter.analyze(points, params).data["algorithmDetails"]["v3"]["noise"]
        self.assertEqual(first, second)
        self.assertEqual(first["worldPoints"], [[3.0, 3.0, 3.0]])
