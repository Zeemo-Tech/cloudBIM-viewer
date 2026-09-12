import unittest
import numpy as np
from algorithms.rebar_curve_envelope import split_curve_bars, build_curve_shells, inside_curve_shells


class CurveEnvelopeTests(unittest.TestCase):
    def test_split_and_closed_surface_protects_curve_but_not_u_turn_void(self):
        # A sampled planar U arc joined to a long straight body.
        arc = [[1, 0, 0], [1.18, .08, 0], [1.25, .25, 0], [1.18, .42, 0], [1, .5, 0]]
        inv = {"bars": [{"designBarId": "b", "points": [[0, 0, 0], *arc], "radiusM": .02,
                          "coverage": "complete", "excludedHookRunCount": 1}]}
        body, paths = split_curve_bars(inv)
        self.assertEqual(len(paths), 1); self.assertTrue(len(body["bars"]) >= 1)
        shell = build_curve_shells(paths, .003)[0]
        edges = {}
        for face in shell["triangles"]:
            for a, b in zip(face, face[1:]+face[:1]): edges[tuple(sorted((a,b)))] = edges.get(tuple(sorted((a,b))), 0)+1
        self.assertTrue(all(count == 2 for count in edges.values()))
        vertices, triangles = np.asarray(shell["verticesM"]), np.asarray(shell["triangles"])
        volume6 = np.sum(np.einsum("ij,ij->i", vertices[triangles[:, 0]], np.cross(vertices[triangles[:, 1]], vertices[triangles[:, 2]])))
        self.assertGreater(volume6, 0.)
        points = np.array([[1.25, .25, .01], [1.0, .25, 0], [1.55, .25, 0]])
        inside = inside_curve_shells(points, [shell])
        self.assertEqual(inside.tolist(), [True, False, False])

    def test_sharp_three_point_bend_is_not_arc_smoothed_and_mesh_decision_matches_surface(self):
        path = [{"designBarId": "v", "points": [[0,0,0], [1,0,0], [1,1,0]], "radiusM": .03}]
        shell = build_curve_shells(path)[0]
        self.assertEqual(len(shell["centerlineM"]), 3)
        self.assertTrue(inside_curve_shells(np.array([[.5, .02, 0], [.5, .2, 0]]), [shell]).tolist()[0])
        vertices, face = np.asarray(shell["verticesM"]), shell["triangles"][0]
        triangle = vertices[face]; center = triangle.mean(axis=0)
        normal = np.cross(triangle[1]-triangle[0], triangle[2]-triangle[0]); normal /= np.linalg.norm(normal)
        # First piece has a straight local tangent; its face normal is outward.
        self.assertEqual(inside_curve_shells(np.array([center, center-normal*1e-5, center+normal*1e-5]), [shell]).tolist(), [True, True, False])

    def test_actual_style_arc_dedupes_fits_only_curve_and_surface_boundary_is_stable(self):
        # Real cache shape: a short curved hook, duplicate samples, then a
        # separate long body.  This path intentionally contains no long body.
        path = [{"designBarId": "actual", "radiusM": .004, "points": [
            [6.3375, -1.4179, .0966], [6.3658, -1.4179, .0683], [6.3658, -1.4179, .0683],
            [6.3712, -1.4179, .0580], [6.3701, -1.4179, .0465], [6.3627, -1.4179, .0375],
            [6.3516, -1.4179, .0341]]}]
        shell = build_curve_shells(path, .003)[0]
        self.assertEqual(shell["centerlineM"][0], path[0]["points"][0])
        self.assertEqual(shell["centerlineM"][-1], path[0]["points"][-1])
        self.assertGreaterEqual(shell["arcFitCount"], 1)
        self.assertGreater(len(shell["centerlineM"]), 6)

    def test_split_rejects_unresolved_and_nonfinite_radius(self):
        inv = {"bars": [{"designBarId": "bad", "points": [[0,0,0], [1,0,0], [1,0,.1]], "radiusM": .01,
                         "coverage": "unresolved", "excludedHookRunCount": 1},
                        {"designBarId": "nan", "points": [[0,0,0], [1,0,0]], "radiusM": float("nan")}]}
        body, paths = split_curve_bars(inv)
        self.assertEqual((body["bars"], paths), ([], []))

    def test_four_cocircular_square_corners_are_not_smoothed(self):
        shell = build_curve_shells([{"designBarId": "square", "radiusM": .01,
                                     "points": [[0,0,0], [1,0,0], [1,1,0], [0,1,0]]}])[0]
        self.assertEqual(shell["arcFitCount"], 0)
        self.assertEqual(len(shell["centerlineM"]), 4)


if __name__ == "__main__": unittest.main()
