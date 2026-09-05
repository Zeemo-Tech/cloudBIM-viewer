import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import laspy
import numpy as np
import trimesh

from analysis_c2m.core import C2MContractError, build_c2m_artifact
from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart


def mesh_part(gid, part, x):
    mesh = trimesh.creation.box(); mesh.apply_translation([x, 0, 0])
    return MeshPart(f"{gid}:{part}", part, mesh, np.eye(4), "source-position")


class AnalysisC2ME2E(unittest.TestCase):
    def _source_artifact(self, root):
        stream = ComponentMeshStream([Component("A", [mesh_part("A", "a", 0)]), Component("B", [mesh_part("B", "b", 20)])], {})
        return build_artifact(stream, root / "analysis", {"id": "test"}, face_cap=5)
    def _scan(self, path):
        # Dense enough to retain all vertices of A after a tiny voxel downsample.
        vertices = mesh_part("A", "a", 0).mesh.vertices
        las = laspy.create(point_format=3, file_version="1.2"); las.x, las.y, las.z = vertices[:, 0], vertices[:, 1], vertices[:, 2]; las.write(path)
    def test_multitile_component_package_has_nan_and_bound_stats(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self._source_artifact(root); self._scan(root / "scan.las")
            identity_col_major = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
            manifest = build_c2m_artifact(root / "scan.las", root / "analysis", root / "result", identity_col_major, {"voxelSize": 0.001, "downsampleEnabled": False, "coverageMaxDistance": 1.0, "knnK": 1})
            self.assertEqual(manifest["schema"], "analysis-c2m-result-v1"); self.assertEqual(manifest["unknownEncoding"]["value"], "NaN")
            self.assertGreater(len(manifest["tiles"]), 2)
            self.assertEqual({r["ifcGlobalId"] for r in manifest["components"]}, {"A", "B"})
            self.assertGreater(manifest["global"]["unknownCount"], 0)
            self.assertFalse(manifest["algorithm"]["effectiveParameters"]["downsampleEnabled"])
            self.assertEqual(manifest["scan"]["pointsBefore"], manifest["scan"]["pointsAfter"])
            self.assertEqual(next(r for r in manifest["components"] if r["ifcGlobalId"] == "B")["stats"]["knownCount"], 0)
            for tile in manifest["tiles"]:
                payload = (root / "result" / tile["distancePath"]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), tile["sha256"])
                values = np.frombuffer(payload, dtype="<f4"); self.assertEqual(len(values), tile["vertexCount"])
                self.assertEqual(len(tile["positionHash"]), 64)
    def test_scan_hash_mismatch_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self._source_artifact(root); self._scan(root / "scan.las")
            with self.assertRaises(C2MContractError):
                build_c2m_artifact(
                    root / "scan.las", root / "analysis", root / "result",
                    [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1], {},
                    scan_content_hash="0" * 64,
                )
            self.assertFalse((root / "result").exists())
    def test_corrupt_input_fails_without_partial_package(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self._source_artifact(root); self._scan(root / "scan.las")
            tile = next((root / "analysis" / "tiles").glob("*.glb")); tile.write_bytes(b"tampered")
            with self.assertRaises(C2MContractError): build_c2m_artifact(root / "scan.las", root / "analysis", root / "result", [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1], {})
            self.assertFalse((root / "result").exists()); self.assertFalse(list(root.glob(".result.staging-*")))
