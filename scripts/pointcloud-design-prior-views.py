#!/usr/bin/env python3
"""Deterministic orthographic review views for design-prior point-cloud runs.

The inputs are run directories written by the point-cloud pipeline.  Positions
are native source-LAS coordinates from ``positions.npy``; no registration,
resampling, or source mutation occurs here.  Global views take a fixed stride
for legibility while each close-up reads every point in its region.

Example:
  .cloudbim/mesh-venv/bin/python scripts/pointcloud-design-prior-views.py \
    --baseline .../20260910T053733-a2c942c9 \
    --old .../20260910T053733-a2c942c9 \
    --result .../20260910T083213-20a8d960 --output .cloudbim/design-prior/review-v3/visuals
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np


VIEWS = {
    "xy-top": (np.array([1., 0., 0.]), np.array([0., 1., 0.]), "X (m)", "Y (m)"),
    "xz-side": (np.array([1., 0., 0.]), np.array([0., 0., 1.]), "X (m)", "Z (m)"),
    "yz-end": (np.array([0., 1., 0.]), np.array([0., 0., 1.]), "Y (m)", "Z (m)"),
    # Fixed, orthonormal camera plane for a reproducible oblique projection.
    "oblique": (np.array([.70710678, -.70710678, 0.]), np.array([.40824829, .40824829, -.81649658]), "oblique U (m)", "oblique V (m)"),
}
PANEL_NAMES = ("Baseline complete", "Earlier topology", "Selected result")
ACTION_COLORS = {"added": "#00a6d6", "deleted": "#e63946"}


def load_run(path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    report_path = path / "design-prior.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"inventory": {"units": []}, "components": []}
    arrays = {name: np.load(path / f"{name}.npy", mmap_mode="r") for name in
              ("positions", "complete_class", "complete_instance")}
    for name, fallback in (("prior_class", "complete_class"), ("prior_instance", "complete_instance")):
        arrays[name] = np.load(path / f"{name}.npy", mmap_mode="r") if (path / f"{name}.npy").exists() else arrays[fallback]
    return {"path": path, "id": manifest.get("runId", path.name), "manifest": manifest, "report": report, "arrays": arrays}


def stable_colors(ids):
    """Stable component/instance hues without depending on encounter order."""
    ids = np.asarray(ids, dtype=np.uint64)
    hue = ((ids * np.uint64(2654435761)) % np.uint64(360)).astype(float) / 360
    return matplotlib.colormaps["tab20"](hue)


def project(points, view):
    u, v, _, _ = VIEWS[view]
    return np.column_stack((points @ u, points @ v))


def extent_from(points2, padding=.04):
    low, high = points2.min(axis=0), points2.max(axis=0)
    span = np.maximum(high - low, .02)
    return low - span * padding, high + span * padding


def apply_extent(ax, low, high):
    # Preserve physical aspect without inventing square empty margins.  The
    # same actual projected limits are supplied to every comparison column.
    ax.set_xlim(low[0], high[0])
    ax.set_ylim(low[1], high[1])
    ax.set_aspect("equal", adjustable="box")


def design_lines(ax, report, view):
    for unit in report.get("inventory", {}).get("units", []):
        a = np.asarray(unit["startM"], float)[None, :]
        b = np.asarray(unit["endM"], float)[None, :]
        q = project(np.vstack((a, b)), view)
        ax.plot(q[:, 0], q[:, 1], color="#202124", lw=.55, ls=(0, (3, 2)), alpha=.55, zorder=4)


def panel(ax, run, mode, view, indices, low, high, lines):
    a = run["arrays"]
    indices = np.asarray(indices, dtype=np.int64)
    points = np.asarray(a["positions"][indices], dtype=np.float64)
    base = np.asarray(a["complete_class"][indices]) == 3
    if mode == "baseline":
        kept, ids = base, np.asarray(a["complete_instance"][indices])
        added = deleted = np.zeros(len(indices), bool)
    else:
        prior = np.asarray(a["prior_class"][indices])
        kept, ids = prior == 3, np.asarray(a["prior_instance"][indices])
        added = kept & ~base
        deleted = (prior == 4) & base
    q = project(points, view)
    # Dull context makes sparse steel and action points traceable without hiding them.
    context = ~(kept | deleted)
    if context.any(): ax.scatter(q[context, 0], q[context, 1], s=.12, c="#e5e7eb", alpha=.07, linewidths=0, rasterized=True)
    normal = kept & ~added
    if normal.any(): ax.scatter(q[normal, 0], q[normal, 1], s=.28, c=stable_colors(ids[normal]), alpha=.68, linewidths=0, rasterized=True)
    if added.any(): ax.scatter(q[added, 0], q[added, 1], s=.8, c=ACTION_COLORS["added"], alpha=.9, linewidths=0, rasterized=True, label="source-added")
    if deleted.any(): ax.scatter(q[deleted, 0], q[deleted, 1], s=.9, c=ACTION_COLORS["deleted"], alpha=.9, linewidths=0, rasterized=True, label="source-deleted")
    if lines: design_lines(ax, run["report"], view)
    apply_extent(ax, low, high)
    _, _, xl, yl = VIEWS[view]
    ax.set_xlabel(xl, fontsize=8); ax.set_ylabel(yl, fontsize=8)
    ax.grid(color="#e5e7eb", lw=.3); ax.tick_params(labelsize=7)
    return {"points": len(indices), "kept": int(kept.sum()), "added": int(added.sum()), "deleted": int(deleted.sum())}


def fixed_stride_indices(n, target):
    step = max(1, int(np.ceil(n / target)))
    return np.arange(0, n, step, dtype=np.int64)


def render_comparison(output, name, runs, view, indices, lines=True, title=None):
    all_points = np.concatenate([np.asarray(r["arrays"]["positions"][indices], float) for r in runs])
    low, high = extent_from(project(all_points, view))
    fig, axes = plt.subplots(1, 3, figsize=(18, 3.8), constrained_layout=True)
    stats = []
    for ax, run, mode, label in zip(axes, runs, ("baseline", "prior", "prior"), PANEL_NAMES):
        stat = panel(ax, run, mode, view, indices, low, high, lines)
        ax.set_title(f"{label}\n{run['id']}", fontsize=10)
        stats.append(stat)
    legend = [Line2D([], [], marker="o", color="w", markerfacecolor="#475569", markersize=5, label="retained (stable instance hue)"),
              Line2D([], [], marker="o", color="w", markerfacecolor=ACTION_COLORS["added"], markersize=5, label="source-added"),
              Line2D([], [], marker="o", color="w", markerfacecolor=ACTION_COLORS["deleted"], markersize=5, label="source-deleted")]
    if lines: legend.append(Line2D([], [], color="#202124", lw=1, ls=(0, (3, 2)), label="design unit"))
    fig.legend(handles=legend, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle(title or f"Aligned orthographic {view} comparison", fontsize=12)
    path = output / f"{name}-{view}.png"; fig.savefig(path, dpi=190); plt.close(fig)
    return {"file": path.name, "view": view, "stats": stats}


def roi_indices(run, lo, hi):
    p = run["arrays"]["positions"]
    # Chunk reads retain all small clusters but never duplicate or mutate source data.
    selected = []
    for start in range(0, len(p), 262144):
        xyz = p[start:start + 262144]
        inside = np.all((xyz >= lo) & (xyz <= hi), axis=1)
        selected.append(np.flatnonzero(inside) + start)
    return np.concatenate(selected)


def box(center, radius): return np.asarray(center) - radius, np.asarray(center) + radius


def review_regions(report):
    regions = []
    shorts = [u for u in report.get("inventory", {}).get("units", []) if u.get("kind") == "short"][:12]
    for i, unit in enumerate(shorts, 1):
        lo = np.minimum(unit["startM"], unit["endM"]) - .35; hi = np.maximum(unit["startM"], unit["endM"]) + .35
        regions.append((f"short-{i:02d}", np.asarray(lo), np.asarray(hi)))
    def component_box(component, padding=.10):
        endpoints = np.vstack((component["startM"], component["endM"], component["centerM"]))
        return endpoints.min(axis=0) - padding, endpoints.max(axis=0) + padding
    components = report.get("components", [])
    crossing = [c for c in components if c.get("kind") == "internal" and c.get("family") not in (3, 4)]
    # The inventory represents each diagonal as a straight unit; component
    # centres identify the observed web breakpoints even when older reports
    # called every inventory segment ``straight`` rather than ``web``.
    for i, component in enumerate(sorted((c for c in components if c.get("family") == 4), key=lambda c: (-c["pointCount"], c["id"]))[:4], 1):
        regions.append((f"web-breakpoint-{i:02d}", *component_box(component)))
    for i, component in enumerate(sorted(crossing, key=lambda c: (-c["pointCount"], c["id"]))[:4], 1):
        regions.append((f"dense-crossing-{i:02d}", *component_box(component, .12)))
    added = [c for c in components if c.get("action", 0) & 2]
    for i, component in enumerate(sorted(added, key=lambda c: (-c["pointCount"], c["id"]))[:6], 1):
        regions.append((f"source-added-{i:02d}", *component_box(component, .08)))
    return regions


def render_link_detail(output, run, pair, link, number):
    ends=[np.asarray([c['startM'],c['endM']]) for c in pair]
    _,a,b=min(((np.linalg.norm(x-y),x,y) for x in ends[0] for y in ends[1]),key=lambda item:item[0])
    lo=np.minimum(a,b)-np.array([.08,.045,.045]);hi=np.maximum(a,b)+np.array([.08,.045,.045])
    rows=roi_indices(run,lo,hi)
    points=np.asarray(run['arrays']['positions'][rows],float)
    classes=np.asarray(run['arrays']['complete_class'][rows])
    ids=np.asarray(run['arrays']['complete_instance'][rows])
    owners=[c['originalInstanceId'] for c in pair]
    highlighted=(classes==3)&np.isin(ids,owners)
    context=(classes==3)&~highlighted
    fig,axes=plt.subplots(3,2,figsize=(12,8),constrained_layout=True)
    for row,view in enumerate(('xy-top','xz-side','yz-end')):
        q=project(points,view);low,high=extent_from(q)
        for col in range(2):
            ax=axes[row,col]
            ax.scatter(q[context,0],q[context,1],s=.55,c='#999999',alpha=.35,linewidths=0,rasterized=True)
            for owner,color in zip(owners,('#006cb7','#ed7d22')):
                selected=highlighted&(ids==owner)
                ax.scatter(q[selected,0],q[selected,1],s=1.6,c=color if col==0 else '#009e73',alpha=.85,linewidths=0,rasterized=True)
            apply_extent(ax,low,high)
            ax.set_xlabel(VIEWS[view][2]);ax.set_ylabel(VIEWS[view][3]);ax.grid(color='#e5e7eb',lw=.3)
            ax.set_title(f'{view}: '+(f'original {owners[0]} (blue), {owners[1]} (orange)' if col==0 else f'joined instance {pair[0]["instanceId"]} (green)'))
    fig.suptitle(f'Join {number}: measured endpoint detail; all {len(rows):,} ROI points; other retained rods gray')
    path=output/f'merge-{number:02d}-detail.png';fig.savefig(path,dpi=180);plt.close(fig)
    return {'file':path.name,'merge':link,'points':len(rows),'minM':lo.tolist(),'maxM':hi.tolist(),'view':'XY/XZ/YZ endpoint detail'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--old", required=True, type=Path, help="Earlier prior-enhanced run")
    parser.add_argument("--result", required=True, type=Path, help="Current/corrected prior-enhanced run")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--global-points", type=int, default=350000)
    parser.add_argument("--no-design-lines", action="store_true")
    parser.add_argument('--merges-only',action='store_true')
    args = parser.parse_args()
    if args.global_points < 1: parser.error('--global-points must be positive')
    runs = [load_run(x) for x in (args.baseline, args.old, args.result)]
    source_hashes = {r["manifest"].get("source", {}).get("sha256") for r in runs}
    if len(source_hashes) != 1: raise SystemExit("runs do not share one source LAS identity")
    n = len(runs[0]["arrays"]["positions"])
    if any(len(r["arrays"]["positions"]) != n for r in runs): raise SystemExit("runs have different source row counts")
    args.output.mkdir(parents=True, exist_ok=True)
    indices = fixed_stride_indices(n, args.global_points)
    written = [] if args.merges_only else [render_comparison(args.output, "global", runs, view, indices, not args.no_design_lines) for view in VIEWS]
    regions=review_regions(runs[2]['report'])
    regions += [(f'old-{name}',lo,hi) for name,lo,hi in review_regions(runs[1]['report']) if name.startswith('source-added')]
    for name, lo, hi in ([] if args.merges_only else regions):
        local = roi_indices(runs[2], lo, hi)
        if len(local): written.append(render_comparison(args.output, name, runs, "xy-top", local, not args.no_design_lines, f"Close-up: {name} (full ROI points)"))
    # Every reported merge receives all-point native-coordinate views.  The
    # left panel uses frozen original instance IDs; the right uses result IDs.
    components = {c["id"]: c for c in runs[2]["report"].get("components", [])}
    for number, link in enumerate(runs[2]["report"].get("links", []), 1):
        pair = [components[link["fromComponentId"]], components[link["toComponentId"]]]
        written.append(render_link_detail(args.output,runs[2],pair,link,number))
        endpoints = np.vstack([p[k] for p in pair for k in ("startM", "endM")])
        lo, hi = endpoints.min(0) - .12, endpoints.max(0) + .12
        local = roi_indices(runs[2], lo, hi)
        for view in VIEWS:
            q = project(np.asarray(runs[2]["arrays"]["positions"][local], float), view); low, high = extent_from(q)
            fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
            base_ids = np.asarray(runs[2]["arrays"]["complete_instance"][local]); result_ids = np.asarray(runs[2]["arrays"]["prior_instance"][local])
            base = np.asarray(runs[2]["arrays"]["complete_class"][local]) == 3; result = np.asarray(runs[2]["arrays"]["prior_class"][local]) == 3
            for ax, mask, ids, title in zip(axes, (base, result), (base_ids, result_ids), ("Baseline original instance IDs", "Result instance IDs")):
                ax.scatter(q[mask,0], q[mask,1], s=.35, c=stable_colors(ids[mask]), linewidths=0, rasterized=True)
                if not args.no_design_lines: design_lines(ax, runs[2]["report"], view)
                apply_extent(ax, low, high); ax.set_title(title, fontsize=10); ax.set_aspect("equal")
                ax.set_xlabel(VIEWS[view][2]); ax.set_ylabel(VIEWS[view][3]); ax.grid(color="#e5e7eb", lw=.3)
            gap = link["measuredEndpointGapM"] * 1000
            fig.suptitle(f"Merge {number}: {pair[0]['originalInstanceId']} + {pair[1]['originalInstanceId']} — gap {gap:.1f} mm — full ROI points")
            path = args.output / f"merge-{number:02d}-{view}.png"; fig.savefig(path, dpi=190); plt.close(fig)
            written.append({"file": path.name, "view": view, "merge": link, "points": int(len(local))})
    manifest = {"schema": "design-prior-views-v1", "sourceSha256": source_hashes.pop(), "runs": [r["id"] for r in runs],
                "globalPointSelection": {"method": "fixed source-row stride", "count": int(len(indices)), "sourceRows": n},
                "localPointSelection": "all source rows inside each axis-aligned ROI", "images": written}
    (args.output / "render-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__": main()
