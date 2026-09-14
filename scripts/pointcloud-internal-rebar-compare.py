#!/usr/bin/env python3
"""Compare internal rebar instance coverage using the same source-point views."""
import argparse
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
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path);parser.add_argument('after',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('raster',Path(__file__).with_name('pointcloud-classification-compare.py'))
    raster=importlib.util.module_from_spec(spec);spec.loader.exec_module(raster);raster.choose_font()
    runs=[args.before,args.after];data=[]
    for run in runs:
        report=json.loads((run/'manifest.json').read_text())
        types=np.load(run/'internal_type.npy',mmap_mode='r');keep=types>0
        data.append((np.load(run/'positions.npy',mmap_mode='r')[keep],types[keep],report))
    if data[0][2]['source']['sha256']!=data[1][2]['source']['sha256']:
        raise ValueError('Before/after must use the same source')
    np.testing.assert_array_equal(data[0][0],data[1][0])
    palette=np.array([[100,116,139],[56,189,248],[251,113,133],[250,204,21],[148,163,184]],np.uint8)
    raster.PALETTE=palette
    bg,fg='#0c1421','#e6edf6';fig,axes=plt.subplots(2,2,figsize=(16,11),facecolor=bg)
    crop=np.array([[2.85,3.16],[-2.30,-1.94],[.012,.12]])
    with threadpool_limits(limits=1):
        for row,window in enumerate([None,crop]):
            bounds,_=raster.projected_bounds([d[0] for d in data],window)
            for col,(points,types,manifest) in enumerate(data):
                img,_,_=raster.rasterize(points,types,window,bounds)
                ax=axes[row,col];ax.imshow(img,origin='lower');r=manifest['internalRebar']
                text=('修复前' if col==0 else '修复后')+(' · 全景' if row==0 else ' · 同一短杆区域放大')
                if row==0:text+=f"\n实例待定 {r['counts']['unassigned']:,} 点"
                ax.set_title(text,loc='left',color=fg,fontsize=17,pad=12)
    for ax in axes.flat:
        ax.set_xticks([]);ax.set_yticks([])
        for spine in ax.spines.values():spine.set_color('#304057')
    after=data[1][2];old=data[0][2]['internalRebar']['counts']['unassigned'];new=after['internalRebar']['counts']['unassigned']
    fig.suptitle('内部短钢筋与实例归属修复',x=.04,ha='left',color=fg,fontsize=24)
    names=['下层钢筋','上层钢筋','腹杆直段','钢筋 · 实例待定']
    fig.legend(handles=[Patch(color=c/255,label=n) for c,n in zip(palette[1:],names)],loc='lower center',bbox_to_anchor=(.5,.065),ncol=4,frameon=False,labelcolor=fg,fontsize=12)
    fig.text(.04,.024,f"同源全量点云、相同视角；补回 {old-new:,} 点的实例归属。当前实例步骤 {after['timings']['internalRebarS']:.2f} s，完整重跑 {after['timings']['totalS']:.2f} s。\n灰色点仍保留为钢筋，尚未确定单根归属；覆盖率不代表人工标注准确率。",color='#9dacc2',fontsize=11)
    fig.subplots_adjust(left=.04,right=.98,top=.87,bottom=.15,hspace=.20,wspace=.08)
    args.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(args.output,dpi=150,facecolor=bg);plt.close(fig)
    print(args.output.resolve())


if __name__=='__main__':main()
