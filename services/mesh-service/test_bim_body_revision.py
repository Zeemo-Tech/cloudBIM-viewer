"""Existing supported bodies must not regress when an extra fit sees less scan."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation
from threadpoolctl import threadpool_limits

import algorithms.bim_bent_bars as bent
from test_bim_bent_bars import data, inventory


def fitted_row(bend=.003):
    x = np.linspace(0, 1.2, 101)
    return dict(id=1, designUnitId='0', designBarId='0', status='fitted',
                pointCount=16000, diameterM=.008, designLengthM=1.2,
                centerlineM=np.c_[x, -bend*np.sin(np.pi*x/1.2)**2, x*0].tolist())


class BodyRevisionTests(unittest.TestCase):
    def test_nearby_measured_fixture_requires_stronger_normal_evidence(self):
        points, normals = data(bend=.003)
        normals[::25] = [1., 0., 0.]
        fixture = np.array([[.6, .025, 0.]])
        inv = inventory([0])
        unit = bent._unit_rows(inv)[0]
        owner = np.ones(len(points), np.uint32)
        exclude = np.zeros(len(points), bool)
        clean, _, _ = bent._existing_body_evidence(points, normals, owner, exclude,
            fitted_row(), unit, [unit], {'planes': []})
        clutter, _, _ = bent._existing_body_evidence(points, normals, owner, exclude,
            fitted_row(), unit, [unit], {'planes': []}, bent.cKDTree(fixture))
        self.assertTrue(clean['skipRefit'])
        self.assertFalse(clutter['skipRefit'])
        self.assertTrue(clutter['competingSurfaceNearby'])
        self.assertFalse(clutter['protectExistingRegions'])

    def test_wrong_parallel_prior_does_not_lock_in_old_source_regions(self):
        actual, actual_normals = data(bend=0.)
        stale, stale_normals = data(offset=.035, bend=0., seed=8)
        points, normals = np.vstack((actual, stale)), np.vstack((actual_normals, stale_normals))
        owner = np.r_[np.zeros(len(actual), np.uint32), np.ones(len(stale), np.uint32)]
        status = np.where(owner > 0, 1, 2).astype(np.uint8)
        row = fitted_row(0.)
        curve = np.asarray(row['centerlineM']); curve[:, 1] += .035
        row['centerlineM'] = curve.tolist()
        solve = bent._fit_ifc_axis

        def partial_correct_model(*args, **kwargs):
            model, reason, ids = solve(*args, **kwargs)
            if model is not None:
                model.pop('_fragmentRefinement', None)
                model['intervals'] = [[0., .72]]
                model['parameters']['observedSupportIntervalsM'] = [[0., .72]]
                ids = ids[points[ids, 0] <= .72]
                model['pointCount'] = len(ids)
            return model, reason, ids

        with threadpool_limits(limits=1), patch.object(bent, '_fit_ifc_axis', side_effect=partial_correct_model):
            report = bent.recover_bent_bars(points, normals, inventory([0]), [row], status, owner,
                                           {'objects': []}, {'objects': []})
        self.assertFalse(report['objects'][0]['existingBodyEvidence']['protectExistingRegions'])
        self.assertFalse(owner[len(actual):].any())
        self.assertGreater(np.count_nonzero(owner[:len(actual)]), 8000)

    def test_supported_body_avoids_redundant_section_and_axis_solves(self):
        points, normals = data(bend=.003)
        owner = np.ones(len(points), np.uint32)
        status = np.ones(len(points), np.uint8)
        rows = [fitted_row()]
        before = copy.deepcopy(rows)
        with threadpool_limits(limits=1), patch.object(bent, '_trace', wraps=bent._trace) as trace:
            report = bent.recover_bent_bars(points, normals, inventory([0]), rows,
                                           status, owner, {'objects': []}, {'objects': []})
        trace.assert_not_called()
        self.assertEqual(rows, before)
        self.assertTrue(np.all(owner == 1))
        self.assertEqual(report['objects'][0]['status'], 'existing-body-supported')

    def test_partial_new_fit_cannot_revoke_a_supported_old_tail(self):
        points, normals = data(bend=.02)
        observed = ((points[:, 0] > .2) & (points[:, 0] < .45)) | (points[:, 0] > .92)
        owner = observed.astype(np.uint32)
        status = np.where(observed, 1, 2).astype(np.uint8)
        rows = [fitted_row(.02)]
        rows[0]['pointCount'] = int(observed.sum())
        before_rows = copy.deepcopy(rows)
        before_owner = owner.copy()
        solve = bent._fit_ifc_axis

        def truncated_candidate(*args, **kwargs):
            model, reason, ids = solve(*args, **kwargs)
            if model is not None:
                # The real solver's validated prefix can be excellent while
                # the known supported tail lies outside its observed domain.
                model['intervals'] = [[0., .72]]
                model['parameters']['observedSupportIntervalsM'] = [[0., .72]]
                ids = ids[points[ids, 0] <= .72]
                model['pointCount'] = len(ids)
            return model, reason, ids

        with threadpool_limits(limits=1), patch.object(bent, '_fit_ifc_axis', side_effect=truncated_candidate):
            report = bent.recover_bent_bars(points, normals, inventory([0]), rows,
                                           status, owner, {'objects': []}, {'objects': []})
        self.assertTrue(np.all(owner[observed & (points[:, 0] > .92)] == 1),
                        'A locally valid prefix replaced a previously supported full-body model')
        np.testing.assert_array_equal(owner, before_owner)
        self.assertEqual(rows, before_rows)
        self.assertEqual(report['objects'][0]['reason'], 'existing-supported-region-regressed')

    def test_supported_fast_path_uses_the_design_frame_after_rigid_rotation(self):
        points, normals = data(bend=.003)
        rotation = Rotation.from_rotvec([.4, -.6, .2]).as_matrix()
        shift = np.array([4., -2., 1.])
        inv = inventory([0])
        for key in ('startM', 'endM'):
            inv['units'][0][key] = (rotation @ inv['units'][0][key] + shift).tolist()
        row = fitted_row()
        row['centerlineM'] = (np.asarray(row['centerlineM']) @ rotation.T + shift).tolist()
        before = copy.deepcopy(row)
        owner = np.ones(len(points), np.uint32)
        status = np.ones(len(points), np.uint8)
        with threadpool_limits(limits=1), patch.object(bent, '_trace', wraps=bent._trace) as trace:
            bent.recover_bent_bars(points @ rotation.T + shift, normals @ rotation.T,
                                  inv, [row], status, owner, {'objects': []}, {'objects': []})
        trace.assert_not_called()
        self.assertEqual(row, before)
        self.assertTrue(np.all(owner == 1))


if __name__ == '__main__':
    unittest.main()
