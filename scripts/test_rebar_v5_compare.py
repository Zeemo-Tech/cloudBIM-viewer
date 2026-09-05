import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

SPEC = importlib.util.spec_from_file_location("compare", Path(__file__).with_name("rebar-v5-compare.py"))
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def write_artifact(root, groups, *, changed=False):
    (root / "features").mkdir(parents=True); (root / "labels").mkdir()
    feature_chunks, label_chunks = [], []
    for ordinal, rows in enumerate(groups):
        rows = np.asarray(rows, dtype=np.uint64); name = f"{ordinal:06d}.npz"
        xyz = np.array([[0., 0., 0.] if i in (2, 9) else [float(i), .1, .2] for i in rows], np.float64)
        np.savez_compressed(root / "features" / name, source_index=rows, xyz=xyz, axis_linearity=np.full(len(rows), .8, np.float32))
        flags = np.array([2 if i == 9 else 0 for i in rows], np.uint8)
        local = np.flatnonzero(flags & 2).astype(np.uint32)
        offsets = np.array([0, 2], np.uint64) if len(local) else np.array([0], np.uint64)
        ids = np.array([7, 8], np.uint32) if len(local) else np.array([], np.uint32)
        scene = np.array([2] * len(rows), np.uint8)
        if changed and 50 in rows: scene[np.flatnonzero(rows == 50)[0]] = 1
        np.savez_compressed(root / "labels" / name, source_index=rows, scene_class=scene, rebar_class=np.ones(len(rows), np.uint8), rebar_instance=np.full(len(rows), 7, np.uint32), rebar_direction=np.ones(len(rows), np.uint16), rebar_flags=flags, class_confidence=np.full(len(rows), .9, np.float32), instance_confidence=np.full(len(rows), .8, np.float32), candidate_point_indices=local, candidate_offsets=offsets, candidate_instance_ids=ids)
        for folder, sink in (("features", feature_chunks), ("labels", label_chunks)):
            path = root / folder / name
            sink.append({"path": name, "count": len(rows), "firstSourceIndex": int(rows.min()), "lastSourceIndex": int(rows.max()), "sha256": sha(path)})
    attrs = {"source_index": {"dtype": "uint64", "shape": []}}
    (root / "features" / "manifest.json").write_text(json.dumps({"schema":"rebar-features-v1","chunks":feature_chunks,"attributes":attrs}))
    (root / "labels" / "manifest.json").write_text(json.dumps({"schema":"rebar-raw-labels-v2","chunks":label_chunks,"attributes":attrs}))
    result = {"analysis":{"instances":[{"id":7,"centerline":[[0.,0.,0.],[1.,0.,0.]]}],"intersections":[{"id":3,"position":[.1,.2,.3]}],"diagnostics":{"elapsedS":12}}}
    (root / "result.json").write_text(json.dumps(result))
    (root / "manifest.json").write_text(json.dumps({"schema":"rebar-artifact-manifest-v2","algorithm":{"id":"geometric-v5","version":"1"},"analysisSchema":"rebar-analysis-v2","featuresPath":"features/manifest.json","resultPath":"result.json"}))


def write_boundary(root, groups, value=.8):
    chunks = []
    for ordinal, rows in enumerate(groups):
        path = root / 'features' / f'updates-{ordinal}.npz'
        np.savez_compressed(path, fixture_source_index=np.asarray(rows, np.uint64),
                            fixture_axis_linearity=np.full(len(rows), value, np.float32))
        chunks.append({'path': path.name, 'sha256': sha(path)})
    path = root / 'features/manifest.json'
    manifest = json.loads(path.read_text())
    manifest['boundaryUpdates'] = {'encoding': 'sparse-source-index',
                                   'applyOrder': ['table', 'fixture'], 'chunks': chunks}
    path.write_text(json.dumps(manifest))


class CompareTest(unittest.TestCase):
    def test_boundary_updates_are_joined_and_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/"a", Path(tmp)/"b"
            write_artifact(first, [[2, 9, 50]])
            write_artifact(second, [[2, 9, 50]])
            write_boundary(first, [[2, 50]])
            write_boundary(second, [[50], [2]])
            self.assertTrue(compare.compare(first, second)['equal'])
            write_boundary(second, [[50], [2]], value=.2)
            self.assertFalse(compare.compare(first, second)['equal'])

    def test_unsigned_source_indices_preserve_full_contract_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/"a", Path(tmp)/"b"
            write_artifact(first, [[2, 2**63 + 1], [2**64 - 1]])
            write_artifact(second, [[2**64 - 1, 2], [2**63 + 1]])
            self.assertTrue(compare.compare(first, second)["equal"])

    def test_geometry_integer_ids_are_compared_exactly(self):
        self.assertFalse(compare._json_equal({"id": 1000000}, {"id": 1000001}))

    def test_source_index_join_accepts_different_chunking_and_duplicate_xyz(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/"a", Path(tmp)/"b"
            write_artifact(first, [[2, 50], [9]])
            write_artifact(second, [[9, 2], [50]])
            report = compare.compare(first, second)
            self.assertTrue(report["equal"], report)
            self.assertTrue(all(item["ok"] for item in report["chunkChecksums"]))

    def test_value_tamper_and_missing_source_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, changed, missing = Path(tmp)/"a", Path(tmp)/"changed", Path(tmp)/"missing"
            write_artifact(first, [[2, 9, 50]])
            write_artifact(changed, [[2, 9, 50]], changed=True)
            # refresh only the changed label checksum: this must be a value mismatch, not a checksum error.
            manifest = json.loads((changed/"labels/manifest.json").read_text()); manifest["chunks"][0]["sha256"] = sha(changed/"labels/000000.npz"); (changed/"labels/manifest.json").write_text(json.dumps(manifest))
            self.assertFalse(compare.compare(first, changed)["equal"])
            write_artifact(missing, [[2, 9]])
            report = compare.compare(first, missing)
            self.assertFalse(report["equal"])
            self.assertTrue(any(item["reason"] == "missing from second artifact" for item in report["mismatches"]))

    def test_checksum_tamper_is_honest_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/"a", Path(tmp)/"b"
            write_artifact(first, [[2, 9, 50]]); write_artifact(second, [[2, 9, 50]])
            with (second/"features/000000.npz").open("ab") as handle: handle.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                compare.compare(first, second)


if __name__ == "__main__": unittest.main()
