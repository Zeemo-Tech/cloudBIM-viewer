#!/usr/bin/env python3
"""Plot actual source rows and final instance colors in fixed comparison windows."""
import argparse
import colorsys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path);parser.add_argument('after',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    points=np.load(args.after/'positions.npy',mmap_mode='r')
    windows=[('Crossing', (4.61,4.67),(-2.31,-2.24),(.015,.050)),
             ('Adjacent rods A',(2.7,3.05),(-2.20,-2.14),(.015,.050)),
             ('Adjacent rods B',(2.7,3.05),(-1.62,-1.56),(.015,.050))]
    fig,axes=plt.subplots(3,2,figsize=(14,11),facecolor='#0b1020')
    for row,(name,xlim,ylim,zlim) in enumerate(windows):
        mask=(points[:,0]>=xlim[0])&(points[:,0]<=xlim[1])&(points[:,1]>=ylim[0])&(points[:,1]<=ylim[1])&(points[:,2]>=zlim[0])&(points[:,2]<=zlim[1])
        for col,root in enumerate((args.before,args.after)):
            owners=np.load(root/'complete_instance.npy',mmap_mode='r')
            classes=np.load(root/'complete_class.npy',mmap_mode='r')
            rows=np.flatnonzero(mask&(classes==3))
            color=np.array([colorsys.hls_to_rgb((int(i)*.61803398875)%1,.58,.72) if i else (.58,.64,.72) for i in owners[rows]])
            ax=axes[row,col];ax.set_facecolor('#0b1020')
            ax.scatter(points[rows,0],points[rows,1],c=color,s=2,linewidths=0)
            ax.set_xlim(xlim);ax.set_ylim(ylim);ax.set_aspect('equal')
            ax.set_title(f'{name} / {"Before" if col==0 else "After"} ({len(rows):,} source points)',color='white')
            ax.tick_params(colors='#cbd5e1');ax.set_xlabel('X (m)',color='#cbd5e1');ax.set_ylabel('Y (m)',color='#cbd5e1')
    fig.suptitle('Final instance ownership — identical spatial windows, no synthetic points',color='white')
    fig.tight_layout();fig.savefig(args.output,dpi=170,facecolor=fig.get_facecolor());plt.close(fig)


if __name__=='__main__':main()
