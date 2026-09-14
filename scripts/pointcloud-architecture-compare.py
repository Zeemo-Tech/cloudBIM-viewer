#!/usr/bin/env python3
"""Compare source-aligned runs, export changed points and fixed-view review images.

Use the mesh-service Python 3.11 venv. This reports behavioral changes rather
than treating matching class totals as equivalence; it never modifies either run.
"""
import argparse
import csv
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('speed_compare', ROOT/'scripts/pointcloud-speed-benchmark.py')
speed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speed)


def load(directory, name):
    return np.load(directory/f'{name}.npy', mmap_mode='r', allow_pickle=False)


def compare(baseline, candidate, output):
    result = speed.compare_runs(baseline, candidate)
    if not result['identityEqual']:
        raise ValueError('source, parameters or frozen design inputs differ')
    xyz = load(baseline, 'positions')
    old, new = load(baseline, 'complete_class'), load(candidate, 'complete_class')
    old_owner, new_owner = load(baseline, 'complete_instance'), load(candidate, 'complete_instance')
    lost = (old == 3) & (new != 3)
    gained = (old != 3) & (new == 3)
    common = (old == 3) & (new == 3)
    rows = np.flatnonzero(lost | gained)
    result['steelChange'] = dict(baseline=int(np.count_nonzero(old == 3)),
        candidate=int(np.count_nonzero(new == 3)), removed=int(lost.sum()), added=int(gained.sum()),
        changedFractionOfBaselineSteel=float(len(rows)/max(1, np.count_nonzero(old == 3))),
        intersectionOverUnion=float(common.sum()/max(1, np.count_nonzero((old == 3) | (new == 3)))))
    left, li = np.unique(old_owner[common], return_inverse=True)
    right, ri = np.unique(new_owner[common], return_inverse=True)
    if len(left) and len(right):
        overlap = np.bincount(li*len(right)+ri, minlength=len(left)*len(right)).reshape(len(left), len(right))
        a, b = linear_sum_assignment(-overlap)
        matches = [dict(baselineInstance=int(left[i]), candidateInstance=int(right[j]), sharedPoints=int(overlap[i, j]))
                   for i, j in zip(a, b) if overlap[i, j]]
        result['retainedOwnership'] = dict(commonSteelPoints=int(common.sum()),
            differingRawIds=int(np.count_nonzero(old_owner[common] != new_owner[common])),
            differingAfterBestOneToOneRemap=int(common.sum()-overlap[a, b].sum()), matches=matches)
    internal = load(candidate, 'internal_type')
    hard = load(candidate, 'shared_floating_noise')
    fused = load(candidate, 'fused_class')
    result['invariants'] = dict(
        sourceCoordinatesUnchanged=result['columns']['positions']['equal'],
        upstreamColumnsUnchanged=all(result['columns'][n]['equal'] for n in
            ['normals','normal_valid','geometry_class','projection_class','fused_class','fused_steel_score','partition_zone']),
        noStep05NoiseResurrection=not bool(np.any((internal == 5) & (new == 3))),
        noHardVetoSteelSurvives=not bool(np.any(hard.astype(bool) & (fused == 3) & (new == 3))),
        noUnassignedFinalSteel=not bool(np.any((new == 3) & (new_owner == 0))),
        noiseHasNoOwner=not bool(np.any((new == 4) & (new_owner != 0))))
    scores = load(candidate, 'fused_steel_score')
    result['changedBoundsM'] = [xyz[rows].min(0).tolist(), xyz[rows].max(0).tolist()] if len(rows) else None
    result['steelChange']['highScoreRemoved'] = int(np.count_nonzero(lost & (scores >= .9-1e-6)))
    result['steelChange']['newStep05Noise'] = int(np.count_nonzero(lost & (internal == 5)))
    result['steelChange']['step06OnlyRemoval'] = int(np.count_nonzero(lost & (internal != 5)))
    output.mkdir(parents=True, exist_ok=True)
    np.save(output/'changed-source-indices.npy', rows.astype('<u8'))
    with (output/'changed-points.csv').open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['source_index','x','y','z','before_class','after_class','before_instance',
                         'after_instance','fusion_score','after_step05_type'])
        for row in rows:
            writer.writerow([int(row), *xyz[row], int(old[row]), int(new[row]), int(old_owner[row]),
                             int(new_owner[row]), float(scores[row]), int(internal[row])])
    # Changed retained populations identify rods that deserve human inspection.
    result['affectedBaselineInstances'] = []
    for owner in np.unique(old_owner[lost]):
        group = np.flatnonzero(lost & (old_owner == owner))
        result['affectedBaselineInstances'].append(dict(instance=int(owner), removedPoints=len(group),
            boundsM=[xyz[group].min(0).tolist(), xyz[group].max(0).tolist()]))
    result['affectedBaselineInstances'].sort(key=lambda item: -item['removedPoints'])
    speed.write_json(output/'comparison.json', result)
    render(xyz, old, new, lost, gained, output)
    print(json.dumps({k: result[k] for k in ['steelChange','invariants','affectedBaselineInstances']}, indent=2))
    return result


def render(xyz, old, new, lost, gained, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9})
    origin = xyz.min(0)
    selected = np.arange(0, len(xyz), max(1, len(xyz)//250000))
    changed = np.flatnonzero(lost | gained)
    figure, axes = plt.subplots(2, 3, figsize=(15, 8), layout='constrained')
    for column, dimensions in enumerate([(0, 1), (0, 2), (1, 2)]):
        for row, labels in enumerate([old, new]):
            ax = axes[row, column]
            keep = selected[labels[selected] == 3]
            points = xyz[keep]-origin
            ax.scatter(points[:, dimensions[0]], points[:, dimensions[1]], s=.4, c='#78958e', linewidths=0)
            highlight = np.flatnonzero(lost if row == 0 else gained)
            points = xyz[highlight]-origin
            ax.scatter(points[:, dimensions[0]], points[:, dimensions[1]], s=2,
                       c='#d14343' if row == 0 else '#176bca', linewidths=0,
                       label='Removed in candidate' if row == 0 else 'Added in candidate')
            ax.set(xlabel=f'Local {"XYZ"[dimensions[0]]} (m)', ylabel=f'Local {"XYZ"[dimensions[1]]} (m)',
                   title=('Baseline' if row == 0 else 'Candidate'))
            ax.set_aspect('equal'); ax.legend(loc='upper right', markerscale=3)
        axes[1, column].set_xlim(axes[0, column].get_xlim())
        axes[1, column].set_ylim(axes[0, column].get_ylim())
    figure.suptitle(f'Same source views: {int(lost.sum()):,} removed, {int(gained.sum()):,} added\n'
        'Context sampled identically; every changed steel row highlighted; colors do not establish ground truth')
    figure.savefig(output/'overview.png', dpi=170); plt.close(figure)
    if not len(changed):
        return
    # A 20 mm connected grouping is for review windows only, never classification.
    import sys
    sys.path.insert(0, str(ROOT/'services/mesh-service'))
    from algorithms.rebar_extension import exterior_clusters, ExtensionParameters
    labels = exterior_clusters(xyz[changed], ExtensionParameters(cluster_connection_radius=.02))
    mass = np.bincount(labels)
    largest = sorted(np.unique(labels), key=lambda label: -mass[label])[:6]
    windows = []
    figure, axes = plt.subplots(len(largest), 2, figsize=(12, 3*len(largest)), squeeze=False, layout='constrained')
    for index, label in enumerate(largest):
        ids = changed[labels == label]
        lo, hi = xyz[ids].min(0)-.035, xyz[ids].max(0)+.035
        roi = np.flatnonzero(np.all((xyz >= lo) & (xyz <= hi), axis=1))
        dimensions = np.sort(np.argsort(hi-lo)[-2:])
        windows.append(dict(id=index+1, changedPointCount=len(ids), boundsM=[lo.tolist(), hi.tolist()],
                            plottedAxes=[int(d) for d in dimensions]))
        for column, classes in enumerate([old, new]):
            ax = axes[index, column]
            keep = roi[classes[roi] == 3]
            points = xyz[keep]-lo
            ax.scatter(points[:,dimensions[0]],points[:,dimensions[1]],s=1,c='#557d71',linewidths=0)
            highlights = roi[(lost if column == 0 else gained)[roi]]
            points = xyz[highlights]-lo
            ax.scatter(points[:,dimensions[0]],points[:,dimensions[1]],s=3,c='#d14343' if column == 0 else '#176bca',linewidths=0)
            ax.set(xlim=(0,hi[dimensions[0]]-lo[dimensions[0]]),ylim=(0,hi[dimensions[1]]-lo[dimensions[1]]),
                xlabel=f'{"XYZ"[dimensions[0]]} (m from window origin)',ylabel=f'{"XYZ"[dimensions[1]]} (m)',
                title=f'ROI {index+1}: '+('baseline' if column == 0 else 'candidate'))
            ax.set_aspect('equal')
    figure.suptitle('Largest spatial change groups — all steel points in each window; red = removed, blue = added')
    figure.savefig(output/'detail.png',dpi=150); plt.close(figure)
    speed.write_json(output/'review-windows.json',windows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('candidate',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    compare(args.baseline.resolve(),args.candidate.resolve(),args.output.resolve())
