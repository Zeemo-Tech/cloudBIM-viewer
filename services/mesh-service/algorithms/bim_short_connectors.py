"""Recover short connector bodies from finite, tilted scan tubes.

A relaxed apparent radius is a measured deviation, not a model edit. Independent
held-out rows, cross-section extent and longitudinal continuity reject tangent
plates and short crops of larger fixture cylinders. All output owns raw rows.
"""
from __future__ import annotations
import math
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

VERSION='short-observed-tubes-v4'


def fit_tube_cluster(points, nominal_radius, direction, center):
    """Fit and validate one unclaimed connected component, in a prior frame."""
    direction=np.asarray(direction,float);direction=direction/np.linalg.norm(direction)
    u=np.cross(direction,np.eye(3)[np.argmin(abs(direction))]);u/=np.linalg.norm(u);v=np.cross(direction,u)
    frame=np.column_stack((u,v,direction));local=(points-center)@frame
    if len(local)<48 or np.ptp(local[:,2])<.032:return None
    # A deterministic spatial split with alternating rows prevents a fit score
    # from doubling as its only acceptance evidence.
    order=np.lexsort((local[:,0],local[:,1],local[:,2]));train=local[order[::2]];held=local[order[1::2]]
    seed=np.r_[np.median(local[:,:2],axis=0),0.,0.,nominal_radius]
    low=np.r_[seed[:2]-.02,-.55,-.55,.65*nominal_radius]
    high=np.r_[seed[:2]+.02,.55,.55,1.65*nominal_radius]
    def residual(x,q):
        axis=np.array([x[2],x[3],1.]);axis/=np.linalg.norm(axis)
        d=q-np.r_[x[:2],0.]
        return np.linalg.norm(d-(d@axis)[:,None]*axis,axis=1)-x[4]
    candidates=[]
    for dx,dy in ((0,0),(nominal_radius,0),(-nominal_radius,0),(0,nominal_radius),(0,-nominal_radius)):
        start=seed.copy();start[:2]+=[dx,dy]
        fit=least_squares(residual,start,args=(train,),bounds=(low,high),loss='soft_l1',f_scale=.0006,max_nfev=80)
        candidates.append(fit)
    best=min(candidates,key=lambda a:a.cost);x=best.x
    if x[4] > .98*high[4] or x[4] < 1.02*low[4]:return None
    errors=abs(residual(x,held));support=errors<.0015
    if support.sum()<24 or support.mean()<.6 or np.median(errors)>.00085:return None
    core=held[support]-held[support].mean(axis=0)
    plane_sigma=np.sqrt(max(0.,np.linalg.eigvalsh(core.T@core/len(core))[0]))
    if plane_sigma < max(.00015,.8*np.median(errors[support])):return None
    axis=np.r_[x[2:4],1.];axis/=np.linalg.norm(axis)
    if np.arccos(axis[2])>math.radians(25):return None
    d=held-np.r_[x[:2],0.];along=d@axis;radial=d-along[:,None]*axis
    dirs=radial[support]/np.maximum(np.linalg.norm(radial[support],axis=1)[:,None],1e-12)
    # A thin tangent strip lacks curved cross-section support.
    transverse=np.linalg.eigvalsh(dirs.T@dirs/len(dirs))
    if transverse[-2]<.06:return None
    bins=np.floor((along[support]-along[support].min())/.006).astype(int)
    counts=np.bincount(bins);occupancy=float(np.mean(counts>=2))
    if occupancy<.65 or len(counts)<6:return None
    full_error=abs(residual(x,local));good=full_error<.0015
    span=float(np.ptp((local-np.r_[x[:2],0.])@axis))
    return {'center':center+frame@np.r_[x[:2],0.],'direction':frame@axis,'radius':float(x[4]),
            'heldOutMedianM':float(np.median(errors)),'heldOutSupportFraction':float(support.mean()),
            'angularEigenvalue':float(transverse[-2]),'axialBinCoverage':occupancy,
            'observedSpanM':span,'inliers':good,'score':float(good.sum()*occupancy),
            'tiltDeg':float(np.rad2deg(np.arccos(axis[2])))}


def _clusters(points):
    import open3d as o3d
    cloud=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    labels=np.asarray(cloud.cluster_dbscan(eps=.004,min_points=8,print_progress=False))
    return [np.flatnonzero(labels==n) for n in np.unique(labels) if n>=0 and np.count_nonzero(labels==n)>=48]


def recover_connectors(points,normals,inventory,rows,status,owner,*,excluded=None,progress=None):
    from algorithms.rebar_control_net import _polyline_distances
    points=np.asarray(points);normals=np.asarray(normals)
    excluded=np.zeros(len(points),bool) if excluded is None else np.asarray(excluded,bool)
    available=(~excluded)&(status!=0)
    ids_all=np.flatnonzero(available);tree=cKDTree(points[ids_all])
    unit_by_id={u['designUnitId']:u for u in inventory['units']}
    row_by_id={r['designUnitId']:r for r in rows}
    recovered=[]
    parents=[b for b in inventory['bars'] if b.get('unitIds') and b.get('radiusM')]
    for bar in parents:
        units=[unit_by_id[k] for k in bar['unitIds'] if k in unit_by_id]
        main=max(units,key=lambda u:u['lengthM'])
        a,b=np.asarray(main['startM']),np.asarray(main['endM']);length=np.linalg.norm(b-a);direction=(b-a)/length
        if length<.05 or length>.25 or abs(direction[2])<.65:continue
        row=row_by_id[main['designUnitId']];number=row['id'];radius=bar['radiusM'];center=(a+b)/2
        ids=ids_all[np.asarray(tree.query_ball_point(center,np.hypot(.065,.5*length)),int)]
        delta=points[ids]-center;along=delta@direction;trans=delta-along[:,None]*direction
        core=(abs(along)<.5*length-.012)&(np.linalg.norm(trans,axis=1)<.06)&((owner[ids]==0)|(owner[ids]==number))
        core&=abs(normals[ids]@direction)<.6
        ids=ids[core]
        audit={'designBarId':bar['designBarId'],'globalId':bar['ifcGlobalId'],'mainUnitId':main['designUnitId'],
               'status':'insufficient-distinct-tube-evidence','corePoints':len(ids),'previousStatus':row['status']}
        if len(ids)<48:recovered.append(audit);continue
        candidates=[]
        for group in _clusters(points[ids]):
            fit=fit_tube_cluster(points[ids[group]],radius,direction,center)
            if fit is None:continue
            if np.linalg.norm(fit['center']-center)>.06 or fit['observedSpanM']<.35*length:continue
            fit['sourceIds']=ids[group][fit['inliers']];candidates.append(fit)
        if not candidates:recovered.append(audit);continue
        candidates.sort(key=lambda f:f['score'],reverse=True)
        best=candidates[0]
        if len(candidates)>1 and candidates[1]['score']>.85*best['score']:
            audit['status']='ambiguous-tube-candidates';recovered.append(audit);continue
        # Every candidate must stay nearer this design slot than any parallel connector.
        for other in parents:
            if other['designBarId']==bar['designBarId']:continue
            ou=[unit_by_id[k] for k in other['unitIds'] if k in unit_by_id]
            if not ou:continue
            om=max(ou,key=lambda u:u['lengthM']);oa,ob=np.asarray(om['startM']),np.asarray(om['endM'])
            if np.linalg.norm(ob-oa)>.25:continue
            if np.linalg.norm((oa+ob)/2-best['center'])<np.linalg.norm(center-best['center'])+.008:
                best=None;break
        if best is None:audit['status']='ambiguous-design-identity';recovered.append(audit);continue
        d=best['direction'];c=best['center'];r=best['radius']
        # Recover raw main-body rows across the design interval; no synthetic fill.
        all_ids=ids_all[np.asarray(tree.query_ball_point(c,np.hypot(.022,.5*length+.004)),int)]
        q=points[all_ids]-c;t=q@d;rad=q-t[:,None]*d
        e=abs(np.linalg.norm(rad,axis=1)-r)
        take=(abs(t)<.5*length)&(e<.0015)&(abs(normals[all_ids]@d)<.65)&((owner[all_ids]==0)|(owner[all_ids]==number))
        assigned=all_ids[take]
        if len(assigned)<48:recovered.append(audit);continue
        owner[assigned]=number;status[assigned]=1
        centerline=np.array([c-.5*length*d,c+.5*length*d])
        row.update(status='fitted',reason='held-out-tilted-connector-surface',centerlineM=centerline.tolist(),
                   rmseM=float(np.sqrt(np.mean(e[take]**2))),observedLengthM=float(np.ptp(t[take])),
                   fittedRadiusM=r,diameterDeviationM=2*(r-radius),pointCount=int(np.count_nonzero(owner==number)),
                   connectorEvidence={k:best[k] for k in ('heldOutMedianM','heldOutSupportFraction','angularEigenvalue','axialBinCoverage','tiltDeg')})
        row.pop('bodyDisplayCenterlineM', None)
        # Parent rigid pose is a search guide for hooks, never a rendered observed line.
        cross=np.cross(direction,d);sine=np.linalg.norm(cross);cos=float(direction@d)
        rot=np.eye(3) if sine<1e-9 else Rotation.from_rotvec(cross/sine*np.arctan2(sine,cos)).as_matrix()
        path=(np.asarray(bar['points'])-center)@rot.T+c
        previous_ids=np.flatnonzero(owner==number)
        previous_error=abs(_polyline_distances(points[previous_ids],path)-r)
        rejected=previous_ids[previous_error>.0015]
        owner[rejected]=0;status[rejected]=2
        local_ids=ids_all[np.asarray(tree.query_ball_point(c,np.max(np.linalg.norm(path-c,axis=1))+.012),int)]
        local_ids=local_ids[owner[local_ids]==0]
        dist=_polyline_distances(points[local_ids],path)
        best_distance=np.full(len(local_ids),np.inf);normal_ok=np.zeros(len(local_ids),bool)
        curve_station=np.zeros(len(local_ids));station=0.
        for pa,pb in zip(path[:-1],path[1:]):
            dd=pb-pa;ll=np.linalg.norm(dd)
            if ll<1e-8:continue
            tangent=dd/ll;offset=points[local_ids]-pa
            tt=np.clip(offset@tangent,0,ll);radial=offset-tt[:,None]*tangent
            rr=np.linalg.norm(radial,axis=1);closer=rr<best_distance
            nn=normals[local_ids]
            alignment=np.abs(np.einsum('ij,ij->i',nn,radial)/np.maximum(rr,1e-10))
            normal_ok[closer]=(alignment>.65)[closer] & (np.abs(nn@tangent)<.6)[closer]
            curve_station[closer]=station+tt[closer];best_distance[closer]=rr[closer]
            station+=ll
        valid=(abs(dist-r)<.0012)&normal_ok
        bins=np.floor(curve_station/.005).astype(int)
        counts=np.bincount(bins[valid],minlength=int(station/.005)+2)
        supported_bins=np.convolve((counts>=3).astype(int),np.ones(3,int),mode='same')>=2
        hook=local_ids[valid & supported_bins[bins]]
        # A sparse handful at a crossed bar is not hook recovery evidence.
        if len(hook)>=24:
            owner[hook]=number;status[hook]=1
            row['pointCount']=int(np.count_nonzero(owner==number))
        else:hook=np.empty(0,int)
        audit.update(status='observed-partial-connector',bodyPointCount=len(assigned),hookCandidatePointCount=len(hook),releasedPreviousPoints=len(rejected),
                     fittedRadiusM=r,designRadiusM=radius,centerlineM=centerline.tolist(),
                     bodyDisplacementM=float(np.linalg.norm(c-center)),
                     **row['connectorEvidence'])
        recovered.append(audit)
        if progress:progress('短连接筋：独立截面与轴向证据',len(recovered),len(parents))
    return {'version':VERSION,'objects':recovered,'policy':{'radiusRatioRange':[.65,1.65],
            'radiusMeaning':'apparent scan radius; design dimensions unchanged','maxTiltDeg':25,
            'maxCenterDisplacementM':.06,'missingPolicy':'insufficient evidence, not physical absence',
            'hookPolicy':'only existing unclaimed source points close to transformed physical centerline; not complete-hook verification'}}
