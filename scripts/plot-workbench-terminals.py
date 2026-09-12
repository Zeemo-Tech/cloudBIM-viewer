#!/usr/bin/env python3
"""Plot source-backed terminal removals in local axial/transverse coordinates."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    m=json.loads((args.run/'manifest.json').read_text());report=m['terminalCleanup']
    xyz=np.load(args.run/'positions.npy',mmap_mode='r');removed=np.load(args.run/'terminal_removed.npy',mmap_mode='r')
    owner=np.load(args.run/'complete_instance.npy',mmap_mode='r');previous=np.load(args.run/'terminal_previous_instance.npy',mmap_mode='r')
    before={r['instanceId']:r for r in report['acceptanceBefore']['instances']}
    after={r['instanceId']:r for r in m['acceptance']['instances']}
    with (args.output/'instances.csv').open('w') as stream:
        writer=csv.writer(stream);writer.writerow(['instance','removed_points','before_length_mm','after_length_mm','before_failures','after_failures'])
        for d in report['decisions']:
            ident=d['instanceId'];b,a=before[ident],after[ident]
            writer.writerow([ident,d['pointCount'],b.get('lengthM',0)*1000,a.get('lengthM',0)*1000,';'.join(b['reasons']),';'.join(a['reasons'])])
    selected=sorted(report['decisions'],key=lambda r:-r['pointCount'])[:4]
    if not selected:return
    fig,axes=plt.subplots(len(selected),3,figsize=(14,3.4*len(selected)),squeeze=False,layout='constrained')
    for row,decision in enumerate(selected):
        ident=decision['instanceId'];keep=np.flatnonzero(owner==ident);cut=np.flatnonzero((removed>0)&(previous==ident))
        points=np.concatenate([xyz[keep],xyz[cut]]);center=points.mean(0)
        _,v=np.linalg.eigh((points-center).T@(points-center));basis=v[:,::-1]
        good=(xyz[keep]-center)@basis*1000;bad=(xyz[cut]-center)@basis*1000
        side=bad[:,0]>=0;dominant=side if np.count_nonzero(side)>=len(side)/2 else ~side
        location=np.median(bad[dominant,0]);good=good[np.abs(good[:,0]-location)<60];bad=bad[np.abs(bad[:,0]-location)<60]
        for col,(u,v,label) in enumerate([(0,1,'axis / transverse 1'),(0,2,'axis / transverse 2'),(1,2,'cross section')]):
            ax=axes[row,col];ax.scatter(good[:,u],good[:,v],s=1,c='#167d9a',label='retained steel');ax.scatter(bad[:,u],bad[:,v],s=4,c='#d62828',label='peeled points')
            ax.set_aspect('equal',adjustable='datalim');ax.set_title(f'Instance {ident} | {label}');ax.set_xlabel('mm');ax.set_ylabel('mm')
        axes[row,0].legend(fontsize=8)
    fig.suptitle('Observed contact ends: retained steel and terminal-only removals (no synthetic points)')
    fig.savefig(args.output/'terminal-multiview.png',dpi=180);plt.close(fig)
    (args.output/'summary.json').write_text(json.dumps(dict(runId=m['runId'],removedPoints=report['removedPointCount'],changedInstances=len(report['decisions']),timings=m['timings'],policy=m['acceptance']['policy']),indent=2))

if __name__=='__main__':main()
