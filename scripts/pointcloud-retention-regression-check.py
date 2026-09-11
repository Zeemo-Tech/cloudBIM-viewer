"""Compare preservation and score propagation using original source row IDs.

Use the project Python environment. Counts describe algorithm decisions, not
ground-truth precision; the optional plot supports inspection of the lower band.
"""
import argparse
import json
from pathlib import Path

import numpy as np


def compare(baseline, previous, run, output):
    manifests = [json.loads((path / 'manifest.json').read_text()) for path in (baseline, previous, run)]
    assert len({m['source']['sha256'] for m in manifests}) == 1
    before, after = manifests[1:]
    load = lambda path, name: np.load(path / f'{name}.npy', mmap_mode='r')
    result = {'preservationBaseline': baseline.name, 'previousRun': previous.name,
              'run': run.name, 'sourcePointCount': after['source']['pointCount'], 'branches': {}}
    for name in ('geometry_class', 'projection_class', 'fused_class'):
        original, current = load(baseline, name), load(run, name)
        changed = int(np.count_nonzero(original != current))
        result['branches'][name] = {'changedRows': changed, 'steelPoints': int(np.count_nonzero(current == 3))}
        assert changed == 0, f'{name}: preserved classification regressed'
    old_score, score = load(previous, 'fused_steel_score'), load(run, 'fused_steel_score')
    high = score >= after['fusion']['score']['protectionThreshold']
    old_types, types = load(previous, 'internal_type'), load(run, 'internal_type')
    old_class, final_class = load(previous, 'complete_class'), load(run, 'complete_class')
    assert not np.any(high & ((types == 5) | (final_class != 3)))
    result['protection'] = {'previousHighPoints': int(np.count_nonzero(old_score >= .9)),
        'highPoints': int(high.sum()), 'highLostIn05Or06': 0,
        'previousNoiseNowProtectedAndRetained': int(np.count_nonzero((old_class == 4) & high & (final_class == 3)))}
    points = load(run, 'positions')
    band = after['internalRebar']['layers']['bands'][0]
    lower = (points[:, 2] >= band['low']) & (points[:, 2] <= band['high'])
    result['lowerBandM'] = [band['low'], band['high']]
    result['decisionsByScore'] = []
    for value in np.unique(score):
        mask = lower & np.isclose(score, value) & (load(run, 'fused_class') == 3)
        if not mask.any():
            continue
        result['decisionsByScore'].append({'score': float(value), 'candidatePoints': int(mask.sum()),
            'previous05Noise': int(np.count_nonzero(mask & (old_types == 5))),
            'current05Noise': int(np.count_nonzero(mask & (types == 5))),
            'current06Noise': int(np.count_nonzero(mask & (final_class == 4)))})
    for stage, old, new in (('05', old_types == 5, types == 5), ('06', old_class == 4, final_class == 4)):
        result[stage] = {'previousNoise': int(old.sum()), 'currentNoise': int(new.sum()),
            'newlyRemoved': int(np.count_nonzero(new & ~old)), 'restored': int(np.count_nonzero(old & ~new)),
            'lowerBandNewlyRemoved': int(np.count_nonzero(lower & new & ~old))}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    candidates = np.flatnonzero(lower & (load(run, 'fused_class') == 3))
    sample = candidates[::max(1, int(np.ceil(len(candidates) / 180_000)))]
    origin = points[candidates, :2].min(axis=0)
    newly_removed = np.flatnonzero(lower & (types == 5) & (old_types != 5))
    fig, axis = plt.subplots(figsize=(13, 6))
    xy = (points[sample, :2] - origin) * 1000
    axis.scatter(xy[:, 0], xy[:, 1], s=.2, color='#8593a3', label='Lower-band candidates (sampled)', rasterized=True)
    if len(newly_removed):
        xy = (points[newly_removed, :2] - origin) * 1000
        axis.scatter(xy[:, 0], xy[:, 1], s=4, color='#e6550d', label=f'New Step 05 removals ({len(newly_removed):,})', rasterized=True)
    axis.set(xlabel='X offset (mm)', ylabel='Y offset (mm)', aspect='equal',
             title='Lower height band: source-row comparison (removals are not ground-truth labels)')
    axis.legend(markerscale=3)
    fig.tight_layout()
    fig.savefig(output / 'lower-band-comparison.png', dpi=180)
    plt.close(fig)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('baseline', 'previous', 'run', 'output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.baseline, args.previous, args.run, args.output), ensure_ascii=False, indent=2))
