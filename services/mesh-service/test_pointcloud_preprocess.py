from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

import laspy
import numpy as np
from fastapi import HTTPException

from algorithms.pointcloud_normals import PointCloudContext
from algorithms.preprocessed_las import (
    PROVENANCE_RECORD_ID,
    PROVENANCE_USER_ID,
    load_preprocessed_las,
)
from algorithms.projection_geometry_classifier import prepare_projection
from algorithms.pointcloud_segmentation import segment_points
from pointcloud_preprocess import build_preprocess
from pointcloud_preprocess_api import PreprocessRequest, create_preprocess_router


def make_cloud(path: Path, *, table=True, upper=True):
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [.001, .001, .001]
    header.offsets = [1_000_000., 2_000_000., 10.]
    header.add_extra_dim(laspy.ExtraBytesParams(name="source_quality", type="u2"))
    cloud = laspy.LasData(header)
    parts = []
    if table:
        x, y = np.meshgrid(np.linspace(0, .38, 20), np.linspace(0, .38, 20))
        parts.append(np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size))))
    else:
        x, y = np.meshgrid(np.linspace(0, .10, 10), np.linspace(0, .10, 10))
        parts.append(np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size))))
    if upper:
        x = np.linspace(.04, .34 if table else .09, 48)
        parts.append(np.column_stack((x, np.full(len(x), .19 if table else .05), np.full(len(x), .05))))
    xyz = np.concatenate(parts)
    cloud.x = xyz[:, 0] + header.offsets[0]
    cloud.y = xyz[:, 1] + header.offsets[1]
    cloud.z = xyz[:, 2] + header.offsets[2]
    rows = np.arange(len(xyz), dtype=np.uint16)
    cloud.intensity = rows + 10
    cloud.red = rows + 100
    cloud.green = rows + 200
    cloud.blue = rows + 300
    cloud.gps_time = rows.astype(float) / 10
    cloud.source_quality = rows + 400
    cloud.write(path)
    return laspy.read(path)


def provenance(cloud):
    vlr = next(v for v in cloud.header.vlrs
               if v.user_id.rstrip("\x00") == PROVENANCE_USER_ID and v.record_id == PROVENANCE_RECORD_ID)
    return json.loads(bytes(vlr.record_data).decode("ascii"))


class PointcloudPreprocessTests(unittest.TestCase):
    def test_preserves_source_records_and_subsets_normals_with_provenance(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            source = root / "source.las"
            original = make_cloud(source)
            result = build_preprocess(source, root / "result")
            annotated = laspy.read(root / "result" / "annotated.las")
            cleaned = laspy.read(root / "result" / "cleaned.las")
            mask = np.asarray(annotated.shared_table_mask, dtype=bool)

            self.assertTrue(result["detected"])
            self.assertEqual(result["tablePoints"], int(mask.sum()))
            self.assertEqual(len(cleaned.points), result["pointsAfter"])
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(annotated[name], original[name])
                np.testing.assert_array_equal(cleaned[name], original[name][~mask])
            np.testing.assert_allclose(np.c_[annotated.x, annotated.y, annotated.z],
                                       np.c_[original.x, original.y, original.z], rtol=0, atol=0)
            for name in ("normal_x", "normal_y", "normal_z", "normal_valid",
                         "normal_curvature", "normal_radius"):
                np.testing.assert_array_equal(cleaned[name], annotated[name][~mask])
            self.assertFalse(np.asarray(cleaned.shared_table_mask).any())
            self.assertEqual(provenance(annotated)["artifactRole"], "annotated")
            self.assertEqual(provenance(cleaned)["artifactRole"], "cleaned")
            self.assertEqual(provenance(cleaned)["sourceSha256"], result["sourceSha256"])
            self.assertEqual(json.loads((root / "result" / "manifest.json").read_text()), result)

    def test_cleaned_las_reuses_normals_and_skips_table_fit(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            source = root / "source.las"
            make_cloud(source)
            build_preprocess(source, root / "result")
            cleaned_path = root / "result" / "cleaned.las"
            cleaned = laspy.read(cleaned_path)
            positions = np.ascontiguousarray(np.c_[cleaned.x, cleaned.y, cleaned.z], dtype=np.float64)
            with patch("algorithms.pointcloud_segmentation.estimate_normals", side_effect=AssertionError("must reuse")):
                stages = segment_points(positions, root, source=cleaned_path, through_step=1, workers=1)
            self.assertTrue(stages.computation["persisted"])
            np.testing.assert_array_equal(stages.context.normals[:, 0], cleaned.normal_x)
            with patch("algorithms.projection_geometry_classifier._table_plane", side_effect=AssertionError("must not fit")):
                prepared = prepare_projection(positions, stages.context.normals, stages.context.normal_valid,
                                              fixed_table=provenance(cleaned)["plane"],
                                              fixed_table_mask=np.zeros(len(positions), bool))
            self.assertFalse(prepared["table_mask"].any())

    def test_no_table_and_all_table_outputs(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            no_table = root / "no-table.las"
            original = make_cloud(no_table, table=False, upper=False)
            first = build_preprocess(no_table, root / "no-table")
            self.assertFalse(first["detected"])
            self.assertEqual(first["pointsAfter"], len(original.points))
            self.assertEqual(len(laspy.read(root / "no-table" / "cleaned.las").points), len(original.points))

            all_table = root / "all-table.las"
            make_cloud(all_table, table=True, upper=False)
            with self.assertRaisesRegex(ValueError, "不足 3 个点"):
                build_preprocess(all_table, root / "all-table")
            self.assertFalse((root / "all-table").exists())

    def test_reprocessing_replaces_reserved_vlr_and_publishes_group_writable_directory(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            source = root / "source.las"
            make_cloud(source)
            build_preprocess(source, root / "first")
            build_preprocess(root / "first" / "cleaned.las", root / "second")
            cloud = laspy.read(root / "second" / "cleaned.las")
            vlrs = [v for v in cloud.header.vlrs
                    if v.user_id.rstrip("\x00") == PROVENANCE_USER_ID and v.record_id == PROVENANCE_RECORD_ID]
            self.assertEqual(len(vlrs), 1)
            self.assertEqual((root / "second").stat().st_mode & 0o7777, 0o2770)
            self.assertEqual((root / "second" / "cleaned.las").stat().st_mode & 0o777, 0o660)

    def test_invalid_provenance_rejected_and_failure_not_published(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            source = root / "source.las"
            make_cloud(source)
            with patch("pointcloud_preprocess.estimate_normals", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    build_preprocess(source, root / "failed")
            self.assertFalse((root / "failed").exists())

            build_preprocess(source, root / "result")
            annotated = laspy.read(root / "result" / "annotated.las")
            annotated.header.vlrs[:] = [v for v in annotated.header.vlrs
                                        if not (v.user_id.rstrip("\x00") == PROVENANCE_USER_ID
                                                and v.record_id == PROVENANCE_RECORD_ID)]
            annotated.header.vlrs.append(laspy.VLR(user_id=PROVENANCE_USER_ID,
                                                   record_id=PROVENANCE_RECORD_ID,
                                                   description="broken", record_data=b"{}"))
            broken = root / "broken.las"
            annotated.write(broken)
            count = len(annotated.points)
            outputs = {"normals": np.empty((count, 3), np.float32),
                       "normal_valid": np.empty(count, np.uint8),
                       "curvature": np.empty(count, np.float32),
                       "neighbor_radius": np.empty(count, np.float32)}
            with self.assertRaisesRegex(ValueError, "provenance"):
                load_preprocessed_las(broken, count=count, normal_k=32, normal_output=outputs)

    def test_http_uses_storage_boundary_and_requires_new_output(self):
        router = create_preprocess_router()
        route = next(route for route in router.routes if route.path.endswith("/compute"))
        with tempfile.TemporaryDirectory() as value, patch.dict("os.environ", {"ANALYSIS_MESH_STORAGE_ROOT": value}):
            root = Path(value)
            source = root / "source.las"
            make_cloud(source)
            with self.assertRaises(HTTPException):
                route.endpoint(PreprocessRequest(sourcePath="/etc/passwd", outputPath=str(root / "out")))
            with patch("pointcloud_preprocess_api.build_preprocess", return_value={"ok": True}) as build:
                request = PreprocessRequest(sourcePath=str(source), outputPath=str(root / "out"))
                self.assertEqual(route.endpoint(request), {"ok": True})
                self.assertEqual(build.call_args.args, (source, root / "out"))


if __name__ == "__main__":
    unittest.main()
