"""Check a published full-source run; optionally compare prior fitted steel.

Run with .cloudbim/mesh-venv/bin/python, passing run directory and --baseline.
"""
import argparse
import json
from pathlib import Path
import numpy as np


def check(directory, baseline=None):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    array = lambda name: np.load(directory/f'{name}.npy', mmap_mode='r')
    hard = array('shared_floating_noise').astype(bool)
    table, zones = array('shared_table_mask'), array('partition_zone')
    global_scope = manifest.get('preprocessing', {}).get('floatingZones', {}).get('forbiddenRule', {}).get('scope') == 'all-source-points'
    if not global_scope:
        assert not np.any(hard & (table != 0)), 'table was hard-rejected'
        assert not np.any(hard & ~np.isin(zones, [1, 3])), 'fixture band or unlocated row was hard-rejected'
    else:
        ids = np.load(directory/'retained/source_record_indices.npy') if hard.any() else np.arange(len(hard))
        np.testing.assert_array_equal(ids, np.flatnonzero(~hard))
        assert np.all(array('internal_type')[hard] == 5) if (directory/'internal_type.npy').exists() else True
    stages = {}
    for name in ['geometry_class', 'projection_class', 'fused_class', 'refined_class', 'complete_class']:
        path = directory/f'{name}.npy'
        if not path.exists():
            continue
        labels = array(name)
        assert len(labels) == len(hard)
        assert np.all(labels[hard] == 4), f'{name} restored a forbidden point'
        stages[name] = {str(i): int(n) for i, n in enumerate(np.bincount(labels, minlength=5))}
    if (directory/'fused_class.npy').exists():
        veto = hard | ((array('geometry_class') == 4) & (array('projection_class') == 4))
        assert np.all(array('fused_class')[veto] == 4), 'fusion restored branch noise'
        assert not np.any(array('fused_steel_score')[veto]), 'removed point has steel score'
    baseline_review = None
    if baseline:
        baseline = Path(baseline)
        before = json.loads((baseline/'manifest.json').read_text())
        assert before['source']['sha256'] == manifest['source']['sha256'], 'baseline source differs'
        types = np.load(baseline/'internal_type.npy', mmap_mode='r')
        fitted = np.isin(types, [1, 2, 3])
        baseline_review = {'previousFittedSteel': int(fitted.sum()), 'designHardHits': int(np.count_nonzero(fitted & hard)),
            'step05NoiseHits': int(np.count_nonzero(fitted & (array('internal_type') == 5))) if (directory/'internal_type.npy').exists() else None}
    report = {'runId': manifest['runId'], 'sourcePoints': len(hard), 'hardForbiddenPoints': int(hard.sum()),
              'layerCounts': {str(i): int(n) for i, n in enumerate(np.bincount(array('shared_layer')))},
              'classes': stages, 'baseline': baseline_review, 'totalS': manifest['timings']['totalS'],
              'step05': {k: manifest.get('internalRebar', {}).get('denoising', {}).get(k) for k in ('reviewedCandidatePointCount', 'removedPointCount', 'confirmedSteelReviewExcluded')},
              'assertions': 'source-order arrays, declared exclusion scope, retained input population, branch/fusion/final hard veto passed'}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check(args.directory, args.baseline)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+'\n')
    print(text)
