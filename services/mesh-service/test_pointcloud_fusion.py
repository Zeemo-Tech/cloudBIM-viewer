import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from algorithms.fixture_regions import classify_regions
from algorithms.normal_geometry_classifier import classify_geometry
from algorithms.pointcloud_fusion import REASONS, fuse_classifications
from algorithms.pointcloud_normals import estimate_normals
from algorithms.projection_geometry_classifier import classify_projection
from test_normal_geometry_classifier import make_context, round_tube, square_tube


def _attach_projection(context, *, workers=1):
    report, cache, output = classify_projection(
        context.positions, context.normals, context.normal_valid, workers=workers
    )
    context.projection_class = output["projection_class"]
    context.projection_layer = output["projection_layer"]
    context.projection_cache = cache
    return report


class PointCloudFusionTests(unittest.TestCase):
    def test_decision_contract_reasons_recovery_and_fixture_veto(self):
        # The first nine rows cover every A/B class pair. The final row repeats
        # A=fixture/B=steel inside physical fixture support and must stay fixture.
        a = np.array([1, 1, 1, 2, 2, 2, 3, 3, 3, 2], np.uint8)
        b = np.array([1, 2, 3, 1, 2, 3, 1, 2, 3, 3], np.uint8)
        count = len(a)
        grid = SimpleNamespace(
            points=np.zeros((count, 3), np.float64),
            source_to_cell=np.arange(count, dtype=np.int32),
        )
        features = {
            "linearity": np.zeros(count), "width": np.full(count, .03),
            "length": np.zeros(count), "axis_alignment": np.zeros(count),
            "support_count": np.zeros(count), "axis": np.zeros((count, 3)),
            "axis_center": np.zeros((count, 3)),
        }
        broad_fixture = np.zeros(count, bool)
        broad_fixture[-1] = True
        context = SimpleNamespace(
            geometry_class=a.copy(), projection_class=b.copy(),
            projection_cache={"source_recovered": np.array(
                [0, 0, 0, 0, 0, 1, 0, 0, 1, 1], bool
            )},
            classification_cache={
                "grid": grid, "residual_ids": np.arange(count),
                "features": features, "broad_fixture": broad_fixture,
                "strong_bars": np.empty(0, np.int64), "rebar_tree": None,
                "cell_labels": a.copy(),
            },
        )
        before_a, before_b = context.geometry_class.copy(), context.projection_class.copy()
        axis_recovered = np.zeros(count, bool)
        axis_recovered[4] = True
        with patch("algorithms.pointcloud_fusion.recover_rebar", return_value=axis_recovered):
            report = fuse_classifications(context, workers=1)

        np.testing.assert_array_equal(
            context.fused_class, [1, 1, 1, 1, 3, 3, 3, 3, 3, 2]
        )
        np.testing.assert_array_equal(
            context.fused_reason, [1, 2, 2, 2, 5, 4, 3, 3, 1, 6]
        )
        # Every flag is final steel. It includes B-v2 recovery and any final
        # steel whose B label was not steel; a B=fixture precondition is absent.
        np.testing.assert_array_equal(
            context.fused_recovered, [0, 0, 0, 0, 1, 1, 1, 1, 1, 0]
        )
        self.assertTrue(np.all(context.fused_class[context.fused_recovered == 1] == 3))
        np.testing.assert_array_equal(context.geometry_class, before_a)
        np.testing.assert_array_equal(context.projection_class, before_b)
        self.assertEqual(set(map(int, context.fused_class)), {1, 2, 3})
        self.assertEqual(set(REASONS), {"1", "2", "3", "4", "5", "6"})
        self.assertNotIn("probability", report)

    def test_empty_residual_single_worker_is_a_valid_all_table_result(self):
        axis = np.arange(-.30 + .0015, .30, .003)
        x, y = np.meshgrid(axis, axis)
        base = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
        points = np.vstack((base, base + [.0002, 0., 0.], base + [0., .0002, 0.]))
        context = make_context(points, np.tile([0., 0., 1.], (len(points), 1)))
        classify_geometry(context, workers=1)
        projection_report = _attach_projection(context, workers=1)
        before = (context.positions.copy(), context.geometry_class.copy(),
                  context.projection_class.copy())

        fusion_report = fuse_classifications(context, workers=1)
        region_report, _, regions = classify_regions(
            context.positions, context.fused_class, projection_report,
            context.projection_cache, workers=1,
        )

        self.assertEqual(context.classification_cache["residual_ids"].size, 0)
        self.assertTrue(np.all(context.fused_class == 1))
        self.assertIsNone(context.fused_region)
        self.assertTrue(np.all(regions == 0))
        self.assertEqual(sum(fusion_report["counts"].values()), len(points))
        self.assertEqual(sum(region_report["counts"].values()), len(points))
        np.testing.assert_array_equal(context.positions, before[0])
        np.testing.assert_array_equal(context.geometry_class, before[1])
        np.testing.assert_array_equal(context.projection_class, before[2])

    def test_projection_crossing_recovery_flows_to_fusion(self):
        bar, normals = round_tube(radius=.005, length=.18)
        rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        crossing = bar @ rotation.T + [0., 0., .010]
        context = make_context(
            np.vstack((bar, crossing)), np.vstack((normals, normals @ rotation.T))
        )
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        _attach_projection(context, workers=1)
        fuse_classifications(context, workers=1)

        intersection = np.linalg.norm(context.positions[:, :2], axis=1) < .018
        recovered = context.projection_cache["source_recovered"] & intersection
        self.assertGreater(np.count_nonzero(recovered), 0)
        self.assertTrue(np.all(context.projection_class[recovered] == 3))
        self.assertTrue(np.all(context.fused_class[recovered] == 3))
        self.assertTrue(np.all(context.fused_recovered[recovered] == 1))
        self.assertGreater(np.mean(context.fused_class[intersection] == 3), .95)

    def test_close_parallel_bars_are_restored_without_fixture_leakage(self):
        bar, bar_normals = round_tube()
        nearby_bar = bar + [.014, 0., 0.]
        fixture, fixture_normals = square_tube(half=.012)
        fixture[:, 0] += .075
        context = make_context(
            np.vstack((bar, nearby_bar, fixture)),
            np.vstack((bar_normals, bar_normals, fixture_normals)),
        )
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        _attach_projection(context, workers=1)
        fuse_classifications(context, workers=1)

        steel_stop = len(bar) * 2
        self.assertTrue(np.all(context.projection_class[:steel_stop] == 2))
        self.assertTrue(np.all(context.fused_class[:steel_stop] == 3))
        self.assertTrue(np.all(context.fused_recovered[:steel_stop] == 1))
        self.assertTrue(np.all(context.fused_class[steel_stop:] == 2))
        self.assertFalse(np.any(context.fused_recovered[steel_stop:]))


if __name__ == "__main__":
    unittest.main()
