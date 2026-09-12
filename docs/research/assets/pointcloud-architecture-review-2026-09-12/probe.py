"""Read-only cached Step 05 replay; process-local ablation, no source edits."""
import sys, json, time, gc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from threadpoolctl import threadpool_limits
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'services/mesh-service'))
from algorithms.design_guided_instances import refine_instances, ATTRIBUTES
from algorithms import floating_noise, rebar_cluster_quality, rebar_final_filter

RUN = ROOT/'.cloudbim/speed-review-20260912/final/3-candidate/20260912T104253-e5f44619'
manifest = json.loads((RUN/'manifest.json').read_text())
inventory = json.loads((ROOT/'.cloudbim/speed-review-20260912/snapshot.json').read_text())['inventory']
names = ['positions','normals','refined_class','refined_zone','internal_type',
         'internal_instance','internal_segment','internal_confidence','fused_steel_score']
inputs = {n: np.load(RUN/f'{n}.npy', mmap_mode='r') for n in names}
inputs['tree'] = cKDTree(inputs['positions'])
original_float = floating_noise.floating_noise_mask
original_fragment = rebar_cluster_quality.final_fragment_filter
original_cluster = rebar_final_filter.filter_final_clusters
results = dict(commit='6d78b3a', inputRun=str(RUN.relative_to(ROOT)),
               source=manifest['source'], pointCount=len(inputs['positions']),
               scope='single cached Step 06 replay per mode; shared full-source tree built outside timing; no publishing; report provenance may differ', runs=[])

for mode in ['baseline', 'skip_exterior_residual_review']:
    context = SimpleNamespace(**inputs)
    timings = {}
    def floating(points, *args, **kwargs):
        t = time.perf_counter()
        if mode == 'skip_exterior_residual_review':
            result = (np.zeros(len(points), bool), {'removedPointCount': 0, 'probeSkipped': True})
        else:
            result = original_float(points, *args, **kwargs)
        timings['exteriorReview'] = dict(elapsedS=time.perf_counter()-t, candidates=len(points), removed=result[1]['removedPointCount'])
        return result
    def fragment(context, out, **kwargs):
        t = time.perf_counter()
        result = original_fragment(context, out, **kwargs)
        timings['fragmentReview'] = dict(elapsedS=time.perf_counter()-t, **result)
        return result
    def cluster(*args, **kwargs):
        t = time.perf_counter()
        result = original_cluster(*args, **kwargs)
        timings['clusterReview'] = dict(elapsedS=time.perf_counter()-t,
            reviewed=result['reviewedComponentCount'], removed=result['removedPointCount'],
            removedOwners=result['removedInstanceCount'])
        return result
    t = time.perf_counter()
    with threadpool_limits(limits=1), patch.object(floating_noise, 'floating_noise_mask', floating), \
         patch.object(rebar_cluster_quality, 'final_fragment_filter', fragment), \
         patch.object(rebar_final_filter, 'filter_final_clusters', cluster):
        report = refine_instances(context, manifest['internalRebar'], inventory, mode='topology', workers=16)
    elapsed = time.perf_counter()-t
    diffs = {}
    for name in ATTRIBUTES:
        before = np.load(RUN/f'{name}.npy', mmap_mode='r')
        after = getattr(context, name)
        diffs[name] = int(np.count_nonzero(before != after))
    review = report['designReview']
    result = dict(mode=mode, wallS=elapsed, timings=timings, differingSourceRows=diffs,
        counts=report['counts'], instanceCount=report['instanceCount'],
        finalUnassignedNoise=review['finalUnassignedNoise'])
    results['runs'].append(result)
    print(json.dumps(result), flush=True)
    del context, report, after
    gc.collect()

(Path(__file__).parent/'probe.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))
