#!/usr/bin/env python3
"""Isolated local step debugger, explicitly separate from the managed app stack.

Use .cloudbim/mesh-venv/bin/python scripts/pointcloud-debug.py --source ... --run
This process only serves the debug UI on loopback, never starts Vite/backend/DB.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import numpy as np
from pathlib import Path
import re
import shutil
import sys
import threading
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/mesh-service"))
from algorithms.pointcloud_normals import available_workers
from pointcloud_step_pipeline import run_from_source
from pointcloud_tile_sidecar import write_complete_rebar_sidecars
from rebar_design_prior import prepare_snapshot
from algorithms.design_prior_refinement import MODES as PRIOR_MODES


DEFAULT_PREVIEW_LIMIT = 1_000_000
RUN_SUMMARY_NAME = "summary.json"


def parsed_host_header(value):
    if not value or any(character in value for character in "/\\@"):
        return None
    try:
        parsed = urlparse(f"//{value}")
        # Accessing ``port`` also rejects malformed and out-of-range values.
        parsed.port
    except ValueError:
        return None
    return parsed.hostname


def is_loopback_host_header(value):
    """Accept loopback browser hosts even when a tunnel rewrites the port."""
    return parsed_host_header(value) in {"localhost", "127.0.0.1", "::1"}


def is_trusted_host_header(value, allowed_hosts=()):
    """Accept loopback plus explicitly configured interface hostnames."""
    hostname = parsed_host_header(value)
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    trusted = {str(host).strip().lower() for host in allowed_hosts}
    trusted.difference_update({"", "0.0.0.0", "::"})
    return hostname is not None and hostname.lower() in trusted


def manifest_summary(manifest):
    """Return only the fields needed to identify a durable history entry."""
    run_id = manifest["runId"]
    source = manifest.get("source") or {}
    parameters = manifest.get("parameters") or {}
    return {
        "runId": run_id,
        "createdAt": manifest.get("createdAt"),
        "completed": manifest.get("completed") is True,
        "priorMode": manifest.get("priorMode", "off"),
        "controlNetMode": manifest.get("controlNetMode", "off"),
        "throughStep": parameters.get("throughStep"),
        "controlNetInputStage": (manifest.get("controlNet") or {}).get("inputStage"),
        "source": {"name": source.get("name"), "pointCount": source.get("pointCount")},
        "manifestUrl": f"/runs/{run_id}/manifest.json",
    }


def run_record(manifest):
    """Build the private, compact record used for history and tile ownership."""
    record = manifest_summary(manifest)
    record["sourcePath"] = str((manifest.get("source") or {}).get("path") or "")
    return record


def public_run_summary(record):
    """Remove server-only ownership data before returning a run summary."""
    return {key: value for key, value in record.items() if key != "sourcePath"}


def write_run_record(directory, manifest):
    """Persist a small sidecar so tile requests never reparse a large manifest."""
    record = run_record(manifest)
    target = directory / RUN_SUMMARY_NAME
    temporary = directory / f"{RUN_SUMMARY_NAME}.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)
    return record


class DebugState:
    def __init__(self, source, output, prior_config=None, preview_limit=DEFAULT_PREVIEW_LIMIT, source_tiles=None):
        self.source, self.output = source, output
        inferred_tiles = source.parent / "tiles"
        self.source_tiles = (Path(source_tiles) if source_tiles else inferred_tiles).resolve()
        if not (self.source_tiles / "tileset.json").is_file():
            self.source_tiles = None
        self.prior_config = prior_config
        self.preview_limit = preview_limit
        self.lock = threading.Lock()
        self.record_lock = threading.Lock()
        self.run_records = {}
        self.run = None
        self.state = {"status": "idle", "progress": {"stage": "等待运行", "completed": 0, "total": 1},
                      "error": None, "latest": None, "sourceName": source.name,
                      "previewLimit": preview_limit, "maxWorkers": available_workers()}
        self.state.update(priorAvailable=False, priorSummary='未配置设计先验')
        if prior_config:
            snapshot = prepare_snapshot(prior_config)
            if Path(snapshot['sourcePath']).resolve() != source.resolve():
                raise ValueError('设计配置绑定了不同的源点云')
            coverage = snapshot['inventory']['coverage']
            self.state.update(priorAvailable=bool(snapshot['inventory']['units']),
                priorSummary=f"{snapshot.get('modelInfo',{}).get('name','设计模型')} · 匹配单元 {coverage['matchingUnitCount']} · 短筋 {coverage['shortUnitCount']} · 未解析构件 {coverage['unresolvedBars']}")
        latest = output / "latest.json"
        if latest.exists():
            run_id = json.loads(latest.read_text())["runId"]
            record = self.record_for_run(run_id)
            if record and record["sourcePath"] == str(source.resolve()):
                self.state.update(status="complete", latest=public_run_summary(record))

    def record_for_run(self, run_id):
        """Load and cache one immutable run record, backfilling old runs once."""
        with self.record_lock:
            if run_id in self.run_records:
                return self.run_records[run_id]
            directory = (self.output / run_id).resolve()
            if not directory.is_relative_to(self.output.resolve()) or directory.name != run_id:
                return None
            manifest_path = directory / "manifest.json"
            summary_path = directory / RUN_SUMMARY_NAME
            record = None
            try:
                if (summary_path.is_file() and manifest_path.is_file()
                        and summary_path.stat().st_mtime_ns >= manifest_path.stat().st_mtime_ns):
                    candidate = json.loads(summary_path.read_text(encoding="utf-8"))
                    if candidate.get("runId") == run_id and isinstance(candidate.get("sourcePath"), str):
                        record = candidate
                if record is None and manifest_path.is_file():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest.get("runId") == run_id:
                        record = write_run_record(directory, manifest)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return None
            if record is not None:
                self.run_records[run_id] = record
            return record

    def remember_run(self, directory, manifest):
        with self.record_lock:
            record = write_run_record(directory, manifest)
            self.run_records[record["runId"]] = record
            return record

    def progress(self, stage, completed, total):
        with self.lock:
            self.state["progress"] = dict(stage=stage, completed=completed, total=total)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self, k, workers, through_step=6, prior_mode='off', control_net_mode='off'):
        with self.lock:
            if self.state["status"] == "running":
                return False
            self.state.update(status="running", error=None,
                progress={"stage": "从原始点云重新开始", "completed": 0, "total": 1})
        def work():
            try:
                # Releasing old context bounds memory; disk snapshots remain.
                self.run = None
                snapshot = prepare_snapshot(self.prior_config) if self.prior_config and through_step >= 2 else None
                self.run = run_from_source(self.source, self.output, k=k, workers=workers, progress=self.progress,
                    through_step=through_step, prior_mode=prior_mode, design_prior=snapshot,
                    preview_limit=self.preview_limit, control_net_mode=control_net_mode)
                self._install_tiles_mvp()
                manifest = self.run.manifest
                record = self.remember_run(self.run.directory, manifest)
                timings = manifest["timings"]
                self.run = None
                with self.lock:
                    self.state.update(status="complete", latest=public_run_summary(record))
                    if snapshot is not None:
                        coverage=snapshot['inventory']['coverage']
                        self.state.update(priorAvailable=bool(snapshot['inventory']['units']),
                            priorSummary=f"{snapshot.get('modelInfo',{}).get('name','设计模型')} · 匹配单元 {coverage['matchingUnitCount']} · 短筋 {coverage['shortUnitCount']} · 未解析构件 {coverage['unresolvedBars']}")
                print(json.dumps({"runId": record["runId"], "timings": timings}), flush=True)
            except Exception as exc:
                with self.lock:
                    self.state.update(status="failed", error=str(exc))
                print(f"Debug run failed: {exc}", file=sys.stderr, flush=True)
            finally:
                self.run = None
        threading.Thread(target=work, name="pointcloud-step-run", daemon=True).start()
        return True

    def _install_tiles_mvp(self):
        """Attach immutable source tiles only to final-stage runs.

        Sidecars are optional: a missing/unmappable source tileset must not
        invalidate the normal debug result or old-preview compatibility.
        """
        if self.source_tiles is None or self.run is None or not self.run.manifest.get("completeRebar"):
            return
        try:
            complete = write_complete_rebar_sidecars(self.source_tiles, self.run.directory, self.run.context)
        except Exception as exc:
            shutil.rmtree(self.run.directory / "tile-attributes", ignore_errors=True)
            self.run.manifest.setdefault("tiles", {"schema": "pointcloud-tiles-v1"})["unavailable"] = str(exc)
        else:
            run_id = self.run.manifest["runId"]
            complete["attributeUrlTemplate"] = (
                f"/runs/{run_id}/{complete['attributeUrlTemplate']}"
            )
            self.run.manifest["tiles"] = {
                "schema": "pointcloud-tiles-v1",
                "mode": "source-3d-tiles-sidecar",
                "coordinateFrame": "native LAS XYZ; source-coordinate PNTS; no RTC_CENTER",
                "tilesetUrl": f"/runs/{run_id}/tiles/tileset.json",
                "contentBaseUrl": f"/runs/{run_id}/tiles/",
                "completeRebar": complete,
            }
        temporary = self.run.directory / "manifest.json.tmp"
        temporary.write_text(json.dumps(self.run.manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.run.directory / "manifest.json")


def handler_for(state, allowed_hosts=()):
    trusted_hosts = frozenset(allowed_hosts)

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
            if not is_trusted_host_header(host, trusted_hosts):
                self.json_response(403, {"error": "Only configured hostnames are accepted"})
                return False
            origin = self.headers.get("Origin")
            if mutation and origin and origin != f"http://{host}":
                self.json_response(403, {"error": "Cross-origin run requests are not accepted"})
                return False
            return True

        def do_GET(self):
            if not self.trusted_request():
                return
            request_url = urlparse(self.path)
            path = unquote(request_url.path)
            if path == "/api/status":
                return self.json_response(200, state.snapshot())
            if path == "/api/runs":
                manifests = []
                for file in sorted(state.output.glob("*/manifest.json"), reverse=True):
                    if not file.parent.name.startswith("."):
                        record = state.record_for_run(file.parent.name)
                        if record and record["sourcePath"] == str(state.source.resolve()):
                            manifests.append(public_run_summary(record))
                return self.json_response(200, manifests)
            if path in ("/", "/index.html", "/viewer.js"):
                file = ROOT / "scripts/pointcloud-debug" / ("viewer.js" if path == "/viewer.js" else "index.html")
            elif path == "/instancePalette.js":
                file = ROOT / "src/features/rebar-visualization/instancePalette.js"
            elif path in ("/vendor/three.module.js", "/vendor/three.core.js"):
                file = ROOT / "node_modules/three/build" / Path(path).name
            elif path.startswith("/vendor/addons/"):
                base = ROOT / "node_modules/three/examples/jsm"
                file = (base / path.removeprefix("/vendor/addons/")).resolve()
                if not file.is_relative_to(base.resolve()):
                    return self.json_response(404, {"error": "Not found"})
            elif path.startswith("/vendor/3d-tiles-renderer/"):
                base = ROOT / "node_modules/3d-tiles-renderer/build"
                file = (base / path.removeprefix("/vendor/3d-tiles-renderer/")).resolve()
                if not file.is_relative_to(base.resolve()):
                    return self.json_response(404, {"error": "Not found"})
            elif path.startswith("/runs/"):
                relative = path.removeprefix("/runs/")
                if not re.match(r"^\d{8}T\d{6}-[0-9a-f]{8}/", relative):
                    return self.json_response(404, {"error": "Not found"})
                run_id, item = relative.split("/", 1)
                if item == RUN_SUMMARY_NAME or item.startswith("."):
                    return self.json_response(404, {"error": "Not found"})
                if item.startswith("tiles/") and state.source_tiles is not None:
                    record = state.record_for_run(run_id)
                    if not record or record["sourcePath"] != str(state.source.resolve()):
                        return self.json_response(404, {"error": "Not found"})
                    file = (state.source_tiles / item.removeprefix("tiles/")).resolve()
                    allowed = state.source_tiles.resolve()
                else:
                    file = (state.output / relative).resolve()
                    allowed = state.output.resolve()
                if not file.is_relative_to(allowed):
                    return self.json_response(404, {"error": "Not found"})
            else:
                return self.json_response(404, {"error": "Not found"})
            if not file.is_file():
                return self.json_response(404, {"error": "Not found"})
            query = parse_qs(request_url.query)
            if "previewPoints" in query or "sourcePoints" in query:
                try:
                    if file.suffix != ".bin" or set(query) - {"previewPoints", "sourcePoints"}:
                        raise ValueError("Preview sampling is only supported for binary preview arrays")
                    if len(query.get("previewPoints", [])) != 1 or len(query.get("sourcePoints", [])) != 1:
                        raise ValueError("Preview sampling requires one source and target point count")
                    preview_points = int(query["previewPoints"][0])
                    source_points = int(query["sourcePoints"][0])
                    size = file.stat().st_size
                    if not 1 <= preview_points <= min(source_points, DEFAULT_PREVIEW_LIMIT):
                        raise ValueError("Invalid preview target point count")
                    if source_points < 1 or size % source_points != 0:
                        raise ValueError("Preview source point count does not match the binary array")
                    item_size = size // source_points
                    if not 1 <= item_size <= 16:
                        raise ValueError("Unsupported preview array item size")
                except (TypeError, ValueError) as exc:
                    return self.json_response(400, {"error": str(exc)})
                ids = np.linspace(0, source_points - 1, preview_points, dtype=np.int64)
                source = np.memmap(file, mode="r", dtype=np.uint8, shape=(source_points, item_size))
                body = source[ids].tobytes(order="C")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.send_header("X-Preview-Point-Count", str(preview_points))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(file.stat().st_size))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if path.startswith("/runs/") else "no-store")
            if path.startswith("/runs/") and "/tiles/" in path:
                self.send_header("X-Pointcloud-Tile-Path", path.split("/tiles/", 1)[1])
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
                if not isinstance(params, dict) or set(params) - {"k", "workers", "throughStep", "priorMode", "controlNetMode"}:
                    raise ValueError("Only k, workers, throughStep, priorMode and controlNetMode may be specified")
                k, workers = params.get("k", 32), params.get("workers", available_workers())
                through_step = params.get("throughStep", 6)
                prior_mode = params.get('priorMode', 'off')
                control_net_mode = params.get('controlNetMode', 'off')
                if control_net_mode not in ('off', 'aligned', 'auto'):
                    raise ValueError('controlNetMode 必须为 off / aligned / auto')
                if control_net_mode != 'off' and (through_step not in (2, 4) or prior_mode != 'off' or state.prior_config is None):
                    raise ValueError('控制网需要 throughStep=2（台面移除后）或 4（融合后）、priorMode=off 和服务端设计快照')
                if prior_mode not in PRIOR_MODES:
                    raise ValueError('priorMode 必须为 off / geometry / topology')
                if through_step >= 7 and (prior_mode == 'off' or state.prior_config is None):
                    raise ValueError('第 06 步需要服务端设计模型与粗配准配置，并启用设计辅助')
                if through_step < 7 and prior_mode != 'off':
                    raise ValueError('设计辅助用于第 06–07 步（throughStep=7/8）')
                if type(through_step) is not int or through_step not in (1, 2, 3, 4, 5, 6, 7, 8):
                    raise ValueError("throughStep 必须是 1–8，8 对应页面第 07 步")
                if type(k) is not int or not 3 <= k <= 128:
                    raise ValueError("k 必须是 3–128 的整数")
                if type(workers) is not int or not 1 <= workers <= available_workers():
                    raise ValueError(f"workers 必须是 1–{available_workers()} 的整数")
            except (ValueError, TypeError) as exc:
                return self.json_response(400, {"error": str(exc)})
            started = (state.start(k, workers, through_step, prior_mode, control_net_mode=control_net_mode)
                       if control_net_mode != 'off' else state.start(k, workers, through_step, prior_mode))
            if not started:
                return self.json_response(409, {"error": "已有计算正在运行"})
            self.json_response(202, {"status": "running"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-tiles", type=Path,
                        help="existing source-coordinate 3D Tiles directory (default: <source-dir>/tiles)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--host", default="127.0.0.1", help="bind address; use --allow-host with wildcard binds")
    parser.add_argument("--allow-host", action="append", default=[], help="additional browser Host value to trust")
    parser.add_argument("--port", type=int, default=6766)
    parser.add_argument("--k", type=int, default=32)
    parser.add_argument("--workers", type=int, default=available_workers())
    parser.add_argument("--through-step", type=int, choices=(1, 2, 3, 4, 5, 6, 7, 8), default=6)
    parser.add_argument("--preview-limit", type=int, default=DEFAULT_PREVIEW_LIMIT,
                        help=f"maximum browser preview points (default: {DEFAULT_PREVIEW_LIMIT})")
    parser.add_argument('--prior-config', type=Path, help='server-owned source/model/alignment JSON; never accepted from browser')
    parser.add_argument('--prior-mode', choices=PRIOR_MODES, default='off')
    parser.add_argument('--control-net-mode', choices=('off', 'aligned', 'auto'), default='off')
    parser.add_argument("--run", action="store_true", help="run from source immediately after startup")
    parser.add_argument("--compute-only", action="store_true", help="one isolated computation, no HTTP server")
    args = parser.parse_args()
    source = args.source.absolute()
    if not source.is_file():
        parser.error("source must be an existing LAS/LAZ file")
    output = (args.output or source.parent / "pointcloud-steps").absolute()
    source_tiles = (args.source_tiles or source.parent / "tiles").absolute()
    if args.source_tiles and not (source_tiles / "tileset.json").is_file():
        parser.error("--source-tiles must contain tileset.json")
    if args.through_step >= 7 and (args.prior_mode == 'off' or args.prior_config is None):
        parser.error('Step 06 requires --prior-config and --prior-mode geometry/topology')
    if args.through_step < 7 and args.prior_mode != 'off':
        parser.error('Design assistance requires --through-step 7 or 8')
    if args.control_net_mode != 'off' and (args.through_step not in (2, 4) or args.prior_mode != 'off' or args.prior_config is None):
        parser.error('Control net requires --through-step 2 (post-table) or 4 (post-fusion), --prior-mode off and --prior-config')
    if args.preview_limit < 1:
        parser.error('--preview-limit must be positive')
    if args.compute_only:
        snapshot = prepare_snapshot(args.prior_config) if args.prior_config and args.through_step >= 2 else None
        result = run_from_source(source, output, k=args.k, workers=args.workers, through_step=args.through_step,
            prior_mode=args.prior_mode, design_prior=snapshot, preview_limit=args.preview_limit, control_net_mode=args.control_net_mode)
        tile_state = DebugState(source, output, args.prior_config, args.preview_limit, source_tiles)
        tile_state.run = result
        tile_state._install_tiles_mvp()
        tile_state.remember_run(result.directory, result.manifest)
        print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
        return
    allowed_hosts = {args.host, *args.allow_host}
    if args.host in {"0.0.0.0", "::"} and not args.allow_host:
        parser.error("wildcard --host requires at least one explicit --allow-host")
    state = DebugState(source, output, args.prior_config, args.preview_limit, source_tiles)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(state, allowed_hosts))
    if args.run:
        state.start(args.k, args.workers, args.through_step, args.prior_mode, args.control_net_mode)
    print(f"Point cloud step debugger listening on {args.host}:{args.port}", flush=True)
    print(f"Durable point attributes: {output}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
