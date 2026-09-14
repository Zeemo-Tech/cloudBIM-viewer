"""Terminal geometry can be completed from a confirmed body and exact design."""
import copy
import unittest
import numpy as np
from scipy.spatial import cKDTree
from algorithms.rebar_control_curves import fit_curved_pieces


def terminal_inventory():
    angle, radius, tail = 3*np.pi/4, .02, .04
    end = np.array([radius*np.sin(angle), radius*(1-np.cos(angle)), 0.])
    tip = end + tail*np.array([np.cos(angle), np.sin(angle), 0.])
    primitives = [
        {'kind': 'line', 'startM': [-1., 0., 0.], 'endM': [0., 0., 0.]},
        {'kind': 'arc', 'startM': [0., 0., 0.], 'endM': end.tolist(),
         'centerM': [0., radius, 0.], 'normal': [0., 0., 1.], 'radiusM': radius, 'sweepRad': angle},
        {'kind': 'line', 'startM': end.tolist(), 'endM': tip.tolist()},
    ]
    inventory = {'bars': [{'designBarId': 'bar', 'diameterM': .006, 'curvePrimitives': primitives}],
                 'units': [{'designUnitId': 'body', 'designBarId': 'bar', 'kind': 'straight',
                            'startM': [-1., 0., 0.], 'endM': [0., 0., 0.], 'diameterM': .006}]}
    rows = [{'id': 1, 'designUnitId': 'body', 'designBarId': 'bar', 'kind': 'straight',
             'status': 'fitted', 'pointCount': 0, 'centerlineM': [[-1., .003, .002], [0., .003, .002]],
             'diameterM': .006, 'designLengthM': 1., 'axisEvidenceRangeM': [.05, .92]}]
    return inventory, rows


def run(inventory, rows):
    points = np.array([[3., 4., 5.], [4., 5., 6.]])
    owner, status = np.array([0, 9], np.uint32), np.array([2, 1], np.uint8)
    before = points.copy(), owner.copy(), status.copy(), copy.deepcopy(inventory)
    reports, summary = fit_curved_pieces(points, None, inventory, rows, status, owner,
        tree=cKDTree(np.empty((0, 3))), source_ids=np.empty(0, np.int64))
    return reports, summary, before, (points, owner, status, inventory)


class TerminalCompletionTests(unittest.TestCase):
    def test_missing_scan_gets_exact_design_terminal_attached_to_observed_body(self):
        inventory, rows = terminal_inventory()
        original = copy.deepcopy(rows[0]['centerlineM'])
        reports, summary, before, after = run(inventory, rows)
        self.assertEqual(len(reports), 1)
        piece = reports[0]
        self.assertEqual(piece['connectionStatus'], 'design-inferred')
        self.assertEqual(piece['inferenceMethod'], 'body-anchored-design-terminal')
        self.assertEqual(piece['status'], 'pending')
        self.assertEqual(piece['pointCount'], 0)
        self.assertNotIn('centerlineM', piece)
        curve = np.array(piece['inferredCenterlineM'])
        np.testing.assert_allclose(curve[0], rows[0]['bodyDisplayCenterlineM'][-1])
        np.testing.assert_allclose(curve[-1], np.array(inventory['bars'][0]['curvePrimitives'][-1]['endM'])+[0., .003, .002])
        self.assertAlmostEqual(np.linalg.norm(curve[-1]-curve[-2]), .04)
        self.assertEqual(rows[0]['centerlineM'], original)
        self.assertEqual(summary['inferredPieces'], 1)
        self.assertEqual(summary['unresolvedPieces'], 0)
        for original, actual in zip(before[:3], after[:3]):np.testing.assert_array_equal(original, actual)
        self.assertEqual(before[3], after[3])

    def test_reversed_analytic_path_has_same_attached_hook(self):
        inventory, rows = terminal_inventory()
        forward, _, _, _ = run(inventory, copy.deepcopy(rows))
        primitives = inventory['bars'][0]['curvePrimitives']
        for primitive in primitives:
            primitive['startM'], primitive['endM'] = primitive['endM'], primitive['startM']
            if primitive['kind'] == 'arc':primitive['sweepRad'] *= -1
        primitives.reverse()
        reverse, _, _, _ = run(inventory, rows)
        np.testing.assert_allclose(reverse[0]['inferredCenterlineM'], forward[0]['inferredCenterlineM'], atol=1e-10)

    def test_rejected_unique_support_can_fall_back_without_fitted_geometry_fields(self):
        from unittest.mock import patch
        from algorithms.rebar_control_curves import _terminal_model
        def candidate(points, normals, piece, rows, **kwargs):
            model, seed, *_ = _terminal_model(piece, rows)
            curve, meta = model(seed)
            evidence = dict(support=np.array([True]), residual=np.array([0.]),
                bendSupport=48, coveredBins=8, crossSectionSpread=1., normalSpread=1.,
                bendMaxGapM=0., attachmentMaxGapM=0.)
            return dict(curve=curve, meta=meta, evidence=evidence,
                validationMaxChangeM=0., validationSupport=48), 'parametric-tube-supported'
        inventory, rows = terminal_inventory()
        # Simulate a local fit that loses all support in the final unique-owner pass.
        with patch('algorithms.rebar_control_curves._fit_piece', side_effect=candidate):
            reports, summary, _, _ = run(inventory, rows)
        self.assertEqual(reports[0]['reason'], 'insufficient-unique-curve-support')
        self.assertEqual(reports[0]['connectionStatus'], 'design-inferred')
        self.assertNotIn('centerlineM', reports[0])
        self.assertNotIn('fittedLengthM', reports[0])
        self.assertEqual(summary['unresolvedPieces'], 0)

    def test_unconfirmed_wrong_parent_duplicate_or_wrong_direction_body_not_completed(self):
        for case in ('pending', 'parent', 'duplicate', 'direction'):
            with self.subTest(case=case):
                inventory, rows = terminal_inventory()
                if case == 'pending':rows[0]['status'] = 'pending'
                if case == 'parent':rows[0]['designBarId'] = 'different'
                if case == 'duplicate':rows.append(dict(rows[0], id=2))
                if case == 'direction':rows[0]['centerlineM'] = [[0., -1., 0.], [0., 0., 0.]]
                reports, summary, _, _ = run(inventory, rows)
                self.assertEqual(summary['inferredPieces'], 0)
                self.assertTrue(all('inferredCenterlineM' not in report for report in reports))
                self.assertTrue(all('bodyDisplayCenterlineM' not in row for row in rows))

    def test_non_tangent_tail_is_not_silently_replaced_by_design_formula(self):
        inventory, rows = terminal_inventory()
        inventory['bars'][0]['curvePrimitives'][-1]['endM'][2] += .03
        reports, summary, _, _ = run(inventory, rows)
        self.assertEqual(summary['inferredPieces'], 0)
        self.assertNotIn('inferredCenterlineM', reports[0])

    def test_single_attachment_cannot_overwrite_existing_connection(self):
        from algorithms.rebar_control_curves import _stage_inferred_attachments
        _, rows = terminal_inventory(); rowmap = {'body': rows[0]}
        existing = {'body': {'end': np.array([0., .003, .002])}}
        before = existing['body']['end'].copy()
        result = _stage_inferred_attachments(rowmap, existing,
            [({'designUnitId': 'body', 'side': 'end'}, np.array([-.02, .003, .002]))], expected_count=1)
        self.assertIsNone(result)
        np.testing.assert_array_equal(existing['body']['end'], before)

    def test_design_bend_smaller_than_steel_radius_is_not_completed(self):
        inventory, rows = terminal_inventory()
        inventory['bars'][0]['diameterM'] = .05
        inventory['units'][0]['diameterM'] = .05
        reports, summary, _, _ = run(inventory, rows)
        self.assertEqual(summary['inferredPieces'], 0)
        self.assertNotIn('inferredCenterlineM', reports[0])


if __name__ == '__main__':unittest.main()
