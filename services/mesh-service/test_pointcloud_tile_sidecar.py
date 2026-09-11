from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from pointcloud_tile_sidecar import HEADER, MAGIC, RECORD, TileSidecarError, referenced_pnts, write_complete_rebar_sidecars


def write_pnts(path: Path, points: np.ndarray) -> None:
    feature = json.dumps({"POINTS_LENGTH": len(points), "POSITION": {"byteOffset": 0}}, separators=(",", ":")).encode()
    feature += b" " * ((-len(feature)) % 8)
    binary = np.asarray(points, dtype="<f4").tobytes()
    total = 28 + len(feature) + len(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pnts" + struct.pack("<6I", 1, total, len(feature), len(binary), 0, 0) + feature + binary)


class Context:
    def __init__(self, points):
        self.positions = np.asarray(points, dtype=np.float64)
        self.tree = cKDTree(self.positions)
        self.complete_class = np.array([3, 4, 3], dtype=np.uint8)
        self.complete_instance = np.array([9, 0, 11], dtype=np.uint32)
        self.complete_cluster = np.array([2, 0, 7], dtype=np.uint32)


class PointcloudTileSidecarTests(unittest.TestCase):
    def tile_tree(self, root: Path, points: np.ndarray, uri="a/content.pnts"):
        (root / "tileset.json").write_text(json.dumps({"root": {"content": {"uri": uri}}}))
        write_pnts(root / uri, points)

    def test_final_labels_follow_pnts_order_and_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tiles"; root.mkdir()
            self.tile_tree(root, np.array([[2, 0, 0], [0, 0, 0], [1, 0, 0]], dtype=float))
            run = Path(tmp) / "run"; run.mkdir()
            result = write_complete_rebar_sidecars(root, run, Context([[0, 0, 0], [1, 0, 0], [2, 0, 0]]))
            output = run / "tile-attributes/complete-rebar/a/content.pnts.bin"
            raw = output.read_bytes()
            magic, version, header_bytes, count, stride, property_count, flags, _ = HEADER.unpack(raw[:HEADER.size])
            self.assertEqual((magic, version, header_bytes, count, stride, property_count, flags), (MAGIC, 1, 32, 3, 9, 3, 0))
            records = np.frombuffer(raw, dtype=RECORD, offset=HEADER.size)
            self.assertEqual(records["complete_class"].tolist(), [3, 3, 4])
            self.assertEqual(records["complete_instance"].tolist(), [11, 9, 0])
            self.assertEqual(records["complete_cluster"].tolist(), [7, 2, 0])
            self.assertEqual(result["pointCount"], 3)
            self.assertEqual(result["format"]["recordBytes"], RECORD.itemsize)

    def test_ambiguous_duplicate_source_positions_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tiles"; root.mkdir(); self.tile_tree(root, np.array([[0, 0, 0]], dtype=float))
            with self.assertRaisesRegex(TileSidecarError, "ambiguous"):
                write_complete_rebar_sidecars(root, Path(tmp) / "run", Context([[0, 0, 0], [0, 0, 0], [1, 0, 0]]))

    def test_tileset_content_cannot_escape_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tiles"; root.mkdir()
            (root / "tileset.json").write_text(json.dumps({"root": {"content": {"uri": "../outside.pnts"}}}))
            write_pnts(Path(tmp) / "outside.pnts", np.array([[0, 0, 0]], dtype=float))
            with self.assertRaisesRegex(TileSidecarError, "escapes"):
                referenced_pnts(root)

    def test_non_identity_tile_transform_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tiles"; root.mkdir()
            transform = np.eye(4).reshape(-1, order="F").tolist()
            transform[12] = 10
            (root / "tileset.json").write_text(json.dumps({"root": {
                "transform": transform, "content": {"uri": "content.pnts"},
            }}))
            write_pnts(root / "content.pnts", np.array([[0, 0, 0]], dtype=float))
            with self.assertRaisesRegex(TileSidecarError, "transformed"):
                referenced_pnts(root)


if __name__ == "__main__":
    unittest.main()
