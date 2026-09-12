"""Verify design candidates do not overwrite branch evidence or source artifacts."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
import numpy as np
import laspy

from algorithms.shared_floating_noise import denoise_branch, review_floating_noise
from algorithms.pointcloud_fusion import fuse_classifications
from algorithms.pointcloud_normals import PointCloudContext
from algorithms.shared_scene import regions_from_partition


class SharedFloatingNoiseTests(unittest.TestCase):
    def test_step05_cloth_is_not_overridden_by_instances_or_observed_support(self):
        with patch('algorithms.multiview_floating_noise.multiview_noise_mask',
                   return_value=(np.zeros(4, bool), {})):
            removed, report = review_floating_noise(np.zeros((4, 3)),
                hard_mask=[1, 1, 1, 0], review_mask=[0, 0, 1, 0],
                observed_support_mask=[1, 0, 0, 1], observed_continuation_mask=[0, 1, 0, 0],
                steel_scores=[1., 1., .25, 1.])
        np.testing.assert_array_equal(removed, [True, True, True, False])
        self.assertEqual(report['additionalDesignRemovedPointCount'], 3)

    def test_step05_cloth_removes_unsupported_residual_without_multiview_vote(self):
        from algorithms.internal_rebar import segment_internal_rebar
        context = PointCloudContext.build(np.array([[0., 0., .1], [.1, 0., .1], [.2, 0., .1], [.3, 0., .1]]))
        context.refined_class = np.array([3, 3, 1, 2], np.uint8)
        context.refined_zone = np.array([3, 3, 0, 2], np.uint8)
        context.shared_floating_noise = np.ones(4, np.uint8)
        context.normals = np.tile([0., 1., 0.], (4, 1))
        context.fused_steel_score = np.array([1., .25, 0., 0.], np.float32)
        with patch('algorithms.multiview_floating_noise.multiview_noise_mask',
                   return_value=(np.zeros(2, bool), {})), \
             patch('algorithms.multiview_floating_noise.observed_cylinder_support', return_value=np.array([True, False])), \
             patch('algorithms.multiview_floating_noise.observed_cylinder_continuation', return_value=np.zeros(2, bool)):
            report = segment_internal_rebar(context)
        np.testing.assert_array_equal(context.internal_type, [5, 5, 0, 0])
        np.testing.assert_array_equal(context.refined_class, [3, 3, 1, 2])
        self.assertEqual(report['denoising']['additionalDesignRemovedPointCount'], 2)

    def test_step05_boundary_overrides_observed_support_and_high_score(self):
        points = np.array([[0., 0., 0.], [1., 0., 0.]])
        with patch('algorithms.multiview_floating_noise.multiview_noise_mask',
                   return_value=(np.zeros(2, bool), {'removedPointCount': 0})):
            removed, report = review_floating_noise(points, hard_mask=[1, 0],
                review_mask=[1, 1], steel_scores=[1., 1.], observed_support_mask=[1, 1])
        np.testing.assert_array_equal(removed, [True, False])
        self.assertEqual(report['highScoreRemovedPointCount'], 1)
        self.assertEqual(report['designProtectedPointCount'], 0)

    def test_design_candidates_preserve_branch_votes_but_observed_noise_stays_noise(self):
        count = 4
        context = PointCloudContext.build(np.array([[0., 0., 0.], [.1, 0., 0.], [.2, 0., 0.], [.3, 0., 0.]]))
        context.normals = np.tile([0., 1., 0.], (count, 1))
        context.shared_floating_noise = np.array([1, 0, 0, 0], np.uint8)
        context.partition_zone = np.array([1, 1, 3, 2], np.uint8)
        context.shared_table_mask = np.zeros(count, np.uint8)
        context.scene_cache = {'region_owned': np.array([1, 1, 0, 0], bool)}
        context.geometry_class = np.array([3, 3, 3, 2], np.uint8)
        context.projection_class = context.geometry_class.copy()
        context.classification_cache = {'source_steel_evidence': np.ones(count, bool)}
        context.projection_cache = {'source_steel_evidence': np.array([1, 0, 1, 0], bool), 'source_steel_candidate': np.ones(count, bool)}
        with patch('algorithms.multiview_floating_noise.multiview_noise_mask',
                   side_effect=AssertionError('A/B must not repeat multiview review')):
            for stage, labels, cache in [('02A', context.geometry_class, context.classification_cache),
                                         ('02B', context.projection_class, context.projection_cache)]:
                denoise_branch(context, labels, cache=cache, stage=stage)
        np.testing.assert_array_equal(context.geometry_class, [3, 3, 3, 2])
        self.assertTrue(context.classification_cache['source_steel_evidence'][0])
        # A historical observed-only rejection cannot be undone by region-only B.
        context.geometry_class[1] = 4
        context.classification_cache['source_steel_evidence'][1] = False
        np.testing.assert_array_equal(context.projection_class, [3, 3, 3, 2])
        cache = context.classification_cache
        cache.update(grid=SimpleNamespace(points=context.positions, source_to_cell=np.arange(count)),
            residual_ids=np.arange(count), broad_fixture=np.zeros(count, bool),
            strong_bars=np.empty(0, np.int64), rebar_tree=None, cell_labels=context.geometry_class,
            features={name: np.zeros(count) for name in ['linearity', 'width', 'length', 'axis_alignment', 'support_count', 'connected_support']})
        cache['features']['connected_support'] = np.zeros(count, bool)
        with patch('algorithms.pointcloud_fusion.recover_rebar', return_value=np.ones(count, bool)):
            report = fuse_classifications(context)
        np.testing.assert_array_equal(context.fused_class[:2], [3, 4])
        self.assertEqual(context.fused_reason[1], 8)
        self.assertEqual(context.fused_steel_score[0], 1.)
        self.assertEqual(context.fused_steel_score[1], 0.)
        self.assertFalse(context.fused_recovered[:2].any())
        self.assertEqual(report['counts']['noise'], 1)
        np.testing.assert_array_equal(regions_from_partition(context.fused_class, context.partition_zone)[:2], [1, 4])

    def test_early_design_snapshot_roundtrips_layers_and_noise(self):
        from pointcloud_step_pipeline import run_from_source
        from rebar_design_prior import SCHEMA, digest_file, inventory_from_bars
        from test_shared_scene import measured_scene
        scene = measured_scene(0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.las'
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.header.scales = [.00001]*3
            las.x, las.y, las.z = scene.positions.T
            las.write(source)
            before = digest_file(source)
            inventory = inventory_from_bars([{'designBarId': 'bar', 'points': [[.1, .1, .04], [.9, .1, .04]],
                'radiusM': .004, 'coverage': 'complete'}], np.eye(4).flatten().tolist())
            snapshot = {'schema': SCHEMA, 'sourcePath': str(source), 'sourceSha256': before, 'inventory': inventory}
            run = run_from_source(source, root/'out', workers=1, through_step=3, design_prior=snapshot, preview_limit=73)
            ids = np.fromfile(run.directory/'preview/source_indices.bin', dtype='<u8')
            saved = laspy.read(run.directory/'pointcloud-with-classes.las')
            for name, preview in [('shared_layer', 'shared_layers'), ('shared_floating_noise', 'shared_floating_noise')]:
                expected = getattr(run.context, name)
                np.testing.assert_array_equal(saved[name], expected)
                np.testing.assert_array_equal(np.load(run.directory/f'{name}.npy'), expected)
                np.testing.assert_array_equal(np.fromfile(run.directory/f'preview/{preview}.bin', dtype='u1'), expected[ids])
            hard = run.context.shared_floating_noise.astype(bool)
            self.assertTrue(hard.any())
            self.assertFalse(np.any(run.context.geometry_class[hard] == 4))
            self.assertFalse(np.any(run.context.projection_class[hard] == 4))
            self.assertEqual(digest_file(source), before)
            steps = {s['id']: s for s in run.manifest['steps']}
            self.assertEqual(steps['02-classification']['dependsOn'], ['01-layering'])
            self.assertEqual(steps['02-projection']['dependsOn'], ['01-layering'])


if __name__ == '__main__':
    unittest.main()
