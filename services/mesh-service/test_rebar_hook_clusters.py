import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_hook_clusters import polish_non_hook_terminals


class HookTerminalPolishTests(unittest.TestCase):
    def test_only_fixture_glue_at_non_hook_terminal_is_removed(self):
        straight = np.array([
            [.18, .004, 0.], [.20, -.004, 0.], [.22, 0., .004],
        ])
        glue = np.array([[.20, .018, 0.], [.21, -.017, .003]])
        hook_side_outlier = np.array([[.92, .018, 0.]])
        hook = np.array([[.90, .004, 0.], [.94, .004, 0.]])
        positions = np.vstack((straight, glue, hook_side_outlier, hook))
        count = len(positions)
        context = SimpleNamespace(positions=positions)
        out = dict(complete_class=np.full(count, 3, np.uint8),
                   complete_instance=np.full(count, 7, np.uint32),
                   complete_segment=np.ones(count, np.uint32),
                   complete_confidence=np.ones(count, np.float32))
        group = dict(rows=np.array([6, 7]), designUnitId='u1', designBarId='b1', radiusM=.004,
                     region={}, record={'id': 11, 'status': 'merged', 'finalInstanceId': 7})
        units = [{'designUnitId': 'u1', 'direction': [1., 0., 0.]}]
        segments = [{'id': 1, 'instanceId': 7, 'startM': [0., 0., 0.],
                     'endM': [1., 0., 0.], 'radiusM': .004}]
        fixture_tree = cKDTree(np.array([[.20, .010, 0.], [.21, -.010, .003]]))
        report = {}

        operations = polish_non_hook_terminals(
            context, out, [group], units, segments, fixture_tree, report)

        np.testing.assert_array_equal(out['complete_class'][:3], 3)
        np.testing.assert_array_equal(out['complete_class'][3:5], 4)
        np.testing.assert_array_equal(out['complete_instance'][3:5], 0)
        self.assertEqual(out['complete_class'][5], 3)
        np.testing.assert_array_equal(out['complete_class'][group['rows']], 3)
        self.assertEqual(report['nonHookTerminalPolish']['removedPointCount'], 2)
        self.assertEqual(operations[0]['reason'], 'non_hook_terminal_fixture_contact_outside_measured_cylinder')

    def test_short_bridge_keeps_cylinder_surface_through_clamp_gap(self):
        positions = np.array([[.19, .004, 0.], [.22, .004, 0.], [.25, .004, 0.]])
        context = SimpleNamespace(positions=positions)
        out = dict(complete_class=np.full(3, 3, np.uint8),
                   complete_instance=np.full(3, 4, np.uint32),
                   complete_segment=np.ones(3, np.uint32),
                   complete_confidence=np.ones(3, np.float32))
        group = dict(rows=np.array([2]), designUnitId='u1', designBarId='b1', radiusM=.004,
                     region={}, record={'id': 2, 'status': 'merged', 'finalInstanceId': 4})
        units = [{'designUnitId': 'u1', 'direction': [1., 0., 0.]}]
        segments = [
            {'id': 1, 'instanceId': 4, 'startM': [0., 0., 0.], 'endM': [.20, 0., 0.], 'radiusM': .004},
            {'id': 2, 'instanceId': 4, 'startM': [.24, 0., 0.], 'endM': [1., 0., 0.], 'radiusM': .004},
        ]
        report = {}

        polish_non_hook_terminals(
            context, out, [group], units, segments, cKDTree(positions.copy()), report)

        np.testing.assert_array_equal(out['complete_class'], 3)
        self.assertEqual(report['nonHookTerminalPolish']['removedPointCount'], 0)


if __name__ == '__main__':
    unittest.main()
