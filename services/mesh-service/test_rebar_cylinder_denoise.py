import unittest
import numpy as np

from algorithms.rebar_cylinder_denoise import fit_instance_denoise


def cylinder(start, end, radius=.01, rings=100, sides=12):
    start, end = np.asarray(start, float), np.asarray(end, float)
    axis = end - start; axis /= np.linalg.norm(axis)
    helper = np.eye(3)[np.argmin(np.abs(axis))]
    u = np.cross(axis, helper); u /= np.linalg.norm(u); v = np.cross(axis, u)
    t, angle = np.meshgrid(np.linspace(0, 1, rings), np.linspace(0, 2*np.pi, sides, endpoint=False))
    return start + t.ravel()[:, None] * (end-start) + radius * (np.cos(angle.ravel())[:, None]*u + np.sin(angle.ravel())[:, None]*v)


def inputs(points, length=1., instance=1, segments=None):
    report = {"instances": [{"id": instance, "designUnitId": "bar/unit0"}], "segments": segments or []}
    inventory = {"units": [{"designUnitId": "bar/unit0", "designBarId": "bar", "diameterM": .02, "lengthM": length}],
                 "bars": [{"designBarId": "bar", "unitIds": ["bar/unit0"]}]}
    return points, np.full(len(points), 3, np.uint8), np.full(len(points), instance, np.uint32), report, inventory


class CylinderDenoiseTests(unittest.TestCase):
    def test_known_hook_is_modelled_at_scan_pose_and_never_cut_by_straight_gate(self):
        from scipy.spatial.transform import Rotation
        theta = np.linspace(-np.pi/2, np.pi/2, 17)
        hook = np.column_stack((1 + .03*np.cos(theta), .03 + .03*np.sin(theta), np.zeros(len(theta))))
        path = np.vstack(([0, 0, 0], hook, [.94, .06, 0]))
        clean_main = cylinder([0, 0, 0], [1, 0, 0], radius=.004)
        clean_hook = np.vstack([cylinder(a, b, radius=.004, rings=8) for a, b in zip(path[1:-1], path[2:])])
        burr = cylinder([.3, .025, 0], [.4, .025, 0], radius=.004, rings=5)
        rotation = Rotation.from_euler('xyz', [24, -37, 65], degrees=True).as_matrix()
        shift = np.array([9., -4., 2.])
        points = np.vstack((clean_main, clean_hook, burr)) @ rotation.T + shift
        data = list(inputs(points))
        data[4]['units'][0].update(diameterM=.008, startM=[0, 0, 0], endM=[1, 0, 0])
        data[4]['bars'][0]['points'] = path.tolist()
        data[3]['clusters'] = [{'id': 99, 'category': 'curved-exterior', 'locked': True, 'finalInstanceId': 1}]
        cluster_ids = np.zeros(len(points), np.uint32)
        cluster_ids[len(clean_main):len(clean_main)+len(clean_hook)] = 99
        result, keep, removed = fit_instance_denoise(*data, cluster_ids=cluster_ids)
        row = result['instances'][0]
        self.assertEqual(row['shapeKind'], 'straight-with-bends')
        self.assertAlmostEqual(row['fittedStraightLengthM'], 1., places=8)
        self.assertAlmostEqual(row['fittedLengthM'], np.linalg.norm(np.diff(path, axis=0), axis=1).sum(), places=8)
        self.assertTrue(keep[cluster_ids == 99].all())
        self.assertGreater(int(removed[-len(burr):].sum()), len(burr)*.8)
        fitted = (np.asarray(row['bendCenterlinesM'][0]) - shift) @ rotation
        np.testing.assert_allclose(fitted, path[1:], atol=.003)
        # Moving/rotating the entire BIM changes no intrinsic shape or scan pose.
        bim_rotation = Rotation.from_euler('xyz', [-30, 77, 4], degrees=True).as_matrix()
        for key in ('startM', 'endM'):
            data[4]['units'][0][key] = (np.asarray(data[4]['units'][0][key]) @ bim_rotation.T + 500).tolist()
        data[4]['bars'][0]['points'] = (path @ bim_rotation.T + 500).tolist()
        second, keep2, removed2 = fit_instance_denoise(*data, cluster_ids=cluster_ids)
        np.testing.assert_allclose(second['instances'][0]['centerlineM'], row['centerlineM'], atol=1e-6)
        np.testing.assert_array_equal(keep, keep2)

    def test_removes_radial_burr_but_keeps_source_alignment(self):
        clean = cylinder([0, 0, 0], [1, 0, 0])
        burr = clean[:30] + [0, .02, 0]
        data = inputs(np.vstack((clean, burr)))
        report, keep, removed = fit_instance_denoise(*data)
        self.assertEqual(report["validation"]["status"], "applied")
        self.assertGreaterEqual(report["removedPointCount"], 25)
        self.assertEqual(keep.dtype, np.uint8); self.assertEqual(removed.dtype, np.uint8)
        np.testing.assert_array_equal(keep + removed, np.ones(len(keep), np.uint8))

    def test_count_mismatch_rolls_back_candidates(self):
        clean = cylinder([0, 0, 0], [1, 0, 0]); data = list(inputs(clean))
        data[4]["units"].append({"designUnitId": "other/unit0", "diameterM": .02, "lengthM": 1.})
        report, keep, removed = fit_instance_denoise(*data)
        self.assertEqual(report["validation"]["status"], "not_applied")
        self.assertEqual(report["validation"]["reason"], "matching_unit_count_mismatch")
        self.assertFalse(removed.any()); self.assertTrue(keep.all())

    def test_curved_or_hooked_instance_is_protected(self):
        points = cylinder([0, 0, 0], [.5, 0, 0])
        segments = [{"instanceId": 1, "startM": [0, 0, 0], "endM": [.5, 0, 0]},
                    {"instanceId": 1, "startM": [.5, 0, 0], "endM": [.5, .2, 0]}]
        report, keep, removed = fit_instance_denoise(*inputs(points, segments=segments))
        self.assertEqual(report["instances"][0]["reason"], "bent_or_hook_protected")
        self.assertFalse(removed.any()); self.assertTrue(keep.all())

    def test_nonsteel_records_are_neither_exported_nor_removed(self):
        data = list(inputs(cylinder([0, 0, 0], [1, 0, 0])))
        data[1][:5] = 2; data[2][:5] = 0
        report, keep, removed = fit_instance_denoise(*data)
        self.assertFalse(keep[:5].any()); self.assertFalse(removed[:5].any())
        np.testing.assert_array_equal(keep + removed, data[1] == 3)
        self.assertEqual(report['pointsAfter'], int(keep.sum()))
        self.assertTrue(report['validation']['instanceIdsPreserved'])

    def test_equal_count_with_duplicate_design_assignment_is_not_accepted(self):
        data = list(inputs(np.vstack((cylinder([0, 0, 0], [1, 0, 0]), cylinder([0, 1, 0], [1, 1, 0])))))
        data[2][1200:] = 2
        data[3]['instances'].append({'id': 2, 'designUnitId': 'bar/unit0'})
        data[4]['units'].append({'designUnitId': 'bar/unit1', 'diameterM': .02, 'lengthM': 1.})
        report, keep, removed = fit_instance_denoise(*data)
        self.assertEqual(report['validation']['reason'], 'design_unit_assignment_mismatch')
        self.assertFalse(removed.any())
        self.assertTrue(report['validation']['countMatchesDesign'])
        self.assertFalse(report['validation']['designAssignmentsUnique'])

    def test_partial_scan_cylinder_uses_exact_design_length_at_scan_pose(self):
        data = list(inputs(cylinder([7., -2., 3.], [7.8, -2., 3.]), length=1.))
        # These intentionally unrelated design coordinates must not seed pose.
        data[4]['units'][0].update(startM=[-100, 0, 0], endM=[-100, 0, 1])
        report, _, _ = fit_instance_denoise(*data)
        row = report['instances'][0]
        centerline = np.array(row['centerlineM'])
        self.assertAlmostEqual(float(np.linalg.norm(np.diff(centerline, axis=0), axis=1).sum()), 1., places=9)
        np.testing.assert_allclose((centerline[0] + centerline[-1]) / 2, [7.4, -2., 3.], atol=1e-4)
        self.assertEqual(report['validation']['fittedInstanceCount'], 1)
        self.assertTrue(report['validation']['allInstancesFitted'])

    def test_overlong_displaced_short_bar_still_has_design_length_cylinder(self):
        data = inputs(cylinder([4., -2., .05], [4., -1.60, .05], radius=.004), length=.28)
        data[4]['units'][0]['diameterM'] = .008
        report, keep, removed = fit_instance_denoise(*data)
        row = report['instances'][0]
        self.assertIsNotNone(row['centerlineM'])
        self.assertEqual(row['fitStatus'], 'fitted')
        self.assertEqual(row['status'], 'retained')
        self.assertEqual(row['reason'], 'observed_span_exceeds_dimension')
        length = np.linalg.norm(np.diff(row['centerlineM'], axis=0), axis=1).sum()
        self.assertAlmostEqual(length, .28, places=9)
        self.assertTrue(keep.all()); self.assertFalse(removed.any())

    def test_bowed_axis_length_is_arc_length_and_missing_ends_extend_stably(self):
        points = cylinder([0, 0, 0], [.8, 0, 0])
        points[:, 1] += .004 * np.sin(np.pi * points[:, 0] / .8)
        report, _, _ = fit_instance_denoise(*inputs(points, length=1.2))
        row = report['instances'][0]
        self.assertEqual(row['fitStatus'], 'fitted')
        axis = np.array(row['centerlineM'])
        self.assertAlmostEqual(np.linalg.norm(np.diff(axis, axis=0), axis=1).sum(), 1.2, places=9)
        self.assertLess(np.abs(axis[:, 1:]).max(), .02)
        self.assertTrue(report['validation']['cylinderLengthsMatchDesign'])

    def test_opposite_small_segment_tilts_do_not_hide_a_straight_bar(self):
        tilt = np.tan(np.deg2rad(6.))
        segments = [{'instanceId': 1, 'startM': [0, 0, 0], 'endM': [.5, .5*tilt, 0]},
                    {'instanceId': 1, 'startM': [.5, 0, 0], 'endM': [1, -.5*tilt, 0]}]
        report, _, _ = fit_instance_denoise(*inputs(cylinder([0, 0, 0], [1, 0, 0]), segments=segments))
        self.assertIsNotNone(report['instances'][0]['centerlineM'])
        self.assertEqual(report['instances'][0]['status'], 'applied')

    def test_translation_invariance(self):
        points = np.vstack((cylinder([0, 0, 0], [1, 0, 0]), cylinder([0, 0, 0], [1, 0, 0])[:24] + [0, .02, 0]))
        first = fit_instance_denoise(*inputs(points))
        shift = np.array([1723.4, -88.2, 921.7])
        second = fit_instance_denoise(*inputs(points + shift))
        np.testing.assert_array_equal(first[1], second[1])
        np.testing.assert_array_equal(first[2], second[2])
        self.assertEqual(first[0]["removedPointCount"], second[0]["removedPointCount"])


if __name__ == "__main__":
    unittest.main()
