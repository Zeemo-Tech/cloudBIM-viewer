"""Registration geometry, observability and preservation of local defects."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from algorithms.rebar_registration import (collect_sections, fine_rebar_registration,
                                            register_sections, solve_rigid, _move)


def fixture():
    delta = np.eye(4)
    delta[:3, :3] = Rotation.from_euler('xyz', [.6, -.3, .8], degrees=True).as_matrix()
    delta[:3, 3] = [.014, -.009, .006]
    bars = []
    for family in range(2):
        direction = np.eye(3)[family]
        for i in range(12):
            start = np.array([.13, .27, .08 + .04*(i % 2)])
            start[1-family] += i * .13
            design = start + np.linspace(.05, 1.35, 30)[:, None] * direction
            bars.append(dict(id=f'{family}-{i:02}', unitId=f'{family}-{i:02}', start=start,
                             tangent=direction, points=_move(design, np.linalg.inv(delta)),
                             weight=1., audit={}))
    return bars, delta


class RebarRegistrationTests(unittest.TestCase):
    def test_recovers_asymmetric_rigid_transform_and_composes_on_left(self):
        bars, expected = fixture()
        correction, evidence = register_sections(bars)
        np.testing.assert_allclose(correction, expected, atol=1e-9)
        self.assertGreaterEqual(len(evidence['heldOutBarIds']), 4)
        self.assertLess(evidence['heldOutAfter']['equalBarRmseM'], 1e-9)
        initial = np.eye(4)
        initial[:3, :3] = Rotation.from_euler('zyx', [21, -12, 87], degrees=True).as_matrix()
        initial[:3, 3] = [23, -17, .9]
        probes = np.array([[.7, 2.1, -.3], [-2.4, .1, 4.2], [0, 0, 0]])
        np.testing.assert_allclose(_move(probes, correction @ initial),
                                   _move(_move(probes, initial), expected), atol=1e-10)
        self.assertAlmostEqual(np.linalg.det(correction[:3, :3]), 1.)

    def test_parallel_bars_do_not_invent_axial_translation(self):
        bars, _ = fixture()
        with self.assertRaisesRegex(ValueError, '六自由度'):
            solve_rigid(bars[:12])

    def test_real_local_offset_is_not_fitted_away(self):
        bars, expected = fixture()
        # One wrongly placed bar is a local defect, not a different global frame.
        bars[2]['points'] += expected[:3, :3].T @ np.array([0., .050, 0.])
        actual, evidence = solve_rigid(bars)
        nominal = np.array([[.7, .8, .1]])
        self.assertLess(np.linalg.norm(_move(nominal, actual)-_move(nominal, expected)), .002)
        d = _move(bars[2]['points'], actual)-bars[2]['start']
        transverse = d-(d@bars[2]['tangent'])[:, None]*bars[2]['tangent']
        self.assertGreater(np.mean(np.linalg.norm(transverse, axis=1)), .045)
        self.assertLess(evidence['robustWeights'][2], .2)

    def test_stale_and_nonrigid_inputs_are_rejected_before_scan_read(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            initial = np.eye(4)
            (p/'control-net.json').write_text(json.dumps({'report': {'modelInfo': {'scanToBim': initial.T.ravel().tolist()}}}))
            initial[0, 3] = .01
            with self.assertRaisesRegex(ValueError, '过期'):
                fine_rebar_registration(directory, initial.T.ravel().tolist())
            initial[0, 0] = -1
            with self.assertRaisesRegex(ValueError, 'rigid'):
                fine_rebar_registration(directory, initial.T.ravel().tolist())

    def test_raw_circle_evidence_weights_thickness_and_straightness(self):
        points, owners, units, instances = [], [], [], []
        for i, (diameter, bow) in enumerate([(.006, 0.), (.012, 0.), (.012, .003)]):
            start = np.array([0, i*.15, 0]); end = start + [1., 0, 0]
            s = np.arange(0., 1.001, .001)
            a = np.arange(24)*2*np.pi/24
            centers = start + s[:, None]*[1, 0, 0]
            centers[:, 2] += bow*np.sin(np.pi*s)
            cloud = centers[:, None, :] + diameter/2*np.array([np.zeros(24), np.cos(a), np.sin(a)]).T
            points.append(cloud.reshape(-1, 3)); owners.extend([i+1]*cloud.reshape(-1, 3).shape[0])
            units.append(dict(designBarId=str(i), designUnitId=str(i), kind='straight', lengthM=1., diameterM=diameter, startM=start.tolist(), endM=end.tolist()))
            instances.append(dict(id=i+1, designUnitId=str(i), reviewStatus='matched'))
        bars, audit = collect_sections(np.concatenate(points), np.array(owners), {'inventory': {'units': units}, 'instances': instances}, np.eye(4))
        self.assertEqual(len(bars), 3)
        self.assertGreater(audit[1]['baseWeight'], 3.8*audit[0]['baseWeight'])
        self.assertGreater(audit[1]['baseWeight'], audit[2]['baseWeight'])
        self.assertGreater(audit[2]['straightnessRmseM'], .0008)


if __name__ == '__main__':
    unittest.main()
