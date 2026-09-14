from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
from dataclasses import replace
import json,sys
import numpy as np
from scipy.spatial import cKDTree
sys.path.insert(0,'services/mesh-service')
from algorithms.design_guided_instances import _early_exterior_support,GuidedParameters
from algorithms.rebar_extension import ATTRIBUTES, exterior_clusters, ExtensionParameters
r=Path('backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260912T092723-e388d613');names=['positions','normals','refined_class','refined_zone','internal_instance','internal_segment','internal_type','complete_instance','complete_cluster'];a={k:np.load(r/(k+'.npy'),mmap_mode='r') for k in names};internal=json.loads((r/'internal-instances.json').read_text());original={i['id']:i for i in internal['instances']}
seed_ids=[65,66,70,71,75,77,78];loose_source=np.flatnonzero((a['refined_class']==3)&(a['internal_type']!=5)&(a['internal_instance']==0));loose_labels=exterior_clusters(a['positions'][loose_source],ExtensionParameters());target_label=loose_labels[np.searchsorted(loose_source,556012)];target_rows=loose_source[loose_labels==target_label];cluster_mask=np.zeros(len(a['positions']),bool);cluster_mask[target_rows]=True;keep=(np.isin(a['internal_instance'],seed_ids)&(a['internal_type']!=5))|cluster_mask;source_rows=np.flatnonzero(keep);local={k:np.asarray(v[keep]) for k,v in a.items()};context=SimpleNamespace(**local);jobs=[(i,np.flatnonzero(local['internal_instance']==i),0) for i in seed_ids];loose=np.flatnonzero(cluster_mask[source_rows]);jobs.append((0,loose,169));fixture=np.flatnonzero(a['refined_class']==2);fixture=fixture[::max(1,len(fixture)//300000)];ftree=cKDTree(a['positions'][fixture]);fnormals=a['normals'][fixture];tip=(local['complete_instance']==78)&(local['positions'][:,1]>-1.30)
records=[]
for reach in [.5,.08]:
 out={k:np.zeros(len(source_rows),dt) for k,dt in ATTRIBUTES.items()};out['complete_class'][:]=local['refined_class'];out['complete_instance'][:]=local['internal_instance'];out['complete_segment'][:]=local['internal_segment'];segments=deepcopy(internal['segments'])
 _,_,_,_,operations=_early_exterior_support(context,jobs,original,out,segments,max(s['id'] for s in segments)+1,ftree,fnormals,replace(GuidedParameters(),exterior_reach=reach),1)
 record={'exteriorReachM':reach,'sourcePoints':len(source_rows),'tipPoints':int(tip.sum()),'assignedTo78':int(np.count_nonzero(out['complete_instance'][tip]==78)),'tipOwners':np.unique(out['complete_instance'][tip],return_counts=True)[0].tolist()};records.append(record);print(json.dumps(record))
Path(__file__).with_suffix('.json').write_text(json.dumps(records,indent=2))
assert records[0]['assignedTo78']==40 and records[0]['tipPoints']==101
assert records[1]['assignedTo78']==0
print('PASS: isolated Step 06 replay reproduces the first 40 of the 101 satellite points (remaining 61 are attached later); reducing search reach alone excludes it. Diagnostic contrast, not a production fix.')
