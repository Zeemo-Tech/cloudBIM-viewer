#!/usr/bin/env python3
"""Render matched oblique before/after views of point-cloud classifications.

The top row shows the complete source cloud.  The bottom row shows an X-low
crop intended to expose the vertical faces at the left end of the CloudBIM
square-tube fixture.  Every panel is rasterized from the full source arrays
with its own depth buffer; no geometry is reconstructed or interpolated.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager, pyplot as plt
from matplotlib.lines import Line2D
import numpy as np


BLOCK_SIZE = 262_144
RASTER_WIDTH = 1_200
BACKGROUND = np.array([12, 20, 33], dtype=np.uint8)
PALETTE = np.array(
    [
        [148, 163, 184],  # unknown
        [100, 116, 139],  # table
        [245, 158, 11],   # fixture / square-tube plane
        [45, 212, 191],   # rebar candidate
    ],
    dtype=np.uint8,
)
CLASS_LABELS = (
    "旧版待定",
    "台面",
    "夹具（含方管）",
    "钢筋",
)

# Camera position relative to the cloud: low X, low Y, high Z.  Thus the
# nearest (-X and -Y) vertical faces are eligible to win the depth test.
CAMERA_VECTOR = np.array([-0.64, -0.50, 0.58], dtype=np.float64)
CAMERA_VECTOR /= np.linalg.norm(CAMERA_VECTOR)
LOOK_VECTOR = -CAMERA_VECTOR
SCREEN_RIGHT = np.cross(LOOK_VECTOR, np.array([0.0, 0.0, 1.0]))
SCREEN_RIGHT /= np.linalg.norm(SCREEN_RIGHT)
SCREEN_UP = np.cross(SCREEN_RIGHT, LOOK_VECTOR)
SCREEN_UP /= np.linalg.norm(SCREEN_UP)


def parse_crop(value: str) -> np.ndarray:
    """Parse XMIN,XMAX,YMIN,YMAX[,ZMIN,ZMAX] into a 3x2 bounds array."""
    try:
        numbers = [float(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("crop values must be numbers") from exc
    if len(numbers) == 4:
        numbers.extend([-math.inf, math.inf])
    if len(numbers) != 6:
        raise argparse.ArgumentTypeError(
            "crop must be XMIN,XMAX,YMIN,YMAX or XMIN,XMAX,YMIN,YMAX,ZMIN,ZMAX"
        )
    bounds = np.asarray(numbers, dtype=np.float64).reshape(3, 2)
    if np.any(bounds[:, 0] >= bounds[:, 1]):
        raise argparse.ArgumentTypeError("every crop minimum must be below its maximum")
    return bounds


def load_run(run: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    required = ("positions.npy", "geometry_class.npy", "manifest.json")
    missing = [name for name in required if not (run / name).is_file()]
    if missing:
        raise FileNotFoundError(f"{run}: missing {', '.join(missing)}")
    points = np.load(run / "positions.npy", mmap_mode="r")
    classes = np.load(run / "geometry_class.npy", mmap_mode="r")
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError(f"{run}: positions.npy must have shape (N, 3), got {points.shape}")
    if classes.shape != (len(points),):
        raise ValueError(
            f"{run}: geometry_class.npy shape {classes.shape} does not match {len(points)} points"
        )
    if classes.dtype.kind not in "ui" or int(classes.max()) >= len(PALETTE):
        raise ValueError(f"{run}: geometry_class.npy contains unsupported class ids")
    manifest = json.loads((run / "manifest.json").read_text())
    return points, classes, manifest


def iter_blocks(points: np.ndarray, classes: np.ndarray | None = None):
    for start in range(0, len(points), BLOCK_SIZE):
        block = np.asarray(points[start : start + BLOCK_SIZE])
        block_classes = None if classes is None else np.asarray(classes[start : start + len(block)])
        yield start, block, block_classes


def world_bounds(point_arrays: Iterable[np.ndarray]) -> np.ndarray:
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    for points in point_arrays:
        for _, block, _ in iter_blocks(points):
            finite = np.isfinite(block).all(axis=1)
            if finite.any():
                low = np.minimum(low, block[finite].min(axis=0))
                high = np.maximum(high, block[finite].max(axis=0))
    if not np.isfinite(np.r_[low, high]).all():
        raise ValueError("point clouds contain no finite XYZ points")
    return np.column_stack((low, high))


def in_crop(points: np.ndarray, crop: np.ndarray | None) -> np.ndarray:
    finite = np.isfinite(points).all(axis=1)
    if crop is None:
        return finite
    return finite & np.all((points >= crop[:, 0]) & (points <= crop[:, 1]), axis=1)


def projected_bounds(
    point_arrays: Iterable[np.ndarray], crop: np.ndarray | None
) -> tuple[np.ndarray, int]:
    low = np.full(2, np.inf)
    high = np.full(2, -np.inf)
    count = 0
    for points in point_arrays:
        for _, block, _ in iter_blocks(points):
            keep = in_crop(block, crop)
            if not keep.any():
                continue
            projected = np.column_stack(
                (block[keep] @ SCREEN_RIGHT, block[keep] @ SCREEN_UP)
            )
            low = np.minimum(low, projected.min(axis=0))
            high = np.maximum(high, projected.max(axis=0))
            count += int(keep.sum())
    if count == 0:
        raise ValueError("crop contains no finite points")
    pad = np.maximum((high - low) * 0.015, 1e-6)
    return np.column_stack((low - pad, high + pad)), count


def raster_size(bounds: np.ndarray) -> tuple[int, int]:
    span = np.maximum(bounds[:, 1] - bounds[:, 0], 1e-9)
    height = int(np.clip(round(RASTER_WIDTH * span[1] / span[0]), 420, 980))
    return RASTER_WIDTH, height


def project_pixels(points: np.ndarray, bounds: np.ndarray, width: int, height: int):
    screen = np.column_stack((points @ SCREEN_RIGHT, points @ SCREEN_UP))
    span = bounds[:, 1] - bounds[:, 0]
    xy = np.floor((screen - bounds[:, 0]) / span * [width - 1, height - 1]).astype(
        np.int64
    )
    valid = (
        (xy[:, 0] >= 0)
        & (xy[:, 0] < width)
        & (xy[:, 1] >= 0)
        & (xy[:, 1] < height)
    )
    return xy, valid


def rasterize(
    points: np.ndarray,
    classes: np.ndarray,
    crop: np.ndarray | None,
    bounds: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Rasterize one panel with an independent nearest-point depth buffer."""
    width, height = raster_size(bounds)
    depth = np.full(width * height, -np.inf, dtype=np.float64)
    included_counts = np.zeros(len(PALETTE), dtype=np.int64)
    included = 0

    for _, block, block_classes in iter_blocks(points, classes):
        keep = in_crop(block, crop)
        if not keep.any():
            continue
        selected = block[keep]
        selected_classes = block_classes[keep]
        included_counts += np.bincount(selected_classes, minlength=len(PALETTE))
        included += len(selected)
        xy, valid = project_pixels(selected, bounds, width, height)
        pixels = xy[valid, 1] * width + xy[valid, 0]
        np.maximum.at(depth, pixels, selected[valid] @ CAMERA_VECTOR)

    winners = np.full(width * height, -1, dtype=np.int64)
    for start, block, _ in iter_blocks(points, classes):
        keep = in_crop(block, crop)
        if not keep.any():
            continue
        local_ids = np.flatnonzero(keep)
        selected = block[local_ids]
        xy, valid = project_pixels(selected, bounds, width, height)
        if not valid.any():
            continue
        selected = selected[valid]
        local_ids = local_ids[valid]
        pixels = xy[valid, 1] * width + xy[valid, 0]
        visible = selected @ CAMERA_VECTOR == depth[pixels]
        np.maximum.at(winners, pixels[visible], start + local_ids[visible])

    occupied = winners >= 0
    raster = np.tile(BACKGROUND, (width * height, 1))
    raster[occupied] = PALETTE[np.asarray(classes[winners[occupied]], dtype=np.int64)]
    return raster.reshape(height, width, 3), included_counts, included


def short_run_name(run: Path, manifest: dict) -> str:
    return str(manifest.get("runId") or run.name)


def count_text(counts: np.ndarray) -> str:
    return "  ·  ".join(
        f"{label}: {int(count):,}" for index, (label, count) in enumerate(zip(CLASS_LABELS, counts))
        if index != 0 or count > 0
    )


def choose_font() -> None:
    for font in font_manager.fontManager.ttflist:
        if font.name in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei"):
            plt.rcParams["font.family"] = font.name
            break
    plt.rcParams["axes.unicode_minus"] = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before_run", type=Path)
    parser.add_argument("after_run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title", default="台面 / 钢筋 / 夹具 · 相同视角对比")
    parser.add_argument(
        "--crop",
        type=parse_crop,
        help="left-end ROI as XMIN,XMAX,YMIN,YMAX[,ZMIN,ZMAX]",
    )
    args = parser.parse_args()

    before_points, before_classes, before_manifest = load_run(args.before_run)
    after_points, after_classes, after_manifest = load_run(args.after_run)
    bounds_3d = world_bounds((before_points, after_points))
    crop = args.crop
    if crop is None:
        # The fixture's longitudinal axis is +X.  Its reported problem faces are
        # at the left end, so keep the low 32% of X and the complete Y/Z range.
        crop = bounds_3d.copy()
        crop[0, 1] = crop[0, 0] + 0.32 * (crop[0, 1] - crop[0, 0])

    whole_bounds, _ = projected_bounds((before_points, after_points), None)
    crop_bounds, _ = projected_bounds((before_points, after_points), crop)

    rows = []
    for points, classes in (
        (before_points, before_classes),
        (after_points, after_classes),
    ):
        whole = rasterize(points, classes, None, whole_bounds)
        local = rasterize(points, classes, crop, crop_bounds)
        rows.append((whole, local))

    choose_font()
    fig, axes = plt.subplots(2, 2, figsize=(22, 14), facecolor="#0c1421")
    panels = (
        (axes[0, 0], rows[0][0], "修改前 · 全景", short_run_name(args.before_run, before_manifest)),
        (axes[0, 1], rows[1][0], "修改后 · 全景", short_run_name(args.after_run, after_manifest)),
        (axes[1, 0], rows[0][1], "修改前 · 左端局部", short_run_name(args.before_run, before_manifest)),
        (axes[1, 1], rows[1][1], "修改后 · 左端局部", short_run_name(args.after_run, after_manifest)),
    )
    for ax, (raster, counts, included), title, run_name in panels:
        ax.imshow(raster, origin="lower", interpolation="nearest")
        ax.set_title(
            f"{title}  ·  {included:,} 个原始点\n{run_name}\n{count_text(counts)}",
            color="#e6edf6",
            loc="left",
            pad=10,
            fontsize=11,
            linespacing=1.45,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#304057")

    legend = [
        Line2D([0], [0], marker="s", linestyle="", markersize=10,
               markerfacecolor=PALETTE[index] / 255, markeredgecolor="none", label=label)
        for index, label in enumerate(CLASS_LABELS)
        if rows[0][0][1][index] + rows[1][0][1][index] > 0
    ]
    fig.legend(
        handles=legend,
        loc="upper right",
        bbox_to_anchor=(0.975, 0.985),
        frameon=False,
        ncol=4,
        labelcolor="#d8e1ee",
        fontsize=11,
    )
    crop_description = (
        f"X [{crop[0, 0]:.3f}, {crop[0, 1]:.3f}] m · "
        f"Y [{crop[1, 0]:.3f}, {crop[1, 1]:.3f}] m · "
        f"Z [{crop[2, 0]:.3f}, {crop[2, 1]:.3f}] m"
    )
    fig.suptitle(
        args.title,
        x=0.055,
        y=0.985,
        ha="left",
        color="#f5f8fc",
        fontsize=21,
    )
    fig.text(
        0.055,
        0.035,
        "相机位于 −X / −Y / +Z 方向，可见近侧竖向平面。"
        f"局部范围：{crop_description}。\n"
        "全部原始点参与投影，每幅图独立进行深度判断；颜色表示几何分类。",
        color="#9dacc2",
        fontsize=11,
    )
    fig.subplots_adjust(left=0.045, right=0.985, top=0.90, bottom=0.09, hspace=0.22, wspace=0.035)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)

    result = {
        "output": str(args.output.resolve()),
        "beforeRun": short_run_name(args.before_run, before_manifest),
        "afterRun": short_run_name(args.after_run, after_manifest),
        "cameraVectorTowardCamera": CAMERA_VECTOR.tolist(),
        "screenRight": SCREEN_RIGHT.tolist(),
        "screenUp": SCREEN_UP.tolist(),
        "crop": {axis: crop[index].tolist() for index, axis in enumerate("XYZ")},
        "panels": {
            "beforeWhole": {"pointCount": rows[0][0][2], "classCounts": rows[0][0][1].tolist()},
            "afterWhole": {"pointCount": rows[1][0][2], "classCounts": rows[1][0][1].tolist()},
            "beforeCrop": {"pointCount": rows[0][1][2], "classCounts": rows[0][1][1].tolist()},
            "afterCrop": {"pointCount": rows[1][1][2], "classCounts": rows[1][1][1].tolist()},
        },
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
