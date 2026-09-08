"""Radius-constrained local cylinders and continuous physical member tracks.

The model graph is small (hundreds of local fits), independent of source size.
Source geometry remains immutable; length families never manufacture points.
"""
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy import optimize, signal, ndimage
from scipy.spatial import cKDTree


def diameter_priors(models, supplied=None):
    result={}; diagnostics={}
    available=np.asarray((supplied or {}).get('diametersM',[]),float)
    available=available[np.isfinite(available)&(available>0)]
    for kind in (1,2,3):
        members=[m for m in models if m['type']==kind]
        if not members:
            continue
        diam=np.array([m['radius']*2 for m in members])
        weights=np.array([max(m['high']-m['low'],.001)/(1+m['fitMedianErrorM']/.0005) for m in members])
        # A half-mm mode is robust to many noisy tiny fragments and radius caps.
        bins=np.rint(diam/.0005).astype(int);mass=np.bincount(bins,weights=weights)
        peak=int(np.argmax(ndimage.gaussian_filter1d(mass, .7)))
        near=np.abs(diam-peak*.0005)<.001
        value=float(np.average(diam[near],weights=weights[near]))
        nominal=float(available[np.argmin(np.abs(available-value))]) if len(available) else round(value/.0005)*.0005
        # IFC is a prior only when geometrically compatible with the scan.
        if abs(nominal-value)>max(.001,value*.15):nominal=round(value/.0005)*.0005
        # Retain supported minority diameters within the same layer/type. A
        # layer is not a diameter family, and radius-clipped fits are not modes.
        reliable=np.array([not m.get('radiusAtBound',False) and m['fitMedianErrorM']<max(.0003,d*.04)
                           for m,d in zip(members,diam)])
        trusted=np.bincount(bins,weights=weights*reliable,minlength=len(mass))
        smooth=ndimage.gaussian_filter1d(np.pad(trusted,(2,2)),.7)
        peaks,_=signal.find_peaks(smooth,distance=3,prominence=max(float(smooth.max())*.1,1.e-9))
        modes=[nominal]
        for p in peaks-2:
            close=reliable&(np.abs(diam-p*.0005)<.0008)
            if not close.any() or weights[close].sum()<weights[reliable].sum()*.10:continue
            value_p=float(np.average(diam[close],weights=weights[close]));snapped=round(value_p/.0005)*.0005
            if len(available):
                candidate=float(available[np.argmin(np.abs(available-value_p))])
                if abs(candidate-value_p)<=max(.001,value_p*.15):snapped=candidate
            if all(abs(snapped-old)>.001 for old in modes):modes.append(snapped)
        result[kind]=sorted(modes)
        diagnostics[str(kind)]={'observedModeM':value,'nominalM':nominal,'source':'ifc' if len(available) and np.any(np.isclose(nominal,available)) else 'pointcloud-mode',
                                'nominalsM':sorted(modes),'rawRangeM':[float(diam.min()),float(diam.max())]}
    return result,diagnostics


def _basis(axis):
    first=np.cross(axis,np.eye(3)[np.argmin(np.abs(axis))]);first/=np.linalg.norm(first)
    return np.array([first,np.cross(axis,first)])


def _fixed_radius(points,model,radius):
    axis=model['axis'];base=points.mean(0);cross=_basis(axis);delta=points-base;q=delta@cross.T
    initial=(model['center']-base)@cross.T
    def residual(v):
        direction=axis+v[2:]@cross;direction/=np.linalg.norm(direction)
        d=delta-v[:2]@cross;t=d@direction
        return np.linalg.norm(d-t[:,None]*direction,axis=1)-radius
    def jacobian(v):
        direction=axis+v[2:]@cross;scale=np.linalg.norm(direction);direction/=scale
        d=delta-v[:2]@cross;t=d@direction;radial=d-t[:,None]*direction
        unit=radial/np.maximum(np.linalg.norm(radial,axis=1)[:,None],1.e-12)
        center_gradient=-unit@cross.T
        return np.column_stack((center_gradient,center_gradient*t[:,None]/scale))
    # Fixing diameter also lets the axis recover from biased PCA seeds near a
    # neighbor. Four bounded variables preserve gentle local bends.
    fit=optimize.least_squares(residual,np.r_[np.clip(initial,-.025,.025),0.,0.],
        jac=jacobian,bounds=([-.03,-.03,-.12,-.12],[.03,.03,.12,.12]),loss='soft_l1',f_scale=.0005,max_nfev=25)
    center=base+fit.x[:2]@cross;direction=axis+fit.x[2:]@cross;direction/=np.linalg.norm(direction)
    result=dict(model);result.update(center=center,axis=direction,radius=radius)
    endpoints=np.array([model['center']+t*axis for t in [model['low'],model['high']]])
    result['low'],result['high']=sorted((endpoints-center)@direction)
    result['fitMedianErrorM']=float(np.median(np.abs(residual(fit.x))))
    return result


def regularize_models(models,priors,params,workers,fit_cylinder,split_points):
    """Refit local sections to supported diameter modes; split broad mixtures."""
    def job(model):
        points=model.get('seedPoints')
        if points is None or model['type'] not in priors:return [dict(model)]
        diameters=np.atleast_1d(priors[model['type']])
        radius=float(diameters[np.argmin(np.abs(diameters-model['radius']*2))])/2
        suspect=abs(model['radius']-radius)>radius*.3 or model['fitMedianErrorM']>.001
        parts=split_points(points,model['type'],params) if suspect else [points]
        result=[]
        for p in parts:
            seed=fit_cylinder(p,model['type'],params) if len(parts)>1 else model
            if seed is None:continue
            seed=dict(seed);seed['group']=model['group']
            if len(parts)>1:
                # Each local part is reconciled geometrically later; do not
                # propagate a parent group that may have joined parallel rods.
                axis=seed['axis'];lo=(model['center']+model['low']*model['axis']-seed['center'])@axis
                hi=(model['center']+model['high']*model['axis']-seed['center'])@axis
                seed['low'],seed['high']=sorted([lo,hi])
            fitted=_fixed_radius(p,seed,radius)
            if fitted['fitMedianErrorM']<=max(.0015,radius*.35):result.append(fitted)
        return result
    with ThreadPoolExecutor(max_workers=max(1,workers)) as pool:
        result=[m for pieces in pool.map(job,models) for m in pieces]
    # Even a preexisting group can contain a diameter/axis mixture. Reconstruct
    # horizontal membership from local centerlines rather than inherited labels.
    for index,m in enumerate(result):m['group']=index
    return result


def reconcile_tracks(models,params):
    if not models:return {'mergedGroups':0,'horizontalTracks':0}
    n=len(models);parent=np.arange(n);members={i:[i] for i in range(n)}
    centers=np.array([m['center'] for m in models]);axis=np.array([m['axis'] for m in models]);r=np.array([m['radius'] for m in models])
    low=np.array([m['low'] for m in models]);high=np.array([m['high'] for m in models]);kind=np.array([m['type'] for m in models])
    ends=np.stack((centers+low[:,None]*axis,centers+high[:,None]*axis),axis=1)
    length=high-low
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    candidates=[]
    # Pair geometry is bounded by local model count, never source point count.
    for i in range(n):
        if kind[i]==3:continue
        alignment=axis@axis[i];delta=centers-centers[i];along=delta@axis[i]
        lo=along+np.minimum(low*alignment,high*alignment);hi=along+np.maximum(low*alignment,high*alignment)
        overlap=np.minimum(high[i],hi)-np.maximum(low[i],lo)
        gap=np.maximum(-overlap,0)
        station=(np.maximum(low[i],lo)+np.minimum(high[i],hi))/2
        on_i=centers[i]+station[:,None]*axis[i]
        on_j=centers+((station-along)/np.where(np.abs(alignment)>.1,alignment,1))[:,None]*axis
        across=np.linalg.norm(on_i-on_j,axis=1)
        direction=int(np.argmax(np.abs(axis[i])))
        lateral=np.linalg.norm(np.delete(delta,direction,axis=1),axis=1)
        # Short PCA fits on ribs can tilt to alternating sides. Nearby section
        # centers supply a second, translation-stable continuity measurement.
        across=np.minimum(across,np.where(np.abs(delta[:,direction])<params.horizontal_fit_span*1.5,lateral,np.inf))
        endpoint_distance=np.linalg.norm(ends[:,:,None,:]-ends[i][None,None,:,:],axis=3).min(axis=(1,2))
        compatible=(np.arange(n)>i)&(kind==kind[i])&(np.abs(alignment)>.965)&(np.abs(r-r[i])<max(.0007,r[i]*.2))
        # In overlap, compare actual axes. Across a gap, use mutual endpoint
        # tangents so small bends are accepted without fusing neighboring rails.
        overlap_ok=(overlap>=0)&(across<min(.005,r[i]*1.2))
        join_ok=(gap>0)&(gap<.040)&(endpoint_distance<gap+.0035)&(across<min(.005,r[i]*1.2))
        for j in np.flatnonzero(compatible&(overlap_ok|join_ok)):
            candidates.append((float(gap[j]+across[j]+(1-abs(alignment[j]))*.01),i,int(j)))
    for _,i,j in sorted(candidates):
        a,b=root(i),root(j)
        if a==b:continue
        left,right=members[a],members[b]
        # A bridge cannot join two distinct tracks that already coexist at the
        # same axial station, even when a third noisy fit lies between them.
        conflict=False
        for u in left:
            v=np.asarray(right);delta=centers[v]-centers[u];t=delta@axis[u];dot=axis[v]@axis[u]
            lo=t+np.minimum(low[v]*dot,high[v]*dot);hi=t+np.maximum(low[v]*dot,high[v]*dot)
            overlap=np.minimum(high[u],hi)-np.maximum(low[u],lo)
            station=(np.maximum(low[u],lo)+np.minimum(high[u],hi))/2
            a_point=centers[u]+station[:,None]*axis[u]
            b_point=centers[v]+((station-t)/dot)[:,None]*axis[v]
            d=np.linalg.norm(a_point-b_point,axis=1)
            direction=int(np.argmax(np.abs(axis[u])))
            lateral=np.linalg.norm(np.delete(delta,direction,axis=1),axis=1)
            d=np.minimum(d,np.where(np.abs(delta[:,direction])<params.horizontal_fit_span*1.5,lateral,np.inf))
            if np.any((overlap>.02)&(d>min(.006,r[u]*1.4))):conflict=True;break
        if conflict:continue
        parent[b]=a;members[a]+=members.pop(b)
    for i,m in enumerate(models):m['group']=int(root(i))
    return {'mergedGroups':int(n-len(members)),'horizontalTracks':sum(models[g]['type']!=3 for g in members)}


def grow_track_ends(models,points,params,workers):
    """Grow only the two outer ends of each horizontal track through support."""
    tree=cKDTree(points);groups={}
    for i,m in enumerate(models):
        if m['type']!=3:groups.setdefault(m['group'],[]).append(i)
    jobs=[]
    for group,indices in groups.items():
        along=int(np.argmax(np.abs(np.mean([models[i]['axis'] for i in indices],axis=0))))
        ends=[(float((models[i]['center']+t*models[i]['axis'])[along]),i,side) for i in indices for side,t in [('low',models[i]['low']),('high',models[i]['high'])]]
        for _,i,side in [min(ends),max(ends)]:jobs.append((i,side))
    def job(job):
        i,side=job;m=models[i];sign=-1 if side=='low' else 1;end=m['center']+m[side]*m['axis'];axis=m['axis']*sign
        maximum=min(.03,max(.015,m['radius']*5));rows=tree.query_ball_point(end,maximum+m['radius'],workers=1)
        q=points[rows]-end;t=q@axis;across=np.linalg.norm(q-t[:,None]*axis,axis=1)
        keep=(t>0)&(t<maximum)&(np.abs(across-m['radius'])<.0015)
        t=t[keep]
        if len(t)<5:return i,side,0.
        bins=np.floor(t/.003).astype(int);mass=np.bincount(bins,minlength=int(np.ceil(maximum/.003)))
        # At most one sparse bin; an observed empty run terminates growth.
        end_bin=0;missing=0
        for k,value in enumerate(mass):
            missing=missing+1 if value<2 else 0
            if missing>=2:break
            if value>=2:end_bin=k+1
        extent=float(t[t<end_bin*.003].max()) if end_bin and np.any(t<end_bin*.003) else 0.
        return i,side,extent
    with ThreadPoolExecutor(max_workers=max(1,workers)) as pool:results=list(pool.map(job,jobs))
    for i,side,extent in results:models[i][side]+=extent*(-1 if side=='low' else 1)
    return {'grownEnds':sum(d>.001 for _,_,d in results),'totalGrowthM':float(sum(d for _,_,d in results))},tree


def remove_explained_fragments(models):
    """Discard an anomalous short fit only when longer tracks explain its data.

    Length is a proposal prior, not proof: an independent short member remains
    whenever its observed surface does not lie on already supported cylinders.
    Multiple nearby members may explain a spurious fit between their surfaces.
    """
    track_statistics(models)
    groups={}
    for m in models:groups.setdefault(m['group'],[]).append(m)
    removed=set()
    for group,parts in sorted(groups.items(),key=lambda item:item[1][0]['trackInfo']['lengthM']):
        info=parts[0]['trackInfo']
        if info['type']==3 or not info['lengthAnomaly']:continue
        seeds=[p['seedPoints'] for p in parts if 'seedPoints' in p]
        if not seeds:continue
        points=np.concatenate(seeds);axis=parts[0]['axis']
        if len(points)>2400:points=points[np.linspace(0,len(points)-1,2400,dtype=int)]
        candidates=[m for m in models if m['group']!=group and m['group'] not in removed
            and m['type']==info['type'] and abs(m['axis']@axis)>.97
            and m['trackInfo']['lengthM']>max(.12,info['lengthM']*1.6)]
        explained=np.zeros(len(points),bool)
        for m in candidates:
            q=points-m['center'];t=q@m['axis']
            within=(t>=m['low']-.004)&(t<=m['high']+.004)
            if not within.any():continue
            radial=np.linalg.norm(q-t[:,None]*m['axis'],axis=1)
            explained|=within&(np.abs(radial-m['radius'])<.0013)
        if explained.mean()>=.80:removed.add(group)
    return [m for m in models if m['group'] not in removed],{'explainedFragmentTracks':len(removed)}


FAMILY_NAMES={'1':'纵向钢筋','2':'横向整长钢筋','3':'短钢筋','4':'腹杆直段'}


def track_statistics(models):
    groups={}
    for model in models:groups.setdefault(model['group'],[]).append(model)
    records={}
    for group,parts in groups.items():
        axis=np.median([p['axis'] for p in parts],axis=0);along=int(np.argmax(np.abs(axis)))
        ends=np.array([m['center']+t*m['axis'] for m in parts for t in [m['low'],m['high']]])
        span=float(np.ptp(ends[:,along])/max(np.median([abs(m['axis'][along]) for m in parts]),.1))
        records[group]={'lengthM':span,'diameterM':float(np.median([2*m['radius'] for m in parts])),
                        'direction':along,'type':parts[0]['type']}
    full={}
    for direction in [0,1]:
        lengths=np.array([r['lengthM'] for r in records.values() if r['type']!=3 and r['direction']==direction])
        if not len(lengths):continue
        bins=np.rint(lengths/.025).astype(int);mass=np.bincount(bins,weights=lengths)
        peak=int(np.argmax(ndimage.gaussian_filter1d(mass,1)))
        near=np.abs(lengths-peak*.025)<max(.05,peak*.025*.08)
        full[direction]=float(np.median(lengths[near])) if near.any() else float(lengths.max())
    for record in records.values():
        record['family']=4 if record['type']==3 else 1 if record['direction']==0 else 3 if record['lengthM']<full.get(1,0)*.6 else 2
    families=[]
    for family in [1,2,3,4]:
        lengths=np.array([r['lengthM'] for r in records.values() if r['family']==family])
        if not len(lengths):continue
        # Weight by observed span, preventing tiny oversegments from inventing
        # the dominant member length of a family.
        bins=np.rint(lengths/.02).astype(int);mass=np.bincount(bins,weights=lengths)
        peak=int(np.argmax(ndimage.gaussian_filter1d(mass,.7)))
        near=np.abs(lengths-peak*.02)<max(.015,peak*.02*.15)
        mode=float(np.median(lengths[near])) if near.any() else float(np.median(lengths))
        families.append({'id':family,'name':FAMILY_NAMES[str(family)],'lengthM':mode,'count':len(lengths),'source':'observed-inner-span'})
        for record in records.values():
            if record['family']==family:record['lengthAnomaly']=abs(record['lengthM']-mode)>max(.03,mode*.25)
    for group,parts in groups.items():
        for model in parts:model['trackInfo']=records[group]
    return {'familyNames':FAMILY_NAMES,'lengthFamilies':families,'anomalousLengthTracks':sum(r['lengthAnomaly'] for r in records.values())}
