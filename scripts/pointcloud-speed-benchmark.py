#!/usr/bin/env python3
"""Benchmark a committed baseline against this checkout, preserving every source row.

Use .cloudbim/mesh-venv/bin/python. Each run is a fresh offline process, with a
fixed source and design snapshot; no HTTP service or live workbench is modified.
Full artifacts are retained under --output (allow several GB per run).
"""
import argparse
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 262144


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def semantic_report(value, run_id=''):
    """Remove only runtime measurements, never counts, decisions or geometry."""
    if isinstance(value, dict):
        return {key: semantic_report(item, run_id) for key, item in value.items()
                if key not in ('elapsedS', 'timings', 'totalS', 'modelS', 'groupingS', 'designEvidenceS',
                               'measurementS', 'updateS', 'decisionS')}
    if isinstance(value, list):
        return [semantic_report(item, run_id) for item in value]
    if run_id and isinstance(value, str) and value.startswith(f'/runs/{run_id}/'):
        return '/runs/<run>/'+value[len(f'/runs/{run_id}/'):]
    return value


def compare_runs(baseline, candidate):
    baseline, candidate = Path(baseline), Path(candidate)
    left = json.loads((baseline/'manifest.json').read_text())
    right = json.loads((candidate/'manifest.json').read_text())
    identity = (left['source'] == right['source'] and left['parameters'] == right['parameters']
                and left.get('priorMode') == right.get('priorMode')
                and left.get('designInputs') == right.get('designInputs'))
    columns = {}
    lcols, rcols = left['attributes']['columns'], right['attributes']['columns']
    for name in sorted(lcols.keys() | rcols.keys()):
        if name not in lcols or name not in rcols:
            columns[name] = {'equal': False, 'reason': 'column missing'}
            continue
        a = np.load(baseline/lcols[name], mmap_mode='r', allow_pickle=False)
        b = np.load(candidate/rcols[name], mmap_mode='r', allow_pickle=False)
        if a.shape != b.shape or a.dtype != b.dtype:
            columns[name] = {'equal': False, 'reason': 'shape or dtype changed'}
            continue
        changed = 0
        max_error = 0.
        confusion = np.zeros((5, 5), dtype=np.int64) if name.endswith('_class') else None
        for start in range(0, len(a), CHUNK):
            x, y = a[start:start+CHUNK], b[start:start+CHUNK]
            equal = x == y
            if a.dtype.kind == 'f':
                equal |= np.isnan(x) & np.isnan(y)
                finite = np.isfinite(x) & np.isfinite(y)
                if finite.any():
                    max_error = max(max_error, float(np.max(np.abs(x[finite].astype(float)-y[finite]))))
            rows_equal = equal.reshape(len(x), -1).all(axis=1)
            changed += int(np.count_nonzero(~rows_equal))
            if confusion is not None:
                if np.any(x > 4) or np.any(y > 4):
                    raise ValueError(f'unexpected class code: {name}')
                confusion += np.bincount(x.astype(np.int64)*5+y, minlength=25).reshape(5, 5)
        record = {'equal': changed == 0, 'changedRows': changed, 'totalRows': len(a)}
        if a.dtype.kind == 'f':
            record['maxAbsError'] = max_error
        if confusion is not None:
            record['confusionBaselineRowsCandidateColumns'] = confusion.tolist()
        columns[name] = record
    reports = {name: semantic_report(left.get(name), left.get('runId', '')) == semantic_report(right.get(name), right.get('runId', ''))
               for name in ('preprocessing', 'classification', 'projection', 'fusion', 'regions',
                            'refinement', 'internalRebar', 'completeRebar')}
    return {'baselineRun': baseline.name, 'candidateRun': candidate.name,
            'identityEqual': identity, 'columns': columns, 'semanticReportsEqual': reports,
            'passed': identity and all(c['equal'] for c in columns.values()) and all(reports.values())}


def worker(args):
    # Import exclusively from the selected checkout/archive, never from a
    # baseline process's working directory or a previously imported pipeline.
    sys.path.insert(0, str(args.code_root/'services/mesh-service'))
    from pointcloud_step_pipeline import run_from_source
    snapshot = json.loads(args.snapshot.read_text())
    began = time.perf_counter()
    run = run_from_source(Path(snapshot['sourcePath']), args.output, k=args.k, workers=args.workers,
                          through_step=7, prior_mode=args.mode, design_prior=snapshot)
    record = {'directory': str(run.directory), 'pipelineCallWallS': time.perf_counter()-began,
              'timings': run.manifest['timings'], 'internalTimings': run.manifest['internalRebar']['timings'],
              'cpuS': run.manifest['performance']['cpuS'],
              'peakRssMB': run.manifest['performance']['peakRssMB']}
    # The A/B branches overlap: sum their enclosing wall timer exactly once.
    record['algorithmStageWallS'] = sum(record['timings'][key] for key in (
        'treeS', 'normalsS', 'preprocessingS', 'classifiersWallS', 'fusionS',
        'regionsS', 'refinementS', 'internalRebarS', 'guidedInstancesS'))
    write_json(args.record, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--snapshot', type=Path, required=True, help='frozen prepare_snapshot output JSON')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--k', type=int, default=32)
    parser.add_argument('--mode', choices=('geometry', 'topology'), default='topology')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--code-root', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--record', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output, args.snapshot = args.output.resolve(), args.snapshot.resolve()
    if args.worker:
        worker(args)
        return
    if args.rounds < 3:
        parser.error('use at least three alternating rounds')
    if not args.baseline_ref:
        parser.error('--baseline-ref is required')
    if args.output.exists():
        parser.error('--output must be new to preserve previous evidence')
    baseline_sha = subprocess.check_output(['git', 'rev-parse', '--verify', args.baseline_ref+'^{commit}'], cwd=ROOT, text=True).strip()
    args.output.mkdir(parents=True)
    frozen_snapshot = args.output/'snapshot.json'
    frozen_snapshot.write_bytes(args.snapshot.read_bytes())
    snapshot = json.loads(frozen_snapshot.read_text())
    base_root = args.output/'baseline-code'
    base_root.mkdir()
    archive = args.output/'baseline.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', 'archive', baseline_sha, 'services/mesh-service'], cwd=ROOT, stdout=stream, check=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(base_root, filter='data')
    archive.unlink()
    code_files = sorted((ROOT/'services/mesh-service').rglob('*.py'))
    code_hash = hashlib.sha256()
    for path in code_files:
        code_hash.update(str(path.relative_to(ROOT)).encode())
        code_hash.update(path.read_bytes())
    candidate_root = args.output/'candidate-code'
    for path in code_files:
        target = candidate_root/path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    result = {'schema': 'pointcloud-speed-benchmark-v1', 'baselineCommit': baseline_sha,
              'candidateHead': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'candidatePythonSha256': code_hash.hexdigest(),
              'sourceSha256': snapshot['sourceSha256'], 'snapshotFingerprint': snapshot['fingerprint'],
              'parameters': {'k': args.k, 'workers': args.workers, 'mode': args.mode, 'throughStep': 7},
              'timingScope': 'fresh offline processes; OS page cache retained; no competing benchmark processes; totalS excludes final manifest publication; pipelineCallWallS includes it; processWallS also includes imports',
              'runs': [], 'comparisons': []}
    reference = None
    for repeat in range(args.rounds):
        # Counterbalance startup/cache drift while keeping workloads sequential.
        variants = ('baseline', 'candidate') if repeat % 2 == 0 else ('candidate', 'baseline')
        for variant in variants:
            name = f'{repeat+1}-{variant}'
            record_path = args.output/(name+'.json')
            command = [sys.executable, str(Path(__file__).resolve()), '--worker',
                       '--code-root', str(base_root if variant == 'baseline' else candidate_root),
                       '--snapshot', str(frozen_snapshot), '--output', str(args.output/name),
                       '--workers', str(args.workers), '--k', str(args.k), '--mode', args.mode,
                       '--record', str(record_path)]
            start = time.perf_counter()
            with (args.output/(name+'.log')).open('w') as stream:
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=900, cwd=ROOT)
            record = json.loads(record_path.read_text())
            record.update(variant=variant, repeat=repeat+1, processWallS=time.perf_counter()-start)
            result['runs'].append(record)
            if reference is None:
                reference = record['directory']
            result['comparisons'].append(compare_runs(reference, record['directory']))
            write_json(args.output/'results.json', result)
            print(json.dumps({'run': name, 'totalS': record['timings']['totalS'],
                              'equal': result['comparisons'][-1]['passed']}), flush=True)
    result['medians'] = {}
    for variant in ('baseline', 'candidate'):
        rows = [r for r in result['runs'] if r['variant'] == variant]
        result['medians'][variant] = {key: statistics.median(r[key] for r in rows)
                                      for key in ('processWallS', 'pipelineCallWallS', 'algorithmStageWallS', 'cpuS', 'peakRssMB')}
        result['medians'][variant]['timings'] = {key: statistics.median(r['timings'][key] for r in rows)
                                                for key in rows[0]['timings']}
        result['medians'][variant]['internalTimings'] = {key: statistics.median(r['internalTimings'][key] for r in rows)
                                                        for key in rows[0]['internalTimings']}
    result['allOutputsEqual'] = all(c['passed'] for c in result['comparisons'])
    before, after = [result['medians'][v]['pipelineCallWallS'] for v in ('baseline', 'candidate')]
    result['speedup'] = before/after
    result['timeReductionFraction'] = 1-after/before
    write_json(args.output/'results.json', result)
    print(json.dumps({'allOutputsEqual': result['allOutputsEqual'], 'speedup': result['speedup'],
                      'results': str(args.output/'results.json')}, indent=2))
    if not result['allOutputsEqual']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
