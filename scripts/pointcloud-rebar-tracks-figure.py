#!/usr/bin/env python3
"""Compare actual source-point instance colors before/after track refinement."""
import argparse
import colorsys
import importlib.util
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import numpy as np
from threadpoolctl import threadpool_limits


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path)
    parser.add_argument('after',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('raster',Path(__file__).with_name('pointcloud-classification-compare.py'))
    raster=importlib.util.module_from_spec(spec);spec.loader.exec_module(raster);raster.choose_font()
    reports=[];data=[]
    for run in [args.before,args.after]:
        manifest=json.loads((run/'manifest.json').read_text());reports.append(manifest)
        keep=np.load(run/'internal_type.npy',mmap_mode='r')>0
        points=np.load(run/'positions.npy',mmap_mode='r')[keep]
        ids=np.load(run/'internal_instance.npy',mmap_mode='r')[keep]
        data.append((points,ids))
    if not np.array_equal(data[0][0],data[1][0]):
        raise ValueError('Comparison requires identical ordered source points and scope')
    bg,fg='#0c1421','#e6edf6'
    fig,axes=plt.subplots(2,2,figsize=(18,12),facecolor=bg)
    crop=np.array([[4.1,5.6],[-1.64,-1.48],[.017,.055]])
    with threadpool_limits(limits=1):
        for row,window in enumerate([None,crop]):
            bounds,_=raster.projected_bounds([d[0] for d in data],window)
            for col,((points,ids),manifest) in enumerate(zip(data,reports)):
                number=manifest['internalRebar']['instanceCount']
                raster.PALETTE=np.array([[148,163,184]]+[[round((12.92*v if v<=.0031308 else 1.055*v**(1/2.4)-.055)*255)
                    for v in colorsys.hls_to_rgb((i*.61803398875)%1,.58,.72)] for i in range(1,number+1)],np.uint8)
                image,_,_=raster.rasterize(points,ids,window,bounds)
                ax=axes[row,col];ax.imshow(image,origin='lower')
                label='修改前' if col==0 else '轴向 + 直径约束后'
                ax.set_title(f'{label} · '+('全部内部钢筋' if row==0 else '相同局部：贴近的下层钢筋'),loc='left',color=fg,fontsize=18,pad=12)
    for ax in axes.flat:
        ax.set_xticks([]);ax.set_yticks([])
        for spine in ax.spines.values():spine.set_color('#304057')
    old,new=[m['internalRebar'] for m in reports]
    fig.suptitle('钢筋实例连续性 · 同一批源点前后对照',x=.04,ha='left',color=fg,fontsize=25)
    fig.text(.04,.91,f"{new['pointCount']:,} 个内部点 · 实例 {old['instanceCount']} → {new['instanceCount']} · 相同视角、范围及显示分辨率",color='#bac8da',fontsize=14)
    fig.text(.04,.04,f"不同颜色表示不同实例，灰色表示实例待定；两次运行重新编号，左右颜色不代表同一个编号。\n"
        f"新增步骤 {reports[0]['timings']['internalRebarS']:.2f} → {reports[1]['timings']['internalRebarS']:.2f} s；本轮全流程 {reports[1]['timings']['totalS']:.2f} s。实例数量是算法结果，未作人工真值精度评估。",color='#9dacc2',fontsize=12)
    fig.subplots_adjust(left=.04,right=.98,top=.85,bottom=.13,hspace=.16,wspace=.08)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.output,dpi=150,facecolor=bg);plt.close(fig)
    print(args.output.resolve())


if __name__=='__main__':main()
