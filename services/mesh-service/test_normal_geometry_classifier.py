import unittest
import numpy as np

from algorithms.pointcloud_normals import PointCloudContext, estimate_normals, available_workers
from algorithms.normal_geometry_classifier import classify_geometry, projector6


def make_context(points, normals):
    context = PointCloudContext.build(np.ascontiguousarray(points, dtype=np.float64))
    context.normals = np.asarray(normals, dtype=np.float32)
    context.normal_valid = np.ones(len(points), dtype=np.uint8)
    return context


def round_tube(radius=.004, length=.30):
    axial = np.arange(-length/2, length/2+.001, .003)
    theta, y = np.meshgrid(np.linspace(0, 2*np.pi, 24, endpoint=False), axial)
    normals = np.column_stack((np.cos(theta.ravel()), np.zeros(theta.size), np.sin(theta.ravel())))
    points = radius*normals
    points[:, 1] = y.ravel()
    points[:, 2] += .04
    return points, normals


def square_tube(half=.004):
    axial = np.arange(-.15, .151, .003)
    transverse = np.arange(-half, half+.0015, .003)
    points, normals = [], []
    for axis, sign in ((0, -1), (0, 1), (2, -1), (2, 1)):
        y, a = np.meshgrid(axial, transverse)
        p = np.zeros((y.size, 3))
        p[:, 1], p[:, axis], p[:, 2 if axis == 0 else 0] = y.ravel(), sign*half, a.ravel()
        p[:, 2] += .04
        n = np.zeros_like(p)
        n[:, axis] = sign
        points.append(p)
        normals.append(n)
    return np.vstack(points), np.vstack(normals)


class NormalGeometryTests(unittest.TestCase):
    def test_projectors_preserve_oblique_planes_lost_by_absolute_components(self):
        normals = np.array([[1., 1., 0.], [1., -1., 0.]]) / np.sqrt(2)
        np.testing.assert_array_equal(np.abs(normals[0]), np.abs(normals[1]))
        self.assertFalse(np.allclose(projector6(normals)[0], projector6(normals)[1]))
        np.testing.assert_array_equal(projector6(normals), projector6(-normals))

    def test_exact_flat_table_and_empty_residual(self):
        axis = np.arange(-.30+.0015, .30, .003)
        x, y = np.meshgrid(axis, axis)
        base = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
        points = np.vstack((base, base+[.0002, 0, 0], base+[0, .0002, 0]))
        context = make_context(points, np.tile([0., 0., 1.], (len(points), 1)))
        report = classify_geometry(context, workers=1)
        self.assertEqual(report["counts"]["table"], len(points))
        self.assertEqual(report["diagnostics"]["residualCells"], 0)
        self.assertIsNotNone(report["diagnostics"]["table"])

    def test_round_and_thin_square_tube_do_not_share_rebar_label(self):
        points, normals = round_tube()
        cylinder = make_context(points, normals)
        report = classify_geometry(cylinder, workers=1)
        self.assertGreater(report["counts"]["rebar"] / len(points), .98)
        points, normals = square_tube()
        square = make_context(points, normals)
        report = classify_geometry(square, workers=1)
        self.assertEqual(report["counts"]["rebar"], 0)
        # Small faces and edges still belong to the whole square tube.
        self.assertEqual(report["counts"]["fixture"], len(points))
        self.assertEqual(set(report["counts"]), {"table", "fixture", "rebar"})
        self.assertEqual(sum(report["counts"].values()), len(points))

    def test_nearby_parallel_vertical_faces_do_not_destroy_plane_support(self):
        # Thin walls / multiple scan returns: an isotropic 8 mm ball includes
        # both 4 mm-separated sheets, although each sheet is a clear plane.
        y, z = np.meshgrid(np.arange(-.30, .30, .0015), np.arange(.01, .06, .0015))
        face = np.column_stack((np.zeros(y.size), y.ravel(), z.ravel()))
        points = np.vstack((face, face+[.004, 0., 0.]))
        normals = np.tile([1., 0., 0.], (len(points), 1))
        context = make_context(points, normals)
        report = classify_geometry(context, workers=1)
        self.assertGreater(report["counts"]["fixture"] / len(points), .95)
        self.assertEqual(report["counts"]["rebar"], 0)

    def test_supported_cells_keep_contact_points_without_normal_rollback(self):
        points, normals = round_tube()
        contact_ids = np.arange(0, len(points), 32)
        # Sparse mixed normals at a contact share cells with a supported bar.
        # They should inherit its class, rather than becoming an unknown band.
        context = make_context(np.vstack((points, points[contact_ids])),
                               np.vstack((normals, np.tile([0., 1., 0.], (len(contact_ids), 1)))))
        context.normal_valid[-1] = 0
        report = classify_geometry(context, workers=1)
        self.assertTrue(np.all(context.geometry_class[len(points):-1] == 3))
        self.assertEqual(context.geometry_class[-1], 3)
        self.assertFalse(report["diagnostics"]["edgeProtection"])

    def test_square_tube_faces_are_retained_after_axial_rotation(self):
        points, normals = square_tube(half=.012)
        angle = .31
        rotation = np.array([[np.cos(angle), 0., np.sin(angle)], [0., 1., 0.],
                             [-np.sin(angle), 0., np.cos(angle)]])
        for transform in (np.eye(3), rotation):
            context = make_context(points@transform.T, normals@transform.T)
            classify_geometry(context, workers=1)
            for face in np.array_split(context.geometry_class, 4):
                self.assertGreater(np.mean(face == 2), .95)
            self.assertFalse(np.any(context.geometry_class == 3))

    def test_sparse_coplanar_wire_is_not_a_solid_fixture_anchor(self):
        corners = np.array([[0., 0., .04], [.2, 0., .04], [.1, 0., .10]])
        t = np.linspace(0., 1., 240, endpoint=False)[:, None]
        points = np.vstack([a+t*(b-a) for a, b in zip(corners, np.roll(corners, -1, axis=0))])
        context = make_context(points, np.tile([0., 1., 0.], (len(points), 1)))
        report = classify_geometry(context, workers=1)
        self.assertEqual(report["diagnostics"]["acceptedPlanePatches"], 0)
        # No cylinder evidence in these synthetic constant normals: remainder
        # follows the three-class scene policy, not an artificial fourth class.
        self.assertEqual(report["counts"]["fixture"], len(points))

    def test_sign_rotation_and_parallel_invariance_and_original_tree_reuse(self):
        points, normals = round_tube()
        original = make_context(points, normals)
        tree = original.tree
        classify_geometry(original, workers=1)
        signs = np.random.default_rng(73).choice([-1., 1.], len(points))
        flipped = make_context(points, normals*signs[:, None])
        classify_geometry(flipped, workers=min(4, available_workers()))
        np.testing.assert_array_equal(original.geometry_class, flipped.geometry_class)
        axis = np.array([1., 2., -1.])/np.sqrt(6)
        x, y, z = axis
        cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
        rotation = np.eye(3)+np.sin(.71)*cross+(1-np.cos(.71))*(cross@cross)
        rotated = make_context(points@rotation.T+[3000., -1500., 40.], normals@rotation.T)
        classify_geometry(rotated, workers=1)
        self.assertGreater(np.mean(rotated.geometry_class == 3), .98)
        self.assertIs(original.tree, tree)
        np.testing.assert_array_equal(original.positions, points)
        self.assertEqual(len(original.classification_cache["grid"].source_to_cell), len(points))

    def test_line_invalid_and_sphere_are_not_rebar(self):
        points = np.column_stack((np.arange(50)*.003, np.zeros((50, 2))))
        context = PointCloudContext.build(points)
        estimate_normals(context, k=16, workers=1)
        report = classify_geometry(context, workers=1)
        self.assertEqual(report["counts"]["fixture"], len(points))
        self.assertEqual(set(report["classNames"]), {"1", "2", "3"})
        rng = np.random.default_rng(10)
        normals = rng.normal(size=(3000, 3))
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        context = make_context(normals*.006, normals)
        report = classify_geometry(context, workers=1)
        self.assertEqual(report["counts"]["rebar"], 0)

    def test_rounded_square_tube_edges_belong_to_fixture(self):
        half, radius = .020, .004
        axial = np.arange(-.15, .151, .003)
        transverse = np.arange(-half+radius, half-radius+.001, .002)
        points, normals = [], []
        for axis, sign in ((0, -1), (0, 1), (2, -1), (2, 1)):
            y, a = np.meshgrid(axial, transverse)
            p = np.zeros((y.size, 3)); n = np.zeros_like(p)
            p[:, 1], p[:, axis], p[:, 2 if axis == 0 else 0] = y.ravel(), sign*half, a.ravel()
            n[:, axis] = sign
            points.append(p); normals.append(n)
        curved_start = sum(len(p) for p in points)
        for quadrant in range(4):
            theta, y = np.meshgrid(np.linspace(quadrant*np.pi/2, (quadrant+1)*np.pi/2, 13), axial)
            a = (quadrant+.5)*np.pi/2
            p = np.column_stack((np.sign(np.cos(a))*(half-radius)+radius*np.cos(theta.ravel()),
                                 y.ravel(), np.sign(np.sin(a))*(half-radius)+radius*np.sin(theta.ravel())))
            n = np.column_stack((np.cos(theta.ravel()), np.zeros(theta.size), np.sin(theta.ravel())))
            points.append(p); normals.append(n)
        context = make_context(np.vstack(points)+[0., 0., .04], np.vstack(normals))
        report = classify_geometry(context, workers=1)
        self.assertGreater(np.mean(context.geometry_class[curved_start:] == 2), .98)
        self.assertEqual(set(report["counts"]), {"table", "fixture", "rebar"})
        self.assertEqual(report["classPolicy"], "table-rebar-fixture-remainder")

    def test_dense_end_cap_is_recovered_from_adjacent_rebar(self):
        points, normals = round_tube(radius=.004, length=.18)
        x, z = np.meshgrid(np.arange(-.004, .0045, .0008), np.arange(-.004, .0045, .0008))
        disk = x*x + z*z <= .004**2
        cap = np.column_stack((x[disk], np.full(disk.sum(), .09), z[disk]+.04))
        context = make_context(np.vstack((points, cap)), np.vstack((normals, np.tile([0., 1., 0.], (len(cap), 1)))))
        # Dense cap returns tilt the estimated neighborhood normals away from
        # the side cylinder. This exercises the actual Step 1 -> Step 2 path.
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        self.assertGreater(np.mean(context.geometry_class[len(points):] == 3), .95)
        self.assertGreater(np.count_nonzero(context.geometry_recovered[len(points):]), 0)

    def test_crossing_with_mixed_normals_is_recovered(self):
        points, normals = round_tube(radius=.005, length=.18)
        angle = np.pi/3
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0.], [np.sin(angle), np.cos(angle), 0.], [0., 0., 1.]])
        crossing = points@rotation.T + [0., 0., .010]
        context = make_context(np.vstack((points, crossing)), np.vstack((normals, normals@rotation.T)))
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        intersection = np.linalg.norm(context.positions[:, :2], axis=1) < .018
        self.assertGreater(np.mean(context.geometry_class[intersection] == 3), .95)
        self.assertTrue(np.any(context.geometry_recovered[intersection]))

    def test_recovery_does_not_consume_adjacent_square_tube(self):
        bar, bar_normals = round_tube()
        for half in (.010, .012):
            fixture, fixture_normals = square_tube(half=half)
            fixture[:, 0] += half+.008  # Face is 4 mm from the steel surface.
            points = np.vstack((bar, fixture))
            context = make_context(points, np.vstack((bar_normals, fixture_normals)))
            estimate_normals(context, k=32, workers=1)
            classify_geometry(context, workers=1)
            self.assertFalse(np.any(context.geometry_recovered[len(bar):]))
            self.assertTrue(np.all(context.geometry_class[len(bar):] == 2))


if __name__ == "__main__":
    unittest.main()
