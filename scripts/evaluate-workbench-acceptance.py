#!/usr/bin/env python3
"""Read immutable workbench artifacts and measure observed design acceptance."""
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/mesh-service'))
from algorithms.design_acceptance import evaluate_acceptance
from rebar_design_inputs import resolve_design_inputs
p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('snapshot',type=Path);p.add_argument('output',type=Path);p.add_argument('--baseline-seconds',type=float)
a=p.parse_args();m=json.loads((a.run/'manifest.json').read_text());snap=json.loads(a.snapshot.read_text())
inventory=resolve_design_inputs(snap,snap['sourcePath'],m['source']['sha256']).inventory
r=json.loads((a.run/'complete-instances.json').read_text())
report=evaluate_acceptance(*[np.load(a.run/(name+'.npy'),mmap_mode='r') for name in ('positions','complete_instance','complete_class')],r['instances'],inventory,elapsed_s=m['timings']['totalS'],baseline_s=a.baseline_seconds)
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in report.items() if k not in ('instances','policy','topologyFailures','missingUnits','duplicateUnits')},ensure_ascii=False))
