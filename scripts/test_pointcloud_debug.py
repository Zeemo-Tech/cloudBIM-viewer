import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
import json
import tempfile
import threading
import time
import urllib.request
import urllib.error
import struct
from unittest.mock import Mock, patch
from types import SimpleNamespace
from scipy.spatial import cKDTree


SCRIPT = Path(__file__).with_name("pointcloud-debug.py")
SPEC = importlib.util.spec_from_file_location("pointcloud_debug", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LoopbackHostHeaderTest(unittest.TestCase):
    def test_accepts_forwarded_loopback_ports(self):
        for value in ("localhost:8766", "localhost:56446", "127.0.0.1:56446", "[::1]:56446"):
            with self.subTest(value=value):
                self.assertTrue(MODULE.is_loopback_host_header(value))

    def test_rejects_non_loopback_or_malformed_hosts(self):
        for value in ("", "example.com:8766", "localhost.example:8766", "localhost@evil.test", "localhost:99999"):
            with self.subTest(value=value):
                self.assertFalse(MODULE.is_loopback_host_header(value))

    def test_accepts_the_explicit_bind_host_without_trusting_other_interfaces(self):
        allowed = {"10.0.0.4"}
        self.assertTrue(MODULE.is_trusted_host_header("10.0.0.4:8766", allowed))
        self.assertTrue(MODULE.is_trusted_host_header("localhost:8766", allowed))
        self.assertFalse(MODULE.is_trusted_host_header("192.168.3.143:8766", allowed))
        self.assertFalse(MODULE.is_trusted_host_header("example.com:8766", allowed))


class PriorApiTests(unittest.TestCase):
    def test_prior_mode_contract_rejects_browser_paths_and_requires_server_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=MODULE.DebugState(Path(tmp)/'source.las',Path(tmp))
            state.start=Mock(return_value=True)
            server=MODULE.ThreadingHTTPServer(('127.0.0.1',0),MODULE.handler_for(state))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def post(body):
                request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/run',
                    data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                try:
                    with urllib.request.urlopen(request) as response:return response.status
                except urllib.error.HTTPError as exc:return exc.code
            try:
                self.assertEqual(post({}),202)
                state.start.assert_called_with(32,MODULE.available_workers(),6,'off')
                self.assertEqual(post({'priorMode':'geometry'}),400)
                self.assertEqual(post({'priorMode':'topology','ifcPath':'/tmp/a.ifc'}),400)
                self.assertEqual(post({'priorMode':True}),400)
                state.prior_config=Path(tmp)/'server-config.json'
                self.assertEqual(post({'priorMode':'topology'}),400)
                self.assertEqual(post({'throughStep':7}),400)
                self.assertEqual(post({'throughStep':7,'priorMode':'topology'}),202)
                state.start.assert_called_with(32,MODULE.available_workers(),7,'topology')
                self.assertEqual(post({'throughStep':8,'priorMode':'off'}),400)
                self.assertEqual(post({'throughStep':8,'priorMode':'topology'}),202)
                state.start.assert_called_with(32,MODULE.available_workers(),8,'topology')
                self.assertEqual(post({'throughStep':9,'priorMode':'topology'}),400)
                self.assertEqual(post({'priorMode':'geometry','throughStep':6}),400)
                self.assertEqual(post({'priorMode':{'path':'/tmp/anything'}}),400)
                for mode in ('aligned', 'auto'):
                    self.assertEqual(post({'throughStep':4, 'controlNetMode':mode}),202)
                    state.start.assert_called_with(32, MODULE.available_workers(), 4, 'off', control_net_mode=mode)
                for body in ({'throughStep':2, 'controlNetMode':'aligned'},
                             {'throughStep':8, 'controlNetMode':'aligned'},
                             {'throughStep':2, 'controlNetMode':'aligned', 'priorMode':'topology'},
                             {'throughStep':2, 'controlNetMode':True},
                             {'throughStep':2, 'controlNetMode':{}},
                             {'throughStep':2, 'controlNetMode':'automatic'}):
                    self.assertEqual(post(body),400)
                state.prior_config=None
                self.assertEqual(post({'throughStep':4, 'controlNetMode':'auto'}),400)
            finally:
                server.shutdown();server.server_close();thread.join()


class PreviewApiTests(unittest.TestCase):
    def test_final_run_manifest_advertises_source_tiles_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source.las"; source.touch(); tiles = root / "tiles"; tiles.mkdir()
            feature = json.dumps({"POINTS_LENGTH": 3, "POSITION": {"byteOffset": 0}}).encode(); feature += b" " * ((-len(feature)) % 8)
            points = MODULE.np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype="<f4").tobytes()
            (tiles / "content.pnts").write_bytes(b"pnts" + struct.pack("<6I", 1, 28 + len(feature) + len(points), len(feature), len(points), 0, 0) + feature + points)
            (tiles / "tileset.json").write_text(json.dumps({"root": {"content": {"uri": "content.pnts"}}}))
            run_id = "20260910T120000-1234abcd"; run = root / "runs" / run_id; run.mkdir(parents=True)
            positions = MODULE.np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]])
            context = SimpleNamespace(positions=positions, tree=cKDTree(positions),
                complete_class=MODULE.np.array([3, 4, 3], "u1"), complete_instance=MODULE.np.array([2, 0, 4], "<u4"),
                complete_cluster=MODULE.np.array([0, 0, 8], "<u4"))
            manifest = {"runId": run_id, "source": {"path": str(source.resolve())}, "completeRebar": {"enabled": True}}
            (run / "manifest.json").write_text(json.dumps(manifest))
            state = MODULE.DebugState(source, root / "runs")
            state.run = SimpleNamespace(manifest=manifest, directory=run, context=context)
            state._install_tiles_mvp()
            contract = manifest["tiles"]
            self.assertEqual(contract["tilesetUrl"], f"/runs/{run_id}/tiles/tileset.json")
            self.assertEqual(contract["completeRebar"]["attributeUrlTemplate"],
                f"/runs/{run_id}/tile-attributes/complete-rebar/{{tilePath}}.bin")
            self.assertEqual(contract["completeRebar"]["format"]["recordBytes"], 9)
            self.assertTrue((run / "tile-attributes/complete-rebar/content.pnts.bin").is_file())
            self.assertNotIn("\n", (run / "manifest.json").read_text())

    def test_source_tiles_and_3d_tiles_renderer_build_are_safely_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source.las"; source.touch()
            tiles = root / "tiles"; tiles.mkdir()
            feature = json.dumps({"POINTS_LENGTH": 1, "POSITION": {"byteOffset": 0}}).encode()
            feature += b" " * ((-len(feature)) % 8)
            payload = b"\0" * 12
            (tiles / "content.pnts").write_bytes(b"pnts" + struct.pack("<6I", 1, 28 + len(feature) + len(payload), len(feature), len(payload), 0, 0) + feature + payload)
            (tiles / "tileset.json").write_text(json.dumps({"root": {"content": {"uri": "content.pnts"}}}))
            run_id = "20260910T120000-1234abcd"; run = root / "runs" / run_id; run.mkdir(parents=True)
            (run / "manifest.json").write_text(json.dumps({"runId": run_id, "source": {"path": str(source.resolve())}}))
            state = MODULE.DebugState(source, root / "runs")
            manifest_reads = 0
            original_read_text = Path.read_text

            def counted_read_text(path, *args, **kwargs):
                nonlocal manifest_reads
                if path.name == "manifest.json":
                    manifest_reads += 1
                    time.sleep(0.02)
                return original_read_text(path, *args, **kwargs)

            server = MODULE.ThreadingHTTPServer(("127.0.0.1", 0), MODULE.handler_for(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with patch.object(Path, "read_text", counted_read_text):
                    def read_tile():
                        with urllib.request.urlopen(base + f"/runs/{run_id}/tiles/content.pnts") as response:
                            self.assertEqual(response.read()[:4], b"pnts")
                            self.assertEqual(response.headers["X-Pointcloud-Tile-Path"], "content.pnts")

                    with ThreadPoolExecutor(max_workers=8) as pool:
                        list(pool.map(lambda _: read_tile(), range(8)))
                with urllib.request.urlopen(base + "/vendor/3d-tiles-renderer/index.js") as response:
                    self.assertIn(b"TilesRenderer", response.read())
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + "/vendor/3d-tiles-renderer/../package.json")
                self.assertEqual(error.exception.code, 404)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + f"/runs/{run_id}/summary.json")
                self.assertEqual(error.exception.code, 404)
                self.assertEqual(manifest_reads, 1, "瓦片请求不应反复解析同一份大型 manifest")
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_binary_preview_can_be_sampled_consistently_for_existing_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.las"
            source.touch()
            output = root / "runs"
            run_id = "20260910T120000-1234abcd"
            preview = output / run_id / "preview"
            preview.mkdir(parents=True)
            (preview / "positions.bin").write_bytes(bytes(range(30)))
            state = MODULE.DebugState(source, output)
            server = MODULE.ThreadingHTTPServer(("127.0.0.1", 0), MODULE.handler_for(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = (f"http://127.0.0.1:{server.server_port}/runs/{run_id}/preview/positions.bin"
                   "?previewPoints=4&sourcePoints=10")
            try:
                with urllib.request.urlopen(url) as response:
                    body = response.read()
                    self.assertEqual(response.headers["X-Preview-Point-Count"], "4")
                    self.assertEqual(response.headers["Cache-Control"], "public, max-age=31536000, immutable")
                self.assertEqual(body, bytes(range(0, 3)) + bytes(range(9, 12))
                                 + bytes(range(18, 21)) + bytes(range(27, 30)))
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_workbench_default_preview_is_one_million_points(self):
        self.assertEqual(MODULE.DEFAULT_PREVIEW_LIMIT, 1_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.las"
            source.touch()
            state = MODULE.DebugState(source, root / "runs")
            self.assertEqual(state.snapshot()["previewLimit"], 1_000_000)

    def test_status_and_history_use_small_summaries_but_manifest_stays_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.las"
            source.touch()
            output = root / "runs"
            run_id = "20260910T120000-1234abcd"
            run = output / run_id
            run.mkdir(parents=True)
            manifest = {
                "runId": run_id,
                "createdAt": "2026-09-10T12:00:01+00:00",
                "completed": True,
                "source": {"path": str(source.resolve()), "pointCount": 9_216_369},
                "parameters": {"throughStep": 7},
                "priorMode": "off",
                "preview": {"positionsUrl": f"/runs/{run_id}/preview/positions.bin"},
                "internalRebar": {"instances": [{"id": value} for value in range(2_000)]},
            }
            (run / "manifest.json").write_text(json.dumps(manifest))
            (output / "latest.json").write_text(json.dumps({"runId": run_id}))
            state = MODULE.DebugState(source, output)
            self.assertTrue((run / "summary.json").is_file())
            server = MODULE.ThreadingHTTPServer(("127.0.0.1", 0), MODULE.handler_for(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def get(path):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{path}") as response:
                    return response.headers, response.read()

            try:
                _, status_body = get("/api/status")
                original_read_text = Path.read_text

                def reject_manifest_read(path, *args, **kwargs):
                    if path.name == "manifest.json":
                        raise AssertionError("已有摘要时不应读取大型 manifest")
                    return original_read_text(path, *args, **kwargs)

                with patch.object(Path, "read_text", reject_manifest_read):
                    _, history_body = get("/api/runs")
                manifest_headers, manifest_body = get(f"/runs/{run_id}/manifest.json")
                status = json.loads(status_body)
                history = json.loads(history_body)

                self.assertEqual(status["latest"]["runId"], run_id)
                self.assertEqual(history, [status["latest"]])
                self.assertNotIn("preview", status["latest"])
                self.assertNotIn("internalRebar", status["latest"])
                self.assertLess(len(history_body), 1024)
                self.assertEqual(json.loads(manifest_body), manifest)
                self.assertEqual(manifest_headers["Cache-Control"], "public, max-age=31536000, immutable")
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_completed_run_releases_full_context_and_keeps_only_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.las"
            source.touch()
            output = root / "runs"
            run_id = "20260910T120000-1234abcd"
            run_dir = output / run_id
            run_dir.mkdir(parents=True)
            manifest = {
                "runId": run_id,
                "createdAt": "2026-09-10T12:00:01+00:00",
                "completed": True,
                "source": {"name": source.name, "path": str(source.resolve()), "pointCount": 3},
                "parameters": {"throughStep": 1},
                "priorMode": "off",
                "timings": {"totalS": 0.01},
            }
            fake_run = SimpleNamespace(manifest=manifest, directory=run_dir, context=object())
            state = MODULE.DebugState(source, output)

            with patch.object(MODULE, "run_from_source", return_value=fake_run), \
                    patch.object(state, "_install_tiles_mvp"):
                self.assertTrue(state.start(32, 1, 1, "off"))
                deadline = time.monotonic() + 2
                while state.snapshot()["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.01)

            snapshot = state.snapshot()
            self.assertEqual(snapshot["status"], "complete")
            self.assertEqual(snapshot["latest"], MODULE.manifest_summary(manifest))
            self.assertIsNone(state.run)
            self.assertTrue((run_dir / "summary.json").is_file())

    def test_viewer_uses_throttled_compact_history_for_experiment_comparison(self):
        viewer = Path(__file__).with_name("pointcloud-debug").joinpath("viewer.js").read_text()
        page = Path(__file__).with_name("pointcloud-debug").joinpath("index.html").read_text()
        self.assertIn("`${api}/runs`", viewer)
        self.assertIn("Date.now() < historyRefreshAt", viewer)
        self.assertIn("historyRefreshAt = Date.now() + 5000", viewer)
        self.assertNotIn("$('history')", viewer)
        self.assertNotIn('id="history"', page)

    def test_viewer_suspends_old_tiles_while_a_run_is_active(self):
        viewer = Path(__file__).with_name("pointcloud-debug").joinpath("viewer.js").read_text()
        self.assertIn("function suspendCompleteTilesForRun()", viewer)
        running_branch = viewer[viewer.index("if (running) {"):viewer.index("} else {", viewer.index("if (running) {"))]
        self.assertIn("suspendCompleteTilesForRun();", running_branch)

    def test_viewer_loads_the_manifest_preview_without_a_second_browser_cap(self):
        viewer = Path(__file__).with_name("pointcloud-debug").joinpath("viewer.js").read_text()
        self.assertNotIn("PREVIEW_RENDER_LIMIT", viewer)
        self.assertIn("const preview = manifest.preview", viewer)
        self.assertNotIn("    installCompleteTiles();", viewer)
        self.assertIn("正在下载预览数据", viewer)
        self.assertIn("正在校验", viewer)
        self.assertIn("正在构建预览场景", viewer)


if __name__ == "__main__":
    unittest.main()
