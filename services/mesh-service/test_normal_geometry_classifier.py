import unittest
from unittest.mock import patch
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
    def test_sparse_apparent_fixture_face_does_not_veto_an_inclined_bar(self):
        points, normals = round_tube(radius=.004, length=.18)
        angle = np.pi/4
        rotation = np.array([[1., 0., 0.], [0., np.cos(angle), -np.sin(angle)],
                             [0., np.sin(angle), np.cos(angle)]])
        context = make_context(points@rotation.T, normals@rotation.T)
        estimate_normals(context, k=32, workers=1)

        def sparse_face(points, features, params, workers):
            # Reproduce an upstream planar grouping that spans mostly empty
            # space between tangent strips, as observed on the real web bars.
            return np.zeros(len(points), np.int32), [{
                'normal': np.array([1., 0., 0.]), 'long_axis': np.array([0., 1., 1.])/np.sqrt(2),
                'center': points.mean(axis=0), 'widths': np.array([.004, .020, .18]),
                'cells': len(points), 'fillRatio': .30,
            }], 1

        with patch('algorithms.normal_geometry_classifier.planar_patches', side_effect=sparse_face):
            report = classify_geometry(context, workers=1)
        self.assertGreater(np.mean(context.geometry_class == 3), .98)
        self.assertGreater(report['recovery']['sparseFaceRecoveredPoints'], 0)

    def test_crowded_thick_parallel_bars_keep_both_circular_surfaces(self):
        for radius in (.005, .006):
            points, normals = round_tube(radius=radius)
            for delta in ([2*radius+.002, 0., 0.], [0., 0., 2*radius+.002]):
                with self.subTest(radius=radius, delta=delta):
                    context = make_context(np.vstack((points, points+delta)), np.vstack((normals, normals)))
                    estimate_normals(context, k=32, workers=1)
                    classify_geometry(context, workers=1)
                    for bar in np.array_split(context.geometry_class, 2):
                        self.assertGreater(np.mean(bar == 3), .99)

    def test_crowded_recovery_is_sign_and_worker_invariant(self):
        points, normals = round_tube(radius=.006)
        xyz = np.vstack((points, points+[.014, 0., 0.]))
        first = make_context(xyz, np.vstack((normals, normals)))
        estimate_normals(first, k=32, workers=1)
        second = make_context(xyz, first.normals*np.random.default_rng(21).choice([-1., 1.], len(xyz))[:, None])
        classify_geometry(first, workers=1)
        classify_geometry(second, workers=min(2, available_workers()))
        np.testing.assert_array_equal(first.geometry_class, second.geometry_class)
        np.testing.assert_array_equal(first.geometry_recovered, second.geometry_recovered)

    def test_detached_dense_blobs_cannot_borrow_a_nearby_cylinder(self):
        bar, normals = round_tube(length=.18)
        rng = np.random.default_rng(123)
        sphere = rng.normal(size=(600, 3))
        sphere /= np.linalg.norm(sphere, axis=1)[:, None]
        # Both the old seed/near-neighbour rules and the end-recovery rule
        # classified every return in these 4 mm-wide spheres as steel.
        for center in ([.010, 0., .04], [.013, 0., .04],
                       [0., .115, .04], [0., .130, .04], [.010, .06, .04]):
            with self.subTest(center=center):
                points = np.vstack((bar, sphere*.002+center))
                context = make_context(points, np.vstack((normals, sphere)))
                estimate_normals(context, k=32, workers=1)
                classify_geometry(context, workers=1)
                self.assertTrue(np.all(context.geometry_class[:len(bar)] == 3))
                self.assertFalse(np.any(context.geometry_class[len(bar):] == 3))
                self.assertFalse(np.any(context.geometry_recovered[len(bar):]))
                np.testing.assert_array_equal(context.positions, points)

    def test_detached_short_bar_establishes_its_own_support(self):
        bar, normals = round_tube(length=.18)
        short, short_normals = round_tube(length=.03)
        short += [.070, .03, .02]
        context = make_context(np.vstack((bar, short)), np.vstack((normals, short_normals)))
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        self.assertGreater(np.mean(context.geometry_class[len(bar):] == 3), .95)

    def test_narrow_scan_fragment_can_bridge_a_gap_without_admitting_a_blob(self):
        bar, normals = round_tube(length=.18)
        x, y = np.meshgrid(np.arange(-.002, .0021, .001), np.arange(-.007, .0075, .001))
        fragment = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .028)))
        context = make_context(np.vstack((bar, fragment)),
            np.vstack((normals, np.tile([0., 0., 1.], (len(fragment), 1)))))
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        self.assertTrue(np.all(context.geometry_class == 3))
        cache = context.classification_cache
        cells = cache['grid'].source_to_cell
        components = cache['features']['support_component']
        self.assertEqual(len(np.intersect1d(components[cells[:len(bar)]], components[cells[len(bar):]])), 0)

    def test_bent_bar_is_not_rejected_for_lacking_one_global_straight_axis(self):
        angle, theta = np.meshgrid(np.linspace(0, np.pi/2, 40), np.linspace(0, 2*np.pi, 24, endpoint=False))
        normals = np.column_stack((np.cos(angle.ravel())*np.cos(theta.ravel()),
                                   np.sin(angle.ravel())*np.cos(theta.ravel()), np.sin(theta.ravel())))
        axis = np.column_stack((.035*np.cos(angle.ravel()), .035*np.sin(angle.ravel()), np.full(angle.size, .04)))
        context = make_context(axis+.004*normals, normals)
        estimate_normals(context, k=32, workers=1)
        classify_geometry(context, workers=1)
        self.assertTrue(np.all(context.geometry_class == 3))

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
