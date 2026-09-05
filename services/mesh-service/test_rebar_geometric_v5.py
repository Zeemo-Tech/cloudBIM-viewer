import unittest
import numpy as np

from algorithms.rebar_v5 import GeometricV5Adapter
from rebar_validation import make_truth_scene


class GeometricV5IntegrationTests(unittest.TestCase):
    def test_physical_truth_scene_emits_finite_observed_geometry(self):
        scene = make_truth_scene(seed=20260905, top_arcs=True)
        adapter = GeometricV5Adapter()
        analysis = adapter.analyze(scene.points, adapter.normalize_parameters({"detection_point_limit": 100000}))
        try:
            data = analysis.data
            self.assertEqual(data["schema"], "rebar-analysis-v2")
            self.assertTrue(data["instances"])
            self.assertTrue(data["algorithmDetails"]["projection"]["instances"])
            for item in data["instances"]:
                self.assertGreater(item["radius"], 0)
                self.assertTrue(np.isfinite(np.asarray(item["centerline"])).all())
                for segment in item["observedSegments"]:
                    self.assertTrue(np.isfinite(np.asarray(segment["points"])).all())
        finally:
            adapter.close(analysis)


if __name__ == "__main__":
    unittest.main()
