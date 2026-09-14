from audit import *
from scipy.optimize import least_squares
from algorithms.rebar_control_net import _frame,_full_model_candidates
def main():
 r=json.loads((RUN/'control-net.json').read_text());p=np.load(RUN/'positions.npy',mmap_mode='r');n=np.load(RUN/'normals.npy',mmap_mode='r');s=np.load(RUN/'control_status.npy');o=np.load(RUN/'control_instance.npy')
 ids=np.flatnonzero(s!=0);tree=cKDTree(p[ids]);out=[];data={}
 for row in r['instances']:
  if row['kind']!='straight' or not 1.<row['designLengthM']<1.3:continue
  c=np.array(row['centerlineM']);radius=row['diameterM']/2;tol=max(.0009,.36*radius)
  for side in ['start','end']:
   a,b=c[:2] if side=='start' else c[-1:-3:-1];t=(b-a)/np.linalg.norm(b-a);u,v=_frame(t)
   ii=_full_model_candidates(tree,ids,np.array([a,a+.11*t]),.014);delta=p[ii]-a;along=delta@t
   mask=(along>.015)&(along<.095)&(np.abs(n[ii]@t)<.5)
   ii=ii[mask];along=along[mask];xy=np.column_stack(((p[ii]-a)@u,(p[ii]-a)@v))
   if len(ii)<80:continue
   # Broad corridor retained for before/after metrics; spatially alternating validation bands.
   band=((along-.015)/.01).astype(int);train=band%2==0;held=~train
   def residual(off):return np.linalg.norm(xy-off,axis=1)-radius
   fits=[least_squares(lambda off:residual(off)[train], [x,y],bounds=(-.01,.01),loss='soft_l1',f_scale=.0004) for x in [-.004,0,.004] for y in [-.004,0,.004]]
   best=min(fits,key=lambda f:f.cost);old=np.abs(residual(np.zeros(2)));new=np.abs(residual(best.x))
   rec={'id':row['id'],'side':side,'points':len(ii),'pending':int((o[ii]==0).sum()),'oldMedianMm':float(np.median(old[held])*1000),'newMedianMm':float(np.median(new[held])*1000),
     'oldHeldCoverage':float(np.mean(old[held]<=tol)),'newHeldCoverage':float(np.mean(new[held]<=tol)), 'shiftMm':float(np.linalg.norm(best.x)*1000),'shiftUVmm':(best.x*1000).tolist(), 'axisModel':row['axisModel']}
   out.append(rec);data[f"{row['id']}-{side}"]={'xy':xy,'along':along,'owner':o[ii],'offset':best.x,'radius':radius,'held':held,'source':ii}
 (OUT/'ends.json').write_text(json.dumps(out,indent=2));np.save(OUT/'ends-data.npy',data,allow_pickle=True)
 for x in sorted(out,key=lambda x:x['oldMedianMm'],reverse=True)[:18]:print(json.dumps(x))
if __name__=='__main__':main()
