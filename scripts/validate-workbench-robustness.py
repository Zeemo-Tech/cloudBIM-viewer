#!/usr/bin/env python3
"""Sequential same-machine workbench baseline/ablation runs; never overwrite runs."""
import argparse,csv,json,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'services/mesh-service'))
from pointcloud_step_pipeline import run_from_source
from algorithms.design_acceptance import evaluate_acceptance
from algorithms.design_evidence_contract import RobustnessPolicy

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--workers',type=int,default=16);p.add_argument('--k',type=int,default=32)
p.add_argument('--modes',nargs='+',default=['off','on','no-candidates','no-reclassification','no-topology'],choices=['off','on','no-candidates','no-reclassification','no-topology'])
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
snapshot=json.loads(a.snapshot.read_text());summary=[];baseline=None;baseline_s=None;last=[0.]
def progress(stage,done,total):
    if time.monotonic()-last[0]>15:print(stage,done,total,flush=True);last[0]=time.monotonic()
for mode in a.modes:
    policy=RobustnessPolicy(enable_candidates=mode!='no-candidates',enable_reclassification=mode!='no-reclassification',enable_topology_retry=mode!='no-topology')
    run=run_from_source(Path(snapshot['sourcePath']),a.output/mode,workers=a.workers,k=a.k,through_step=7,prior_mode='topology',design_prior=snapshot,robustness_mode='off' if mode=='off' else 'design-evidence',robustness_policy=policy,baseline_seconds=baseline_s,preview_limit=300000,progress=progress)
    manifest=run.manifest
    if mode=='off':baseline=run.directory;baseline_s=manifest['timings']['totalS']
    acceptance=manifest.get('acceptance') or evaluate_acceptance(run.context.positions,run.context.complete_instance,run.context.complete_class,manifest['completeRebar']['instances'],snapshot['inventory'],elapsed_s=manifest['timings']['totalS'],baseline_s=baseline_s)
    (a.output/f'{mode}-acceptance.json').write_text(json.dumps(acceptance,ensure_ascii=False,indent=2))
    with (a.output/f'{mode}-instances.csv').open('w',newline='') as f:
        columns=['instanceId','designUnitId','kind','pointCount','lengthM','designLengthM','lengthErrorM','diameterM','designDiameterM','positionErrorM','angleErrorDegrees','passed','reasons']
        writer=csv.DictWriter(f,columns,extrasaction='ignore');writer.writeheader();writer.writerows(acceptance['instances'])
    changes={}
    if baseline is not None and mode!='off':
        old=np.load(baseline/'complete_class.npy',mmap_mode='r');new=run.context.complete_class
        changes=dict(changedClassPoints=int(np.count_nonzero(old!=new)),newSteelPoints=int(np.count_nonzero((old!=3)&(new==3))),removedSteelPoints=int(np.count_nonzero((old==3)&(new!=3))))
    entry=dict(mode=mode,runId=manifest['runId'],directory=str(run.directory),timings=manifest['timings'],status=acceptance['status'],geometryPassed=acceptance['geometryPassed'],expected=acceptance['expectedInstances'],observed=acceptance['observedInstances'],failedInstances=acceptance['failedInstances'],missing=acceptance['missingUnits'],duplicates=acceptance['duplicateUnits'],topologyFailures=len(acceptance['topologyFailures']),**changes)
    summary.append(entry);(a.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(entry,ensure_ascii=False),flush=True)
    del run
