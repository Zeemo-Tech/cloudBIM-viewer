from audit import *
from algorithms.rebar_control_net import _full_model_candidates,_polyline_distances
def main():
 r=json.loads((RUN/'control-net.json').read_text());p=np.load(RUN/'positions.npy',mmap_mode='r');s=np.load(RUN/'control_status.npy');ids=np.flatnonzero(s==2);tree=cKDTree(p[ids]);records=[]
 for kind in ['straight','short','web']:
  best=np.full(len(ids),np.inf);second=best.copy();winner=np.zeros(len(ids),int)
  for row in r['instances']:
   if row['kind']!=kind or row['status']!='fitted':continue
   c=np.array(row['centerlineM']);radius=row['diameterM']/2
   local=_full_model_candidates(tree,np.arange(len(ids)),c,.012);score=np.abs(_polyline_distances(p[ids[local]],c)-radius)/max(.0009,.36*radius)
   improve=score<best[local];second[local]=np.where(improve,best[local],np.minimum(second[local],score));winner[local[improve]]=row['id'];best[local[improve]]=score[improve]
  good=best<=1.;amb=good&(second<=best+.18);unexpected=good&~amb
  records.append({'kind':kind,'pendingWithinShellUniquePoints':int(good.sum()),'competitionExplained':int(amb.sum()),'unexplained':int(unexpected.sum()),'unexplainedById':{str(i):int((winner[unexpected]==i).sum()) for i in np.unique(winner[unexpected])}})
 (OUT/'competition.json').write_text(json.dumps(records,indent=2));print(json.dumps(records,indent=2))
if __name__=='__main__':main()
