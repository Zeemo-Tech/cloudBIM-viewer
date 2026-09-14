import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

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
from main import C2MParams, C2MRecolorRequest, C2MRequest, _c2m_compute_quick, c2m_recolor


IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def component(gid, x):
    mesh = trimesh.creation.box(extents=[.02, .02, .02]); mesh.apply_translation([x, 0, 0])
    return Component(gid, [MeshPart(gid, gid.lower(), mesh, np.eye(4), f"source-{gid}")])


class RebarComparisonTests(unittest.TestCase):
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
                self.assertEqual(response["algorithmVersion"], "c2m-rebar-instance-v1")
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
