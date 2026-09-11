import json
from pathlib import Path
import tempfile
import unittest

import laspy
import numpy as np

from algorithms.pointcloud_normals import PointCloudContext, available_workers, estimate_normals
from pointcloud_step_pipeline import run_from_source


class NormalTests(unittest.TestCase):
    def test_tilted_plane_accuracy_and_shared_index(self):
        x, y = np.meshgrid(np.linspace(-1, 1, 23), np.linspace(-1, 1, 21))
        xyz = np.column_stack((x.ravel(), y.ravel(), (.2*x - .3*y).ravel()))
        # Large native coordinates must not destroy the local covariance.
        xyz += [5.e6, 4.e6, 100]
        context = PointCloudContext.build(xyz)
        tree = context.tree
        stats = estimate_normals(context, k=24, workers=min(2, available_workers()), chunk_size=64)
        truth = np.array([-.2, .3, 1.])
        truth /= np.linalg.norm(truth)
        self.assertTrue(context.normal_valid.all())
        np.testing.assert_allclose(np.abs(context.normals @ truth), 1., atol=2.e-6)
        self.assertIs(context.tree, tree)
        self.assertEqual(stats["treeBuildsDuringNormals"], 0)
        np.testing.assert_allclose(np.linalg.norm(context.normals, axis=1), 1., atol=1.e-6)

    def test_sphere_and_parallel_equivalence(self):
        rng = np.random.default_rng(91)
        xyz = rng.normal(size=(4000, 3))
        xyz /= np.linalg.norm(xyz, axis=1)[:, None]
        a, b = PointCloudContext.build(xyz.copy()), PointCloudContext.build(xyz.copy())
        estimate_normals(a, k=24, workers=1, chunk_size=211)
        estimate_normals(b, k=24, workers=min(4, available_workers()), chunk_size=257)
        self.assertGreater(np.quantile(np.abs(np.sum(a.normals * xyz, axis=1)), .01), .995)
        np.testing.assert_allclose(np.abs(np.sum(a.normals * b.normals, axis=1)), 1, atol=1.e-6)
        np.testing.assert_array_equal(a.normal_valid, b.normal_valid)

    def test_degenerate_and_invalid_inputs(self):
        for xyz in (np.zeros((20, 3)), np.column_stack((np.arange(20), np.zeros((20, 2))))):
            context = PointCloudContext.build(xyz.astype(float))
            estimate_normals(context, k=32, workers=1)
            self.assertFalse(context.normal_valid.any())
            np.testing.assert_array_equal(context.normals, 0)
            self.assertTrue(np.isfinite(context.curvature).all())
        with self.assertRaises(ValueError):
            PointCloudContext.build(np.full((10, 3), np.nan))
        with self.assertRaises(ValueError):
            PointCloudContext.build(np.zeros((2, 3)))

    def test_las_roundtrip_preserves_original_rows_attributes_and_fresh_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.las"
            header = laspy.LasHeader(point_format=3, version="1.2")
            header.scales = [.0001] * 3
            header.offsets = [1.e6, 2.e6, 0]
            header.add_extra_dim(laspy.ExtraBytesParams(name="custom_id", type="u4"))
            las = laspy.LasData(header)
            x, y = np.meshgrid(np.arange(10)*.02, np.arange(12)*.02)
            las.x, las.y, las.z = x.ravel()+1.e6, y.ravel()+2.e6, (x*.1+y*.2).ravel()
            las.intensity = np.arange(len(las.points), dtype=np.uint16)
            las.classification = np.arange(len(las.points), dtype=np.uint8) % 20
            las.red = np.full(len(las.points), 65535, dtype=np.uint16)
            las.green = np.full(len(las.points), 1024, dtype=np.uint16)
            las.blue = np.full(len(las.points), 800, dtype=np.uint16)
            las.custom_id = np.arange(len(las.points), dtype=np.uint32)[::-1]
            las.write(source)
            original_bytes = source.read_bytes()
            first = run_from_source(source, root / "out", k=12, workers=1, preview_limit=50)
            second = run_from_source(source, root / "out", k=16, workers=1, preview_limit=50, through_step=2)
            self.assertNotEqual(first.manifest["runId"], second.manifest["runId"])
            self.assertIsNot(first.context.tree, second.context.tree)
            self.assertEqual(source.read_bytes(), original_bytes)
            saved = laspy.read(first.directory / "pointcloud-with-normals.las")
            original = laspy.read(source)
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(saved[name], original[name], err_msg=name)
            np.testing.assert_array_equal(saved.header.scales, original.header.scales)
            np.testing.assert_array_equal(saved.header.offsets, original.header.offsets)
            np.testing.assert_array_equal(saved.normal_x, first.context.normals[:, 0])
            np.testing.assert_array_equal(saved.normal_valid, first.context.normal_valid)
            manifest = json.loads((first.directory / "manifest.json").read_text())
            self.assertEqual(manifest["source"]["pointCount"], 120)
            self.assertEqual(manifest["preview"]["pointCount"], 50)
            self.assertEqual(json.loads((root / "out/latest.json").read_text())["runId"], second.manifest["runId"])
            positions = np.load(first.directory / "positions.npy", mmap_mode="r")
            np.testing.assert_array_equal(positions, original.xyz)
            ids = np.fromfile(first.directory / "preview/source_indices.bin", dtype="<u8")
            preview_normals = np.fromfile(first.directory / "preview/normals.bin", dtype="<f4").reshape(-1, 3)
            np.testing.assert_array_equal(preview_normals, first.context.normals[ids])
            classified = laspy.read(second.directory / "pointcloud-with-classes.las")
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(classified[name], original[name], err_msg=name)
            np.testing.assert_array_equal(classified.geometry_class, second.context.geometry_class)
            np.testing.assert_array_equal(classified.geometry_support, second.context.geometry_support)
            np.testing.assert_array_equal(classified.geometry_recovered, second.context.geometry_recovered)
            np.testing.assert_array_equal(np.load(second.directory / "geometry_recovered.npy"),
                                          second.context.geometry_recovered)
            np.testing.assert_array_equal(np.fromfile(second.directory / "preview/recovered.bin", dtype="u1"),
                                          second.context.geometry_recovered[ids])
            self.assertIn("recoveredUrl", second.manifest["preview"])
            self.assertNotIn("recoveredUrl", first.manifest["preview"])
            self.assertEqual(sum(second.manifest["classification"]["counts"].values()), 120)
            self.assertIn("classesUrl", second.manifest["preview"])
            self.assertNotIn("classesUrl", first.manifest["preview"])

            both = run_from_source(source, root / "out", k=16, workers=min(2, available_workers()),
                                   preview_limit=50, through_step=3)
            parallel_las = laspy.read(both.directory / "pointcloud-with-classes.las")
            # Running an independent branch must not alter existing labels.
            np.testing.assert_array_equal(both.context.geometry_class, second.context.geometry_class)
            np.testing.assert_array_equal(both.context.geometry_recovered, second.context.geometry_recovered)
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(parallel_las[name], original[name], err_msg=name)
            for name, preview_name in (("projection_class", "projection_classes"), ("projection_layer", "projection_layers")):
                expected = getattr(both.context, name)
                np.testing.assert_array_equal(parallel_las[name], expected)
                np.testing.assert_array_equal(np.load(both.directory / (name+".npy")), expected)
                np.testing.assert_array_equal(np.fromfile(both.directory / "preview" / (preview_name+".bin"), dtype="u1"), expected[ids])
            self.assertIn("projectionClassesUrl", both.manifest["preview"])
            self.assertNotIn("projectionClassesUrl", second.manifest["preview"])
            self.assertEqual(sum(both.manifest["projection"]["counts"].values()), len(original.points))
            self.assertEqual(set(both.manifest["projection"]["images"]),
                             {"binary", "density", "height", "classes", "layers", "edges", "fixtures", "side_0", "side_45", "side_90", "side_135"})
            from PIL import Image
            for view in both.manifest['projection']['multiview']['views']:
                with Image.open(both.directory / 'projection' / (view['key']+'.png')) as image:
                    self.assertEqual(image.size, tuple(reversed(view['gridShape'])))
                    image.verify()
            with np.load(both.directory / 'projection-features.npz', allow_pickle=False) as evidence:
                np.testing.assert_array_equal(evidence['source_web_recovered'], both.context.projection_cache['source_web_recovered'])
            self.assertIsNotNone(both.context.projection_cache)
            self.assertEqual(source.read_bytes(), original_bytes)
            import xml.etree.ElementTree as ET
            ET.parse(both.directory / "projection/layers.svg")
            fused = run_from_source(source, root / "out", k=16, workers=min(2, available_workers()),
                                    preview_limit=50, through_step=4)
            saved_fusion = laspy.read(fused.directory / "pointcloud-with-classes.las")
            np.testing.assert_array_equal(fused.context.geometry_class, both.context.geometry_class)
            np.testing.assert_array_equal(fused.context.projection_class, both.context.projection_class)
            np.testing.assert_array_equal(saved_fusion.source_record_index, np.arange(len(original.points)))
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(saved_fusion[name], original[name], err_msg=name)
            for name, preview_name in (("fused_class", "fused_classes"), ("fused_region", "fused_regions"),
                                       ("fused_recovered", "fused_recovered"), ("fused_reason", "fused_reasons")):
                expected = getattr(fused.context, name)
                np.testing.assert_array_equal(saved_fusion[name], expected)
                np.testing.assert_array_equal(np.load(fused.directory / (name+".npy")), expected)
                np.testing.assert_array_equal(np.fromfile(fused.directory / "preview" / (preview_name+".bin"), dtype="u1"), expected[ids])
            for kind, filename in ((2, "fixture-only.las"), (3, "steel-only.las")):
                subset = laspy.read(fused.directory / filename)
                selected = np.flatnonzero(fused.context.fused_class == kind)
                np.testing.assert_array_equal(subset.source_record_index, selected)
                for name in saved_fusion.point_format.dimension_names:
                    np.testing.assert_array_equal(subset[name], saved_fusion[name][selected], err_msg=name)
            self.assertEqual(sum(fused.manifest['fusion']['counts'].values()), len(original.points))
            self.assertEqual(sum(fused.manifest['regions']['counts'].values()), len(original.points))
            self.assertEqual(source.read_bytes(), original_bytes)
            refined = run_from_source(source, root / 'out', k=16, workers=min(2, available_workers()),
                                      preview_limit=50, through_step=5)
            saved_refined = laspy.read(refined.directory / 'pointcloud-with-classes.las')
            for name in ('geometry_class', 'projection_class', 'fused_class', 'fused_region'):
                np.testing.assert_array_equal(getattr(refined.context, name), getattr(fused.context, name), err_msg=name)
            for name, preview_name in (('refined_class', 'refined_classes'), ('refined_region', 'refined_regions'),
                                       ('refined_zone', 'refined_zones'), ('refined_changed', 'refined_changed'),
                                       ('refined_reason', 'refined_reasons')):
                expected = getattr(refined.context, name)
                np.testing.assert_array_equal(saved_refined[name], expected)
                np.testing.assert_array_equal(np.load(refined.directory / (name+'.npy')), expected)
                np.testing.assert_array_equal(np.fromfile(refined.directory / 'preview' / (preview_name+'.bin'), dtype='u1'), expected[ids])
            for kind, filename in ((2, 'fixture-only.las'), (3, 'steel-only.las')):
                subset = laspy.read(refined.directory / filename)
                selected = np.flatnonzero(refined.context.refined_class == kind)
                np.testing.assert_array_equal(subset.source_record_index, selected)
                for name in saved_refined.point_format.dimension_names:
                    np.testing.assert_array_equal(subset[name], saved_refined[name][selected], err_msg=name)
            self.assertEqual(refined.manifest['files']['subsetClassAttribute'], 'refined_class')
            self.assertEqual(sum(refined.manifest['refinement']['counts'].values()), len(original.points))
            self.assertEqual(source.read_bytes(), original_bytes)

    def test_failed_run_does_not_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            las = laspy.create()
            las.x, las.y, las.z = [0, 1], [0, 0], [0, 0]
            las.write(root / "tiny.las")
            with self.assertRaises(ValueError):
                run_from_source(root / "tiny.las", root / "out", workers=1)
            self.assertFalse((root / "out/latest.json").exists())
            self.assertEqual(list((root / "out").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
