import os
os.environ['OPENBLAS_NUM_THREADS']='1'
from pathlib import Path
import sys,json,inspect,time
import numpy as np
from scipy.spatial import cKDTree
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'services/mesh-service').is_dir());sys.path.insert(0,str(ROOT/'services/mesh-service'))
from algorithms import rebar_control_net as net
import rebar_prior_axis as prior
OUT=Path('/tmp/cloudbim-bow-audit-20260915');OUT.mkdir(parents=True,exist_ok=True);RUN=ROOT/'backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260914T163600-043c4c6a'
r=json.loads((RUN/'control-net.json').read_text());p=np.load(RUN/'positions.npy');n=np.load(RUN/'normals.npy');s=np.load(RUN/'control_status.npy');o=np.load(RUN/'control_instance.npy');ids=np.flatnonzero(s!=0);tree=cKDTree(p[ids]);units=net._unit_rows(r['inventory'])
realaxis=prior.fit_prior_axis;realshell=net._piecewise_shell
asrc=inspect.getsource(realaxis).replace('if bow_fit.success and band_support(bow_error) >= .80 and (','if False and bow_fit.success and band_support(bow_error) >= .80 and (')
ag=dict(prior.__dict__);exec(asrc,ag);cubicaxis=ag['fit_prior_axis']
sg=dict(net.__dict__);exec(inspect.getsource(realshell).replace('np.linspace(low, high, 13)','np.linspace(low, high, 25)'),sg);shell24=sg['_piecewise_shell']
results=[];t=time.time()
for unit in units:
 number=unit['index']+1
 if unit['kind']!='straight' or unit['length']<=2:continue
 row=r['instances'][number-1];old=np.array(row['centerlineM']);rad=unit['radius'];tol=max(.0009,.36*rad)
 near=net._full_model_candidates(tree,ids,old,.012);d=net._polyline_distances(p[near],old);along=(p[near]-old[0])@unit['direction'];norm=np.linalg.norm(n[near],axis=1)
 near=near[(d<rad+.006)&(along>.06)&(along<unit['length']-.06)&(norm>.5)&(np.abs(n[near]@unit['direction'])<.5*norm)]
 np.savez_compressed(OUT/f'raw-{number}.npz',points=p[near],normals=n[near],source=near,status=s[near],owner=o[near])
 gate=max(.05,min(.10,.22*unit['length']),9*rad)
 candidates={k:net._candidate_indices(tree,ids,unit['start'],unit['end'],gate,neighbours=k) for k in [768,1536,3072]}
 held=~np.isin(near,np.unique(np.concatenate(list(candidates.values()))))
 def measure(c):
  res=np.abs(net._polyline_distances(p[near],c)-rad)
  med,sup=net._surface_review(p[near],n[near],c,rad)
  return {'medianMm':float(np.median(res)*1000),'p90Mm':float(np.quantile(res,.9)*1000),'support':float(np.mean(res<=tol)),'heldSupport':float(np.mean(res[held]<=tol)) if held.any() else None,'heldMedianMm':float(np.median(res[held])*1000) if held.any() else None,'bandMedianMm':(med*1000).tolist(),'bandSupport':sup.tolist()}
 results.append(dict(id=number,variant='saved',model=row['axisModel'],points=len(near),heldPoints=int(held.sum()),**measure(old)))
 np.save(OUT/f'axis-{number}-saved.npy',old)
 for variant,k,af,sf in [('baseline',768,realaxis,realshell),('k1536',1536,realaxis,realshell),('k3072',3072,realaxis,realshell),('cubic',768,cubicaxis,realshell),('bands24',768,realaxis,shell24),('cubic-bands24',768,cubicaxis,shell24)]:
  captured={}
  def capture(points,*args,**kwargs):
   axis,reason=af(points,*args,**kwargs);captured.update(axis=axis,selected=points.copy());return axis,reason
  net.fit_prior_axis=capture;net._piecewise_shell=sf
  model,reason,_=net._fit_unit(p,n,candidates[k],unit,unit['start'],unit['direction'])
  rec=dict(id=number,variant=variant,candidates=len(candidates[k]),reason=reason)
  if model:
   c=model['curve'];np.save(OUT/f'axis-{number}-{variant}.npy',c)
   rec.update(model=model['axisModel'],rmseMm=model['rmse']*1000,matchesSaved=bool(np.allclose(c,old,atol=1e-9,rtol=0)),**measure(c))
  results.append(rec)
 (OUT/'probe.json').write_text(json.dumps(results,indent=2))
 print(number,[(x['variant'],round(x.get('support',0),3),round(x.get('medianMm',0),2)) for x in results if x['id']==number],round(time.time()-t,1),flush=True)
net.fit_prior_axis=realaxis;net._piecewise_shell=realshell
