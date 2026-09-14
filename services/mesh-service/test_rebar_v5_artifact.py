import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rebar_poc import compute_rebar_artifact
from rebar_validation import make_truth_scene
from test_rebar_tiles import _write


def write_ascii_ply(path, points):
    with path.open("w", encoding="ascii") as stream:
        stream.write("ply\nformat ascii 1.0\n")
        stream.write(f"element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
        np.savetxt(stream, points, fmt="%.8f %.8f %.8f")


class V5ArtifactTests(unittest.TestCase):
    def test_detected_bolt_cells_survive_complete_artifact_publication(self):
        from test_rebar_v5_bolts import fixture_plane, shaft, head, features, FIXTURE, P
        from algorithms.rebar_v5.bolts import detect_bolts
        plate, body, cap = fixture_plane(), shaft(), head()
        points = np.vstack((plate, body, cap))
        models, diagnostic = detect_bolts(points, features(len(points), np.arange(len(plate), len(plate)+len(body))), FIXTURE, P)
        self.assertEqual(len(models), 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'; source.mkdir()
            (source/'tileset.json').write_text('{}')
            shown = points.astype('<f4')
            _write(source/'bolt.pnts', {'POINTS_LENGTH': len(shown), 'POSITION': {'byteOffset': 0}}, shown.tobytes())
            cloud = root/'bolt.ply'; write_ascii_ply(cloud, points)
            # Feed the real bolt detector's output through the complete writer.
            # This avoids relying on another detector to discover this fixture.
            with patch('algorithms.rebar_v5.pipeline.detect_bolts', return_value=(models, diagnostic)):
                manifest = compute_rebar_artifact(point_cloud_path=str(cloud), point_cloud_format='ply',
                    source_tileset_path=str(source), output_directory=str(root/'artifact'), artifact_version='bolt-json',
                    algorithm='geometric-v5', input_options={}, parameters={}, storage_root=str(root))
            result = json.loads((root/'artifact'/manifest['resultPath']).read_text())
            bolts = result['analysis']['algorithmDetails']['fixture']['bolts']
            self.assertEqual(len(bolts), 1)
            self.assertTrue(bolts[0]['shaft']['observedCells'])
            self.assertTrue(bolts[0]['head']['occupiedCells'])

    def test_physical_ply_and_pnts_publish_complete_v2_source_artifacts(self):
        truth = make_truth_scene(seed=20260905, top_arcs=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "truth.ply"
            write_ascii_ply(cloud, truth.points)
            source = root / "source"; source.mkdir()
            (source / "tileset.json").write_text("{}", encoding="utf-8")
            shown = truth.points[:256].astype("<f4")
            _write(source / "truth.pnts", {"POINTS_LENGTH": len(shown), "POSITION": {"byteOffset": 0}}, shown.tobytes())
            manifest = compute_rebar_artifact(
                point_cloud_path=str(cloud), point_cloud_format="ply", source_tileset_path=str(source),
                output_directory=str(root / "artifact"), artifact_version="v5-test", algorithm="geometric-v5",
                input_options={"maxInputPoints": 100000}, parameters={"detection_point_limit": 100000}, storage_root=str(root),
            )
            artifact = root / "artifact"
            self.assertEqual(manifest, json.loads((artifact / 'manifest.json').read_text()),
                             'runtime cleanup must not mutate the published manifest response')
            self.assertEqual(manifest["schema"], "rebar-artifact-manifest-v2")
            self.assertEqual(manifest["analysisSchema"], "rebar-analysis-v2")
            self.assertEqual(manifest["featuresPath"], "features/manifest.json")
            self.assertNotIn("intersectionPointCount", manifest["summary"])
            self.assertIn("intersectionCount", manifest["summary"])
            self.assertEqual(manifest["summary"]["rawSource"]["finitePointCount"], len(truth.points))
            features = json.loads((artifact / "features" / "manifest.json").read_text())
            labels = json.loads((artifact / "labels" / "manifest.json").read_text())
            self.assertEqual(features["schema"], "rebar-features-v1")
            self.assertEqual(labels["schema"], "rebar-raw-labels-v2")
            self.assertEqual(features["finitePointCount"], len(truth.points))
            self.assertEqual(labels["finitePointCount"], len(truth.points))
            self.assertIn("class_confidence", labels["attributes"])
            self.assertIn("instance_confidence", labels["attributes"])
            source_rows = []
            for chunk in labels["chunks"]:
                with np.load(artifact / "labels" / chunk["path"]) as payload:
                    source_rows.append(payload["source_index"])
                    self.assertEqual(len(payload["scene_class"]), len(payload["source_index"]))
                    self.assertEqual(len(payload["rebar_flags"]), len(payload["source_index"]))
            # Spatial feature chunks are cell ordered; their union must still
            # cover every raw reader record exactly once.
            np.testing.assert_array_equal(np.sort(np.concatenate(source_rows)), np.arange(len(truth.points), dtype=np.uint64))


if __name__ == "__main__":
    unittest.main()
