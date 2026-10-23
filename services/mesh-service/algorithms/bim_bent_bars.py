"""IFC sweep-axis flexible registration for bent bars inside the fixture bay.

BIM establishes identity and a bounded search corridor, never a point source.
An absent or occluded design member does not receive fabricated observations.
"""
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import least_squares
from scipy.interpolate import BSpline
from algorithms.rebar_control_net import _frame, _unit_rows
from rebar_prior_axis import fit_prior_axis

VERSION = 'bim-flexible-axis-v3'


def fixture_envelope(scene, scene_report, units):
    """Find broad opposing body panels in a design-derived orthonormal frame.

    This is an interior search prior, not a watertight collision mesh. Holes and
    discontinuous fixtures remain in the actual surface classifier. Full tilted
    plane normals are retained; axial protrusions are allowed separately.
    """
    long=[u for u in units if u['kind']=='straight' and u['length']>.6]
    if not long:return {'status':'unavailable','planes':[]}
    first=max(long,key=lambda u:u['length'])['direction'].copy()
    first*=1 if first[np.argmax(abs(first))]>=0 else -1
    perpendicular=[u for u in long if abs(u['direction']@first)<.3]
    if not perpendicular:return {'status':'unavailable-one-axis','planes':[]}
    second=max(perpendicular,key=lambda u:u['length'])['direction'].copy()
    second-=first*(second@first);second/=np.linalg.norm(second)
    second*=1 if second[np.argmax(abs(second))]>=0 else -1
    frame=np.column_stack((first,second,np.cross(first,second)))
    ends=np.array([x for u in long for x in (u['start'],u['end'])]);origin=np.median(ends,axis=0)
    local=(ends-origin)@frame;spread=np.ptp(local,axis=0)
    rows={r['globalId']:r for r in scene_report.get('objects',[])};candidates=[]
    for obj in scene.get('objects',[]):
        if obj.get('category')!='fixture':continue
        transform=np.asarray(rows.get(obj['globalId'],{}).get('transform',np.eye(4)))
        vertices=np.asarray(obj['vertices'])@transform[:3,:3].T+transform[:3,3]
        triangles=vertices[np.asarray(obj['faces'])];cross=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        size=np.linalg.norm(cross,axis=1);normals=cross/np.maximum(size[:,None],1e-12)
        centers=triangles.mean(axis=1)
        for axis in (0,1):
            ids=np.flatnonzero((abs(normals@frame[:,axis])>.985)&(size>1e-10))
            if not len(ids):continue
            normal=normals[ids]*np.sign(normals[ids]@frame[:,axis])[:,None]
            normal=np.average(normal,axis=0,weights=size[ids]);normal/=np.linalg.norm(normal)
            offsets=centers[ids]@normal
            for key in np.unique(np.round(offsets/.002).astype(int)):
                selected=ids[np.round(offsets/.002).astype(int)==key]
                area=float(size[selected].sum()/2)
                if area<.02:continue
                face=triangles[selected].reshape(-1,3);bounds=(face-origin)@frame
                lo,hi=bounds.min(0),bounds.max(0);width=hi-lo
                if width[1-axis]<.50*spread[1-axis] or width[2]<max(.04,.55*spread[2]):continue
                if lo[2]>.015 or hi[2]<-.015:continue
                pivot=np.average(centers[selected],axis=0,weights=size[selected]);coordinate=(pivot-origin)@frame[:,axis]
                transverse=[.5*(u['start']+u['end']) for u in long if abs(u['direction']@frame[:,axis])<.3]
                if not transverse:continue
                values=(np.asarray(transverse)-origin)@frame[:,axis];low,high=np.quantile(values,[.10,.90])
                side=-1 if coordinate<low else 1 if coordinate>high else 0
                if side==0:continue
                inward=-side*normal;offset=float(inward@pivot)
                candidates.append({'axis':axis,'side':side,'normal':inward.tolist(),'offsetM':offset,
                    'globalId':obj['globalId'],'areaM2':area,'distanceFromCenterM':abs(float(inward@origin-offset)),
                    'heightBoundsM':[float(lo[2]),float(hi[2])],'localBoundsM':[lo.tolist(),hi.tolist()]})
    planes=[]
    for axis in (0,1):
        for side in (-1,1):
            choices=[p for p in candidates if p['axis']==axis and p['side']==side]
            if choices:planes.append(min(choices,key=lambda p:p['distanceFromCenterM']))
    return {'status':'supported-design-bay' if len(planes)==4 else 'partial-design-bay',
            'frameColumns':frame.tolist(),'originM':origin.tolist(),'planes':planes,
            'policy':'tilted broad panel halfspaces constrain body search only; axial protrusions retain finite design-end bounds; not a watertight collision model'}


def _inside_body(point,direction,envelope):
    planes=envelope.get('planes',[])
    axial=[p for p in planes if abs(np.asarray(p['normal'])@direction)>.75]
    # A real bar may protrude through its end-side fixtures. The trace query
    # separately bounds these positions to nominal ends plus 40 mm.
    if any(np.asarray(p['normal'])@point-p['offsetM']<-.004 for p in axial):return True
    for plane in planes:
        normal=np.asarray(plane['normal'])
        if abs(normal@direction)<.25 and normal@point-plane['offsetM']<-.004:return False
    return True


def _identity_allowed(point,unit,units,envelope):
    if not _inside_body(point,unit['direction'],envelope):return False
    t=np.clip((point-unit['start'])@unit['direction'],0,unit['length'])
    own=np.linalg.norm(point-unit['start']-t*unit['direction'])
    for peer in units:
        if (peer['source']['designBarId']==unit['source']['designBarId']
                or peer['kind']!='straight' or abs(unit['direction']@peer['direction'])<.98):continue
        along=np.clip((point-peer['start'])@peer['direction'],0,peer['length'])
        distance=np.linalg.norm(point-peer['start']-along*peer['direction'])
        if distance<own+.003:return False
    return True

def _sections(q,n,r,prior_distance):
    if len(q)<14:return []
    nl=np.linalg.norm(n,axis=1);valid=nl>.65
    if valid.sum()<12:return []
    nn=n[valid]/nl[valid,None];qq=q[valid]
    vote=np.concatenate([qq-r*nn,qq+r*nn]);tree=cKDTree(vote)
    counts=tree.query_ball_point(vote,.0015,return_length=True)
    order=np.argsort(counts)[::-1];centers=[];found=[];trials=0
    for at in order:
        center=vote[at]
        if counts[at]<8 or trials>=12:break
        if any(np.linalg.norm(center-c)<1.5*r for c in centers):continue
        centers.append(center)
        if not prior_distance(center):continue
        # Neighbour or exterior votes must not exhaust the current member's
        # bounded fitting budget before its own cross-section is considered.
        trials+=1
        near=np.linalg.norm(q-center,axis=1)<2*r
        if near.sum()<14:continue
        xy=q[near];nt=n[near];ntlen=np.linalg.norm(nt,axis=1)
        def fun(x):return np.linalg.norm(xy-x[:2],axis=1)-x[2]
        # Every alternate source row is held out from circle optimization.
        def training(x):return fun(x)[::2]
        fit=least_squares(training,[*center,r],bounds=([center[0]-.04,center[1]-.04,.001],[center[0]+.04,center[1]+.04,.06]),loss='soft_l1',f_scale=.0005,max_nfev=45)
        c=fit.x[:2];rad=fit.x[2]
        if not .72*r<rad<1.45*r or not prior_distance(c):continue
        residual=abs(fun(fit.x));shell=residual<.00125
        if shell.sum()<12 or shell.mean()<.62:continue
        if np.mean(shell[1::2])<.62:continue
        delta=xy-c;norm=np.linalg.norm(delta,axis=1)
        radial=delta/np.maximum(norm[:,None],1e-9)
        angle=abs(np.sum(radial*nt,axis=1))/np.maximum(ntlen,1e-9)
        good=shell&(ntlen>.65)&(angle>.65)
        if good.sum()<12 or np.mean(angle[shell]>.65)<.6:continue
        eigen=np.linalg.eigvalsh(radial[good].T@radial[good]/good.sum())
        if eigen[0]<.055:continue
        if any(np.linalg.norm(c-f['center'])<r for f in found):continue
        found.append(dict(center=c,radius=rad,count=int(shell.sum()),score=min(2.,shell.sum()/22)*min(1.,eigen[0]/.15),rmse=float(np.sqrt(np.mean(residual[shell]**2)))))
    return sorted(found,key=lambda x:x['score'],reverse=True)[:5]

def _trace(points,normals,eligible,unit,units,envelope):
    d=unit['direction'];u,v=_frame(d);r=unit['radius'];length=unit['length'];start=unit['start']
    delta=points-start;t=delta@d;x=delta@u;y=delta@v
    mask=eligible&(t>-.04)&(t<length+.04)&(x*x+y*y<.15**2)
    ids=np.flatnonzero(mask);pos=np.c_[x[ids],y[ids]];tt=t[ids];ns=np.c_[normals[ids]@u,normals[ids]@v]
    peers=[]
    for other in units:
        if other is unit or other['kind']!='straight' or abs(d@other['direction'])<.98:continue
        # The complete design neighbourhood constrains identity even when a
        # neighbour itself has not yet been found in the scan.
        if other['source']['designBarId']==unit['source']['designBarId']:continue
        peers.append(other)
    nodes=[];step=.012
    for i,s in enumerate(np.arange(0,length+.0001,step)):
        use=abs(tt-s)<.008
        def own(c):
            point=start+s*d+c[0]*u+c[1]*v;own_dist=np.linalg.norm(c)
            if not _inside_body(point,d,envelope):return False
            for peer in peers:
                along=np.clip((point-peer['start'])@peer['direction'],0,peer['length'])
                dist=np.linalg.norm(point-peer['start']-along*peer['direction'])
                if dist<own_dist+.003:return False
            return True
        for candidate in _sections(pos[use],ns[use],r,own):
            candidate.update(station=float(s),index=i);nodes.append(candidate)
    if not nodes:return {'status':'no-sections','candidateRows':len(ids),'_candidateIds':ids},np.empty(0,int)
    score=np.zeros(len(nodes));prev=np.full(len(nodes),-1,int)
    for j,node in enumerate(nodes):
        score[j]=node['score']
        for k in range(j-1,-1,-1):
            old=nodes[k];gap=node['station']-old['station']
            if gap>.09:break
            if gap<.5*step:continue
            dist=np.linalg.norm(node['center']-old['center'])
            if dist>min(.025,.6*gap+.002):continue
            value=score[k]+node['score']-.4*(gap/step-1)-.7*dist/gap
            if value>score[j]:score[j]=value;prev[j]=k
    paths=[];used=set()
    required_span=max(.20,min(.60,.20*length))
    for endpoint in np.argsort(score)[::-1]:
        j=int(endpoint);part=[]
        while j>=0 and nodes[j]['index'] not in used:
            part.append(nodes[j]);j=int(prev[j])
        part.reverse()
        if len(part)<20:continue
        span=part[-1]['station']-part[0]['station']
        if span<=required_span or len(part)*step/max(span,step)<=.55:continue
        paths.append(part);used.update(x['index'] for x in part)
    if paths:
        path=sorted([x for part in paths for x in part],key=lambda x:x['station'])
        # A substantial body fixes identity. Short end fragments can then
        # support the same IFC member, but cannot establish a missing member
        # by themselves. Keep the original 90 mm within-fragment link gate.
        stations=np.asarray([x['station'] for x in path]);offsets=np.asarray([x['center'] for x in path])
        def prediction(s):
            if s<stations[0]:return offsets[0]+(s-stations[0])*(offsets[1]-offsets[0])/(stations[1]-stations[0])
            if s>stations[-1]:return offsets[-1]+(s-stations[-1])*(offsets[-1]-offsets[-2])/(stations[-1]-stations[-2])
            return np.array([np.interp(s,stations,offsets[:,i]) for i in range(2)])
        for endpoint in np.argsort(score)[::-1]:
            j=int(endpoint);part=[]
            while j>=0 and nodes[j]['index'] not in used:
                part.append(nodes[j]);j=int(prev[j])
            part.reverse()
            if len(part)<5 or part[-1]['station']-part[0]['station']<.047:continue
            if any(np.linalg.norm(x['center']-prediction(x['station']))>.030 for x in part):continue
            paths.append(part);used.update(x['index'] for x in part)
        path=sorted([x for part in paths for x in part],key=lambda x:x['station'])
    else:
        j=int(np.argmax(score));path=[]
        while j>=0:path.append(nodes[j]);j=int(prev[j])
        path.reverse()
    span=path[-1]['station']-path[0]['station']
    coverage=len(path)*step/max(span,step)
    # Identity can be supported by a substantial visible body even when much
    # of a long member is occluded. This threshold establishes partial evidence,
    # never complete length; the missing stations remain unassigned.
    required_span=max(.20,min(.60,.20*length))
    result={'status':'observed-trace' if paths else 'fragment',
            'visibleChainCount':len(paths),
            'candidateRows':len(ids),'sectionCount':len(nodes),'traceSections':len(path),'spanM':span,'coverage':coverage,
            'nodes':[{k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in x.items()} for x in path]}
    selected=np.zeros(len(ids),bool)
    if result['status']=='observed-trace':
        # Only local observed sections own source rows; no gap extrapolation.
        for node in path:
            radial=pos-node['center'];distance=np.linalg.norm(radial,axis=1)
            alignment=abs(np.sum(radial*ns,axis=1))/np.maximum(distance*np.linalg.norm(ns,axis=1),1e-9)
            near=(abs(tt-node['station'])<.008)&(abs(distance-node['radius'])<.0015)&(alignment>.65)
            selected|=near
    result['points']=int(selected.sum());result['unitId']=unit['source']['designUnitId']
    result['centerlineM']=[(start+x['station']*d+x['center'][0]*u+x['center'][1]*v).tolist() for x in path]
    result['maxTransverseDisplacementM']=float(max(np.linalg.norm(x['center']) for x in path))
    result['_candidateIds']=ids
    return result,ids[selected]


def _local_axis(points,unit,centers):
    """Fit transverse IFC spline DOFs with enough local freedom for end curl.

    Section circles initialize the model, but fixed-radius original surfaces
    drive optimization. Unsupported gaps only get inferred spline geometry.
    """
    d=unit['direction'];u,v=_frame(d);length=unit['length'];start=unit['start']
    station=(centers-start)@d/length
    low,high=float(station.min()),float(station.max())
    if high-low<.15:return None
    interior=np.arange(low+.06/length,high-.02/length,.06/length)
    knots=np.r_[np.repeat(low,4),interior,np.repeat(high,4)]
    basis=BSpline(knots,np.eye(len(knots)-4),3,extrapolate=True)
    # Match the existing serializable support-clamped basis contract.
    from rebar_prior_axis import _SupportedSpline
    spline=_SupportedSpline(basis,low,high)
    anchor=spline(station);target=np.c_[(centers-start)@u,(centers-start)@v]
    coeff=np.linalg.lstsq(anchor,target,rcond=None)[0]
    delta=points-start;t=delta@d/length;xy=np.c_[delta@u,delta@v]
    design=spline(t);shape=coeff.shape
    smooth=np.diff(np.eye(len(coeff)),n=2,axis=0)
    def fun(flat):
        c=flat.reshape(shape)
        return np.r_[np.linalg.norm(xy-design@c,axis=1)-unit['radius'],
                     (.15*smooth@c).ravel()]
    def jac(flat):
        c=flat.reshape(shape);radial=design@c-xy
        radial/=np.maximum(np.linalg.norm(radial,axis=1)[:,None],1e-12)
        return np.vstack(((design[:,:,None]*radial[:,None,:]).reshape(len(xy),-1),
                          .15*np.kron(smooth,np.eye(2))))
    fit=least_squares(fun,coeff.ravel(),jac=jac,loss='soft_l1',f_scale=.0005,max_nfev=35)
    coeff=fit.x.reshape(shape)
    return dict(start=start,tangent=d,u=u,v=v,spline=spline,coeff=coeff,
                shapeModel='local-flexible-spline',fitRmseM=float(np.sqrt(np.mean(fun(fit.x)[:len(xy)]**2))))


def _fit_ifc_axis(points,normals,seed_ids,candidate_ids,unit,units,envelope,trace):
    """Optimize the IFC sweep-axis degrees of freedom against source surfaces.

    Cross-section centers initialize the solver only. Fixed nominal radius and
    the design axial parameterization are retained. Unsupported model portions
    remain inferred; only validated source support can acquire class ownership.
    """
    centers=np.asarray(trace['centerlineM'])
    spatial=np.floor(((points[seed_ids]-unit['start'])@unit['direction'])/.005).astype(int)%2==0
    train=seed_ids[spatial];held=seed_ids[~spatial]
    if len(held)<50:return None,'insufficient-held-out-source',np.empty(0,int)
    best=None;attempts=[];tolerance=max(.0009,.35*unit['radius'])
    for count in (6,10,14,'local'):
        if count=='local':axis,reason=_local_axis(points[train],unit,centers),None
        else:
            axis,reason=fit_prior_axis(points[train],unit['start'],unit['direction'],unit['length'],unit['radius'],
                initial_centerline=centers,guard_bending=True,spline_coefficients=count)
        if axis is None:attempts.append({'requestedCoefficients':count,'reason':reason});continue
        delta=points[held]-axis['start'];along=delta@axis['tangent']
        xy=np.c_[delta@axis['u'],delta@axis['v']];offset=axis['spline'](along/unit['length'])@axis['coeff']
        residual=abs(np.linalg.norm(xy-offset,axis=1)-unit['radius'])
        fraction=float(np.mean(residual<=tolerance));median=float(np.median(residual))
        attempts.append({'requestedCoefficients':count,'heldOutSupport':fraction,'heldOutMedianResidualM':median,'shapeModel':axis['shapeModel']})
        if fraction<.80 or median>.0009:continue
        cs=(centers-unit['start'])@unit['direction']
        co=axis['spline'](cs/unit['length'])@axis['coeff']
        predicted=unit['start']+cs[:,None]*unit['direction']+co[:,:1]*axis['u']+co[:,1:]*axis['v']
        supported=int(np.count_nonzero(np.linalg.norm(predicted-centers,axis=1)<=max(.0025,.65*unit['radius'])))
        attempts[-1]['modelConsistentSections']=supported
        if best is None or (supported>best[4] and fraction>=best[1]-.01) or (supported>=best[4] and median<.85*best[2] and fraction>=best[1]-.01):
            best=(axis,fraction,median,count,supported)
    if best is None:return None,'ifc-axis-not-supported-on-held-out-source',np.empty(0,int)
    axis,fraction,median,count,_=best
    station=(centers-unit['start'])@unit['direction'];offset=axis['spline'](station/unit['length'])@axis['coeff']
    fitted=unit['start']+station[:,None]*unit['direction']+offset[:,:1]*axis['u']+offset[:,1:]*axis['v']
    deviation=np.linalg.norm(fitted-centers,axis=1)
    valid=(deviation<=max(.0025,.65*unit['radius']))
    valid &= np.array([_identity_allowed(point,unit,units,envelope) for point in fitted])
    held_delta=points[held]-unit['start'];held_s=held_delta@unit['direction']
    held_xy=np.c_[held_delta@axis['u'],held_delta@axis['v']]
    held_error=abs(np.linalg.norm(held_xy-axis['spline'](held_s/unit['length'])@axis['coeff'],axis=1)-unit['radius'])
    # Dense middle support must not conceal an unsupported terminal correction.
    local_validation=[]
    for i,s in enumerate(station):
        near=abs(held_s-s)<.014
        accepted=int(near.sum())>=5 and float(np.mean(held_error[near]<=tolerance))>=.65
        valid[i]&=accepted
        local_validation.append({'heldOutPoints':int(near.sum()),'accepted':bool(accepted)})
    if valid.sum()<20:return None,'insufficient-model-consistent-sections',np.empty(0,int)
    # Model fitting rejects inverted/isolated circle centers. Those windows do
    # not authorize ownership even when the smooth model passes through them.
    intervals=[]
    for s in station[valid]:
        lo=max(0.,float(s)-.008);hi=min(unit['length'],float(s)+.008)
        if intervals and lo<=intervals[-1][1]:intervals[-1][1]=max(intervals[-1][1],hi)
        else:intervals.append([lo,hi])
    delta=points[candidate_ids]-unit['start'];along=delta@unit['direction']
    xy=np.c_[delta@axis['u'],delta@axis['v']];offset=axis['spline'](along/unit['length'])@axis['coeff']
    radial=xy-offset;distance=np.linalg.norm(radial,axis=1)
    n=np.c_[normals[candidate_ids]@axis['u'],normals[candidate_ids]@axis['v']]
    alignment=abs(np.sum(radial*n,axis=1))/np.maximum(distance*np.linalg.norm(n,axis=1),1e-9)
    supported=np.zeros(len(candidate_ids),bool)
    for lo,hi in intervals:supported|=(along>=lo)&(along<=hi)
    supported&=(abs(distance-unit['radius'])<=tolerance)&(alignment>.65)
    ids=candidate_ids[supported]
    if len(ids)<100:return None,'insufficient-model-surface-source',np.empty(0,int)
    # The saved parameters reproduce the deformed IFC axis, including its
    # explicit support-clamped tangent continuation outside observed regions.
    spline=axis['spline'];base=getattr(spline,'spline',spline)
    full_s=np.linspace(0,1,97);full_offset=spline(full_s)@axis['coeff']
    full=unit['start']+full_s[:,None]*unit['length']*unit['direction']+full_offset[:,:1]*axis['u']+full_offset[:,1:]*axis['v']
    arc=float(np.linalg.norm(np.diff(full,axis=0),axis=1).sum())
    if arc>1.08*unit['length']:return None,'ifc-body-length-prior-exceeded',np.empty(0,int)
    observed_arc=0.
    for lo,hi in intervals:
        ss=np.linspace(lo,hi,max(2,int((hi-lo)/.003)+1));oo=spline(ss/unit['length'])@axis['coeff']
        cc=unit['start']+ss[:,None]*unit['direction']+oo[:,:1]*axis['u']+oo[:,1:]*axis['v']
        observed_arc+=float(np.linalg.norm(np.diff(cc,axis=0),axis=1).sum())
    parameters={'schema':'ifc-axis-flexible-registration-v1','sourceGlobalId':unit['source']['designBarId'].split(':')[0],
        'designUnitId':unit['source']['designUnitId'],'model':'IfcSweptDiskSolid axis transverse B-spline deformation',
        'designStartM':unit['start'].tolist(),'designEndM':unit['end'].tolist(),'designAxialLengthM':unit['length'],
        'nominalRadiusM':unit['radius'],'radiusChanged':False,'frameU':axis['u'].tolist(),'frameV':axis['v'].tolist(),
        'tangent':unit['direction'].tolist(),'degree':int(base.k),'knots':base.t.tolist(),'transverseCoefficientsM':axis['coeff'].tolist(),
        'basisSupportClamp':[float(getattr(spline,'low',0.)),float(getattr(spline,'high',1.))],
        'outsideSupport':'tangent continuation of fitted IFC axis; inferred geometry, never observed point ownership',
        'fittedIfcAxisM':full.tolist(),'observedSupportIntervalsM':intervals,'heldOutSourcePoints':len(held),
        'heldOutSurfaceSupport':fraction,'heldOutMedianResidualM':median,'heldOutPolicy':'alternating 5mm axial spatial bands','attempts':attempts,
        'rejectedLocalSectionIndices':np.flatnonzero(~valid).tolist(),'localSectionDeviationM':deviation.tolist(),
        'localSpatialValidation':local_validation,
        'actualCoefficientCount':len(axis['coeff']),'shapeModel':axis['shapeModel']}
    parameters.update(fittedBodyArcLengthM=arc,observedSupportedArcLengthM=observed_arc,
                      lengthPriorRatioRange=[1.,1.08],observedAxialCoverageFraction=sum(b-a for a,b in intervals)/unit['length'])
    return {'parameters':parameters,'observedCentersM':fitted[valid].tolist(),'intervals':intervals,
            'rmseM':axis['fitRmseM'],'pointCount':len(ids)},None,ids


def recover_bent_bars(points,normals,inventory,instances,status,owner,scene,scene_report,*,excluded=None,progress=None):
    points=np.asarray(points);normals=np.asarray(normals)
    if points.shape!=normals.shape or np.shape(owner)!=(len(points),) or np.shape(status)!=(len(points),):
        raise ValueError('bent trace arrays must be source aligned')
    excluded=np.zeros(len(points),bool) if excluded is None else np.asarray(excluded,bool)
    if excluded.shape!=(len(points),):raise ValueError('excluded mask must be source aligned')
    units=_unit_rows(inventory);envelope=fixture_envelope(scene,scene_report,units)
    rows={r['designUnitId']:r for r in instances};results=[]
    for unit in units:
        row=rows[unit['source']['designUnitId']]
        if unit['kind']!='straight' or unit['length']<.6:continue
        if progress:progress('弯曲钢筋：IFC 轴线柔性拟合',len(results),len(units))
        eligible=~excluded&((owner==0)|(owner==row['id']))&np.isfinite(points).all(axis=1)
        result,ids=_trace(points,normals,eligible,unit,units,envelope)
        candidate_ids=result.pop('_candidateIds')
        result.update(designUnitId=row['designUnitId'],designBarId=row['designBarId'],id=row['id'])
        if result['status']=='observed-trace' and len(ids)>=100:
            model,reason,ids=_fit_ifc_axis(points,normals,ids,candidate_ids,unit,units,envelope,result)
            if model is None:
                result.update(status='ifc-flexible-fit-rejected',reason=reason,points=0);results.append(result);continue
            added=ids[owner[ids]==0]
            if not len(added):
                result.update(status='already-supported',points=0);results.append(result);continue
            intervals=model['intervals'];result['flexibleRegistration']=model['parameters'];result['points']=len(added)
            result['observedCenterlineM']=model['observedCentersM']
            owner[ids]=row['id'];status[ids]=1
            row.update(status='fitted',reason='ifc-flexible-axis-surface-supported',pointCount=int(np.count_nonzero(owner==row['id'])),
                observedLengthM=sum(b-a for a,b in intervals),rmseM=model['rmseM'],
                fittedBodyArcLengthM=model['parameters']['fittedBodyArcLengthM'],
                observedSupportedArcLengthM=model['parameters']['observedSupportedArcLengthM'],
                centerlineM=model['parameters']['fittedIfcAxisM'],bodyDisplayCenterlineM=model['observedCentersM'],
                observedSupportIntervalsM=intervals,axisModel='ifc-flexible-sweep-axis',fitCandidateScope='fixture-bay-and-design-neighbourhood',
                axisEvidenceRangeM=[intervals[0][0],intervals[-1][1]],
                flexibleRegistration=model['parameters'],
                traceEvidence={k:v for k,v in result.items() if k not in ('nodes','centerlineM','flexibleRegistration','observedCenterlineM')})
        elif result['status']=='observed-trace':result['status']='insufficient-unique-source-support'
        results.append(result)
    return {'version':VERSION,'objects':results,'fixtureEnvelope':envelope,
            'policy':{'searchRadiusM':.15,'endpointMarginM':.04,'stationStepM':.012,'sectionHalfWidthM':.008,
                      'maximumLinkGapM':.09,'maximumTransverseStepM':.025,'neighbourIdentityMarginM':.003,
                      'radiusRatioRange':[.72,1.45],'heldOutFraction':.5,
                      'minimumBodySpan':'max(0.20m,min(0.60m,20% of design length)); at least20 supported stations',
                      'modelPolicy':'local sections initialize IFC axis degrees of freedom; final ownership uses the fitted fixed-radius IFC sweep and validated station intervals',
                      'ownership':'unclaimed original rows supported by fitted IFC surfaces only; no points assigned in unsupported gaps',
                      'existencePrior':'design presence requests a search; does not imply observation or physical presence'}}
