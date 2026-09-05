import unittest
import numpy as np

from algorithms.rebar_base import RebarPointAttributes
from rebar_validation import (
    TruthScene,
    evaluate,
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


if __name__ == "__main__":
    unittest.main()
