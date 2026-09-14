"""Synthetic evidence tests for the raw-point design control net."""
import json
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from algorithms.rebar_control_net import fit_control_net


def tube(start, end, radius=.004, along=80, around=16, bend=None):
    start, end = np.asarray(start, float), np.asarray(end, float)
    direction = end-start
    length = np.linalg.norm(direction)
    direction /= length
    helper = np.eye(3)[np.argmin(np.abs(direction))]
    u = np.cross(direction, helper); u /= np.linalg.norm(u)
    v = np.cross(direction, u)
    s, angle = np.meshgrid(np.linspace(0, 1, along),
                           np.linspace(0, 2*np.pi, around, endpoint=False), indexing="ij")
    centre = start+s.ravel()[:, None]*(end-start)
    if bend is not None:
        centre += bend(s.ravel())[:, None]*u
    radial = np.cos(angle.ravel())[:, None]*u + np.sin(angle.ravel())[:, None]*v
    return centre+radius*radial, radial


def inventory(lines, radius=.004, kinds=None, parent=False):
    kinds = kinds or ["straight"]*len(lines)
    units = []
    bars = []
    for i, ((start, end), kind) in enumerate(zip(lines, kinds)):
        start, end = np.asarray(start, float), np.asarray(end, float)
        length = float(np.linalg.norm(end-start))
        bar_id = "physical" if parent else f"b{i}"
        uid = f"{bar_id}/u{i}"
        units.append(dict(designBarId=bar_id, designUnitId=uid, startM=start.tolist(),
                          endM=end.tolist(), direction=((end-start)/length).tolist(),
                          lengthM=length, diameterM=2*radius, kind=kind,
                          source="synthetic"))
        bars.append(dict(designBarId=bar_id, unitIds=[uid], points=[start.tolist(), end.tolist()],
                         radiusM=radius, coverage="complete", source="synthetic"))
    return {"bars": bars, "units": units, "relations": []}


class ControlNetTests(unittest.TestCase):
    def fit(self, points, inv, normals=None, table=None, mode="aligned", **kwargs):
        table = np.zeros(len(points), bool) if table is None else table
        return fit_control_net(points, table, inv, mode=mode, normals=normals, **kwargs)

    def test_vote_groups_preserve_negative_cells_and_tied_seed_order(self):
        from algorithms.rebar_control_net import _circle_vote_groups
        rng = np.random.default_rng(37)
        for keys in (np.array([[-2,3], [0,-4], [-2,3], [0,-4], [-2,-4]]),
                     rng.integers(-120, 121, (20000, 2), dtype=np.int64),
                     np.zeros((30, 2), dtype=np.int64),
                     np.array([[0, np.iinfo(np.int64).max],
                               [1, np.iinfo(np.int64).max]], dtype=np.int64)):
            before = keys.copy()
            _, expected_inverse, expected_counts = np.unique(
                keys, axis=0, return_inverse=True, return_counts=True)
            inverse, counts = _circle_vote_groups(keys)
            np.testing.assert_array_equal(inverse, expected_inverse)
            np.testing.assert_array_equal(counts, expected_counts)
            np.testing.assert_array_equal(np.argsort(counts, kind='stable')[-24:],
                np.argsort(expected_counts, kind='stable')[-24:])
            np.testing.assert_array_equal(keys, before)

    def test_vote_groups_fall_back_without_integer_overflow(self):
        from algorithms.rebar_control_net import _circle_vote_groups
        limits = np.iinfo(np.int64)
        keys = np.array([[limits.min, 0], [limits.max, 0],
                         [0, limits.min], [0, limits.max]], dtype=np.int64)
        _, expected_inverse, expected_counts = np.unique(
            keys, axis=0, return_inverse=True, return_counts=True)
        inverse, counts = _circle_vote_groups(keys)
        np.testing.assert_array_equal(inverse, expected_inverse)
        np.testing.assert_array_equal(counts, expected_counts)

    def test_post_fusion_contract_excludes_nonsteel_and_reports_semantic_layers(self):
        bottom, bn = tube([0, 0, .02], [1, 0, .02], along=60)
        top, tn = tube([0, .08, .08], [1, .08, .08], along=60)
        fixture = np.column_stack((np.linspace(0, 1, 80), np.full(80, .2), np.full(80, .02)))
        points = np.vstack((bottom, top, fixture))
        normals = np.vstack((bn, tn, np.tile([0., 0., 1.], (len(fixture), 1))))
        fused = np.r_[np.full(len(bottom)+len(top), 3, np.uint8),
                      np.full(len(fixture), 2, np.uint8)]
        layers = np.r_[np.full(len(bottom), 1, np.uint8),
                       np.full(len(top), 2, np.uint8), np.zeros(len(fixture), np.uint8)]
        inv = inventory([([0, 0, .02], [1, 0, .02]),
                         ([0, .08, .08], [1, .08, .08])])
        # Design layer IDs are provenance and deliberately do not define the
        # measured bottom/top fitting partition.
        inv['units'][0]['layerId'] = 17
        inv['units'][1]['layerId'] = 23
        layering = {'layers': [
            {'id': 1, 'name': 'bottom', 'heightM': .02, 'lowM': .01, 'highM': .03},
            {'id': 2, 'name': 'top', 'heightM': .08, 'lowM': .07, 'highM': .09},
            {'id': 3, 'name': 'web'}]}
        report, attrs = self.fit(points, inv, normals, fused_classes=fused,
                                 layer_ids=layers, layering=layering, workers=2)
        self.assertEqual(report['inputStage'], 'post-fusion')
        self.assertEqual(report['counts']['fittedUnits'], 2)
        self.assertEqual(report['counts']['excluded'], len(fixture))
        self.assertTrue(np.all(attrs['control_status'][-len(fixture):] == 4))
        self.assertFalse(np.any(attrs['control_instance'][-len(fixture):]))
        self.assertEqual([row['fitLayerId'] for row in report['instances']], [1, 2])
        self.assertEqual([row['layerId'] for row in report['instances']], [17, 23])
        self.assertEqual([row['inputPoints'] for row in report['layers']],
                         [len(bottom), len(top), 0])
        counts = report['counts']
        self.assertEqual(counts['input'], sum(counts[key] for key in
            ('table', 'matched', 'pending', 'removed', 'excluded')))
        json.dumps(report, allow_nan=False)

    def test_fusion_excluded_neighbours_preserve_sparse_boundary_candidate(self):
        steel, normals = tube([0, 0, 0], [1, 0, 0], along=55)
        boundary = np.array([[.5, .012, 0.]])
        excluded = boundary + np.array([[0., .0004, 0.], [0., -.0004, 0.]])
        points = np.vstack((steel, boundary, excluded))
        all_normals = np.vstack((normals, [[0., 1., 0.]],
                                 np.tile([0., 1., 0.], (len(excluded), 1))))
        fused = np.r_[np.full(len(steel)+1, 3, np.uint8),
                      np.full(len(excluded), 2, np.uint8)]
        report, attrs = self.fit(points, inventory([([0, 0, 0], [1, 0, 0])]),
                                 all_normals, fused_classes=fused)
        self.assertEqual(report['counts']['fittedUnits'], 1)
        self.assertEqual(attrs['control_status'][len(steel)], 2)
        np.testing.assert_array_equal(attrs['control_status'][-len(excluded):], [4, 4])

    def test_web_layer_uses_horizontal_endpoint_halo_and_layer_zero_fallback(self):
        steel, normals = tube([0, 0, .02], [.18, 0, .08], along=75)
        z = steel[:, 2]
        layers = np.where(z < .031, 1, np.where(z > .069, 2, 3)).astype(np.uint8)
        # Some middle evidence is deliberately unassigned and must remain in
        # the geometric layer-0 fallback.
        layers[(z > .047) & (z < .053)] = 0
        inv = inventory([([0, 0, .02], [.18, 0, .08])], kinds=['web'])
        layering = {'layers': [
            {'id': 1, 'heightM': .02, 'lowM': .01, 'highM': .03},
            {'id': 2, 'heightM': .08, 'lowM': .07, 'highM': .09},
            {'id': 3, 'lowM': .03, 'highM': .07}]}
        report, attrs = self.fit(steel, inv, normals,
            fused_classes=np.full(len(steel), 3, np.uint8), layer_ids=layers,
            layering=layering)
        self.assertEqual(report['instances'][0]['fitLayerId'], 3)
        self.assertEqual(report['instances'][0]['status'], 'fitted')
        for layer_id in (0, 1, 2, 3):
            selected = layers == layer_id
            self.assertGreater(np.mean(attrs['control_status'][selected] == 1), .65)

    def test_mislayered_fused_steel_gets_bounded_geometric_retry(self):
        steel, normals = tube([0, 0, .02], [1, 0, .02], along=55)
        layering = {'layers': [
            {'id': 1, 'heightM': .02, 'lowM': .01, 'highM': .03},
            {'id': 2, 'heightM': .08, 'lowM': .07, 'highM': .09}]}
        report, attrs = self.fit(steel, inventory([([0, 0, .02], [1, 0, .02])]), normals,
            fused_classes=np.full(len(steel), 3, np.uint8),
            layer_ids=np.full(len(steel), 2, np.uint8), layering=layering)
        row = report['instances'][0]
        self.assertEqual(row['status'], 'fitted')
        self.assertEqual(row['fitLayerId'], 1)
        self.assertEqual(row['fitCandidateScope'], 'bounded-geometric-retry')
        self.assertGreater(np.count_nonzero(attrs['control_status'] == 1), .8*len(steel))

    def test_parallel_workers_are_deterministic(self):
        lines = [([0, 0, 0], [1, 0, 0]), ([0, .06, 0], [1, .06, 0])]
        clouds, normal_sets = zip(*(tube(a, b, along=55) for a, b in lines))
        points, normals = np.vstack(clouds), np.vstack(normal_sets)
        kwargs = dict(fused_classes=np.full(len(points), 3, np.uint8),
                      layer_ids=np.ones(len(points), np.uint8))
        sequential, seq_attrs = self.fit(points, inventory(lines), normals, workers=1, **kwargs)
        parallel, par_attrs = self.fit(points, inventory(lines), normals, workers=2, **kwargs)
        np.testing.assert_array_equal(seq_attrs['control_status'], par_attrs['control_status'])
        np.testing.assert_array_equal(seq_attrs['control_instance'], par_attrs['control_instance'])
        for key in ('counts', 'instances'):
            self.assertEqual(sequential[key], parallel[key])

    def test_fusion_arguments_must_be_source_aligned(self):
        points = np.zeros((4, 3))
        inv = inventory([([0, 0, 0], [1, 0, 0])])
        with self.assertRaises(ValueError):
            self.fit(points, inv, fused_classes=np.zeros(3, np.uint8))
        with self.assertRaises(ValueError):
            self.fit(points, inv, layer_ids=np.zeros(3, np.uint8))
        with self.assertRaises(ValueError):
            self.fit(points, inv, layer_ids=np.zeros(4, float))
        with self.assertRaises(ValueError):
            self.fit(points, inv, workers=0)

    def test_flat_tangential_fixture_is_pending_with_or_without_normals(self):
        x, y = np.meshgrid(np.linspace(0, 1, 90), np.linspace(-.001, .001, 9), indexing='ij')
        points = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .004)))
        for normals in (None, np.tile([0., 0., 1.], (len(points), 1))):
            report, attrs = self.fit(points, inventory([([0,0,0], [1,0,0])]), normals)
            self.assertEqual(report['counts']['fittedUnits'], 0)
            self.assertTrue(np.all(attrs['control_status'] == 2))

    def test_unmodeled_crossing_tube_is_not_deleted_as_burrs(self):
        known, kn = tube([-.5,0,0], [.5,0,0], along=72)
        crossing, cn = tube([0,-.5,0], [0,.5,0], along=72)
        report, attrs = self.fit(np.vstack((known,crossing)), inventory([([-.5,0,0],[.5,0,0])]), np.vstack((kn,cn)))
        self.assertEqual(report['counts']['fittedUnits'], 1)
        self.assertEqual(np.count_nonzero(attrs['control_status'] == 3), 0)
        self.assertGreater(np.count_nonzero(attrs['control_status'][len(known):] == 2), 900)

    def test_fitting_sample_budget_does_not_limit_full_source_assignment(self):
        points, normals = tube([0,0,0], [1,0,0], along=100, around=64)
        with patch('algorithms.rebar_control_net._MAX_UNIT_CANDIDATES', 1000):
            report, attrs = self.fit(points, inventory([([0,0,0],[1,0,0])]), normals)
        self.assertEqual(report['counts']['fittedUnits'], 1)
        self.assertGreater(report['counts']['matched'], 6000)
        self.assertEqual(report['counts']['matched'], np.count_nonzero(attrs['control_instance']))

    def test_auto_cannot_count_one_observed_line_as_two_design_units(self):
        from algorithms.rebar_control_net import _registration_score, _unit_rows
        inv = inventory([([0,-.02,0],[1,-.02,0]),([0,.02,0],[1,.02,0])])
        observed = [{'center': np.array([.5,0,0]), 'direction': np.array([1.,0,0]), 'length':1.}]
        _, count, _ = _registration_score(np.eye(3), np.zeros(3), _unit_rows(inv), observed)
        self.assertEqual(count, 1)
        points, normals = tube([0,0,0], [1,0,0])
        report, attrs = self.fit(points, inv, normals, mode='auto')
        self.assertLessEqual(report['registration']['matchedUnits'], 1)
        self.assertNotEqual(report['registration']['status'], 'supported')
        self.assertEqual(report['counts']['fittedUnits'], 0)
        self.assertTrue(np.all(attrs['control_status'] == 2))

    def test_raw_provenance_missing_and_outliers_are_not_fake_support(self):
        rng = np.random.default_rng(4)
        steel, normals = tube([0, 0, 0], [1, 0, 0])
        fixture = rng.uniform([-.2, -.25, -.2], [1.2, .25, .2], (800, 3))
        points = np.vstack((steel, fixture, [[9, 9, 9]]))
        all_normals = np.vstack((normals, np.zeros_like(fixture), [[0, 0, 1]]))
        inv = inventory([([0, 0, 0], [1, 0, 0]), ([0, .15, 0], [1, .15, 0])])
        inv["bars"].append(dict(designBarId="unresolved-fixture-like-parent", points=[],
                                boundsM=[[2, 2, 2], [3, 3, 3]], coverage="unresolved"))
        original = points.copy()
        report, attrs = self.fit(points, inv, all_normals)
        self.assertEqual(report["counts"]["fittedUnits"], 1)
        self.assertEqual(report["counts"]["designBars"], 3)
        self.assertEqual(report["instances"][1]["status"], "missing")
        self.assertEqual(attrs["control_status"].dtype, np.uint8)
        self.assertEqual(attrs["control_instance"].dtype, np.uint32)
        self.assertEqual(attrs["control_status"][-1], 2)
        self.assertEqual(len(attrs["control_status"]), len(points))
        np.testing.assert_array_equal(points, original)
        json.dumps(report, allow_nan=False)

    def test_smooth_local_bend_keeps_fixed_design_arc_length(self):
        steel, normals = tube([0, 0, 0], [1, 0, 0], bend=lambda s: .014*np.sin(np.pi*s))
        report, attrs = self.fit(steel, inventory([([0, 0, 0], [1, 0, 0])]), normals)
        row = report["instances"][0]
        self.assertEqual(row["status"], "fitted")
        curve = np.asarray(row["centerlineM"])
        self.assertAlmostEqual(np.linalg.norm(np.diff(curve, axis=0), axis=1).sum(), 1., places=12)
        self.assertGreater(curve[:, 1:].ptp(axis=0).max(), .008)
        self.assertGreater(np.count_nonzero(attrs["control_status"] == 1), .8*len(steel))

    def test_duplicate_design_identity_is_pending(self):
        steel, normals = tube([0, 0, 0], [1, 0, 0])
        inv = inventory([([0, 0, 0], [1, 0, 0]), ([0, 0, 0], [1, 0, 0])])
        report, attrs = self.fit(steel, inv, normals)
        self.assertTrue(all(row["status"] == "pending" for row in report["instances"]))
        self.assertFalse(np.any(attrs["control_instance"]))
        self.assertTrue(np.all(attrs["control_status"] == 2))

    def test_close_unequal_length_members_keep_their_own_surfaces(self):
        long, ln = tube([0, -.014, 0], [4, -.014, 0], along=240, around=16,
                        bend=lambda s: .003*np.sin(np.pi*s))
        short, sn = tube([.2, 0, 0], [3.8, 0, 0], along=260, around=32)
        inv = inventory([([0, 0, 0], [4, 0, 0]), ([.2, .004, 0], [3.8, .004, 0])])
        report, attrs = self.fit(np.vstack((long, short)), inv, np.vstack((ln, sn)))
        self.assertEqual(report['counts']['fittedUnits'], 2)
        first, second = report['instances']
        np.testing.assert_allclose(np.asarray(first['centerlineM'])[:, 1], -.014, atol=.0005)
        np.testing.assert_allclose(np.asarray(second['centerlineM'])[:, 1:], 0., atol=.0005)
        self.assertGreater(np.mean(attrs['control_instance'][:len(long)] == 1), .95)
        self.assertGreater(np.mean(attrs['control_instance'][len(long):] == 2), .95)
        self.assertFalse(np.any(attrs['control_instance'][:len(long)] == 2))
        self.assertFalse(np.any(attrs['control_instance'][len(long):] == 1))

    def test_unequal_overhang_cannot_hide_duplicate_observed_axis(self):
        steel, normals = tube([0, 0, 0], [4, 0, 0], along=200)
        inv = inventory([([0, 0, 0], [4, 0, 0]), ([.2, 0, 0], [3.8, 0, 0])])
        report, attrs = self.fit(steel, inv, normals)
        self.assertTrue(all(row['status'] == 'pending' for row in report['instances']))
        self.assertFalse(np.any(attrs['control_instance']))

    def test_short_bar_at_wrong_axial_interval_stays_pending(self):
        steel, normals = tube([.10, 0, 0], [.30, 0, 0], along=55)
        inv = inventory([([0, 0, 0], [.20, 0, 0])], kinds=["short"])
        report, attrs = self.fit(steel, inv, normals)
        self.assertEqual(report["instances"][0]["status"], "pending")
        self.assertFalse(np.any(attrs["control_instance"]))

    def test_rotated_short_hook_run_can_fit_without_changing_length(self):
        angle = np.deg2rad(34)
        end = np.array([.18*np.cos(angle), .18*np.sin(angle), 0])
        steel, normals = tube([0, 0, 0], end, along=60)
        inv = inventory([([0, 0, 0], [.18, 0, 0])], kinds=["short"])
        report, _ = self.fit(steel, inv, normals)
        row = report["instances"][0]
        self.assertEqual(row["status"], "fitted")
        self.assertGreater(row["directionDifferenceDeg"], 25)
        curve = np.asarray(row["centerlineM"])
        self.assertAlmostEqual(np.linalg.norm(np.diff(curve, axis=0), axis=1).sum(), .18, places=12)

    def test_auto_pose_ignores_saved_absolute_coordinates(self):
        lines = [([0, 0, 0], [1, 0, 0]), ([.18, -.15, .04], [.18, .55, .04]),
                 ([.55, -.10, .12], [.90, .35, .32])]
        clouds, normal_sets = zip(*(tube(a, b, along=70) for a, b in lines))
        design = inventory(lines, kinds=["straight", "straight", "web"])
        rotation = Rotation.from_rotvec([.42, -.31, .58]).as_matrix()
        translation = np.array([2.3, -1.7, .8])
        steel = np.vstack(clouds)@rotation.T+translation
        normals = np.vstack(normal_sets)@rotation.T
        rng = np.random.default_rng(9)
        fixture = rng.uniform([1.7, -2.2, .2], [3.2, -.8, 1.5], (500, 3))
        points = np.vstack((steel, fixture))
        all_normals = np.vstack((normals, np.zeros_like(fixture)))
        report, attrs = self.fit(points, design, all_normals, mode="auto")
        self.assertEqual(report["registration"]["status"], "supported")
        self.assertEqual(report["registration"]["identityStatus"], "supported")
        self.assertGreaterEqual(report["counts"]["fittedUnits"], 2)
        self.assertGreater(np.count_nonzero(attrs["control_status"] == 1), len(steel)*.65)
        # Input inventory remains at the origin: automatic pose did not refine a
        # supplied global transform and reports its independent transform.
        self.assertGreater(np.linalg.norm(report["registration"]["translationM"]), 1.)

        # Re-express the same design topology in an unrelated saved pose. Auto
        # must solve it back to the same scan geometry rather than benefit from
        # the supplied absolute coordinates.
        saved_rotation = Rotation.from_rotvec([-.7, .2, .3]).as_matrix()
        saved_translation = np.array([10., -4., 2.])
        moved_lines = [(np.asarray(a)@saved_rotation.T+saved_translation,
                        np.asarray(b)@saved_rotation.T+saved_translation) for a, b in lines]
        moved_design = inventory(moved_lines, kinds=["straight", "straight", "web"])
        moved_report, _ = self.fit(points, moved_design, all_normals, mode="auto")
        first = np.asarray([u["startM"] for u in report["inventory"]["units"]])
        second = np.asarray([u["startM"] for u in moved_report["inventory"]["units"]])
        np.testing.assert_allclose(first, second, atol=1e-8)

    def test_symmetric_auto_grid_reports_identity_ambiguity(self):
        lines = [([0, -.04, 0], [1, -.04, 0]), ([0, .04, 0], [1, .04, 0])]
        clouds, normal_sets = zip(*(tube(a, b) for a, b in lines))
        points, normals = np.vstack(clouds), np.vstack(normal_sets)
        report, attrs = self.fit(points, inventory(lines), normals, mode="auto")
        self.assertEqual(report["registration"]["status"], "supported")
        self.assertEqual(report["registration"]["identityStatus"], "ambiguous")
        self.assertTrue(report["registration"]["alternatives"])
        self.assertEqual(report["counts"]["fittedUnits"], 0)
        self.assertFalse(np.any(attrs["control_instance"]))


if __name__ == "__main__":
    unittest.main()
