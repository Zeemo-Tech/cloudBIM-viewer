"""Independent float64 Moller-Trumbore checks of retained inward matches.
Does not call the production Open3D guard. Deterministic, stratified by bar.
"""
import json
from pathlib import Path
import sys
import numpy as np
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'services/mesh-service'))
from analysis_c2m.core import _tile_mesh
DATA=ROOT/'.cloudbim/normal-solid-audit/replay'
s=json.loads((DATA/'summary.json').read_text());root=Path(s['input']['analysis_mesh_path'])
registry=json.loads((root/'components.json').read_text())
report=json.loads((DATA/'after-report.json').read_text());trace=np.load(DATA/'after-trace.npz')
rng=np.random.default_rng(13092026);checked=0;violations=[];supported=0
for bar in report['bars']:
 lo=bar['vertexStart'];hi=lo+bar['vertexCount'];d=trace['distances'][lo:hi]
 rows=np.flatnonzero(np.isfinite(d)&(d < -1e-6))+lo
 if not len(rows):continue
 supported+=1
 # Include largest negative distances plus uniformly sampled negative results.
 samples=np.unique(np.r_[rows[np.argsort(trace['distances'][rows])[:10]],rng.choice(rows,min(len(rows),10),replace=False)])
 triangles=np.concatenate([_tile_mesh(root/t['uri']).triangles for t in registry['tiles'] if t['ifcGlobalId']==bar['ifcGlobalId']])
 e1=triangles[:,1]-triangles[:,0];e2=triangles[:,2]-triangles[:,0]
 for row in samples:
  p=trace['vertices'][row];q=trace['targets'][row];delta=q-p;length=np.linalg.norm(delta);direction=delta/length
  h=np.cross(direction,e2);det=np.einsum('ij,ij->i',e1,h);valid=np.abs(det)>1e-18
  inv=np.divide(1.,det,out=np.zeros_like(det),where=valid);rel=p-triangles[:,0]
  u=inv*np.einsum('ij,ij->i',rel,h);cross=np.cross(rel,e1);v=inv*(cross@direction);t=inv*np.einsum('ij,ij->i',e2,cross)
  hit=valid&(u>=-1e-8)&(v>=-1e-8)&(u+v<=1+1e-8)&(t>1e-6)
  first=np.min(t[hit]) if hit.any() else np.nan
  checked+=1
  if not np.isfinite(first) or length>first+2e-7:violations.append({'vertex':int(row),'distance':length,'firstExit':float(first)})
result={'method':'independent-float64-Moller-Trumbore','barsSampled':supported,'inwardMatchesChecked':checked,'violations':violations,'seed':13092026,'toleranceM':2e-7}
(HERE/'independent-verification.json').write_text(json.dumps(result,indent=2));print(result)
assert not violations
