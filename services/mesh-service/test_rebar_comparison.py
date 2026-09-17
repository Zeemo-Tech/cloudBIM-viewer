import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from pydantic import ValidationError

import laspy
import numpy as np
import trimesh

from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart
from analysis_c2m.core import C2MContractError
from rebar_comparison import (
    _validated_directory, compute_instance_comparison, refresh_rebar_comparison,
    statistics_for_finite,
)
from rebar_deviation import constrained_nearest, local_tangents
from scipy.spatial import cKDTree
from main import C2MParams, C2MRecolorRequest, C2MRequest, _c2m_compute_quick, c2m_recolor


IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def component(gid, x):
    mesh = trimesh.creation.box(extents=[.02, .02, .02]); mesh.apply_translation([x, 0, 0])
    return Component(gid, [MeshPart(gid, gid.lower(), mesh, np.eye(4), f"source-{gid}")])


class RebarComparisonTests(unittest.TestCase):
    def test_split_inverted_mesh_repairs_normals_but_single_scan_point_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = trimesh.creation.cylinder(radius=.01, height=.2, sections=32).subdivide()
            mesh.invert()
            build_artifact(ComponentMeshStream([Component("A", [MeshPart("part", "part", mesh, np.eye(4), "source")])], {}),
                           root / "analysis", {"id": "test"}, face_cap=50)
            mapping = {"schema": "rebar-instance-map-v1", "inventory": {
                "bars": [{"designBarId": "A", "ifcGlobalId": "A"}],
                "units": [{"designBarId": "A", "designUnitId": "u", "startM": [0, 0, -.1], "endM": [0, 0, .1]}]},
                "instances": [{"id": 1, "designBarId": "A", "designUnitId": "u", "reviewStatus": "matched"}]}
            (root / "map.json").write_text(json.dumps(mapping))
            for x in [-.015, .005, .015]:
                las = laspy.create(point_format=3, file_version="1.2")
                las.header.scales = np.full(3, 1e-6)
                las.add_extra_dim(laspy.ExtraBytesParams(name="cloudbim_instance_id", type=np.uint32))
                las.x, las.y, las.z = [x], [0.], [0.]
                las["cloudbim_instance_id"] = [1]; las.write(root / "scan.las")
                result = compute_instance_comparison(str(root / "scan.las"), str(root / "analysis"), str(root / "map.json"),
                    IDENTITY, voxel_size=.001, downsample_enabled=False, max_histogram_distance=.1,
                    histogram_bins=20, tolerance=.005, normal_constraint_enabled=True, normal_fallback_mode="unknown")
                xyz = np.asarray(result["mesh"].vertices)
                at = np.all(np.isclose(xyz, [.01, 0, 0], atol=1e-8), axis=1)
                self.assertTrue(at.any())
                # A lone point has no reliable observed axis/side in v3.
                self.assertTrue(np.isnan(result["distances"][at]).all())
                normals = np.asarray(result["mesh"].vertex_normals)
                self.assertTrue((normals[at, 0] > .99).all())
                audit = result["rebarComparison"]["bars"][0]["solid"]
                self.assertEqual(audit["closedPartCount"], 1)
                self.assertEqual(audit["invalidPartCount"], 0)
                self.assertEqual(audit["flippedFaceCount"], len(mesh.faces))

    def test_enabled_normal_mode_requires_instance_comparison_inputs(self):
        with self.assertRaises(ValidationError):
            C2MRequest(scan_path="scan.las", mesh_path="mesh.ply", alignment_matrix=IDENTITY,
                       params=C2MParams(normal_constraint_enabled=True))

    def test_normal_constraint_removes_axial_motion_and_preserves_sign(self):
        vertices = np.array([[0., 0., 0.]])
        scan = np.array([[.01, 0., 0.], [0., .02, 0.]])
        tangent = np.array([[1., 0., 0.]])
        normal = np.array([[0., 1., 0.]])
        values, indices, axial, accepted = constrained_nearest(
            cKDTree(scan), scan, vertices, tangent, normal, k=2,
            max_angle_deg=30, half_space_only=False, fallback_mode="unknown")
        self.assertTrue(accepted[0]); self.assertEqual(indices[0], 1)
        self.assertAlmostEqual(values[0], .02, places=8)
        self.assertAlmostEqual(axial[0], 0., places=8)
        values, _, _, accepted = constrained_nearest(
            cKDTree(scan[:1]), scan[:1], vertices, tangent, normal, k=1,
            max_angle_deg=30, half_space_only=False, fallback_mode="unknown")
        self.assertFalse(accepted[0]); self.assertTrue(np.isnan(values[0]))
        negative, _, _, _ = constrained_nearest(
            cKDTree(np.array([[0., -.02, 0.]])), np.array([[0., -.02, 0.]]), vertices, tangent, normal,
            k=64, max_angle_deg=30, half_space_only=False, fallback_mode="unknown")
        self.assertAlmostEqual(negative[0], -.02, places=8)

    def test_curved_topology_uses_the_local_segment_tangent(self):
        vertices = np.array([[1., .5, 0.]])
        tangents = local_tangents(vertices, [(np.array([0., 0., 0.]), np.array([1., 0., 0.])),
                                              (np.array([1., 0., 0.]), np.array([1., 1., 0.]))])
        np.testing.assert_allclose(tangents, [[0., 1., 0.]])
        values, _, _, accepted = constrained_nearest(
            cKDTree(np.array([[1.02, .5, 0.]])), np.array([[1.02, .5, 0.]]), vertices,
            tangents, np.array([[1., 0., 0.]]), k=1, max_angle_deg=30,
            half_space_only=False, fallback_mode="unknown")
        self.assertTrue(accepted[0]); self.assertAlmostEqual(values[0], .02, places=8)

    def test_normal_constraint_missing_topology_is_strict_unknown(self):
        values, indices, axial, accepted = constrained_nearest(
            cKDTree(np.array([[0., .01, 0.]])), np.array([[0., .01, 0.]]),
            np.array([[0., 0., 0.]]), None, np.array([[0., 1., 0.]]), k=1,
            max_angle_deg=30, half_space_only=False, fallback_mode="unknown")
        self.assertTrue(np.isnan(values[0])); self.assertEqual(indices[0], -1)
        self.assertTrue(np.isnan(axial[0])); self.assertFalse(accepted[0])
    def test_matched_instances_must_follow_unique_design_units(self):
        base = {
            "inventory": {
                "bars": [
                    {"designBarId": "A", "ifcGlobalId": "A", "coverage": "complete"},
                    {"designBarId": "B", "ifcGlobalId": "B", "coverage": "complete"},
                ],
                "units": [
                    {"designBarId": "A", "designUnitId": "u-a"},
                    {"designBarId": "B", "designUnitId": "u-b"},
                ],
            },
        }
        mismatched = {**base, "instances": [
            {"id": 1, "designBarId": "A", "designUnitId": "u-b", "reviewStatus": "matched"},
        ]}
        duplicate = {**base, "instances": [
            {"id": 1, "designBarId": "A", "designUnitId": "u-a", "reviewStatus": "matched"},
            {"id": 2, "designBarId": "A", "designUnitId": "u-a", "reviewStatus": "matched"},
        ]}
        for value in (mismatched, duplicate):
            with self.assertRaisesRegex(C2MContractError, "topology"):
                _validated_directory(value)

    def _fixture(self, root):
        components = [component("A", 0), component("C", .4), component("D", .8), component("E", 1.0), component("FIXTURE", 1.2)]
        build_artifact(ComponentMeshStream(components, {}), root / "analysis", {"id": "test"}, face_cap=100)
        a_vertices = components[0].parts[0].mesh.vertices
        # Instance 1 is measurably displaced. Unassigned instance 99 is exact
        # and would incorrectly win if comparison used the whole cleaned cloud.
        xyz = np.concatenate((a_vertices + [0, 0, .05], a_vertices), axis=0)
        las = laspy.create(point_format=3, file_version="1.2")
        las.add_extra_dim(laspy.ExtraBytesParams(name="cloudbim_instance_id", type=np.uint32))
        las.x, las.y, las.z = xyz.T
        las["cloudbim_instance_id"] = np.r_[np.ones(len(a_vertices), np.uint32), np.full(len(a_vertices), 99, np.uint32)]
        las.write(root / "scan.las")
        bars = [
            {"designBarId": bar, "ifcGlobalId": bar, "name": bar, "unitIds": [f"{bar}/unit0"], "coverage": "complete"}
            for bar in ("A", "C", "D")
        ]
        bars.extend([
            {"designBarId": "E", "ifcGlobalId": "E", "name": "E", "unitIds": [], "coverage": "unresolved"},
            {"designBarId": "NO_TILE", "ifcGlobalId": "NO_TILE", "name": "NO_TILE", "unitIds": ["NO_TILE/unit0"], "coverage": "complete"},
        ])
        units = [{"designBarId": bar, "designUnitId": f"{bar}/unit0", "kind": "straight"} for bar in ("A", "C", "D")]
        units.append({"designBarId": "NO_TILE", "designUnitId": "NO_TILE/unit0", "kind": "straight"})
        mapping = {
            "schema": "rebar-instance-map-v1",
            "instances": [
                {"id": 1, "designBarId": "A", "designUnitId": "A/unit0", "reviewStatus": "matched", "pointCount": len(a_vertices)},
                {"id": 99, "designBarId": "D", "reviewStatus": "pending", "pointCount": len(a_vertices)},
            ],
            "inventory": {"bars": bars, "units": units},
        }
        (root / "instance-map.json").write_text(json.dumps(mapping))

    def test_neighbours_are_instance_constrained_and_missing_review_are_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self._fixture(root)
            result = compute_instance_comparison(
                str(root / "scan.las"), str(root / "analysis"), str(root / "instance-map.json"),
                IDENTITY, voxel_size=.001, downsample_enabled=False,
                max_histogram_distance=.1, histogram_bins=20, tolerance=.01,
            )
            report = result["rebarComparison"]
            rows = {row["designBarId"]: row for row in report["bars"]}
            self.assertEqual(rows["A"]["status"], "matched")
            self.assertGreater(rows["A"]["stats"]["meanAbs"], .03)
            self.assertEqual(rows["C"]["status"], "missing")
            self.assertEqual(rows["D"]["status"], "review")
            self.assertEqual(rows["D"]["instanceIds"], [])
            self.assertEqual(rows["D"]["reviewInstanceIds"], [99])
            self.assertEqual(rows["D"]["pointCount"], 0)
            self.assertEqual(rows["D"]["reviewPointCount"], 8)
            self.assertEqual(rows["E"]["status"], "review")
            self.assertEqual(rows["NO_TILE"]["status"], "review")
            self.assertEqual(rows["NO_TILE"]["vertexCount"], 0)
            distances = result["distances"]
            for bar in ("C", "D", "E"):
                row = rows[bar]; values = distances[row["vertexStart"]:row["vertexStart"] + row["vertexCount"]]
                self.assertTrue(np.isnan(values).all())
                self.assertIsNone(row["stats"])
            self.assertEqual(report["excludedComponentCount"], 1)
            self.assertEqual(report["unassignedPointCount"], 8)
            self.assertEqual(len(result["mesh"].vertices), len(distances))

    def test_recolor_refresh_uses_only_finite_values_per_bar(self):
        distances = np.array([-.02, .005, np.nan, np.nan], dtype=np.float32)
        report = {
            "schema": "rebar-comparison-v1", "bars": [
                {"designBarId": "A", "vertexStart": 0, "vertexCount": 2},
                {"designBarId": "B", "vertexStart": 2, "vertexCount": 2},
            ], "knownVertexCount": 0, "unknownVertexCount": 4,
        }
        refreshed = refresh_rebar_comparison(report, distances, .03, 10, .01)
        global_stats = statistics_for_finite(distances, .03, 10, .01)
        self.assertEqual(refreshed["bars"][0]["stats"], global_stats["stats"])
        self.assertAlmostEqual(refreshed["bars"][0]["stats"]["withinToleranceRatio"], .5)
        self.assertIsNone(refreshed["bars"][1]["stats"])
        self.assertEqual((refreshed["knownVertexCount"], refreshed["unknownVertexCount"]), (2, 2))

    def test_compute_and_recolor_publish_nan_compatible_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self._fixture(root)
            fingerprint_mesh = root / "fingerprint.ply"
            component("FINGERPRINT", 0).parts[0].mesh.export(fingerprint_mesh)
            with patch("main.C2M_OUTPUT_DIR", str(root / "outputs")):
                response = _c2m_compute_quick(C2MRequest(
                    scan_path=str(root / "scan.las"), mesh_path=str(fingerprint_mesh),
                    analysis_mesh_path=str(root / "analysis"), instance_map_path=str(root / "instance-map.json"),
                    alignment_matrix=IDENTITY,
                    params=C2MParams(downsample_enabled=False, voxel_size=.001,
                                     max_colormap_distance=.1, max_histogram_distance=.1,
                                     histogram_bins=20, tolerance_limit=.01),
                ))
                self.assertEqual(response["algorithmVersion"], "c2m-rebar-instance-v3")
                saved = np.fromfile(response["distancesPath"], dtype="<f4")
                self.assertTrue(np.isnan(saved).any())
                recolored = c2m_recolor(C2MRecolorRequest(
                    distances_path=response["distancesPath"], mesh_path=response["coloredPlyPath"],
                    max_colormap_distance=.1, max_histogram_distance=.1,
                    histogram_bins=20, tolerance_limit=.06,
                    rebar_comparison=response["diagnostics"]["rebarComparison"],
                ))
                self.assertTrue(Path(recolored["coloredPlyPath"]).is_file())
                updated = recolored["diagnostics"]["rebarComparison"]
                a = next(row for row in updated["bars"] if row["designBarId"] == "A")
                self.assertEqual(a["stats"]["withinToleranceRatio"], 1.0)


if __name__ == "__main__":
    unittest.main()
