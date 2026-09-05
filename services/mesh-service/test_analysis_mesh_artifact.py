import json
import stat
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart
from analysis_mesh.quality import mesh_quality_metrics


def part(gid, name, offset=0):
    mesh = trimesh.creation.icosphere(subdivisions=1); mesh.apply_translation([offset, 0, 0])
    return MeshPart(name, name, mesh, np.eye(4), f"hash-{name}")


class ArtifactTest(unittest.TestCase):
    def test_manifest_hashes_and_face_cap_keep_components_separate(self):
        tree = {"id": "root", "children": [{"id": "A", "children": []}, {"id": "B", "children": []}]}
        stream = ComponentMeshStream([Component("A", [part("A", "a")]), Component("B", [part("B", "b", 0.01)])], tree)
        with tempfile.TemporaryDirectory() as d:
            destination = Path(d) / "artifact"
            manifest = build_artifact(stream, destination, {"id": "fake", "effectiveParameters": {}}, face_cap=10)
            self.assertTrue((destination / "manifest.json").exists())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o2770)
            self.assertEqual(stat.S_IMODE((destination / "manifest.json").stat().st_mode), 0o660)
            self.assertEqual(manifest["entryPath"], "tileset.json")
            self.assertTrue(np.allclose(manifest["modelFrame"]["normalizationCenter"], [0.005, 0.0, 0.0]))
            self.assertEqual(len(manifest["contentHash"]), 64)
            tiles = json.loads((destination / "tileset.json").read_text())["root"]["children"]
            self.assertGreater(len(tiles), 2)
            components = json.loads((destination / "components.json").read_text())
            self.assertEqual(components["tree"], tree)
            self.assertEqual({tile["ifcGlobalId"] for tile in components["tiles"]}, {"A", "B"})
            self.assertTrue(all(len(tile["positionHash"]) == 64 for tile in components["tiles"]))
            for row in manifest["files"].values(): self.assertEqual(len(row["sha256"]), 64)
            for tile in (destination / "tiles").glob("*.glb"):
                loaded = trimesh.load(tile, force="scene"); self.assertLessEqual(sum(len(g.faces) for g in loaded.geometry.values()), 10)

            metrics = json.loads((destination / "metrics.json").read_text())
            quality = metrics["components"][0]["parts"][0]["analysisQuality"]
            self.assertEqual(quality["degenerateFaceCount"], 0)
            self.assertGreater(quality["triangleQuality"]["p05"], 0.5)
            self.assertLess(quality["edgeLength"]["coefficientOfVariation"], 0.2)

    def test_quality_metrics_measure_anisotropy(self):
        regular = trimesh.creation.icosphere(subdivisions=1)
        stretched = regular.copy()
        stretched.apply_scale([8.0, 1.0, 1.0])
        regular_metrics = mesh_quality_metrics(regular)
        stretched_metrics = mesh_quality_metrics(stretched)
        self.assertGreater(
            stretched_metrics["edgeLength"]["coefficientOfVariation"],
            regular_metrics["edgeLength"]["coefficientOfVariation"],
        )
        self.assertLess(
            stretched_metrics["triangleQuality"]["p05"],
            regular_metrics["triangleQuality"]["p05"],
        )
    def test_failure_leaves_no_partial_artifact(self):
        stream = ComponentMeshStream([], {})
        with tempfile.TemporaryDirectory() as d:
            destination = Path(d) / "artifact"; destination.mkdir()
            with self.assertRaises(FileExistsError): build_artifact(stream, destination, {}, face_cap=1)
            self.assertFalse(list(Path(d).glob(".artifact.staging-*")))

    def test_empty_stream_is_rejected_without_publishing(self):
        with tempfile.TemporaryDirectory() as d:
            destination = Path(d) / "artifact"
            with self.assertRaises(ValueError):
                build_artifact(ComponentMeshStream([], {}), destination, {}, face_cap=1)
            self.assertFalse(destination.exists())
