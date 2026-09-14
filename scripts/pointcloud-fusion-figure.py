#!/usr/bin/env python3
"""Matched source-point views of recovery and fixture-relative steel regions."""
import argparse
import importlib.util
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.patches import Patch
import numpy as np
from threadpoolctl import threadpool_limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("pointcloud_raster", Path(__file__).with_name("pointcloud-classification-compare.py"))
    raster = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(raster)
    raster.choose_font()
    points = np.load(args.run / "positions.npy", mmap_mode="r")
    before = np.load(args.baseline / "projection_class.npy", mmap_mode="r")
    after = np.load(args.run / "fused_class.npy", mmap_mode="r")
    regions = np.load(args.run / "fused_region.npy", mmap_mode="r")
    manifest = json.loads((args.run / "manifest.json").read_text())
    baseline = json.loads((args.baseline / "manifest.json").read_text())
    if manifest['source']['sha256'] != baseline['source']['sha256']:
        raise ValueError("Comparison requires the same immutable source")
    # A fixed physical window contains several crossbars and a truss. Both
    # panels show the very same source records with independent depth buffers.
    crop = np.array([[2.65, 3.55], [-2.18, -1.90], [.012, .12]])
    background, foreground, secondary = "#0c1421", "#e6edf6", "#9dacc2"
    fig, axes = plt.subplots(2, 2, figsize=(18, 14), facecolor=background)
    with threadpool_limits(limits=1):
        bounds, _ = raster.projected_bounds([points], crop)
        for ax, labels, title in zip(axes[0], [before, after], ["修改前 · 原投影分类", "修改后 · 交点恢复 + 两路几何融合"]):
            image, _, _ = raster.rasterize(points, labels, crop, bounds)
            ax.imshow(image, origin="lower")
            ax.set_title(title, loc="left", color=foreground, fontsize=19, pad=15)
        raster.PALETTE = np.array([[100,116,139], [45,212,191], [244,114,182], [245,158,11], [96,165,250]], np.uint8)
        raster.SCREEN_RIGHT = np.array([1., 0., 0.])
        raster.SCREEN_UP = np.array([0., 1., 0.])
        raster.CAMERA_VECTOR = np.array([0., 0., 1.])
        bounds, _ = raster.projected_bounds([points], None)
        for ax, keep, title in zip(axes[1], [after != 1, after == 3],
                                   ["俯视区域 · 夹具 / 围内钢筋 / 外露钢筋", "仅钢筋 · 已移除台面和夹具"]):
            image, _, _ = raster.rasterize(points[keep], regions[keep], None, bounds)
            ax.imshow(image, origin="lower", extent=[*bounds[0], *bounds[1]])
            ax.set_title(title, loc="left", color=foreground, fontsize=19, pad=15)
            if manifest['regions']['frame']['detected']:
                corners = np.array(manifest['regions']['frame']['cornersM'])
                closed = np.vstack((corners, corners[0]))
                ax.plot(closed[:,0], closed[:,1], color="#a8b3c2", linestyle="--", linewidth=.8, alpha=.7)
    for ax in axes.flat:
        ax.set_facecolor(background)
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#304057")
    fig.legend(handles=[Patch(color="#2dd4bf", label="钢筋 / 围内钢筋"),
                        Patch(color="#f472b6", label="外露钢筋"),
                        Patch(color="#f59e0b", label="夹具（含方管）")],
               loc="lower center", bbox_to_anchor=(.5, .07), ncol=3,
               facecolor=background, labelcolor=foreground, frameon=False, fontsize=15)
    t = manifest['timings']
    c = manifest['regions']['counts']
    fig.suptitle("交点与断段恢复 · 两路融合 · 围框区域分离\n"
                 f"投影 {t['projectionS']:.2f}s  ·  融合 {t['fusionS']:.2f}s  ·  区域 {t['regionsS']:.2f}s  ·  全流程 {t['totalS']:.2f}s",
                 x=.045, ha="left", fontsize=25, color=foreground)
    fig.text(.045, .035, f"围内钢筋 {c['interior']:,} 点  /  外露钢筋 {c['exterior']:,} 点  /  夹具 {c['fixture']:,} 点\n"
             "所有图由全量源点直接投影；上排采用相同局部、相同视角。虚线表示估计的夹具外缘；点数不代表人工标注准确率。",
             color=secondary, fontsize=12)
    fig.subplots_adjust(left=.045, right=.98, top=.88, bottom=.14, hspace=.15, wspace=.08)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160, facecolor=background)
    plt.close(fig)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
