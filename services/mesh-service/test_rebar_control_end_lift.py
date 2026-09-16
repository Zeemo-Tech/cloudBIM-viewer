"""Small observed end bends must survive whole-body smoothing without chasing clutter."""
import unittest
import numpy as np
from scipy.spatial import cKDTree
from algorithms.rebar_control_net import _refine_straight_ends, _polyline_distances
from test_rebar_control_net import tube


class ObservedEndLiftTests(unittest.TestCase):
    def refine(self, length=4., shift=.006, side='start', rotation=None,
               missing=False, owned=False, flat=False, bend_span=.5, attached_ends=()):
        points, normals = tube([0, 0, 0], [length, 0, 0], along=900, around=20)
        position = points[:, 0] if side == 'start' else length-points[:, 0]
        fade = np.maximum(1-position/bend_span, 0.)
        points[:, 2] += shift*fade**2
        slope = -2*shift/bend_span*fade*(1 if side == 'start' else -1)
        normals[:, 0] = -normals[:, 2]*slope
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        if flat:
            points[:, 2] = .004+shift*fade**2
            normals[:] = [0., 0., 1.]
        if missing:
            points, normals = points[position > .25], normals[position > .25]
        curve = np.column_stack((np.linspace(0, length, 17), np.zeros((17, 2))))
        direction = np.array([1., 0., 0.])
        if rotation is not None:
            points, normals, curve = points@rotation.T, normals@rotation.T, curve@rotation.T
            direction = rotation@direction
        owner = np.full(len(points), 7 if owned else 0, np.uint32)
        model = {'curve': curve.copy(), 'unit': {'kind': 'straight', 'length': length,
                 'radius': .004, 'direction': direction, 'curveAttachedEnds': attached_ends}, 'tolerance': .00144,
                 'axisEvidenceRangeM': [.02, length-.02], 'candidateIds': np.arange(len(points))}
        originals = points.copy(), normals.copy(), owner.copy()
        _refine_straight_ends(points, normals, cKDTree(points), np.arange(len(points)), owner, model)
        for original, current in zip(originals, (points, normals, owner)):
            np.testing.assert_array_equal(original, current)
        return model, curve, points

    def test_lift_at_either_observed_end_is_recovered_locally(self):
        for length in (1.15, 4.):
            for side in ('start', 'end'):
                with self.subTest(length=length, side=side):
                    model, original, points = self.refine(length=length, side=side)
                    self.assertIn('axisRefinement', model)
                    tip = 0 if side == 'start' else -1
                    self.assertGreater(model['curve'][tip, 2], .0055)
                    position = points[:, 0] if side == 'start' else length-points[:, 0]
                    local = points[(position > .015) & (position < .45)]
                    error = abs(_polyline_distances(local, model['curve'])-.004)
                    self.assertLess(np.median(error), .0002)
                    self.assertGreater(np.mean(error <= .00144), .95)
                    np.testing.assert_array_equal(model['curve'][7:10], original[7:10])
                    self.assertAlmostEqual(np.linalg.norm(np.diff(model['curve'], axis=0), axis=1).sum(), length, places=12)

    def test_equivalent_rotated_scan_has_same_correction(self):
        from scipy.spatial.transform import Rotation
        rotation = Rotation.from_rotvec([.5, -.3, .7]).as_matrix()
        model, _, _ = self.refine()
        rotated, _, _ = self.refine(rotation=rotation)
        self.assertIn('axisRefinement', rotated)
        np.testing.assert_allclose(rotated['curve'], model['curve']@rotation.T, atol=1e-7)

    def test_straight_missing_owned_flat_and_large_offsets_do_not_move_axis(self):
        for kwargs in ({'shift': 0.}, {'missing': True}, {'owned': True},
                       {'flat': True}, {'shift': .015}):
            with self.subTest(kwargs=kwargs):
                model, original, _ = self.refine(**kwargs)
                self.assertNotIn('axisRefinement', model)
                np.testing.assert_array_equal(model['curve'], original)

    def test_short_tip_bend_is_not_diluted_by_the_normal_inner_body(self):
        for length in (1.15, 4.):
            for side in ('start', 'end'):
                with self.subTest(length=length, side=side):
                    model, original, points = self.refine(length=length, shift=.006,
                                                         side=side, bend_span=.2)
                    self.assertIn('axisRefinement', model)
                    event = model['axisRefinement']['ends'][0]
                    self.assertEqual(event['windowSelection'], 'bounded-local-tip-review')
                    self.assertLessEqual(event['correctionSpanM'], .25)
                    position = points[:, 0] if side == 'start' else length-points[:, 0]
                    local = points[(position > .015) & (position < .15)]
                    self.assertLess(np.median(abs(_polyline_distances(local, model['curve'])-.004)), .0004)
                    interior = model['curve'][(model['curve'][:, 0] > .3) & (model['curve'][:, 0] < length-.3)]
                    np.testing.assert_array_equal(interior[:, 1:], 0.)
                    self.assertAlmostEqual(np.linalg.norm(np.diff(model['curve'], axis=0), axis=1).sum(), length, places=12)

    def test_local_sampling_has_no_duplicate_vertices_after_rotation(self):
        from scipy.spatial.transform import Rotation
        rotation = Rotation.from_rotvec([.5, -.3, .7]).as_matrix()
        model, _, _ = self.refine(length=4.19395908, side='end', bend_span=.2, rotation=rotation)
        self.assertIn('axisRefinement', model)
        self.assertTrue(np.isfinite(model['curve']).all())
        self.assertGreater(np.linalg.norm(np.diff(model['curve'], axis=0), axis=1).min(), 1e-8)

    def test_local_tip_review_preserves_hook_attachment(self):
        model, original, _ = self.refine(bend_span=.2, attached_ends=('start',))
        self.assertNotIn('axisRefinement', model)
        np.testing.assert_array_equal(model['curve'], original)
        model, original, _ = self.refine(bend_span=.2, side='end', attached_ends=('start',))
        self.assertIn('axisRefinement', model)
        np.testing.assert_array_equal(model['curve'][:2], original[:2])


if __name__ == '__main__':
    unittest.main()
