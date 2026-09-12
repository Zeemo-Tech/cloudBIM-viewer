"""Render the saved full-source benchmark; run using the mesh-service venv."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
result = json.loads((HERE/'results.json').read_text())
baseline = result['medians']['baseline']
candidate = result['medians']['candidate']
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
fig = plt.figure(figsize=(13, 9), layout='constrained')
grid = fig.add_gridspec(2, 2, height_ratios=(1, 1.2))
ax = fig.add_subplot(grid[0, :])
labels = ['A/B classification', 'Step 05 denoising', 'Step 06 instances', 'Algorithm stages', 'Full pipeline call']
def values(record):
    return [record['timings']['classifiersWallS'], record['internalTimings']['floatingDenoiseS'],
            record['timings']['guidedInstancesS'], record['algorithmStageWallS'], record['pipelineCallWallS']]
y = np.arange(len(labels))
for data, offset, name, color in ((values(baseline), -.18, 'Baseline 169d1a0', '#718096'),
                                   (values(candidate), .18, 'Optimized', '#008c83')):
    bars = ax.barh(y+offset, data, height=.32, label=name, color=color)
    ax.bar_label(bars, fmt='%.2f s', padding=5, fontsize=9)
ax.set_yticks(y, labels)
ax.invert_yaxis()
ax.set_xlim(0, max(values(baseline)+values(candidate))*1.20)
ax.set_xlabel('Median of 3 fresh processes; lower is better')
ax.legend(loc='lower right', frameon=False)
ax.spines[['top', 'right']].set_visible(False)
ax.grid(axis='x', alpha=.15)
ax.set_axisbelow(True)

runs = [next(r for r in result['runs'] if r['variant'] == v) for v in ('baseline', 'candidate')]
reference = Path(runs[0]['directory'])
positions = np.load(reference/'positions.npy', mmap_mode='r')
origin = positions.min(axis=0)
for column, (run, title) in enumerate(zip(runs, ('Baseline', 'Optimized'))):
    ax = fig.add_subplot(grid[1, column])
    classes = np.load(Path(run['directory'])/'complete_class.npy', mmap_mode='r')
    # Identical source-row stride and coordinate frame on both panels. The
    # equality gate covers every row; these displays are deliberately sampled.
    rows = np.arange(0, len(positions), 12)
    for code, color, name, size in ((4, '#d45d50', 'Removed noise', .5), (3, '#008c83', 'Retained steel', .35)):
        selected = rows[classes[rows] == code]
        xy = positions[selected, :2]-origin[:2]
        ax.scatter(xy[:, 0], xy[:, 1], s=size, color=color, alpha=.6, linewidths=0, rasterized=True, label=name)
    ax.set_title(f'{title}: {np.count_nonzero(classes == 3):,} steel points')
    ax.set_aspect('equal')
    ax.set_xlabel('Local X (m)')
    ax.set_ylabel('Local Y (m)')
    ax.legend(markerscale=5, loc='upper right', fontsize=8)
    ax.spines[['top', 'right']].set_visible(False)
fig.suptitle('9,216,369 source points: identical classes, noise masks, instances and confidence\n'
             'Same k=32, 16 workers, topology mode, source and design snapshot', fontsize=14)
fig.savefig(HERE/'comparison.png', dpi=170)
