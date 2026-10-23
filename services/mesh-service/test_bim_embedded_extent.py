import unittest
import numpy as np
from algorithms.bim_embedded_extent import extend_embedded_axes


def cylinder(radius, stations):
    t, a = np.meshgrid(stations, np.linspace(0, 2*np.pi, 40, endpoint=False), indexing='ij')
    return (np.c_[radius*np.cos(a.ravel()), radius*np.sin(a.ravel()), t.ravel()],
            np.c_[np.cos(a.ravel()), np.sin(a.ravel()), np.zeros(a.size)])


def inputs(points):
    vertices, _ = cylinder(.016, [-.075, .075])
    scene = {'objects': [{'globalId': 'tube', 'category': 'embedded', 'vertices': vertices}]}
    report = {'objects': [{'globalId': 'tube', 'id': 1, 'pointCount': 100, 'transform': np.eye(4).tolist()}]}
    seed = (points[:, 2] > -.075) & (points[:, 2] < .075) & (np.linalg.norm(points[:, :2], axis=1) > .014)
    return scene, report, np.where(seed, 5, 0).astype(np.uint8), seed.astype(np.uint32), np.zeros(len(points), bool)


class EmbeddedExtentTests(unittest.TestCase):
    def run_extent(self, p, n, setup=None):
        s, r, labels, owners, protected = inputs(p)
        if setup:
            setup(s, r, labels, owners, protected)
        old = labels.copy(), owners.copy(), p.copy()
        report = extend_embedded_axes(p, n, s, r, labels, owners, protected)
        np.testing.assert_array_equal(p, old[2])
        np.testing.assert_array_equal(owners[old[1] != 0], old[1][old[1] != 0])
        np.testing.assert_array_equal(labels[old[0] != 0], old[0][old[0] != 0])
        return report, labels, owners

    def test_connected_outer_shell(self):
        p, n = cylinder(.016, np.arange(-.14, .161, .002))
        r, labels, _ = self.run_extent(p, n)
        self.assertGreater(r['addedPointCount'], 2000)
        self.assertLess(r['objects'][0]['observedAxialBoundsM'][0], -.13)
        self.assertGreater(r['objects'][0]['observedAxialBoundsM'][1], .15)
        self.assertEqual(r['objects'][0]['modelLengthM'], .15)
        self.assertTrue(np.all(labels == 5))
        fit = r['objects'][0]['flexibleRegistration']
        np.testing.assert_allclose(fit['fittedAxialBoundsM'], [-.14, .16], atol=1e-8)
        self.assertFalse(fit['radiusChanged'])
        self.assertEqual(fit['physicalEndFacesConfirmed'], [False, False])

    def test_unobserved_end_keeps_design_prior_without_source_ownership(self):
        p, n = cylinder(.016, np.arange(-.14, .051, .002))
        r, labels, _ = self.run_extent(p, n)
        fit = r['objects'][0]['flexibleRegistration']
        self.assertAlmostEqual(fit['fittedAxialBoundsM'][1], .075)
        self.assertLess(fit['observedAxialBoundsM'][1], .051)
        self.assertEqual(fit['endDisplacementsM'][1], 0)
        self.assertEqual(len(labels), len(p))

    def test_disconnected_shell(self):
        p, n = cylinder(.016, np.r_[np.arange(-.074, .075, .002), np.arange(.10, .16, .002)])
        r, labels, _ = self.run_extent(p, n)
        self.assertEqual(r['addedPointCount'], 0)
        self.assertTrue(np.all(labels[p[:, 2] > .09] == 0))

    def test_inner_hole_steel_and_foreign_owner(self):
        outer, on = cylinder(.016, np.arange(-.074, .075, .002))
        inner, inn = cylinder(.010, np.arange(.076, .16, .002))
        p, n = np.r_[outer, inner], np.r_[on, inn]
        def setup(s, r, labels, owners, protected):
            labels[-len(inner):] = 3
            protected[-len(inner):] = True
        r, labels, _ = self.run_extent(p, n, setup)
        self.assertEqual(r['addedPointCount'], 0)
        self.assertTrue(np.all(labels[-len(inner):] == 3))
        p, n = cylinder(.016, np.arange(-.074, .16, .002))
        def foreign(s, r, labels, owners, protected):
            h = p[:, 2] > .075
            labels[h] = 2
            owners[h] = 99
        r, labels, owners = self.run_extent(p, n, foreign)
        self.assertEqual(r['addedPointCount'], 0)
        self.assertTrue(np.all(owners[p[:, 2] > .075] == 99))

    def test_protected_unclassified_outer_rows_remain_unclaimed(self):
        p, n = cylinder(.016, np.arange(-.074, .16, .002))
        def setup(s, r, labels, owners, protected):
            protected[p[:, 2] > .075] = True
        r, labels, owners = self.run_extent(p, n, setup)
        self.assertEqual(r['addedPointCount'], 0)
        self.assertTrue(np.all(labels[p[:, 2] > .075] == 0))
        self.assertTrue(np.all(owners[p[:, 2] > .075] == 0))

    def test_crossing_plane(self):
        core, cn = cylinder(.016, np.arange(-.074, .075, .002))
        x, z = np.meshgrid(np.arange(-.009, .010, .001), np.arange(.076, .15, .002))
        plane = np.c_[x.ravel(), np.full(x.size, .016), z.ravel()]
        pn = np.tile([0., 1., 0.], (len(plane), 1))
        p, n = np.r_[core, plane], np.r_[cn, pn]
        r, labels, _ = self.run_extent(p, n)
        self.assertEqual(r['addedPointCount'], 0)
        self.assertTrue(np.all(labels[-len(plane):] == 0))

    def test_thin_ring(self):
        p, n = cylinder(.016, np.arange(-.074, .16, .002))
        def setup(s, r, labels, owners, protected):
            s['objects'][0]['vertices'], _ = cylinder(.016, [-.003, .003])
        r, _, _ = self.run_extent(p, n, setup)
        self.assertEqual(r['addedPointCount'], 0)
        self.assertEqual(r['objects'][0]['status'], 'thin-or-non-axial-part')

    def test_limit(self):
        p, n = cylinder(.016, np.arange(-.074, .40, .002))
        r, labels, _ = self.run_extent(p, n)
        self.assertTrue(np.all(labels[p[:, 2] > .275] == 0))
        self.assertEqual(r['objects'][0]['ends'][1]['stopReason'], 'maximum-measured-traversal-reached')

    def test_rotated_physical_axis(self):
        p, n = cylinder(.016, np.arange(-.074, .16, .002))
        s, r, labels, owners, protected = inputs(p)
        rot = np.array([[0., 0., 1.], [1., 0., 0.], [0., 1., 0.]])
        transform = np.eye(4); transform[:3, :3] = rot; transform[:3, 3] = [1., 2., 3.]
        r['objects'][0]['transform'] = transform.tolist()
        report = extend_embedded_axes(p@rot.T+transform[:3, 3], n@rot.T, s, r, labels, owners, protected)
        self.assertGreater(report['addedPointCount'], 1000)
        np.testing.assert_allclose(report['objects'][0]['axisScan'], [1., 0., 0.], atol=1e-10)


if __name__ == '__main__':
    unittest.main()
