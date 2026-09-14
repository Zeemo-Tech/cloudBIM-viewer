"""User contract: three steel layers, independent branches, no design input clipping."""
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

import numpy as np

from algorithms import pointcloud_segmentation as pipeline
from algorithms.shared_floating_noise import prepare_floating_scene, review_floating_noise
from test_shared_scene import measured_scene


class LayeringBranchContractTests(unittest.TestCase):
    def test_crossed_bar_heights_form_two_layers_and_a_web_layer(self):
        heights = [.04, .052, .20, .212]
        inventory = {'bars': [dict(designBarId=str(i), points=[[0, 0, z], [1, 0, z]],
                                  radiusM=.005, coverage='complete') for i, z in enumerate(heights)]}
        inventory['bars'].append(dict(designBarId='web', points=[[0, 0, .04], [.5, 0, .212]],
                                      radiusM=.005, coverage='complete'))
        points = np.array([[.5, 0, z] for z in heights] + [[.25, 0, .126]])
        context = SimpleNamespace(positions=points, normals=np.tile([0., 1., 0.], (5, 1)),
            normal_valid=np.ones(5, np.uint8), shared_table_mask=np.zeros(5, np.uint8),
            partition_zone=np.ones(5, np.uint8), scene_cache={})
        output = {name: np.zeros(5, np.uint8) for name in ('shared_layer', 'shared_floating_noise')}
        layering, _ = prepare_floating_scene(context, inventory, output=output)
        np.testing.assert_array_equal(context.shared_layer, [1, 1, 2, 2, 3])
        self.assertEqual({item['name'] for item in layering['layers']}, {'底层钢筋', '顶层钢筋', '腹杆层'})

    def test_step05_boundary_is_independent_of_observed_review(self):
        with patch('algorithms.multiview_floating_noise.multiview_noise_mask',
                   return_value=(np.array([False, True, False]), {})):
            removed, _ = review_floating_noise(np.zeros((3, 3)), hard_mask=[1, 1, 1],
                review_mask=[1, 1, 0], steel_scores=[1., .35, 0.], observed_support_mask=[1, 0, 0])
        np.testing.assert_array_equal(removed, [True, True, True])

    def test_ab_overlap_and_receive_the_same_complete_source(self):
        points = measured_scene(0).positions
        inventory = {'bars': [dict(points=[[-.6, 0, .08], [.6, 0, .08]], radiusM=.005, coverage='complete')]}
        barrier = Barrier(2, timeout=10)
        seen = {}
        def watch(name, original):
            def call(context, *args, **kwargs):
                xyz = context if isinstance(context, np.ndarray) else context.positions
                seen[name] = xyz.copy()
                barrier.wait()
                return original(context, *args, **kwargs)
            return call
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(pipeline, 'classify_geometry', side_effect=watch('A', pipeline.classify_geometry)), \
             patch.object(pipeline, 'classify_projection', side_effect=watch('B', pipeline.classify_projection)):
            run = pipeline.segment_points(points, Path(tmp), workers=2, through_step=3, design_inventory=inventory)
        for xyz in seen.values():
            np.testing.assert_array_equal(xyz, points)
        self.assertEqual(run.execution['mode'], 'parallel')
        table = run.context.shared_table_mask.astype(bool)
        self.assertTrue(np.all(run.context.geometry_class[table] == 1))
        self.assertTrue(np.all(run.context.projection_class[table] == 1))


if __name__ == '__main__':
    unittest.main()
