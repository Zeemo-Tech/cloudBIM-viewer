"""Read-only Lumos upper-chord regression; not a whole-bar accuracy metric.

From the repository root, with the mesh venv:
  python docs/research/assets/lumos-instance-quality-2026-09-27/probe.py RUN_DIR
The original run fails because the clearly observed upper chord has no layer.
The fixed run must recover that layer and assign most of the sampled region.
The ROI also includes web junctions; its candidate count is NOT a bar count.
"""
import argparse
import json
from pathlib import Path

import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('run', type=Path)
args = parser.parse_args()
points = np.load(args.run / 'positions.npy', mmap_mode='r')
axes = np.load(args.run / 'region-features.npz')['frame_axes']
xy = points[:, :2] @ axes.T
labels = np.load(args.run / 'internal_instance.npy', mmap_mode='r')
roi = ((xy[:, 0] > -1.35) & (xy[:, 0] < .6)
       & (xy[:, 1] > .25) & (xy[:, 1] < .32) & (points[:, 2] > .085))
values, counts = np.unique(labels[roi], return_counts=True)
report = json.loads((args.run / 'internal-instances.json').read_text())
result = {
    'samplePointCount': int(roi.sum()),
    'assignedFraction': float(np.mean(labels[roi] > 0)),
    'largestCandidateFraction': float(max(counts[values > 0], default=0) / max(roi.sum(), 1)),
    'instanceCount': report['instanceCount'],
    'shorterThan20cm': sum(i['lengthM'] < .2 for i in report['instances']),
    'layers': report['layers'],
    'wholeBarAccuracy': 'unverified; needs labeled rods including bends and crossings',
}
print(json.dumps(result, indent=2))
assert any(b['height'] > .07 for b in report['layers']['bands']), 'Visible upper chord has no upper layer'
assert result['assignedFraction'] > .85, 'Upper chord region is largely missing instance assignments'
