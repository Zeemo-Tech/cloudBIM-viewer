"""Replay saved Step 05 with old/new tail filters; audit every final instance.

Run from repo root with .cloudbim/mesh-venv/bin/python. Does not modify saved
runs or start services. Full source indices and arrays stay under .cloudbim.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from importlib.util import module_from_spec, spec_from_file_location
import argparse, json, subprocess, sys, time
import numpy as np
from scipy.spatial import cKDTree
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'services/mesh-service'))
from algorithms.design_guided_instances import refine_instances
from algorithms.rebar_overlength_tails import filter_overlength_tails, retained_instance_groups

parser = argparse.ArgumentParser()
parser.add_argument('--run-id', default='20260913T123033-c6bb90c1')
parser.add_argument('--current', action='store_true')
options = parser.parse_args()
RUN = ROOT/'backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps'/options.run_id
OUT = ROOT/'.cloudbim/tail-closest-20260913'
if options.current: OUT = OUT/'current'
OUT.mkdir(parents=True, exist_ok=True)
old_path = OUT/'baseline_tails.py'
old_path.write_bytes((Path(__file__).parent/'baseline_tails_v2.py').read_bytes())
spec = spec_from_file_location('historical_tails', old_path)
old_module = module_from_spec(spec); sys.modules[spec.name] = old_module; spec.loader.exec_module(old_module)
manifest = json.loads((RUN/'manifest.json').read_text())
internal = manifest['internalRebar']; inventory = manifest['completeRebar']['designReview']['inventory']
names = ['positions','normals','refined_class','refined_zone','internal_instance','internal_segment','internal_type','internal_confidence','fused_steel_score','normal_valid','neighbor_radius']
inputs = {k:np.load(RUN/(k+'.npy'), mmap_mode='r') for k in names}
inputs['tree'] = cKDTree(inputs['positions'])
outputs = []; prefilter = {}
def current_filter(context, out, *args, **kwargs):
    for k in ('complete_class','complete_instance'):
        prefilter[k] = out[k].copy()
    return filter_overlength_tails(context, out, *args, **kwargs)
for name, implementation in [('before', old_module.filter_overlength_tails), ('after', current_filter)]:
    context = SimpleNamespace(**inputs)
    start = time.perf_counter()
    with threadpool_limits(limits=1), patch('algorithms.design_guided_instances.filter_overlength_tails', implementation):
        report = refine_instances(context, internal, inventory, mode=manifest['priorMode'], workers=16)
    print(name, report['elapsedS'], report['designReview']['overlengthTailFilter']['removedPointCount'], flush=True)
    for k in ('complete_class','complete_instance','complete_segment','complete_confidence','complete_cluster'):
        np.save(OUT/f'{name}-{k}.npy', getattr(context,k))
    (OUT/f'{name}-report.json').write_text(json.dumps(report, indent=2))
    outputs.append((context, report))
before, after = outputs
changed = np.flatnonzero(before[0].complete_class != after[0].complete_class)
np.save(OUT/'removed_source_rows.npy', changed)
assert np.all(before[0].complete_class[changed] == 3)
assert np.all(after[0].complete_class[changed] == 4)
unchanged = np.ones(len(inputs['positions']), bool); unchanged[changed] = False
for key in ('complete_class','complete_instance','complete_segment','complete_confidence'):
    np.testing.assert_array_equal(getattr(before[0],key)[unchanged], getattr(after[0],key)[unchanged])
for key in ('complete_instance','complete_segment','complete_confidence'):
    assert not np.any(getattr(after[0],key)[changed])
np.testing.assert_array_equal(before[0].complete_cluster, after[0].complete_cluster)
counts = np.bincount(after[0].complete_instance); segment_counts = np.bincount(after[0].complete_segment)
for instance in after[1]['instances']: assert instance['pointCount'] == counts[instance['id']]
for segment in after[1]['segments']: assert segment['pointCount'] == segment_counts[segment['id']]
assert after[1]['unassignedRebarPointCount'] == 0
# The audit includes every retained instance, even those without internal anchors
# or without a reliable design association (which the tail filter may skip).
groups = retained_instance_groups(prefilter)
by_owner = {i['id']:i for i in after[1]['instances']}
bars = {b['designBarId']:b['name'] for b in inventory['bars']}
audit = []
for owner, rows in groups.items():
    xyz = inputs['positions'][rows]
    center = xyz.mean(0); _,_,basis = np.linalg.svd((xyz-center)[::max(1,len(rows)//4096)], full_matrices=False)
    local = (xyz-center)@basis.T
    order = np.argsort(local[:,0]); parts = np.split(order, np.flatnonzero(np.diff(local[order,0]) > .03)+1)
    removed = after[0].complete_class[rows] == 4
    baseline_removed = before[0].complete_class[rows] == 4
    additional = removed & ~baseline_removed
    source_core = inputs['internal_instance'][rows] > 0
    record = by_owner.get(owner,{})
    blocks = [dict(points=len(p), internalPoints=int(source_core[p].sum()),
                   removedPoints=int(removed[p].sum()), axialIntervalM=[float(local[p,0].min()),float(local[p,0].max())]) for p in parts]
    audit.append(dict(instanceId=owner, designName=bars.get(record.get('designBarId')),
                      inputPoints=len(rows), retainedPoints=int((~removed).sum()),
                      removedPoints=int(removed.sum()), removedInternalPoints=int((removed&source_core).sum()),
                      additionalRemovedPoints=int(additional.sum()),
                      axialBlocks=blocks, shapeFlags=record.get('shapeReview',{}).get('flags',[])))
    np.savez(OUT/f'audit-{owner}.npz', rows=rows, local=local, removed=removed,
             baseline_removed=baseline_removed, internal=source_core)
summary = dict(baselineRun=RUN.name, sourceSha256=manifest['source']['sha256'],
               sourcePointCount=len(inputs['positions']), instanceCountBefore=before[1]['instanceCount'],
               instanceCountAfter=after[1]['instanceCount'], auditedInstances=len(audit),
               auditedRetainedInstances=sum(i['instanceId'] in by_owner for i in audit),
               additionalRemovedPoints=len(changed), changedOwners=np.unique(before[0].complete_instance[changed]).tolist(),
               preservedOtherLabels=True, preservedClusterIds=True, removedInternalPoints=int(np.count_nonzero(inputs['internal_instance'][changed])),
               beforeFilter=before[1]['designReview']['overlengthTailFilter'], afterFilter=after[1]['designReview']['overlengthTailFilter'],
               stage6BeforeS=before[1]['elapsedS'], stage6AfterS=after[1]['elapsedS'], instances=audit)
filename = 'current-replay-results.json' if options.current else 'replay-results.json'
(Path(__file__).parent/filename).write_text(json.dumps(summary, indent=2, ensure_ascii=False))
print(json.dumps({k:v for k,v in summary.items() if k not in ('beforeFilter','afterFilter','instances')},indent=2), flush=True)
