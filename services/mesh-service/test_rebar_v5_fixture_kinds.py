"""Physical tube walls and narrow clamping plates need different evidence."""
import unittest

import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.scene import detect_fixtures, fixture_mask


def tube_and_clamp():
    x = np.arange(-.24, .241, .006)
    cross = np.arange(-.03, .031, .006)
    xx, cc = np.meshgrid(x, cross)
    parts, normals = [], []
    for sign in (-1, 1):
        parts.append(np.column_stack((xx.ravel(), np.full(xx.size, sign*.03), .12+cc.ravel())))
        normals.append(np.tile([0, sign, 0], (xx.size, 1)))
        parts.append(np.column_stack((xx.ravel(), cc.ravel(), np.full(xx.size, .12+sign*.03))))
        normals.append(np.tile([0, 0, sign], (xx.size, 1)))
    tube = np.vstack(parts)
    # 26 mm wide clamp on the top wall: narrower than the general 32 mm
    # anti-cylinder face gate, but backed by an observed rectangular tube.
    px, py = np.meshgrid(np.linspace(-.045, .045, 23), np.linspace(-.013, .013, 9))
    plate = np.column_stack((px.ravel(), py.ravel(), np.full(px.size, .158)))
    points = np.vstack((tube, plate))
    features = {
        'surface_normal': np.vstack((*normals, np.tile([0, 0, 1], (len(plate), 1)))),
        'surface_planarity': np.ones(len(points)),
        'surface_valid': np.ones(len(points), np.uint8),
    }
    return points, features, len(tube)


class FixtureKindTests(unittest.TestCase):
    def test_small_clamping_plate_on_tube_is_recovered_as_plate(self):
        points, features, split = tube_and_clamp()
        p = Params()
        faces = detect_fixtures(points, features, p)
        tube = [face for face in faces if face['kind'] == 'square-tube-face']
        plates = [face for face in faces if face['kind'] == 'plate']
        self.assertGreater(fixture_mask(points[:split], tube, p).mean(), .95)
        self.assertGreater(fixture_mask(points[split:], plates, p).mean(), .90)
        self.assertFalse(fixture_mask(points[split:], tube, p).any())

    def test_unobserved_hollow_interior_remains_unclassified(self):
        points, features, _ = tube_and_clamp()
        p = Params()
        faces = detect_fixtures(points, features, p)
        self.assertFalse(fixture_mask(np.array([[0, 0, .12], [.1, 0, .12]]), faces, p).any())

    def test_narrow_plate_without_tube_context_does_not_bypass_width_guard(self):
        points, features, split = tube_and_clamp()
        plate = points[split:]
        features = {key: value[split:] for key, value in features.items()}
        self.assertEqual(detect_fixtures(plate, features, Params()), [])

    def test_small_perpendicular_plate_is_not_a_longitudinal_tube_wall(self):
        from algorithms.rebar_v5.scene import _tube_face_pair
        def face(normal, axes, extent, origin):
            return {'normal':normal, 'axes':axes, 'halfExtent':extent, 'origin':origin}
        wall = face([0,1,0], [[1,0,0],[0,0,1]], [.3,.042], [0,.03,.12])
        clamp = face([0,0,1], [[1,0,0],[0,1,0]], [.06,.025], [0,0,.158])
        self.assertFalse(_tube_face_pair(wall, clamp, Params()))

    def test_two_equal_adjacent_l_bracket_faces_do_not_assert_a_tube(self):
        points, features, _ = tube_and_clamp()
        # The first two arrays are adjoining walls, with no opposite wall.
        rows = np.arange(2*81*11)
        faces = detect_fixtures(points[rows], {k:v[rows] for k,v in features.items()}, Params())
        self.assertTrue(faces)
        self.assertFalse(any(face['kind']=='square-tube-face' for face in faces))

    def test_cylinder_near_tube_is_not_a_contextual_clamp(self):
        from algorithms.rebar_v5.features import multiscale
        from rebar_validation import _rod
        points, _, split = tube_and_clamp()
        cylinder = _rod(np.random.default_rng(8103), [-.12,0,.17], [.12,0,.17], .006, 900, top_arcs=False)
        cloud = np.vstack((points[:split], cylinder))
        p = Params()
        faces = detect_fixtures(cloud, multiscale(cloud,cloud,p), p)
        self.assertTrue(any(face['kind']=='square-tube-face' for face in faces))
        self.assertFalse(fixture_mask(cylinder, faces, p).any())


if __name__ == '__main__':
    unittest.main()
