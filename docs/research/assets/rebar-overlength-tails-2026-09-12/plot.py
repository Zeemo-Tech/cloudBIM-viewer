"""Measured before/after points for instance 78; run at repository root."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

root = Path('backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps')
before = root/'20260912T100054-e6470275'
after = root/'20260912T101509-17e6a074'
points = np.load(after/'positions.npy', mmap_mode='r')
old_ids = np.load(before/'complete_instance.npy', mmap_mode='r')
ids = np.load(after/'complete_instance.npy', mmap_mode='r')
rows = np.flatnonzero(old_ids == 78)
deleted = ids[rows] != 78
internal = json.loads((before/'internal-instances.json').read_text())
segment = max((s for s in internal['segments'] if s['instanceId'] == 78), key=lambda s: s['pointCount'])
axis = np.asarray(segment['endM'])-segment['startM']
axis /= np.linalg.norm(axis)
delta = points[rows]-points[rows[~deleted]].mean(axis=0)
along = delta @ axis
if np.mean(along[deleted]) < 0:
    along = -along
along = (along-along[~deleted].min())*1000
height = delta[:, 2]*1000
fig, axes = plt.subplots(2, 1, figsize=(10, 4.8), sharex=True, sharey=True, constrained_layout=True)
for i, ax in enumerate(axes):
    ax.scatter(along[~deleted], height[~deleted], s=2, color='#2369a5', linewidths=0, alpha=.7)
    if i == 0:
        ax.scatter(along[deleted], height[deleted], s=8, color='#d94a38', linewidths=0, label='159 reclaimed points')
        ax.legend(loc='upper right', frameon=False, fontsize=9)
    ax.set_title('Before: remote points share instance #78' if i == 0 else 'After: original core preserved', loc='left', fontsize=11)
    ax.grid(alpha=.18)
    ax.set_ylabel('Relative Z (mm)')
    ax.spines[['top', 'right']].set_visible(False)
axes[-1].set_xlabel('Projection along the observed core axis (mm)')
fig.suptitle('Instance #78: 496 mm → 323 mm | design length 280 mm', fontsize=14)
fig.savefig(Path(__file__).with_name('before-after.png'), dpi=170)
