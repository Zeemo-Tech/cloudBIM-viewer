"""Bounded long-body repairs need independent, distributed surface evidence."""
from pathlib import Path
import unittest
import numpy as np
from algorithms.rebar_control_net import _review_long_body_worker, _polyline_distances
from rebar_prior_axis import fit_prior_axis

FIXTURE = Path(__file__).resolve().parents[2] / 'docs/research/assets/rebar-bow-audit-2026-09-15/fixture-11.npz'

class LongBodyReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with np.load(FIXTURE) as data:
            cls.data = dict(data)
        cls.unit = {'length': float(cls.data['length']), 'radius': float(cls.data['radius'])}

    def review(self, held=None, normals=None):
        f = self.data
        return _review_long_body_worker(f['points'], self.unit,
            f['validationBody'] if held is None else held,
            f['validationNormals'] if normals is None else normals, f['baselineCurve'])

    def test_same_real_candidate_points_recover_independently_observed_local_bow(self):
        model = self.review()
        self.assertIsNotNone(model)
        self.assertEqual(model['surfaceReview']['splineCoefficients'], 10)
        error = np.abs(_polyline_distances(self.data['validation'], model['curve'])-self.unit['radius'])
        self.assertLess(np.median(error), .0008)
        self.assertGreater(np.mean(error <= .00144), .85)
        self.assertAlmostEqual(np.linalg.norm(np.diff(model['curve'], axis=0), axis=1).sum(), self.unit['length'])
        self.assertLessEqual(model['surfaceReview']['maxDisplacementM'], .008)

    def test_a_local_validation_patch_cannot_certify_a_whole_body(self):
        mask = (self.data['validationBody'][:, 0] > 4.3) & (self.data['validationBody'][:, 0] < 4.85)
        self.assertIsNone(self.review(self.data['validationBody'][mask], self.data['validationNormals'][mask]))

    def test_training_bend_cannot_override_a_well_supported_validation_axis(self):
        curve = self.data['baselineCurve']
        stations, angles = np.meshgrid(np.linspace(.02, .98, 240), np.linspace(0., 2*np.pi, 24, endpoint=False), indexing='ij')
        centers = np.column_stack([np.interp(stations.ravel(), np.linspace(0, 1, len(curve)), curve[:, i]) for i in range(3)])
        normals = np.column_stack((np.zeros(stations.size), np.cos(angles.ravel()), np.sin(angles.ravel())))
        held = centers + self.unit['radius']*normals
        self.assertIsNone(self.review(held, normals))

    def test_unoriented_normals_do_not_change_acceptance(self):
        a = self.review()
        b = self.review(normals=-self.data['validationNormals'])
        self.assertIsNotNone(b)
        np.testing.assert_array_equal(a['curve'], b['curve'])
        self.assertEqual(a['surfaceReview'], b['surfaceReview'])

    def test_axis_capacity_is_bounded(self):
        f = self.data
        for capacity in [True, 6.0, 0, 7, 100]:
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                fit_prior_axis(f['points'], f['start'], f['tangent'], self.unit['length'],
                               self.unit['radius'], spline_coefficients=capacity)

if __name__ == '__main__':
    unittest.main()
