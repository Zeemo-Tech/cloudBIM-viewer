"""Shared preparation and source identity contracts, including a rotated frame."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import laspy
import numpy as np

from algorithms.pointcloud_normals import PointCloudContext
from algorithms.shared_scene import prepare_scene, regions_from_partition
from algorithms.projection_geometry_classifier import classify_projection, _table_plane
from algorithms.normal_geometry_classifier import classify_geometry
from algorithms.fixture_regions import _detect_frame


def measured_scene(angle=23):
    clouds, normals = [], []
    def plane(x, y, z):
        xx, yy = np.meshgrid(x, y)
        p = np.column_stack((xx.ravel(), yy.ravel(), np.full(xx.size, z)))
        clouds.append(p); normals.append(np.tile([0., 0., 1.], (len(p), 1)))
    plane(np.arange(-.7, .701, .005), np.arange(-.45, .451, .005), 0.)
    for x in (-.5, .468):
        plane(np.arange(x, x+.033, .002), np.arange(-.3, .301, .002), .04)
    for y in (-.3, .268):
        plane(np.arange(-.5, .501, .002), np.arange(y, y+.033, .002), .04)
    axis, theta = np.meshgrid(np.arange(-.6, .601, .003), np.linspace(0, 2*np.pi, 20, endpoint=False))
    clouds.append(np.column_stack((axis.ravel(), (.005*np.cos(theta)).ravel(), (.08+.005*np.sin(theta)).ravel())))
    normals.append(np.column_stack((np.zeros(axis.size), np.cos(theta).ravel(), np.sin(theta).ravel())))
    xyz, nn = np.vstack(clouds), np.vstack(normals)
    r = np.deg2rad(angle); axes = np.array([[np.cos(r), np.sin(r)], [-np.sin(r), np.cos(r)]])
    xyz[:, :2] = xyz[:, :2] @ axes; nn[:, :2] = nn[:, :2] @ axes
    context = PointCloudContext.build(xyz)
    context.normals, context.normal_valid = nn.astype(np.float32), np.ones(len(xyz), np.uint8)
    return context


class SharedSceneTests(unittest.TestCase):
    def test_region_retention_does_not_fabricate_geometry_evidence(self):
        c = PointCloudContext.build(np.array([[0., 0., .02], [.01, 0., .02], [0., .01, .02]]))
        c.normals = np.zeros((3, 3), np.float32)
        c.normal_valid = np.zeros(3, np.uint8)
        c.shared_table_mask = np.zeros(3, np.uint8)
        c.scene_cache = {'region_owned': np.ones(3, bool)}
        classify_geometry(c, workers=1)
        np.testing.assert_array_equal(c.geometry_class, 3)
        self.assertFalse(c.classification_cache['source_steel_evidence'].any())
        self.assertFalse(c.geometry_recovered.any())

    def test_recovered_cell_does_not_mark_table_rows_as_recovered_steel(self):
        # Both rows straddle the 5 mm table clearance within one 3 mm voxel.
        c = PointCloudContext.build(np.array([[0., 0., 0.], [.1, .1, .0049],
                                             [.1001, .1001, .0051], [.2, .2, .01]]))
        c.normals = np.tile([0., 0., 1.], (4, 1)).astype(np.float32)
        c.normal_valid = np.ones(4, np.uint8)
        c.shared_table_mask = np.array([1, 1, 0, 0], np.uint8)
        c.scene_cache = {'region_owned': np.zeros(4, bool)}
        # Exercise source mapping after a support cell has been recovered.
        with patch('algorithms.normal_geometry_classifier.recover_rebar',
                   side_effect=lambda points, features, seeds, tree, candidates, *args: candidates.copy()):
            classify_geometry(c, workers=1)
        cells = c.classification_cache['grid'].source_to_cell
        self.assertEqual(cells[1], cells[2])
        self.assertEqual(c.geometry_class[1], 1)
        self.assertEqual(c.geometry_class[2], 3)
        np.testing.assert_array_equal(c.geometry_recovered[1:3], [0, 1])
        self.assertFalse(np.any((c.geometry_recovered == 1) & (c.geometry_class != 3)))

    def test_table_then_rotated_partition_once_and_branch_reuse(self):
        c = measured_scene()
        xyz = c.positions.copy()
        tree = c.tree
        with patch('algorithms.projection_geometry_classifier._table_plane', wraps=_table_plane) as table_fit, \
             patch('algorithms.fixture_regions._detect_frame', wraps=_detect_frame) as frame_fit:
            report = prepare_scene(c)
            self.assertTrue(report['tableRemoval']['detected'])
            self.assertTrue(report['partition']['frame']['innerDetected'])
            self.assertGreater(report['partition']['counts']['interior'], 1000)
            with patch('algorithms.normal_geometry_classifier.dominant_plane', side_effect=AssertionError('duplicate table fit')):
                a = classify_geometry(c, workers=1)
            b, cache, out = classify_projection(c.positions, c.normals, c.normal_valid, workers=1,
                prepared=c.scene_cache['projection'], region_owned=c.scene_cache['region_owned'])
            self.assertEqual(table_fit.call_count, 1)
            self.assertEqual(frame_fit.call_count, 1)
        table = c.shared_table_mask.astype(bool)
        inner = c.scene_cache['region_owned']
        np.testing.assert_array_equal(c.geometry_class == 1, table)
        np.testing.assert_array_equal(out['projection_class'] == 1, table)
        self.assertFalse(np.any(c.geometry_class[inner] == 2))
        self.assertFalse(np.any(out['projection_class'][inner] == 2))
        self.assertTrue(np.all(c.geometry_class[inner] == 3))
        self.assertTrue(np.all(out['projection_class'][inner] == 3))
        self.assertTrue(np.all(c.geometry_class[c.classification_cache['source_steel_evidence']] == 3))
        self.assertTrue(np.all(out['projection_class'][cache['source_steel_evidence']] == 3))
        np.testing.assert_array_equal(cache['source_steel_evidence'],
            cache['source_shape_steel_evidence'] | cache['source_height_steel_evidence'])
        self.assertFalse(np.any(out['projection_class'] == 0))
        self.assertGreater(a['diagnostics']['fixtureSearchExcludedCells'], 0)
        self.assertEqual(b['diagnostics']['tableFitCalls'], 0)
        self.assertIs(cache['source_to_pixel'], c.scene_cache['projection']['source_to_pixel'])
        self.assertIs(cache['table_mask'], c.scene_cache['projection']['table_mask'])
        excluded_pixels = ~c.scene_cache['projection']['fixture_candidate_pixels']
        self.assertFalse(np.isfinite(cache['fixture_footprint_ceiling'][excluded_pixels]).any())
        self.assertFalse(cache['source_fixture_footprint'][inner].any())
        self.assertIs(c.tree, tree)
        np.testing.assert_array_equal(c.positions, xyz)
        # Shared layer metadata must not acquire B's class decisions.
        self.assertTrue(all('classCounts' not in layer for layer in c.scene_cache['projection']['layers']))

    def test_missing_frame_never_claims_inner_steel(self):
        rng = np.random.default_rng(8)
        c = PointCloudContext.build(rng.normal(size=(80, 3))*.01)
        c.normals = np.zeros((80, 3), np.float32)
        c.normal_valid = np.zeros(80, np.uint8)
        report = prepare_scene(c)
        self.assertFalse(report['tableRemoval']['detected'])
        self.assertFalse(report['partition']['frame']['detected'])
        self.assertFalse(c.scene_cache['region_owned'].any())
        np.testing.assert_array_equal(c.partition_zone, 0)
        np.testing.assert_array_equal(regions_from_partition(np.full(80, 3), c.partition_zone), 4)

    def test_pipeline_single_worker_and_parallel_have_identical_shared_results(self):
        from algorithms.pointcloud_segmentation import segment_points
        c = measured_scene(0)
        # Source normals are controlled to exercise branch scheduling independently
        # from the separately tested PCA implementation.
        def known_normals(context, output, **kwargs):
            for name, value in [('normals', c.normals), ('normal_valid', c.normal_valid),
                                ('curvature', np.zeros(len(c.positions))), ('neighbor_radius', np.ones(len(c.positions))*.01)]:
                output[name][:] = value
                setattr(context, name, output[name])
            return {'effectiveK': 32}
        with tempfile.TemporaryDirectory() as tmp, patch('algorithms.pointcloud_segmentation.estimate_normals', side_effect=known_normals):
            runs = []
            for workers in (1, 2):
                path = Path(tmp)/str(workers); path.mkdir()
                with patch('algorithms.projection_geometry_classifier._table_plane', wraps=_table_plane) as table_fit, \
                     patch('algorithms.fixture_regions._detect_frame', wraps=_detect_frame) as frame_fit, \
                     patch('algorithms.region_refinement.same_surface_votes', side_effect=AssertionError('retired cleanup')):
                    run = segment_points(c.positions, path, workers=workers, through_step=5)
                    self.assertEqual(table_fit.call_count, 1)
                    self.assertEqual(frame_fit.call_count, 1)
                self.assertTrue(run.refinement['diagnostics']['reusedPartition'])
                self.assertTrue(run.regions['reusedPreclassificationFrame'])
                self.assertEqual(run.refinement['mode'], 'fusion-pass-through')
                np.testing.assert_array_equal(run.context.refined_class, run.context.fused_class)
                self.assertFalse(run.context.refined_changed.any())
                for alias, source in [('refined_class', 'fused_class'), ('refined_region', 'fused_region'),
                                      ('refined_zone', 'partition_zone')]:
                    view = getattr(run.context, alias)
                    self.assertTrue(np.shares_memory(view, getattr(run.context, source)))
                    with self.assertRaises(ValueError):
                        view[0] = 0
                    self.assertFalse((path/f'{alias}.npy').exists())
                both = (run.context.classification_cache['source_steel_evidence'] &
                        run.context.projection_cache['source_steel_evidence'] & (run.context.fused_class == 3))
                np.testing.assert_array_equal(run.context.fused_steel_score == 1, both)
                runs.append(run)
            for field in ('shared_table_mask', 'partition_zone', 'geometry_class', 'projection_class',
                          'fused_class', 'fused_steel_score', 'fused_steel_evidence', 'refined_class'):
                np.testing.assert_array_equal(getattr(runs[0].context, field), getattr(runs[1].context, field), err_msg=field)

    def test_new_fields_roundtrip_las_npy_and_preview(self):
        from pointcloud_step_pipeline import run_from_source
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.las'
            xyz = np.random.default_rng(3).uniform(-.03, .03, (200, 3))
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.header.scales = [.00001]*3
            las.x, las.y, las.z = xyz.T
            las.intensity = np.arange(200, dtype=np.uint16)
            las.write(source)
            source_bytes = source.read_bytes()
            result = run_from_source(source, root/'out', workers=1, through_step=5, preview_limit=50)
            saved = laspy.read(result.directory/'pointcloud-with-classes.las')
            ids = np.fromfile(result.directory/'preview/source_indices.bin', dtype='<u8')
            for name, preview_name, dtype in (
                ('shared_table_mask', 'shared_table_mask', 'u1'), ('partition_zone', 'partition_zones', 'u1'),
                ('fused_steel_score', 'fused_steel_score', '<f4'), ('fused_steel_evidence', 'fused_steel_evidence', 'u1'),
                ('refined_class', 'refined_classes', 'u1'), ('refined_region', 'refined_regions', 'u1'),
                ('refined_zone', 'refined_zones', 'u1'), ('refined_changed', 'refined_changed', 'u1'),
                ('refined_reason', 'refined_reasons', 'u1')):
                expected = getattr(result.context, name)
                np.testing.assert_array_equal(saved[name], expected)
                np.testing.assert_array_equal(np.load(result.directory/f'{name}.npy'), expected)
                np.testing.assert_array_equal(np.fromfile(result.directory/'preview'/f'{preview_name}.bin', dtype=dtype), expected[ids])
            np.testing.assert_array_equal(saved.intensity, np.arange(200))
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertIn('preprocessing', result.manifest)
            self.assertNotIn('04-refinement', [s['id'] for s in result.manifest['steps']])


if __name__ == '__main__':
    unittest.main()
