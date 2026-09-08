#!/usr/bin/env python3
"""Source-point effect figures for internal steel types and cylinder instances."""
import argparse
import colorsys
import importlib.util
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.patches import Patch
import numpy as np
from threadpoolctl import threadpool_limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('raster', Path(__file__).with_name('pointcloud-classification-compare.py'))
    raster = importlib.util.module_from_spec(spec); spec.loader.exec_module(raster); raster.choose_font()
    manifest = json.loads((args.run/'manifest.json').read_text()); report = manifest['internalRebar']
    types = np.load(args.run/'internal_type.npy', mmap_mode='r')
    keep = types > 0; types = types[keep]
    points = np.load(args.run/'positions.npy', mmap_mode='r')[keep]
    ids = np.load(args.run/'internal_instance.npy', mmap_mode='r')[keep]
    type_colors = np.array([[100,116,139],[56,189,248],[251,113,133],[250,204,21],[148,163,184]], np.uint8)
    instance_colors = np.array([[148,163,184]]+[[round((12.92*v if v<=.0031308 else 1.055*v**(1/2.4)-.055)*255)
                                               for v in colorsys.hls_to_rgb((i*.61803398875)%1,.58,.72)]
                                             for i in range(1,report['instanceCount']+1)],np.uint8)
    bg, fg = '#0c1421','#e6edf6'
    fig, axes = plt.subplots(2,2,figsize=(18,13),facecolor=bg)
    crop = np.array([[2.65,3.55],[-2.18,-1.90],[.012,.12]])
    with threadpool_limits(limits=1):
        for row, window in enumerate([None,crop]):
            bounds,_ = raster.projected_bounds([points],window)
            for col, (labels,palette,title) in enumerate([(types,type_colors,'三类钢筋'),(ids,instance_colors,'逐根实例')]):
                raster.PALETTE = palette
                image,_,_ = raster.rasterize(points,labels,window,bounds)
                ax=axes[row,col];ax.imshow(image,origin='lower')
                ax.set_title(('全景 · ' if row==0 else '同一局部放大 · ')+title,loc='left',color=fg,fontsize=18,pad=13)
    for ax in axes.flat:
        ax.set_facecolor(bg);ax.set_xticks([]);ax.set_yticks([])
        for spine in ax.spines.values():spine.set_color('#304057')
    names=['下层钢筋','上层钢筋','腹杆直段','实例待定（保留钢筋点）']
    fig.legend(handles=[Patch(color=c/255,label=n) for c,n in zip(type_colors[1:],names)],loc='lower center',
               bbox_to_anchor=(.5,.085),ncol=4,frameon=False,labelcolor=fg,fontsize=13)
    count=report['counts']; timing=manifest['timings']
    fig.suptitle(f"内部钢筋 · 分层与圆柱实例\n{report['pointCount']:,} 个内部源点 · {report['instanceCount']} 个实例 · 新增步骤 {timing['internalRebarS']:.2f} s",
                 x=.04,ha='left',color=fg,fontsize=24)
    per_type=[sum(i['type']==k for i in report['instances']) for k in [1,2,3]]
    fig.text(.04,.024,f"实例：下层 {per_type[0]} / 上层 {per_type[1]} / 腹杆直段 {per_type[2]}　·　实例待定 {count['unassigned']:,} 点\n"
             f"不同实例使用不同颜色；交点逐源点分配。全部图像由源点投影，完整流程 {timing['totalS']:.2f} s；实例数量是算法结果，尚非人工标注真值。",
             color='#9dacc2',fontsize=11.5)
    fig.subplots_adjust(left=.04,right=.98,top=.84,bottom=.16,hspace=.16,wspace=.08)
    args.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(args.output,dpi=150,facecolor=bg);plt.close(fig)
    print(args.output.resolve())


if __name__=='__main__':main()
