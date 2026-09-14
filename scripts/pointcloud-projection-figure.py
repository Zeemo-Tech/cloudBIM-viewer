#!/usr/bin/env python3
"""Render the saved full-source projection rasters and measured Z histogram."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt, font_manager
from matplotlib.patches import Patch
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text())
    projection = manifest["projection"]
    for font in font_manager.fontManager.ttflist:
        if font.name in ("Noto Sans CJK JP", "Noto Sans CJK SC", "WenQuanYi Zen Hei"):
            plt.rcParams["font.family"] = font.name
            break
    plt.rcParams["axes.unicode_minus"] = False
    background, foreground, secondary = "#0c1421", "#e6edf6", "#9dacc2"
    fig, axes = plt.subplots(2, 2, figsize=(18, 10), facecolor=background)
    lo = projection["xyOriginM"]
    height, width = projection["gridShape"]
    pixel = projection["pixelSizeM"]
    extent = [lo[0], lo[0] + width * pixel, lo[1], lo[1] + height * pixel]
    panels = [("binary", "移除台面 → 俯视二值图"),
              ("density", "俯视密度图 · 越亮点数越多（对数色阶）"),
              ("classes", "投影分类 · 每像素显示最高 Z 点的类别")]
    for ax, (key, title) in zip(axes.flat, panels):
        raster = plt.imread(args.run / "projection" / f"{key}.png")
        ax.imshow(raster, origin="upper", extent=extent, interpolation="nearest")
        ax.set_title(title, color=foreground, loc="left", pad=14, fontsize=17)
        ax.set_xlabel("源坐标 X (m)", color=secondary)
        ax.set_ylabel("源坐标 Y (m)", color=secondary)
    axes[1, 0].legend(handles=[Patch(color="#64748b", label="台面"),
                              Patch(color="#f59e0b", label="夹具（含方管）"),
                              Patch(color="#2dd4bf", label="钢筋")],
                      loc="lower left", ncol=3,
                      facecolor=background, labelcolor=foreground, framealpha=.85)
    ax = axes[1, 1]
    with np.load(args.run / "projection-features.npz") as cache:
        all_counts, remaining = cache["histogram_all"], cache["histogram_remaining"]
        z = (float(cache["histogram_origin"]) + (np.arange(len(all_counts)) + .5) *
             projection["parameters"]["histogram_bin"]) * 1000
    ax.plot(z, all_counts, color="#9dacc2", lw=1.4, label="全部原始点")
    ax.plot(z, remaining, color="#2dd4bf", lw=1.7, label="移除台面后")
    ax.set_yscale("symlog", linthresh=100)
    ax.set_ylim(0, max(all_counts.max() * 2, 1000))
    for layer, color in zip(projection["layers"], ["#2dd4bf", "#f59e0b", "#60a5fa"] * 4):
        ax.axvspan(layer["lowM"] * 1000, layer["highM"] * 1000, color=color, alpha=.13)
        ax.axvline(layer["heightM"] * 1000, color=color, alpha=.7, lw=1)
        ax.text(layer["heightM"] * 1000, .98, f"{layer['heightM'] * 1000:.1f} mm",
                transform=ax.get_xaxis_transform(), color=color, ha="center", va="top", fontsize=12)
    ax.set_title("全部点投影至 Z 轴 · 自动检出的 3 个候选高度带" if len(projection["layers"]) == 3
                 else f"全部点投影至 Z 轴 · {len(projection['layers'])} 个候选高度带",
                 color=foreground, loc="left", pad=14, fontsize=17)
    ax.set_xlabel("源坐标 Z (mm)", color=secondary)
    ax.set_ylabel("每 0.5 mm 的点数（纵轴压缩显示）", color=secondary)
    ax.legend(loc="lower right", facecolor=background, labelcolor=foreground, frameon=False)
    ax.grid(alpha=.12)
    for ax in axes.flat:
        ax.set_facecolor(background)
        ax.tick_params(colors=secondary)
        for spine in ax.spines.values():
            spine.set_color("#304057")
    t = manifest["timings"]
    fig.suptitle(f"02B  投影图像分类  |  {projection['pointCount']:,} 个原始点\n"
                 f"投影分支 {t['projectionS']:.2f}s  ·  两路并行 {t['classifiersWallS']:.2f}s  ·  从原始点重跑 {t['totalS']:.2f}s",
                 x=.06, ha="left", color=foreground, fontsize=24)
    bands = " / ".join(f"{layer['name']} {layer['heightM'] * 1000:.1f} mm" for layer in projection["layers"])
    fig.text(.06, .045, bands + "\n"
             f"计算使用全部源点；俯视像素 {pixel * 1000:g} mm。高度带是几何候选，斜向腹杆连接关系尚未计算。",
             color=secondary, fontsize=12)
    fig.subplots_adjust(left=.06, right=.98, top=.85, bottom=.16, hspace=.42, wspace=.18)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160, facecolor=background)
    plt.close(fig)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
