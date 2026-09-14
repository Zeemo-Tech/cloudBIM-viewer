import os
os.environ['OPENBLAS_NUM_THREADS']='1'
from pathlib import Path
import sys,json,time
import numpy as np
from scipy.spatial import cKDTree
ROOT=next(path for path in Path(__file__).resolve().parents if (path/'services/mesh-service').is_dir())
sys.path.insert(0,str(ROOT/'services/mesh-service'))
from algorithms.rebar_control_net import _full_model_candidates,_polyline_distances
from algorithms.rebar_control_curves import _project
RUN=ROOT/'backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260914T155930-098fe029'
OUT=Path('/tmp/cloudbim-fit-audit-20260915')
OUT.mkdir(parents=True,exist_ok=True)
def main():
 t=time.time();r=json.loads((RUN/'control-net.json').read_text())
 p=np.load(RUN/'positions.npy',mmap_mode='r');n=np.load(RUN/'normals.npy',mmap_mode='r');s=np.load(RUN/'control_status.npy');o=np.load(RUN/'control_instance.npy')
 ids=np.flatnonzero(s!=0);tree=cKDTree(p[ids]);rows=[]
 for kind,items in [('body',r['instances']),('curve',r['curvedPieces'])]:
  for row in items:
   c=np.array(row.get('centerlineM',row.get('inferredCenterlineM',[])))
   if len(c)<2:continue
   rad=row['diameterM']/2;tol=max(.0009,.36*rad)
   near=_full_model_candidates(tree,ids,c,.012)
   dist,radial,tangent,station,side=_project(p[near],c)
   res=np.abs(dist-rad);shell=res<=tol;pending=o[near]==0
   valid=np.linalg.norm(n[near],axis=1)>.5
   align=np.abs(np.einsum('ij,ij->i',n[near],radial))/np.maximum(np.linalg.norm(n[near],axis=1),1e-12)
   good=shell&side&(~valid|(align>=.75))
   rec={'type':kind,'id':row.get('unitIds',row.get('id')),'kind':row['kind'],'status':row['status'],'reason':row['reason'],
    'toleranceMm':tol*1000,'shellPoints':int(shell.sum()),'pendingInsideShell':int((shell&pending).sum()),
    'pendingInsideShellSideNormal':int((good&pending).sum()),'pendingWithin3mm':int(((res<=.003)&pending).sum()),
    'pendingWithin6mm':int(((res<=.006)&pending).sum()),'ownedWithin6mm':int(((res<=.006)&~pending).sum()),
    'rmseMm':row.get('rmseM',0)*1000,'lengthM':float(np.linalg.norm(np.diff(c,axis=0),axis=1).sum()),
    'first':c[0].tolist(),'last':c[-1].tolist()}
   rows.append(rec)
  print(kind,'done',round(time.time()-t,2),flush=True)
 result={'run':RUN.name,'rows':rows,'elapsedS':time.time()-t}
 (OUT/'audit.json').write_text(json.dumps(result,indent=2))
 print('BODY PENDING IN SHELL',sum(x['pendingInsideShell'] for x in rows if x['type']=='body'))
 for typ in ['body','curve']:
  print(typ,'top pending');print(json.dumps(sorted([x for x in rows if x['type']==typ],key=lambda x:x['pendingInsideShell'],reverse=True)[:12],indent=2))
 # A red-capable observation check: pending points still lie on displayed tubes.
 assert not any(x['pendingInsideShellSideNormal']>=24 for x in rows), 'REPRODUCED: displayed tube has >=24 geometrically and normally compatible unassigned points'
if __name__=='__main__':main()
