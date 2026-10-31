#!/usr/bin/env python3
"""Replay a saved C2M request without writing business artifacts or database rows.

Run with .cloudbim/mesh-venv/bin/python. Request JSON uses the keyword names of
compute_instance_comparison. --compare checks every numeric row and report field
except timing fields against a prior run's output prefix.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pstats
import resource
import sys
from time import perf_counter, process_time

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/mesh-service"))


def without_timings(value):
    if isinstance(value, dict):
        return {key: without_timings(item) for key, item in value.items()
                if key not in {"timings", "timingSeconds"}}
    if isinstance(value, list):
        return [without_timings(item) for item in value]
    return value


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--module-source", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    source = args.module_source or ROOT / "services/mesh-service/rebar_comparison.py"
    spec = importlib.util.spec_from_file_location("benchmark_comparison", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = json.loads(args.request.read_text())
    profiler = cProfile.Profile() if args.profile else None
    if profiler:
        profiler.enable()
    started = perf_counter()
    cpu_started = process_time()
    result = module.compute_instance_comparison(**request)
    wall = perf_counter() - started
    cpu_seconds = process_time() - cpu_started
    if profiler:
        profiler.disable()
    arrays = {"distances": result.pop("distances")}
    mesh = result.pop("mesh")
    arrays.update(vertices=np.asarray(mesh.vertices), triangles=np.asarray(mesh.triangles),
                  vertex_normals=np.asarray(mesh.vertex_normals))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(args.output) + ".npz", **arrays)
    report_path = Path(str(args.output) + ".result.json")
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    checks = {}
    if args.compare:
        with np.load(str(args.compare) + ".npz") as previous:
            for key, value in arrays.items():
                old = previous[key]
                checks[key] = {"shape": list(value.shape),
                               "exact": bool(np.array_equal(old, value, equal_nan=True))}
                if key == "distances":
                    checks[key]["nanMaskExact"] = bool(np.array_equal(np.isnan(old), np.isnan(value)))
                    finite = np.isfinite(old) & np.isfinite(value)
                    checks[key]["maxAbsDifference"] = float(np.max(np.abs(old[finite] - value[finite]))) if finite.any() else 0.
        prior_report = json.loads(Path(str(args.compare) + ".result.json").read_text())
        checks["reportExcludingTimings"] = {"exact": without_timings(prior_report) == without_timings(result)}
    summary = {
        "wallSeconds": wall, "cpuSeconds": cpu_seconds,
        "peakRssKiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "moduleSource": str(source), "moduleSha256": sha256(source),
        "requestSha256": sha256(args.request),
        "workerSetting": os.environ.get("REBAR_COMPARISON_WORKERS", "default"),
        "effectiveWorkerCount": module._comparison_worker_count(len(result["rebarComparison"]["bars"]))
        if hasattr(module, "_comparison_worker_count") else 1,
        "scanSha256": sha256(request["scan_path"]),
        "instanceMapSha256": sha256(request["instance_map_path"]),
        "analysisManifestSha256": sha256((Path(request["analysis_mesh_path"]) if Path(request["analysis_mesh_path"]).is_dir()
                                         else Path(request["analysis_mesh_path"]).parent) / "manifest.json"),
        "meshVertexCount": len(arrays["vertices"]),
        "knownVertexCount": int(np.count_nonzero(np.isfinite(arrays["distances"]))),
        "barCount": len(result["rebarComparison"]["bars"]),
        "timings": result["rebarComparison"]["timings"], "checks": checks,
    }
    Path(str(args.output) + ".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if profiler:
        profiler.dump_stats(str(args.output) + ".prof")
        with Path(str(args.output) + ".profile.txt").open("w") as stream:
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime").print_stats(50)
    print(json.dumps(summary, separators=(",", ":")), flush=True)
    if any(not check["exact"] for check in checks.values()):
        raise SystemExit("C2M output changed")


if __name__ == "__main__":
    main()
