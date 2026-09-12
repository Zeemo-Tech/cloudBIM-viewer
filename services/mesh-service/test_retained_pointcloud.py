from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from algorithms import pointcloud_segmentation as pipeline
from test_shared_scene import measured_scene


class RetainedPointcloudTests(unittest.TestCase):
    def test_all_stages_keep_source_population_and_evidence(self):
        points = measured_scene(0).positions
        inventory = {'bars': [{'designBarId': 'rod', 'points': [[-.6, 0, .08], [.6, 0, .08]],
                               'radiusM': .005, 'coverage': 'complete'}]}
        seen = {}
        def watch(name, original):
            def call(context, *args, **kwargs):
                xyz = context if isinstance(context, np.ndarray) else context.positions
                seen[name] = len(xyz)
                np.testing.assert_array_equal(xyz, points)
                if hasattr(context, 'tree'): self.assertEqual(context.tree.n, len(xyz))
                return original(context, *args, **kwargs)
            return call
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(pipeline, 'classify_geometry', side_effect=watch('02A', pipeline.classify_geometry)), \
             patch.object(pipeline, 'classify_projection', side_effect=watch('02B', pipeline.classify_projection)), \
             patch.object(pipeline, 'fuse_classifications', side_effect=watch('03', pipeline.fuse_classifications)), \
             patch.object(pipeline, 'segment_internal_rebar', side_effect=watch('05', pipeline.segment_internal_rebar)):
            run = pipeline.segment_points(points, Path(tmp), workers=2, through_step=6, design_inventory=inventory)
            context = run.context; hard = context.shared_floating_noise.astype(bool)
            self.assertFalse(hasattr(context, 'retained_context'))
            self.assertFalse(hard[context.shared_table_mask.astype(bool)].any())
            self.assertEqual(seen, {name: len(points) for name in ('02A', '02B', '03', '05')})
            self.assertEqual(len(context.classification_cache['grid'].source_to_cell), len(points))
            self.assertEqual(int(context.scene_cache['projection']['density'].sum()),
                             int((~context.shared_table_mask.astype(bool)).sum()))
            table = context.shared_table_mask.astype(bool)
            for name in ('geometry_class', 'projection_class', 'fused_class', 'refined_class'):
                self.assertTrue((getattr(context, name)[table] == 1).all())
            np.testing.assert_array_equal(context.positions, points)
            np.testing.assert_array_equal(context.refined_zone, context.partition_zone)
            np.testing.assert_array_equal(context.refined_region, context.fused_region)

    def test_outside_steel_is_filtered_only_after_classification_and_exports_keep_source_rows(self):
        import laspy
        from pointcloud_step_pipeline import run_from_source
        from rebar_design_prior import SCHEMA, digest_file, inventory_from_bars
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source.las'
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.header.scales = [.00001]*3
            x,y = np.meshgrid(np.arange(10)*.01, np.arange(10)*.01)
            las.x=x.ravel(); las.y=y.ravel(); las.z=np.zeros(100); las.write(source)
            inventory = inventory_from_bars([{'designBarId':'a', 'points':[[10,10,10],[11,10,10]],
                                             'radiusM':.005, 'coverage':'complete'}], np.eye(4).ravel().tolist())
            snapshot = {'schema':SCHEMA, 'sourcePath':str(source), 'sourceSha256':digest_file(source),
                        'inventory':inventory, 'fingerprint':'empty-cloth-test'}
            for step in (6,7):
                run = run_from_source(source, root/str(step), workers=1, through_step=step,
                    design_prior=snapshot, prior_mode='geometry' if step==7 else 'off')
                self.assertEqual(int(run.context.shared_floating_noise.sum()), 100)
                steel = run.context.refined_class == 3
                self.assertEqual(len(run.context.positions), 100)
                self.assertTrue((run.context.internal_type[steel] == 5).all())
                with laspy.open(run.directory/'noise-only.las') as noise:
                    self.assertEqual(noise.header.point_count, int(steel.sum()))
                if step==7:
                    self.assertEqual(run.manifest['completeRebar']['counts']['noise'], int(steel.sum()))
                    self.assertTrue((run.context.complete_class[steel] == 4).all())

    def test_design_does_not_change_either_branch_or_fusion(self):
        points = measured_scene(0).positions
        inventory = {'bars': [{'points': [[10, 10, 10], [11, 10, 10]], 'radiusM': .005, 'coverage': 'complete'}]}
        with tempfile.TemporaryDirectory() as tmp:
            runs = []
            for index, design in enumerate((None, inventory)):
                directory = Path(tmp)/str(index)
                directory.mkdir()
                runs.append(pipeline.segment_points(points, directory, workers=1+index, through_step=4,
                                                    design_inventory=design))
            for name in ('geometry_class', 'geometry_support', 'projection_class', 'projection_layer',
                         'fused_class', 'fused_steel_score', 'fused_steel_evidence'):
                np.testing.assert_array_equal(getattr(runs[0].context, name), getattr(runs[1].context, name))


if __name__ == '__main__': unittest.main()
