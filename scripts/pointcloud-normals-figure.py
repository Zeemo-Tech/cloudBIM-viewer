#!/usr/bin/env python3
"""Render real persisted normals as a full-population XY orthographic figure.

Visualization only; no recomputation, interpolation or sampling of the source.
At each image pixel keep the highest-Z source record (ties: greatest row index).
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib import font_manager
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads((args.run / "manifest.json").read_text())
    points = np.load(args.run / "positions.npy", mmap_mode="r")
    normals = np.load(args.run / "normals.npy", mmap_mode="r")
    colors = np.load(args.run / "colors.npy", mmap_mode="r")
    valid = np.load(args.run / "normal_valid.npy", mmap_mode="r")
    count = len(points)
    lo, hi = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    span = np.maximum(hi - lo, 1.e-6)
    width = 1500
    height = max(120, round(width * span[1] / span[0]))
    depth = np.full(width * height, -np.inf)
    winners = np.full(width * height, -1, np.int64)

    def pixels(block):
        xy = np.floor((block[:, :2] - lo) / span * [width - 1, height - 1]).astype(np.int64)
        return xy[:, 1] * width + xy[:, 0]

    for start in range(0, count, 262144):
        block = points[start:start+262144]
        np.maximum.at(depth, pixels(block), block[:, 2])
    for start in range(0, count, 262144):
        block = points[start:start+262144]
        index = pixels(block)
        visible = block[:, 2] == depth[index]
        np.maximum.at(winners, index[visible], np.arange(start, start+len(block))[visible])
    occupied = winners >= 0
    ids = winners[occupied]
    background = np.array([12, 20, 33], np.uint8)
    images = []
    for kind in ("raw", "absolute", "signed"):
        raster = np.tile(background, (width * height, 1))
        if kind == "raw":
            shade = colors[ids]
        else:
            n = normals[ids]
            rgb = np.abs(n) if kind == "absolute" else (n + 1.) * .5
            shade = np.clip(rgb * 255, 0, 255).astype(np.uint8)
            shade[valid[ids] == 0] = [255, 0, 180]
        raster[occupied] = shade
        images.append(raster.reshape(height, width, 3))
    for font in font_manager.fontManager.ttflist:
        if font.name in ("Noto Sans CJK JP", "Noto Sans CJK SC", "WenQuanYi Zen Hei"):
            plt.rcParams["font.family"] = font.name
            break
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 1, figsize=(16, 20), facecolor="#0c1421")
    titles = ["00  原始点云 · 原始强度", "01  法向量 |n| · 忽略正负朝向", "01  法向量 (n+1)/2 · 可观察朝向翻转"]
    for ax, raster, title in zip(axes, images, titles):
        ax.imshow(raster, origin="lower", extent=[lo[0], hi[0], lo[1], hi[1]], interpolation="nearest")
        ax.set_title(title, color="#e6edf6", loc="left", pad=15, fontsize=18)
        ax.set_xlabel("源坐标 X (m)", color="#9dacc2")
        ax.set_ylabel("源坐标 Y (m)", color="#9dacc2")
        ax.tick_params(colors="#9dacc2")
        for spine in ax.spines.values():
            spine.set_color("#304057")
    t = data["timings"]
    fig.suptitle(f"第一步：法向量计算  |  {count:,} 个原始点\n"
                 f"计算 {t['normalsS']:.2f}s  ·  全流程 {t['totalS']:.2f}s  ·  k={data['parameters']['k']}  ·  {data['parameters']['workers']} 线程",
                 x=.075, ha="left", color="#f5f8fc", fontsize=23)
    fig.text(.075, .025, "全量点沿 −Z 正射投影；每像素显示最高 Z 点。RGB 对应法向量 XYZ，未统一正负朝向。\n"
             "没有去噪、分类或点数删减；此图为持久化结果离线投影，不是浏览器截图。", color="#9dacc2", fontsize=13)
    fig.subplots_adjust(left=.075, right=.98, top=.90, bottom=.08, hspace=.25)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(json.dumps({"output": str(args.output.resolve()), "sourcePointCount": count,
                      "visiblePixels": int(occupied.sum()), "sampled": False}))


if __name__ == "__main__":
    main()
