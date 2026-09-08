import unittest

import numpy as np

from algorithms.internal_rebar import InternalRebarParameters, _recover_residual_models


def cylinder(start, end, radius=.0025, along=121, around=12):
    start = np.asarray(start, np.float64)
    axis = np.asarray(end, np.float64) - start
    length = np.linalg.norm(axis)
    axis /= length
    auxiliary = np.eye(3)[np.argmin(np.abs(axis))]
    first = np.cross(axis, auxiliary)
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    distance = np.linspace(0, length, along)
    angle = np.linspace(0, 2 * np.pi, around, endpoint=False)
    radial = np.cos(angle)[None, :, None] * first + np.sin(angle)[None, :, None] * second
    points = start + distance[:, None, None] * axis + radius * radial
    normals = np.broadcast_to(radial, points.shape)
    return points.reshape(-1, 3), normals.reshape(-1, 3)


class InternalRebarRecoveryTests(unittest.TestCase):
    def test_recovers_short_rod_without_direction_seed(self):
        points, normals = cylinder((-.025, 0, .033), (.025, 0, .033), along=35)
        recovered, diagnostics, _ = _recover_residual_models(
            points,
            normals,
            [],
            [{'height': .02, 'low': .014, 'high': .026},
             {'height': .08, 'low': .074, 'high': .086}],
            InternalRebarParameters(),
            1,
        )

        self.assertEqual(diagnostics['newInstances'], 1)
        self.assertEqual({model['group'] for model in recovered}, {0})
        self.assertTrue(all(model['type'] == 1 for model in recovered))

    def test_close_parallel_weak_seed_rods_remain_separate_instances(self):
        samples = [
            cylinder((-.12, y, .02), (.12, y, .02))
            for y in (-.004, .004)
        ]
        points = np.vstack([sample[0] for sample in samples])
        normals = np.vstack([sample[1] for sample in samples])
        recovered, diagnostics, _ = _recover_residual_models(
            points,
            normals,
            [],
            [{'height': .02, 'low': .01, 'high': .03}],
            InternalRebarParameters(),
            1,
        )

        self.assertEqual(diagnostics['newInstances'], 2)
        self.assertEqual(len({model['group'] for model in recovered}), 2)
        self.assertTrue(all(abs(model['radius'] - .0025) < .001 for model in recovered))

    def test_collinear_continuation_reuses_existing_instance(self):
        points, normals = cylinder((0, 0, .02), (.12, 0, .02))
        existing = [{
            'type': 1,
            'center': np.array([-.05, 0, .02]),
            'axis': np.array([1., 0, 0]),
            'low': -.05,
            'high': .05,
            'radius': .0025,
            'fitMedianErrorM': 0.,
            'group': 7,
        }]
        recovered, diagnostics, _ = _recover_residual_models(
            points,
            normals,
            existing,
            [{'height': .02, 'low': .01, 'high': .03}],
            InternalRebarParameters(),
            1,
        )

        self.assertGreater(len(recovered), 0)
        self.assertEqual(diagnostics['newInstances'], 0)
        self.assertEqual({model['group'] for model in recovered}, {7})


if __name__ == '__main__':
    unittest.main()
