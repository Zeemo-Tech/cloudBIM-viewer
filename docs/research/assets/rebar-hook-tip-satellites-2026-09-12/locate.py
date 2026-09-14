from pathlib import Path
import json,sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
sys.path.insert(0,'services/mesh-service')
from algorithms.rebar_extension import exterior_clusters,ExtensionParameters
R=Path('backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260912T104455-24892901');O=Path(__file__).parent
a={k:np.load(R/(k+'.npy'),mmap_mode='r') for k in ['positions','complete_instance','complete_class','internal_instance','complete_cluster','complete_segment','complete_confidence','refined_zone','fused_steel_score']}
p=a['positions'];ids=a['complete_instance'];palette={v['id']:np.array(v['rgb'])/255 for v in json.loads((O/'palette.json').read_text())}
rows=np.flatnonzero((a['complete_class']==3)&(p[:,0]>5.68)&(p[:,1]>-1.80)&(p[:,1]<-1.27)&(p[:,2]<.115))
fig,ax=plt.subplots(figsize=(11,8),facecolor='#0b1020');ax.set_facecolor('#0b1020')
for owner in np.unique(ids[rows]):
 local=rows[ids[rows]==owner];ax.scatter(-p[local,0],-p[local,1],s=.65,color=palette[int(owner)],linewidths=0)
ax.set_aspect('equal');ax.tick_params(colors='white');ax.set_xlabel('-X (m)',color='white');ax.set_ylabel('-Y (m)',color='white')
for owner in [26,52]:
 local=np.flatnonzero(ids==owner);tip=local[p[local,0]>6.075]
 print('TIP',owner,len(tip), 'bounds',p[tip].min(0).tolist(),p[tip].max(0).tolist())
 for name in ['internal_instance','complete_cluster','complete_segment','complete_confidence','refined_zone','fused_steel_score']:
  v,c=np.unique(a[name][tip],return_counts=True);print(name,list(zip(v.tolist(),c.tolist())))
 labels=exterior_clusters(p[local],ExtensionParameters());components=[]
 for label in np.unique(labels[np.isin(local,tip)]):
  q=local[labels==label];other=local[labels!=label];distance=float(cKDTree(p[other]).query(p[q])[0].min()) if len(other) else None
  row=dict(instanceId=owner,cluster=int(label),count=len(q),sourceRows=q.tolist(),min=p[q].min(0).tolist(),max=p[q].max(0).tolist(),gapM=distance,originalOwners=np.unique(a['internal_instance'][q],return_counts=True)[0].tolist())
  components.append(row);print('COMPONENT',json.dumps({k:v for k,v in row.items() if k!='sourceRows'}))
 (O/f'instance-{owner}-components.json').write_text(json.dumps(components,indent=2))
 center=p[tip].mean(0);ax.annotate(f'#{owner}',xy=(-center[0],-center[1]),xytext=(-center[0]-.11,-center[1]-.025),color='white',fontsize=14,arrowprops=dict(arrowstyle='->',color='white'))
ax.set_xlim(-6.42,-5.68);ax.set_ylim(1.26,1.74);fig.tight_layout();fig.savefig(O/'local-top.png',dpi=160,facecolor=fig.get_facecolor())
