"""The matched instance + design dimensions must yield one continuous axis."""
import unittest
import numpy as np
from rebar_metrics import measure_bar
from rebar_scan_surface import ObservedRebarSurface

class PriorAxisTests(unittest.TestCase):
    def test_guarded_fit_does_not_bend_toward_sparse_terminal_noise(self):
        from rebar_prior_axis import fit_prior_axis
        from test_rebar_control_net import tube
        body, _ = tube([.18, 0, 0], [1., 0, 0], along=140, around=24)
        noise, _ = tube([0, .025, 0], [.035, .025, 0], along=4, around=8)
        axis, reason = fit_prior_axis(np.vstack((body, noise)), np.zeros(3), np.array([1., 0, 0]),
                                      1., .004, guard_bending=True)
        self.assertIsNotNone(axis, reason)
        offset = axis['spline'](np.linspace(0, 1, 65))@axis['coeff']
        self.assertLess(np.abs(offset).max(), .0005)
        self.assertEqual(axis['shapeModel'], 'evidence-selected-straight')
        self.assertGreater(axis['supportedRange'][0], .1)

    def test_guarded_fit_preserves_a_distributed_observed_bow(self):
        from rebar_prior_axis import fit_prior_axis
        from test_rebar_control_net import tube
        body, _ = tube([0, 0, 0], [1., 0, 0], along=160, around=24,
                       bend=lambda s: .014*np.sin(np.pi*s))
        axis, reason = fit_prior_axis(body, np.zeros(3), np.array([1., 0, 0]),
                                      1., .004, guard_bending=True)
        self.assertIsNotNone(axis, reason)
        stations=np.linspace(.05, .95, 25)
        offset=axis['spline'](stations)@axis['coeff']
        self.assertIn(axis['shapeModel'], ('evidence-selected-bow', 'supported-spline'))
        np.testing.assert_allclose(offset[:, 0], .014*np.sin(np.pi*stations), atol=.0004)

    def test_dense_local_contamination_cannot_outvote_the_remaining_body(self):
        from rebar_prior_axis import fit_prior_axis
        from test_rebar_control_net import tube
        body, _ = tube([0, 0, 0], [1., 0, 0], along=160, around=24)
        contamination, _ = tube([.46, .012, 0], [.54, .012, 0], along=200, around=48)
        axis, reason = fit_prior_axis(np.vstack((body, contamination)), np.zeros(3), np.array([1., 0, 0]),
                                      1., .004, guard_bending=True)
        self.assertIsNotNone(axis, reason)
        offset=axis['spline'](np.linspace(0, 1, 65))@axis['coeff']
        self.assertLess(np.abs(offset).max(), .0005)
        self.assertEqual(axis['shapeModel'], 'evidence-selected-straight')

    def test_supported_spline_extrapolates_a_tangent_not_curvature(self):
        from scipy.interpolate import BSpline
        from rebar_prior_axis import _SupportedSpline
        spline=_SupportedSpline(BSpline([0,0,0,0,1,1,1,1],np.eye(4),3),.2,.8)
        np.testing.assert_allclose(spline(0), spline(.2)-.2*spline(.2,nu=1))
        np.testing.assert_allclose(spline(1), spline(.8)+.2*spline(.8,nu=1))
        np.testing.assert_allclose(spline([0,1],nu=2), 0)

    def partial_tube(self, gap=False):
        stations=np.linspace(0,1,181)
        if gap:stations=stations[(stations<.4)|(stations>.6)]
        angles=np.linspace(-.85,.85,21)
        s,a=np.meshgrid(stations,angles,indexing='ij')
        centers=np.column_stack((.006+.002*s.ravel()+.001*np.sin(np.pi*s.ravel()),np.full(s.size,-.003),s.ravel()))
        points=centers+np.column_stack((.004*np.cos(a.ravel()),.004*np.sin(a.ravel()),np.zeros(s.size)))
        return points
    def measure(self,points):
        segments=[('u',np.array([0.,0,0]),np.array([0.,0,1]))]
        m=measure_bar(np.array([]),np.empty((0,3)),segments,points,unit_points={'u':points},radii={'u':.004},axis_method='design-prior')
        return m,segments
    def test_known_diameter_partial_arc_has_continuous_accurate_axis(self):
        m,_=self.measure(self.partial_tube())
        rows=m['longitudinalProfile']
        self.assertTrue(rows)
        self.assertTrue(all(r['observedCenterM'] is not None for r in rows),'known-radius instance axis must not disappear at independently rejected cross sections')
        for row in rows:
            s=row['stationM'];truth=[.006+.002*s+.001*np.sin(np.pi*s),-.003,s]
            np.testing.assert_allclose(row['observedCenterM'],truth,atol=.0003)
    def test_observed_station_seed_avoids_mirror_axis_on_bowed_partial_arc(self):
        from rebar_prior_axis import fit_prior_axis
        def center(stations):
            return np.column_stack((4*stations, .014*np.sin(np.pi*stations),
                                    np.zeros_like(stations)))
        station, angle = np.meshgrid(np.linspace(0, 1, 160), np.linspace(-1., 1., 21), indexing='ij')
        points = center(station.ravel()) + np.column_stack((np.zeros(station.size),
            .004*np.sin(angle.ravel()), .004*np.cos(angle.ravel())))
        axis, reason = fit_prior_axis(points, np.zeros(3), np.array([1., 0., 0.]),
            4., .004, initial_centerline=center(np.linspace(0, 1, 13)))
        self.assertIsNotNone(axis, reason)
        stations = np.linspace(0, 1, 17)
        offset = axis['spline'](stations)@axis['coeff']
        observed = (axis['start'] + 4*stations[:, None]*axis['tangent']
                    + offset[:, :1]*axis['u'] + offset[:, 1:]*axis['v'])
        np.testing.assert_allclose(observed, center(stations), atol=.0002)
        self.assertGreater(axis['inliers'].mean(), .99)

    def test_gap_keeps_axis_but_does_not_invent_scan_matches(self):
        points=self.partial_tube(gap=True);m,segments=self.measure(points)
        self.assertTrue(all(r['observedCenterM'] is not None for r in m['longitudinalProfile']))
        surface=ObservedRebarSurface(segments,{'u':points},m['longitudinalProfile'])
        d,_,_=surface.match(np.array([[.004,0,.5]]),np.array([[1.,0,0]]),max_angle_deg=30)
        self.assertTrue(np.isnan(d[0]),'an inferred axis through an occlusion is not an observed surface')

    def test_noisy_uneven_partial_scan_with_outliers(self):
        rng = np.random.default_rng(17)
        points = self.partial_tube()
        points = np.concatenate((points, np.repeat(points[points[:, 2] < .15], 4, axis=0)))
        points += rng.normal(0, .00008, points.shape)
        outliers = points[::12] + rng.normal(0, .002, points[::12].shape)
        m, _ = self.measure(np.concatenate((points, outliers)))
        for row in m['longitudinalProfile']:
            s = row['stationM']
            np.testing.assert_allclose(row['observedCenterM'],
                [.006+.002*s+.001*np.sin(np.pi*s), -.003, s], atol=.0004)
        self.assertIsNone(m['crossSection']['maxAbsRadiusDeltaM'])

    def test_inspection_window_and_arc_gate_do_not_change_axis(self):
        points = self.partial_tube()
        baseline, segments = self.measure(points)
        changed = measure_bar(np.array([]), np.empty((0,3)), segments, points,
            unit_points={'u':points}, radii={'u':.004}, axis_method='design-prior',
            window_scale=2, min_arc_coverage_deg=300)
        np.testing.assert_allclose([r['observedCenterM'] for r in baseline['longitudinalProfile']],
                                   [r['observedCenterM'] for r in changed['longitudinalProfile']], atol=1e-12)

    def test_single_generator_and_short_patch_cannot_invent_whole_axis(self):
        for points in (np.c_[np.full(200, .01), np.zeros(200), np.linspace(0, 1, 200)],
                       self.partial_tube()[self.partial_tube()[:, 2] < .1]):
            m, _ = self.measure(points)
            self.assertTrue(all(r['observedCenterM'] is None for r in m['longitudinalProfile']))

    def test_rotated_translated_coordinates_keep_the_same_solution(self):
        from scipy.spatial.transform import Rotation
        rotation = Rotation.from_rotvec([.8,-.2,.5]).as_matrix()
        origin = np.array([100., -30., 25.])
        points = self.partial_tube() @ rotation.T + origin
        segments = [('u', origin, origin + rotation[:, 2])]
        m = measure_bar(np.array([]), np.empty((0,3)), segments, points,
            unit_points={'u': points}, radii={'u': .004}, axis_method='design-prior')
        for row in m['longitudinalProfile']:
            s = row['stationM']
            truth = rotation @ np.array([.006+.002*s+.001*np.sin(np.pi*s),-.003,s]) + origin
            np.testing.assert_allclose(row['observedCenterM'], truth, atol=.0003)

    def test_display_budget_keeps_both_ends_of_each_design_unit(self):
        points = self.partial_tube()
        segments = [(str(i), np.zeros(3), np.array([0.,0.,1.])) for i in range(4)]
        m = measure_bar(np.array([]), np.empty((0,3)), segments, points,
            unit_points={str(i): points for i in range(4)}, radii={str(i): .004 for i in range(4)},
            axis_method='design-prior', max_samples=4)
        for unit_id, _, _ in segments:
            rows = [r for r in m['longitudinalProfile'] if r['designUnitId'] == unit_id]
            self.assertGreaterEqual(len(rows), 3)
            self.assertEqual(rows[0]['stationM'], 0)
            self.assertEqual(rows[-1]['stationM'], 1)
            self.assertTrue(all(r['observedCenterM'] is not None for r in rows))
