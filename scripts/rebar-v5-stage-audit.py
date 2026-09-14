#!/usr/bin/env python3
"""Observe a fresh V5 artifact run without changing algorithm source or asset metadata.

Run with .cloudbim/mesh-venv/bin/python. Stage snapshots are diagnostic source
arrays/models, not final semantic labels. Rendering must happen after measurement.
"""
import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import resource
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/mesh-service"))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--tiles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--chunk-size", type=int, default=100000)
    args = parser.parse_args()
    if args.output.exists() or args.evidence.exists():
        parser.error("output and evidence must both be new directories")
    args.evidence.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    import numpy as np
    import algorithms.rebar_v5.pipeline as pipeline
    import algorithms.rebar_v5 as adapter
    import rebar_poc
    import rebar_stream
    import rebar_tiles
    from algorithms.rebar_v5.contracts import Params

    parameters = asdict(Params.from_value(json.loads(args.parameters.read_text()) if args.parameters else {}))
    sources = sorted((ROOT / "services/mesh-service/algorithms/rebar_v5").glob("*.py"))
    sources += [ROOT / "services/mesh-service" / name for name in (
        "algorithms/rebar_v4_geometry.py", "algorithms/spatial_keys.py",
        "rebar_poc.py", "rebar_stream.py", "rebar_tiles.py")]
    report = {
        "schema": "rebar-v5-stage-audit-v1", "source": str(args.source.resolve()),
        "sourceSha256": digest(args.source), "parameters": parameters,
        "codeSha256": {str(p.relative_to(ROOT)): digest(p) for p in sources},
        "algorithm": "geometric-v5", "completed": False,
        "runMode": "fresh-process-no-algorithm-cache", "osPageCache": "not-flushed",
        "readerChunkSize": args.chunk_size, "humanTruth": False,
        "timingScope": "host isolated run; HTTP, queue, browser download/render excluded",
        "stageRecords": [], "snapshots": [],
    }
    report_path = args.evidence / "measurement.json"
    def save_report():
        report_path.write_text(json.dumps(report, indent=2, default=json_default))
    save_report()
    original_reader = rebar_stream.iter_source_chunks
    rebar_stream.iter_source_chunks = lambda path, file_format=None: original_reader(path, file_format, chunk_size=args.chunk_size)
    stats = defaultdict(lambda: {"calls": 0, "sumElapsedS": 0., "maxElapsedS": 0.})
    lock = threading.Lock()
    instrumentation_s = 0.
    original_append = pipeline.StageLog.append

    def observed_append(stages, item):
        nonlocal instrumentation_s
        original_append(stages, item)
        started = time.perf_counter()
        local = sys._getframe(1).f_locals
        name = item["name"]
        report["stageRecords"].append(dict(item))
        arrays = {}
        models = {}
        if name == "denoise":
            runtime = local["runtime"]
            # Source-index lookup retains original LAS identity; no resampling.
            import laspy
            with laspy.open(args.source) as source:
                count = source.header.point_count
            noise = np.zeros(count, np.uint8)
            suspect = np.zeros(count, np.uint8)
            for key in runtime.store.cells:
                ids = runtime.store.records(key)["source_index"]
                noise[ids] = runtime.noise_maps[key]
                suspect[ids] = runtime.suspect_maps[key]
            arrays = {"noise": noise, "suspect": suspect}
        elif name == "features":
            arrays = {"xyz": local["points"], "source_index": local["indices"]}
        elif name in ("table", "fixtures", "planar-bars", "terminal-hooks", "web-bars",
                      "ownership-review", "raw-support-verification", "raw-ownership-finalization", "intersections"):
            for key in ("table", "fixture", "body"):
                if key in local:
                    arrays[key] = local[key]
            for key in ("plane", "surfaces", "bolts", "planar", "web", "layers"):
                if key in local:
                    models[key] = local[key]
            if "data" in local:
                models["instances"] = local["data"]["instances"]
                models["intersections"] = local["data"]["intersections"]
        if arrays:
            np.savez(args.evidence / (name + ".npz"), **arrays)
        if models:
            (args.evidence / (name + ".json")).write_text(json.dumps(models, default=json_default))
        report["snapshots"].append({"stage": name, "arrays": sorted(arrays), "models": sorted(models)})
        save_report()
        instrumentation_s += time.perf_counter() - started
    pipeline.StageLog.append = observed_append

    def wrap(module, name, label=None):
        original = getattr(module, name)
        def measured(*pos, **kw):
            caller = sys._getframe(1)
            key = label or name
            if name == "multiscale":
                key += ":" + caller.f_code.co_name
                if caller.f_code.co_name == "analyze":
                    key += ":" + ("after-fixture" if "active" in caller.f_locals else "after-table")
            started = time.perf_counter()
            try:
                return original(*pos, **kw)
            finally:
                elapsed = time.perf_counter() - started
                with lock:
                    item = stats[key]
                    item["calls"] += 1
                    item["sumElapsedS"] += elapsed
                    item["maxElapsedS"] = max(item["maxElapsedS"], elapsed)
        setattr(module, name, measured)
    for name in ("multiscale", "_prime_boundary_updates", "classify", "distance_to_paths", "denoise"):
        wrap(pipeline, name)
    wrap(adapter, "export_sidecars")
    wrap(rebar_poc, "load_point_cloud", "source-bootstrap")
    wrap(rebar_tiles, "rewrite_pnts", "display-tile-rewrite")
    started = time.perf_counter()
    try:
        manifest = rebar_poc.compute_rebar_artifact(
            point_cloud_path=str(args.source.resolve()), point_cloud_format="las",
            source_tileset_path=str(args.tiles.resolve()), output_directory=str(args.output.resolve()),
            artifact_version=args.output.name, algorithm="geometric-v5", input_options={},
            parameters=parameters, storage_root=str(ROOT / "backend/data"))
        report.update(completed=True, summary=manifest["summary"])
    finally:
        report["wallElapsedS"] = time.perf_counter() - started
        report["snapshotOverheadS"] = instrumentation_s
        report["computeElapsedEstimateS"] = report["wallElapsedS"] - instrumentation_s
        report["functionTimings"] = dict(stats)
        report["functionTimingNote"] = "Nested timings overlap. Worker call sums are accumulated elapsed time, NOT wall time. Do not add to stage totals."
        report["peakRssBytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        report["codeChangedDuringRun"] = any(digest(ROOT / path) != expected for path, expected in report["codeSha256"].items())
        report["sourceUnchanged"] = digest(args.source) == report["sourceSha256"]
        save_report()
        print(json.dumps({key: report[key] for key in ("completed", "wallElapsedS", "snapshotOverheadS", "computeElapsedEstimateS", "codeChangedDuringRun")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
