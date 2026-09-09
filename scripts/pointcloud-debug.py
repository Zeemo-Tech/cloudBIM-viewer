#!/usr/bin/env python3
"""Isolated local step debugger, explicitly separate from the managed app stack.

Use .cloudbim/mesh-venv/bin/python scripts/pointcloud-debug.py --source ... --run
This process only serves the debug UI on loopback, never starts Vite/backend/DB.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import shutil
import sys
import threading
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/mesh-service"))
from algorithms.pointcloud_normals import available_workers
from pointcloud_step_pipeline import run_from_source


def is_loopback_host_header(value):
    """Accept loopback browser hosts even when a tunnel rewrites the port."""
    if not value or any(character in value for character in "/\\@"):
        return False
    try:
        parsed = urlparse(f"//{value}")
        # Accessing ``port`` also rejects malformed and out-of-range values.
        parsed.port
    except ValueError:
        return False
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


class DebugState:
    def __init__(self, source, output):
        self.source, self.output = source, output
        self.lock = threading.Lock()
        self.run = None
        self.state = {"status": "idle", "progress": {"stage": "等待运行", "completed": 0, "total": 1},
                      "error": None, "latest": None, "sourceName": source.name, "maxWorkers": available_workers()}
        latest = output / "latest.json"
        if latest.exists():
            run_id = json.loads(latest.read_text())["runId"]
            path = output / run_id / "manifest.json"
            if path.is_file() and path.resolve().is_relative_to(output.resolve()):
                manifest = json.loads(path.read_text())
                if manifest["source"]["path"] == str(source.resolve()):
                    self.state.update(status="complete", latest=manifest)

    def progress(self, stage, completed, total):
        with self.lock:
            self.state["progress"] = dict(stage=stage, completed=completed, total=total)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self, k, workers, through_step=6):
        with self.lock:
            if self.state["status"] == "running":
                return False
            self.state.update(status="running", error=None,
                progress={"stage": "从原始点云重新开始", "completed": 0, "total": 1})
        def work():
            try:
                # Releasing old context bounds memory; disk snapshots remain.
                self.run = None
                self.run = run_from_source(self.source, self.output, k=k, workers=workers, progress=self.progress, through_step=through_step)
                with self.lock:
                    self.state.update(status="complete", latest=self.run.manifest)
                print(json.dumps({"runId": self.run.manifest["runId"], "timings": self.run.manifest["timings"]}), flush=True)
            except Exception as exc:
                with self.lock:
                    self.state.update(status="failed", error=str(exc))
                print(f"Debug run failed: {exc}", file=sys.stderr, flush=True)
        threading.Thread(target=work, name="pointcloud-step-run", daemon=True).start()
        return True


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if args and str(args[0]).startswith("GET /api/status"):
                return
            super().log_message(fmt, *args)

        def json_response(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def trusted_request(self, mutation=False):
            host = self.headers.get("Host", "")
            if not is_loopback_host_header(host):
                self.json_response(403, {"error": "Only loopback hostnames are accepted"})
                return False
            origin = self.headers.get("Origin")
            if mutation and origin and origin != f"http://{host}":
                self.json_response(403, {"error": "Cross-origin run requests are not accepted"})
                return False
            return True

        def do_GET(self):
            if not self.trusted_request():
                return
            path = unquote(urlparse(self.path).path)
            if path == "/api/status":
                return self.json_response(200, state.snapshot())
            if path == "/api/runs":
                manifests = []
                for file in sorted(state.output.glob("*/manifest.json"), reverse=True):
                    if not file.parent.name.startswith("."):
                        item = json.loads(file.read_text())
                        if item["source"]["path"] == str(state.source.resolve()):
                            manifests.append(item)
                return self.json_response(200, manifests)
            if path in ("/", "/index.html", "/viewer.js"):
                file = ROOT / "scripts/pointcloud-debug" / ("viewer.js" if path == "/viewer.js" else "index.html")
            elif path in ("/vendor/three.module.js", "/vendor/three.core.js"):
                file = ROOT / "node_modules/three/build" / Path(path).name
            elif path.startswith("/vendor/addons/"):
                base = ROOT / "node_modules/three/examples/jsm"
                file = (base / path.removeprefix("/vendor/addons/")).resolve()
                if not file.is_relative_to(base.resolve()):
                    return self.json_response(404, {"error": "Not found"})
            elif path.startswith("/runs/"):
                relative = path.removeprefix("/runs/")
                if not re.match(r"^\d{8}T\d{6}-[0-9a-f]{8}/", relative):
                    return self.json_response(404, {"error": "Not found"})
                file = (state.output / relative).resolve()
                if not file.is_relative_to(state.output.resolve()):
                    return self.json_response(404, {"error": "Not found"})
            else:
                return self.json_response(404, {"error": "Not found"})
            if not file.is_file():
                return self.json_response(404, {"error": "Not found"})
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(file.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                with file.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not self.trusted_request(mutation=True):
                return
            if self.path != "/api/run":
                return self.json_response(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid request size")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected application/json")
                params = json.loads(self.rfile.read(length))
                if not isinstance(params, dict) or set(params) - {"k", "workers", "throughStep"}:
                    raise ValueError("Only k, workers and throughStep may be specified")
                k, workers = params.get("k", 32), params.get("workers", available_workers())
                through_step = params.get("throughStep", 6)
                if type(through_step) is not int or through_step not in (1, 2, 3, 4, 5, 6):
                    raise ValueError("throughStep 必须是 1–6，6 为内部钢筋分层与实例")
                if type(k) is not int or not 3 <= k <= 128:
                    raise ValueError("k 必须是 3–128 的整数")
                if type(workers) is not int or not 1 <= workers <= available_workers():
                    raise ValueError(f"workers 必须是 1–{available_workers()} 的整数")
            except (ValueError, TypeError) as exc:
                return self.json_response(400, {"error": str(exc)})
            if not state.start(k, workers, through_step):
                return self.json_response(409, {"error": "已有计算正在运行"})
            self.json_response(202, {"status": "running"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--k", type=int, default=32)
    parser.add_argument("--workers", type=int, default=available_workers())
    parser.add_argument("--through-step", type=int, choices=(1, 2, 3, 4, 5, 6), default=6)
    parser.add_argument("--run", action="store_true", help="run from source immediately after startup")
    parser.add_argument("--compute-only", action="store_true", help="one isolated computation, no HTTP server")
    args = parser.parse_args()
    source = args.source.absolute()
    if not source.is_file():
        parser.error("source must be an existing LAS/LAZ file")
    output = (args.output or source.parent / "pointcloud-steps").absolute()
    if args.compute_only:
        result = run_from_source(source, output, k=args.k, workers=args.workers, through_step=args.through_step)
        print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
        return
    state = DebugState(source, output)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(state))
    if args.run:
        state.start(args.k, args.workers, args.through_step)
    print(f"Point cloud step debugger: http://127.0.0.1:{args.port}", flush=True)
    print(f"Durable point attributes: {output}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
