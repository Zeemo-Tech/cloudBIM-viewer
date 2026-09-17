"""Control-net stage boundaries and source-preserving artifacts."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import laspy
import numpy as np
from pointcloud_step_pipeline import write_las, write_preview, run_from_source
from algorithms.pointcloud_segmentation import segment_points, SCENE_ATTRIBUTES, FUSION_ATTRIBUTES

class EarlyPipelineTests(unittest.TestCase):
    def test_table_only_preserves_boundary_without_height_inference(self):
        from algorithms.projection_geometry_classifier import prepare_projection
        points = np.random.default_rng(7).uniform([0, 0, 0], [1, 1, .3], (800, 3))
        normals = np.tile([0., 0., 1.], (len(points), 1))
        valid = np.ones(len(points), dtype='u1')
        plane = {'origin': [0., 0., .04], 'slopes': [.01, -.02]}
        with patch('algorithms.projection_geometry_classifier._table_plane', return_value=plane):
            ordinary = prepare_projection(points, normals, valid)
            with patch('algorithms.projection_geometry_classifier._height_layers', side_effect=AssertionError('height layers ran')):
                early = prepare_projection(points, normals, valid, table_only=True)
                fixed = prepare_projection(points, normals, valid, table_only=True,
                    fixed_table=plane, fixed_table_mask=ordinary['table_mask'])
        np.testing.assert_array_equal(early['table_mask'], ordinary['table_mask'])
        np.testing.assert_array_equal(fixed['table_mask'], ordinary['table_mask'])
        self.assertNotIn('layers', early)
        self.assertNotIn('density', early)

    def test_table_boundary_skips_design_and_classification(self):
        points = np.random.default_rng(19).normal(size=(80, 3))
        table = np.arange(80) < 20
        with TemporaryDirectory() as tmp, \
                patch('algorithms.projection_geometry_classifier.prepare_projection', return_value={
                    'table_mask': table, 'table': {'z': 0}, 'timings': {'tableFitS': 0.1}}), \
                patch('algorithms.pointcloud_segmentation.prepare_scene') as scene, \
                patch('algorithms.pointcloud_segmentation.prepare_floating_scene') as floating, \
                patch('algorithms.pointcloud_segmentation.classify_geometry') as classifier:
            result = segment_points(points, Path(tmp), k=8, workers=1, through_step=2, stop_after_table=True)
            scene.assert_not_called(); floating.assert_not_called(); classifier.assert_not_called()
            self.assertIsNone(result.classification); self.assertIsNone(result.internal_rebar)
            np.testing.assert_array_equal(result.context.shared_table_mask, table)
            self.assertEqual(result.preprocessing['tableRemoval']['remainingPoints'], 60)
            self.assertNotIn('layering', result.preprocessing)
            for name in SCENE_ATTRIBUTES:
                self.assertEqual(len(result.arrays[name]), len(points))

    def test_segmentation_layer_boundary_never_calls_zones_or_classifiers(self):
        points = np.random.default_rng(19).normal(size=(80, 3))
        def scene(context, *, output, **kwargs):
            output['shared_table_mask'][:] = np.arange(80) < 20
            output['partition_zone'][:] = 1
            context.shared_table_mask = output['shared_table_mask']
            context.partition_zone = output['partition_zone']
            context.scene_cache = {}
            return {'tableRemoval': {'remainingPoints': 60}, 'partition': {}}
        with TemporaryDirectory() as tmp, \
             patch('algorithms.pointcloud_segmentation.prepare_scene', side_effect=scene), \
             patch('algorithms.shared_floating_noise.classify_floating_zones', side_effect=AssertionError('zones ran')), \
             patch('algorithms.pointcloud_segmentation.classify_geometry', side_effect=AssertionError('classifier ran')), \
             patch('algorithms.pointcloud_segmentation.classify_projection', side_effect=AssertionError('projection ran')), \
             patch('algorithms.pointcloud_segmentation.fuse_classifications', side_effect=AssertionError('fusion ran')):
            result = segment_points(points, Path(tmp), k=8, workers=1, through_step=2, stop_after_layering=True)
        self.assertIn('layering', result.preprocessing)
        self.assertNotIn('floatingZones', result.preprocessing)
        self.assertIsNone(result.context.fused_class)
        self.assertFalse(result.context.shared_floating_noise.any())

    def test_control_run_requires_mode_snapshot_and_fusion_step(self):
        with TemporaryDirectory() as tmp:
            for kwargs in ({'control_net_mode':'auto', 'through_step':4},
                           {'control_net_mode':'aligned', 'through_step':3, 'design_prior':{}},
                           {'control_net_mode':'aligned', 'through_step':8, 'design_prior':{}},
                           {'control_net_mode':'bad', 'through_step':2, 'design_prior':{}}):
                with self.assertRaises(ValueError):
                    run_from_source(Path(tmp)/'absent.las', Path(tmp)/'out', workers=1, **kwargs)
            self.assertFalse((Path(tmp)/'out').exists())

    def test_fusion_result_and_layers_are_control_inputs(self):
        from algorithms.pointcloud_normals import PointCloudContext
        events = []
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.las'
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.x = [0., .1, .2, .3]; las.y = [0.]*4; las.z = [0.]*4
            las.write(source)
            context = PointCloudContext(np.zeros((4, 3)), None,
                normals=np.zeros((4, 3)), normal_valid=np.ones(4, dtype='u1'),
                shared_table_mask=np.array([1,0,0,0], dtype='u1'),
                fused_class=np.array([1,3,3,2], dtype='u1'),
                shared_layer=np.array([0,1,3,0], dtype='u1'))
            layering = {'layers': [{'id':1,'name':'底层钢筋'}]}
            stages = SimpleNamespace(context=context, arrays={}, shapes={}, computation={'effectiveK':3},
                timing={}, preprocessing={'layering':layering}, classification={'ready':True},
                projection={'ready':True}, fusion={'ready':True}, regions={}, refinement=None,
                internal_rebar=None, complete_rebar=None, execution={})
            def segment(*args, **kwargs):
                self.assertEqual(kwargs['through_step'], 4)
                self.assertFalse(kwargs.get('stop_after_table', False))
                events.append('fusion')
                return stages
            def fit(points, table, inventory, **kwargs):
                self.assertEqual(events, ['fusion'])
                self.assertIs(kwargs['fused_classes'], context.fused_class)
                self.assertIs(kwargs['layer_ids'], context.shared_layer)
                self.assertIs(kwargs['layering'], layering)
                self.assertEqual(kwargs['workers'], 1)
                events.append('fit')
                return {'version':'test','inputStage':'post-fusion'}, {
                    'control_status':np.array([0,1,2,4],dtype='u1'),
                    'control_instance':np.array([0,1,0,0],dtype='<u4')}
            with patch('pointcloud_step_pipeline.resolve_design_inputs', return_value=SimpleNamespace(
                    inventory={}, dimensions={}, report={'design':{}})), \
                    patch('pointcloud_step_pipeline.segment_points', side_effect=segment), \
                    patch('algorithms.rebar_control_net.fit_control_net', side_effect=fit), \
                    patch('pointcloud_step_pipeline.write_las'), \
                    patch('pointcloud_step_pipeline.write_preview', return_value={}):
                result = run_from_source(source, root/'out', through_step=4,
                    control_net_mode='aligned', design_prior={}, workers=1)
            ids = [step['id'] for step in result.manifest['steps']]
            self.assertEqual(ids[-2:], ['03-fusion', '03-control-net'])
            self.assertIn('01-layering', ids)
            self.assertNotIn('01-control-net', ids)
            self.assertIn('controlNetExcludedLasUrl', result.manifest['files'])
            self.assertEqual(result.manifest['controlNet']['inputStage'], 'post-fusion')

    def test_table_result_is_fit_without_any_layer_or_classification_input(self):
        from algorithms.pointcloud_normals import PointCloudContext
        events = []
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.las'
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.x = [0., .1, .2, .3]; las.y = [0.]*4; las.z = [0.]*4
            las.write(source)
            context = PointCloudContext(np.zeros((4, 3)), None,
                normals=np.zeros((4, 3)), normal_valid=np.ones(4, dtype='u1'),
                shared_table_mask=np.array([1,0,0,0], dtype='u1'),
                fused_class=None,
                shared_layer=np.array([0,1,3,0], dtype='u1'))
            layering = {'layers': [{'id':1,'name':'底层钢筋'}]}
            stages = SimpleNamespace(context=context, arrays={}, shapes={}, computation={'effectiveK':3},
                timing={}, preprocessing={'tableRemoval': {'remainingPoints': 3}}, classification=None,
                projection=None, fusion=None, regions={}, refinement=None,
                internal_rebar=None, complete_rebar=None, execution={})
            def segment(*args, **kwargs):
                self.assertEqual(kwargs['through_step'], 2)
                self.assertTrue(kwargs['stop_after_table'])
                self.assertFalse(kwargs.get('stop_after_layering', False))
                events.append('table')
                return stages
            def fit(points, table, inventory, **kwargs):
                self.assertEqual(events, ['table'])
                self.assertIsNone(kwargs['fused_classes'])
                self.assertIsNone(kwargs['layer_ids'])
                self.assertIsNone(kwargs['layering'])
                self.assertEqual(kwargs['workers'], 1)
                events.append('fit')
                return {'version':'test','inputStage':'post-table'}, {
                    'control_status':np.array([0,1,2,2],dtype='u1'),
                    'control_instance':np.array([0,1,0,0],dtype='<u4')}
            with patch('pointcloud_step_pipeline.resolve_design_inputs', return_value=SimpleNamespace(
                    inventory={}, dimensions={}, report={'design':{}})), \
                    patch('pointcloud_step_pipeline.segment_points', side_effect=segment), \
                    patch('algorithms.rebar_control_net.fit_control_net', side_effect=fit), \
                    patch('pointcloud_step_pipeline.write_las'), \
                    patch('pointcloud_step_pipeline.write_preview', return_value={}):
                result = run_from_source(source, root/'out', through_step=2,
                    control_net_mode='aligned', design_prior={}, workers=1)
            ids = [step['id'] for step in result.manifest['steps']]
            self.assertEqual(ids[-2:], ['01-table-removal', '01-control-net'])
            self.assertNotIn('01-layering', ids)
            self.assertNotIn('01-partition', ids)
            self.assertEqual(result.manifest['steps'][-1]['dependsOn'], ['01-table-removal'])
            self.assertNotIn('03-fusion', ids)
            self.assertNotIn('02-classification', ids)
            self.assertNotIn('floatingZones', result.manifest['preprocessing'])
            self.assertIn('controlNetExcludedLasUrl', result.manifest['files'])
            self.assertEqual(result.manifest['controlNet']['inputStage'], 'post-table')

    def test_subsets_keep_original_dimensions_and_unmatched_points(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.x = np.arange(8, dtype=float)/10; las.y = np.zeros(8); las.z = np.zeros(8)
            las.intensity = np.arange(20, 28, dtype=np.uint16); las.gps_time = np.arange(8, dtype=float) + .123
            source = root/'source.las'; las.write(source); zeros = np.zeros(8, dtype='u1')
            context = SimpleNamespace(positions=np.column_stack((las.x, las.y, las.z)),
                normals=np.tile([0, 1, 0], (8, 1)), normal_valid=np.ones(8, dtype='u1'),
                curvature=np.zeros(8), neighbor_radius=np.ones(8),
                shared_table_mask=np.array([1,0,0,0,0,0,0,0], dtype='u1'),
                partition_zone=zeros, shared_layer=zeros, shared_floating_noise=zeros,
                geometry_class=None, projection_class=None, fused_class=None,
                refined_class=None, internal_type=None, complete_class=None,
                control_status=np.array([0,1,2,3,1,2,4,1], dtype='u1'),
                control_instance=np.array([0,1,0,0,4,0,0,4], dtype='<u4'))
            for name, dtype in FUSION_ATTRIBUTES.items():
                setattr(context, name, np.zeros(8, dtype=dtype))
            context.fused_class[:] = [1,3,3,3,3,3,2,3]
            subsets = {f'control-{name}':root/f'{name}.las' for name in ('steel','pending','removed','excluded')}
            write_las(source, root/'full.las', context, subsets)
            for name, ids in {'steel':[1,4,7], 'pending':[2,5], 'removed':[3], 'excluded':[6]}.items():
                actual = laspy.read(root/f'{name}.las')
                for attr in ('X', 'gps_time', 'intensity'):
                    np.testing.assert_array_equal(actual[attr], las[attr][ids])
                np.testing.assert_array_equal(actual.source_record_index, ids)
                np.testing.assert_array_equal(actual.control_instance, context.control_instance[ids])
                np.testing.assert_array_equal(actual.fused_class, context.fused_class[ids])
            self.assertEqual(len(laspy.read(root/'full.las').points), 8)
            preview = write_preview(root, context, np.zeros((8,3), dtype='u1'), 'run', 5)
            ids = np.fromfile(root/'preview/source_indices.bin', dtype='<u8')
            for name, dtype in [('control_status','u1'),('control_instance','<u4')]:
                self.assertIn(name+'Url', preview)
                np.testing.assert_array_equal(np.fromfile(root/f'preview/{name}.bin', dtype=dtype), getattr(context,name)[ids])

if __name__ == '__main__':
    unittest.main()
