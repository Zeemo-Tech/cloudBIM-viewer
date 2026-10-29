"""IFC sweep-axis flexible registration for bent bars inside the fixture bay.

BIM establishes identity and a bounded search corridor, never a point source.
An absent or occluded design member does not receive fabricated observations.
"""
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from collections import deque
import multiprocessing

import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import least_squares, linear_sum_assignment
from scipy.interpolate import BSpline
from algorithms.rebar_control_net import _frame, _unit_rows
from rebar_prior_axis import fit_prior_axis

VERSION = 'bim-flexible-axis-v8'


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
    order=np.argsort(counts)[::-1];suppressed=np.zeros(len(vote),bool);found=[];trials=0
    for at in order:
        if counts[at]<8 or trials>=12:break
        if suppressed[at]:continue
        center=vote[at]
        # Preserve the greedy vote order, including suppression by centres that
        # fail the design prior. Mark their local neighbourhood once instead of
        # comparing every later source vote with every previous centre in Python.
        # The tree is a broad phase; retain the original strict distance gate.
        near_votes=np.asarray(tree.query_ball_point(center,np.nextafter(1.5*r,np.inf)),dtype=int)
        near_votes=near_votes[np.linalg.norm(vote[near_votes]-center,axis=1)<1.5*r]
        suppressed[near_votes]=True
        if not prior_distance(center):continue
        # Neighbour or exterior votes must not exhaust the current member's
        # bounded fitting budget before its own cross-section is considered.
        trials+=1
        near=np.linalg.norm(q-center,axis=1)<2*r
        if near.sum()<14:continue
        xy=q[near];nt=n[near];ntlen=np.linalg.norm(nt,axis=1)
        def fun(x):return np.linalg.norm(xy-x[:2],axis=1)-x[2]
        # Every alternate source row is held out from circle optimization.
        train=xy[::2]
        def training(x):return np.linalg.norm(train-x[:2],axis=1)-x[2]
        def jacobian(x):
            delta=x[:2]-train;length=np.linalg.norm(delta,axis=1)
            return np.c_[delta/np.maximum(length[:,None],1e-12),-np.ones(len(train))]
        bounds=([center[0]-.04,center[1]-.04,.001],[center[0]+.04,center[1]+.04,.06])
        fit=least_squares(training,[*center,r],jac=jacobian,bounds=bounds,loss='soft_l1',f_scale=.0005,max_nfev=45)
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

def _section_worker_init():
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)


def _section_task(task):
    """Fit one independent station using only its bounded source rows."""
    i,s,q,n,r,start,d,u,v,peers,envelope=task

    peer_starts=np.asarray([p[0] for p in peers]).reshape(-1,3)
    peer_directions=np.asarray([p[1] for p in peers]).reshape(-1,3)
    peer_lengths=np.asarray([p[2] for p in peers])
    def own(c):
        point=start+s*d+c[0]*u+c[1]*v;own_dist=np.linalg.norm(c)
        if not _inside_body(point,d,envelope):return False
        delta=point-peer_starts
        along=np.clip(np.einsum('ij,ij->i',delta,peer_directions),0,peer_lengths)
        distance=np.linalg.norm(delta-along[:,None]*peer_directions,axis=1)
        return bool(np.all(distance>=own_dist+.003))

    return i,s,_sections(q,n,r,own)


def _section_batch(tasks):
    return [_section_task(task) for task in tasks]


class _TraceSections:
    """Bounded speculative section work, reconciled before sequential ownership."""
    block_size=4

    def __init__(self,points,normals,eligible,unit,units,envelope,workers):
        d=unit['direction'];u,v=_frame(d);start=unit['start']
        source_ids=np.flatnonzero(eligible)
        delta=points[source_ids]-start;t=delta@d;x=delta@u;y=delta@v
        mask=(t>-.04)&(t<unit['length']+.04)&(x*x+y*y<.15**2)
        self.ids=source_ids[mask];self.pos=np.c_[x[mask],y[mask]];self.tt=t[mask]
        self.ns=np.c_[normals[self.ids]@u,normals[self.ids]@v]
        peers=[(other['start'],other['direction'],other['length']) for other in units
               if other is not unit and other['kind']=='straight' and abs(d@other['direction'])>=.98
               and other['source']['designBarId']!=unit['source']['designBarId']]
        self.parameters=(unit['radius'],start,d,u,v,peers,envelope)
        self.stations=np.arange(0,unit['length']+.0001,.012)
        order=np.argsort(self.tt,kind='stable');ordered=self.tt[order]
        self.rows=[]
        for station in self.stations:
            lo=np.searchsorted(ordered,np.nextafter(station-.008,-np.inf),side='left')
            hi=np.searchsorted(ordered,np.nextafter(station+.008,np.inf),side='right')
            local=order[lo:hi]
            self.rows.append(np.sort(local[abs(self.tt[local]-station)<.008]))
        self.pending=deque();self.next_station=0;self.window=max(1,8*workers)

    def task(self,i):
        use=self.rows[i]
        return (i,self.stations[i],self.pos[use],self.ns[use],*self.parameters)

    def prefetch(self,executor):
        while len(self.pending)<self.window and self.next_station<len(self.stations):
            stop=min(len(self.stations),self.next_station+self.block_size)
            indices=range(self.next_station,stop)
            self.pending.append((tuple(indices),executor.submit(_section_batch,[self.task(i) for i in indices])))
            self.next_station=stop

    def results(self,eligible,executor):
        keep=eligible[self.ids]
        dirty=[]
        if not keep.all():
            remap=np.cumsum(keep)-1
            for i,use in enumerate(self.rows):
                valid=keep[use]
                if i<self.next_station and not valid.all():dirty.append(i)
                self.rows[i]=remap[use[valid]]
            self.ids=self.ids[keep];self.pos=self.pos[keep];self.ns=self.ns[keep];self.tt=self.tt[keep]
        replacements={}
        if executor is None:
            yield from map(_section_task,(self.task(i) for i in range(len(self.stations))))
            return
        # Only prefetched windows whose actual source rows changed need a refit.
        # Both the old and replacement payloads are bounded by the prefetch window.
        repairs=[executor.submit(_section_batch,[self.task(i) for i in dirty[j:j+self.block_size]])
                 for j in range(0,len(dirty),self.block_size)]
        for future in repairs:
            replacements.update((i,(i,s,candidates)) for i,s,candidates in future.result())
        self.prefetch(executor)
        while self.pending:
            _,future=self.pending.popleft()
            self.prefetch(executor)
            for result in future.result():
                yield replacements.get(result[0],result)


def _track_prediction(part, station):
    """Robust local tangent prediction, in the design-axis transverse frame."""
    support=part[:7] if station<part[0]['station'] else part[-7:]
    ss=np.array([x['station'] for x in support]);xy=np.array([x['center'] for x in support])
    a,b=np.triu_indices(len(ss),1);gap=ss[b]-ss[a];valid=gap>=.018
    slope=np.median((xy[b[valid]]-xy[a[valid]])/gap[valid,None],axis=0) if valid.any() else np.zeros(2)
    return np.median(xy+(station-ss)[:,None]*slope,axis=0)


def _track_gate(gap, radius):
    # Local circle uncertainty plus a bounded curvature allowance. A missed
    # section can be crossed, but cannot cause a jump to a parallel tube.
    return min(.030,max(.003,1.5*radius)+2*gap*gap)


def _axial_tracks(sections, radius, length, step=.012):
    """Cluster section modes into axial tracks, then grow one physical body.

    A section can contain several valid tubes. Match each mode to at most one
    predicted track; other modes start independent tracks, never blank out the
    station. A substantial track anchors identity. Its two ends can grow across
    occlusion only when the two local tangent predictions agree. No points in
    the occluded interval become observations.
    """
    tracks=[]
    for index,station,candidates in sections:
        for c in candidates:c.update(station=float(station),index=index)
        if not candidates:continue
        active=[i for i,t in enumerate(tracks) if station-t[-1]['station']<=.090001]
        costs=np.full((len(active),len(candidates)+len(active)),2.)
        for i,at in enumerate(active):
            track=tracks[at];gap=station-track[-1]['station']
            predicted=_track_prediction(track,station)
            distance=np.linalg.norm(np.array([c['center'] for c in candidates])-predicted,axis=1)
            gate=_track_gate(gap,radius)
            costs[i,:len(candidates)]=np.where(distance<=gate,distance/gate+.1*(gap/step-1),1e6)
        matched=set()
        if active:
            for i,j in zip(*linear_sum_assignment(costs)):
                if j<len(candidates) and costs[i,j]<2:
                    tracks[active[i]].append(candidates[j]);matched.add(j)
        tracks.extend([c] for j,c in enumerate(candidates) if j not in matched)
    required=max(.20,min(.60,.20*length))
    substantial=[t for t in tracks if len(t)>=20 and t[-1]['station']-t[0]['station']>required
                 and len(t)*step/(t[-1]['station']-t[0]['station'])>=.55]
    if not substantial:return [],tracks
    def merit(part):
        # Count supported axial stations, not raw surface-point density.
        # The bounded design term resolves equally long disjoint tracks without
        # preventing a continuously observed, genuinely bent member.
        return sum(min(1.,x['score'])-.03*min(5.,np.linalg.norm(x['center'])/radius) for x in part)
    anchor=max(substantial,key=merit)
    path=list(anchor);used={x['index'] for x in path}
    remaining=[t for t in tracks if t is not anchor]
    max_gap=max(.09,min(.30,.25*length))
    while remaining:
        proposals=[]
        for at,track in enumerate(remaining):
            for before in (True,False):
                part=[x for x in track if x['station']<path[0]['station']] if before else [x for x in track if x['station']>path[-1]['station']]
                if len(part)<5 or part[-1]['station']-part[0]['station']<.047:continue
                anchor=path[0] if before else path[-1];tip=part[-1] if before else part[0]
                gap=abs(tip['station']-anchor['station'])
                if gap>max_gap:continue
                forward=np.linalg.norm(_track_prediction(path,tip['station'])-tip['center'])
                backward=np.linalg.norm(_track_prediction(part,anchor['station'])-anchor['center'])
                error=max(forward,backward);gate=_track_gate(gap,radius)
                if error<=gate:proposals.append((error/gate,at,part))
        if not proposals:break
        _,at,part=min(proposals,key=lambda x:(x[0],-len(x[2]),x[1]))
        path=sorted(path+part,key=lambda x:x['station']);used.update(x['index'] for x in part)
        remaining.pop(at)
    # Fill missed interior stations from their own measured clusters, guided by
    # the anchored axis on both sides. Never add a second mode at one station.
    ss=np.array([x['station'] for x in path]);xy=np.array([x['center'] for x in path])
    for index,station,candidates in sections:
        if index in used or not candidates or not ss[0]<station<ss[-1]:continue
        near=np.searchsorted(ss,station);gap=ss[near]-ss[near-1]
        if gap>max_gap:continue
        predicted=np.array([np.interp(station,ss,xy[:,j]) for j in range(2)])
        candidate=min(candidates,key=lambda x:np.linalg.norm(x['center']-predicted))
        if np.linalg.norm(candidate['center']-predicted)<=_track_gate(min(station-ss[near-1],ss[near]-station),radius):
            path.append(candidate);used.add(index)
    return sorted(path,key=lambda x:x['station']),tracks


def _trace(points,normals,eligible,unit,units,envelope,*,executor=None,workers=1,prepared=None):
    d=unit['direction'];u,v=_frame(d);r=unit['radius'];length=unit['length'];start=unit['start']
    prepared=prepared or _TraceSections(points,normals,eligible,unit,units,envelope,workers)
    sections=list(prepared.results(eligible,executor))
    path,tracks=_axial_tracks(sections,r,length)
    ids,pos,ns=prepared.ids,prepared.pos,prepared.ns
    section_count=sum(len(choices) for _,_,choices in sections)
    selected_nodes={id(x) for x in path}
    evidence={'candidateTrackCount':len(tracks),
        'competingSectionCount':sum(len(choices)>1 for _,_,choices in sections),
        'tracks':[{'stationRangeM':[float(t[0]['station']),float(t[-1]['station'])],
                   'sections':len(t),'selectedSections':sum(id(x) in selected_nodes for x in t)} for t in tracks]}
    if not path:return {'status':'fragment' if tracks else 'no-sections',
        'candidateRows':len(ids),'sectionCount':section_count,'_candidateIds':ids,**evidence},np.empty(0,int)
    span=path[-1]['station']-path[0]['station'];step=.012
    result={'status':'observed-trace','visibleChainCount':sum(x['selectedSections']>0 for x in evidence['tracks']),
            'candidateRows':len(ids),'sectionCount':section_count,'traceSections':len(path),'spanM':span,'coverage':len(path)*step/max(span,step),
            **evidence,'nodes':[{k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in x.items()} for x in path]}
    selected=np.zeros(len(ids),bool)
    for node in path:
        local=prepared.rows[node['index']]
        radial=pos[local]-node['center'];distance=np.linalg.norm(radial,axis=1)
        local_normals=ns[local]
        alignment=abs(np.sum(radial*local_normals,axis=1))/np.maximum(distance*np.linalg.norm(local_normals,axis=1),1e-9)
        near=(abs(distance-node['radius'])<.0015)&(alignment>.65)
        selected[local[near]]=True
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


def _axis_task(task):
    count,train,unit,centers=task
    if count=='local':return _local_axis(train,unit,centers),None
    return fit_prior_axis(train,unit['start'],unit['direction'],unit['length'],unit['radius'],
                          initial_centerline=centers,guard_bending=True,spline_coefficients=count)


def _radial_normal_correlation(radial, normals):
    """Sign-invariant variation agreement; constant/noisy plane normals fail."""
    oriented=normals*np.where(np.sum(radial*normals,axis=1)>=0,1.,-1.)[:,None]
    rr=radial-radial.mean(axis=0);nn=oriented-oriented.mean(axis=0)
    return float(np.sum(rr*nn)/max(np.sqrt(np.sum(rr*rr)*np.sum(nn*nn)),1e-12))


def _grow_axis_support(along, radial, normals, surface, seed_stations, length, radius, identity_allowed,
                       *, refine=True, coarse_support=None):
    """Grow observed support on the fitted tube using fresh local source evidence.

    Circle discovery can fail in clutter even when a known axis explains the
    surface. Growth therefore validates the fixed model directly. Distance is
    insufficient: require radial AND normal evidence, held-out support and a
    bounded connection to an accepted fragment. A link never observes its gap.
    Weaker partial-view patches require reliable support on BOTH sides and
    independently correlated radial/normal variation in train/held-out bands.
    """
    # Preserve the established 12 mm pass first. A second, interleaved grid is
    # evaluated only where its window contains still-unclaimed surface rows.
    step=.006;coarse_step=2*step;stations=np.arange(0,length+.0001,step)
    coarse=np.arange(0,len(stations),2)
    accepted=np.zeros(len(stations),bool)
    accepted[2*np.clip(np.rint(seed_stations/coarse_step).astype(int),0,len(coarse)-1)]=True
    seeds=accepted.copy();validated=np.zeros(len(stations),bool)
    bridgeable=np.zeros(len(stations),bool);linkable=np.zeros(len(stations),bool);evidence={}
    identity=np.full(len(stations),-1,np.int8);identity[seeds]=1
    def allowed(i):
        if identity[i]<0:identity[i]=bool(identity_allowed(stations[i]))
        return bool(identity[i])
    distance=np.linalg.norm(radial,axis=1);nl=np.linalg.norm(normals,axis=1)
    held=np.floor(along/.005).astype(int)%2!=0
    # Reuse the corridor projections; sort only the narrow tube neighbourhood,
    # not every candidate in the much wider design search. No new spatial tree,
    # scan copy, process pool or per-fragment model fit is needed.
    near=np.flatnonzero(distance<2*radius)
    order=near[np.argsort(along[near],kind='stable')];ordered=along[order]
    bounds=np.searchsorted(ordered,np.c_[stations-.008,stations+.008])
    def validate_window(i):
        if seeds[i]:return
        s=stations[i]
        lo,hi=bounds[i];local=order[lo:hi]
        good=local[surface[local]&(nl[local]>.65)]
        held_local=local[held[local]]
        if len(good)<12 or len(good)<.35*len(local) or len(held_local)<5:return
        held_fraction=float(np.mean(surface[held_local]))
        if held_fraction<.35:return
        rr=radial[good]/np.maximum(distance[good,None],1e-12)
        nn=normals[good]/nl[good,None]
        radial_spread=float(np.linalg.eigvalsh(rr.T@rr/len(rr))[0])
        normal_spread=float(np.linalg.eigvalsh(nn.T@nn/len(nn))[0])
        strict=(len(good)>=.62*len(local) and held_fraction>=.65
                and min(radial_spread,normal_spread)>=.055)
        correlation=None
        span=float(np.ptp(along[good]))
        if not strict:
            # A narrow visible arc can have little normal spread, and crossing
            # clutter can dilute the local support fraction. Only a short patch
            # bracketed by reliable axial fragments may use this evidence. The
            # original pointwise radius/residual/normal tests remain unchanged.
            split=held[good]
            if (span<.006 or radial_spread<.10 or normal_spread<.01
                    or split.sum()<5 or (~split).sum()<5):return
            correlation=min(_radial_normal_correlation(rr[part],nn[part]) for part in (split,~split))
            if correlation<.65:return
        if not allowed(i):return
        validated[i]=strict;bridgeable[i]=not strict;linkable[i]=span>=.006
        evidence[i]={'stationM':float(s),'sourcePoints':len(good),'heldOutPoints':len(held_local),
                     'radialSpread':radial_spread,'normalSpread':normal_spread,
                     'sourceAxialSpanM':span,'heldOutSurfaceSupport':held_fraction,
                     'sourceRangeM':[float(along[good].min()),float(along[good].max())],
                     'validation':'strict' if strict else 'bilateral-fragment',
                     **({'radialNormalCorrelation':correlation} if correlation is not None else {})}
    # Preserve ordinary adjacent growth, then link independently supported short
    # fragments in either direction. Ownership/fixture boundaries are checked
    # at every skipped station; a nearer disconnected island cannot hide one.
    def grow_adjacent(stride):
        frontier=list(np.flatnonzero(accepted))
        while frontier:
            at=frontier.pop()
            for i in (at-stride,at+stride):
                if 0<=i<len(stations) and not accepted[i] and validated[i]:
                    accepted[i]=True;frontier.append(i)
    max_gap=.09;connections={};linked=np.zeros(len(stations),bool)
    def connect_fragments(stride):
        grow_adjacent(stride)
        adjacent=accepted.copy()
        # Weak bilateral patches remain observations, never independent anchors
        # for the refinement pass or for extending the search radius.
        nodes=np.flatnonzero((accepted&~bridgeable)|(validated&linkable))
        def established_edge(left,right):
            # Adjacent coarse source windows already overlap. Traversing that
            # retained interval introduces neither a gap nor a new source claim;
            # rechecking every midpoint would dominate the tiny fragment pass.
            # Every newly validated window still calls allowed() independently.
            return (stride==1 and right-left==2
                    and coarse_accepted[left] and coarse_accepted[right])
        cuts=[0]
        for k in range(1,len(nodes)):
            left,right=nodes[k-1:k+1]
            if established_edge(left,right):continue
            if (stations[right]-stations[left]>max_gap+1e-10
                    or not all(allowed(j) for j in range(left+stride,right,stride))):cuts.append(k)
        cuts.append(len(nodes))
        for lo,hi in zip(cuts[:-1],cuts[1:]):
            part=nodes[lo:hi]
            if not seeds[part].any():continue
            accepted[part]=True
            for left,right in zip(part[:-1],part[1:]):
                if right-left<=stride or established_edge(left,right):continue
                connections[(int(left),int(right))]={'fromM':float(stations[left]),'toM':float(stations[right]),
                    'gapM':float(stations[right]-stations[left]),'validationStepM':stride*step}
                inside=np.arange(left+stride,right,stride)
                accepted[inside[bridgeable[inside]]]=True
        # A clipped end window need not initiate an independent link, but can
        # follow its newly linked body through ordinary adjacent validation.
        grow_adjacent(stride)
        linked[:] |= accepted&validated&~adjacent

    inherited_links=0
    if coarse_support is None:
        for i in coarse:validate_window(i)
        connect_fragments(2)
    else:
        # The late pass retains the already committed fixed-axis observations.
        # Their supported rows cannot have been taken by a later body. Recheck
        # only unclaimed windows against the now-final body ownership mask.
        coarse_stations,prior=coarse_support
        accepted[2*np.rint(np.asarray(coarse_stations)/coarse_step).astype(int)]=True
        identity[accepted]=1
        inherited_links=prior['linkedFragmentSections']
        for edge in prior['fragmentConnections']:
            connections[(int(round(edge['fromM']/step)),int(round(edge['toM']/step)))]=edge
        for item in prior['sourceValidation']:
            i=2*int(round(item['stationM']/coarse_step))
            evidence[i]=item
            validated[i]=item['validation']=='strict';bridgeable[i]=not validated[i]
            linkable[i]=item['sourceAxialSpanM']>=.006
        for i in coarse[~accepted[coarse]]:validate_window(i)
    coarse_accepted=accepted.copy()
    extra=np.empty(0,int)
    if refine:
        covered=np.zeros(len(order),bool)
        for i in np.flatnonzero(accepted):
            lo,hi=bounds[i];covered[lo:hi]=True
        remaining=surface[order]&~covered&(ordered>=0)&(ordered<=length)
        prefix=np.r_[0,np.cumsum(remaining,dtype=np.int64)]
        extra=np.arange(1,len(stations),2)
        extra=extra[(prefix[bounds[extra,1]]-prefix[bounds[extra,0]])>0]
    for i in extra:validate_window(i)
    if len(extra):connect_fragments(1)
    intervals=[];windows=[];observed=[]
    for i in np.flatnonzero(accepted):
        s=stations[i]
        lo=max(0.,float(s)-.008);hi=min(length,float(s)+.008)
        if not coarse_accepted[i]:
            # Refining the grid must not invent extra observed length around a
            # tiny cluster. New windows stop at their measured surface extent.
            low,high=evidence[i]['sourceRangeM'];lo=max(lo,low);hi=min(hi,high)
        if hi<lo:continue
        observed.append(float(np.clip(s,lo,hi)))
        windows.append((lo,hi))
    for lo,hi in sorted(windows):
        if intervals and lo<=intervals[-1][1]:intervals[-1][1]=max(intervals[-1][1],hi)
        else:intervals.append([lo,hi])
    return intervals,np.unique(observed),{
        'seedSections':int(seeds.sum()),'grownSections':int((accepted&~seeds).sum()),
        'linkedFragmentSections':inherited_links+int(linked.sum()),
        'bridgedFragmentSections':int((accepted&bridgeable).sum()),
        'coarseStepM':coarse_step,'refinementStepM':step,
        'adaptiveWindowsEvaluated':len(extra),'adaptiveGrownSections':int((accepted&~coarse_accepted).sum()),
        'maxFragmentGapM':max_gap,'fragmentConnections':list(connections.values()),
        'validatedUnconnectedSections':int((validated&~accepted).sum()),
        'sourceValidation':[x for i,x in evidence.items() if accepted[i]],
        'policy':'bidirectional bounded fragment links on fixed fitted axis; preserve coarse support then refine only windows containing unclaimed surface rows; unchanged strict or bilateral held-out gates; new support clipped to measured extent; identity checked throughout links; no inferred-gap ownership'}


def _fit_ifc_axis(points,normals,seed_ids,candidate_ids,unit,units,envelope,trace,*,executor=None,on_submitted=None,
                  defer_refinement=False):
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
    counts=(6,10,14,'local');training=points[train]
    tasks=[(count,training,unit,centers) for count in counts]
    results=map(_axis_task,tasks) if executor is None else executor.map(_axis_task,tasks)
    if on_submitted is not None:on_submitted()
    for count,(axis,reason) in zip(counts,results):
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
    delta=points[candidate_ids]-unit['start'];along=delta@unit['direction']
    xy=np.c_[delta@axis['u'],delta@axis['v']];offset=axis['spline'](along/unit['length'])@axis['coeff']
    radial=xy-offset;distance=np.linalg.norm(radial,axis=1)
    n=np.c_[normals[candidate_ids]@axis['u'],normals[candidate_ids]@axis['v']]
    alignment=abs(np.sum(radial*n,axis=1))/np.maximum(distance*np.linalg.norm(n,axis=1),1e-9)
    surface=(abs(distance-unit['radius'])<=tolerance)&(alignment>.65)
    def growth_identity(s):
        offset=axis['spline'](s/unit['length'])@axis['coeff']
        point=unit['start']+s*unit['direction']+offset[0]*axis['u']+offset[1]*axis['v']
        return _identity_allowed(point,unit,units,envelope)
    # A failed local circle is a detector failure, not proof that the source
    # disappears. Grow from validated anchors using the fixed-axis surface.
    intervals,observed_stations,growth=_grow_axis_support(
        along,radial,n,surface,station[valid],unit['length'],unit['radius'],growth_identity,
        refine=not defer_refinement)
    supported=np.zeros(len(candidate_ids),bool)
    for lo,hi in intervals:supported|=(along>=lo)&(along<=hi)
    supported&=surface
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
        'outsideSupport':'tangent continuation outside fitted basis domain; observed ownership requires separate axial source validation',
        'fittedIfcAxisM':full.tolist(),'observedSupportIntervalsM':intervals,'heldOutSourcePoints':len(held),
        'heldOutSurfaceSupport':fraction,'heldOutMedianResidualM':median,'heldOutPolicy':'alternating 5mm axial spatial bands','attempts':attempts,
        'rejectedLocalSectionIndices':np.flatnonzero(~valid).tolist(),'localSectionDeviationM':deviation.tolist(),
        'localSpatialValidation':local_validation,'axialGrowth':growth,
        'actualCoefficientCount':len(axis['coeff']),'shapeModel':axis['shapeModel']}
    parameters.update(fittedBodyArcLengthM=arc,observedSupportedArcLengthM=observed_arc,
                      lengthPriorRatioRange=[1.,1.08],observedAxialCoverageFraction=sum(b-a for a,b in intervals)/unit['length'])
    observed_offset=spline(observed_stations/unit['length'])@axis['coeff']
    observed=unit['start']+observed_stations[:,None]*unit['direction']+observed_offset[:,:1]*axis['u']+observed_offset[:,1:]*axis['v']
    model={'parameters':parameters,'observedCentersM':observed.tolist(),'intervals':intervals,
           'rmseM':axis['fitRmseM'],'pointCount':len(ids)}
    if defer_refinement:
        # Keep only narrow-corridor row IDs and the small fitted model. Global
        # source arrays remain shared; no scan copy or second spatial tree.
        model['_fragmentRefinement']=(axis,candidate_ids[distance<2*unit['radius']],
                                      station[valid],observed_stations)
    return model,None,ids


def _refine_fixed_axis_support(points,normals,owner,row_id,unit,units,envelope,model):
    """Find extra source support after all body models/owners have settled."""
    axis,candidate_ids,seeds,coarse_stations=model.pop('_fragmentRefinement')
    ids=candidate_ids[(owner[candidate_ids]==0)|(owner[candidate_ids]==row_id)]
    delta=points[ids]-unit['start'];along=delta@unit['direction']
    spline=axis['spline'];coeff=axis['coeff']
    radial=np.c_[delta@axis['u'],delta@axis['v']]-spline(along/unit['length'])@coeff
    distance=np.linalg.norm(radial,axis=1)
    n=np.c_[normals[ids]@axis['u'],normals[ids]@axis['v']]
    alignment=abs(np.sum(radial*n,axis=1))/np.maximum(distance*np.linalg.norm(n,axis=1),1e-9)
    surface=(abs(distance-unit['radius'])<=max(.0009,.35*unit['radius']))&(alignment>.65)
    def identity(s):
        offset=spline(s/unit['length'])@coeff
        return _identity_allowed(unit['start']+s*unit['direction']+offset[0]*axis['u']+offset[1]*axis['v'],
                                 unit,units,envelope)
    intervals,stations,growth=_grow_axis_support(along,radial,n,surface,seeds,unit['length'],unit['radius'],identity,
        coarse_support=(coarse_stations,model['parameters']['axialGrowth']))
    supported=np.zeros(len(ids),bool)
    for lo,hi in intervals:supported|=(along>=lo)&(along<=hi)
    added=ids[supported&surface&(owner[ids]==0)]
    observed_arc=0.
    for lo,hi in intervals:
        ss=np.linspace(lo,hi,max(2,int((hi-lo)/.003)+1));off=spline(ss/unit['length'])@coeff
        cc=unit['start']+ss[:,None]*unit['direction']+off[:,:1]*axis['u']+off[:,1:]*axis['v']
        observed_arc+=float(np.linalg.norm(np.diff(cc,axis=0),axis=1).sum())
    off=spline(stations/unit['length'])@coeff
    observed=unit['start']+stations[:,None]*unit['direction']+off[:,:1]*axis['u']+off[:,1:]*axis['v']
    growth.update(commitPhase='after-all-body-fits',addedSourcePoints=len(added))
    model['parameters'].update(observedSupportIntervalsM=intervals,axialGrowth=growth,
        observedSupportedArcLengthM=observed_arc,observedAxialCoverageFraction=sum(b-a for a,b in intervals)/unit['length'])
    model.update(intervals=intervals,observedCentersM=observed.tolist(),pointCount=model['pointCount']+len(added))
    return added


def recover_bent_bars(points,normals,inventory,instances,status,owner,scene,scene_report,*,excluded=None,progress=None,workers=1,executor=None):
    points=np.asarray(points);normals=np.asarray(normals)
    if points.shape!=normals.shape or np.shape(owner)!=(len(points),) or np.shape(status)!=(len(points),):
        raise ValueError('bent trace arrays must be source aligned')
    excluded=np.zeros(len(points),bool) if excluded is None else np.asarray(excluded,bool)
    if excluded.shape!=(len(points),):raise ValueError('excluded mask must be source aligned')
    if isinstance(workers,bool) or not isinstance(workers,(int,np.integer)) or workers<1:
        raise ValueError('workers must be a positive integer')
    workers=min(8,int(workers))
    units=_unit_rows(inventory);envelope=fixture_envelope(scene,scene_report,units)
    rows={r['designUnitId']:r for r in instances};results=[];refinements=[]
    # Workers only compute local fits. The parent publishes bars in design order
    # and invalidates speculative sections touched by new source-row ownership.
    # An injected pool remains owned by the pipeline that created it.
    pool=(nullcontext(executor) if executor is not None else
          ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),
                              initializer=_section_worker_init) if workers>1 else nullcontext(None))
    finite=np.isfinite(points).all(axis=1)
    with pool as executor:
        prepared=None
        targets=[unit for unit in units if unit['kind']=='straight' and unit['length']>=.6]
        for target_index,unit in enumerate(targets):
            row=rows[unit['source']['designUnitId']]
            if progress:progress('弯曲钢筋：IFC 轴线柔性拟合',len(results),len(units))
            eligible=~excluded&((owner==0)|(owner==row['id']))&finite
            result,ids=_trace(points,normals,eligible,unit,units,envelope,executor=executor,workers=workers,prepared=prepared)
            prepared=None
            def prepare_next():
                nonlocal prepared
                if executor is None or target_index+1>=len(targets):return
                following=targets[target_index+1]
                following_row=rows[following['source']['designUnitId']]
                # The current body is provisional until its revision commits.
                # Prefetch a superset including rows it may release; results()
                # removes all rows still owned by it after the commit.
                available=~excluded&((owner==0)|(owner==following_row['id'])|(owner==row['id']))&finite
                prepared=_TraceSections(points,normals,available,following,units,envelope,workers)
                prepared.prefetch(executor)
            candidate_ids=result.pop('_candidateIds')
            result.update(designUnitId=row['designUnitId'],designBarId=row['designBarId'],id=row['id'])
            if result['status']=='observed-trace' and len(ids)>=100:
                model,reason,ids=_fit_ifc_axis(points,normals,ids,candidate_ids,unit,units,envelope,result,executor=executor,on_submitted=prepare_next,
                                             defer_refinement=True)
                if model is None:
                    result.update(status='ifc-flexible-fit-rejected',reason=reason,points=0);results.append(result);continue
                added=ids[owner[ids]==0]
                previous=np.flatnonzero(owner==row['id'])
                released=previous[~np.isin(previous,ids)]
                intervals=model['intervals'];result['flexibleRegistration']=model['parameters'];result['points']=len(added)
                result.update(retainedPoints=int(len(ids)-len(added)),releasedPoints=len(released))
                result['observedCenterlineM']=model['observedCentersM']
                # A validated axis and its source support are one revision.
                # Unioning old and new support would keep parallel surfaces of
                # an obsolete body model under the same physical identity.
                # Curves and cut faces must be fitted after body revisions.
                owner[released]=0;status[released]=2
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
                if '_fragmentRefinement' in model:refinements.append((unit,row,result,model))
            elif result['status']=='observed-trace':result['status']='insufficient-unique-source-support'
            results.append(result)
        # Extra fragments must not perturb the source seeds or fitted axis of
        # the next body. All axis fitting and coarse ownership commits finish
        # first; this phase adds only unowned rows on those unchanged models.
        for unit,row,result,model in refinements:
            added=_refine_fixed_axis_support(points,normals,owner,row['id'],unit,units,envelope,model)
            owner[added]=row['id'];status[added]=1
            intervals=model['intervals'];parameters=model['parameters']
            result['points']+=len(added);result['observedCenterlineM']=model['observedCentersM']
            row.update(pointCount=int(np.count_nonzero(owner==row['id'])),
                observedLengthM=sum(b-a for a,b in intervals),observedSupportedArcLengthM=parameters['observedSupportedArcLengthM'],
                bodyDisplayCenterlineM=model['observedCentersM'],observedSupportIntervalsM=intervals,
                axisEvidenceRangeM=[intervals[0][0],intervals[-1][1]],
                traceEvidence={k:v for k,v in result.items() if k not in ('nodes','centerlineM','flexibleRegistration','observedCenterlineM')})
    return {'version':VERSION,'objects':results,'fixtureEnvelope':envelope,
            'policy':{'searchRadiusM':.15,'endpointMarginM':.04,'stationStepM':.012,'sectionHalfWidthM':.008,
                      'maximumLinkGapM':.09,'maximumOcclusionLinkGapM':'max(0.09m,min(0.30m,25% of design length))',
                      'transversePredictionGate':'min(30mm,max(3mm,1.5*radius)+2/m*axialGap^2)',
                      'neighbourIdentityMarginM':.003,
                      'radiusRatioRange':[.72,1.45],'heldOutFraction':.5,
                      'minimumBodySpan':'max(0.20m,min(0.60m,20% of design length)); at least20 supported stations',
                      'modelPolicy':'local sections initialize IFC axis degrees of freedom; final ownership uses the fitted fixed-radius IFC sweep and validated station intervals',
                      'ownership':'validated body revision replaces its previous source support; released rows become pending; other owners remain fixed; curves and cut faces follow final bodies',
                      'tracking':'independent section clusters; local tangent association; substantial axial track anchors bidirectional growth; one mode per station; observed surface support only',
                      'existencePrior':'design presence requests a search; does not imply observation or physical presence'}}
