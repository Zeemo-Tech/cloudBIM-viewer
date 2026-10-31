#!/usr/bin/env python3
"""Render real LAS/source-row denoise evidence as a phone-friendly PNG album.

No services, databases, segmentation, interpolation, or registration are run.
New results require source-labels.npz. Legacy previews are accepted only after
verifying EVERY preview vertex against the fixed source-row linspace contract.

Example:
  .cloudbim/mesh-venv/bin/python scripts/qa/render_denoise_comparison.py \
    --manifest .cloudbim/repair-20261029-three-clouds/datasets.json \
    --output .cloudbim/repair-20261029-three-clouds/figures --scan-id 14
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import colorsys
import hashlib
import html
import io
import json
import math
from pathlib import Path
import platform
import sys
import time

import laspy
import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.font_manager as font_manager
from matplotlib.patches import Patch
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


SCHEMA = "source-row-denoise-phone-album-v1"
CLASS_KEYS = ("unknown", "table", "fixture", "steel", "noise", "embedded", "pipe")
CLASS_NAMES = ("未知 / 待判", "台面", "夹具", "钢筋", "显式噪声", "预埋件", "管件")
CLASS_COLORS = ("#A0A7B2", "#BF986A", "#F2AA38", "#36C9F2", "#F15B82", "#39D6A3", "#C397F8")
BACKGROUND = "#101827"
FOREGROUND = "#EDF2FA"
GRID = "#3B4759"
FONT = None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_write(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(path: Path) -> dict:
    s = path.stat()
    return {"size": s.st_size, "mtimeNs": s.st_mtime_ns}


def evenly_spaced(rows: np.ndarray, limit: int) -> np.ndarray:
    if len(rows) <= limit:
        return rows.copy()
    return rows[np.linspace(0, len(rows) - 1, limit, dtype=np.int64)]


def row_digest(rows: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(rows, dtype="<i8").tobytes()).hexdigest()


def load_preview(path: Path) -> np.ndarray:
    """Read scalar binary PLY vertices without optional Open3D/plyfile imports."""
    types = {"char": "i1", "uchar": "u1", "int8": "i1", "uint8": "u1", "short": "i2",
             "ushort": "u2", "int16": "i2", "uint16": "u2", "int": "i4", "uint": "u4",
             "int32": "i4", "uint32": "u4", "float": "f4", "float32": "f4", "double": "f8"}
    with path.open("rb") as stream:
        if stream.readline().strip() != b"ply":
            raise ValueError(f"Not a PLY file: {path}")
        fields, count, in_vertex, endian = [], None, False, None
        for _ in range(200):
            line = stream.readline().decode("ascii").strip()
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "format":
                endian = {"binary_little_endian": "<", "binary_big_endian": ">"}.get(parts[1])
                if endian is None:
                    raise ValueError("Legacy preview must use scalar binary PLY")
            elif parts[0] == "element":
                in_vertex = parts[1] == "vertex"
                if in_vertex:
                    count = int(parts[2])
            elif parts[0] == "property" and in_vertex:
                if len(parts) != 3 or parts[1] not in types:
                    raise ValueError("Unsupported preview vertex property")
                fields.append((parts[2], endian + types[parts[1]]))
            elif line == "end_header":
                break
        else:
            raise ValueError("PLY header is incomplete")
        if count is None or endian is None:
            raise ValueError("Missing preview vertex count/format")
        vertices = np.fromfile(stream, dtype=np.dtype(fields), count=count)
        if len(vertices) != count or not {"x", "y", "z", "label", "instance"}.issubset(vertices.dtype.names):
            raise ValueError("Incomplete preview labels/vertices")
    return vertices


@dataclass
class Labels:
    path: Path
    rows: np.ndarray
    classes: np.ndarray
    instances: np.ndarray
    manifest: dict
    instance_map: dict
    evidence: dict

    @property
    def full(self) -> bool:
        return self.evidence["coverage"] == "all-source-rows"

    def at(self, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.full:
            return self.classes[rows], self.instances[rows]
        positions = np.searchsorted(self.rows, rows)
        if np.any(positions >= len(self.rows)) or not np.array_equal(self.rows[positions], rows):
            raise ValueError("Attempted to invent a label outside verified baseline source rows")
        return self.classes[positions], self.instances[positions]


def load_labels(path: Path, xyz: np.ndarray, source_sha: str, *, require_full: bool) -> Labels:
    manifest = read_json(path / "manifest.json")
    if manifest.get("pointsBefore") != len(xyz):
        raise ValueError(f"Source point count mismatch: {path}")
    if manifest.get("sourceSha256") != source_sha:
        raise ValueError(f"Source SHA256 mismatch/missing: {path}")
    instance_map = read_json(path / "instance-map.json")
    npz = path / "source-labels.npz"
    evidence = {"resultDirectory": str(path), "resultVersion": path.name,
                "algorithmVersion": manifest.get("algorithmVersion"),
                "manifestSha256": sha256(path / "manifest.json"),
                "instanceMapSha256": sha256(path / "instance-map.json")}
    if npz.is_file():
        with np.load(npz, allow_pickle=False) as z:
            classes, instances = z["classes"].copy(), z["instances"].copy()
        if classes.shape != (len(xyz),) or instances.shape != (len(xyz),):
            raise ValueError(f"NPZ must cover every original LAS record: {npz}")
        rows = np.arange(len(xyz), dtype=np.int64)
        evidence.update(coverage="all-source-rows", labelArtifact=str(npz), labelSha256=sha256(npz),
                        mapping="source-labels.npz row i equals original LAS record i")
    else:
        if require_full:
            raise ValueError(f"New result requires full source-labels.npz; missing: {npz}")
        ply = path / "preview.ply"
        vertices = load_preview(ply)
        if len(vertices) != int(manifest["previewPointCount"]):
            raise ValueError("Preview count differs from manifest")
        rows = np.linspace(0, len(xyz) - 1, len(vertices), dtype=np.int64)
        origin = np.asarray(manifest["previewOrigin"], dtype=float)
        expected = (xyz[rows] - origin).astype(np.float32)
        actual = np.column_stack([vertices[k] for k in ("x", "y", "z")])
        error = np.abs(expected.astype(float) - actual.astype(float))
        # A tiny float32 ULP tolerance accepts equivalent binary writers, never a nearest-neighbour remap.
        tolerance = np.maximum(np.abs(expected), 1.0) * np.finfo(np.float32).eps * 2
        if not np.all(error <= tolerance):
            raise ValueError(f"Legacy preview fixed source-row mapping failed (max {error.max():.9g} m): {ply}")
        classes, instances = vertices["label"].copy(), vertices["instance"].copy()
        evidence.update(coverage="verified-preview-source-rows-only", labelArtifact=str(ply),
                        labelSha256=sha256(ply), verifiedVertices=len(rows),
                        maximumFloat32CoordinateDifferenceM=float(error.max()),
                        mapping="linspace(0,N-1,previewPointCount,dtype=int64), every vertex checked against (LAS XYZ-previewOrigin).astype(float32)",
                        previewOrigin=origin.tolist(), missingRowsPolicy="never inferred or filled as unknown")
    if classes.dtype.kind not in "iu" or instances.dtype.kind not in "iu":
        raise ValueError("Class and instance labels must be integers")
    if np.any(classes > 6) or np.any(classes < 0):
        raise ValueError("Unsupported semantic labels; expected the explicit 0..6 class contract")
    if evidence["coverage"] == "all-source-rows":
        counts = np.bincount(classes, minlength=7)
        for k, n in zip(CLASS_KEYS, counts):
            if int(manifest.get("counts", {}).get(k, 0)) != int(n):
                raise ValueError(f"Full-source class count differs from manifest for {k}: {path}")
    evidence["labeledRows"] = len(rows)
    evidence["classCountsOnLabeledRows"] = dict(zip(CLASS_KEYS, map(int, np.bincount(classes, minlength=7))))
    return Labels(path, rows, classes, instances, manifest, instance_map, evidence)


def parent_maps(results: tuple[Labels, Labels]) -> tuple[dict[int, str], dict[int, str], dict[str, dict]]:
    maps, parents = [], {}
    for result in results:
        bars = {str(b["designBarId"]): b for b in result.instance_map.get("inventory", {}).get("bars", [])}
        mapping = {}
        for i in result.instance_map["instances"]:
            bar_id = str(i["designBarId"])
            bar = bars.get(bar_id, {})
            key = str(bar.get("ifcGlobalId") or bar_id.split(":", 1)[0])
            mapping[int(i["id"])] = key
            if key not in parents:
                digest = hashlib.sha256(key.encode()).digest()
                rgb = colorsys.hsv_to_rgb(int.from_bytes(digest[:4], "big") / 2**32,
                                         .52 + digest[4] / 255 * .22, .91 + digest[5] / 255 * .09)
                parents[key] = {"code": "P-" + hashlib.sha256(key.encode()).hexdigest()[:5],
                                "color": mcolors.to_hex(rgb), "name": bar.get("name", ""),
                                "designBarIds": set(), "baselineInstances": [], "newInstances": []}
            parents[key]["designBarIds"].add(bar_id)
            parents[key]["baselineInstances" if len(maps) == 0 else "newInstances"].append(int(i["id"]))
        unmapped = set(map(int, np.unique(result.instances[result.classes == 3]))) - {0} - set(mapping)
        if unmapped:
            raise ValueError(f"Steel labels missing physical-parent metadata: {sorted(unmapped)}")
        maps.append(mapping)
    for p in parents.values():
        p["designBarIds"] = sorted(p["designBarIds"])
    return maps[0], maps[1], parents


def point_colors(classes: np.ndarray, instances: np.ndarray, mapping: dict, parents: dict, mode: str) -> np.ndarray:
    colors = np.asarray([mcolors.to_rgb(c) for c in CLASS_COLORS], dtype=np.float32)[classes].copy()
    if mode == "parents":
        for instance in np.unique(instances[classes == 3]):
            mask = (instances == instance) & (classes == 3)
            colors[mask] = mcolors.to_rgb(parents[mapping[int(instance)]]["color"]) if instance else (1., 1., 1.)
    return colors


def camera(name: str, origin: np.ndarray | None = None, right: np.ndarray | None = None,
           up: np.ndarray | None = None) -> dict:
    if right is None:
        if name == "top":
            right, up = np.array([1., 0., 0.]), np.array([0., 1., 0.])
        else:
            a, e = np.deg2rad(18), np.deg2rad(27)
            right = np.array([np.cos(a), np.sin(a), 0.])
            up = np.array([-np.sin(a) * np.sin(e), np.cos(a) * np.sin(e), np.cos(e)])
    right, up = np.asarray(right, float), np.asarray(up, float)
    right /= np.linalg.norm(right)
    up = up - np.dot(up, right) * right
    up /= np.linalg.norm(up)
    basis = np.column_stack((right, up, np.cross(right, up)))
    if not np.allclose(basis.T @ basis, np.eye(3), atol=1e-10) or np.linalg.det(basis) < .999999:
        raise ValueError("Camera must be an orthonormal right-handed projection")
    return {"name": name, "origin": np.zeros(3) if origin is None else np.asarray(origin), "basis": basis}


def project(xyz: np.ndarray, cam: dict) -> np.ndarray:
    return (xyz - cam["origin"]) @ cam["basis"]


def projection_limits(points: np.ndarray, pad: float = .035) -> list[float]:
    lo, hi = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    span = np.maximum(hi - lo, .01)
    lo, hi = lo - span * pad, hi + span * pad
    return [float(lo[0]), float(hi[0]), float(lo[1]), float(hi[1])]


def scale_bar(ax, limits: list[float]) -> float:
    width = limits[1] - limits[0]
    preferred = width * .18
    exponent = 10 ** math.floor(math.log10(preferred))
    length = max(v for v in (1, 2, 5, 10) if v * exponent <= preferred) * exponent
    x = limits[0] + width * .055
    y = limits[2] + (limits[3] - limits[2]) * .055
    ax.plot([x, x + length], [y, y], color="white", linewidth=3, zorder=8)
    ax.text(x + length / 2, y, f"{length:g} m", ha="center", va="bottom", color="white", fontsize=11,
            bbox={"facecolor": BACKGROUND, "edgecolor": "none", "alpha": .85, "pad": 2}, zorder=9)
    return float(length)


def axes_style(ax, limits: list[float], cam: dict, show_scale: bool = True) -> None:
    ax.set_facecolor(BACKGROUND)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=FOREGROUND, labelsize=10)
    ax.set_xlim(limits[:2]); ax.set_ylim(limits[2:])
    ax.set_aspect("equal", adjustable="box")
    ax.grid(color=GRID, alpha=.4, linewidth=.45)
    if cam["name"] == "top":
        ax.set_xlabel("原 LAS X（m）", color=FOREGROUND)
        ax.set_ylabel("原 LAS Y（m）", color=FOREGROUND)
    elif cam["name"] == "local-baseline-boundary":
        ax.set_xlabel("局部原 LAS X（m）", color=FOREGROUND)
        ax.set_ylabel("局部原 LAS Y（m）", color=FOREGROUND)
    elif cam["name"].startswith("local"):
        ax.set_xlabel("基线轴向 s（m）", color=FOREGROUND)
        ax.set_ylabel("局部横向 t（m）", color=FOREGROUND)
    else:
        ax.set_xlabel("正交投影 U（m）", color=FOREGROUND)
        ax.set_ylabel("正交投影 V（m）", color=FOREGROUND)
    if show_scale:
        scale_bar(ax, limits)


def draw(ax, projected: np.ndarray, classes: np.ndarray, instances: np.ndarray, mapping: dict,
         parents: dict, mode: str, point_size: float, limits: list[float], cam: dict) -> None:
    # Depth order controls occlusion only; source XYZ and semantic labels are untouched.
    order = np.argsort(projected[:, 2], kind="stable")
    colors = point_colors(classes, instances, mapping, parents, mode)
    ax.scatter(projected[order, 0], projected[order, 1], c=colors[order], s=point_size,
               marker="o", linewidths=0, alpha=1, rasterized=True, zorder=3)
    axes_style(ax, limits, cam)


def semantic_legend(fig, *, parents: dict | None = None, target: str | None = None) -> None:
    patches = [Patch(facecolor=c, edgecolor="none", label=n) for n, c in zip(CLASS_NAMES, CLASS_COLORS)]
    if parents is not None:
        patches[3] = Patch(facecolor=CLASS_COLORS[3], label="钢筋：按物理父构件配色")
        if target and target in parents:
            patches.append(Patch(facecolor=parents[target]["color"], label=f"目标 {parents[target]['code']}"))
    legend = fig.legend(handles=patches, loc="lower center", bbox_to_anchor=(.50, .05), ncol=4,
                        frameon=False, fontsize=11, handlelength=1.6, handleheight=1.2,
                        labelcolor=FOREGROUND, columnspacing=1.0)
    for t in legend.get_texts():
        t.set_fontproperties(FONT)
        t.set_fontsize(11)


def save_png(fig, path: Path) -> dict:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=155, facecolor=BACKGROUND, transparent=False,
                metadata={"Software": SCHEMA, "Description": "Real source LAS coordinates and verified source-row labels"})
    plt.close(fig)
    buf.seek(0)
    image = Image.open(buf).convert("RGB")
    image.save(path, optimize=True, compress_level=9)
    encoding = "RGB PNG, lossless"
    if path.stat().st_size > 1_100_000:
        image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
        encoding = "256-color PNG palette quantization; no geometry/label change, no dithering"
    with Image.open(path) as saved:
        saved.verify()
    return {"file": path.name, "width": image.width, "height": image.height,
            "bytes": path.stat().st_size, "sha256": sha256(path), "encoding": encoding}


def serialize_camera(cam: dict, limits: list[float]) -> dict:
    return {"name": cam["name"], "type": "orthographic", "originM": cam["origin"].tolist(),
            "columnsRightUpTowardViewer": cam["basis"].tolist(), "projectedBoundsM": limits,
            "mapping": "projected=(originalLAS_XYZ-originM) @ basis; sort by depth only for occlusion",
            "scale": "orthonormal basis; 1 projected meter equals 1 source meter; no vertical exaggeration"}


def render_single(output: Path, key: str, title: str, caption: str, xyz: np.ndarray, rows: np.ndarray,
                  result: Labels, mapping: dict, parents: dict, cam: dict, *, mode: str = "semantic",
                  steel_only: bool = False, limits: list[float] | None = None, point_size: float = .7) -> dict:
    classes, instances = result.at(rows)
    if steel_only:
        keep = classes == 3
        rows, classes, instances = rows[keep], classes[keep], instances[keep]
    if not len(rows):
        raise ValueError(f"No real points to render: {key}")
    projected = project(xyz[rows], cam)
    limits = projection_limits(projected) if limits is None else limits
    fig, ax = plt.subplots(figsize=(8.4, 6.5), facecolor=BACKGROUND)
    fig.subplots_adjust(left=.10, right=.97, top=.86, bottom=.26)
    fig.suptitle(title, color=FOREGROUND, fontsize=19, y=.97, fontweight="bold")
    fig.text(.5, .90, caption, color=FOREGROUND, ha="center", fontsize=10)
    draw(ax, projected, classes, instances, mapping, parents, mode, point_size, limits, cam)
    if not steel_only:
        semantic_legend(fig, parents=parents if mode == "parents" else None)
    else:
        by_parent = Counter(mapping.get(int(i), "unassigned") for i in instances)
        selected = sorted(by_parent, key=lambda p: (-by_parent[p], p))[:6]
        patches = [Patch(facecolor=parents[p]["color"] if p in parents else "white",
                         label=parents[p]["code"] if p in parents else "钢筋无实例号") for p in selected]
        fig.legend(handles=patches, loc="lower center", bbox_to_anchor=(.5, .075), ncol=3,
                   frameon=False, labelcolor=FOREGROUND, fontsize=11, handleheight=1.2)
        fig.text(.5, .035, "同一 IFC 物理父构件跨版本同色；图例列主要父构件，完整映射见 parent-colors.json。",
                 ha="center", color=FOREGROUND, fontsize=9)
    result_meta = save_png(fig, output / f"{key}.png")
    return dict(result_meta, title=title, caption=caption, sourceRows=len(rows), rowsSha256=row_digest(rows),
                semanticCounts=dict(zip(CLASS_KEYS, map(int, np.bincount(classes, minlength=7)))),
                camera=serialize_camera(cam, limits), displayMode=mode, steelOnly=steel_only)


def render_pair(output: Path, key: str, title: str, caption: str, xyz: np.ndarray, rows: np.ndarray,
                baseline: Labels, new: Labels, maps: tuple[dict, dict], parents: dict, cam: dict,
                *, mode: str = "semantic", target: str | None = None, point_size: float = .8,
                limits: list[float] | None = None) -> dict:
    if not len(rows):
        raise ValueError(f"No shared labeled source rows for comparison: {key}")
    projected = project(xyz[rows], cam)
    limits = projection_limits(projected, .055) if limits is None else limits
    fig, axes = plt.subplots(2, 1, figsize=(8.4, 10.4), facecolor=BACKGROUND)
    fig.subplots_adjust(left=.10, right=.97, top=.87, bottom=.18, hspace=.40)
    fig.suptitle(title, color=FOREGROUND, fontsize=19, y=.97, fontweight="bold")
    fig.text(.5, .91, caption, color=FOREGROUND, ha="center", fontsize=10)
    counts = []
    panel_names = ("基线A（绘图自检）", "基线B（同一结果）") if baseline.path == new.path else ("修复前", "修复后")
    for ax, result, mapping, label in zip(axes, (baseline, new), maps, panel_names):
        classes, instances = result.at(rows)
        draw(ax, projected, classes, instances, mapping, parents, mode, point_size, limits, cam)
        counts.append(dict(zip(CLASS_KEYS, map(int, np.bincount(classes, minlength=7)))))
        target_n = int(np.count_nonzero((classes == 3) & np.isin(instances, [i for i, p in mapping.items() if p == target]))) if target else None
        note = f"钢筋 {np.count_nonzero(classes == 3):,} / 未知 {np.count_nonzero(classes == 0):,}"
        if target:
            note += f" / 目标父构件 {target_n:,}"
        ax.set_title(f"{label}   {note}", loc="left", color=FOREGROUND, fontsize=13, pad=9)
    semantic_legend(fig, parents=parents if mode == "parents" else None, target=target)
    fig.text(.5, .025, "上下使用完全相同的原 LAS 源行、相机、比例与范围；未知点保留，未推断缺失旧标签。",
             color=FOREGROUND, ha="center", fontsize=9)
    result_meta = save_png(fig, output / f"{key}.png")
    return dict(result_meta, title=title, caption=caption, sourceRows=len(rows), rowsSha256=row_digest(rows),
                baselineCounts=counts[0], newCounts=counts[1], camera=serialize_camera(cam, limits),
                displayMode=mode, targetPhysicalParent=target)


def baseline_unit(result: Labels, target: int) -> dict:
    report = result.instance_map.get("controlNet", {}).get("report", {})
    if not report:
        report = read_json(result.path / "control-net.json")["report"]
    found = [i for i in report["instances"] if int(i["id"]) == target]
    if not found:
        raise ValueError(f"Requested baseline instance {target} missing: {result.path}")
    return found[0]


def axis_crop(xyz: np.ndarray, result: Labels, target: int, srange: tuple[float, float],
              half_lateral: float = .15, half_vertical: float = .11) -> tuple[np.ndarray, dict, dict]:
    unit = baseline_unit(result, target)
    line = np.asarray(unit.get("bodyDisplayCenterlineM", unit["centerlineM"]), dtype=float)
    origin = line[0]
    along = line[-1] - line[0]
    length = np.linalg.norm(along)
    along /= length
    lateral = np.cross(np.array([0., 0., 1.]), along)
    if np.linalg.norm(lateral) < 1e-6:
        lateral = np.cross(np.array([0., 1., 0.]), along)
    lateral /= np.linalg.norm(lateral)
    cam = camera("local-baseline-axis", origin, along, lateral)
    p = project(xyz, cam)
    mask = ((p[:, 0] >= srange[0]) & (p[:, 0] <= srange[1]) &
            (np.abs(p[:, 1]) <= half_lateral) & (np.abs(p[:, 2]) <= half_vertical))
    rows = np.flatnonzero(mask)
    spec = {"selection": "predeclared baseline instance axis, independent of repaired classification",
            "baselineInstance": target, "baselineDesignBarId": unit["designBarId"],
            "baselineChordLengthM": float(length), "sRangeM": list(srange),
            "tRangeM": [-half_lateral, half_lateral], "depthRangeM": [-half_vertical, half_vertical],
            "axisReference": "baseline fitted centerline endpoint chord used only as camera/crop, never drawn as measured points",
            "originM": origin.tolist(), "basis": cam["basis"].tolist(), "allSourceRowsInCrop": len(rows)}
    return rows, cam, spec


def boundary_crops(xyz: np.ndarray, baseline: Labels) -> list[tuple[np.ndarray, dict, dict]]:
    """Select baseline mixed steel/unknown cells, without observing new labels."""
    classes, _ = baseline.at(baseline.rows)
    steel = xyz[baseline.rows[classes == 3]]
    if not len(steel):
        raise ValueError("No baseline steel points for representative boundary selection")
    bounds = np.array([steel[:, :2].min(axis=0), steel[:, :2].max(axis=0)])
    side = .24
    eligible = ((classes == 0) | (classes == 3))
    p = xyz[baseline.rows]
    eligible &= np.all((p[:, :2] >= bounds[0] - side) & (p[:, :2] <= bounds[1] + side), axis=1)
    eligible &= np.abs(p[:, 2] - np.median(steel[:, 2])) <= .10
    r = baseline.rows[eligible]; c = classes[eligible]
    cell = np.floor(xyz[r, :2] / side).astype(np.int64)
    keys, inverse = np.unique(cell, axis=0, return_inverse=True)
    n_steel = np.bincount(inverse[c == 3], minlength=len(keys))
    n_unknown = np.bincount(inverse[c == 0], minlength=len(keys))
    ratio = n_steel / np.maximum(n_steel + n_unknown, 1)
    valid = np.flatnonzero((n_steel >= 20) & (n_unknown >= 20) & (ratio >= .15) & (ratio <= .85))
    if len(valid) < 2:
        raise ValueError("Insufficient baseline mixed steel/unknown cells for two honest local crops")
    centers = (keys[valid] + .5) * side
    median = np.median(centers, axis=0)
    first = int(np.argmin(np.linalg.norm(centers - median, axis=1)))
    distance = np.linalg.norm(centers - centers[first], axis=1)
    # The second is the spatial median of the opposite half, not the largest improvement.
    remote = np.flatnonzero(distance >= max(float(np.median(distance)), side * 1.25))
    if not len(remote):
        remote = np.array([int(np.argmax(distance))])
    second = int(remote[np.argmin(np.linalg.norm(centers[remote] - np.median(centers[remote], axis=0), axis=1))])
    out = []
    for candidate in (first, second):
        k = valid[candidate]
        center = centers[candidate]
        member = np.all(cell == keys[k], axis=1)
        z = float(np.median(xyz[r[member & (c == 3)], 2]))
        origin = np.array([center[0], center[1], z])
        cam = camera("local-baseline-boundary", origin, np.array([1., 0., 0.]), np.array([0., 1., 0.]))
        half = .20
        local = xyz - origin
        mask = (np.abs(local[:, 0]) <= half) & (np.abs(local[:, 1]) <= half) & (np.abs(local[:, 2]) <= .12)
        rows = np.flatnonzero(mask)
        spec = {"selection": "baseline-only mixed steel/unknown grid: >=20 each and steel fraction 0.15..0.85; spatial-median candidate then spatially separate opposite-half median",
                "gridSideM": side, "candidateCount": len(valid), "selectedGridCell": keys[k].tolist(),
                "baselineCellSteel": int(n_steel[k]), "baselineCellUnknown": int(n_unknown[k]),
                "originM": origin.tolist(), "basis": cam["basis"].tolist(),
                "sRangeM": [-half, half], "tRangeM": [-half, half], "depthRangeM": [-.12, .12],
                "allSourceRowsInCrop": len(rows), "selectionUsesNewLabels": False}
        out.append((rows, cam, spec))
    return out


def choose_crops(scan_id: str, xyz: np.ndarray, baseline: Labels):
    if scan_id == "5":
        return [(*axis_crop(xyz, baseline, 9, (3., 4.)), "实例9：轴向3–4 m", 9),
                (*axis_crop(xyz, baseline, 9, (3.82, 4.25), .16, .11), "实例9：轴向末端", 9)]
    if scan_id == "14":
        return [(*axis_crop(xyz, baseline, 70, (-.09, .31), .15, .12), "实例70：基线起端", 70),
                (*axis_crop(xyz, baseline, 168, (-.09, .31), .15, .12), "实例168：基线起端", 168)]
    return [(*crop, f"匹配 / 未知边界 {i}", None) for i, crop in enumerate(boundary_crops(xyz, baseline), 1)]


def write_dataset_html(output: Path, name: str, description: str, figures: list[dict]) -> None:
    cards = []
    for figure in figures:
        file = html.escape(figure["file"], quote=True)
        cards.append(f'<article><h2>{html.escape(figure["title"])}</h2><p>{html.escape(figure["caption"])}</p>'
                     f'<a href="{file}" target="_blank"><img src="{file}" loading="lazy" '
                     f'width="{figure["width"]}" height="{figure["height"]}" '
                     f'alt="{html.escape(figure["title"] + "；" + figure["caption"], quote=True)}"></a>'
                     f'<p class="muted">点击查看原图 · {figure["bytes"] / 1024:.0f} KB</p></article>')
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>' + html.escape(name) + '</title><style>'
            'body{margin:0;background:#101827;color:#edf2fa;font:16px/1.65 system-ui,sans-serif}'
            'main{max-width:1040px;margin:auto;padding:20px 12px}h1{font-size:26px}h2{font-size:21px}'
            'a{color:#71d5ff}article{margin:30px 0;padding-top:10px;border-top:1px solid #3b4759}'
            'img{display:block;width:100%;height:auto;border:1px solid #3b4759;box-sizing:border-box}'
            '.muted{color:#b6c1d1;font-size:14px}p{overflow-wrap:anywhere}a:focus{outline:3px solid #f2aa38}'
            '</style></head><body><main><a href="../index.html">全部点云</a><h1>' + html.escape(name) + '</h1><p>'
            + html.escape(description) + '</p><p><a href="provenance.json">数据与绘图追溯</a> · '
            '<a href="parent-colors.json">物理父构件颜色与实例号</a> · '
            '<a href="source-rows.npz">全部绘图源行编号</a></p>' + ''.join(cards) + '</main></body></html>')
    (output / "index.html").write_text(page, encoding="utf-8")


def run_dataset(dataset: dict, output: Path, max_points: int) -> dict:
    start = time.monotonic()
    scan_id = str(dataset.get("scanId", dataset["id"]))
    name = str(dataset["name"])
    if not dataset.get("result"):
        raise ValueError(f"scan {scan_id}: result is null; fill the repaired result directory first")
    output.mkdir(parents=True, exist_ok=True)
    source = Path(dataset["source"]).resolve()
    initial_stamp = stamp(source)
    source_sha = sha256(source)
    with laspy.open(source) as reader:
        las = reader.read()
        xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
        las_metadata = {"pointFormat": reader.header.point_format.id, "version": str(reader.header.version),
                        "scales": reader.header.scales.tolist(), "offsets": reader.header.offsets.tolist(),
                        "crs": str(reader.header.parse_crs()), "pointCount": len(xyz),
                        "boundsM": [reader.header.mins.tolist(), reader.header.maxs.tolist()]}
    del las
    if not np.isfinite(xyz).all():
        raise ValueError("Nonfinite LAS coordinates require an explicit separate policy")
    baseline = load_labels(Path(dataset["baselineResult"]).resolve(), xyz, source_sha, require_full=False)
    new = load_labels(Path(dataset["result"]).resolve(), xyz, source_sha, require_full=True)
    same_result_demo = baseline.path == new.path
    state_name = "同一基线绘图自检" if same_result_demo else "修复后"
    before_map, new_map, parents = parent_maps((baseline, new))
    maps = (before_map, new_map)
    all_rows = new.rows
    overview_rows = evenly_spaced(all_rows, max_points)
    compare_rows = evenly_spaced(baseline.rows, max_points)
    steel_rows = evenly_spaced(np.flatnonzero(new.classes == 3), max_points)
    samples = {"overview": overview_rows, "comparison": compare_rows, "steel_only": steel_rows}
    figures = []
    top, oblique = camera("top"), camera("oblique")
    for key, cam, view in (("01-semantic-top", top, "原坐标俯视"), ("02-semantic-oblique", oblique, "斜视")):
        bounds = projection_limits(project(xyz, cam))
        figures.append(render_single(output, key, f"{name} · {state_name}整体语义", f"{view} · 原始源点 {len(xyz):,}，等源行抽样 {len(overview_rows):,} · 单位 m",
                                     xyz, overview_rows, new, new_map, parents, cam, limits=bounds))
    figures.append(render_single(output, "03-physical-parents", f"{name} · 钢筋物理实例", "仅绘钢筋，按 IFC 物理父构件固定配色；弯钩 / 直段单元共享父构件颜色。",
                                 xyz, steel_rows, new, new_map, parents, oblique, mode="parents", steel_only=True, point_size=.9))
    comparison_title = "整体同一基线绘图自检" if same_result_demo else "整体修复前后"
    figures.append(render_pair(output, "04-before-after", f"{name} · {comparison_title}", f"相同源行 {len(compare_rows):,} / {len(xyz):,} · 相同俯视范围 · 标签覆盖：{'全源' if baseline.full else '仅核验后的旧预览源行'}",
                               xyz, compare_rows, baseline, new, maps, parents, top, limits=projection_limits(project(xyz, top))))
    crop_specs = []
    # Keep the full context above, but add a labelled specimen view when remote
    # scan fragments make the actual steel assembly too small to inspect.
    old_steel_rows = baseline.rows[baseline.classes == 3]
    specimen_limits = projection_limits(project(xyz[old_steel_rows], top), pad=.08)
    specimen_area = (specimen_limits[1]-specimen_limits[0])*(specimen_limits[3]-specimen_limits[2])
    if np.prod(np.ptp(xyz[:, :2], axis=0)) > 4*specimen_area:
        inside = ((xyz[:, 0] >= specimen_limits[0]) & (xyz[:, 0] <= specimen_limits[1]) &
                  (xyz[:, 1] >= specimen_limits[2]) & (xyz[:, 1] <= specimen_limits[3]))
        specimen_rows = np.flatnonzero(inside)
        shared = np.intersect1d(specimen_rows, baseline.rows, assume_unique=True)
        shown = evenly_spaced(shared, max_points)
        samples['specimen_all_source'] = specimen_rows
        samples['specimen_comparison'] = shown
        crop_specs.append({'kind': 'specimen-overview', 'selection': 'baseline steel XY bounds padded by 8%; all classes and full depth retained',
                           'selectionUsesNewLabels': False, 'xyLimitsM': specimen_limits,
                           'allSourceRowsInCrop': len(specimen_rows), 'commonLabeledRows': len(shared),
                           'drawnRows': len(shown), 'comparisonSampling': 'linspace over shared source rows'})
        figures.append(render_pair(output, '04b-workpiece-before-after', f'{name} · 试件范围修复前后',
            f'裁剪：基线钢筋包围框外扩 8% · 保留框内全部类别和深度 · 同源行 {len(shown):,}',
            xyz, shown, baseline, new, maps, parents, top, limits=specimen_limits, point_size=.8))
    for i, (local_rows, cam, spec, local_name, target) in enumerate(choose_crops(scan_id, xyz, baseline), 1):
        if not len(local_rows):
            raise ValueError(f"Requested local crop has no original source points: {local_name}")
        common_rows = local_rows if baseline.full else np.intersect1d(local_rows, baseline.rows, assume_unique=True)
        samples[f"local{i}_all_source"] = local_rows
        samples[f"local{i}_comparison"] = common_rows
        spec["commonLabeledRows"] = len(common_rows)
        spec["comparisonSampling"] = "all shared labeled rows in the crop; no subsampling"
        crop_specs.append(spec)
        limits = [*spec["sRangeM"], *spec["tRangeM"]]
        parent = before_map.get(target) if target else None
        figures.append(render_pair(output, f"0{4 + i}-local-{i}", f"{name} · {local_name}",
                                   f"裁剪内原源点 {len(local_rows):,}，同源行对照 {len(common_rows):,} · 局部全标签行绘制 · 单位 m",
                                   xyz, common_rows, baseline, new, maps, parents, cam,
                                   mode="parents", target=parent, point_size=1.1, limits=limits))
        # A separate full-source repaired view is essential when the legacy result is only a preview.
        if not baseline.full:
            figures.append(render_single(output, f"07-local-{i}-full-source", f"{name} · {local_name}全源细节",
                                         f"修复后裁剪内全部 {len(local_rows):,} 个原 LAS 点；包含旧预览未覆盖的源行。",
                                         xyz, local_rows, new, new_map, parents, cam, mode="parents", limits=limits, point_size=1.1))
    np.savez_compressed(output / "source-rows.npz", **samples)
    json_write(output / "parent-colors.json", parents)
    if stamp(source) != initial_stamp:
        raise ValueError("Original source changed while rendering; figures must not be accepted")
    provenance = {"schema": SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "scanId": scan_id,
                  "comparisonIsSameResultDemo": same_result_demo,
                  "name": name, "source": {"path": str(source), "sha256": source_sha, **initial_stamp, **las_metadata},
                  "coordinatePolicy": {"frame": "original source LAS XYZ", "units": "meters, matching mesh-service source-space contract; LAS CRS is recorded separately",
                                       "savedAlignmentMatrixAsSupplied": dataset.get("transform"), "alignmentAppliedToPoints": False,
                                       "alignmentExplanation": "The saved matrix is retained verbatim only. No BIM is overlaid, so no alignment/axis flip/unit conversion is applied. Camera projections are orthonormal and separately recorded.",
                                       "geometryOperations": "orthographic projection/crop/depth ordering only; no smoothing, interpolation, point generation, coordinate edits, or re-registration"},
                  "baseline": baseline.evidence, "new": new.evidence, "samples": {k: {"count": len(v), "sha256Int64LittleEndian": row_digest(v)} for k, v in samples.items()},
                  "sampling": {"seed": None, "randomSampling": False, "overviewMethod": "linspace indices over original source row order",
                               "comparisonMethod": "linspace subset of baseline verified labeled source rows, identically used in both panels",
                               "steelMethod": "linspace subset of new class=3 original source rows; all nonsteel excluded only in explicitly titled steel-only figure",
                               "maxOverviewPoints": max_points, "sourceRowsFile": "source-rows.npz", "sourceRowsFileSha256": sha256(output / "source-rows.npz")},
                  "localCrops": crop_specs, "classContract": [{"id": i, "key": k, "nameZh": n, "color": c} for i, (k, n, c) in enumerate(zip(CLASS_KEYS, CLASS_NAMES, CLASS_COLORS))],
                  "physicalParentColorPolicy": "SHA256(IFC GlobalId) -> fixed HSV color, same physical parent across matching units and result versions; close colors remain possible, use parent map/target counts",
                  "figures": figures, "runtimeSeconds": time.monotonic() - start,
                  "runtime": {"python": platform.python_version(), "numpy": np.__version__, "matplotlib": matplotlib.__version__, "laspy": laspy.__version__},
                  "script": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
                  "interpretation": "A real-data rendering for visual inspection, not independent ground truth or proof that every colored point is correctly classified; unknown is retained and never relabeled noise for display."}
    json_write(output / "provenance.json", provenance)
    description = (f"原 LAS {len(xyz):,} 点；新结果全源标签。整体图按原源行等距抽样，局部不抽样。"
                   + ("旧结果也有全源标签。" if baseline.full else "旧结果只有预览标签，逐点核验源行后，前后对照只用共同标签行；另附修复后全源局部。")
                   + "所有图使用原坐标，单位 m，无垂直夸张；保存的对齐矩阵未应用，未叠加 BIM。未知保留灰色。")
    if same_result_demo:
        description = "绘图器自检：前后使用同一个基线结果，不能作为修复效果证据。" + description
    write_dataset_html(output, name, description, figures)
    print(json.dumps({"scanId": scan_id, "figures": len(figures), "output": str(output), "seconds": provenance["runtimeSeconds"]}, ensure_ascii=False), flush=True)
    return {"id": scan_id, "name": name, "path": f"scan-{scan_id}/index.html", "figureCount": len(figures),
            "sourceSha256": source_sha, "baselineCoverage": baseline.evidence["coverage"], "result": str(new.path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scan-id", action="append", help="Select scan ID; repeat for more than one scan")
    parser.add_argument("--max-points", type=int, default=240_000, help="Only the overall figures are sampled; local comparisons retain every shared source row")
    args = parser.parse_args()
    if args.max_points < 1000:
        parser.error("--max-points must be at least 1000")
    global FONT
    choices = [f for f in font_manager.fontManager.ttflist if f.name == "Noto Sans CJK JP" and "Regular" in f.fname]
    if not choices:
        raise ValueError("A CJK font is required for readable Chinese legends (Noto Sans CJK)")
    FONT = font_manager.FontProperties(fname=choices[0].fname)
    plt.rcParams.update({"font.family": FONT.get_name(), "font.size": 11, "axes.unicode_minus": False,
                         "text.color": FOREGROUND, "axes.labelcolor": FOREGROUND, "savefig.facecolor": BACKGROUND})
    manifest = read_json(args.manifest)
    datasets = manifest["datasets"]
    if args.scan_id:
        wanted = set(args.scan_id)
        datasets = [d for d in datasets if str(d.get("scanId", d["id"])) in wanted]
        absent = wanted - {str(d.get("scanId", d["id"])) for d in datasets}
        if absent:
            raise ValueError(f"Scan IDs missing from manifest: {sorted(absent)}")
    if not datasets:
        raise ValueError("No datasets selected")
    # Fail before writing any figures if a selected new result is not ready.
    for d in datasets:
        if not d.get("result"):
            raise ValueError(f"scan {d['id']}: result is null; complete the repaired-result manifest first")
        if not (Path(d["result"]) / "source-labels.npz").is_file():
            raise ValueError(f"scan {d['id']}: new result must contain full source-labels.npz")
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = [run_dataset(d, args.output / f"scan-{d.get('scanId', d['id'])}", args.max_points) for d in datasets]
    json_write(args.output / "provenance.json", {"schema": SCHEMA, "inputManifest": str(args.manifest.resolve()),
               "inputManifestSha256": sha256(args.manifest), "datasets": summaries})
    links = ''.join(f'<li><a href="{html.escape(d["path"], quote=True)}">{html.escape(d["name"])} · {d["figureCount"]} 张真实点云图</a></li>' for d in summaries)
    (args.output / "index.html").write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>三份点云修复核验图册</title><style>body{margin:24px auto;padding:0 16px;max-width:800px;background:#101827;color:#edf2fa;font:18px/1.8 system-ui,sans-serif}a{color:#71d5ff}li{margin:22px 0}</style>'
        '<h1>点云修复核验 · 真实源点图册</h1><p>点击每份数据，手机单列查看；点图片可打开原 PNG。原始 LAS 坐标与真实标签，不生成或插值点。未知灰色保留。</p><ul>'
        + links + '</ul><p>每份数据含整体语义、物理父构件配色、同源行修复前后与两处局部。采样、裁剪、源文件哈希和结果版本见数据追溯。</p></html>', encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
