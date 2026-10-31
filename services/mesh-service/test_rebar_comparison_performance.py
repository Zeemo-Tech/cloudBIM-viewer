"""Ordering and ownership regressions for concurrent per-bar comparison."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import laspy
import numpy as np
import trimesh

from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart
from rebar_comparison import _instance_point_rows, compute_instance_comparison


def without_timings(value):
    if isinstance(value, dict):
        return {key: without_timings(item) for key, item in value.items()
                if key not in {"timings", "timingSeconds"}}
    if isinstance(value, list):
        return [without_timings(item) for item in value]
    return value


class ComparisonPerformanceTests(unittest.TestCase):
    def test_sparse_instance_grouping_keeps_original_source_order(self):
        owners = np.array([0, 2**32 - 1, 7, 7, 0, 2**32 - 1, 13], dtype=np.uint32)
        rows, counts = _instance_point_rows(owners)
        self.assertEqual(counts, {0: 2, 7: 2, 13: 1, 2**32 - 1: 2})
        for instance_id, indices in rows.items():
            np.testing.assert_array_equal(indices, np.flatnonzero(owners == instance_id))
        self.assertEqual(_instance_point_rows(np.array([], dtype=np.uint32)), ({}, {}))

    def test_concurrency_preserves_geometry_ownership_measurements_and_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Unequal translations and a rigid rotation exercise the complete
            # scan-to-model matrix, including signs and viewing-axis permutation.
            matrix = np.array([[0., -1., 0., 1.7], [0., 0., 1., -.4],
                               [-1., 0., 0., 2.3], [0., 0., 0., 1.]])
            components, bars, units, instances, reports = [], [], [], [], []
            point_groups, owner_groups = [], []
            for index, instance_id in enumerate((7, 2**32 - 1, 13)):
                bar_id, unit_id = f"bar-{index}", f"unit-{index}"
                offset = .12 * index
                mesh = trimesh.creation.cylinder(radius=.01, height=.3, sections=16)
                mesh.apply_translation([offset, 0., 0.])
                mesh.apply_transform(matrix)
                components.append(Component(bar_id, [MeshPart(bar_id, bar_id, mesh, np.eye(4), bar_id)]))
                bars.append({"designBarId": bar_id, "ifcGlobalId": bar_id})
                start, end = [offset, 0., -.15], [offset, 0., .15]
                units.append({"designBarId": bar_id, "designUnitId": unit_id,
                              "startM": start, "endM": end, "diameterM": .02})
                zz, aa = np.meshgrid(np.linspace(-.14, .14, 25), np.linspace(-1.1, 1.1, 13), indexing="ij")
                points = np.column_stack((offset + .011 * np.cos(aa.ravel()),
                                          .011 * np.sin(aa.ravel()), zz.ravel()))
                point_groups.append(points)
                owner_groups.append(np.full(len(points), instance_id, dtype=np.uint32))
                instances.append({"id": instance_id, "designBarId": bar_id, "designUnitId": unit_id,
                                  "reviewStatus": "matched", "pointCount": len(points)})
                reports.append({"id": instance_id, "designBarId": bar_id, "designUnitId": unit_id,
                                "status": "fitted", "pointCount": len(points),
                                "centerlineM": [start, end]})
            # Keep review, missing, and unassigned records distinguishable.
            bars.extend([{"designBarId": "review", "ifcGlobalId": "review", "coverage": "unresolved"},
                         {"designBarId": "missing", "ifcGlobalId": "missing"}])
            for bar_id in ("review", "missing"):
                mesh = trimesh.creation.box(extents=(.01, .01, .01))
                components.append(Component(bar_id, [MeshPart(bar_id, bar_id, mesh, np.eye(4), bar_id)]))
            units.append({"designBarId": "missing", "designUnitId": "missing-unit",
                          "startM": [.5, 0., -.15], "endM": [.5, 0., .15], "diameterM": .02})
            instances.append({"id": 19, "designBarId": "review", "reviewStatus": "review"})
            # A review bar still retains trusted control evidence in inspection;
            # comparison status must not erase it during per-bar preparation.
            units.append({"designBarId": "review", "designUnitId": "review-unit",
                          "startM": [0., 0., -.15], "endM": [0., 0., .15], "diameterM": .02})
            instances.append({"id": 41, "designBarId": "review", "designUnitId": "review-unit",
                              "reviewStatus": "matched", "pointCount": len(point_groups[0])})
            reports.append({"id": 41, "designBarId": "review", "designUnitId": "review-unit",
                            "status": "fitted", "pointCount": len(point_groups[0]),
                            "centerlineM": [[0., 0., -.15], [0., 0., .15]]})
            point_groups.append(point_groups[0].copy())
            owner_groups.append(np.full(len(point_groups[0]), 41, dtype=np.uint32))
            point_groups.append(np.array([[.4, .02, .03], [.5, .06, .04]]))
            owner_groups.append(np.array([19, 0], dtype=np.uint32))
            xyz, owners = np.concatenate(point_groups), np.concatenate(owner_groups)
            order = np.random.default_rng(24).permutation(len(xyz))
            las = laspy.create(point_format=3, file_version="1.2")
            las.header.scales = np.full(3, 1e-6)
            las.add_extra_dim(laspy.ExtraBytesParams(name="cloudbim_instance_id", type=np.uint32))
            las.x, las.y, las.z = xyz[order].T
            las["cloudbim_instance_id"] = owners[order]
            las.write(root / "scan.las")
            build_artifact(ComponentMeshStream(components, {}), root / "analysis", {"id": "test"}, face_cap=30)
            mapping = {"schema": "rebar-instance-map-v1", "inventory": {"bars": bars, "units": units},
                       "instances": instances,
                       "controlNet": {"schema": "rebar-control-net-evidence-v1", "coordinateFrame": "scan",
                                      "algorithmVersion": "test-control-net",
                                      "report": {"instances": reports, "curvedPieces": []}}}
            (root / "map.json").write_text(json.dumps(mapping))
            for same_side, downsample in ((False, False), (True, False), (True, True)):
                with self.subTest(same_side=same_side, downsample=downsample):
                    results = []
                    for worker_count in (1, 4):
                        with patch.dict(os.environ, {"REBAR_COMPARISON_WORKERS": str(worker_count)}):
                            results.append(compute_instance_comparison(
                                str(root / "scan.las"), str(root / "analysis"), str(root / "map.json"),
                                matrix.T.reshape(-1).tolist(), voxel_size=.003, downsample_enabled=downsample,
                                max_histogram_distance=.03, histogram_bins=20, tolerance=.01,
                                normal_constraint_enabled=same_side))
                    serial, threaded = results
                    for attribute in ("vertices", "triangles", "vertex_normals"):
                        np.testing.assert_array_equal(getattr(serial["mesh"], attribute), getattr(threaded["mesh"], attribute))
                    np.testing.assert_array_equal(serial["distances"], threaded["distances"])
                    self.assertEqual(without_timings({key: value for key, value in serial.items() if key not in {"mesh", "distances"}}),
                                     without_timings({key: value for key, value in threaded.items() if key not in {"mesh", "distances"}}))
                    rows = threaded["rebarComparison"]["bars"]
                    self.assertEqual([row["designBarId"] for row in rows], [row["designBarId"] for row in bars])
                    self.assertEqual([row["pointCount"] for row in rows], [325, 325, 325, 0, 0])
                    self.assertEqual(rows[3]["reviewPointCount"], 326)
                    self.assertEqual(rows[4]["status"], "missing")
                    self.assertEqual(threaded["rebarComparison"]["unassignedPointCount"], 327)
                    inspection_bar = threaded["rebarComparison"]["inspection"]["bars"][3]
                    self.assertEqual(inspection_bar["status"], "review")
                    self.assertTrue(inspection_bar["observedSegments"])
                    self.assertTrue(np.isfinite(threaded["distances"]).any())


if __name__ == "__main__":
    unittest.main()
