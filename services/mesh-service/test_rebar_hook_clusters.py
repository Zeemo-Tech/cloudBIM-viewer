import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_hook_clusters import polish_non_hook_terminals, verify_hook_clusters


def _case(*terminal_points):
    x = np.linspace(-.05, -.18, 16)
    shell = np.vstack((
        np.column_stack((x, np.full(len(x), .004), np.zeros(len(x)))),
        np.column_stack((x, np.zeros(len(x)), np.full(len(x), .004))),
    ))
    bend = np.array([[.005, 0., .004], [.01, 0., .02], [.005, 0., .04]])
    positions = np.vstack((bend, shell, np.asarray(terminal_points, float)))
    count = len(positions)
    out = dict(complete_class=np.full(count, 3, np.uint8),
               complete_instance=np.full(count, 7, np.uint32),
               complete_segment=np.ones(count, np.uint32),
               complete_confidence=np.ones(count, np.float32))
    group = dict(rows=np.arange(count), bendRows=np.arange(len(bend)),
                 bodyEndM=[0., 0., 0.], towardBody=[-1., 0., 0.],
                 stemCollarLengthM=.25, designUnitId='u1', designBarId='b1', radiusM=.004,
                 region={}, record={'id': 11, 'status': 'merged', 'finalInstanceId': 7})
    units = [{'designUnitId': 'u1', 'direction': [1., 0., 0.]}]
    segments = [{'id': 1, 'instanceId': 7, 'startM': [-.30, 0., 0.],
                 'endM': [-.02, 0., 0.], 'radiusM': .004}]
    return SimpleNamespace(positions=positions), out, group, units, segments


class HookTerminalPolishTests(unittest.TestCase):
    def test_only_fixture_glue_at_hook_atoms_straight_terminal_is_removed(self):
        context, out, group, units, segments = _case(
            [-.24, .018, 0.], [-.235, -.017, .003],
        )
        fixture_tree = cKDTree(context.positions[-2:]+np.array([0., -.002, 0.]))
        report = {'filteredPointCount': 0, 'splitClusterCount': 0}

        operations = polish_non_hook_terminals(
            context, out, [group], units, segments, fixture_tree, report)

        np.testing.assert_array_equal(out['complete_class'][:-2], 3)
        np.testing.assert_array_equal(out['complete_class'][-2:], 4)
        np.testing.assert_array_equal(out['complete_instance'][-2:], 0)
        np.testing.assert_array_equal(out['complete_class'][group['bendRows']], 3)
        self.assertEqual(report['nonHookTerminalPolish']['removedPointCount'], 2)
        self.assertEqual(report['filteredPointCount'], 2)
        self.assertEqual(report['splitClusterCount'], 1)
        self.assertEqual(
            operations[0]['reason'],
            'hook_straight_collar_fixture_contact_outside_local_cylinder',
        )
        verify_hook_clusters(out, [group])

    def test_fixture_contact_on_cylinder_surface_is_retained(self):
        context, out, group, units, segments = _case([-.24, .004, 0.])
        # A clamp-biased local fit must not override the 8 mm design diameter.
        segments[0]['radiusM'] = .006
        report = {'filteredPointCount': 0, 'splitClusterCount': 0}

        polish_non_hook_terminals(
            context, out, [group], units, segments,
            cKDTree(context.positions[-1:].copy()), report,
        )

        np.testing.assert_array_equal(out['complete_class'], 3)
        self.assertEqual(report['nonHookTerminalPolish']['removedPointCount'], 0)
        self.assertEqual(report['nonHookTerminalPolish']['surfaceToleranceM'], .001)
        verify_hook_clusters(out, [group])

    def test_unrecorded_bend_deletion_still_fails_verification(self):
        _, out, group, _, _ = _case([-.24, .004, 0.])
        out['complete_class'][group['bendRows'][0]] = 4

        with self.assertRaisesRegex(RuntimeError, '弯曲核心'):
            verify_hook_clusters(out, [group])


if __name__ == '__main__':
    unittest.main()
