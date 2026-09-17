"""Successful sampled fits must still explain independent raw tube surfaces."""
import copy
import json
from pathlib import Path
import unittest
import numpy as np
from threadpoolctl import threadpool_limits
from algorithms.rebar_control_net import fit_control_net, _polyline_distances

RUN = Path(__file__).resolve().parents[2] / 'backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260914T155930-098fe029'

@unittest.skipUnless((RUN/'control-net.json').exists(), 'local full-source regression fixture unavailable')
class RawSurfaceRegressionTests(unittest.TestCase):
    def test_long_local_bow_recovers_surface_without_widening_classification(self):
        baseline = json.loads((RUN/'control-net.json').read_text())
        inventory = copy.deepcopy(baseline['inventory'])
        inventory['units'] = [inventory['units'][10]]
        parent = inventory['units'][0]['designBarId']
        inventory['bars'] = [b for b in inventory['bars'] if b['designBarId'] == parent]
        points = np.load(RUN/'positions.npy', mmap_mode='r')
        normals = np.load(RUN/'normals.npy', mmap_mode='r')
        table = np.load(RUN/'shared_table_mask.npy')
        original = np.asarray(baseline['instances'][10]['centerlineM'])
        local = np.flatnonzero(np.all(np.abs(points-[4.575, -1.79, .035]) < [.275, .02, .015], axis=1) & ~table)
        radial = _polyline_distances(points[local], original)
        ids = local[(radial < .010) & (np.linalg.norm(normals[local], axis=1) > .5)
                    & (np.abs(normals[local, 0]) < .5)]
        self.assertGreater(len(ids), 5000)
        with threadpool_limits(limits=1):
            report, attrs = fit_control_net(points, table, inventory, normals=normals)
        row = report['instances'][0]
        residual = np.abs(_polyline_distances(points[ids], np.asarray(row['centerlineM']))-.004)
        self.assertLess(np.median(residual), .0008, 'local bow is flattened')
        self.assertGreater(np.mean(attrs['control_instance'][ids] == 1), .85)
        self.assertAlmostEqual(np.linalg.norm(np.diff(row['centerlineM'], axis=0), axis=1).sum(),
                               inventory['units'][0]['lengthM'], places=8)
        np.testing.assert_array_equal(attrs['control_status'][table], 0)

    def test_successful_but_biased_transverse_bars_recover_full_endpoint_surface(self):
        baseline = json.loads((RUN/'control-net.json').read_text())
        inventory = copy.deepcopy(baseline['inventory'])
        chosen = [28, 29, 30]
        inventory['units'] = [inventory['units'][i-1] for i in chosen]
        parents = {u['designBarId'] for u in inventory['units']}
        inventory['bars'] = [b for b in inventory['bars'] if b['designBarId'] in parents]
        points = np.load(RUN/'positions.npy', mmap_mode='r')
        normals = np.load(RUN/'normals.npy', mmap_mode='r')
        table = np.load(RUN/'shared_table_mask.npy')
        with threadpool_limits(limits=1):
            report, attrs = fit_control_net(points, table, inventory, normals=normals)
            parallel, parallel_attrs = fit_control_net(points, table, inventory, normals=normals, workers=2, curve_workers=2)
        for key in attrs:
            np.testing.assert_array_equal(attrs[key], parallel_attrs[key])
        for a, b in zip(report['instances'], parallel['instances']):
            np.testing.assert_array_equal(a['centerlineM'], b['centerlineM'])
        for index, number in enumerate(chosen):
            row = report['instances'][index]
            self.assertEqual(row['status'], 'fitted')
            original = np.array(baseline['instances'][number-1]['centerlineM'])
            a = original[0]; t = original[1]-a; t /= np.linalg.norm(t)
            # Broad, fixed endpoint corridor, independent of the new fit.
            local = np.flatnonzero(np.all(np.abs(points-a) < .12, axis=1) & ~table)
            delta = points[local]-a; along = delta@t
            radial = np.linalg.norm(delta-along[:, None]*t, axis=1)
            ids = local[(along > .015) & (along < .095) & (radial < .014)
                        & (np.abs(normals[local]@t) < .5)]
            residual = np.abs(_polyline_distances(points[ids], np.array(row['centerlineM']))-.004)
            self.assertLess(np.median(residual), .0007, f'unit {number} remains biased')
            self.assertGreater(np.mean(attrs['control_instance'][ids] == index+1), .95)
        np.testing.assert_array_equal(attrs['control_status'][table], 0)

if __name__ == '__main__': unittest.main()
