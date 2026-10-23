"""BIM-guided non-rebar surface evidence, before structural tube fitting.

Templates may move rigidly; only existing source rows close to supported mesh
surfaces are labelled. Rebar/fixture boundary conflicts remain available for
structural fitting. No point coordinates, alignment, or IFC geometry are edited.
"""
from __future__ import annotations
import copy
import math
import time
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

VERSION = 'bim-scene-surface-v2'
CLASS_IDS = {'fixture': 2, 'embedded': 5, 'pipe': 6}


def _surface_samples(vertices, faces, count=800):
    tri = np.asarray(vertices)[np.asarray(faces)]
    normal = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    area = np.linalg.norm(normal, axis=1)
    good = area > 1e-14
    tri, normal, area = tri[good], normal[good], area[good]
    if not len(tri):
        return np.empty((0, 3)), np.empty((0, 3))
    normal /= area[:, None]
    rng = np.random.default_rng(1701)
    ids = np.searchsorted(np.cumsum(area)/area.sum(), (np.arange(count)+.5)/count)
    uv = rng.random((count, 2)); u = np.sqrt(uv[:, :1]); v = uv[:, 1:]
    points = (1-u)*tri[ids, 0]+u*(1-v)*tri[ids, 1]+u*v*tri[ids, 2]
    return points, normal[ids]


def _rigid_fit(samples, sample_normals, tree, points, normals, initial=None, max_shift=.08):
    """Trimmed point-to-plane local pose, with bounded regularized null space."""
    transform = np.eye(4) if initial is None else initial.copy()
    pivot = samples.mean(axis=0)
    for gate in (.035, .018, .009, .005):
        for _ in range(5):
            moved = samples @ transform[:3, :3].T + transform[:3, 3]
            distance, ids = tree.query(moved, workers=1)
            rotated = sample_normals @ transform[:3, :3].T
            normal = normals[ids]
            ok = (distance < gate) & (np.abs(np.einsum('ij,ij->i', normal, rotated)) > .65)
            if ok.sum() < 24: break
            residual = np.einsum('ij,ij->i', moved[ok]-points[ids[ok]], normal[ok])
            keep = np.abs(residual) <= max(.0015, np.quantile(np.abs(residual), .8))
            p, n, residual = moved[ok][keep], normal[ok][keep], residual[keep]
            center = p.mean(axis=0)
            a = np.c_[np.cross(p-center, n), n]
            # Null directions on one observed plane must retain their template pose.
            penalty = np.diag([.015]*3+[.02]*3)
            step = np.linalg.lstsq(np.vstack((a, penalty)), np.r_[-residual, np.zeros(6)], rcond=None)[0]
            angle = np.linalg.norm(step[:3])
            if angle > .06: step[:3] *= .06/angle
            step[3:] = np.clip(step[3:], -.012, .012)
            rot = Rotation.from_rotvec(step[:3]).as_matrix()
            delta = np.eye(4);delta[:3, :3] = rot;delta[:3, 3] = center-rot@center+step[3:]
            trial = delta@transform
            if (np.linalg.norm(trial[:3,:3]@pivot+trial[:3,3]-pivot) > max_shift
                    or np.linalg.norm(Rotation.from_matrix(trial[:3,:3]).as_rotvec()) > math.radians(18)):
                break
            transform = trial
            if np.linalg.norm(step) < 1e-5: break
    return transform


def _rebar_guard(points, normals, inventory, tree):
    """Design tubes protect plausible real steel near contacts, never label it."""
    protected = np.zeros(len(points), bool)
    for bar in (inventory or {}).get('bars', []):
        if not bar.get('unitIds') or not bar.get('radiusM'): continue
        path = np.asarray(bar.get('points', []), float)
        radius = float(bar['radiusM'])
        for a,b in zip(path[:-1], path[1:]):
            delta=b-a;length=np.linalg.norm(delta)
            if length < 1e-8: continue
            tangent=delta/length
            centers=a+np.linspace(0,1,max(2,int(np.ceil(length/.012))+1))[:,None]*delta
            groups=tree.query_ball_point(centers,radius+.014,workers=1)
            ids=np.unique(np.concatenate([g for g in groups if len(g)])) if any(len(g) for g in groups) else np.empty(0,int)
            if not len(ids):continue
            d=points[ids]-a; t=d@tangent
            radial=np.linalg.norm(d-np.clip(t,0,length)[:,None]*tangent,axis=1)
            plausible=(np.abs(radial-radius)<.003) & (np.abs(normals[ids]@tangent)<.6)
            protected[ids[plausible]]=True
    return protected


class _CompatibleSurfaceIndex:
    """Exact finite-face alternatives with spatial and normal bounds.

    Every surviving leaf tests all its finite triangles: a wrong normal can
    never hide a second face. Spatial AABBs bound distance, and normal bounds
    use dot products and Euclidean normal radii, including both normal signs.
    Partition axes affect work only, not eligibility in a rotated scan frame.
    """

    def __init__(self, meshes):
        triangles=[]; owners=[]
        for vertices, faces, owner in meshes:
            tri=np.asarray(vertices, float)[np.asarray(faces)]
            triangles.append(tri); owners.append(np.full(len(tri),owner,np.uint32))
        self.triangles=np.concatenate(triangles).astype(float)
        self.owners=np.concatenate(owners)
        cross=np.cross(self.triangles[:,1]-self.triangles[:,0],self.triangles[:,2]-self.triangles[:,0])
        length=np.linalg.norm(cross,axis=1)
        self.normals=cross/np.maximum(length,1e-30)[:,None]
        # An unoriented normal is canonicalized only for partition efficiency.
        partition=self.normals.copy()
        major=np.argmax(np.abs(partition),axis=1)
        partition*=np.where(partition[np.arange(len(partition)),major]<0,-1.,1.)[:,None]
        self.partition=partition
        self.centers=self.triangles.mean(axis=1)
        self.root=self._node(np.flatnonzero(length>1e-14))
        self.query_count=0

    def _node(self, ids):
        n=self.partition[ids]
        center=n.mean(0) if len(n) else np.zeros(3)
        radius=float(np.linalg.norm(n-center,axis=1).max()) if len(n) else 0.
        bounds=(self.triangles[ids].min(axis=(0,1)),self.triangles[ids].max(axis=(0,1))) if len(ids) else (np.zeros(3),np.zeros(3))
        return dict(ids=ids,center=center,radius=radius,bounds=bounds,children=None)

    def _split(self,node):
        ids=node['ids'];center=self.centers[ids]
        axis=int(np.argmax(np.ptp(center,axis=0)))
        order=np.argsort(center[:,axis],kind='stable');middle=len(ids)//2
        node['children']=(self._node(ids[order[:middle]]),self._node(ids[order[middle:]]))
        return node['children']

    def _leaf(self, ids, points, normals):
        """Small leaves evaluate every finite triangle, never an infinite plane."""
        tri=self.triangles[ids];a=tri[:,0];ab=tri[:,1]-a;ac=tri[:,2]-a
        face=self.normals[ids]
        distance=np.full((len(points),len(ids)),np.inf)
        compatible=np.abs(normals@face.T)>.75
        # Bound temporary point/triangle arrays independently of source size.
        for first in range(0,len(points),512):
            p=points[first:first+512,None,:];d=p-a
            height=np.einsum('ptj,tj->pt',d,face)
            projected=d-height[:,:,None]*face
            aa=np.einsum('tj,tj->t',ab,ab);cc=np.einsum('tj,tj->t',ac,ac);bc=np.einsum('tj,tj->t',ab,ac)
            u=np.einsum('ptj,tj->pt',projected,ab);v=np.einsum('ptj,tj->pt',projected,ac)
            det=np.maximum(aa*cc-bc*bc,1e-30)
            beta=(u*cc-v*bc)/det;gamma=(v*aa-u*bc)/det
            best=np.where((beta>=0)&(gamma>=0)&(beta+gamma<=1),height*height,np.inf)
            for start,end in ((tri[:,0],tri[:,1]),(tri[:,1],tri[:,2]),(tri[:,2],tri[:,0])):
                edge=end-start;relative=p-start
                t=np.clip(np.einsum('ptj,tj->pt',relative,edge)/np.maximum(np.einsum('tj,tj->t',edge,edge),1e-30),0,1)
                delta=relative-t[:,:,None]*edge
                best=np.minimum(best,np.einsum('ptj,ptj->pt',delta,delta))
            distance[first:first+len(p)]=np.sqrt(best)
        distance[~compatible]=np.inf
        local=np.argmin(distance,axis=1);best=distance[np.arange(len(points)),local]
        return best,ids[local]

    def query(self, points, normals, tolerance=.003, *, return_triangle_ids=False):
        points=np.asarray(points,float);normals=np.asarray(normals,float)
        best=np.full(len(points),tolerance);chosen=np.full(len(points),-1,np.int64)
        norm_length=np.linalg.norm(normals,axis=1)
        stack=[(self.root,np.arange(len(points)))]
        while stack:
            node,rows=stack.pop()
            if not len(rows) or not len(node['ids']):continue
            low,high=node['bounds']
            delta=np.maximum(np.maximum(low-points[rows],points[rows]-high),0)
            near=np.einsum('ij,ij->i',delta,delta)<(best[rows]+1e-12)**2
            possible=np.abs(normals[rows]@node['center'])+norm_length[rows]*node['radius']>.75
            rows=rows[near&possible]
            if not len(rows):continue
            ids=node['ids']
            if len(ids)>32:
                children=node['children'] if node['children'] is not None else self._split(node)
                stack.extend((child,rows) for child in reversed(children))
                continue
            # Stable triangle order breaks equal-distance ties independently of
            # BVH partition direction and query/source chunk boundaries.
            distance,candidate=self._leaf(np.sort(ids),points[rows],normals[rows])
            self.query_count+=len(rows)*len(ids)
            valid=(distance<best[rows]-1e-12)|((np.abs(distance-best[rows])<=1e-12)&(chosen[rows]>=0)&(candidate<chosen[rows]))
            best[rows[valid]]=distance[valid];chosen[rows[valid]]=candidate[valid]
        owners=np.zeros(len(points),np.uint32)
        found=chosen>=0;owners[found]=self.owners[chosen[found]]
        return (best,owners,chosen) if return_triangle_ids else (best,owners)


def classify_scene_surfaces(points, normals, table_mask, scene, inventory, *, progress=None, allow_fallback=True):
    import open3d as o3d
    started=time.perf_counter()
    points=np.asarray(points,float);normals=np.asarray(normals,float)
    if normals.shape != points.shape or np.shape(table_mask)!=(len(points),):
        raise ValueError('scene arrays must be source aligned')
    eligible=np.flatnonzero(~np.asarray(table_mask,bool) & np.isfinite(points).all(axis=1))
    tree=cKDTree(points[eligible])
    # All matching uses original source points. A separate full-index tree serves guards.
    full_tree=tree if len(eligible)==len(points) else cKDTree(points)
    protected=_rebar_guard(points,normals,inventory,full_tree)
    objects=[o for o in scene['objects'] if o['category'] in CLASS_IDS]
    samples=[_surface_samples(o['vertices'],o['faces'],800 if o['category']=='fixture' else 1400) for o in objects]
    fixture_samples=[s for o,s in zip(objects,samples) if o['category']=='fixture']
    global_transform=np.eye(4)
    if fixture_samples:
        q=np.concatenate([s[0][::4] for s in fixture_samples]);n=np.concatenate([s[1][::4] for s in fixture_samples])
        global_transform=_rigid_fit(q,n,tree,points[eligible],normals[eligible],max_shift=.08)
    ray=o3d.t.geometry.RaycastingScene(nthreads=2)
    rows=[];geometry_map={};admitted_meshes=[]
    for index,(obj,(q,n)) in enumerate(zip(objects,samples)):
        if progress and index%30==0:progress('夹具与预埋件：局部表面验证',index,len(objects))
        row={k:obj[k] for k in ('globalId','name','ifcType','category')}
        row.update(id=index+1,status='insufficient-scan-evidence',pointCount=0)
        if len(q)<32:row['status']='model-parse-omission';rows.append(row);continue
        initial=global_transform if obj['category']=='fixture' else np.eye(4)
        # Alternate deterministic samples are held out of local fitting.
        transform=_rigid_fit(q[::2],n[::2],tree,points[eligible],normals[eligible],initial,max_shift=.08)
        held=q[1::2]@transform[:3,:3].T+transform[:3,3]
        distance,ids=tree.query(held,workers=1)
        align=np.abs(np.einsum('ij,ij->i',n[1::2]@transform[:3,:3].T,normals[eligible[ids]]))
        support=(distance<.0045)&(align>.7)
        fraction=float(np.mean(support))
        pivot=q.mean(axis=0)
        row.update(transform=transform.tolist(),heldOutSurfaceSupport=fraction,
                   displacementM=float(np.linalg.norm(transform[:3,:3]@pivot+transform[:3,3]-pivot)),
                   rotationDeg=float(np.rad2deg(np.linalg.norm(Rotation.from_matrix(transform[:3,:3]).as_rotvec()))))
        # Only the supported surface will receive rows; missing back faces are never filled.
        if support.sum()>=18 and fraction>=.06:
            v=np.asarray(obj['vertices'])@transform[:3,:3].T+transform[:3,3]
            mesh=o3d.t.geometry.TriangleMesh(o3d.core.Tensor(v.astype(np.float32)),o3d.core.Tensor(np.asarray(obj['faces'],np.uint32)))
            gid=int(ray.add_triangles(mesh));geometry_map[gid]=index
            admitted_meshes.append((v,np.asarray(obj['faces']),index+1))
            row['status']='supported-surface-candidate'
        rows.append(row)
    labels=np.zeros(len(points),np.uint8);owners=np.zeros(len(points),np.uint32)
    conflicts=0;alternatives=None;recovered=0
    if geometry_map:
        for first in range(0,len(eligible),200000):
            ids=eligible[first:first+200000]
            query=ray.compute_closest_points(o3d.core.Tensor(points[ids].astype(np.float32)),nthreads=2)
            closest=query['points'].numpy();face_normals=query['primitive_normals'].numpy();gids=query['geometry_ids'].numpy()
            distance=np.linalg.norm(points[ids]-closest,axis=1)
            alignment=np.abs(np.einsum('ij,ij->i',normals[ids],face_normals))
            supported=(distance<.003)&(alignment>.75)
            conflict=supported&protected[ids];conflicts+=int(conflict.sum())
            supported &= ~protected[ids]
            for gid in np.unique(gids[supported]):
                index=geometry_map[int(gid)];chosen=ids[supported&(gids==gid)]
                labels[chosen]=CLASS_IDS[rows[index]['category']];owners[chosen]=index+1
            retry=(distance<.003)&(alignment<=.75)&~protected[ids]
            if allow_fallback and np.any(retry):
                if alternatives is None:alternatives=_CompatibleSurfaceIndex(admitted_meshes)
                retry_ids=ids[retry]
                _,candidate_owners=alternatives.query(points[retry_ids],normals[retry_ids])
                for owner in np.unique(candidate_owners[candidate_owners>0]):
                    chosen=retry_ids[candidate_owners==owner]
                    labels[chosen]=CLASS_IDS[rows[int(owner)-1]['category']];owners[chosen]=owner
                    recovered+=len(chosen)
        counts=np.bincount(owners,minlength=len(rows)+1)
        for row in rows:
            count=int(counts[row['id']]);row['pointCount']=count
            if count>=32:row['status']='observed-partial-surface'
            elif row['status']=='supported-surface-candidate':
                # A template's proximity score alone is not a detected object.
                labels[owners==row['id']]=0;owners[owners==row['id']]=0
                row.update(status='insufficient-unique-scan-evidence',pointCount=0)
    report={'schema':'bim-guided-scene-v1','version':VERSION,'coordinateFrame':'scan','objects':rows,
            'parseDiagnostics':scene['diagnostics'],'fixtureAssemblyTransform':global_transform.tolist(),
            'counts':{name:int(np.count_nonzero(labels==value)) for name,value in CLASS_IDS.items()},
            'boundaryConflictPoints':conflicts,'compatibleAlternativePoints':recovered,
            'elapsedSeconds':time.perf_counter()-started,
            'policy':{'sequence':'fixture assembly pose, local component pose, held-out surface evidence, structural fitting',
                      'maxRigidDisplacementM':.08,'maxRotationDeg':18,'surfaceToleranceM':.003,
                      'surfaceNormalAbsCosMin':.75,'dimensionDifferencePolicy':'surface tolerance only; no global scale or source deformation',
                      'candidatePolicy':'retain passing closest owner; retry wrong-normal contacts against compatible admitted finite faces',
                      'compatibleAlternativeRecoveryEnabled':bool(allow_fallback),
                      'conflictPolicy':'near structural design tube with compatible normals remains unclaimed',
                      'identityPolicy':'overlapping design objects retain GUIDs; point ownership does not establish distinct physical occurrence',
                      'unobservedPolicy':'unknown; no template points added'}}
    return report,labels,owners


def recover_scene_alternatives(points, normals, table_mask, scene, inventory,
                               report, labels, owners, *, excluded_mask=None):
    """Defer alternative faces until structural evidence has claimed its rows.

    Reuses admitted fitted triangles and GUIDs from ``report``; never refits or
    admits another object. Existing labels/owners and explicit exclusions win.
    Inputs are copied so a caller can inspect/retain its preceding generation.
    """
    import open3d as o3d
    started=time.perf_counter()
    points=np.asarray(points,float);normals=np.asarray(normals,float)
    labels=np.asarray(labels,np.uint8).copy();owners=np.asarray(owners,np.uint32).copy()
    preexisting=owners>0
    table_mask=np.asarray(table_mask,bool)
    excluded=np.zeros(len(points),bool) if excluded_mask is None else np.asarray(excluded_mask,bool)
    if (normals.shape!=points.shape or table_mask.shape!=(len(points),)
            or labels.shape!=(len(points),) or owners.shape!=(len(points),) or excluded.shape!=(len(points),)):
        raise ValueError('scene recovery arrays must be source aligned')
    report=copy.deepcopy(report);rows=report['objects'];by_guid={r['globalId']:r for r in rows}
    meshes=[];ray=o3d.t.geometry.RaycastingScene(nthreads=2)
    for obj in scene['objects']:
        row=by_guid.get(obj['globalId'])
        if (row is None or row['category'] not in CLASS_IDS or 'transform' not in row
                or row.get('heldOutSurfaceSupport',0)<.06):continue
        transform=np.asarray(row['transform'])
        vertices=np.asarray(obj['vertices'])@transform[:3,:3].T+transform[:3,3]
        faces=np.asarray(obj['faces'])
        mesh=o3d.t.geometry.TriangleMesh(o3d.core.Tensor(vertices.astype(np.float32)),o3d.core.Tensor(faces.astype(np.uint32)))
        ray.add_triangles(mesh);meshes.append((vertices,faces,row['id']))
    eligible=np.flatnonzero((labels==0)&(owners==0)&~table_mask&~excluded&np.isfinite(points).all(axis=1))
    recovered=0;index=None
    if meshes and len(eligible):
        protected=_rebar_guard(points,normals,inventory,cKDTree(points))
        for first in range(0,len(eligible),200000):
            ids=eligible[first:first+200000]
            out=ray.compute_closest_points(o3d.core.Tensor(points[ids].astype(np.float32)),nthreads=2)
            distance=np.linalg.norm(points[ids]-out['points'].numpy(),axis=1)
            alignment=np.abs(np.einsum('ij,ij->i',normals[ids],out['primitive_normals'].numpy()))
            retry=ids[(distance<.003)&(alignment<=.75)&~protected[ids]]
            if not len(retry):continue
            if index is None:index=_CompatibleSurfaceIndex(meshes)
            _,candidate=index.query(points[retry],normals[retry])
            for owner in np.unique(candidate[candidate>0]):
                chosen=retry[candidate==owner]
                owners[chosen]=owner;labels[chosen]=CLASS_IDS[rows[int(owner)-1]['category']]
        counts=np.bincount(owners,minlength=len(rows)+1)
        for row in rows:
            count=int(counts[row['id']])
            if count>=32:
                row.update(pointCount=count,status='observed-partial-surface')
            elif count:
                # Only newly assigned rows are removable here; earlier owners
                # have precedence even if a caller supplies a partial subset.
                chosen=(owners==row['id'])&~preexisting
                owners[chosen]=0;labels[chosen]=0
                row.update(pointCount=int(np.count_nonzero(owners==row['id'])))
        recovered=int(np.count_nonzero(owners[eligible]))
    report['version']=VERSION
    report['counts']={name:int(np.count_nonzero(labels==value)) for name,value in CLASS_IDS.items()}
    report['compatibleAlternativePoints']=report.get('compatibleAlternativePoints',0)+recovered
    report['compatibleAlternativeRecoverySeconds']=time.perf_counter()-started
    report['policy']['compatibleAlternativeRecoveryEnabled']=True
    report['policy']['compatibleAlternativeRecoverySequence']='after existing labels, explicit structural exclusions, and design-tube guard'
    return report,labels,owners
