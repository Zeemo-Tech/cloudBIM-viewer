from __future__ import annotations
import hashlib, json, struct, tempfile, unittest
from pathlib import Path
import numpy as np
from rebar_tiles import PntsError, rewrite_pnts
from algorithms.rebar_base import RebarPointAttributes, RebarAnalysis
from rebar_poc import LoadedPointCloud, compute_rebar_artifact
from unittest import mock

def _pad(raw): return raw + b" " * ((-len(raw)) % 8)
def _write(path, feature, binary, batch=None, batch_binary=b"", legacy_header=False):
    feature_raw = _pad(json.dumps(feature, separators=(",", ":")).encode())
    batch_raw = _pad(json.dumps(batch, separators=(",", ":")).encode()) if batch else b""
    actual = 28+len(feature_raw)+len(binary)+len(batch_raw)+len(batch_binary)
    declared = 28+len(feature_raw)+len(binary) if legacy_header else actual
    body = b"pnts" + struct.pack("<6I", 1, declared, len(feature_raw),len(binary),len(batch_raw),len(batch_binary)) + feature_raw + binary + batch_raw + batch_binary
    path.write_bytes(body)
def _read(path):
    raw=path.read_bytes(); _, length, fj, fb, bj, bb=struct.unpack_from("<6I",raw,4); assert length==len(raw)
    feature=json.loads(raw[28:28+fj].decode().rstrip(" ")); batch=json.loads(raw[28+fj+fb:28+fj+fb+bj].decode().rstrip(" "))
    return raw, feature, batch, raw[28+fj+fb+bj:]
def _attrs(points): return RebarPointAttributes(np.array([1,0],np.uint8),np.array([3,0],np.uint16),np.array([9,0],np.uint32))

class PntsRewriteTests(unittest.TestCase):
    def test_raw_preserves_feature_properties_and_writes_typed_attributes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"raw.pnts"; positions=np.array([[0,0,0],[1,0,0]],"<f4").tobytes(); rgb=b"\x01\x02\x03\x04\x05\x06"; intensity=np.array([4,5],"<u2").tobytes(); classification=b"\x02\x03"
            _write(p,{"POINTS_LENGTH":2,"POSITION":{"byteOffset":0},"RGB":{"byteOffset":24},"INTENSITY":{"byteOffset":30},"CLASSIFICATION":{"byteOffset":34}},positions+rgb+intensity+classification)
            rewrite_pnts(p,_attrs); raw, feature, batch, btb=_read(p)
            self.assertEqual(raw[28+struct.unpack_from("<6I",raw,4)[2]:28+struct.unpack_from("<6I",raw,4)[2]+36], positions+rgb+intensity+classification)
            self.assertEqual(batch["REBAR_CLASS"]["componentType"],"UNSIGNED_BYTE")
            self.assertEqual(batch["REBAR_DIRECTION"]["componentType"],"UNSIGNED_SHORT")
            self.assertEqual(batch["REBAR_INSTANCE"]["componentType"],"UNSIGNED_INT")
            self.assertEqual(batch["REBAR_CLASS"]["byteOffset"] % 1, 0)
            self.assertEqual(batch["REBAR_DIRECTION"]["byteOffset"] % 2, 0)
            self.assertEqual(batch["REBAR_INSTANCE"]["byteOffset"] % 4, 0)
            self.assertEqual(len(btb) % 8, 0)
            self.assertEqual(batch["REBAR_CLASS"]["byteOffset"], 0)
            self.assertEqual(batch["REBAR_DIRECTION"]["byteOffset"], 2)
            self.assertEqual(batch["REBAR_INSTANCE"]["byteOffset"], 8)
            self.assertEqual(np.frombuffer(btb, np.uint8, count=2, offset=0).tolist(), [1,0])
            self.assertEqual(np.frombuffer(btb, "<u2", count=2, offset=2).tolist(), [3,0])
            self.assertEqual(np.frombuffer(btb, "<u4", count=2, offset=8).tolist(), [9,0])
            self.assertEqual(btb[6:8], b"\0\0")

    def test_quantized_positions_are_decoded_for_projection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"quantized.pnts"; q=np.array([[0,0,0],[65535,0,0]],"<u2").tobytes()
            _write(p,{"POINTS_LENGTH":2,"POSITION_QUANTIZED":{"byteOffset":0},"QUANTIZED_VOLUME_SCALE":[1,1,1],"QUANTIZED_VOLUME_OFFSET":[10,20,30]},q)
            seen=[]; rewrite_pnts(p,lambda points: (seen.append(points.copy()) or _attrs(points)))
            np.testing.assert_allclose(seen[0], [[10,20,30],[11,20,30]])

    def test_legacy_header_with_batch_sections_is_accepted_and_normalized(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"legacy.pnts"; positions=np.zeros((2,3),"<f4").tobytes()
            _write(p, {"POINTS_LENGTH":2,"POSITION":{"byteOffset":0}}, positions,
                   {"OLD":{"byteOffset":0,"componentType":"UNSIGNED_BYTE","type":"SCALAR"}}, b"\x07\x08", legacy_header=True)
            self.assertNotEqual(struct.unpack_from("<I",p.read_bytes(),8)[0], len(p.read_bytes()))
            rewrite_pnts(p,_attrs); raw, _, batch, btb=_read(p)
            self.assertEqual(struct.unpack_from("<I",raw,8)[0],len(raw))
            self.assertEqual(batch["OLD"], {"byteOffset":0,"componentType":"UNSIGNED_BYTE","type":"SCALAR"})
            self.assertEqual(btb[:2],b"\x07\x08")
            self.assertEqual(np.frombuffer(btb,"<u2",count=2,offset=batch["REBAR_DIRECTION"]["byteOffset"]).tolist(),[3,0])

    def test_output_sections_use_absolute_eight_byte_alignment(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"alignment.pnts"; _write(p,{"POINTS_LENGTH":2,"POSITION":{"byteOffset":0}},np.zeros((2,3),"<f4").tobytes())
            rewrite_pnts(p,_attrs); raw=p.read_bytes(); _, length, fj, fb, bj, bb=struct.unpack_from("<6I",raw,4)
            feature_start=28; feature_end=feature_start+fj; binary_end=feature_end+fb; batch_end=binary_end+bj; end=batch_end+bb
            self.assertEqual(length,end); self.assertEqual(end,len(raw)); self.assertEqual(end % 8,0)
            self.assertEqual(feature_end % 8,0); self.assertEqual(binary_end % 8,0); self.assertEqual(batch_end % 8,0)

    def test_rtc_center_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"rtc.pnts"; _write(p,{"POINTS_LENGTH":1,"POSITION":{"byteOffset":0},"RTC_CENTER":[1,2,3]},np.zeros((1,3),"<f4").tobytes())
            before=p.read_bytes()
            with self.assertRaisesRegex(PntsError,"RTC_CENTER PNTS is unsupported"): rewrite_pnts(p,_attrs)
            self.assertEqual(before,p.read_bytes())

    def test_unknown_or_draco_payload_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"bad.pnts"; _write(p,{"POINTS_LENGTH":1,"extensions":{"3DTILES_draco_point_compression":{} }},b"")
            before=p.read_bytes()
            with self.assertRaisesRegex(PntsError,"Draco PNTS is unsupported"): rewrite_pnts(p,_attrs)
            self.assertEqual(p.read_bytes(),before)

    def test_non_draco_feature_extension_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"extended.pnts"; positions=np.zeros((2,3),"<f4").tobytes()
            extension={"VENDOR_metadata":{"revision":1}}
            _write(p,{"POINTS_LENGTH":2,"POSITION":{"byteOffset":0},"extensions":extension},positions)
            rewrite_pnts(p,_attrs); _, feature, _, _=_read(p)
            self.assertEqual(feature["extensions"],extension)

    def test_bad_tile_never_publishes_a_ready_staging_result(self):
        class Fake:
            descriptor={"id":"fake","version":"1","capabilities":{}}
            def normalize_parameters(self, raw): return {}
            def analyze(self, sample, parameters): return RebarAnalysis({"algorithmDetails": {}})
            def project_points(self, points, analysis): return _attrs(np.zeros((2,3)))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"source"; source.mkdir(); (source/"tileset.json").write_text("{}")
            _write(source/"bad.pnts", {"POINTS_LENGTH": 1}, b"")
            output=root/"artifact"
            loaded=LoadedPointCloud(np.zeros((2,3)),np.array([0,1]),{})
            with mock.patch("algorithms.REBAR_ALGORITHM_REGISTRY.get", return_value=Fake()), mock.patch("rebar_poc.load_point_cloud", return_value=loaded):
                with self.assertRaises(PntsError): compute_rebar_artifact(point_cloud_path=str(root/"x.ply"),point_cloud_format="ply",source_tileset_path=str(source),output_directory=str(output),artifact_version="1",algorithm="fake",input_options={},parameters={},storage_root=str(root))
            self.assertFalse(output.exists())

    def test_compute_manifest_is_camel_case_and_paths_are_relative(self):
        class Fake:
            descriptor={"id":"fake","version":"1","capabilities":{"class":True,"direction":True,"instance":True,"confidence":False}}
            def normalize_parameters(self, raw): return {"normal": True}
            def analyze(self, sample, parameters): return RebarAnalysis({"algorithmDetails":{"diagnostics":{},"directions":[],"instances":[]}})
            def project_points(self, points, analysis):
                return RebarPointAttributes(np.array([1,0],np.uint8),np.array([1,0],np.uint16),np.array([1,0],np.uint32))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"source"; source.mkdir(); (source/"tileset.json").write_text("{}")
            _write(source/"ok.pnts", {"POINTS_LENGTH":2,"POSITION":{"byteOffset":0}}, np.zeros((2,3),"<f4").tobytes())
            loaded=LoadedPointCloud(np.zeros((2,3)),np.array([0,1]),{})
            with mock.patch("algorithms.REBAR_ALGORITHM_REGISTRY.get", return_value=Fake()), mock.patch("rebar_poc.load_point_cloud", return_value=loaded):
                manifest=compute_rebar_artifact(point_cloud_path=str(root/"x.ply"),point_cloud_format="ply",source_tileset_path=str(source),output_directory=str(root/"artifact"),artifact_version="v1",algorithm="fake",input_options={},parameters={},storage_root=str(root))
            for key in ("artifactVersion","analysisSchema","inputOptions","effectiveParameters","resultPath","tilesetPath","manifestPath","contentHash","byteSize"):
                self.assertIn(key,manifest)
            self.assertEqual(manifest["algorithm"], {"id":"fake","version":"1"})
            self.assertEqual(manifest["summary"]["rebarPointCount"], 1)
            self.assertEqual(manifest["summary"]["directionCount"], 1)
            self.assertEqual(manifest["summary"]["instanceCount"], 1)
            self.assertEqual(manifest["inputOptions"], {"maxInputPoints": 200000, "voxelSize": None})
            self.assertNotIn("analysis", manifest)
            result = json.loads((root/"artifact"/"result.json").read_text())
            self.assertEqual(result["schema"], "rebar-analysis-v1")
            self.assertIn("analysis", result)
            self.assertEqual((root/"artifact").stat().st_mode & 0o7777, 0o2770)
            self.assertEqual((root/"artifact"/"tiles").stat().st_mode & 0o7777, 0o2770)
            self.assertEqual((root/"artifact"/"tiles"/"ok.pnts").stat().st_mode & 0o777, 0o660)
            self.assertTrue(all(not Path(manifest[k]).is_absolute() for k in ("resultPath","tilesetPath","manifestPath")))
            self.assertEqual(manifest["byteSize"], sum(p.stat().st_size for p in (root/"artifact").rglob("*") if p.is_file()))
            hasher = hashlib.sha256(); artifact_root = root / "artifact"
            for file in sorted((p for p in artifact_root.rglob("*") if p.is_file() and p.relative_to(artifact_root).as_posix() != "manifest.json"), key=lambda p: p.relative_to(artifact_root).as_posix()):
                hasher.update(file.relative_to(artifact_root).as_posix().encode("utf-8")); hasher.update(b"\0"); hasher.update(file.read_bytes())
            self.assertEqual(manifest["contentHash"], hasher.hexdigest())

    def test_source_tree_symlink_is_rejected_before_copy(self):
        class Fake:
            descriptor={"id":"fake","version":"1","capabilities":{}}
            def normalize_parameters(self, raw): return {}
            def analyze(self, sample, parameters): return RebarAnalysis({"algorithmDetails":{}})
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"source"; source.mkdir(); (source/"tileset.json").write_text("{}")
            (source/"outside-link").symlink_to(root / "elsewhere")
            loaded=LoadedPointCloud(np.zeros((2,3)),np.array([0,1]),{})
            with mock.patch("algorithms.REBAR_ALGORITHM_REGISTRY.get", return_value=Fake()), mock.patch("rebar_poc.load_point_cloud", return_value=loaded):
                with self.assertRaisesRegex(Exception, "must not contain symlinks"):
                    compute_rebar_artifact(point_cloud_path=str(root/"x.ply"),point_cloud_format="ply",source_tileset_path=str(source),output_directory=str(root/"artifact"),artifact_version="1",algorithm="fake",input_options={},parameters={},storage_root=str(root))

    def test_tileset_transform_is_rejected_before_copy(self):
        class Fake:
            descriptor={"id":"fake","version":"1","capabilities":{}}
            def normalize_parameters(self, raw): return {}
            def analyze(self, sample, parameters): return RebarAnalysis({"algorithmDetails":{}})
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/"source"; source.mkdir(); (source/"tileset.json").write_text('{"root":{"transform":[1]}}')
            loaded=LoadedPointCloud(np.zeros((2,3)),np.array([0,1]),{})
            with mock.patch("algorithms.REBAR_ALGORITHM_REGISTRY.get", return_value=Fake()), mock.patch("rebar_poc.load_point_cloud", return_value=loaded):
                with self.assertRaisesRegex(Exception,"tileset transform is unsupported"):
                    compute_rebar_artifact(point_cloud_path=str(root/"x.ply"),point_cloud_format="ply",source_tileset_path=str(source),output_directory=str(root/"artifact"),artifact_version="1",algorithm="fake",input_options={},parameters={},storage_root=str(root))
