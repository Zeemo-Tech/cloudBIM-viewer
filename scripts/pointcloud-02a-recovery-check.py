"""Verify additive 02A recovery and render a full-source local comparison.

Run with .cloudbim/mesh-venv/bin/python. The ROI is a review window, not a
manually labelled ground-truth set. Baseline and result must share Step 01/02B.
"""
import argparse
import json
from pathlib import Path
import runpy

import numpy as np


def audit(baseline, run, output, roi):
    verify = runpy.run_path(str(Path(__file__).with_name('pointcloud-02a-noise-check.py')))['verify']
    report = verify(baseline, run)
    for name in ('projection_class', 'projection_layer'):
        np.testing.assert_array_equal(np.load(baseline/f'{name}.npy', mmap_mode='r'),
                                      np.load(run/f'{name}.npy', mmap_mode='r'), err_msg=name)
    before = np.load(baseline/'geometry_class.npy', mmap_mode='r')
    after = np.load(run/'geometry_class.npy', mmap_mode='r')
    recovered = np.load(run/'geometry_recovered.npy', mmap_mode='r')
    additions = (before != 3) & (after == 3)
    assert np.all(after[before == 3] == 3), 'Existing steel must not be removed'
    assert np.all(recovered[additions] == 1), 'Every addition needs its recovery flag'
    assert report['tableLabelChanges'] == 0
    p = np.load(run/'positions.npy', mmap_mode='r')
    selected = np.ones(len(p), bool)
    for axis in range(3):
        selected &= (p[:, axis] > roi[axis*2]) & (p[:, axis] < roi[axis*2+1])
    p, a, b = p[selected], before[selected], after[selected]
    report.update(existingSteelPreserved=True, recoveryFlagsVerified=True, unchanged02B=True,
                  roiM=list(roi), regions={})
    for name, mask in [('webAndUpperWindow', p[:, 2] > .05), ('bottomWindow', p[:, 2] < .041)]:
        report['regions'][name] = {'sourcePoints': int(mask.sum()),
            'beforeSteel': int(np.count_nonzero((a == 3) & mask)),
            'afterSteel': int(np.count_nonzero((b == 3) & mask))}
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(14, 7.5), layout='constrained')
    for row, labels in enumerate((a, b)):
        for col, (x, y, title) in enumerate(((0, 2, 'Webs: side view'), (0, 1, 'Bottom parallel bars: top view'))):
            ax = axes[row, col]
            mask = np.ones(len(p), bool) if col == 0 else p[:, 2] < .041
            for cls, color in [(2, '#d89620'), (3, '#0a9487')]:
                q = p[mask & (labels == cls)]
                ax.scatter(q[:, x], q[:, y], s=.7, c=color, rasterized=True, linewidths=0)
            ax.set_title(('Before | ' if row == 0 else 'After | ')+title)
            ax.set_aspect('equal')
            ax.set_xlabel('X (m)')
            ax.set_ylabel(('Z' if col == 0 else 'Y')+' (m)')
    fig.suptitle('02A: teal = steel; amber = fixture / remainder. All source points in the window.')
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output/'before-after.png', dpi=170)
    plt.close(fig)
    (output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('run', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--roi', nargs=6, type=float, default=[2.65, 3.15, -1.60, -1.49, .018, .12],
                        metavar=('XMIN', 'XMAX', 'YMIN', 'YMAX', 'ZMIN', 'ZMAX'))
    args = parser.parse_args()
    print(json.dumps(audit(args.baseline, args.run, args.output, args.roi), ensure_ascii=False, indent=2))
