"""Replay the preserved pre-fix selector and current production comparison.
Run with the project Python 3.11 venv; --inputs is an audit input JSON.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'services/mesh-service'))
import rebar_comparison as comparison
from rebar_deviation import constrained_nearest
from rebar_solid import RebarSolid

spec = importlib.util.spec_from_file_location('before', HERE / 'before_rebar_deviation.py')
legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)

class UnchangedMesh:
    def __init__(self, *args): self.diagnostics = {}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=HERE)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.inputs.read_text()); params = data.pop('parameters')
    snapshots = {}; results = {}
    for label in ['before', 'after']:
        traces = []
        def query(tree, scan, vertices, tangents, normals, **kwargs):
            solid = kwargs.pop('solid')
            if label == 'before':
                # Historical selector used a fixed 200 mm cap.
                assert kwargs.pop('max_search_distance', 0.2) == 0.2
                result = legacy.constrained_nearest(tree, scan, vertices, tangents, normals, **kwargs)
            else: result = constrained_nearest(tree, scan, vertices, tangents, normals, solid=solid, **kwargs)
            d, ix, axial, ok = result
            q = np.full(vertices.shape, np.nan); valid = ix >= 0; q[valid] = scan[ix[valid]]
            traces.append({'vertices': vertices.copy(), 'normals': normals.copy(), 'targets': q,
                           'tangents': tangents.copy() if tangents is not None else np.zeros(vertices.shape)})
            return result
        start = perf_counter()
        with patch.object(comparison, 'RebarSolid', UnchangedMesh if label == 'before' else RebarSolid), patch.object(comparison, 'constrained_nearest', query):
            result = comparison.compute_instance_comparison(**data, **params)
        wall = perf_counter() - start
        report = result['rebarComparison']; (args.output / (label + '-report.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        snapshots[label] = {key: np.concatenate([t[key] for t in traces]) for key in traces[0]}
        snapshots[label]['distances'] = result['distances']
        np.savez_compressed(args.output / (label + '-trace.npz'), **snapshots[label])
        results[label] = {'wallSeconds': wall, 'known': report['knownVertexCount'], 'unknown': report['unknownVertexCount'],
                          'stats': result['stats'], 'solid': {key: sum((b.get('solid') or {}).get(key, 0) for b in report['bars'])
                          for key in ['closedPartCount','invalidPartCount','faceCount','flippedFaceCount','inwardCandidateCount','blockedCandidateCount']}}
        print(label, json.dumps(results[label]), flush=True)
    b, a = snapshots['before']['distances'], snapshots['after']['distances']
    summary = {'input': {**data, 'parameters': params}, 'runs': results,
               'lostCoverage': int(np.sum(np.isfinite(b) & ~np.isfinite(a))),
               'gainedCoverage': int(np.sum(~np.isfinite(b) & np.isfinite(a))),
               'changedFinite': int(np.sum(np.isfinite(b) & np.isfinite(a) & (np.abs(b-a) > 1e-7))),
               'sha256': {key: hashlib.sha256(Path(data[key]).read_bytes()).hexdigest() for key in ['scan_path', 'instance_map_path']}}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print({k:v for k,v in summary.items() if k not in ['input','runs','sha256']})

if __name__ == '__main__': main()
