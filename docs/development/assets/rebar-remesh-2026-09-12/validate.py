import sys,time,json
from pathlib import Path
import numpy as np,trimesh,open3d as o3d
sys.path.insert(0,'services/mesh-service')
from algorithms.rebar_sweep import remesh_rebar,recover_rings
from analysis_mesh.loader import load_component_stream
from analysis_mesh import registry
from analysis_mesh.artifact import build_artifact
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

root=Path('docs/development/assets/rebar-remesh-2026-09-12');root.mkdir(parents=True,exist_ok=True)
model=Path('backend/data/assets/88d5ff2f0b10c8507273a309/model.glb')
scene=trimesh.load(model,force='scene',process=False)
rows=[];outputs={};started=time.perf_counter()
for name,mesh in scene.geometry.items():
 output,report=remesh_rebar(mesh,{})
 outputs[name]=output;rows.append(dict(geometry=name,sourceVertices=len(mesh.vertices),sourceFaces=len(mesh.faces),outputVertices=len(output.vertices),outputFaces=len(output.faces),**report))
geometry_s=time.perf_counter()-started

def distances(source,target):
 p=np.vstack([source.vertices,source.triangles_center])
 if len(p)>10000:p=p[np.linspace(0,len(p)-1,10000,dtype=int)]
 ray=o3d.t.geometry.RaycastingScene()
 ray.add_triangles(o3d.core.Tensor(np.asarray(target.vertices,dtype=np.float32)),o3d.core.Tensor(np.asarray(target.faces,dtype=np.uint32)))
 nearest=ray.compute_closest_points(o3d.core.Tensor(np.asarray(p,dtype=np.float32)))['primitive_ids'].numpy().astype(int)
 adjacency=np.tile(np.arange(len(target.faces))[:,None],(1,4))
 slots=np.ones(len(target.faces),int)
 for a,b in target.face_adjacency:
  if slots[a]<4:adjacency[a,slots[a]]=b;slots[a]+=1
  if slots[b]<4:adjacency[b,slots[b]]=a;slots[b]+=1
 candidates=adjacency[nearest]
 query=np.repeat(p,4,axis=0)
 closest=trimesh.triangles.closest_point(target.triangles[candidates.ravel()],query)
 return np.linalg.norm(closest-query,axis=1).reshape(-1,4).min(1)

for row in rows:
 if row['status']!='rebuilt':continue
 name=row['geometry'];source=scene.geometry[name];target=outputs[name]
 d=np.r_[distances(source,target),distances(target,source)]
 row['sampledBidirectionalSurfaceDistanceUpperBoundM']={'max':float(d.max()),'p95':float(np.quantile(d,.95)),'sampleCount':len(d)}
 row['watertight']=bool(target.is_watertight);row['consistentWinding']=bool(target.is_winding_consistent)

# Show hook end at millimetre scale, using actual original and rebuilt triangles.
g=scene.geometry['210'];out=outputs['210'];centers,_,_,_=recover_rings(g)
origin=centers[0];center=(centers[:6].mean(0)-origin)*1000
fig,axes=plt.subplots(1,3,figsize=(15,5),layout='constrained')
for ax,mesh,title in zip(axes[:2],[g,out],['Source BIM: hook detail','16 sides + uniform axial rings']):
 v=(mesh.vertices-origin)*1000; e=mesh.edges_unique
 mask=(np.max(v[e,0],axis=1)>-15)&(np.min(v[e,0],axis=1)<90)
 ax.add_collection(LineCollection(v[e[mask]][:,:,[0,2]],colors='#276c86',linewidths=.35,alpha=.7))
 ax.plot((centers[:,0]-origin[0])*1000,(centers[:,2]-origin[2])*1000,color='#eb6745',lw=1.3,label='Recovered axis')
 ax.set_xlim(-12,85);ax.set_ylim(-65,15);ax.set_aspect('equal');ax.set_title(title);ax.set_xlabel('x (mm)');ax.set_ylabel('z (mm)');ax.grid(alpha=.15)
phi=np.linspace(0,2*np.pi,400);axes[2].plot(4*np.cos(phi),4*np.sin(phi),'--',color='#94a3b8',label='Circular section')
for n,color in [(8,'#d99022'),(16,'#187a77')]:
 phi=np.arange(n+1)*2*np.pi/n;axes[2].plot(4*np.cos(phi),4*np.sin(phi),'-o',ms=3,color=color,label=f'{n} sides')
axes[2].set_aspect('equal');axes[2].set_title('8 mm diameter: selectable polygon');axes[2].set_xlabel('mm');axes[2].set_ylabel('mm');axes[2].legend();axes[2].grid(alpha=.15)
fig.savefig(root/'comparison.png',dpi=160);plt.close(fig)

stream=load_component_stream(model,model.parent/'metadata.json');builder=registry.get('rebar-sweep-component-v1');params=builder.descriptor.effective_parameters({})
t=time.perf_counter();builder.build(stream,params,'.cloudbim/rebar-remesh')
dest=Path('.cloudbim/rebar-remesh/artifact')
import shutil
if dest.exists():shutil.rmtree(dest)
manifest=build_artifact(stream,dest,{**builder.descriptor.public(),'effectiveParameters':params},face_cap=10000)
artifact_s=time.perf_counter()-t
report={'model':str(model),'parameters':params,'geometrySeconds':geometry_s,'componentBuildAndArtifactSeconds':artifact_s,
 'rebuiltCount':sum(r['status']=='rebuilt' for r in rows),'preservedCount':sum(r['status']=='preserved' for r in rows),
 'sourceFaces':sum(r['sourceFaces'] for r in rows),'outputFaces':sum(r['outputFaces'] for r in rows),
 'maxSampledSurfaceDistanceUpperBoundM':max(r.get('sampledBidirectionalSurfaceDistanceUpperBoundM',{}).get('max',0) for r in rows),
 'artifactComponents':manifest['componentCount'],'artifactTiles':manifest['tileCount'],'rows':rows}
(root/'validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
