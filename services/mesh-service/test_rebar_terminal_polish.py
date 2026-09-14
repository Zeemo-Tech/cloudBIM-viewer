import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_terminal_polish import _design_radius, polish_fixture_terminals


def cylinder_shell(start, end, radius=.004, along=31, around=12):
    x = np.linspace(start, end, along)
    theta = np.linspace(0, 2*np.pi, around, endpoint=False)
    xx, tt = np.meshgrid(x, theta)
    return np.column_stack((xx.ravel(), radius*np.cos(tt).ravel(), radius*np.sin(tt).ravel()))


def merged_case(tail_inner=.009):
    left = cylinder_shell(-.20, -.015)
    right = cylinder_shell(.015, .20)
    y = np.r_[np.linspace(-.035, -tail_inner, 14), np.linspace(tail_inner, .035, 14)]
    left_tail = np.column_stack((np.full(len(y), -.015), y, np.full(len(y), .004)))
    right_tail = np.column_stack((np.full(len(y), .015), y, np.full(len(y), .004)))
    tails = np.vstack((left_tail, right_tail))
    fixture = tails+np.array([0., 0., -.001])
    positions = np.vstack((left, right, tails, fixture))
    n_left, n_right, n_tail = len(left), len(right), len(tails)
    owner = np.r_[np.full(n_left+n_right+n_tail, 7), np.zeros(len(fixture))].astype(np.uint32)
    segment = np.r_[np.full(n_left, 1), np.full(n_right, 2),
                    np.r_[np.full(len(left_tail), 1), np.full(len(right_tail), 2)],
                    np.zeros(len(fixture))].astype(np.uint32)
    out = dict(complete_class=np.where(owner, 3, 2).astype(np.uint8),
               complete_instance=owner, complete_segment=segment,
               complete_confidence=np.where(owner, .9, 0).astype(np.float32))
    segments = [
        dict(id=1, instanceId=7, startM=[-.20, 0., 0.], endM=[-.015, 0., 0.], radiusM=.004),
        dict(id=2, instanceId=7, startM=[.015, 0., 0.], endM=[.20, 0., 0.], radiusM=.004),
    ]
    units = [dict(designUnitId='bar/unit0', diameterM=.008)]
    context = SimpleNamespace(positions=positions, tree=cKDTree(positions))
    return context, out, segments, units, cKDTree(fixture), slice(n_left+n_right, n_left+n_right+n_tail), n_left+n_right


class FixtureTerminalPolishTests(unittest.TestCase):
    def test_diameter_is_selected_only_from_six_and_eight_mm_design_families(self):
        units = [dict(designUnitId='six', diameterM=.006),
                 dict(designUnitId='eight', diameterM=.008)]
        cases = [
            (7, {7: {0}}, .0045, .003, 'matched-design-unit', 'six'),
            (8, {8: {1}}, .0030, .004, 'matched-design-unit', 'eight'),
            (9, {}, .0032, .003, 'nearest-design-family', None),
            (10, {}, .0038, .004, 'nearest-design-family', None),
        ]
        for owner, associations, observed, expected, source, unit_id in cases:
            with self.subTest(owner=owner):
                radius, actual_source, actual_unit = _design_radius(
                    owner, associations, units, observed)
                self.assertEqual(radius, expected)
                self.assertEqual(actual_source, source)
                self.assertEqual(actual_unit, unit_id)

    def test_finds_clamp_cut_ends_inside_one_merged_instance_and_removes_t_tails(self):
        context, out, segments, units, fixture_tree, tails, shell_count = merged_case()

        operations, report = polish_fixture_terminals(
            context, out, segments, units, {7: {0}}, fixture_tree, workers=2)

        np.testing.assert_array_equal(out['complete_class'][:shell_count], 3)
        np.testing.assert_array_equal(out['complete_class'][tails], 4)
        np.testing.assert_array_equal(out['complete_instance'][tails], 0)
        self.assertEqual(report['openEndpointCount'], 4)
        self.assertEqual(report['fixtureTruncatedEndpointCount'], 2)
        self.assertEqual(report['fittedEndpointCount'], 2)
        self.assertEqual(report['removedPointCount'], tails.stop-tails.start)
        self.assertEqual(report['diameterSources'], {'matched-design-unit': 2})
        self.assertEqual({item['diameterM'] for item in report['terminals']}, {.008})
        self.assertEqual(report['surfaceToleranceM'], .001)
        self.assertEqual(len(operations), 2)

    def test_fixed_one_mm_limit_removes_more_design_diameter_tail_than_two_mm(self):
        reports = []
        for tolerance in (.001, .002):
            context, out, segments, units, fixture_tree, _, _ = merged_case(tail_inner=.0038)
            _, report = polish_fixture_terminals(
                context, out, segments, units, {7: {0}}, fixture_tree,
                workers=2, surface_tolerance=tolerance)
            reports.append(report)
        self.assertGreater(reports[0]['removedPointCount'], reports[1]['removedPointCount'])
        self.assertEqual(reports[0]['surfaceToleranceM'], .001)

    def test_fixture_contact_on_expected_cylinder_surface_is_retained(self):
        context, out, segments, units, fixture_tree, tails, _ = merged_case()
        # Turn the synthetic tails into legitimate cylinder-surface samples.
        out['complete_class'][tails] = 2
        out['complete_instance'][tails] = 0
        out['complete_segment'][tails] = 0

        _, report = polish_fixture_terminals(
            context, out, segments, units, {7: {0}}, fixture_tree, workers=2)

        self.assertEqual(report['removedPointCount'], 0)
        self.assertTrue(np.all(out['complete_class'][out['complete_instance'] == 7] == 3))

    def test_parallel_and_serial_results_are_deterministic(self):
        cases = [merged_case(), merged_case()]
        reports = []
        for workers, (context, out, segments, units, fixture_tree, _, _) in zip((1, 4), cases):
            _, report = polish_fixture_terminals(
                context, out, segments, units, {7: {0}}, fixture_tree, workers=workers)
            reports.append((out['complete_class'].copy(), out['complete_instance'].copy(), report['removedPointCount']))
        np.testing.assert_array_equal(reports[0][0], reports[1][0])
        np.testing.assert_array_equal(reports[0][1], reports[1][1])
        self.assertEqual(reports[0][2], reports[1][2])


if __name__ == '__main__':
    unittest.main()
