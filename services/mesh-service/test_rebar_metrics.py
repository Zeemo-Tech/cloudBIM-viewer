import unittest
import numpy as np
from scipy.spatial import cKDTree
from rebar_metrics import fit_section, measure_bar
from rebar_deviation import constrained_nearest, local_tangents


class RebarMetricTests(unittest.TestCase):
    def test_circle_fit_is_not_biased_by_uneven_scan_density(self):
        angles = np.r_[np.linspace(0, 2*np.pi, 80, endpoint=False), np.linspace(0, .5, 300)]
        points = np.column_stack((np.zeros(len(angles)), .004*np.cos(angles)+.002, .004*np.sin(angles)-.001))
        observed = fit_section(points, np.zeros(3), np.array([1., 0., 0.]), .004)
        np.testing.assert_allclose(observed, [0, .002, -.001], atol=1e-9)
        self.assertGreater(np.linalg.norm(points.mean(axis=0)-observed), .002)

    def test_small_visible_arc_cannot_claim_a_center(self):
        angles = np.linspace(0, np.pi/2, 100)
        points = np.column_stack((np.zeros(100), .004*np.cos(angles), .004*np.sin(angles)))
        self.assertIsNone(fit_section(points, np.zeros(3), np.array([1., 0., 0.]), .004))

    def _bar(self, center):
        angles = np.linspace(0, 2*np.pi, 40, endpoint=False)
        sections = []
        for x in np.linspace(0, 1, 16):
            y, z = center(x)
            sections.append(np.column_stack((np.full(40, x), y+.004*np.cos(angles), z+.004*np.sin(angles))))
        points = np.concatenate(sections)
        return measure_bar(np.array([-.03,.01,np.nan]), np.array([[0,0,0],[1,0,0],[.5,0,0]]), [('u',np.zeros(3),np.array([1.,0,0]))], points, radii={'u':.004})

    def test_translation_and_tilt_do_not_become_bending(self):
        # Signed displacement crosses zero: magnitude-based detrending gives a false bow.
        result = self._bar(lambda x: (.02*(x-.5), .003))
        self.assertAlmostEqual(result['surface']['maxAbs'], .03)
        self.assertEqual(result['surface']['maxLocationM'], [0,0,0])
        self.assertLess(result['bending']['residualBowM'], 1e-9)
        self.assertAlmostEqual(result['bending']['maxCentrelineDepartureM'], np.hypot(.01,.003), places=8)

    def test_known_bow_is_recovered_in_vector_coordinates(self):
        result = self._bar(lambda x: (.003*np.sin(np.pi*x), 0))
        x = np.linspace(0, 1, 16); y = .003*np.sin(np.pi*x)
        expected = np.max(np.abs(y-np.polyval(np.polyfit(x,y,1),x)))
        self.assertAlmostEqual(result['bending']['residualBowM'], expected, places=8)
        self.assertGreater(result['bending']['maxCentrelineDepartureM'], .0029)
        self.assertEqual(result['bending']['quality'], 'supported')

    def test_profile_budget_and_missing_points(self):
        segments = [(str(i),np.array([i,0.,0]),np.array([i+1,0.,0])) for i in range(70)]
        result = measure_bar(np.array([np.nan]),np.zeros((1,3)), segments, np.empty((0,3)))
        self.assertEqual(len(result['longitudinalProfile']),64)
        self.assertTrue(all(r['observedCenterM'] is None and r['transverseOffsetM'] is None for r in result['longitudinalProfile']))
        self.assertIsNone(result['bending']['residualBowM'])

    def test_circumferential_neighbours_and_far_axial_points_are_unknown(self):
        for scan in (np.array([[0.,0.,.01]]),np.array([[.4,.001,0.]])):
            values, _, _, known = constrained_nearest(cKDTree(scan), scan, np.zeros((1,3)), np.array([[1.,0,0]]), np.array([[0.,1.,0]]),k=1,max_angle_deg=30,half_space_only=False,fallback_mode='unknown')
            self.assertTrue(np.isnan(values).all()); self.assertFalse(known.any())

if __name__=='__main__': unittest.main()
