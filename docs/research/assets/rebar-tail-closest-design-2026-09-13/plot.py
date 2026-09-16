"""Render every instance in two transverse projections, including all source points."""
from pathlib import Path
import json, argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).parent
DATA = ROOT/'.cloudbim/tail-closest-20260913'
parser = argparse.ArgumentParser()
parser.add_argument('--current', action='store_true')
options = parser.parse_args()
if options.current: DATA = DATA/'current'
records = json.loads((OUT/('current-replay-results.json' if options.current else 'replay-results.json')).read_text())['instances']
prefix = 'current-atlas' if options.current else 'atlas'
for offset in range(0,len(records),12):
    fig, axs = plt.subplots(4,3,figsize=(16,13))
    for ax,record in zip(axs.flat,records[offset:offset+12]):
        d = np.load(DATA/f"audit-{record['instanceId']}.npz")
        p = d['local']; removed = d['removed']; baseline_removed = d['baseline_removed']
        for j,color in [(1,'#7696af'),(2,'#164967')]:
            ax.scatter(p[~removed,0],p[~removed,j]*1000,s=.3,color=color,rasterized=True)
            ax.scatter(p[baseline_removed,0],p[baseline_removed,j]*1000,s=.3,color='#bbbbbb',rasterized=True)
            extra = removed & ~baseline_removed
            ax.scatter(p[extra,0],p[extra,j]*1000,s=2,color='#d53f2a',rasterized=True)
        name = (record['designName'] or '').split(':')
        name = name[-2] if len(name)>1 else '?'
        ax.set_title(f"#{record['instanceId']} / BIM {name} | {record['inputPoints']} pts | new removal {record['additionalRemovedPoints']}",fontsize=9)
        ax.set_xlabel('Axial position (m)',fontsize=8);ax.set_ylabel('Transverse (mm)',fontsize=8)
        ax.tick_params(labelsize=7);ax.grid(alpha=.15)
    for ax in list(axs.flat)[len(records[offset:offset+12]):]: ax.set_visible(False)
    fig.suptitle(f'All-instance tail audit {offset//12+1:02d} | blue: retained, red: NEW removal, grey: baseline noise | full points / 2 projections',fontsize=11)
    fig.tight_layout();fig.savefig(OUT/f'{prefix}-{offset//12+1:02d}.png',dpi=100);plt.close(fig)
print('Rendered',len(records),'instances',flush=True)
