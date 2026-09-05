#!/usr/bin/env python3
"""Render fixed, raw-first review panels for a V5 rebar artifact.

Labels and centreline products are overlays for inspection only.  They are not
human ground truth and this tool never interprets an intersection as a point
classification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import laspy
import matplotlib.pyplot as plt
import numpy as np

SCENE_NAMES = {0: "unknown", 1: "table", 2: "rebar", 3: "noise", 4: "fixture"}
SCENE_COLORS = {0: "#64748b", 1: "#cbd5e1", 2: "#ef4444", 3: "#e879f9", 4: "#10b981"}


def stable_rank(indices: np.ndarray) -> np.ndarray:
    """Stable pseudo-random ranking without retaining a full source cloud."""
    value = indices.astype(np.uint64) * np.uint64(11400714819323198485)
    return value ^ (value >> np.uint64(29))


def bounded_append(existing: list[tuple[int, np.ndarray]], items, limit: int):
    existing.extend(items)
    if len(existing) <= limit:
        return
    ranks = stable_rank(np.fromiter((item[0] for item in existing), dtype=np.uint64))
    keep = np.argpartition(ranks, limit - 1)[:limit]
    existing[:] = [existing[int(index)] for index in keep]


def load_regions(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "rebar-v5-raw-review-regions-v1":
        raise ValueError(f"unsupported region schema in {path}")
    return value


def in_bounds(xyz: np.ndarray, bounds: dict) -> np.ndarray:
    return np.all(xyz >= np.asarray(bounds["min"], float), axis=1) & np.all(xyz <= np.asarray(bounds["max"], float), axis=1)


def collect_raw(source: Path, regions: list[dict], sample_limit: int, chunk_size: int):
    samples = {region["id"]: [] for region in regions}
    counts = Counter()
    index = 0
    with laspy.open(source) as reader:
        header = reader.header
        header_summary = {
            "pointCount": int(header.point_count), "pointFormat": int(header.point_format.id),
            "mins": list(map(float, header.mins)), "maxs": list(map(float, header.maxs)),
            "scales": list(map(float, header.scales)), "offsets": list(map(float, header.offsets)),
        }
        for records in reader.chunk_iterator(chunk_size):
            xyz = np.column_stack((records.x, records.y, records.z)).astype(np.float64, copy=False)
            ids = np.arange(index, index + len(xyz), dtype=np.uint64)
            index += len(xyz)
            finite = np.isfinite(xyz).all(axis=1)
            for region in regions:
                selected = finite & in_bounds(xyz, region["bounds"])
                counts[region["id"]] += int(selected.sum())
                rows = np.flatnonzero(selected)
                # Keep the per-chunk candidate bounded too: the overall ROI
                # may contain every raw point in a 250k LAS chunk.
                if len(rows) > sample_limit:
                    ranks = stable_rank(ids[rows])
                    rows = rows[np.argpartition(ranks, sample_limit - 1)[:sample_limit]]
                bounded_append(samples[region["id"]], [(int(ids[row]), xyz[row].copy()) for row in rows], sample_limit)
    return header_summary, counts, samples


def label_manifest(directory: Path) -> tuple[Path, dict] | tuple[None, None]:
    for candidate in (directory / "labels" / "manifest.json", directory / "manifest.json"):
        if candidate.exists():
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
            if manifest.get("schema") == "rebar-raw-labels-v2":
                return candidate.parent, manifest
    return None, None


def collect_labels(directory: Path, wanted: set[int]) -> dict[int, tuple[int, int]]:
    root, manifest = label_manifest(directory)
    if not root or not manifest or not wanted:
        return {}
    result: dict[int, tuple[int, int]] = {}
    for chunk in manifest.get("chunks", []):
        path = root / chunk["path"]
        with np.load(path) as data:
            ids = data["source_index"]
            rows = np.flatnonzero(np.isin(ids, np.fromiter(wanted, dtype=np.uint64)))
            scene = data["scene_class"]
            instance = data["rebar_instance"]
            for row in rows:
                result[int(ids[row])] = (int(scene[row]), int(instance[row]))
    return result


def result_analysis(directory: Path) -> dict:
    path = directory / "result.json"
    if not path.exists():
        return {"instances": [], "intersections": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("analysis", value).get("analysis", value.get("analysis", {})) or {"instances": [], "intersections": []}


def draw_products(axis, region, analysis, side: bool):
    bounds = region["bounds"]
    for instance in analysis.get("instances", []):
        for observed in instance.get("observedSegments", []):
            points = np.asarray(observed.get("points", []), float)
            if points.ndim == 2 and points.shape[1:] == (3,) and len(points):
                axis.plot(points[:, 0], points[:, 2] if side else points[:, 1], color="#facc15", linewidth=.7)
        for inferred in instance.get("inferredSegments", []):
            points = np.asarray(inferred.get("points", inferred.get("centerline", [])), float)
            if points.ndim == 2 and points.shape[1:] == (3,) and len(points):
                axis.plot(points[:, 0], points[:, 2] if side else points[:, 1], color="#fb923c", linewidth=.7, linestyle="--")
    for marker in analysis.get("intersections", []):
        point = np.asarray(marker.get("position", []), float)
        if point.shape == (3,) and in_bounds(point[None, :], bounds)[0]:
            axis.scatter([point[0]], [point[2] if side else point[1]], marker="x", c="#fef08a", s=24, linewidths=1)


def plot_region(region, rows, labels, analysis, output: Path):
    xyz = np.asarray([point for _, point in rows], float) if rows else np.empty((0, 3))
    ids = [identifier for identifier, _ in rows]
    scene = np.asarray([labels.get(identifier, (0, 0))[0] for identifier in ids])
    instance = np.asarray([labels.get(identifier, (0, 0))[1] for identifier in ids])
    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for row, side in enumerate((False, True)):
        horizontal = xyz[:, 2] if side else xyz[:, 1]
        for col, mode in enumerate(("classification", "instance")):
            axis = axes[row, col]
            if len(xyz):
                if mode == "classification":
                    colors = [SCENE_COLORS.get(int(value), "#64748b") for value in scene]
                else:
                    colors = plt.cm.hsv((instance % 31) / 31)
                    colors[instance == 0] = (0.38, 0.45, 0.55, 1)
                axis.scatter(xyz[:, 0], horizontal, c=colors, s=1, linewidths=0, rasterized=True)
            draw_products(axis, region, analysis, side)
            axis.set_xlabel("x (m)")
            axis.set_ylabel("z (m)" if side else "y (m)")
            bounds=region['bounds']
            axis.set_xlim(bounds['min'][0],bounds['max'][0])
            dimension=2 if side else 1
            axis.set_ylim(bounds['min'][dimension],bounds['max'][dimension])
            axis.set_title(f"{'side' if side else 'top'} · {mode}")
            axis.grid(alpha=.15)
    figure.suptitle(f"{region['id']} — algorithm overlays, not human truth\n"
                   "red: steel · green: fixture · grey: table/unknown · magenta: noise · yellow: measured axis/intersection · orange dashed: inferred", fontsize=9)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_las", type=Path, help="raw LAS source; read in bounded chunks")
    parser.add_argument("artifact_directory", type=Path, help="V5 artifact directory containing result.json and labels/")
    parser.add_argument("output_directory", type=Path, help="directory for PNG panels and review-summary.json")
    parser.add_argument("--regions", type=Path, default=Path(__file__).resolve().parents[1] / "docs/research/rebar-v5-review-regions.json")
    parser.add_argument("--sample-limit", type=int, default=12000, help="maximum raw points retained per ROI")
    parser.add_argument("--chunk-size", type=int, default=250000, help="LAS read chunk size")
    args = parser.parse_args()
    if args.sample_limit <= 0 or args.chunk_size <= 0:
        parser.error("sample-limit and chunk-size must be positive")
    regions_doc = load_regions(args.regions)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    header, raw_counts, samples = collect_raw(args.source_las, regions_doc["regions"], args.sample_limit, args.chunk_size)
    wanted = {identifier for rows in samples.values() for identifier, _ in rows}
    labels = collect_labels(args.artifact_directory, wanted)
    analysis = result_analysis(args.artifact_directory)
    summary = {"schema": "rebar-v5-review-summary-v1", "disclaimer": "Algorithm labels and products are review overlays, not human truth.", "sourceHeader": header, "regions": {}}
    for region in regions_doc["regions"]:
        identifier = region["id"]
        rows = samples[identifier]
        scene_counts = Counter(SCENE_NAMES.get(labels.get(index, (0, 0))[0], "unknown") for index, _ in rows)
        instances = {labels.get(index, (0, 0))[1] for index, _ in rows} - {0}
        plot_region(region, rows, labels, analysis, args.output_directory / f"{identifier}.png")
        summary["regions"][identifier] = {"rawPointCount": int(raw_counts[identifier]), "samplePointCount": len(rows), "labelledSamplePointCount": sum(index in labels for index, _ in rows), "sceneCountsInSample": dict(scene_counts), "nonzeroInstanceCountInSample": len(instances), "explanation": region["explanation"]}
    (args.output_directory / "review-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
