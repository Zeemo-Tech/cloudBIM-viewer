import unittest
import numpy as np

from algorithms.rebar_bend_cloth import build_bend_cloths
from algorithms.rebar_curve_envelope import inside_curve_shells


class BendClothTests(unittest.TestCase):
    def test_parallel_u_bends_are_one_closed_cavity_preserving_mesh(self):
        # The row direction is Y here, but the implementation derives it from
        # each curve's plane rather than treating Y as special.
        curve = np.array([[0, 0, 0], [.1, 0, 0], [.14, 0, .04], [.14, 0, .16], [.1, 0, .2], [0, 0, .2]])
        paths = [{"designBarId": str(i), "radiusM": .004, "points": (curve + [0, -.1*i, 0]).tolist()} for i in range(10)]
        cloths = build_bend_cloths(paths)
        self.assertEqual(len(cloths), 1)
        cloth = cloths[0]; self.assertEqual(cloth["surfaceRole"], "shared-bend-cloth")
        self.assertEqual(len(cloth["designBarIds"]), 10)
        edges = {}
        for face in cloth["triangles"]:
            for a, b in zip(face, face[1:]+face[:1]):
                key = tuple(sorted((a, b))); edges[key] = edges.get(key, 0)+1
        self.assertTrue(all(n == 2 for n in edges.values()))
        directed = {}
        for face in cloth["triangles"]:
            for a, b in zip(face, face[1:]+face[:1]):
                directed[(a, b)] = directed.get((a, b), 0)+1
        self.assertTrue(all(directed.get((b, a), 0) == count for (a, b), count in directed.items()))
        vertices, triangles = np.asarray(cloth["verticesM"]), np.asarray(cloth["triangles"])
        volume6 = np.einsum("ij,ij->i", vertices[triangles[:, 0]], np.cross(vertices[triangles[:, 1]], vertices[triangles[:, 2]])).sum()
        self.assertGreater(volume6, 0)
        # A curve point is protected, while the inside of the U stays empty.
        self.assertTrue(inside_curve_shells(np.array([[.14, -.45, .10]]), cloths)[0])
        self.assertFalse(inside_curve_shells(np.array([[.06, -.45, .10]]), cloths)[0])
        # A circular steel surface sample for each row is within the cloth.
        sample = []
        for row in range(10):
            center = curve[2] + [0, -.1*row, 0]
            for angle in np.linspace(0, 2*np.pi, 16, endpoint=False):
                sample.append(center + .004*np.array([np.cos(angle), np.sin(angle), 0]))
        self.assertTrue(inside_curve_shells(np.asarray(sample), cloths).all())
        # The side of the outermost steel receives 5 mm more clearance.  It
        # includes the surface boundary and rejects a point 2 um past it.
        self.assertTrue(inside_curve_shells(np.array([[.14, .009, .10]]), cloths)[0])
        self.assertFalse(inside_curve_shells(np.array([[.14, .009002, .10]]), cloths)[0])

    def test_rotation_and_translation_are_equivariant(self):
        base = np.array([[0, 0, 0], [.1, 0, 0], [.1, 0, .1]])
        paths = [{"designBarId": str(i), "radiusM": .01, "points": (base + [0, .03*i, 0]).tolist()} for i in range(2)]
        matrix = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0.]])
        offset = np.array([3., -2., .5])
        moved = [{**p, "points": (np.asarray(p["points"]) @ matrix.T + offset).tolist()} for p in paths]
        a, b = build_bend_cloths(paths)[0], build_bend_cloths(moved)[0]
        expected = np.asarray(a["verticesM"]) @ matrix.T + offset
        self.assertTrue(np.allclose(expected, b["verticesM"], atol=1e-9))

    def test_two_point_and_nonplanar_paths_fall_back_without_being_dropped(self):
        paths = [
            {"designBarId": "line", "radiusM": .004, "points": [[0, 0, 0], [0, 0, .1]]},
            {"designBarId": "spatial", "radiusM": .004,
             "points": [[1, 0, 0], [1.04, .01, .02], [1.05, -.01, .06], [1.08, .02, .1]]},
        ]
        cloths = build_bend_cloths(paths)
        self.assertEqual({x["designBarIds"][0] for x in cloths}, {"line", "spatial"})
        self.assertTrue(all(x["surfaceRole"] == "shared-bend-cloth-fallback" for x in cloths))
        self.assertTrue(inside_curve_shells(np.array([[0, 0, .05], [1.04, .01, .02]]), cloths).all())


if __name__ == "__main__":
    unittest.main()
