"""Independent surface corrections must fix biased axes without chasing clutter."""
import unittest
import numpy as np
from scipy.spatial import cKDTree
from algorithms.rebar_control_net import _refine_short_surface
from test_rebar_control_net import tube


class ShortSurfaceTests(unittest.TestCase):
    def refine(self, points, normals, curve, owner=None):
        owner = np.zeros(len(points), np.uint32) if owner is None else owner
        model = {'curve': np.asarray(curve, float), 'unit': {'kind': 'short', 'length': .28,
                 'radius': .004, 'direction': np.array([1., 0., 0.])}, 'tolerance': .00144}
        before = points.copy(), normals.copy(), owner.copy()
        _refine_short_surface(points, normals, cKDTree(points), np.arange(len(points)), owner, model)
        for a, b in zip(before, (points, normals, owner)):
            np.testing.assert_array_equal(a, b)
        return model

    def test_biased_axis_recovers_position_and_tilt(self):
        points, normals = tube([0, 0, 0], [.37, 0, 0], along=160, around=20)
        curve = [[0, .0015, .0004], [.28, -.0015, .0004]]
        model = self.refine(points, normals, curve)
        self.assertIn('axisRefinement', model)
        self.assertLess(np.max(np.abs(model['curve'][:, 1:])), .00015)
        evidence = model['axisRefinement']
        self.assertLess(evidence['validationMedianAfterM'], .2*evidence['validationMedianBeforeM'])
        self.assertGreaterEqual(evidence['improvedValidationBands'], 5)
        self.assertAlmostEqual(np.linalg.norm(np.diff(model['curve'], axis=0)), np.linalg.norm(np.diff(curve, axis=0)))

    def test_correct_axis_stays_exactly_unchanged(self):
        points, normals = tube([0, 0, 0], [.37, 0, 0], along=140, around=20)
        curve = [[0, 0, 0], [.28, 0, 0]]
        model = self.refine(points, normals, curve)
        np.testing.assert_array_equal(model['curve'], curve)
        self.assertNotIn('axisRefinement', model)

    def test_confirmed_neighbour_cannot_pull_axis(self):
        a, an = tube([0, 0, 0], [.37, 0, 0], along=100, around=20)
        b, bn = tube([0, .006, 0], [.37, .006, 0], along=220, around=20)
        owner = np.r_[np.zeros(len(a), np.uint32), np.full(len(b), 7, np.uint32)]
        curve = [[0, 0, 0], [.28, 0, 0]]
        model = self.refine(np.vstack((a, b)), np.vstack((an, bn)), curve, owner)
        np.testing.assert_array_equal(model['curve'], curve)
        self.assertNotIn('axisRefinement', model)

    def test_dense_perpendicular_crossing_does_not_pull_correct_axis(self):
        a, an = tube([0, 0, 0], [.37, 0, 0], along=120, around=20)
        b, bn = tube([.14, -.06, .001], [.14, .06, .001], along=250, around=20)
        curve = [[0, 0, 0], [.28, 0, 0]]
        model = self.refine(np.vstack((a, b)), np.vstack((an, bn)), curve)
        np.testing.assert_array_equal(model['curve'], curve)
        self.assertNotIn('axisRefinement', model)


class StraightEndSurfaceTests(unittest.TestCase):
    def refine(self, points, normals, curve, owner=None):
        from algorithms.rebar_control_net import _refine_straight_ends
        owner = np.zeros(len(points), np.uint32) if owner is None else owner
        model = {'curve': np.asarray(curve, float).copy(),
                 'unit': {'kind': 'straight', 'length': 4., 'radius': .004,
                          'direction': np.array([1., 0., 0.])},
                 'tolerance': .00144, 'axisEvidenceRangeM': [.45, 3.55],
                 'candidateIds': np.arange(len(points))}
        before = points.copy(), normals.copy(), owner.copy()
        _refine_straight_ends(points, normals, cKDTree(points), np.arange(len(points)), owner, model)
        for a, b in zip(before, (points, normals, owner)):
            np.testing.assert_array_equal(a, b)
        return model

    @staticmethod
    def curve(bias=0.):
        x = np.linspace(0., 4., 17)
        y = bias*(np.clip(1-x/.6, 0, 1)**2 + np.clip(1-(4-x)/.6, 0, 1)**2)
        return np.column_stack((x, y, np.zeros_like(x)))

    def test_both_extrapolated_ends_recover_without_moving_interior(self):
        points, normals = tube([0, 0, 0], [4, 0, 0], along=900, around=20)
        curve = self.curve(.007)
        model = self.refine(points, normals, curve)
        self.assertIn('axisRefinement', model)
        self.assertEqual(len(model['axisRefinement']['ends']), 2)
        self.assertLess(np.max(np.abs(model['curve'][:, 1:])), .0002)
        np.testing.assert_array_equal(model['curve'][3:-3], curve[3:-3])
        self.assertAlmostEqual(np.linalg.norm(np.diff(model['curve'], axis=0), axis=1).sum(), 4., places=12)
        for end in model['axisRefinement']['ends']:
            self.assertLess(end['validationMedianAfterM'], .2*end['validationMedianBeforeM'])

    def test_correct_axis_unchanged_at_dense_crossing(self):
        a, an = tube([0, 0, 0], [4, 0, 0], along=600, around=20)
        b, bn = tube([.2, -.05, .001], [.2, .05, .001], along=300, around=20)
        model = self.refine(np.vstack((a, b)), np.vstack((an, bn)), self.curve())
        np.testing.assert_array_equal(model['curve'], self.curve())
        self.assertNotIn('axisRefinement', model)

    def test_missing_end_evidence_does_not_invent_correction(self):
        points, normals = tube([.25, 0, 0], [3.75, 0, 0], along=600, around=20)
        curve = self.curve(.007)
        model = self.refine(points, normals, curve)
        np.testing.assert_array_equal(model['curve'], curve)
        self.assertNotIn('axisRefinement', model)

    def test_confirmed_neighbour_is_not_used(self):
        points, normals = tube([0, .007, 0], [4, .007, 0], along=600, around=20)
        owner = np.full(len(points), 42, np.uint32)
        curve = self.curve()
        model = self.refine(points, normals, curve, owner)
        np.testing.assert_array_equal(model['curve'], curve)
        self.assertNotIn('axisRefinement', model)

    def test_planar_surface_does_not_pull_axis(self):
        x, y = np.meshgrid(np.linspace(0, 4, 700), np.linspace(-.005, .005, 20))
        points = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .003)))
        normals = np.tile([0., 0., 1.], (len(points), 1))
        curve = self.curve(.004)
        model = self.refine(points, normals, curve)
        np.testing.assert_array_equal(model['curve'], curve)
        self.assertNotIn('axisRefinement', model)


class RecoveryExtentTests(unittest.TestCase):
    def resolve(self, long_alternative):
        from algorithms.rebar_control_net import _resolve_short_recovery
        a, an = tube([0, 0, 0], [.28, 0, 0], along=100, around=20)
        start, end = ([-.2, .03, 0], [.5, .03, 0]) if long_alternative else ([0, .03, 0], [.28, .03, 0])
        b, bn = tube(start, end, along=200, around=20)
        points, normals = np.vstack((a, b)), np.vstack((an, bn))
        unit = {'kind': 'short', 'length': .28, 'radius': .004}
        alternatives = [{'curve': np.array([[0., y, 0.], [.28, y, 0.]]),
                         'tolerance': .00144, 'recoveryScore': 1.} for y in (0., .03)]
        candidate = dict(alternatives[0], recoveryAlternatives=alternatives)
        owner = np.zeros(len(points), np.uint32)
        result, reason = _resolve_short_recovery(points, normals, cKDTree(points),
                            np.arange(len(points)), owner, unit, candidate)
        self.assertFalse(owner.any())
        return result, reason

    def test_complete_surface_rejects_sampled_slice_of_long_bar(self):
        result, reason = self.resolve(True)
        self.assertEqual(reason, 'relocated-short-supported')
        np.testing.assert_array_equal(result['curve'][:, 1:], 0.)
        self.assertEqual(result['recoveryEvidence']['validAlternatives'], 1)

    def test_two_actual_short_surfaces_remain_ambiguous(self):
        _, reason = self.resolve(False)
        self.assertEqual(reason, 'ambiguous-short-recovery')


if __name__ == '__main__':
    unittest.main()
