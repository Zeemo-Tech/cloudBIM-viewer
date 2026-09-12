#!/usr/bin/env python3
"""Export source-backed multiview evidence for baseline/new workbench runs."""
import argparse,json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
p=argparse.ArgumentParser();p.add_argument('baseline',type=Path);p.add_argument('result',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
if a.output.exists():raise FileExistsError(a.output)
old=np.load(a.baseline/'complete_class.npy',mmap_mode='r');new=np.load(a.result/'complete_class.npy',mmap_mode='r');xyz=np.load(a.result/'positions.npy',mmap_mode='r')
changed=np.flatnonzero(old!=new);steel=np.flatnonzero((old==3)|(new==3))
context=steel[np.linspace(0,len(steel)-1,min(len(steel),20000),dtype=int)]
selected=changed[np.linspace(0,len(changed)-1,min(len(changed),20000),dtype=int)] if len(changed) else changed
fig,axes=plt.subplots(2,3,figsize=(16,9),layout='constrained')
views=[(0,1,'XY'),(0,2,'XZ'),(1,2,'YZ')]
for ax,(u,v,title) in zip(axes[0],views):
    ax.scatter(xyz[context,u],xyz[context,v],s=.2,c='#adb5bd',rasterized=True)
    for take,color,label in [((old[selected]!=3)&(new[selected]==3),'#0077b6','recovered steel'),((old[selected]==3)&(new[selected]!=3),'#d62828','removed steel')]:
        rows=selected[take];ax.scatter(xyz[rows,u],xyz[rows,v],s=1,c=color,label=label,rasterized=True)
    ax.set(title=title,xlabel='XYZ'[u]+' (m)',ylabel='XYZ'[v]+' (m)');ax.margins(.05)
axes[0,0].legend(markerscale=4,fontsize=8)
if len(changed):
    cells=np.floor(xyz[changed]/.08).astype(int);keys,inverse,counts=np.unique(cells,axis=0,return_inverse=True,return_counts=True)
    centers=[]
    for i in np.argsort(counts)[::-1]:
        center=(keys[i]+.5)*.08
        if all(np.linalg.norm(center-c)>.15 for c in centers):centers.append(center)
        if len(centers)==3:break
    for ax,center in zip(axes[1],centers):
        local=steel[np.all(np.abs(xyz[steel]-center)<.12,axis=1)]
        if len(local)>20000:local=local[np.linspace(0,len(local)-1,20000,dtype=int)]
        colors=np.where((old[local]!=3)&(new[local]==3),'#0077b6',np.where((old[local]==3)&(new[local]!=3),'#d62828','#adb5bd'))
        ax.scatter(xyz[local,0],xyz[local,2],s=1,c=colors,rasterized=True)
        ax.set(title=f'Changed region near {center.round(2)}',xlabel='X (m)',ylabel='Z (m)');ax.margins(.05)
fig.suptitle(f'Observed source evidence | changed classes: {len(changed):,} | blue: recovered; red: removed; gray: retained context | independent axis scales')
a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=180);plt.close(fig)
print(a.output)
