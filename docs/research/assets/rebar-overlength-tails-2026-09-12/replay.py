"""Run from the repository root with .cloudbim/mesh-venv/bin/python.

Replays only Step 06 from immutable saved Step 05 arrays. It does not start a
service or replace a published run. Twenty paired warm measurements include
the grouping reused by final quality statistics; the paired delta isolates
the tail filter from that existing cost.
"""
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
import hashlib, json, sys, time
import numpy as np
from scipy.spatial import cKDTree
from threadpoolctl import threadpool_limits
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'services/mesh-service'))
from algorithms.design_guided_instances import refine_instances, GuidedParameters
import algorithms.rebar_overlength_tails as tails
RUN=ROOT/'backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260912T100054-e6470275'
OUT=ROOT/'.cloudbim/overlength-tail-validation'
OUT.mkdir(parents=True, exist_ok=True)
manifest=json.loads((RUN/'manifest.json').read_text())
internal=json.loads((RUN/'internal-instances.json').read_text())
inventory=manifest['completeRebar']['designReview']['inventory']
names=['positions','normals','refined_class','refined_zone','internal_instance','internal_segment','internal_type','internal_confidence','fused_steel_score','normal_valid','neighbor_radius']
context=SimpleNamespace(**{k:np.load(RUN/(k+'.npy'),mmap_mode='r') for k in names})
print('building reused source tree',flush=True);context.tree=cKDTree(context.positions)
original=tails.filter_overlength_tails;capture={}
def capture_filter(*args,**kwargs):
    capture['args']=list(args)
    capture['args'][1]={k:v.copy() for k,v in args[1].items()}
    capture['args'][2]=deepcopy(args[2])
    capture['kwargs']={**kwargs,'enabled':True}
    return original(*args,**kwargs)
def progress(stage,done,total):
    if done==0:print(stage,flush=True)
with threadpool_limits(limits=1):
    with patch('algorithms.design_guided_instances.filter_overlength_tails',capture_filter):
        baseline=refine_instances(context,internal,inventory,mode=manifest['priorMode'],workers=16,
            params=replace(GuidedParameters(),overlength_tail_filter=False),progress=progress)
    old={k:getattr(context,k).copy() for k in ['complete_class','complete_instance','complete_segment','complete_confidence']}
    result=refine_instances(context,internal,inventory,mode=manifest['priorMode'],workers=16,progress=progress)
    diff=np.flatnonzero(old['complete_class']!=context.complete_class)
    np.save(OUT/'removed_source_rows.npy',diff)
    print('FILTER',json.dumps(result['designReview']['overlengthTailFilter']),flush=True)
    unchanged=np.ones(len(context.positions),bool);unchanged[diff]=False
    for key in old:
        np.testing.assert_array_equal(old[key][unchanged],getattr(context,key)[unchanged])
    assert np.all(old['complete_class'][diff]==3) and np.all(context.complete_class[diff]==4)
    assert not np.any(context.complete_instance[diff])
    counts=np.bincount(context.complete_instance);seg_counts=np.bincount(context.complete_segment)
    for item in result['instances']:assert item['pointCount']==counts[item['id']]
    for segment in result['segments']:assert segment['pointCount']==seg_counts[segment['id']]
    assert result['unassignedRebarPointCount']==0
    durations={False:[],True:[]}
    for i in range(21):
        for enabled in (False,True):
            args=list(capture['args']);args[1]={k:v.copy() for k,v in args[1].items()};args[2]=deepcopy(args[2])
            kwargs={**capture['kwargs'],'enabled':enabled}
            started=time.perf_counter();_,report,_=original(*args,**kwargs);elapsed=time.perf_counter()-started
            if i:durations[enabled].append(elapsed)
            if enabled:assert report['removedPointCount']==len(diff)
    summary=dict(baselineRun=RUN.name,sourcePointCount=len(context.positions),
        sourceSha256=manifest['source']['sha256'], workers=16, measuredRepetitions=20,
        codeSha256={name:hashlib.sha256((ROOT/'services/mesh-service/algorithms'/name).read_bytes()).hexdigest()
            for name in ('design_guided_instances.py','rebar_overlength_tails.py','rebar_cluster_quality.py')},
        baselineStage6S=baseline['elapsedS'],enabledStage6S=result['elapsedS'],
        baselineInstanceCount=baseline['instanceCount'],instanceCount=result['instanceCount'],
        changedPoints=len(diff),filter=result['designReview']['overlengthTailFilter'],
        isolatedTimings={str(k):dict(medianS=float(np.median(v)),p95S=float(np.quantile(v,.95))) for k,v in durations.items()},
        pairedAddedMedianS=float(np.median(np.array(durations[True])-durations[False])),
        instance78Before=next((i for i in baseline['instances'] if i['id']==78),None),
        instance78After=next((i for i in result['instances'] if i['id']==78),None))
    (OUT/'result.json').write_text(json.dumps(summary,indent=2))
    (OUT/'complete-instances.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('filter','instance78Before','instance78After')},indent=2),flush=True)
