"""A partly observed hook and repeated refinement retain real surface evidence."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_control_curves import _local_curve_support, fit_curved_pieces
from test_rebar_control_curves_adversarial import (
    analytic_terminal, inventory_for, terminal_fixture, tube,
)


class TerminalRecoveryTests(unittest.TestCase):
    def test_short_stable_surface_patch_is_not_discarded_by_whole_hook_count(self):
        curve = np.array([[0., 0., 0.], [.12, 0., 0.]])
        stations = np.linspace(.0645, .0785, 15)
        angles = np.array([-.8, .8])
        radial = np.c_[np.zeros(2), np.cos(angles), np.sin(angles)]
        normals = np.tile(radial, (len(stations), 1))
        centers = np.c_[stations, np.zeros((len(stations), 2))]
        points = np.repeat(centers, 2, axis=0) + .004 * normals
        # Unrelated records make up the rest of the candidate crop. They must
        # not be needed to certify this 14 mm, 30-point observed patch.
        points = np.vstack((points, np.tile([.03, .05, .05], (24, 1))))
        normals = np.vstack((normals, np.tile([0., 1., 0.], (24, 1))))
        selected, intervals, change = _local_curve_support(
            points, normals, curve, curve.copy(), .004, np.array([0., 0., 1.]))
        self.assertTrue(selected[:30].all())
        self.assertFalse(selected[30:].any())
        self.assertEqual(len(intervals), 1)
        np.testing.assert_allclose(intervals[0], [.0645, .0785])
        self.assertEqual(change, 0.)
        # Removing unrelated records cannot invalidate the observed patch.
        selected, intervals, _ = _local_curve_support(
            points[:30], normals[:30], curve, curve.copy(), .004, np.array([0., 0., 1.]))
        self.assertTrue(selected.all())
        np.testing.assert_allclose(intervals, [[.0645, .0785]])

    def test_second_pass_keeps_confirmed_hook_without_needing_new_points(self):
        piece, row = terminal_fixture()
        centers, tangents, _ = analytic_terminal()
        points, normals = tube(centers, tangents, np.linspace(-1.2, 1.2, 12))
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        rows = [row]
        source_ids = np.arange(len(points), dtype=np.int64)
        with patch('algorithms.rebar_design_curves.build_design_curve_pieces', return_value=[piece]):
            first, _ = fit_curved_pieces(points, normals, inventory_for('a'), rows,
                status, owner, tree=cKDTree(points), source_ids=source_ids)
            self.assertEqual(first[0]['status'], 'fitted')
            before = owner.copy(), status.copy(), copy.deepcopy(rows[0]['centerlineM'])
            second, _ = fit_curved_pieces(points, normals, inventory_for('a'), rows,
                status, owner, tree=cKDTree(points), source_ids=source_ids)
        self.assertEqual(second[0]['status'], 'fitted')
        self.assertEqual(second[0]['addedPointCount'], 0)
        self.assertGreater(second[0]['retainedPointCount'], 48)
        np.testing.assert_allclose(second[0]['centerlineM'], first[0]['centerlineM'])
        np.testing.assert_array_equal(owner, before[0])
        np.testing.assert_array_equal(status, before[1])
        self.assertEqual(rows[0]['centerlineM'], before[2])

    def test_second_pass_keeps_local_evidence_without_filling_occluded_arc(self):
        from algorithms.rebar_control_curves import _terminal_model
        from algorithms.rebar_design_curves import build_design_curve_pieces
        from test_rebar_control_terminal_completion import terminal_inventory
        from test_rebar_control_curves import tube as curved_tube
        inventory, rows = terminal_inventory()
        piece = build_design_curve_pieces(inventory)[0]
        piece['_designAnchors'] = [np.zeros(3)]
        piece['_designTangents'] = [np.array([1., 0., 0.])]
        curve, _ = _terminal_model(piece, {'body': rows[0]})[0](
            np.array([.003, .001, 0., 0., .021, 2.38]))
        patches = [curved_tube(a + (b-a)*np.linspace(0, 1, 8, endpoint=False)[:, None])
                   for i, (a, b) in enumerate(zip(curve[:-1], curve[1:])) if not 26 <= i < 45]
        points = np.vstack([p for p, _ in patches])
        normals = np.vstack([n for _, n in patches])
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        kwargs = dict(tree=cKDTree(points), source_ids=np.arange(len(points)))
        first, _ = fit_curved_pieces(points, normals, inventory, rows, status, owner, **kwargs)
        self.assertEqual(first[0]['status'], 'pending')
        self.assertGreater(first[0]['localSupport']['pointCount'], 24)
        before = owner.copy()
        second, _ = fit_curved_pieces(points, normals, inventory, rows, status, owner, **kwargs)
        self.assertEqual(second[0]['status'], 'pending')
        self.assertEqual(second[0]['pointCount'], 0)
        self.assertEqual(second[0]['localSupport']['addedPointCount'], 0)
        self.assertEqual(second[0]['localSupport']['pointCount'], first[0]['localSupport']['pointCount'])
        np.testing.assert_array_equal(owner, before)
        np.testing.assert_allclose(second[0]['localSupport']['intervalsM'], first[0]['localSupport']['intervalsM'])


if __name__ == '__main__':
    unittest.main()
