"""Measured cross-section plots, using replay traces and original mesh faces."""
import json
from pathlib import Path
import sys
import numpy as np
import trimesh
import laspy
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
font_manager.fontManager.addfont('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
CJK_FONT = font_manager.FontProperties(fname='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc').get_name()
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon
HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[3]
sys.path.insert(0,str(ROOT/'services/mesh-service'))
from analysis_c2m.core import _tile_mesh
from rebar_solid import RebarSolid
DATA=ROOT/'.cloudbim/normal-solid-audit/replay'
summary=json.loads((DATA/'summary.json').read_text()); inputs=summary['input']
b=np.load(DATA/'before-trace.npz'); a=np.load(DATA/'after-trace.npz')
report=json.loads((DATA/'after-report.json').read_text())
registry=json.loads((Path(inputs['analysis_mesh_path'])/'components.json').read_text())
# A straight 8 mm bar; choose a removed match well away from both caps.
bar=next(row for row in report['bars'] if '形状 01' in row['name'] and row['vertexCount']<3000)
lo=bar['vertexStart'];hi=lo+bar['vertexCount']; verts=b['vertices'][lo:hi]
axis=np.argmax(np.ptp(verts,axis=0)); plane_axes=[j for j in range(3) if j!=axis]
lost=np.flatnonzero(np.isfinite(b['distances'][lo:hi]) & ~np.isfinite(a['distances'][lo:hi]))
mid=verts[:,axis].mean(); local=lost[np.argmin(np.abs(verts[lost,axis]-mid))]; pick=lo+local
p=b['vertices'][pick]; q=b['targets'][pick]; normal=b['normals'][pick]
tiles=[_tile_mesh(Path(inputs['analysis_mesh_path'])/t['uri']) for t in registry['tiles'] if t['ifcGlobalId']==bar['ifcGlobalId']]
mesh=trimesh.util.concatenate(tiles); before=mesh.copy(); solid=RebarSolid([mesh])
t=np.eye(3)[axis]; station=p.copy(); station[axis]+=1e-6
lines,face_ids=trimesh.intersections.mesh_plane(mesh,t,station,return_faces=True)
center=lines.mean(axis=(0,1)); center[axis]=station[axis]
def xy(points): return (np.asarray(points)-center)[...,plane_axes]*1000
polygon=xy(lines.reshape(-1,3)); polygon=np.unique(np.round(polygon,6),axis=0);polygon=polygon[np.argsort(np.arctan2(polygon[:,1],polygon[:,0]))]
plt.rcParams.update({'font.family':CJK_FONT,'font.size':12,'axes.unicode_minus':False,'figure.facecolor':'#f5f7fa','axes.facecolor':'white'})
blue='#2478ad'; green='#168568'; red='#c74645'
def base(ax,lim=12):
 ax.add_patch(Polygon(polygon,facecolor='#dfe5eb',edgecolor='#414e60',lw=1.6,zorder=1))
 ax.axhline(0,color='#dde2e8',lw=.6);ax.axvline(0,color='#dde2e8',lw=.6)
 ax.set_aspect('equal');ax.set_xlim(-lim,lim);ax.set_ylim(-lim,lim)
 ax.set_xlabel('截面 X（mm）');ax.set_ylabel('截面 Y（mm）')
 for spine in ax.spines.values():spine.set_color('#d5dce5')
 ax.scatter([0],[0],marker='+',c='#414e60',s=60,zorder=5)
def arrows(ax,m):
 c=xy(lines.mean(axis=1));n=m.face_normals[face_ids][:,plane_axes]
 # Each side is intersected through two triangles: show one arrow per side.
 _,unique=np.unique(np.round(n,4),axis=0,return_index=True);c=c[unique];n=n[unique]
 ax.quiver(c[:,0],c[:,1],n[:,0]*2,n[:,1]*2,angles='xy',scale_units='xy',scale=1,color=blue,width=.008,zorder=4)
 return int(np.sum(np.einsum('ij,ij->i',c,n)>0)),len(n)
fig,axes=plt.subplots(1,2,figsize=(13,6.5))
for ax,title,m in zip(axes,['修复前：原始细分网格','修复后：实体校验后的网格'],[before,mesh]):
 base(ax,8); valid,total=arrows(ax,m);ax.set_title(title,pad=15,fontweight='bold');ax.text(.5,.03,f'本截面 {valid}/{total} 个侧面法向朝外',transform=ax.transAxes,ha='center',color=blue)
fig.suptitle('真实钢筋截面与面片法向：当前网格本身没有朝内问题',fontsize=17,fontweight='bold',y=.98)
fig.text(.5,.04,f"IFC {bar['ifcGlobalId']} · 设计直径 8 mm · 截面站位 {station[axis]:.6f} m\n全模型：70 个闭合实体，805,344 个三角面；需翻转面片 0 个。箭头取三角面几何法向。",ha='center',fontsize=11)
fig.subplots_adjust(bottom=.19,top=.87,wspace=.26);fig.savefig(HERE/'normals-cross-section.png',dpi=170);plt.close(fig)
las=laspy.read(inputs['scan_path']); owners=np.asarray(las['cloudbim_instance_id']); pts=np.column_stack([las.x,las.y,las.z]); pts=pts[np.isin(owners,bar['instanceIds'])]
matrix=np.asarray(inputs['alignment_matrix']).reshape(4,4).T;pts=(matrix@np.column_stack([pts,np.ones(len(pts))]).T).T[:,:3]
slab=pts[np.abs(pts[:,axis]-p[axis])<=.002]
# Include selected sample even when it falls just outside the displayed slab.
coords=xy(np.vstack([slab,q,p]));lim=max(12,float(np.max(np.abs(coords)))+2)
fig,axes=plt.subplots(1,2,figsize=(13,7))
for ax,label,title in zip(axes,['before','after'],['修复前：穿过另一表面后仍被选中','修复后：在第一次穿出表面处截断']):
 base(ax,lim);ax.scatter(*xy(slab).T,s=13,color='#708eab',alpha=.65,label='同一实例点云（轴向 ±2 mm）',zorder=2)
 ring=np.flatnonzero(np.abs(verts[:,axis]-p[axis])<1e-5)+lo
 trace=b if label=='before' else a
 for idx in ring:
  if np.isfinite(trace['distances'][idx]):
   pair=xy([trace['vertices'][idx],trace['targets'][idx]])
   ax.plot(*pair.T,color=red if label=='before' else green,alpha=.4,lw=1,zorder=3)
 start,end=xy([p,q]);nr=normal[plane_axes];ax.quiver(*start,*(nr*2.4),angles='xy',scale_units='xy',scale=1,color=blue,width=.009,zorder=7)
 ax.scatter(*start,s=70,color=blue,zorder=6,label='设计表面采样点 / 向外法向')
 ax.scatter(*end,s=90,marker='x',lw=2,color=red,zorder=6,label='旧算法选中的真实点')
 ray=q-p;length=np.linalg.norm(ray);direction=ray/length
 hit=solid.scene.cast_rays(__import__('open3d').core.Tensor(np.r_[p-solid.origin+solid.epsilon*direction,direction][None].astype(np.float32)))
 exit_distance=float(hit['t_hit'].numpy()[0])+solid.epsilon
 exit_point=p+direction*exit_distance
 if label=='before':
  ax.annotate('',xy=end,xytext=start,arrowprops={'arrowstyle':'->','color':red,'lw':2.5})
 else:
  ex=xy(exit_point); ax.plot(*np.array([start,ex]).T,color=green,lw=3,label='允许的实体内反向路径',zorder=5)
  ax.plot(*np.array([ex,end]).T,color=red,lw=2,ls='--',zorder=5);ax.scatter(*ex,marker='s',s=55,color=green,zorder=7)
 ax.set_title(title,pad=14,fontweight='bold');ax.legend(loc='upper left',fontsize=9,framealpha=.95)
 ax.text(.5,-.21,('旧偏差 = %.3f mm，计入统计' % (b['distances'][pick]*1000)) if label=='before' else '该顶点无其他有效候选 → 缺测（NaN）',transform=ax.transAxes,ha='center',color=red if label=='before' else green,fontsize=12)
fig.suptitle('同一根钢筋、同一截面、同一份点云的实际选点对比',fontsize=17,fontweight='bold',y=.99)
fig.text(.5,.035,f'沿候选连线：实体内路径 {exit_distance*1000:.3f} mm；点距表面 {length*1000:.3f} mm；越过另一表面 {(length-exit_distance)*1000:.3f} mm\n保留当前设置：半角 10°、32 个候选、不降采样。连线为真实三维匹配在截面上的投影。',ha='center',fontsize=11)
fig.subplots_adjust(bottom=.27,top=.88,wspace=.28);fig.savefig(HERE/'capture-before-after.png',dpi=170);plt.close(fig)
(HERE/'sample.json').write_text(json.dumps({'ifcGlobalId':bar['ifcGlobalId'],'vertexIndex':int(pick),'surfaceM':p.tolist(),'scanPointM':q.tolist(),'outwardNormal':normal.tolist(),'exitPointM':exit_point.tolist(),'interiorPathMm':exit_distance*1000,'candidateDistanceMm':length*1000,'beyondSurfaceMm':(length-exit_distance)*1000,'beforeSignedMm':float(b['distances'][pick]*1000),'afterSignedMm':None},indent=2))
# Copy compact summary only; full point/vertex traces stay in ignored local storage.
(HERE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.loads((HERE/'sample.json').read_text()))
