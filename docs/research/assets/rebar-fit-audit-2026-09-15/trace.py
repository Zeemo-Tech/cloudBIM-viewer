from audit import *
from algorithms import rebar_control_net as net
def main():
 r=json.loads((RUN/'control-net.json').read_text());p=np.load(RUN/'positions.npy');n=np.load(RUN/'normals.npy');s=np.load(RUN/'control_status.npy');ids=np.flatnonzero(s!=0);tree=cKDTree(p[ids]);units=net._unit_rows(r['inventory']);real=net.fit_prior_axis;results=[]
 for number in [28,29,30,34]:
  unit=units[number-1];gate=max(.05,min(.10,.22*unit['length']),9*unit['radius'])
  for k in [net._NEIGHBOURS_PER_QUERY,768]:
   captured={}
   def probe(points,*args,**kwargs):
    value=real(points,*args,**kwargs);captured['selected']=points.copy();return value
   net.fit_prior_axis=probe
   ii=net._candidate_indices(tree,ids,unit['start'],unit['end'],gate,neighbours=k)
   model,reason,_=net._fit_unit(p,n,ii,unit,unit['start'],unit['direction'])
   if model is None:results.append({'id':number,'k':k,'reason':reason});continue
   c=model['curve'];np.save(OUT/f'axis-{number}-{k}.npy',c);chosen=captured['selected'];localtree=cKDTree(chosen)
   row={'id':number,'k':k,'candidateCount':len(ii),'selectedCount':len(chosen),'reason':reason,'axisModel':model['axisModel'],'rmseMm':model['rmse']*1000,
     'matchesSavedAxis':bool(np.allclose(c,r['instances'][number-1]['centerlineM'],atol=1e-10,rtol=0)),'axisEvidenceRangeM':model['axisEvidenceRangeM']}
   # Evaluate the same independent terminal records used by ends.py.
   d=np.load(OUT/'ends-data.npy',allow_pickle=True).item()[f'{number}-start'];raw=p[d['source']]
   res=np.abs(net._polyline_distances(raw,c)-unit['radius'])
   row.update(endpointMedianMm=float(np.median(res)*1000),endpointCoverage=float(np.mean(res<=model['tolerance'])),endpointPoints=len(raw),endpointPointsSelected=int((localtree.query(raw)[0]<1e-10).sum()))
   results.append(row)
   if k==net._NEIGHBOURS_PER_QUERY:np.save(OUT/f'selected-{number}.npy',chosen)
 (OUT/'trace.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
if __name__=='__main__':main()
