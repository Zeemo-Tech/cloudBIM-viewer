import unittest
import numpy as np

from rebar_cluster_axis import measure_clusters, partition_design_mesh
from rebar_scan_surface import ObservedRebarSurface


class IndependentClusterTests(unittest.TestCase):
    def points(self, shift=(.25, -.02, .3), length=.12):
        s, angle = np.meshgrid(np.linspace(0, length, 100), np.linspace(-.9, .9, 30), indexing='ij')
        return np.c_[.004*np.cos(angle.ravel())+.04*s.ravel(),
                     .004*np.sin(angle.ravel()), s.ravel()] + shift

    def measure(self, points, start=None, end=None):
        segments = [('u', np.zeros(3) if start is None else start, np.array([0., 0, .28]) if end is None else end)]
        return measure_clusters(np.empty((0, 3)), segments, {'u': points}, {'u': .004}), segments

    def test_displaced_short_cluster_is_fitted_outside_design_length_and_distance(self):
        points = self.points()
        m, segments = self.measure(points)
        self.assertTrue(all(r['observedCenterM'] is not None for r in m['longitudinalProfile']))
        for row in m['longitudinalProfile']:
            x, y, z = row['observedCenterM']
            self.assertAlmostEqual(x, .25+.04*(z-.3), delta=.0001)
            self.assertAlmostEqual(y, -.02, delta=.0001)
        self.assertAlmostEqual(m['clusterFits'][0]['observedLengthM'], .12, delta=.003)
        surface = ObservedRebarSurface(segments, {'u': points}, m['longitudinalProfile'], independent_axes=True)
        self.assertGreater(surface.diagnostics['supportedPointCount'], len(points)*.9)
        # The 200 mm correspondence cap does not suppress the independent fit.
        values, _, _ = surface.match(np.array([[.004, 0, .15]]), np.array([[1., 0, 0]]))
        self.assertTrue(np.isnan(values[0]))

    def test_design_position_length_and_direction_cannot_change_observed_fit(self):
        points = self.points()
        original, _ = self.measure(points)
        changed, _ = self.measure(points, np.array([12., -4, 2]), np.array([12.4, -3.8, 2.3]))
        def axis(m):
            return np.array([r['observedCenterM'] for r in sorted(m['longitudinalProfile'], key=lambda r: r['axisStationM'])])
        np.testing.assert_array_equal(axis(original), axis(changed))
        self.assertNotEqual(original['bending']['maxCentrelineDepartureM'], changed['bending']['maxCentrelineDepartureM'])

    def test_neighbor_cluster_does_not_influence_fit_or_normals(self):
        points = self.points()
        m, segments = self.measure(points)
        both = segments + [('v', np.array([.1, 0, 0]), np.array([.1, 0, .28]))]
        # Equal sample budget for u; changing a different cluster must have no effect.
        pair = measure_clusters(np.empty((0, 3)), both, {'u': points, 'v': self.points((-.4,.1,.2))},
                                {'u': .004, 'v': .004}, max_samples=128)
        np.testing.assert_array_equal([r['observedCenterM'] for r in m['longitudinalProfile']],
                                      [r['observedCenterM'] for r in pair['longitudinalProfile'] if r['designUnitId']=='u'])

    def test_window_change_does_not_change_independent_axis(self):
        points = self.points(); m, segments = self.measure(points)
        wide = measure_clusters(np.empty((0,3)), segments, {'u':points}, {'u':.004}, window_scale=4)
        np.testing.assert_array_equal([r['observedCenterM'] for r in m['longitudinalProfile']],
                                      [r['observedCenterM'] for r in wide['longitudinalProfile']])

    def test_mesh_partitions_keep_every_face_once_including_missing_cluster(self):
        vertices = np.array([[0,0,0], [.01,0,.4], [-.01,0,.4], [0,0,.6], [.01,0,1], [-.01,0,1]])
        faces = np.array([[0,1,2], [1,2,3], [3,4,5]])
        segments = [('a',np.zeros(3),np.array([0.,0,.5])), ('missing',np.array([0.,0,.5]),np.array([0.,0,1]))]
        vu, fu, parts = partition_design_mesh(vertices, faces, segments)
        self.assertEqual(len(vu),len(vertices)); self.assertEqual(len(fu),len(faces))
        self.assertEqual(set(fu),{'a','missing'})
        partitioned = [tuple(face) for part in parts for face in np.array(part['indices']).reshape(-1,3)]
        self.assertCountEqual(partitioned, map(tuple, faces))

    def test_single_generator_does_not_invent_axis(self):
        m, _ = self.measure(np.c_[np.full(100,.25), np.zeros(100), np.linspace(.3,.5,100)])
        self.assertTrue(all(r['observedCenterM'] is None for r in m['longitudinalProfile']))
