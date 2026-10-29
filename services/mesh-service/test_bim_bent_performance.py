"""Dense section votes must not repeat scalar comparisons for every point."""
import unittest
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree
from threadpoolctl import threadpool_limits

from algorithms.bim_bent_bars import (
    _sections, _trace, _unit_rows, recover_bent_bars, _TraceSections, _section_worker_init,
)
from test_bim_bent_bars import data, inventory


class SectionVoteTests(unittest.TestCase):
    def test_prefetch_rechecks_claimed_rows_in_queued_and_future_windows(self):
        points,normals=data()
        units=_unit_rows(inventory([0.]))
        eligible=np.ones(len(points),bool)
        # Remove an entire queued window, alternating training/validation rows,
        # and rows past the bounded prefetch horizon.
        current=~(((points[:,0]>.18)&(points[:,0]<.25)) |
                  ((np.arange(len(points))%3==0)&(points[:,0]<.6)) |
                  ((points[:,0]>.95)&(points[:,0]<1.02)))
        with threadpool_limits(limits=1):
            expected,expected_ids=_trace(points,normals,current,units[0],units,{'planes':[]})
            with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn'),
                                     initializer=_section_worker_init) as pool:
                plan=_TraceSections(points,normals,eligible,units[0],units,{'planes':[]},2)
                plan.prefetch(pool)
                self.assertLess(plan.next_station,len(plan.stations))
                actual,actual_ids=_trace(points,normals,current,units[0],units,{'planes':[]},
                                        prepared=plan,executor=pool,workers=2)
                self.assertEqual(pool.submit(abs,-7).result(),7)
        np.testing.assert_array_equal(actual.pop('_candidateIds'),expected.pop('_candidateIds'))
        self.assertEqual(actual,expected)
        np.testing.assert_array_equal(actual_ids,expected_ids)
        self.assertFalse(np.any(~current[actual_ids]))

    def test_supplied_pool_remains_usable_after_bent_recovery(self):
        points,normals=data()
        owner=np.zeros(len(points),np.uint32)
        status=np.full(len(points),2,np.uint8)
        rows=[dict(id=1,designUnitId='0',designBarId='0',pointCount=0,status='missing')]
        with threadpool_limits(limits=1), \
             ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn'),
                                 initializer=_section_worker_init) as pool:
            recover_bent_bars(points,normals,inventory([0.]),rows,status,owner,
                              {'objects':[]},{'objects':[]},workers=2,executor=pool)
            self.assertEqual(pool.submit(abs,-11).result(),11)
        self.assertGreater(np.mean(owner==1),.9)

    def test_trace_surface_checks_only_rows_in_each_station_window(self):
        points, normals = data()
        units = _unit_rows(inventory([0.]))
        norm = np.linalg.norm
        full_corridor_calls = 0

        def counted_norm(value, *args, **kwargs):
            nonlocal full_corridor_calls
            if np.shape(value) == (len(points), 2):
                full_corridor_calls += 1
            return norm(value, *args, **kwargs)

        with threadpool_limits(limits=1), \
             patch('algorithms.bim_bent_bars.np.linalg.norm', side_effect=counted_norm):
            trace, ids = _trace(points, normals, np.ones(len(points), bool),
                                units[0], units, {'planes': []})
        self.assertEqual(trace['status'], 'observed-trace')
        self.assertGreater(len(ids), .9*len(points))
        self.assertLessEqual(full_corridor_calls, 1,
                             'each station measured radial distance for the entire bar corridor')

    def test_parallel_stations_preserve_sequential_bar_ownership_and_geometry(self):
        a, an = data()
        b, bn = data(offset=.09, bend=.02, seed=7)
        points, normals = np.vstack((a, b)), np.vstack((an, bn))
        keep = ~((points[:, 0] > .48) & (points[:, 0] < .61))
        points, normals = points[keep], normals[keep]
        original = points.copy()
        inv = inventory([0., .09])
        excluded = points[:, 0] < .08
        results = []
        for workers in (1, 2):
            owner = np.zeros(len(points), np.uint32)
            owner[7000] = 1
            status = np.full(len(points), 2, np.uint8)
            status[7000] = 1
            rows = [dict(id=i+1, designUnitId=str(i), designBarId=str(i),
                         pointCount=int(i == 0), status='fitted' if i == 0 else 'missing')
                    for i in range(2)]
            with threadpool_limits(limits=1):
                report = recover_bent_bars(points, normals, inv, rows, status, owner,
                                          {'objects': []}, {'objects': []},
                                          excluded=excluded, workers=workers)
            self.assertEqual(owner[7000], 1)
            self.assertFalse(owner[excluded].any())
            self.assertGreater(np.count_nonzero(owner == 1), 5000)
            self.assertGreater(np.count_nonzero(owner == 2), 5000)
            results.append((report, rows, status, owner))
        self.assertEqual(results[0][:2], results[1][:2])
        np.testing.assert_array_equal(results[0][2], results[1][2])
        np.testing.assert_array_equal(results[0][3], results[1][3])
        np.testing.assert_array_equal(points, original)

    def test_rejected_dense_votes_keep_greedy_order_with_bounded_work(self):
        rng = np.random.default_rng(712)
        angle = rng.uniform(-np.pi, np.pi, 1800)
        normals = np.c_[np.cos(angle), np.sin(angle)]
        radius = .004
        points = radius*normals + rng.normal(0, .00012, normals.shape)
        points[900:] += [.024, .003]
        votes = np.concatenate([points-radius*normals, points+radius*normals])
        counts = cKDTree(votes).query_ball_point(votes, .0015, return_length=True)
        # Independent scalar oracle: even a rejected centre suppresses nearby
        # later votes, but must not consume the budget for fitting own sections.
        expected = []
        for at in np.argsort(counts)[::-1]:
            if counts[at] < 8:
                break
            if all(np.linalg.norm(votes[at]-c) >= 1.5*radius for c in expected):
                expected.append(votes[at])
        visited = []
        norm = np.linalg.norm
        scalar_calls = 0

        def counted_norm(value, *args, **kwargs):
            nonlocal scalar_calls
            if np.shape(value) == (2,):
                scalar_calls += 1
            return norm(value, *args, **kwargs)

        def reject(center):
            visited.append(center.copy())
            return False

        with patch('algorithms.bim_bent_bars.np.linalg.norm', side_effect=counted_norm):
            self.assertEqual(_sections(points, normals, radius, reject), [])
        np.testing.assert_array_equal(visited, expected)
        self.assertLess(scalar_calls, 100,
                        'dense votes repeated thousands of scalar centre comparisons')


if __name__ == '__main__':
    unittest.main()
