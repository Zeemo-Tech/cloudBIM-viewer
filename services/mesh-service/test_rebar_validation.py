import unittest
import numpy as np

from algorithms.rebar_base import RebarPointAttributes
from rebar_validation import (
    TruthScene,
    evaluate,
    evaluate_v5,
    make_truth_scene,
    REBAR,
    FIXTURE,
    TABLE,
)


def truth():
    # Two physical rods, one fixture and one declared ambiguous crossing point.
    return TruthScene(
        np.zeros((8, 3)),
        np.array(
            [REBAR, REBAR, REBAR, REBAR, FIXTURE, FIXTURE, TABLE, REBAR], np.uint8
        ),
        np.array([1, 1, 2, 2, 0, 0, 0, 0], np.uint32),
        np.array([False] * 7 + [True]),
        1,
        {},
        np.array(["a"] * 8),
    )


def attrs(scene, instances):
    n = len(scene)
    return RebarPointAttributes(
        np.array(scene == REBAR, np.uint8),
        np.zeros(n, np.uint16),
        np.array(instances, np.uint32),
        np.array(scene, np.uint8),
        np.zeros(n, np.uint8),
    )


class EvaluatorTests(unittest.TestCase):
    def test_fixture_faces_close_pairs_and_hook_are_geometrically_valid(self):
        t = make_truth_scene()
        tube = t.points[t.case_labels == "fixture_square_tube"]
        # Four faces of a 40mm square tube, entirely above the table.
        self.assertTrue(
            np.allclose(np.max(np.abs(tube[:, 1:] - [-0.26, 0.08]), axis=1), 0.02)
        )
        self.assertGreater(tube[:, 2].min(), 0)
        for first, second, gap in [(3, 4, 0.016), (11, 12, 0.022)]:
            a = t.points[t.instance == first]
            b = t.points[t.instance == second]
            self.assertAlmostEqual(b[:, 1].mean() - a[:, 1].mean(), gap, delta=0.0005)
            self.assertLess(np.ptp(a[:, 1]), 0.0081)
            self.assertGreaterEqual(a[:, 2].min(), 0.026)
        # All arc points lie on one 8mm-radius torus about a 250mm bend.
        arc = t.points[t.case_labels == "hook_180"]
        radial = np.linalg.norm(arc[:, :2] - [0.5, 0.65], axis=1) - 0.25
        self.assertTrue(np.allclose(radial**2 + (arc[:, 2] - 0.03) ** 2, 0.008**2))
        # The end of the first quadrant and start of the next share (.75,.65).
        preceding = t.points[(t.case_labels == "hook_90") & (t.points[:, 0] > 0.74)]
        self.assertLess(
            np.min(np.linalg.norm(preceding[:, None, :] - arc[None, :, :], axis=2)),
            0.006,
        )
        for name, angle in [("inclined_45", 45), ("inclined_60", 60)]:
            points = t.points[t.case_labels == name]
            _, _, axes = np.linalg.svd(
                points - points.mean(axis=0), full_matrices=False
            )
            actual = np.rad2deg(
                np.arctan2(abs(axes[0, 2]), np.linalg.norm(axes[0, :2]))
            )
            self.assertAlmostEqual(actual, angle, delta=1.0)

    def test_all_steel_is_penalized_for_fixture_leakage(self):
        t = truth()
        a = attrs(np.full(8, REBAR, np.uint8), [1] * 8)
        r = evaluate(t, a, 0.01)
        self.assertLess(r["semantic"]["precision"], 1)
        self.assertEqual(r["semantic"]["fixtureLeakage"], 1)

    def test_merged_and_split_predictions_are_reported(self):
        t = truth()
        merged = attrs(t.scene, [9, 9, 9, 9, 0, 0, 0, 0])
        r = evaluate(t, merged, 0.01)
        self.assertGreaterEqual(r["instances"]["merged"], 1)
        split = attrs(t.scene, [1, 3, 2, 2, 0, 0, 0, 0])
        r = evaluate(t, split, 0.01)
        self.assertGreaterEqual(r["instances"]["split"], 1)

    def test_v5_counts_raw_projection_and_intersections_separately(self):
        t = truth()
        scene = np.array([0, REBAR, REBAR, REBAR, FIXTURE, TABLE, 0, 0], np.uint8)
        a = RebarPointAttributes((scene == REBAR).astype(np.uint8), np.zeros(8, np.uint16),
            np.array([0, 1, 2, 2, 0, 0, 0, 0], np.uint32), scene, np.zeros(8, np.uint8))
        class Analysis:
            data = {"intersections": [{"id": 1}, {"id": 2}]}
        result = evaluate_v5(t, a, Analysis(), .01)
        self.assertEqual(result["intersectionCount"], 2)
        self.assertEqual(result["rawSourceProjection"]["pointCount"], 8)
        self.assertEqual(result["semantic"]["sceneConfusionMatrix"][REBAR][0], 2)


if __name__ == "__main__":
    unittest.main()

class V5AcceptanceTests(unittest.TestCase):
    def test_fixture_only_predicted_instance_counts_as_false_positive(self):
        t=truth()
        scene=t.scene.copy();scene[t.scene==FIXTURE]=REBAR
        ids=t.instance.copy();ids[t.scene==FIXTURE]=77
        result=evaluate_v5(t,attrs(scene,ids),type('Analysis',(),{'data':{'intersections':[]}})(),0)
        self.assertGreaterEqual(result['instances']['predicted'],3)
        self.assertLess(result['instances']['iou50Precision'],.9)

    def test_v5_keeps_ambiguous_rebar_in_semantic_score_but_not_instance_truth(self):
        t = truth()
        # The final point is deliberately ambiguous but still a rebar semantic truth.
        scene = np.array([REBAR, REBAR, REBAR, REBAR, FIXTURE, FIXTURE, TABLE, 0], np.uint8)
        a = attrs(scene, [1, 1, 2, 2, 0, 0, 0, 99])

        class Analysis:
            data = {"intersections": []}

        legacy = evaluate(t, a, 0.01)
        v5 = evaluate_v5(t, a, Analysis(), 0.01)
        self.assertEqual(legacy["semantic"]["recall"], 1.0)
        self.assertEqual(v5["semantic"]["recall"], 4 / 5)
        self.assertEqual(v5["instanceTruth"]["truthIdZeroNoInstanceTruthCount"], 1)
        self.assertEqual(v5["instances"]["groundTruth"], 2)
        self.assertNotIn("99", v5["instances"]["perGroundTruthIoU"])

    def test_v5_reports_deletion_rates_and_fixed_acceptance_checks(self):
        t = truth()
        scene = np.array([TABLE, FIXTURE, REBAR, REBAR, FIXTURE, FIXTURE, TABLE, REBAR], np.uint8)
        a = attrs(scene, [1, 1, 2, 2, 0, 0, 0, 0])

        class Analysis:
            data = {"intersections": []}

        result = evaluate_v5(t, a, Analysis(), 0.01)
        self.assertEqual(result["semantic"]["tableSteelDeletionRate"], 1 / 5)
        self.assertEqual(result["semantic"]["fixtureSteelDeletionRate"], 1 / 5)
        self.assertIn("basePrecisionMin", result["acceptance"]["checks"])
        self.assertFalse(result["acceptance"]["passed"])

    def test_v5_base_precision_includes_fixture_false_positives_and_is_bounded(self):
        t = truth()
        a = attrs(np.full(8, REBAR, np.uint8), [1] * 8)

        class Analysis:
            data = {"intersections": []}

        result = evaluate_v5(t, a, Analysis(), 0.01)
        precision = result["acceptance"]["checks"]["basePrecisionMin"]["value"]
        self.assertGreaterEqual(precision, 0.0)
        self.assertLessEqual(precision, 1.0)
        self.assertLess(precision, 0.99)
        self.assertFalse(result["acceptance"]["checks"]["basePrecisionMin"]["passed"])
        self.assertFalse(result["acceptance"]["passed"])

    def test_validation_matrix_freezes_parameter_and_holdout_dimensions(self):
        from rebar_validation import V5_VALIDATION_CASES, _with_density, source_fingerprint

        parameter = [case for case in V5_VALIDATION_CASES if case["split"] == "parameter"]
        holdout = [case for case in V5_VALIDATION_CASES if case["split"] == "holdout"]
        self.assertGreaterEqual(len(parameter), 3)
        self.assertTrue(holdout)
        self.assertTrue(any(not case["topArcs"] for case in V5_VALIDATION_CASES))
        self.assertTrue(any(case["density"] < 1 for case in V5_VALIDATION_CASES))
        sparse = _with_density(make_truth_scene(), 0.5, 7)
        self.assertLess(len(sparse.points), len(make_truth_scene().points))
        fingerprint = source_fingerprint("geometric-v5")
        self.assertEqual(len(fingerprint["sha256"]), 64)
        self.assertTrue(fingerprint["files"])


if __name__ == "__main__":
    unittest.main()
