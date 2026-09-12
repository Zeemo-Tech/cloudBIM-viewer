import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import laspy
import numpy as np
from scipy.spatial import cKDTree

from algorithms.internal_rebar import (
    InternalRebarParameters,
    _height_bands,
    assign_cylinders,
    segment_internal_rebar,
)
from algorithms.pointcloud_normals import PointCloudContext, available_workers
from pointcloud_step_pipeline import run_from_source


def cylinder(start, end, radius=.0025, along=35, around=12):
    start = np.asarray(start, np.float64)
    axis = np.asarray(end, np.float64) - start
    length = np.linalg.norm(axis)
    axis /= length
    auxiliary = np.eye(3)[np.argmin(np.abs(axis))]
    u = np.cross(axis, auxiliary)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    t = np.linspace(0, length, along)
    theta = np.linspace(0, 2 * np.pi, around, endpoint=False)
    radial = np.cos(theta)[None, :, None] * u + np.sin(theta)[None, :, None] * v
    points = start + t[:, None, None] * axis + radius * radial
    normals = np.broadcast_to(radial, points.shape)
    axes = np.broadcast_to(axis, points.shape)
    return points.reshape(-1, 3), normals.reshape(-1, 3), axes.reshape(-1, 3)


def model(start, end, radius=.005, kind=1):
    start, end = np.asarray(start, float), np.asarray(end, float)
    axis = end - start
    length = np.linalg.norm(axis)
    axis /= length
    return {"type": kind, "center": (start + end) / 2, "axis": axis,
            "low": -length / 2, "high": length / 2, "radius": radius,
            "fitMedianErrorM": 0.0}


def synthetic_context(reverse_normals=False):
    members = [
        ((-.12, -.04, .02), (.12, -.04, .02)),
        ((-.12, .04, .02), (.12, .04, .02)),
        ((-.12, -.04, .08), (.12, -.04, .08)),
        ((-.12, .04, .08), (.12, .04, .08)),
        ((-.08, -.02, .02), (.08, -.02, .08)),
        ((-.08, .02, .02), (.08, .02, .08)),
    ]
    samples = [cylinder(*member) for member in members]
    points = np.vstack([sample[0] for sample in samples])
    normals = np.vstack([sample[1] for sample in samples])
    axes = np.vstack([sample[2] for sample in samples])
    member_size = len(samples[0][0])

    # A strict-inner point with no cylinder support must remain type 4, while a
    # point geometrically on a rod but outside the inner zone must remain type 0.
    points = np.vstack((points, [[.30, 0, .05], samples[0][0][0]]))
    normals = np.vstack((normals, [[0, 0, 1], samples[0][1][0]]))
    axes = np.vstack((axes, [[0, 0, 0], samples[0][2][0]]))
    if reverse_normals:
        normals = -normals

    context = PointCloudContext.build(points.copy())
    context.normals = normals.copy()
    context.refined_class = np.full(len(points), 3, np.uint8)
    context.refined_zone = np.ones(len(points), np.uint8)
    context.refined_zone[-1] = 2
    grid = SimpleNamespace(points=points.copy(), origin=np.zeros(3),
                           source_to_cell=np.arange(len(points), dtype=np.int32),
                           tree=cKDTree(points))
    context.classification_cache = {
        "grid": grid,
        "residual_ids": np.arange(len(points), dtype=np.int32),
        "features": {"axis": axes, "linearity": np.r_[np.full(len(points)-2, .95), 0, .95]},
    }
    context.region_cache = {"frame_axes": np.eye(2)}
    return context, member_size


class InternalRebarTests(unittest.TestCase):
    def test_exterior_density_review_uses_fixture_context_and_steel_scores(self):
        from test_multiview_floating_noise import rounded_fixture_lip
        points, normals, fixture, fn = rounded_fixture_lip()
        for score, background_class, expected_noise in ((.65, 2, True), (1., 2, False), (.65, 1, False)):
            context = PointCloudContext.build(np.vstack((points, fixture)))
            context.normals = np.vstack((normals, fn))
            context.refined_class = np.r_[np.full(len(points), 3, np.uint8),
                                          np.full(len(fixture), background_class, np.uint8)]
            context.refined_zone = np.full(len(context.positions), 3, np.uint8)
            context.fused_steel_score = np.r_[np.full(len(points), score, np.float32),
                                              np.zeros(len(fixture), np.float32)]
            scores_before = context.fused_steel_score.copy()
            report = segment_internal_rebar(context, workers=1)
            np.testing.assert_array_equal(context.internal_type[:len(points)], 5 if expected_noise else 0)
            np.testing.assert_array_equal(context.internal_type[len(points):], 0)
            np.testing.assert_array_equal(context.fused_steel_score, scores_before)
            self.assertEqual(report['denoising']['exteriorRemovedPointCount'], len(points) if expected_noise else 0)

    def test_exterior_high_score_noise_is_reviewed_while_round_steel_survives(self):
        from test_multiview_floating_noise import rod, ball
        steel, normals = rod([0, 0, 0], [.18, 0, 0])
        floating, floating_normals = ball([.08, 0, .025])
        context = PointCloudContext.build(np.vstack((steel, floating)))
        context.normals = np.vstack((normals, floating_normals))
        context.refined_class = np.full(len(context.positions), 3, np.uint8)
        context.refined_zone = np.full(len(context.positions), 3, np.uint8)
        context.fused_steel_score = np.ones(len(context.positions), np.float32)
        report = segment_internal_rebar(context, workers=1)
        np.testing.assert_array_equal(context.internal_type[:len(steel)], 0)
        np.testing.assert_array_equal(context.internal_type[len(steel):], 5)
        np.testing.assert_array_equal(context.internal_instance, 0)
        np.testing.assert_array_equal(context.refined_class, 3)
        self.assertEqual(report['denoising']['exteriorRemovedPointCount'], len(floating))
        self.assertEqual(report['denoising']['interiorRemovedPointCount'], 0)

    def test_short_lower_rod_above_height_peak_window_is_still_discovered(self):
        from algorithms.internal_rebar import _horizontal_models
        points, _, axes = cylinder((-.025, 0, .033), (.025, 0, .033))
        bands = [{'low':.014, 'high':.026, 'height':.02},
                 {'low':.074, 'high':.086, 'height':.08}]
        models = _horizontal_models(points, axes, np.ones(len(points)), bands,
                                    InternalRebarParameters(), 1)
        self.assertEqual(len({m['group'] for m in models}), 1)
        self.assertTrue(all(m['type']==1 for m in models))

    def test_close_parallel_rods_do_not_merge_into_one_wide_cylinder(self):
        from algorithms.internal_rebar import _horizontal_models
        samples = [cylinder((-.12, y, .02), (.12, y, .02), radius=.004)
                   for y in (0, .012)]
        points = np.vstack([s[0] for s in samples])
        directions = np.vstack([s[2] for s in samples])
        models = _horizontal_models(points, directions, np.ones(len(points)),
            [{'low': .01, 'high': .03, 'height': .02}], InternalRebarParameters(), 1)
        self.assertEqual(len({m['group'] for m in models}), 2)
        self.assertTrue(all(abs(m['radius']-.004)<.001 for m in models))

    def test_short_lower_rod_creates_its_own_instance(self):
        samples = [cylinder((-.025, -.04, .02), (.025, -.04, .02)),
                   cylinder((-.12, .04, .02), (.12, .04, .02)),
                   cylinder((-.12, .04, .08), (.12, .04, .08))]
        points, normals, axes = [np.vstack([s[i] for s in samples]) for i in range(3)]
        context = PointCloudContext.build(points)
        context.normals = normals
        context.refined_class = np.full(len(points), 3, np.uint8)
        context.refined_zone = np.ones(len(points), np.uint8)
        context.classification_cache = {
            'grid': SimpleNamespace(points=points, origin=np.zeros(3),
                source_to_cell=np.arange(len(points)), tree=cKDTree(points)),
            'residual_ids': np.arange(len(points)),
            'features': {'axis': axes, 'linearity': np.full(len(points), .95)}}
        context.region_cache = {'frame_axes': np.eye(2)}
        segment_internal_rebar(context, workers=1)
        short = slice(0, len(samples[0][0]))
        np.testing.assert_array_equal(context.internal_type[short], 1)
        ids = np.unique(context.internal_instance[short])
        self.assertEqual(len(ids), 1)
        self.assertGreater(ids[0], 0)
        self.assertNotEqual(ids[0], context.internal_instance[len(samples[0][0])])

    def test_height_bands_keep_both_narrow_boundary_populations(self):
        points = np.column_stack((
            np.tile(np.linspace(-.10, .10, 60), 2),
            np.zeros(120),
            np.repeat([.02, .08], 60),
        ))
        directions = np.broadcast_to([1., 0., 0.], points.shape)
        bands, _ = _height_bands(points, directions, np.ones(120), InternalRebarParameters())
        self.assertEqual(len(bands), 2)
        np.testing.assert_allclose([band["height"] for band in bands], [.02, .08], atol=.002)

    def test_finite_cylinder_assignment_is_sign_and_worker_stable(self):
        models = [model((-.10, 0, 0), (.10, 0, 0)),
                  model((0, -.10, 0), (0, .10, 0), kind=3)]
        points = np.array([
            [-.06, .005, 0], [.06, -.005, 0],
            [.005, -.06, 0], [-.005, .06, 0],
            [.104, .005, 0], [.106, .005, 0], [0, 0, .005],
        ])
        normals = np.array([[0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0],
                            [0, 1, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
        one, confidence, _ = assign_cylinders(points, normals, models, workers=1)
        many, reversed_confidence, _ = assign_cylinders(
            points, -normals, models, workers=min(4, available_workers()))
        np.testing.assert_array_equal(one, [1, 1, 2, 2, 1, 0, 1])
        np.testing.assert_array_equal(many, one)
        np.testing.assert_allclose(reversed_confidence, confidence)
        self.assertTrue(np.all(confidence[:4] > 0))
        self.assertEqual(confidence[5], 0)

    def test_model_candidate_search_cannot_starve_valid_surface(self):
        # Several duplicate/phantom axes must not consume every sampled-neighbor
        # candidate before a geometrically exact finite cylinder is considered.
        distractors = [model((-.10, 0, 0), (.10, 0, 0), radius=.009) for _ in range(4)]
        exact = model((-.10, .009, 0), (.10, .009, 0), radius=.009)
        labels, confidence, _ = assign_cylinders(
            np.array([[0., 0., 0.]]), np.array([[0., -1., 0.]]), distractors + [exact])
        np.testing.assert_array_equal(labels, [5])
        self.assertGreater(confidence[0], 0)

    def test_residual_denoising_cannot_remove_confirmed_instances(self):
        context, member_size = synthetic_context()
        # A reviewer returning out-of-scope decisions cannot erase fitted models.
        context.fused_steel_score = np.ones(len(context.positions), np.float32)
        def remove_assigned(points, **kwargs):
            removed = np.zeros(len(points), bool)
            removed[:member_size] = True
            removed[member_size:member_size+5] = True
            return removed, {'removedPointCount': int(removed.sum())}
        with mock.patch('algorithms.multiview_floating_noise.multiview_noise_mask', side_effect=remove_assigned):
            report = segment_internal_rebar(context, workers=1)
        self.assertFalse(np.any(context.internal_type[:member_size+5] == 5))
        self.assertTrue(np.all(context.internal_instance[:member_size+5] > 0))
        segments = {s['id']: s for s in report['segments']}
        for segment in segments.values():
            self.assertGreater(segment['pointCount'], 0)
            self.assertEqual(segment['pointCount'], np.count_nonzero(context.internal_segment == segment['id']))
            self.assertEqual(segment['highConfidencePointCount'], segment['pointCount'])
        for instance in report['instances']:
            self.assertGreater(instance['pointCount'], 0)
            self.assertEqual(instance['pointCount'], np.count_nonzero(context.internal_instance == instance['id']))
            self.assertTrue(all(s in segments and segments[s]['instanceId'] == instance['id'] for s in instance['segmentIds']))
        self.assertEqual(set(segments), {s for i in report['instances'] for s in i['segmentIds']})

    def test_cloth_boundary_removes_assigned_points_and_recounts_instances(self):
        context, member_size = synthetic_context()
        context.fused_steel_score = np.ones(len(context.positions), np.float32)
        context.shared_floating_noise = np.zeros(len(context.positions), np.uint8)
        context.shared_floating_noise[:member_size+5] = 1
        with mock.patch('algorithms.multiview_floating_noise.multiview_noise_mask',
                        side_effect=lambda points, **kw: (np.zeros(len(points), bool), {})):
            report = segment_internal_rebar(context, workers=1)
        np.testing.assert_array_equal(context.internal_type[:member_size+5], 5)
        for name in ('internal_instance', 'internal_segment', 'internal_confidence'):
            self.assertFalse(getattr(context, name)[:member_size+5].any())
        for segment in report['segments']:
            self.assertEqual(segment['pointCount'], np.count_nonzero(context.internal_segment == segment['id']))
            self.assertGreater(segment['pointCount'], 0)
        for instance in report['instances']:
            self.assertEqual(instance['pointCount'], np.count_nonzero(context.internal_instance == instance['id']))
            self.assertGreater(instance['pointCount'], 0)

    def test_discovers_two_layers_parallel_rods_and_individual_webs(self):
        first, member_size = synthetic_context()
        second, _ = synthetic_context(reverse_normals=True)
        original_positions = first.positions.copy()
        original_class = first.refined_class.copy()
        original_zone = first.refined_zone.copy()
        first_report = segment_internal_rebar(first, workers=1)
        second_report = segment_internal_rebar(second, workers=min(4, available_workers()))

        self.assertEqual(first_report["counts"], {"lower": 2 * member_size,
                                                   "upper": 2 * member_size,
                                                   "web": 2 * member_size,
                                                   "unassigned": 0, "noise": 1})
        self.assertEqual(first_report["instanceCount"], 6)
        self.assertEqual([item["type"] for item in first_report["instances"]], [1, 1, 2, 2, 3, 3])
        cluster_ids = []
        for index, expected_type in enumerate((1, 1, 2, 2, 3, 3)):
            rows = slice(index * member_size, (index + 1) * member_size)
            np.testing.assert_array_equal(first.internal_type[rows], expected_type)
            ids = np.unique(first.internal_instance[rows])
            self.assertEqual(len(ids), 1)
            self.assertNotEqual(ids[0], 0)
            cluster_ids.append(int(ids[0]))
        self.assertEqual(len(set(cluster_ids)), 6)
        self.assertEqual(first.internal_type[-2], 5)
        self.assertEqual(first.internal_instance[-2], 0)
        self.assertEqual(first.internal_type[-1], 0)
        self.assertEqual(first.internal_instance[-1], 0)
        self.assertTrue(np.all(first.internal_segment[first.internal_instance > 0] > 0))
        self.assertTrue(np.all(first.internal_segment[first.internal_instance == 0] == 0))
        web_instances = [item for item in first_report["instances"] if item["type"] == 3]
        self.assertEqual(len(web_instances), 2)
        self.assertTrue(all(len(item["segmentIds"]) == 1 for item in web_instances))
        np.testing.assert_array_equal(first.internal_type, second.internal_type)
        np.testing.assert_array_equal(first.internal_instance, second.internal_instance)
        np.testing.assert_allclose(first.internal_confidence, second.internal_confidence)
        np.testing.assert_array_equal(first.positions, original_positions)
        np.testing.assert_array_equal(first.refined_class, original_class)
        np.testing.assert_array_equal(first.refined_zone, original_zone)
        self.assertFalse(first.positions.flags.writeable)
        self.assertEqual(second_report["instanceCount"], first_report["instanceCount"])

    def test_empty_strict_scope_is_safe_without_feature_or_frame_cache(self):
        context = PointCloudContext.build(np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]))
        context.refined_class = np.array([1, 2, 3], np.uint8)
        context.refined_zone = np.array([1, 1, 2], np.uint8)
        before_class, before_zone = context.refined_class.copy(), context.refined_zone.copy()
        report = segment_internal_rebar(context, workers=1)
        self.assertEqual(report["pointCount"], 0)
        self.assertEqual(report["instanceCount"], 0)
        np.testing.assert_array_equal(context.internal_type, 0)
        np.testing.assert_array_equal(context.internal_instance, 0)
        np.testing.assert_array_equal(context.refined_class, before_class)
        np.testing.assert_array_equal(context.refined_zone, before_zone)

    def test_step6_npy_las_preview_and_subset_persist_same_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.las"
            las = laspy.create(point_format=3, file_version="1.2")
            las.x = np.arange(10) * .01
            las.y = np.arange(10) * .02
            las.z = np.arange(10) * .03
            las.intensity = np.arange(10, dtype=np.uint16)
            las.write(source)
            source_bytes = source.read_bytes()
            expected_type = np.array([0, 1, 2, 3, 5, 1, 0, 3, 4, 2], np.uint8)
            expected_instance = np.array([0, 11, 12, 13, 0, 11, 0, 13, 0, 12], np.uint32)
            expected_confidence = np.array([0, .9, .8, .7, 0, .6, 0, .5, 0, .4], np.float32)

            def estimate(context, k, workers, output, progress):
                output["normals"][:] = [0, 0, 1]
                output["normal_valid"][:] = 1
                output["curvature"][:] = .1
                output["neighbor_radius"][:] = .02
                context.normals, context.normal_valid = output["normals"], output["normal_valid"]
                context.curvature, context.neighbor_radius = output["curvature"], output["neighbor_radius"]
                return {"effectiveK": k, "treeBuildsDuringNormals": 0}

            def classify(context, workers, output, progress):
                output["geometry_class"][:] = 3
                output["geometry_support"][:] = .7
                output["geometry_recovered"][:] = 0
                context.geometry_class, context.geometry_support = output["geometry_class"], output["geometry_support"]
                context.geometry_recovered = output["geometry_recovered"]
                grid = SimpleNamespace(points=np.asarray(context.positions), projectors=np.zeros((10, 6)),
                                       source_to_cell=np.arange(10))
                context.classification_cache = {"grid": grid, "residual_ids": np.arange(10),
                    "patch_ids": np.full(10, -1), "cell_labels": np.full(10, 3),
                    "recovered_cells": np.zeros(10), "strong_bars": np.arange(10),
                    "features": {"axis": np.zeros((10, 3))}}
                return {"counts": {"table": 0, "fixture": 0, "rebar": 10}}

            def prepare(
                context,
                *,
                output=None,
                progress=None,
                fixed_table=None,
                fixed_table_mask=None,
            ):
                self.assertIsNone(fixed_table)
                self.assertIsNone(fixed_table_mask)
                output["shared_table_mask"][:] = 0
                output["partition_zone"][:] = 1
                context.shared_table_mask = output["shared_table_mask"]
                context.partition_zone = output["partition_zone"]
                context.region_cache = {"frame_axes": np.eye(2)}
                context.scene_cache = {"projection": {}, "region_owned": np.ones(10, bool),
                                       "regions": {"counts": {"interior": 10}}, "report": {}}
                return {"tableRemoval": {"removedPoints": 0}, "partition": {"counts": {"interior": 10}}}

            def projection(positions, normals, valid, workers, output, progress,
                           prepared=None, region_owned=None):
                output["projection_class"][:] = 3
                output["projection_layer"][:] = 1
                return ({"counts": {"table": 0, "fixture": 0, "rebar": 10}, "images": {}},
                        {"dummy": np.arange(1)}, None)

            def fusion(context, workers, output, progress):
                output["fused_class"][:] = 3
                output["fused_recovered"][:] = 0
                output["fused_reason"][:] = 1
                output["fused_steel_score"][:] = 1
                output["fused_steel_evidence"][:] = 3
                context.fused_class, context.fused_recovered = output["fused_class"], output["fused_recovered"]
                context.fused_reason = output["fused_reason"]
                context.fused_steel_score = output["fused_steel_score"]
                context.fused_steel_evidence = output["fused_steel_evidence"]
                context.fusion_cache = {"dummy": np.arange(1)}
                return {"counts": {"table": 0, "fixture": 0, "rebar": 10}}

            def region_map(classes, zones, output=None):
                output[:] = 1
                return output

            def region_report(context, regions):
                return {"counts": {"interior": 10}, "frame": {"detected": True, "innerDetected": True}}

            def internal(context, workers, output, progress):
                output["internal_type"][:] = expected_type
                output["internal_instance"][:] = expected_instance
                output["internal_segment"][:] = expected_instance
                output["internal_confidence"][:] = expected_confidence
                for name, values in output.items():
                    setattr(context, name, values)
                return {"pointCount": 8, "counts": {"lower": 2, "upper": 2, "web": 2, "unassigned": 1, "noise": 1},
                        "instances": [], "segments": [], "instanceCount": 0, "segmentCount": 0}

            patches = {
                "pointcloud_step_pipeline.estimate_normals": estimate,
                "pointcloud_step_pipeline.prepare_scene": prepare,
                "pointcloud_step_pipeline.classify_geometry": classify,
                "pointcloud_step_pipeline.classify_projection": projection,
                "pointcloud_step_pipeline.fuse_classifications": fusion,
                "pointcloud_step_pipeline.regions_from_partition": region_map,
                "pointcloud_step_pipeline.partition_region_report": region_report,
                "pointcloud_step_pipeline.segment_internal_rebar": internal,
                "pointcloud_step_pipeline.write_projection_artifacts": lambda *args: None,
            }
            with mock.patch("pointcloud_step_pipeline.write_projection_artifacts", patches.pop("pointcloud_step_pipeline.write_projection_artifacts")), mock.patch.multiple("algorithms.pointcloud_segmentation", **{name.rsplit(".", 1)[-1]: value for name, value in patches.items()}):
                run = run_from_source(source, root / "out", k=8, workers=1, preview_limit=7, through_step=6)

            self.assertEqual(source.read_bytes(), source_bytes)
            saved = laspy.read(run.directory / "pointcloud-with-classes.las")
            ids = np.linspace(0, 9, 7, dtype=np.int64)
            for name, expected, preview_name, dtype in (
                ("internal_type", expected_type, "internal_types", "u1"),
                ("internal_instance", expected_instance, "internal_instances", "<u4"),
                ("internal_segment", expected_instance, "internal_segments", "<u4"),
                ("internal_confidence", expected_confidence, "internal_confidence", "<f4"),
            ):
                np.testing.assert_array_equal(np.load(run.directory / f"{name}.npy"), expected)
                np.testing.assert_array_equal(saved[name], expected)
                np.testing.assert_array_equal(np.fromfile(run.directory / "preview" / f"{preview_name}.bin", dtype=dtype), expected[ids])
            subset = laspy.read(run.directory / "internal-steel.las")
            selected = np.flatnonzero((expected_type > 0) & (expected_type < 5))
            np.testing.assert_array_equal(subset.source_record_index, selected)
            np.testing.assert_array_equal(subset.internal_type, expected_type[selected])
            noise = laspy.read(run.directory / "noise-only.las")
            np.testing.assert_array_equal(noise.source_record_index, [4])
            steel = laspy.read(run.directory / "steel-only.las")
            self.assertNotIn(4, steel.source_record_index)
            manifest = json.loads((run.directory / "manifest.json").read_text())
            self.assertEqual(manifest["steps"][-1]["id"], "05-internal-rebar")
            self.assertEqual(manifest["files"]["internalSteelLasUrl"].split("/")[-1], "internal-steel.las")
            self.assertEqual(json.loads((run.directory / "internal-instances.json").read_text()), manifest["internalRebar"])


if __name__ == "__main__":
    unittest.main()
