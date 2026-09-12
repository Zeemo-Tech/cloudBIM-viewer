"""UI Step 06: design-constrained reconciliation of observed steel.

Only Step 05's retained steel enters. Coordinates and earlier labels are immutable.
Position, span and neighboring lanes constrain ownership; positive fixture evidence
rejects extra components. Straight web runs remain separate matching units.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict, replace
import time
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from .internal_rebar import InternalRebarParameters, _fit_cylinder, _split_parallel_points, assign_cylinders
from .rebar_extension import ATTRIBUTES, ExtensionParameters, exterior_clusters, _terminal_rays
from .design_prior_refinement import PriorParameters, _candidates

VERSION = 'design-guided-instances-v9-locked-hooks-final-filter'
PROTECTION_THRESHOLD = .9
LOW_SCORE_THRESHOLD = .5


@dataclass(frozen=True)
class GuidedParameters:
    sample_limit: int = 2048
    fit_span: float = .18
    maximum_gap: float = .65
    maximum_offset: float = .007
    maximum_angle_degrees: float = 12.
    exterior_reach: float = .5
    maximum_components: int = 30000
    minimum_span: float = .025
    extra_instance_penalty: float = 3.
    fixture_distance: float = .012
    fixture_surface_distance: float = .0015


def _sample(ids, limit):
    return ids[np.linspace(0, len(ids)-1, min(len(ids), limit), dtype=int)]


def _high_confidence_steel(context):
    score = getattr(context, 'fused_steel_score', None)
    if score is None:
        return np.zeros(len(context.positions), bool)
    score = np.asarray(score)
    if score.shape != (len(context.positions),):
        raise ValueError('fused_steel_score 必须与源点逐行对应')
    return (score >= PROTECTION_THRESHOLD) & (context.refined_class == 3)


def _fit_evidence(points, normals, kind, params):
    """Fit local observed sections so a bowed bar need not fit one design line."""
    if len(points) < 12:
        return None
    fit_params = InternalRebarParameters()
    whole = _fit_cylinder(points, kind, fit_params)
    axis = whole['axis']; center = points.mean(0); t = (points-center)@axis
    span = float(np.ptp(t))
    edges = np.linspace(t.min(), t.max(), max(2, int(np.ceil(span/params.fit_span))+1))
    fits = []
    for low, high in zip(edges[:-1], edges[1:]):
        rows = (t >= low-.01) & (t <= high+.01)
        if np.count_nonzero(rows) < 12:
            continue
        m = _fit_cylinder(points[rows], kind, fit_params)
        if m is not None and abs(m['axis']@axis) > .94:
            fits.append(m)
    if not fits:
        fits = [whole]
    good = [m for m in fits if m['fitMedianErrorM'] <= .0015 and not m['radiusAtBound']]
    # Sparse transverse ribs at a track end must not extend its axial support.
    # Keep those source rows, but use density-supported round sections for links.
    core_error=max(.0006,2*float(np.median([m['fitMedianErrorM'] for m in good]))) if good else .0015
    supported = [m for m in good if m['seedCount'] >= .25*max(f['seedCount'] for f in fits) and m['fitMedianErrorM']<=core_error]
    radius = float(np.median([m['radius'] for m in (supported or fits)]))
    error = float(np.median([m['fitMedianErrorM'] for m in fits]))
    normal_rows = np.linalg.norm(normals, axis=1) > .5
    normal_agreement = float(np.mean(np.abs(normals[normal_rows]@axis) < .5)) if normal_rows.any() else None
    normal_flatness = float(np.linalg.eigvalsh(normals[normal_rows].T@normals[normal_rows])[-1]/normal_rows.sum()) if normal_rows.any() else None
    _, singular, _ = np.linalg.svd(points-center, full_matrices=False)
    linearity = float(1-singular[1]**2/max(singular[0]**2, 1e-15))
    radial_votes = []
    if normal_rows.any():
        for m in fits:
            delta=points-m['center']; along=delta@m['axis']; radial=delta-along[:,None]*m['axis']
            radius_norm=np.linalg.norm(radial,axis=1)
            local=normal_rows & (along>=m['low']) & (along<=m['high']) & (radius_norm>1e-8)
            if local.any():radial_votes.extend((np.abs(np.sum(normals[local]*radial[local],axis=1))/radius_norm[local]>.7).tolist())
    radial_agreement=float(np.mean(radial_votes)) if radial_votes else None
    strong = span >= params.minimum_span and linearity >= .7 and sum(m['seedCount'] for m in good) >= .5*sum(m['seedCount'] for m in fits)
    strong &= error <= .0015 and (normal_agreement is None or normal_agreement >= .6)
    strong &= radial_agreement is None or radial_agreement >= .70
    core = supported or fits
    histogram,bins=np.histogram(t,bins=max(1,int(np.ceil(span/.03))))
    dense=np.flatnonzero(histogram>=max(3,.25*np.quantile(histogram[histogram>0],.85)))
    core_low,core_high=(bins[dense[0]],bins[dense[-1]+1]) if len(dense) else (t.min(),t.max())
    terminals=[]
    for target in (core_low,core_high):
        m=min(core,key=lambda m:abs((m['center']-center)@axis-target))
        along=(target-(m['center']-center)@axis)/(m['axis']@axis)
        along=np.clip(along,m['low'],m['high'])
        terminals.append((m['center']+along*m['axis'],m['axis']))
    start, end = terminals[0][0], terminals[-1][0]
    return {'center': (start+end)/2, 'axis': axis, 'start': start, 'end': end,
            'length': float((end-start)@axis), 'radius': radius, 'surfaceError': error,
            'linearity': linearity, 'normalAgreement': normal_agreement,
            'radialNormalAgreement': radial_agreement, 'normalFlatness': normal_flatness, 'crossSectionThickness': float(singular[-1]/np.sqrt(len(points))), 'strong': bool(strong),
            'fits': fits, 'coreFits': core, 'terminals': [terminals[0], terminals[-1]],
            'radiusAtBound': bool(all(m['radiusAtBound'] for m in fits))}


def _candidate_sets(summaries, inventory, mode):
    units = inventory['units']
    if not units:
        return [[] for _ in summaries], [None for _ in summaries]
    centers = np.array([(np.asarray(u['startM'])+u['endM'])/2 for u in units])
    axes = np.array([u['direction'] for u in units]); lengths = np.array([u['lengthM'] for u in units])
    prior_params = PriorParameters()
    tree = cKDTree(centers)
    rows = [_candidates(s, units, centers, axes, lengths, tree, prior_params) for s in summaries]
    # Estimate each lane from long round observations. Parallel neighbors supply
    # a local displacement prior, so registration error does not dominate links.
    anchors={}
    for i,(s,row) in enumerate(zip(summaries,rows)):
        if not s['strong'] or s['length']<.18 or not row or row[0][0]>.8:continue
        if len(row)>1 and row[1][0]-row[0][0]<.08:continue
        u=row[0][1]
        if u not in anchors or s['length']>summaries[anchors[u]]['length']:anchors[u]=i
    if mode=='topology':
        for i,row in enumerate(rows):
            scored=[]
            for cost,u in row:
                axis=axes[u];shifts=[];weights=[]
                for v,j in anchors.items():
                    if j==i or abs(axes[v]@axis)<.98 or units[u]['kind']!=units[v]['kind']:continue
                    separation=centers[v]-centers[u];separation-=axis*(separation@axis)
                    if np.linalg.norm(separation)>.45:continue
                    delta=summaries[j]['center']-centers[v];delta-=axis*(delta@axis)
                    shifts.append(delta);weights.append(1/(.04+np.linalg.norm(separation)))
                if shifts:
                    expected=np.average(shifts,axis=0,weights=weights)
                    delta=summaries[i]['center']-centers[u];delta-=axis*(delta@axis)
                    scale=.15 if units[u]['kind']=='short' else .025
                    cost=.65*cost+.35*min(8.,np.linalg.norm(delta-expected)**2/(2*scale**2))
                scored.append((cost,u))
            rows[i]=sorted(scored)
    choices=[row[0][1] if row and row[0][0]<=2. and (len(row)==1 or row[1][0]-row[0][0]>=.12) else None for row in rows]
    return rows,choices


def _shared_unit(first, second, rows, units):
    # Ambiguous lane matches may share a candidate, but geometry must then decide.
    left = {u: cost for cost, u in rows[first] if cost <= min(3., rows[first][0][0]+.8)} if rows[first] else {}
    right = {u: cost for cost, u in rows[second] if cost <= min(3., rows[second][0][0]+.8)} if rows[second] else {}
    shared = left.keys() & right.keys()
    return min(shared, key=lambda u: (left[u]+right[u], units[u]['designUnitId'])) if shared else None


def _join_geometry(a, b, unit, params):
    if not a['strong'] or not b['strong']:
        return None
    if a.get('webRole') != b.get('webRole'):
        return None
    cosine = np.cos(np.deg2rad(params.maximum_angle_degrees))
    if abs(a['axis']@b['axis']) < cosine or abs(a['radius']-b['radius']) > .002:
        return None
    stable = max((a,b), key=lambda s: s['length'])
    axis = stable['axis']; origin = stable['start']
    intervals = [sorted(float((p-origin)@axis) for p in (s['start'],s['end'])) for s in (a,b)]
    overlap = min(i[1] for i in intervals)-max(i[0] for i in intervals)
    if -overlap > min(params.maximum_gap, unit['lengthM']*.8):
        return None
    if max(i[1] for i in intervals)-min(i[0] for i in intervals) > unit['lengthM']+max(.12,.12*unit['lengthM']):
        return None
    if overlap>.008:
        # Overlapping SURFACE fragments can belong to one cylinder. Verify their
        # locally fitted centers, not PCA centroids of partial visible arcs.
        low=max(t[0] for t in intervals)+origin@axis
        high=min(t[1] for t in intervals)+origin@axis
        errors=[]
        for t in np.linspace(low,high,5):
            centers=[]
            for s in (a,b):
                m=min(s['coreFits'],key=lambda m:abs(m['center']@axis-t))
                centers.append(m['center']+m['axis']*((t-m['center']@axis)/(m['axis']@axis)))
            errors.append(np.linalg.norm(centers[1]-centers[0]))
        tolerance=.003
        if overlap<.1*min(a['length'],b['length']):
            # A short transition may contain transverse ribs in the fitted
            # section. Bound the allowance by its measured fit uncertainty.
            tolerance=min(.005,.003+2*max(m['fitMedianErrorM'] for s in (a,b) for m in s['coreFits']))
        if np.quantile(errors,.8)>tolerance: return None
        return {'gapM':0.,'offsetM':float(np.median(errors)),'overlapM':float(overlap)}
    pairs = [(np.linalg.norm(p-q), p, u, q, v) for p,u in a['terminals'] for q,v in b['terminals']]
    gap, p, u, q, v = min(pairs, key=lambda pair: pair[0])
    if abs(u@v) < cosine:
        return None
    if u@v < 0: v = -v
    delta = q-p
    # Both local tangents must predict the same gap midpoint. This tolerates a
    # gradual bend without allowing a transitive hop into the neighboring lane.
    midpoint_error = np.linalg.norm((p+u*(delta@u)*.5)-(q-v*(delta@v)*.5))
    if midpoint_error > params.maximum_offset:
        return None
    return {'gapM': float(gap), 'offsetM': float(midpoint_error), 'overlapM': float(max(0.,overlap))}


def _group_compatible(group_a, group_b, summaries, unit, params):
    # Test all members, not just the latest union edge: no chain across lanes or
    # across repeated same-direction diagonals of a continuous web parent.
    items = [summaries[i] for i in group_a+group_b]
    axis = max(items, key=lambda s:s['length'])['axis']
    ends = [float(p@axis) for s in items for p in (s['start'],s['end'])]
    if max(ends)-min(ends) > unit['lengthM']+max(.12,.12*unit['lengthM']):
        return False
    for i in group_a:
        for j in group_b:
            a,b = summaries[i],summaries[j]
            aa=sorted(float(p@axis) for p in (a['start'],a['end']))
            bb=sorted(float(p@axis) for p in (b['start'],b['end']))
            if min(aa[1],bb[1])-max(aa[0],bb[0]) > .008 and _join_geometry(a,b,unit,params) is None:
                return False
    return True


def _record_model(model, segment_id, instance_id, point_count=0):
    return {'id': segment_id, 'instanceId': instance_id, 'type': int(model['type']),
            'startM': (model['center']+model['low']*model['axis']).tolist(),
            'endM': (model['center']+model['high']*model['axis']).tolist(),
            'radiusM': float(model['radius']), 'fitMedianErrorM': float(model['fitMedianErrorM']),
            'pointCount': point_count}


def _fixture_evidence(points, normals, fixture_tree, fixture_normals, params):
    if fixture_tree is None:
        return {'fixtureNearFraction':0.,'fixtureSurfaceFraction':0.}
    distance,neighbor=fixture_tree.query(points,k=min(12,fixture_tree.n),distance_upper_bound=params.fixture_distance)
    if distance.ndim==1:distance,neighbor=distance[:,None],neighbor[:,None]
    valid=np.isfinite(distance);neighbor=np.minimum(neighbor,fixture_tree.n-1)
    delta=fixture_tree.data[neighbor]-points[:,None]
    other=fixture_normals[neighbor]
    same=valid & (np.abs(np.sum(normals[:,None]*other,axis=2))>.90)
    same &= np.abs(np.sum(delta*normals[:,None],axis=2))<params.fixture_surface_distance
    same &= np.abs(np.sum(delta*other,axis=2))<params.fixture_surface_distance
    return {'fixtureNearFraction':float(np.mean(valid.any(axis=1))),
            'fixtureSurfaceFraction':float(np.mean(same.sum(axis=1)>=3))}


def _assign_units(members, atoms, ranked, units, params):
    """One observed group per design run; missing runs use no synthetic points.

    A dummy column charges an explicit extra-instance penalty. Long round support
    wins a contested slot over a tiny fixture remnant; genuine displaced rods may
    still remain observed exceptions when every plausible slot is occupied.
    """
    groups=[g for g in members.values() if any(atoms[i]['instanceId'] for i in g)]
    if not groups:return {},set()
    # Design rows keep memory O(units * groups), not O(groups squared).
    costs=np.full((len(units),len(groups)+len(units)),1.e6)
    costs[:,len(groups):]=0.
    for row,group in enumerate(groups):
        weight=np.array([max(.02,atoms[i]['summary']['length']) for i in group])
        candidates=set(u for i in group for cost,u in ranked[i] if cost<=params.extra_instance_penalty)
        for u in candidates:
            scores=[dict((unit,cost) for cost,unit in ranked[i]).get(u,8.) for i in group]
            cost=float(np.average(scores,weights=weight))
            if cost<=params.extra_instance_penalty:costs[u,row]=cost
        strength=sum(atoms[i]['summary']['length'] for i in group if atoms[i]['summary']['strong'])
        costs[:,row]-=params.extra_instance_penalty+min(3.,strength*2)
    row_ids,columns=linear_sum_assignment(costs)
    choices={i:None for group in groups for i in group};extras=set(choices)
    for unit,column in zip(row_ids,columns):
        if column>=len(groups):continue
        for i in groups[column]:choices[i]=int(unit);extras.discard(i)
    return choices,extras


def _exterior_local_tangents(points, workers):
    """Local branch directions on equal-weight voxels, not scan-density votes."""
    _,inverse=np.unique(np.floor((points-points.min(0))/.0015).astype(np.int64),axis=0,return_inverse=True)
    counts=np.bincount(inverse)
    centers=np.column_stack([np.bincount(inverse,weights=points[:,i])/counts for i in range(3)])
    directions=np.zeros_like(centers);reliable=np.zeros(len(centers),bool)
    if len(centers)<5:return directions[inverse],reliable[inverse]
    tree=cKDTree(centers);k=min(96,len(centers))
    for start in range(0,len(centers),2048):
        stop=min(start+2048,len(centers))
        distance,index=tree.query(centers[start:stop],k=k,distance_upper_bound=.012,workers=workers)
        valid=np.isfinite(distance);neighbors=centers[np.minimum(index,len(centers)-1)]
        n=np.maximum(valid.sum(1),1)
        mean=np.sum(neighbors*valid[...,None],axis=1)/n[:,None]
        delta=(neighbors-mean[:,None,:])*valid[...,None]
        covariance=np.einsum('nki,nkj->nij',delta,delta)/n[:,None,None]
        values,vectors=np.linalg.eigh(covariance)
        directions[start:stop]=vectors[:,:,-1]
        reliable[start:stop]=(n>=5)&(values[:,-1]>2.5*values[:,-2])&(values[:,-1]>.002**2)
    return directions[inverse],reliable[inverse]


def _separate_transverse_fixture(points, normals, labels, search, fixture_tree,
                                 fixture_normals, params, workers):
    """Cut a T/L junction by local direction before a cluster-wide decision.

    Only a measured, transverse branch with positive fixture evidence triggers
    the split. A normal short tip or hook retains the existing whole-body path.
    """
    blocked=np.zeros(len(points),bool);mixed=False
    if len(points)<24 or not np.any(labels):return labels,blocked,mixed
    tangents,reliable=_exterior_local_tangents(points,workers)
    for code in np.unique(labels[labels>0]):
        m=search[code-1];cosine=np.abs(tangents@m['axis'])
        transverse=reliable&(cosine<.5)
        indices=np.flatnonzero(transverse)
        if len(indices)<12:continue
        sample=_sample(indices,params.sample_limit)
        delta=points[sample]-m['center'];radial=delta-(delta@m['axis'])[:,None]*m['axis']
        if np.linalg.norm(np.ptp(radial,axis=0))<.020:continue
        fixture=_fixture_evidence(points[sample],normals[sample],fixture_tree,fixture_normals,params)
        if fixture['fixtureNearFraction']<.45 and fixture['fixtureSurfaceFraction']<.3:continue
        kind=3 if np.ptp(points[sample,2])>np.ptp(points[sample,:2],axis=0).max()*.25 else 1
        rounded=_fit_evidence(points[sample],normals[sample],kind,params)
        if rounded and rounded['strong'] and rounded['length']<max(.08,20*m['radius']) and (rounded['normalFlatness'] or 0)<.85 and abs(rounded['radius']-m['radius'])<max(.001,.35*m['radius']):
            # A real hook also turns across its stem and can touch a clamp.
            # Its observed round section takes precedence over proximity.
            continue
        mixed=True;blocked|=transverse
        labels[(labels==code)&transverse]=0
        own=labels==code;aligned=own&reliable&(cosine>.85)
        # A transverse edge merely crossing the search cylinder is not a rod.
        if np.count_nonzero(aligned)<12 or np.ptp(points[aligned]@m['axis'])<.012:
            labels[own]=0
    blocked &= labels==0
    return labels,blocked,mixed


def _early_exterior_support(context, jobs, original, out, segments, next_segment,
                            fixture_tree, fixture_normals, params, workers):
    """Give original interior tracks first refusal, before any exterior deletion.

    Freeze the seed set: newly claimed points never seed this pass. An exterior
    sliver need not fit its own cylinder, but must lie on a measured terminal
    cylinder, with compatible normals and no equally plausible other owner.
    """
    evidence_cache={};models=[]
    for old_id,rows,_ in jobs:
        if not old_id or len(rows)<12:continue
        sid=_sample(rows,params.sample_limit);kind=int(original[old_id]['type'])
        e=_fit_evidence(context.positions[sid],context.normals[sid],kind,params)
        evidence_cache[old_id]=e
        if e is None or not e['strong'] or e['length']<.12 or np.mean(context.refined_zone[rows]==1)<.8:continue
        fixture=_fixture_evidence(context.positions[sid],context.normals[sid],fixture_tree,fixture_normals,params)
        if fixture['fixtureNearFraction']>=.2:continue
        # A potentially mixed original instance must be separated before it can
        # tell exterior points which physical rod they belong to.
        if len(_split_parallel_points(context.positions[sid],kind,InternalRebarParameters(),depth=1))>1:continue
        models.extend({**m,'instanceId':old_id} for m in e['coreFits'])
    protected=np.zeros(len(context.positions),bool);blocked=protected.copy();operations=[]
    if not models:return evidence_cache,protected,blocked,next_segment,operations
    # Only terminal parts can reach beyond the observed interior. Materialize
    # rays before extending bounds, so iteration never sees a modified endpoint.
    rays=list(_terminal_rays(models));search=[]
    for _,m,sign,_ in rays:
        extended={**m}
        if sign<0:extended['high']=m['low']+.005;extended['low']=m['low']-params.exterior_reach
        else:extended['low']=m['high']-.005;extended['high']=m['high']+params.exterior_reach
        search.append(extended)
    candidates=np.flatnonzero((out['complete_class']==3)&(out['complete_instance']==0)&(context.refined_zone!=1))
    if not len(candidates):return evidence_cache,protected,blocked,next_segment,operations
    assigned,_,cache=assign_cylinders(context.positions[candidates],context.normals[candidates],search,
        workers=workers,params=replace(InternalRebarParameters(),assignment_tolerance=.0025,endpoint_margin=.005),instance_margin=.0005)
    geometric=assigned.copy()
    owner_ids=np.array([0]+[m['instanceId'] for m in search],np.uint32)
    model_index=np.maximum(assigned.astype(np.int64)-1,0)
    centers=np.array([m['center'] for m in search]);axes=np.array([m['axis'] for m in search])
    delta=context.positions[candidates]-centers[model_index];along=np.sum(delta*axes[model_index],axis=1)
    radial=delta-along[:,None]*axes[model_index];normals=context.normals[candidates]
    normal_ok=(np.abs(np.sum(normals*axes[model_index],axis=1))<=.65)
    normal_ok &= np.abs(np.sum(normals*radial,axis=1))/np.maximum(np.linalg.norm(radial,axis=1),1.e-9)>=.45
    assigned[~normal_ok]=0
    for old_id,rows,cluster in jobs:
        if old_id:continue
        rows=rows[context.refined_zone[rows]!=1]
        if not len(rows):continue
        local=np.searchsorted(candidates,rows);labels=assigned[local].copy()
        points=context.positions[rows];center=points.mean(0)
        labels,transverse,mixed=_separate_transverse_fixture(points,context.normals[rows],labels,
            search,fixture_tree,fixture_normals,params,workers)
        blocked[rows[transverse]]=True
        if mixed:
            operations.append({'action':'separate','phase':'before_filter','clusterId':cluster,
                'pointCount':len(rows),'transversePointCount':int(transverse.sum()),
                'reason':'local_axial_branch_and_transverse_fixture'})
        _,singular,basis=np.linalg.svd(points-center,full_matrices=False)
        directional=len(points)>=12 and np.ptp((points-center)@basis[0])>=.015 and singular[1]<.55*singular[0]
        if directional and not mixed:
            for code in np.unique(labels[labels>0]):
                if abs(basis[0]@search[code-1]['axis'])<.9:labels[labels==code]=0
        owners=owner_ids[labels];positive=owners>0
        # Ties remain candidate steel for later review; their presence cannot
        # justify accepting a whole glued cluster as one rod.
        protected[rows[cache['ambiguous'][local]]]=True
        winners,numbers=np.unique(owners[positive],return_counts=True)
        if not len(winners):continue
        geometric_owners=owner_ids[geometric[local]]
        # Retain a connected tip/hook as a body when a majority of its measured
        # surface supports one interior rod. Short tips can have poor normals
        # near the clamp; a corroborating patch still needs valid normals.
        whole=(not mixed and len(winners)==1 and int(numbers[0])>=12 and
            np.count_nonzero(geometric_owners==winners[0])>=.55*len(rows) and
            not np.any((geometric_owners>0)&(geometric_owners!=winners[0])) and
            not cache['ambiguous'][local].any())
        take=np.ones(len(rows),bool) if whole else positive
        if np.count_nonzero(take)<12:continue
        if whole:
            code=int(np.bincount(labels)[1:].argmax())+1
            labels[labels==0]=code;owners[:]=winners[0]
        selected=rows[take];chosen=labels[take]
        out['complete_instance'][selected]=owners[take];out['complete_confidence'][selected]=.55
        protected[selected]=True
        for code in np.unique(chosen):
            claimed=selected[chosen==code];m=search[code-1]
            t=(context.positions[claimed]-m['center'])@m['axis']
            observed={**m,'low':float(t.min()),'high':float(t.max())}
            record=_record_model(observed,next_segment,m['instanceId'],len(claimed))
            record['evidence']='interior_axis_supported_exterior_points'
            segments.append(record);out['complete_segment'][claimed]=next_segment;next_segment+=1
        operations.append({'action':'attach','phase':'before_filter','clusterId':cluster,
            'instanceIds':list(map(int,winners)),'pointCount':len(selected),
            'wholeCluster':bool(whole),'reason':'original_interior_terminal_axis_support'})
    return evidence_cache,protected,blocked,next_segment,operations


def refine_instances(context, internal_report, inventory, *, mode='topology', params=None, workers=1, output=None, progress=None):
    if mode not in ('geometry', 'topology'):
        raise ValueError('第六步需要 geometry 或 topology 设计辅助模式')
    if not inventory.get('units'):
        raise ValueError('第六步需要可解析的设计钢筋及已保存的粗配准')
    params = params or GuidedParameters(); started = time.perf_counter()
    progress = progress or (lambda *args: None)
    count = len(context.positions)
    out = output if output is not None else {k:np.zeros(count,dtype) for k,dtype in ATTRIBUTES.items()}
    for v in out.values(): v[:] = 0
    out['complete_class'][:] = context.refined_class
    protected_high = _high_confidence_steel(context)
    fused_scores = getattr(context, 'fused_steel_score', None)
    low_score = (np.zeros(count, bool) if fused_scores is None else
                 (np.asarray(fused_scores) <= LOW_SCORE_THRESHOLD) & (context.refined_class == 3))
    blocked_noise_rows = np.zeros(count, bool)
    internal_noise = context.internal_type == 5
    spatial_override = (internal_report.get('denoising', {}).get('highScoreOverrideAllowed') is True
                        or internal_report.get('denoising', {}).get('designBoundaryAppliesToAllSteel') is True)
    if spatial_override:
        # Step 05 has already weighed the fusion score against independent
        # spatial evidence. The same old score cannot undo that decision here.
        protected_high &= ~internal_noise
    blocked_noise_rows |= internal_noise & protected_high
    out['complete_class'][internal_noise & ~protected_high] = 4
    steel = out['complete_class'] == 3
    out['complete_instance'][steel] = context.internal_instance[steel]
    out['complete_segment'][steel] = context.internal_segment[steel]
    out['complete_confidence'][steel] = context.internal_confidence[steel]
    segments = deepcopy(internal_report['segments'])
    original = {int(i['id']):i for i in internal_report['instances']}
    before = int(len(np.unique(out['complete_instance'][out['complete_instance']>0])))
    units = inventory['units']; operations = []; rejected = 0
    next_id = max(original, default=0)+1
    next_segment = max([s['id'] for s in segments],default=0)+1
    from .rebar_hook_clusters import freeze_hook_clusters, merge_hook_clusters, verify_hook_clusters
    from .rebar_cluster_quality import final_fragment_filter, design_cluster_quality
    from .rebar_final_filter import filter_final_clusters
    progress('第 6 步：识别并锁定弯曲外筋整簇（只能合并）', 0, count)
    hook_groups, hook_protected, hook_report = freeze_hook_clusters(context, out, inventory, segments)
    deferred_noise = np.zeros(count, bool)
    # Cluster only still-retained, unassigned steel. No candidate discarded by
    # Step 05 is reintroduced, even if it lies exactly on an IFC centerline.
    loose = np.flatnonzero(steel & (out['complete_instance']==0) & ~hook_protected)
    clusters = exterior_clusters(context.positions[loose], ExtensionParameters())
    out['complete_cluster'][loose] = clusters
    total_clusters = int(clusters.max()) if len(clusters) else 0
    if total_clusters > params.maximum_components:
        raise ValueError('第六步候选簇超过资源预算')
    order = np.argsort(clusters,kind='stable'); counts = np.bincount(clusters,minlength=total_clusters+1)
    offsets = np.r_[0,np.cumsum(counts[1:])]; grouped = loose[order]
    cluster_records = [{'id':i,'pointCount':int(counts[i])} for i in range(1,total_clusters+1)]
    for group in hook_groups:
        record = dict(id=len(cluster_records)+1, pointCount=len(group['rows']),
            category='curved-exterior', locked=True, allowedOperations=['merge'],
            designUnitId=group['designUnitId'], designBarId=group['designBarId'],
            boundsM=group['region']['boundsM'], status='protected-pending')
        cluster_records.append(record); group['record'] = record
        group['region']['clusterId'] = record['id']
        out['complete_cluster'][group['rows']] = record['id']
    jobs = []
    owners = out['complete_instance']; owned = np.flatnonzero(owners>0)
    ordered = owned[np.argsort(owners[owned],kind='stable')]
    values, starts = np.unique(owners[ordered],return_index=True)
    for idx,i in enumerate(values):
        ids=ordered[starts[idx]:starts[idx+1] if idx+1<len(starts) else len(ordered)]
        jobs.append((int(i), ids, 0))
    for i in range(total_clusters): jobs.append((0,grouped[offsets[i]:offsets[i+1]],i+1))
    progress('第 6 步：内外钢筋实测几何与近邻分离',0,len(jobs))
    atoms=[]
    fixture_rows = np.flatnonzero(context.refined_class==2)
    fixture_sample=fixture_rows[::max(1,len(fixture_rows)//300000)]
    fixture_tree = cKDTree(context.positions[fixture_sample]) if len(fixture_rows) else None
    fixture_normals=context.normals[fixture_sample]
    progress('第 6 步：内部轴向优先接续外露点',0,len(loose))
    evidence_cache,early_protected,transverse_blocked,next_segment,early_operations=_early_exterior_support(
        context,jobs,original,out,segments,next_segment,fixture_tree,fixture_normals,params,workers)
    operations.extend(early_operations)
    early_rows=np.flatnonzero(early_protected & (out['complete_instance']>0))
    # The initial frozen cluster IDs remain intact. Claimed exterior rows are
    # already part of an interior instance and never compete as extra rods.
    mixed_clusters={o['clusterId'] for o in early_operations if o['action']=='separate'}
    split_jobs=[]
    for old_id,rows,cluster in jobs:
        rows=rows[~(hook_protected[rows] if old_id else early_protected[rows])]
        if cluster in mixed_clusters and len(rows):
            # Once the axial branch is peeled off, disconnected edge remnants
            # must be fitted and filtered independently. Keep source cluster IDs
            # for audit/UI provenance; they no longer define processing units.
            parts=exterior_clusters(context.positions[rows],ExtensionParameters())
            for part in np.unique(parts):split_jobs.append((0,rows[parts==part],cluster))
            for op in early_operations:
                if op['action']=='separate' and op['clusterId']==cluster:
                    op['residualPartCount']=int(len(np.unique(parts)))
        else:split_jobs.append((old_id,rows,cluster))
    jobs=split_jobs
    for index,(old_id,ids,cluster) in enumerate(jobs):
        if len(ids)<12:
            continue
        sample=_sample(ids,params.sample_limit); xyz=context.positions[sample]; normals=context.normals[sample]
        kind=int(original.get(old_id,{}).get('type',3 if np.ptp(xyz[:,2])>np.ptp(xyz[:,:2],axis=0).max()*.25 else 1))
        evidence=evidence_cache.get(old_id) if old_id else _fit_evidence(xyz,normals,kind,params)
        if evidence is None: continue
        # Validate a possible split from measured cross sections; the design can
        # suggest two lanes, but cannot turn the two visible sides of one rod
        # into two rods. Both explanations must persist along the same span.
        parts = _split_parallel_points(xyz,kind,InternalRebarParameters(),depth=1)
        fits = [_fit_cylinder(p,kind,InternalRebarParameters()) for p in parts]
        split = len(parts)==2 and all(m and not m['radiusAtBound'] for m in fits)
        if split:
            sample_tree=cKDTree(xyz)
            summaries=[_fit_evidence(p,normals[sample_tree.query(p)[1]],kind,params) for p in parts]
            for s in summaries: s.update(family=None,webRole=kind==3)
            ranks,_=_candidate_sets(summaries,inventory,mode)
            # Require plausible distinct design lanes, independent of nearest
            # distance and without inventing a second observed support set.
            split=all(s['strong'] for s in summaries) and all(ranks) and any(u!=v and cu<=2. and cv<=2. for cu,u in ranks[0][:3] for cv,v in ranks[1][:3])
            axis=evidence['axis']; projections=[p@axis for p in parts]
            low=max(float(t.min()) for t in projections); high=min(float(t.max()) for t in projections)
            bins=np.linspace(low,high,max(3,int(np.ceil((high-low)/params.fit_span)))+1)
            verified_sections=0
            for lo,hi in zip(bins[:-1],bins[1:]):
                local=[p[(t>=lo)&(t<=hi)] for p,t in zip(parts,projections)]
                fits_local=[_fit_cylinder(p,kind,InternalRebarParameters()) for p in local]
                if any(m is None or m['radiusAtBound'] or m['fitMedianErrorM']>.0009 for m in fits_local):continue
                a,b=fits_local;delta=b['center']-a['center']
                separation=np.linalg.norm(delta-a['axis']*(delta@a['axis']))
                if abs(a['axis']@b['axis'])>.985 and separation>=max(.003,.75*(a['radius']+b['radius'])):
                    verified_sections+=1
            # A bent cylinder's upper/lower arcs can look like two global fits.
            # Its local fitted centers coincide, so it fails this repeated test.
            split=split and verified_sections>=max(3,int(np.ceil(.7*(len(bins)-1))))
        if split:
            full=context.positions[ids]
            errors=[]
            for m in fits:
                delta=full-m['center']; along=delta@m['axis']
                errors.append(np.abs(np.linalg.norm(delta-along[:,None]*m['axis'],axis=1)-m['radius']))
            labels=np.argmin(errors,axis=0)
            if min(np.bincount(labels,minlength=2))<12: split=False
        groups=[ids[labels==i] for i in (0,1)] if split else [ids]
        split_ids=[]
        for group_index,rows in enumerate(groups):
            sid=_sample(rows,params.sample_limit)
            e=_fit_evidence(context.positions[sid],context.normals[sid],kind,params) if split else evidence
            if e is None: continue
            family=original.get(old_id,{}).get('family')
            e.update(family=family,webRole=kind==3,pointCount=len(rows))
            e.update(_fixture_evidence(context.positions[sid],context.normals[sid],fixture_tree,fixture_normals,params))
            e['interiorFraction']=float(np.mean(context.refined_zone[rows]==1))
            # A planar remnant needs *both* fixture proximity and failed round
            # section evidence. Partial visible arcs and displaced rods survive.
            planar=e['normalFlatness'] is not None and e['normalFlatness']>.90
            fixture_plane=e['fixtureSurfaceFraction']>.65 or (e['normalFlatness'] is not None and e['normalFlatness']>.985 and e['fixtureNearFraction']>.7)
            noise=planar and fixture_plane and (e['radiusAtBound'] or
                (e['radialNormalAgreement'] is not None and e['radialNormalAgreement']<.7) or e['crossSectionThickness']<.00025)
            # Weak classifier support lowers the fixture-evidence threshold,
            # but failed round geometry is still required; low score alone
            # never rejects a measured round bar or a supported partial arc.
            suspect = (not e['strong'] and planar and e['fixtureNearFraction'] > .3 and
                       (e['radiusAtBound'] or e['crossSectionThickness'] < .00025))
            candidate = (out['complete_class'][rows] == 3) & (noise | (suspect & low_score[rows]))
            # Evidence may disqualify an axis as a growth seed, but semantics are
            # decided only after every inner/outer connection has been attempted.
            deferred_noise[rows[candidate]] = True
            instance_id=old_id
            if split or (old_id==0 and e['strong']):
                instance_id=old_id if split and group_index==0 and old_id else next_id
                if instance_id==next_id: next_id+=1
                out['complete_instance'][rows]=instance_id
                # Attach each row to an observed local section, no gap bridging
                # line is emitted as a fitted cylinder.
                centers=np.array([m['center'] for m in e['fits']]); tree=cKDTree(centers)
                _,part=tree.query(context.positions[rows],workers=workers)
                for f,m in enumerate(e['fits']):
                    selected=rows[part==f]
                    if not len(selected):continue
                    segments.append(_record_model(m,next_segment,instance_id,len(selected)))
                    out['complete_segment'][selected]=next_segment;next_segment+=1
                out['complete_confidence'][rows]=max(.1,min(.9,1-e['surfaceError']/.003))
                if split:split_ids.append(instance_id)
                else:operations.append({'action':'instantiate','instanceId':instance_id,'clusterId':cluster,'pointCount':len(rows),'reason':'observed_round_sections'})
            atoms.append({'id':len(atoms),'instanceId':instance_id,'originalInstanceId':old_id,'clusterId':cluster,'rows':rows,'summary':e})
        if split and len(split_ids)==2:
            operations.append({'action':'split','sourceInstanceId':old_id,'clusterId':cluster,'instanceIds':split_ids,
                'reason':'two_distinct_overlapping_observed_cylinders_with_design_lanes'})
        if index%64==0:progress('第 6 步：内外钢筋实测几何与近邻分离',index,len(jobs))
    summaries=[a['summary'] for a in atoms]
    ranked,choices=_candidate_sets(summaries,inventory,mode)
    progress('第 6 步：设计单元约束下的断片合并',0,len(atoms))
    parents=list(range(len(atoms)));members={i:[i] for i in parents}
    def leader(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    # Candidate buckets avoid O(observed instances squared) for large models.
    buckets={}
    for i,row in enumerate(ranked):
        if atoms[i]['instanceId']:
            for cost,u in row:
                if cost<=min(3.,row[0][0]+.8):buckets.setdefault(u,[]).append(i)
    pairs=set()
    for indices in buckets.values():
        if len(indices)>256:continue
        for pos,i in enumerate(indices):
            for j in indices[pos+1:]:pairs.add((min(i,j),max(i,j)))
    edges=[]
    for i,j in sorted(pairs):
        u=_shared_unit(i,j,ranked,units)
        if u is None:continue
        geometry=_join_geometry(summaries[i],summaries[j],units[u],params)
        if geometry is not None:edges.append((geometry['offsetM'],geometry['gapM'],i,j,u,geometry))
    for _,_,i,j,u,geometry in sorted(edges):
        a,b=leader(i),leader(j)
        if a==b:continue
        # An early pair of tiny fragments must not lock a nearby alternative
        # lane before the long observed track arrives. All members must admit
        # the current unit; the global assignment resolves the remaining choice.
        if any(not any(v==u and cost<=min(3.,ranked[k][0][0]+.8) for cost,v in ranked[k]) for k in members[a]+members[b]):continue
        if not _group_compatible(members[a],members[b],summaries,units[u],params):continue
        parents[b]=a;members[a]+=members.pop(b)
        operations.append({'action':'merge','sourceInstanceIds':[atoms[i]['instanceId'],atoms[j]['instanceId']],
            'designUnitId':units[u]['designUnitId'],
            'reason':'same_design_unit_and_local_cylinder_continuity',
            'acrossFixture':bool((summaries[i]['interiorFraction']>.5)!=(summaries[j]['interiorFraction']>.5)),**geometry})
    assigned_units,extra_atoms=_assign_units(members,atoms,ranked,units,params)
    for i,u in assigned_units.items():choices[i]=u
    remap={}
    for root,group in members.items():
        ids=[atoms[i]['instanceId'] for i in group if atoms[i]['instanceId']]
        if not ids:continue
        target=min(ids)
        for i in group:
            old=atoms[i]['instanceId']
            if old:
                remap[old]=target;out['complete_instance'][atoms[i]['rows']]=target;atoms[i]['instanceId']=target
    for part in segments:part['instanceId']=remap.get(part['instanceId'],part['instanceId'])
    if len(early_rows):
        mapping=np.arange(max(original,default=0)+1,dtype=np.uint32)
        for old,target in remap.items():
            if old<len(mapping):mapping[old]=target
        out['complete_instance'][early_rows]=mapping[out['complete_instance'][early_rows]]
    # Fixture-like groups cannot seed growth, but remain available until the final
    # filter so fragments are never deleted before the inner/outer merge finishes.
    growth_blocked_owners = {a['instanceId'] for a in atoms if a['instanceId'] and np.all(deferred_noise[a['rows']])}
    growth_blocked_owners -= {a['instanceId'] for a in atoms if a['instanceId'] and not np.any(deferred_noise[a['rows']])}
    for group in members.values():
        es=[summaries[i] for i in group];weights=[len(atoms[i]['rows']) for i in group]
        anchor=any(e['strong'] and e['length']>.12 and e['fixtureNearFraction']<.2 for e in es)
        near=float(np.average([e['fixtureNearFraction'] for e in es],weights=weights))
        plane=float(np.average([e['fixtureSurfaceFraction'] for e in es],weights=weights))
        radius=float(np.median([e['radius'] for e in es]));flat=float(np.average([e['normalFlatness'] or 0 for e in es],weights=weights))
        surplus=all(i in extra_atoms for i in group)
        fixture_edge=not anchor and near>.6 and ((plane>.22 and (radius>.005 or flat>.85)) or (surplus and flat>.8))
        suspect_edge=not anchor and near>.35 and ((plane>.15 and (radius>.005 or flat>.85)) or (surplus and flat>.8))
        if not (fixture_edge or suspect_edge):continue
        for i in group:
            a=atoms[i];rows=a['rows']
            candidate=(out['complete_class'][rows] == 3) & (fixture_edge | (suspect_edge & low_score[rows]))
            deferred_noise[rows[candidate]] = True
            if fixture_edge and a['instanceId']:
                growth_blocked_owners.add(a['instanceId'])
    # Weak stand-alone fits must compete for an established observed cylinder.
    # In particular, old Step 05 identities do not exempt fixture edges from QA.
    strong_owners={a['instanceId'] for i,a in enumerate(atoms) if a['summary']['strong'] and a['instanceId'] and
        (i not in extra_atoms or a['summary']['length']>=.12)}
    for i,a in enumerate(atoms):
        if a['instanceId'] and a['instanceId'] not in strong_owners:
            operations.append({'action':'release','sourceInstanceId':a['originalInstanceId'],
                'clusterId':a['clusterId'],'pointCount':len(a['rows']),'reason':'weak_round_evidence_requires_observed_owner'})
            for name in ('complete_instance','complete_segment','complete_confidence'):out[name][a['rows']]=0
            a['instanceId']=0;choices[i]=None
    # Observed terminal cylinders supply exterior ownership across the fixture
    # frame. They may be prolonged only for point lookup, never exported as data.
    progress('第 6 步：外部钢筋有限圆柱竞争',0,len(loose))
    # A split or rejection invalidates old fit parts with no remaining owned
    # rows. They must not reclaim exterior points through the search index.
    supported_segments=set(map(int,np.unique(out['complete_segment'])))
    segments=[part for part in segments if part['id'] in supported_segments]
    models=[]
    for part in segments:
        start,end=np.array(part['startM']),np.array(part['endM']);delta=end-start;length=np.linalg.norm(delta)
        if length<1e-8:continue
        models.append({'instanceId':part['instanceId'],'center':(start+end)/2,'axis':delta/length,
            'low':-length/2,'high':length/2,'radius':part['radiusM'],'segmentId':part['id'],'type':part['type']})
    # Only axes backed by a surviving original or newly instantiated source row.
    live=set(map(int,np.unique(out['complete_instance'][out['complete_instance']>0])))
    models=[m for m in models if m['instanceId'] in live]
    search_models=[]
    for a in atoms:
        if a['instanceId'] not in live or not a['summary']['strong'] or a['instanceId'] in growth_blocked_owners:continue
        for m in a['summary']['coreFits']:
            search_models.append({**m,'instanceId':a['instanceId']})
    for index,m,sign,_ in _terminal_rays(search_models):
        search_models[index]['low' if sign<0 else 'high']+=sign*params.exterior_reach
    pending=np.flatnonzero((out['complete_class']==3)&(out['complete_instance']==0)&~hook_protected)
    attached=len(early_rows)
    if len(pending) and search_models:
        assigned,score,ownership=assign_cylinders(context.positions[pending],context.normals[pending],search_models,
            workers=workers,params=replace(InternalRebarParameters(),assignment_tolerance=.0025,endpoint_margin=.005),instance_margin=.0005)
        assigned[transverse_blocked[pending]]=0
        # Reject pointwise competition between different rods with similar
        # residuals. Whole curved hook clusters follow only exclusive support.
        model_ids=np.array([0]+[m['instanceId'] for m in search_models],np.uint32)
        claimed=model_ids[assigned]
        pending_clusters=out['complete_cluster'][pending]
        for cluster in np.unique(pending_clusters):
            local=np.flatnonzero(pending_clusters==cluster); winners,numbers=np.unique(claimed[local][claimed[local]>0],return_counts=True)
            if not len(winners):continue
            winner=int(winners[np.argmax(numbers)]);support=int(max(numbers));fraction=support/len(local)
            # If two rods explain the cluster, keep per-point competition instead
            # of dragging the entire glued cluster into the largest rod.
            exclusive=cluster>0 and cluster not in mixed_clusters and len(winners)==1 and support>=12 and fraction>=.55 and not np.any(ownership['ambiguous'][local])
            take=local if exclusive else local[claimed[local]>0]
            if not len(take):continue
            selected=pending[take]
            chosen=claimed[take].copy();chosen[chosen==0]=winner
            out['complete_instance'][selected]=chosen
            for target in np.unique(chosen):
                rows=selected[chosen==target]
                options=[m for m in models if m['instanceId']==target]
                tree=cKDTree([m['center'] for m in options]);_,nearest=tree.query(context.positions[rows],workers=workers)
                out['complete_segment'][rows]=np.array([m['segmentId'] for m in options],np.uint32)[nearest]
            out['complete_confidence'][selected]=.45
            attached+=len(selected)
            operations.append({'action':'attach','clusterId':int(cluster),'instanceIds':list(map(int,np.unique(chosen))),
                'pointCount':len(selected),'reason':'exclusive_cluster_cylinder_support' if exclusive else 'competing_observed_cylinder_surfaces'})
    associations = {}
    for i, a in enumerate(atoms):
        if a['instanceId']:
            associations.setdefault(a['instanceId'], set())
            if choices[i] is not None:
                associations[a['instanceId']].add(choices[i])
    hook_operations = merge_hook_clusters(context, out, hook_groups, associations, units, segments, hook_report, workers=workers)
    operations.extend(hook_operations)
    attached += sum(o['pointCount'] for o in hook_operations)
    progress('第 6 步：内外筋合并完成，执行最终整簇过滤', 0, count)
    safe_owners = strong_owners-growth_blocked_owners
    candidate = deferred_noise & ~hook_protected & ~np.isin(out['complete_instance'], list(safe_owners))
    blocked_noise_rows |= candidate & protected_high
    selected = np.flatnonzero(candidate & ~protected_high)
    if len(selected):
        out['complete_class'][selected] = 4
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][selected] = 0
        rejected += len(selected)
        operations.append(dict(action='filter', phase='final_after_all_merges', pointCount=len(selected),
            reason='deferred_fixture_evidence_without_successful_observed_connection'))
    # Positive fixture evidence plus failure to join a measured rod is now a
    # rejection, not an indefinitely retained "steel, pending" classification.
    for i,a in enumerate(atoms):
        e=a['summary'];rows=a['rows'];extra=i in extra_atoms
        fixture=e['fixtureNearFraction']>.45 or e['fixtureSurfaceFraction']>.3
        suspect_fixture=e['fixtureNearFraction']>.25 or e['fixtureSurfaceFraction']>.15
        if not ((fixture or suspect_fixture) and (not e['strong'] or extra)):continue
        candidate=(~hook_protected[rows] & (out['complete_instance'][rows]==0)&(out['complete_class'][rows]==3) &
                   (fixture | (suspect_fixture & low_score[rows])))
        blocked=rows[candidate & protected_high[rows]]
        blocked_noise_rows[blocked]=True
        selected=rows[candidate & ~protected_high[rows]]
        if not len(selected):continue
        out['complete_class'][selected]=4
        for name in ('complete_instance','complete_segment','complete_confidence'):out[name][selected]=0
        rejected+=len(selected)
        operations.append({'action':'filter','sourceInstanceId':a['originalInstanceId'],'clusterId':a['clusterId'],
            'pointCount':len(selected),'protectedPointCount':len(blocked),
            'reason':'fixture_residual_without_observed_cylinder_support',
            'fixtureNearFraction':e['fixtureNearFraction'],'fixtureSurfaceFraction':e['fixtureSurfaceFraction'],
            'extraInstancePenalty':params.extra_instance_penalty if extra else 0.})
        if not np.any(out['complete_instance'][rows]):a['instanceId']=0;choices[i]=None
    # Step 05 has already checked inner support. Here check only still-loose
    # non-high-score exterior rows against surviving observed finite rods; a design
    # line cannot provide the missing physical support.
    exterior_denoising = {'removedPointCount': 0}
    review_score = np.zeros(count, bool) if fused_scores is None else np.asarray(fused_scores) < PROTECTION_THRESHOLD
    pending_suspect = np.flatnonzero(review_score & (context.refined_zone != 1) &
                                    (out['complete_class'] == 3) & (out['complete_instance'] == 0) & ~hook_protected)
    if len(pending_suspect):
        from .floating_noise import floating_noise_mask
        live_counts = np.bincount(out['complete_segment'], minlength=next_segment)
        reliable_segments = [{**s, 'pointCount': int(live_counts[s['id']])} for s in segments
                             if s['instanceId'] in strong_owners and live_counts[s['id']] > 0]
        anchors = protected_high & (out['complete_class'] == 3)
        removed, exterior_denoising = floating_noise_mask(context.positions[pending_suspect],
            np.full(len(pending_suspect), 4, np.uint8), [], reliable_segments, workers=workers,
            steel_scores=np.asarray(fused_scores)[pending_suspect], protected=protected_high[pending_suspect],
            observed_support_points=context.positions[anchors])
        selected = pending_suspect[removed]
        out['complete_class'][selected] = 4
        rejected += len(selected)
        if len(selected):
            operations.append({'action': 'filter', 'pointCount': len(selected),
                'reason': 'non_high_score_exterior_without_observed_support'})
    progress('第 6 步：最终细小悬浮残片与设计数量形态复核', 0, count)
    final_cluster_filter = filter_final_clusters(context, out, inventory, associations, hook_protected, cluster_records)
    rejected += final_cluster_filter['removedPointCount']
    operations.extend(dict(action='filter', phase='final_after_all_merges', **decision)
                      for decision in final_cluster_filter['decisions'])
    final_denoising = final_fragment_filter(context, out, workers=workers, protected=hook_protected)
    rejected += final_denoising['removedPointCount']
    if final_denoising['removedPointCount']:
        operations.append({'action': 'filter', 'phase': 'final_statistics',
            'pointCount': final_denoising['removedPointCount'],
            'reason': 'tiny_low_score_components_without_retained_steel_support'})
    # Rebuild all point counts from final ownership. Empty original fits/instances
    # are not counted as observed rods. IDs stay stable where possible.
    segment_counts=np.bincount(out['complete_segment'],minlength=next_segment)
    segments=[{**s,'pointCount':int(segment_counts[s['id']])} for s in segments if segment_counts[s['id']]>0]
    verify_hook_clusters(out, hook_groups)
    instances=[]; associations={}
    for i,a in enumerate(atoms):
        if a['instanceId']:
            associations.setdefault(a['instanceId'],set())
            if choices[i] is not None:associations[a['instanceId']].add(choices[i])
    for instance_id in sorted(map(int,np.unique(out['complete_instance'][out['complete_instance']>0]))):
        parts=[s for s in segments if s['instanceId']==instance_id]
        first=parts[0];ends=np.array([p for s in parts for p in (s['startM'],s['endM'])]);axis=np.array(first['endM'],dtype=float)-first['startM'];axis/=np.linalg.norm(axis)
        assoc=associations.get(instance_id,set());choice=next(iter(assoc)) if len(assoc)==1 else None
        entry={**original.get(instance_id,{}),'id':instance_id,'type':first['type'],'pointCount':sum(s['pointCount'] for s in parts),
            'segmentIds':[s['id'] for s in parts],'lengthM':float(np.ptp(ends@axis)),
            'diameterM':float(2*np.median([s['radiusM'] for s in parts])),'modelKind':'observed-piecewise-cylinder',
            'designUnitId':units[choice]['designUnitId'] if choice is not None else None,
            'designBarId':units[choice]['designBarId'] if choice is not None else None,
            'reviewStatus':'matched' if choice is not None else 'pending'}
        instances.append(entry)
    owners_by_unit={u['designUnitId']:[] for u in units}
    for i in instances:
        if i['designUnitId']:owners_by_unit[i['designUnitId']].append(i['id'])
    diagnostics=[{'designUnitId':u['designUnitId'],'kind':u['kind'],'observedInstanceIds':owners_by_unit[u['designUnitId']],
        'status':'unobserved' if not owners_by_unit[u['designUnitId']] else 'multiple_fragments_or_conflict' if len(owners_by_unit[u['designUnitId']])>1 else 'associated'} for u in units]
    cluster_quality = design_cluster_quality(context, out, instances, inventory)
    for name,values in out.items():setattr(context,name,values)
    counts=np.bincount(out['complete_class'],minlength=5)
    surviving_ids={i['id'] for i in instances}
    for o in operations:
        if o['action']=='merge':
            target=remap.get(o['sourceInstanceIds'][0],o['sourceInstanceIds'][0])
            o['finalInstanceId']=target if target in surviving_ids else 0
    report={'version':VERSION,'enabled':True,'pointCount':count,'instanceCount':len(instances),'instances':instances,'segments':segments,
        'counts':dict(zip(('table','fixture','rebar','noise'),map(int,counts[1:5]))),'clusters':cluster_records,
        'matchedExteriorPointCount':attached,'rejectedExteriorPointCount':rejected,
        'unassignedRebarPointCount':int(np.count_nonzero((out['complete_class']==3)&(out['complete_instance']==0))),
        'elapsedS':time.perf_counter()-started,'parameters':asdict(params),
        'designReview':{'mode':mode,'inventory':inventory,'operations':operations,'units':diagnostics,
            'components':[{'id':a['id'],'instanceId':a['instanceId'],'originalInstanceId':a['originalInstanceId'],
                'clusterId':a['clusterId'],'pointCount':len(a['rows']),
                'evidence':{k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in a['summary'].items() if k not in ('fits','coreFits','terminals')}} for a in atoms],
            'observedInstancesBefore':before,'observedInstancesAfter':len(instances),
            'designPhysicalBars':inventory.get('coverage',{}).get('physicalBarCount'),'designMatchingUnits':len(units),
            'mergedInstances':sum(o['action']=='merge' and bool(o.get('finalInstanceId')) for o in operations),'splitInstances':sum(o['action']=='split' for o in operations),
            'newInstances':sum(o['action']=='instantiate' for o in operations),'filteredPoints':rejected,
            'highConfidenceSteelPoints':int(np.count_nonzero(protected_high)),
            'blockedNoisePoints':int(np.count_nonzero(blocked_noise_rows)),
            'inheritedSpatialNoisePoints':int(np.count_nonzero(internal_noise)) if spatial_override else 0,
            'protectionThreshold':PROTECTION_THRESHOLD,
            'lowScoreThreshold':LOW_SCORE_THRESHOLD,
            'lowScoreFilteredPoints':int(np.count_nonzero(low_score & (out['complete_class'] == 4))),
            'exteriorDenoising':exterior_denoising,
            'finalDenoising':final_denoising,
            'finalClusterFilter':final_cluster_filter,
            'hookClusters':hook_report,
            'clusterQuality':cluster_quality,
            'hookProtectedPointCount':int(np.count_nonzero(hook_protected)),
            'hookAttachedPointCount':sum(o['pointCount'] for o in hook_operations),
            'pendingInstances':sum(i['reviewStatus']=='pending' for i in instances),
            'unobservedUnits':sum(u['status']=='unobserved' for u in diagnostics),
            'conflictingUnits':sum(u['status']=='multiple_fragments_or_conflict' for u in diagnostics),
            'extraObservedInstances':sum(i['reviewStatus']=='pending' for i in instances),
            'earlyExtensionClusters':sum(o['action']=='attach' for o in early_operations),'earlyExtensionPoints':len(early_rows),
            'separatedExteriorClusters':len(mixed_clusters),
            'reclusteredExteriorParts':sum(o.get('residualPartCount',0) for o in early_operations if o['action']=='separate'),
            'earlyAmbiguousPoints':int(np.count_nonzero(early_protected))-len(early_rows),
            'acrossFixtureMerges':sum(o['action']=='merge' and o.get('acrossFixture',False) and bool(o.get('finalInstanceId')) for o in operations),
            'qualityStatus':'requires_visual_review','countPolicy':'merge curved exterior clusters only; finish all connections before rejecting whole clusters with both design-length and point-count deficits'},
        'inputPolicy':'Step 05 retained steel only; no recovery of discarded points; measured XYZ unchanged'}
    return report
