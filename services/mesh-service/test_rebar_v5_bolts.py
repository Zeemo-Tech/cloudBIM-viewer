import unittest
from types import SimpleNamespace

import numpy as np

from algorithms.rebar_v5.bolts import bolt_mask, detect_bolts


P = SimpleNamespace(min_radius=0.002, max_radius=0.012, support_distance=0.002)
FIXTURE = [{
    "origin": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0],
    "axes": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "halfExtent": [0.12, 0.12],
}]


def fixture_plane():
    grid = np.linspace(-0.10, 0.10, 31)
    return np.array([[x, y, 0.0] for x in grid for y in grid])


def shaft(z0=0.003, z1=0.035, radius=0.004, count=144):
    theta = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    z = np.linspace(z0, z1, count // len(theta))
    return np.array([[radius * np.cos(t), radius * np.sin(t), value] for value in z for t in theta])


def head(z=0.035, inner=0.0055, outer=0.010):
    theta = np.linspace(0, 2 * np.pi, 20, endpoint=False)
    radii = np.linspace(inner, outer, 5)
    return np.array([[radius * np.cos(t), radius * np.sin(t), z] for radius in radii for t in theta])


def features(count, shaft_rows):
    tangent = np.zeros((count, 3))
    tangent[shaft_rows, 2] = 1.0
    linearity = np.zeros(count)
    linearity[shaft_rows] = 0.9
    return {"axis_tangent": tangent, "axis_linearity": linearity}


class BoltCandidatesTest(unittest.TestCase):
    def test_headed_bolt_near_finite_fixture_is_confirmed_and_bounded(self):
        plate = fixture_plane()
        body = shaft()
        cap = head()
        # A nearby horizontal rebar must not be consumed by a bolt's local surface mask.
        rebar = np.array([[x, 0.030, 0.025] for x in np.linspace(-0.04, 0.04, 80)])
        points = np.vstack((plate, body, cap, rebar))
        start = len(plate)
        f = features(len(points), np.arange(start, start + len(body)))
        models, pending = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["source"], "fixture-bounded-headed-cylinder-v1")
        self.assertGreater(models[0]["confidence"], 0.5)
        self.assertEqual(pending["pending"], [])
        masked = bolt_mask(points, models, P)
        self.assertGreater(masked[start:start + len(body) + len(cap)].mean(), 0.75)
        self.assertFalse(masked[-len(rebar):].any())

    def test_short_cylinder_without_head_stays_pending(self):
        plate, body = fixture_plane(), shaft()
        points = np.vstack((plate, body))
        f = features(len(points), np.arange(len(plate), len(points)))
        models, diagnostic = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(models, [])
        self.assertIn("short-cylinder-without-wider-observed-head", [item["reason"] for item in diagnostic["pending"]])

    def test_headed_short_cylinder_far_from_fixture_stays_pending(self):
        plate = fixture_plane()
        body = shaft(0.080, 0.112)
        cap = head(0.112)
        points = np.vstack((plate, body, cap))
        f = features(len(points), np.arange(len(plate), len(plate) + len(body)))
        models, diagnostic = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(models, [])
        self.assertIn("short-cylinder-not-fixture-adjacent", [item["reason"] for item in diagnostic["pending"]])


if __name__ == "__main__":
    unittest.main()

class BoltContractTests(unittest.TestCase):
    def test_candidate_budget_is_explicit_not_silently_truncated(self):
        from algorithms.rebar_v5.bolts import MAX_CANDIDATE_POINTS, _components
        with self.assertRaisesRegex(ValueError, "budget exceeded"):
            _components(np.zeros((MAX_CANDIDATE_POINTS + 1, 3)), 0.01)

    def test_wide_nonplanar_head_stays_pending(self):
        plate, body = fixture_plane(), shaft()
        warped = head()
        warped[:, 2] += 0.005 * np.sin(np.arange(len(warped)) * 0.61)
        points = np.vstack((plate, body, warped))
        f = features(len(points), np.arange(len(plate), len(plate) + len(body)))
        models, diagnostic = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(models, [])
        self.assertIn("short-cylinder-head-not-planar-and-wider", [item["reason"] for item in diagnostic["pending"]])

    def test_reversed_fixture_normal_confirms_the_same_physical_bolt(self):
        plate, body, cap = fixture_plane(), shaft(), head()
        points = np.vstack((plate, body, cap))
        f = features(len(points), np.arange(len(plate), len(plate) + len(body)))
        reversed_face = [{**FIXTURE[0], "normal": [0.0, 0.0, -1.0]}]
        models, _ = detect_bolts(points, f, reversed_face, P)
        self.assertEqual(len(models), 1)
        self.assertGreater(models[0]["axis"][2], 0)

    def test_head_hole_is_not_filled_by_observed_cell_mask(self):
        plate, body, cap = fixture_plane(), shaft(), head()
        # This is a missing central head sample, physically inside the old disk mask.
        hole = np.array([[0.0, 0.0, 0.035]])
        points = np.vstack((plate, body, cap, hole))
        f = features(len(points), np.arange(len(plate), len(plate) + len(body)))
        models, _ = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(len(models), 1)
        self.assertFalse(bolt_mask(points, models, P)[-1])


if __name__ == "__main__":
    unittest.main()

class BoltNumericalSafetyTests(unittest.TestCase):
    def test_scattered_wide_candidate_is_pending_without_solver_failure(self):
        plate = fixture_plane()
        # It has the local tangent/linearity of a candidate but no physical circle.
        wide = np.array([[x, y, z] for x, y, z in zip(
            np.linspace(-0.08, 0.08, 30), np.linspace(-0.06, 0.06, 30), np.linspace(0.004, 0.035, 30)
        )])
        points = np.vstack((plate, wide))
        f = features(len(points), np.arange(len(plate), len(points)))
        models, diagnostic = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(models, [])
        self.assertTrue(diagnostic["pending"])

    def test_extremely_thin_line_candidate_is_pending_without_solver_failure(self):
        plate = fixture_plane()
        thin = np.array([[0.0, 0.0, z] for z in np.linspace(0.003, 0.035, 30)])
        points = np.vstack((plate, thin))
        f = features(len(points), np.arange(len(plate), len(points)))
        models, diagnostic = detect_bolts(points, f, FIXTURE, P)
        self.assertEqual(models, [])
        self.assertTrue(diagnostic["pending"])


if __name__ == "__main__":
    unittest.main()

class BoltCircleFitBoundsTests(unittest.TestCase):
    def test_circle_fit_clips_a_large_local_coordinate_initial_value(self):
        from algorithms.rebar_v5.bolts import _fit_circle
        angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        # The old fixed ±30 mm centre bounds rejected this valid x≈70 mm support.
        support = np.c_[0.070 + 0.004 * np.cos(angles), -0.045 + 0.004 * np.sin(angles)]
        fitted = _fit_circle(support, 0.002, 0.010)
        self.assertIsNotNone(fitted)
        centre, radius, residual, ok = fitted
        self.assertTrue(ok)
        self.assertTrue(np.allclose(centre, [0.070, -0.045], atol=1e-4))
        self.assertAlmostEqual(radius, 0.004, delta=1e-4)
        self.assertLess(residual, 1e-5)


if __name__ == "__main__":
    unittest.main()
