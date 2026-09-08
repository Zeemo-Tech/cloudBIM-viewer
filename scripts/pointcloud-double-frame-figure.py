#!/usr/bin/env python3
"""Render measured double frames and matched source-point before/after views."""
import argparse
import importlib.util
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
from threadpoolctl import threadpool_limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("raster", Path(__file__).with_name("pointcloud-classification-compare.py"))
    raster = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(raster)
    raster.choose_font()
    points = np.load(args.run / "positions.npy", mmap_mode="r")
    before = np.load(args.run / "fused_class.npy", mmap_mode="r")
    after = np.load(args.run / "refined_class.npy", mmap_mode="r")
    manifest = json.loads((args.run / "manifest.json").read_text())
    refinement = manifest["refinement"]
    background, text_color = "#0c1421", "#e6edf6"
    fig, axes = plt.subplots(2, 2, figsize=(18, 13), facecolor=background)
    with threadpool_limits(limits=1):
        # Same physical crop and camera for both panels, including all source points.
        crop = np.array([[2.65, 3.55], [-2.18, -1.90], [.012, .12]])
        bounds, _ = raster.projected_bounds([points], crop)
        for ax, labels, title in zip(axes[1], [before, after],
                                    ["局部修改前 · 钢筋上仍有黄色片段", "局部修改后 · 内框里的非台面点归还钢筋"]):
            image, _, _ = raster.rasterize(points, labels, crop, bounds)
            ax.imshow(image, origin="lower")
            ax.set_title(title, loc="left", color=text_color, fontsize=17, pad=14)
        raster.SCREEN_RIGHT = np.array([1., 0., 0.])
        raster.SCREEN_UP = np.array([0., 1., 0.])
        raster.CAMERA_VECTOR = np.array([0., 0., 1.])
        bounds, _ = raster.projected_bounds([points], None)
        for ax, labels, title in zip(axes[0], [before, after],
                                    ["03 · 融合结果（隐藏台面）", "04 · 双框约束 + 同表面类别整理"]):
            keep = labels != 1
            image, _, _ = raster.rasterize(points[keep], labels[keep], None, bounds)
            ax.imshow(image, origin="lower", extent=[*bounds[0], *bounds[1]])
            ax.set_title(title, loc="left", color=text_color, fontsize=17, pad=14)
            for key, color in [("outerCornersM", "#ffffff"), ("innerCornersM", "#57a6ff")]:
                corners = np.array(refinement['frame'][key])
                closed = np.vstack((corners, corners[0]))
                ax.plot(closed[:, 0], closed[:, 1], color=color, linestyle=(0, (6, 4)), linewidth=1.15)
    for ax in axes.flat:
        ax.set_facecolor(background)
        ax.set_xticks([]); ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#304057")
    fig.legend(handles=[Patch(color="#2dd4bf", label="钢筋"), Patch(color="#f59e0b", label="夹具（含方管）"),
                        Line2D([], [], color="#57a6ff", linestyle="--", label="内框"),
                        Line2D([], [], color="white", linestyle="--", label="外框")],
               loc="lower center", bbox_to_anchor=(.5, .09), ncol=4, frameon=False, labelcolor=text_color, fontsize=14)
    t = manifest['timings']; changes = refinement['changes']
    fig.suptitle(f"双框分区与类别整理\n新增步骤 {t['refinementS']:.2f} s  ·  从原始文件全流程 {t['totalS']:.2f} s",
                 x=.04, ha="left", color=text_color, fontsize=25)
    fig.text(.04, .025, f"内框归还 {changes['interiorToSteel']:,} 点  ·  框边及外侧整理 {changes['totalChanged']-changes['interiorToSteel']:,} 点\n"
             "内框依据夹具横梁的实测内缘；这是当前场景的分类约束。所有图来自全量原始点，点数变化不代表人工标注准确率。",
             color="#9dacc2", fontsize=12)
    fig.subplots_adjust(left=.04, right=.98, top=.85, bottom=.17, hspace=.16, wspace=.08)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=150, facecolor=background)
    plt.close(fig)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
