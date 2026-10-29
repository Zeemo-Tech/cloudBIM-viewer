"""A revised physical body must not accumulate support from obsolete axes."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
from threadpoolctl import threadpool_limits

from algorithms.bim_bent_bars import recover_bent_bars
from algorithms.rebar_control_net import fit_control_net
from test_bim_bent_bars import data, inventory


class BodyOwnershipTests(unittest.TestCase):
    def test_axis_growth_recovers_source_when_local_circle_candidates_are_missing(self):
        from algorithms.bim_bent_bars import _section_task
        points, normals = data(bend=.003)
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='missing', pointCount=0)]
        def missing_sections(task):
            # Reproduce a local detector outage; the original source points and
            # normals remain available for independent axis-guided validation.
            if .30 < task[1] < .55:
                return task[0], task[1], []
            return _section_task(task)
        with threadpool_limits(limits=1), \
             patch('algorithms.bim_bent_bars._section_task', side_effect=missing_sections):
            recover_bent_bars(points, normals, inventory([0]), rows, status, owner,
                              {'objects': []}, {'objects': []})
        inside = (points[:, 0] > .32) & (points[:, 0] < .53)
        self.assertGreater(np.mean(owner[inside] == 1), .95,
                           'A missing local circle erased source supported by the continuous axis')
        self.assertGreater(np.mean(owner == 1), .95)

    def test_axis_growth_stops_at_a_tangent_planar_strip(self):
        first, first_normals = data(bend=0)
        keep = first[:, 0] < .75
        first, first_normals = first[keep], first_normals[keep]
        rng = np.random.default_rng(19)
        plane = np.c_[rng.uniform(.76, 1.2, 8000), np.full(8000, .004),
                      rng.uniform(-.002, .002, 8000)]
        points = np.vstack((first, plane))
        normals = np.vstack((first_normals, np.tile([0, 1, 0], (len(plane), 1))))
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='missing', pointCount=0)]
        with threadpool_limits(limits=1):
            recover_bent_bars(points, normals, inventory([0]), rows, status, owner,
                              {'objects': []}, {'objects': []})
        self.assertGreater(np.mean(owner[:len(first)] == 1), .95)
        # The 16 mm detection windows can straddle the tube/plane boundary;
        # pure-plane stations beyond that mixed window must stop axial growth.
        self.assertFalse(owner[len(first):][plane[:, 0] > .78].any(),
                         'Distance alone grew a tube onto a flat surface')
        self.assertLess(np.mean(owner[len(first):] == 1), .03)

    def test_axis_growth_preserves_parallel_neighbour_identity_bounds(self):
        points, normals = data(bend=0)
        points[:, 1] += .03*points[:, 0]/1.2
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        rows = [dict(id=i+1, designUnitId=str(i), designBarId=str(i), status='missing', pointCount=0)
                for i in range(2)]
        with threadpool_limits(limits=1):
            recover_bent_bars(points, normals, inventory([0, .045]), rows, status, owner,
                              {'objects': []}, {'objects': []})
        self.assertGreater(np.mean(owner[points[:, 0] < .75] == 1), .9)
        self.assertFalse(np.any(owner[points[:, 0] > 1.05] == 1),
                         'Growth bypassed the same neighbour identity prior used in candidate discovery')

    def test_provisional_bodies_cannot_publish_dependent_curves_or_caps(self):
        points, normals = data(bend=.003)
        with threadpool_limits(limits=1), \
             patch('algorithms.rebar_control_curves.fit_curved_pieces') as curves, \
             patch('algorithms.rebar_control_endpoints.complete_end_faces') as caps:
            report, arrays = fit_control_net(points, np.zeros(len(points), bool), inventory([0]),
                normals=normals, complete_members=False)
        self.assertGreater(np.count_nonzero(arrays['control_instance']), 100)
        curves.assert_not_called()
        caps.assert_not_called()
        self.assertEqual(report['curvedPieces'], [])
        self.assertEqual(report['counts']['endFaceMatchedPoints'], 0)

    def test_released_rows_are_available_to_prefetched_next_body(self):
        first, first_normals = data(bend=.003)
        second, second_normals = data(offset=.06, bend=.002, seed=8)
        points = np.vstack((first, second))
        normals = np.vstack((first_normals, second_normals))
        results = []
        for workers in (1, 2):
            owner = np.ones(len(points), np.uint32)
            status = np.ones(len(points), np.uint8)
            rows = [dict(id=i+1, designUnitId=str(i), designBarId=str(i),
                         status='fitted' if i == 0 else 'missing', pointCount=len(points) if i == 0 else 0)
                    for i in range(2)]
            with threadpool_limits(limits=1):
                recover_bent_bars(points, normals, inventory([0, .06]), rows,
                                  status, owner, {'objects': []}, {'objects': []}, workers=workers)
            self.assertGreater(np.mean(owner[:len(first)] == 1), .95)
            self.assertGreater(np.mean(owner[len(first):] == 2), .95,
                               'Prefetch lost rows released by the preceding body revision')
            results.append(owner)
        np.testing.assert_array_equal(*results)

    def test_revised_axis_replaces_parallel_old_support(self):
        points, normals = data(bend=.003)
        stale, stale_normals = data(offset=.035, bend=0)
        stale, stale_normals = stale[::8], stale_normals[::8]
        # The obsolete fit claimed a surface whose normals do not support a
        # second tube. Valid competing modes are tested separately below.
        stale_normals[:] = [1, 0, 0]
        true_count = len(points)
        points = np.vstack((points, stale, [[.5, .2, 0]]))
        normals = np.vstack((normals, stale_normals, [[0, 1, 0]]))
        before = points.copy()
        owner = np.zeros(len(points), np.uint32)
        owner[true_count:-1] = 1
        owner[-1] = 2
        status = np.where(owner > 0, 1, 2).astype(np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='fitted',
                     pointCount=len(stale), centerlineM=[[0, .035, 0], [1.2, .035, 0]])]
        with threadpool_limits(limits=1):
            report = recover_bent_bars(points, normals, inventory([0]), rows,
                                      status, owner, {'objects': []}, {'objects': []})
        self.assertEqual(report['objects'][0]['status'], 'observed-trace')
        self.assertGreater(np.mean(owner[:true_count] == 1), .95)
        self.assertFalse(np.any(owner[true_count:-1]), 'Obsolete parallel tube retained by final axis')
        self.assertTrue(np.all(status[true_count:-1] == 2), 'Unresolved support must become pending, not noise')
        self.assertEqual(owner[-1], 2, 'Other physical instance must remain unchanged')
        self.assertEqual(rows[0]['pointCount'], int(np.count_nonzero(owner == 1)))
        np.testing.assert_array_equal(owner > 0, status == 1)
        np.testing.assert_array_equal(points, before)

    def test_parallel_tracks_use_design_identity_without_merging_or_deleting_both(self):
        first, first_normals = data(bend=0)
        second, second_normals = data(offset=.035, bend=0, seed=8)
        second, second_normals = np.repeat(second, 2, axis=0), np.repeat(second_normals, 2, axis=0)
        points = np.vstack((first, second))
        normals = np.vstack((first_normals, second_normals))
        owner = np.zeros(len(points), np.uint32)
        owner[len(first):] = 1
        status = np.where(owner > 0, 1, 2).astype(np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='fitted',
                     pointCount=len(second), centerlineM=[[0, .035, 0], [1.2, .035, 0]])]
        with threadpool_limits(limits=1):
            report = recover_bent_bars(points, normals, inventory([0]), rows,
                                      status, owner, {'objects': []}, {'objects': []})
        self.assertGreater(np.mean(owner[:len(first)] == 1), .95)
        self.assertFalse(owner[len(first):].any(), 'Disjoint parallel track acquired the same instance')
        self.assertTrue(np.all(status[len(first):] == 2))
        self.assertEqual(rows[0]['status'], 'fitted')

    def test_local_competition_preserves_unique_body_in_a_rotated_frame(self):
        from scipy.spatial.transform import Rotation
        first, first_normals = data(bend=0)
        second, second_normals = data(offset=.035, bend=0, seed=8)
        keep = second[:, 0] < .35
        second, second_normals = second[keep], second_normals[keep]
        points = np.vstack((first, second))
        normals = np.vstack((first_normals, second_normals))
        axial = points[:, 0].copy()
        rotation = Rotation.from_rotvec([.4, -.6, .2]).as_matrix()
        translation = np.array([4., -2., 1.])
        points = points@rotation.T+translation
        normals = normals@rotation.T
        inv = inventory([0])
        for key in ('startM', 'endM'):
            inv['units'][0][key] = (rotation@inv['units'][0][key]+translation).tolist()
        owner = np.zeros(len(points), np.uint32)
        owner[:len(first)] = 1
        status = np.where(owner > 0, 1, 2).astype(np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='fitted', pointCount=len(first))]
        before = points.copy()
        with threadpool_limits(limits=1):
            recover_bent_bars(points, normals, inv, rows, status, owner, {'objects': []}, {'objects': []})
        self.assertGreater(np.mean(owner[:len(first)][axial[:len(first)] < .30] == 1), .95,
                           'Local competition erased the continuous main tube')
        self.assertGreater(np.mean(owner[:len(first)] == 1), .95)
        self.assertFalse(owner[len(first):].any(), 'Axial growth jumped onto the parallel branch')
        self.assertEqual(rows[0]['status'], 'fitted')
        np.testing.assert_array_equal(points, before)

    def test_bowed_body_grows_through_denser_clutter_nearer_the_design_axis(self):
        first, first_normals = data(bend=.065)
        second, second_normals = data(offset=-.020, bend=0, seed=8)
        keep = (second[:, 0] > .30) & (second[:, 0] < .85)
        second = np.repeat(second[keep], 3, axis=0)
        second_normals = np.repeat(second_normals[keep], 3, axis=0)
        points = np.vstack((first, second))
        normals = np.vstack((first_normals, second_normals))
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='missing', pointCount=0)]
        with threadpool_limits(limits=1):
            recover_bent_bars(points, normals, inventory([0]), rows, status, owner,
                              {'objects': []}, {'objects': []})
        self.assertGreater(np.mean(owner[:len(first)] == 1), .95,
                           'The continuous bowed body lost to a denser, design-nearer local track')
        self.assertFalse(owner[len(first):].any())
        self.assertTrue(np.all(status[len(first):] == 2))

    def test_validated_subset_updates_even_without_new_points(self):
        points, normals = data(bend=.003)
        owner = np.ones(len(points), np.uint32)
        status = np.ones(len(points), np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='fitted',
                     pointCount=len(points), centerlineM=[[0, .02, 0], [1.2, .02, 0]])]
        with threadpool_limits(limits=1):
            report = recover_bent_bars(points, normals, inventory([0]), rows,
                                      status, owner, {'objects': []}, {'objects': []})
        self.assertIn('flexibleRegistration', rows[0], 'No new rows is not a reason to keep obsolete geometry')
        self.assertEqual(report['objects'][0]['status'], 'observed-trace')
        self.assertLess(abs(np.median(np.asarray(rows[0]['centerlineM'])[:, 1])), .004)
        self.assertEqual(rows[0]['pointCount'], int(np.count_nonzero(owner == 1)))

    def test_failed_revision_keeps_previous_model_and_support(self):
        points, normals = data(bend=.003)
        owner = np.ones(len(points), np.uint32)
        status = np.ones(len(points), np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='fitted',
                     pointCount=len(points), centerlineM=[[0, 0, 0], [1.2, 0, 0]])]
        original = copy.deepcopy(rows)
        with threadpool_limits(limits=1), patch('algorithms.bim_bent_bars._fit_ifc_axis',
                return_value=(None, 'unsupported', np.empty(0, int))):
            recover_bent_bars(points, normals, inventory([0]), rows, status, owner,
                              {'objects': []}, {'objects': []})
        self.assertEqual(rows, original)
        self.assertTrue(np.all(owner == 1))
        self.assertTrue(np.all(status == 1))


if __name__ == '__main__':
    unittest.main()
