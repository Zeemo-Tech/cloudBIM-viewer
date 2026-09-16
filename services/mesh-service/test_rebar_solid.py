import unittest
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from rebar_deviation import constrained_nearest
from rebar_solid import RebarSolid

class RebarSolidTests(unittest.TestCase):
    def cylinder(self):
        return trimesh.creation.cylinder(radius=.01, height=.2, sections=32)

    def select(self, scan, fallback='unknown', half=False):
        solid = RebarSolid([self.cylinder()])
        scan = np.asarray(scan, float)
        return constrained_nearest(cKDTree(scan), scan, np.array([[.01, 0, 0]]),
                                   np.array([[0., 0, 1]]), np.array([[1., 0, 0]]),
                                   k=32, max_angle_deg=30, half_space_only=half,
                                   fallback_mode=fallback, solid=solid)

    def test_inward_stops_at_opposite_surface(self):
        for x, expected in [(.012, .002), (.01, 0), (.005, -.005), (0, -.01),
                            (-.005, -.015), (-.01, -.02), (-.01001, None), (-.015, None)]:
            with self.subTest(x=x):
                d, ix, _, ok = self.select([[x, 0, 0]])
                if expected is None:
                    self.assertTrue(np.isnan(d[0])); self.assertEqual(ix[0], -1)
                    self.assertFalse(ok[0])
                else:
                    self.assertTrue(ok[0]); self.assertAlmostEqual(d[0], expected, places=7)

    def test_rejected_candidate_does_not_hide_next_valid_candidate(self):
        d, ix, _, ok = self.select([[-.011, 0, 0], [.04, 0, 0]])
        self.assertTrue(ok[0]); self.assertEqual(ix[0], 1)
        self.assertAlmostEqual(d[0], .03)

    def test_fallback_cannot_bypass_solid_or_half_space(self):
        for half in [False, True]:
            d, ix, _, _ = self.select([[-.015, 0, 0]], fallback='nearest', half=half)
            self.assertTrue(np.isnan(d[0])); self.assertEqual(ix[0], -1)

    def test_oblique_ray_uses_actual_chord_not_diameter(self):
        solid = RebarSolid([self.cylinder()])
        np.testing.assert_array_equal(solid.allows_inward(
            np.array([[.01, 0, 0], [.01, 0, 0]]),
            np.array([[-.009, .008, 0], [-.005, .003, 0]])), [False, True])

    def test_ray_cannot_exit_and_reenter_another_limb(self):
        second = self.cylinder(); second.apply_translation([-.03, 0, 0])
        solid = RebarSolid([self.cylinder(), second], ['first', 'second'])
        self.assertFalse(solid.allows_inward(np.array([[.01, 0, 0]]), np.array([[-.03, 0, 0]]))[0])

    def test_reversed_and_mixed_winding_are_repaired_without_reordering(self):
        for mixed in [False, True]:
            mesh = self.cylinder(); before = mesh.vertices.copy()
            faces = mesh.faces.copy()
            if mixed: faces[::3] = faces[::3, ::-1]
            else: faces = faces[:, ::-1]
            mesh.faces = faces
            solid = RebarSolid([mesh])
            np.testing.assert_array_equal(mesh.vertices, before)
            self.assertGreater(solid.diagnostics['flippedFaceCount'], 0)
            self.assertTrue(mesh.is_winding_consistent)
            radial = mesh.triangles_center[:, :2]
            side = np.abs(mesh.face_normals[:, 2]) < .5
            self.assertTrue(np.all(np.einsum('ij,ij->i', radial[side], mesh.face_normals[side, :2]) > 0))
            cap = ~side
            self.assertTrue(np.all(mesh.triangles_center[cap, 2] * mesh.face_normals[cap, 2] > 0))

    def test_tiles_reassemble_closed_part_and_share_seam_normals(self):
        from analysis_mesh.artifact import _face_chunks
        tiles = list(_face_chunks(self.cylinder(), 20))
        positions = [m.vertices.copy() for m in tiles]
        solid = RebarSolid(tiles, ['part'] * len(tiles))
        self.assertEqual(solid.diagnostics['closedPartCount'], 1)
        self.assertEqual(solid.diagnostics['invalidPartCount'], 0)
        seen = {}
        for tile, before in zip(tiles, positions):
            np.testing.assert_array_equal(tile.vertices, before)
            for v, n in zip(tile.vertices, tile.vertex_normals):
                key = tuple(v)
                if key in seen: np.testing.assert_allclose(n, seen[key])
                seen[key] = n

    def test_open_part_has_no_trusted_normals(self):
        mesh = self.cylinder(); mesh.update_faces(np.arange(len(mesh.faces) - 1))
        solid = RebarSolid([mesh])
        self.assertEqual(solid.diagnostics['invalidPartCount'], 1)
        np.testing.assert_array_equal(mesh.vertex_normals, 0)

    def test_recentered_geometry_preserves_millimetre_ray_accuracy(self):
        mesh = self.cylinder(); offset = np.array([1e5, -2e5, 3e5]); mesh.apply_translation(offset)
        solid = RebarSolid([mesh])
        np.testing.assert_array_equal(solid.allows_inward(
            np.array([[.01, 0, 0], [.01, 0, 0]]) + offset,
            np.array([[-.009, 0, 0], [-.011, 0, 0]]) + offset), [True, False])

if __name__ == '__main__': unittest.main()
