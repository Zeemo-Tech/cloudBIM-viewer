#!/usr/bin/env python3
"""Full-source classification views; per-class highest-Z orthographic raster."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt, font_manager
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads((args.run / "manifest.json").read_text())
    points = np.load(args.run / "positions.npy", mmap_mode="r")
    classes = np.load(args.run / "geometry_class.npy", mmap_mode="r")
    count = len(points)
    lo, hi = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    span = np.maximum(hi-lo, 1.e-6)
    width, height = 1600, max(120, round(1600 * span[1]/span[0]))
    palette = np.array([[148, 163, 184], [70, 85, 105], [245, 158, 11], [45, 212, 191]], np.uint8)
    background = [12, 20, 33]
    images = []
    three_class = data["classification"].get("classPolicy") == "table-rebar-fixture-remainder"
    for category in (None, 3, 2, 1 if three_class else 0):
        depth = np.full(width*height, -np.inf)
        winners = np.full(width*height, -1, np.int64)
        for second in (False, True):
            for start in range(0, count, 262144):
                block = points[start:start+262144]
                ids = np.arange(start, start+len(block))
                if category is not None:
                    keep = classes[ids] == category
                    block, ids = block[keep], ids[keep]
                xy = np.floor((block[:, :2]-lo)/span * [width-1, height-1]).astype(np.int64)
                pixels = xy[:, 1]*width + xy[:, 0]
                if second:
                    visible = block[:, 2] == depth[pixels]
                    np.maximum.at(winners, pixels[visible], ids[visible])
                else:
                    np.maximum.at(depth, pixels, block[:, 2])
        occupied = winners >= 0
        raster = np.tile(np.array(background, np.uint8), (width*height, 1))
        raster[occupied] = palette[classes[winners[occupied]]]
        images.append(raster.reshape(height, width, 3))
    for font in font_manager.fontManager.ttflist:
        if font.name in ("Noto Sans CJK JP", "Noto Sans CJK SC", "WenQuanYi Zen Hei"):
            plt.rcParams["font.family"] = font.name
            break
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(4, 1, figsize=(16, 25), facecolor="#0c1421")
    c = data["classification"]["counts"]
    titles = ["02  全部分类 · 深灰台面 / 橙色夹具 / 青色钢筋",
              f"仅钢筋 · {c['rebar']:,} 点", f"仅夹具（含方管边缘、侧面）· {c['fixture']:,} 点",
              f"仅台面 · {c['table']:,} 点" if three_class else f"旧版待定 · {c['unknown']:,} 点"]
    for ax, raster, title in zip(axes, images, titles):
        ax.imshow(raster, origin="lower", extent=[lo[0], hi[0], lo[1], hi[1]], interpolation="nearest")
        ax.set_title(title, color="#e6edf6", loc="left", pad=15, fontsize=17)
        ax.set_xlabel("源坐标 X (m)", color="#9dacc2")
        ax.set_ylabel("源坐标 Y (m)", color="#9dacc2")
        ax.tick_params(colors="#9dacc2")
        for spine in ax.spines.values():
            spine.set_color("#304057")
    t = data["timings"]
    fig.suptitle(f"第二步：法向量几何分类  |  {count:,} 个原始点\n"
                 f"分类 {t['classificationS']:.2f}s  ·  法向量 {t['normalsS']:.2f}s  ·  全流程 {t['totalS']:.2f}s",
                 x=.075, ha="left", color="#f5f8fc", fontsize=23)
    fig.text(.075, .025, "全量点沿 −Z 正射投影；每个单类视图独立取最高 Z 点。仅依据几何证据，不代表人工标注准确率。\n" +
             ("三类归属：台面、钢筋，其余统一归夹具；方管边缘和侧面属于同一类。" if three_class
              else "旧版结果保留待定类别。"), color="#9dacc2", fontsize=13)
    fig.subplots_adjust(left=.075, right=.98, top=.92, bottom=.07, hspace=.23)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(json.dumps({"output": str(args.output.resolve()), "pointCount": count, "counts": c}))


if __name__ == "__main__":
    main()
