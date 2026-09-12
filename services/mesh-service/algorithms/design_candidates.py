"""Bounded, design-seeded fixed-radius observation candidates.

This module deliberately proposes only from source rows.  It does not extend a
design centreline or create support when the local scan window is empty.
"""
from __future__ import annotations

from dataclasses import asdict
import math
import numpy as np
from scipy.spatial import cKDTree

from .design_evidence_contract import Candidate, RobustnessPolicy
from .design_evidence import evaluate_evidence, retry_decision
from .rebar_tracks import _fixed_radius


TYPE = {'straight': 1, 'short': 2, 'web': 3}


def _basis(axis):
    first = np.cross(axis, np.eye(3)[np.argmin(np.abs(axis))])
    first /= max(np.linalg.norm(first), 1.e-12)
    return np.array([first, np.cross(axis, first)])


def _sample(rows, points, voxel, limit):
    """One stable source row per spatial voxel, then a stable bounded sample."""
    keys = np.floor(points[rows] / voxel).astype(np.int64)
    # Coordinate ordering makes sampling invariant to source-row permutation.
    order = np.lexsort((points[rows,2],points[rows,1],points[rows,0],keys[:,2],keys[:,1],keys[:,0]))
    keys, ordered_rows = keys[order], rows[order]
    _, first = np.unique(keys, axis=0, return_index=True)
    chosen = ordered_rows[first]
    if len(chosen) > limit:
        chosen = chosen[np.linspace(0, len(chosen) - 1, limit, dtype=int)]
    return chosen


def _finite_window(points, start, end, reach):
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length <= 1.e-12:
        return np.zeros(len(points), bool)
    axis = vector / length
    t = (points - start) @ axis
    radial = np.linalg.norm(points - start - t[:, None] * axis, axis=1)
    return (t >= -reach) & (t <= length + reach) & (radial <= reach)


def _competing_plane_patches(points,normals,valid,cylinder_residual,policy):
    """Measured flat faces distinguish square sections from round surfaces.

    Each plane and cylinder residual uses the SAME subset. Highly concentrated
    normals only propose a plane; curvature which fits the cylinder better vetoes it.
    """
    if np.count_nonzero(valid)<12:return 0.,0
    nn=normals[valid].copy();largest=np.argmax(np.abs(nn),axis=1)
    nn*=np.where(nn[np.arange(len(nn)),largest]<0,-1,1)[:,None]
    keys,inverse,counts=np.unique(np.rint(nn*4).astype(int),axis=0,return_inverse=True,return_counts=True)
    covered=np.zeros(len(points),bool);directions=[]
    for group in np.argsort(-counts,kind='stable')[:8]:
        direction=nn[inverse==group].mean(0);direction/=np.linalg.norm(direction)
        if any(abs(direction@d)>.98 for d in directions):continue
        rows=np.flatnonzero(valid&(np.abs(normals@direction)>=policy.plane_normal_alignment))
        if len(rows)<policy.min_planar_patch_fraction*len(points):continue
        projection=points[rows]@direction;mid=(projection.min()+projection.max())/2
        successful=False
        for part in (rows[projection<=mid],rows[projection>mid]):
            if len(part)<max(12,policy.min_planar_patch_fraction*len(points)):continue
            centered=points[part]-points[part].mean(0);_,_,basis=np.linalg.svd(centered,full_matrices=False)
            plane_error=float(np.median(np.abs(centered@basis[-1])))
            cylinder_error=float(np.median(cylinder_residual[part]))
            if plane_error<policy.plane_advantage*cylinder_error and abs(basis[-1]@direction)>=policy.plane_normal_alignment:
                covered[part]=True;successful=True
        if successful:directions.append(direction)
    return float(covered.mean()),len(directions)


def _metrics(points, normals, normal_valid, model, policy, fixture_fraction):
    axis = model['axis']; center = model['center']; radius = model['radius']
    delta = points - center; t = delta @ axis
    radial_vector = delta - t[:, None] * axis
    radial = np.linalg.norm(radial_vector, axis=1)
    cylinder_error = float(np.median(np.abs(radial - radius))) if len(points) else math.inf
    # A plane is a competing local explanation, fitted only to observations.
    if len(points) >= 3:
        origin = points.mean(0); _, _, vh = np.linalg.svd(points - origin, full_matrices=False)
        plane_normal = vh[-1]; plane_distance = np.abs((points - origin) @ plane_normal)
        plane_error = float(np.median(plane_distance))
    else:
        plane_error = math.inf
    cross = _basis(axis)
    angles = np.arctan2(radial_vector @ cross[1], radial_vector @ cross[0])
    ordered_angles=np.sort(np.mod(angles,2*np.pi))
    arc=float(np.degrees(2*np.pi-np.max(np.diff(np.r_[ordered_angles,ordered_angles[0]+2*np.pi])))) if len(angles) else 0.
    cells = np.unique(np.floor(points / policy.voxel_size).astype(np.int64), axis=0)
    axial = np.unique(np.floor(t / policy.axial_bin).astype(np.int64))
    valid = np.asarray(normal_valid, bool) & np.isfinite(normals).all(axis=1)
    valid &= np.linalg.norm(normals, axis=1) > .5
    if valid.any():
        unit = radial_vector[valid] / np.maximum(radial[valid, None], 1.e-12)
        alignment = float(np.mean(np.abs(np.sum(normals[valid] * unit, axis=1))))
        normal_fraction = float(valid.mean())
        plane_coherence = float(np.mean(np.abs(normals[valid] @ plane_normal))) if len(points) >= 3 else 0.
    else:
        alignment, normal_fraction, plane_coherence = None, 0., 0.
    patch_fraction,patch_count=_competing_plane_patches(points,normals,valid,np.abs(radial-radius),policy)
    span = float(np.ptp(t)) if len(t) else 0.
    return dict(point_count=int(len(points)), occupied_cells=int(len(cells)), occupied_bins=int(len(axial)),
                span_m=span, axial_coverage=float(len(axial)/max(1,np.ceil(span/policy.axial_bin))), cylinder_error_m=cylinder_error, plane_error_m=plane_error,
                radial_alignment=alignment, normal_valid_fraction=normal_fraction,
                arc_degrees=arc, angle_degrees=0., offset_m=0., diameter_error_m=0.,
                plane_coherence=plane_coherence, planar_patch_fraction=patch_fraction,planar_patch_count=patch_count,
                fixture_fraction=float(fixture_fraction), design_only=False,
                hard_excluded=0)


def _fit_model(sample, initial, radius):
    """Fixed-radius fitting is an optional refinement, never an admission gate."""
    try:
        fitted = _fixed_radius(sample, initial, radius)
        if np.isfinite(fitted['center']).all() and np.isfinite(fitted['axis']).all():
            return fitted
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        pass
    return dict(initial, radius=radius)


def _branch_support(context, rows):
    """Count classifier observations without treating shared rows as two votes."""
    a = getattr(context, 'geometry_class', None)
    b = getattr(context, 'projection_class', None)
    a = np.zeros(len(rows), bool) if a is None else np.asarray(a)[rows] == 3
    b = np.zeros(len(rows), bool) if b is None else np.asarray(b)[rows] == 3
    return {'02A': int(a.sum()), '02B': int(b.sum()),
            'both': int((a & b).sum()), 'unique_source_count': int((a | b).sum()),
            'independence': 'shared source rows; branch counts are provenance, not independent evidence'}


def _surface_rows(points, rows, model, tolerance):
    q = points[rows] - model['center']; t = q @ model['axis']
    radial = np.linalg.norm(q - t[:, None] * model['axis'], axis=1)
    return rows[(t >= model['low'] - tolerance) & (t <= model['high'] + tolerance) &
                (np.abs(radial - model['radius']) <= tolerance)]


def _short_pools(points, rows, start, end, axis, policy):
    """Bound a same-layer scene-wide search into directional local pools."""
    if not len(rows):return []
    transverse=np.array([-axis[1],axis[0],0.]);transverse/=np.linalg.norm(transverse)
    q=points[rows]@transverse
    order=np.argsort(q,kind='stable');rows=rows[order];q=q[order]
    cuts=np.r_[0,np.flatnonzero(np.diff(q)>.004)+1,len(rows)]
    groups=[]
    for lo,hi in zip(cuts[:-1],cuts[1:]):
        group=rows[lo:hi]
        t=points[group]@axis;order=np.argsort(t,kind='stable');group=group[order];t=t[order]
        gaps=np.r_[0,np.flatnonzero(np.diff(t)>.035)+1,len(group)]
        for a,b in zip(gaps[:-1],gaps[1:]):
            part=group[a:b]
            if len(part)>=12 and .02<=t[b-1]-t[a]<=np.linalg.norm(end-start)+.025:groups.append(part)
    return sorted(groups,key=lambda g:(-len(g),*np.mean(points[g],axis=0)))[:policy.max_unit_candidates]


def generate_candidates(context, inventory, *, policy=None, workers=1, progress=None):
    """Return fixed-radius proposals and an auditable bounded-attempt report.

    ``rows`` always indexes the original ``context.positions`` population.
    """
    policy = policy or RobustnessPolicy()
    progress = progress or (lambda *args: None)
    positions = np.asarray(context.positions, float)
    count = len(positions)
    table = np.asarray(getattr(context, 'shared_table_mask', np.zeros(count)), bool)
    hard = np.asarray(getattr(context, 'shared_floating_noise', np.zeros(count)), bool)
    eligible = ~table & ~hard
    normals = np.asarray(getattr(context, 'normals', np.zeros_like(positions)), float)
    valid = np.asarray(getattr(context, 'normal_valid', np.zeros(count)), bool)
    fixture = np.asarray(getattr(context, 'refined_class', np.zeros(count)), int) == 2
    units = sorted((u for u in inventory.get('units', []) if u.get('coverage', 'complete') != 'unresolved'),
                   key=lambda u: str(u.get('designUnitId', '')))
    # A source-row tree is shared by callers that already have one.  Test and
    # minimal contexts have no such cache, so only then build a local fallback.
    tree = getattr(context, 'tree', None)
    if tree is None:
        tree = (getattr(context, 'classification_cache', None) or {}).get('tree')
    if tree is None and eligible.any():
        tree = cKDTree(positions)
    eligible_rows = np.flatnonzero(eligible)
    candidates, attempts = [], []
    for ordinal, unit in enumerate(units):
        progress('design candidate', ordinal, len(units))
        start, end = np.asarray(unit['startM'], float), np.asarray(unit['endM'], float)
        vector = end - start; length = float(np.linalg.norm(vector))
        radius = float(unit.get('diameterM', 0.)) / 2
        kind = unit.get('kind', 'straight')
        if length <= 1.e-9 or not np.isfinite(radius) or radius <= 0:
            attempts.append({'unitId': unit.get('designUnitId'), 'attempt': 0, 'accepted': False, 'reason': 'invalid-design'})
            continue
        axis = vector / length; center = (start + end) / 2
        # A finite tube prevents a nominal design extension from collecting data.
        reach = radius + policy.window_margin
        nearby = tree.query_ball_point(center, length / 2 + reach) if tree is not None else []
        nearby = np.asarray(nearby, int)
        if getattr(tree, 'n', 0) == count:
            rows = nearby[eligible[nearby]]
        else:
            rows = eligible_rows[nearby] if len(nearby) else np.empty(0, int)
        rows = rows[_finite_window(positions[rows], start, end, reach)] if len(rows) else rows
        excluded_nearby = int(np.count_nonzero(hard[nearby])) if getattr(tree,'n',0)==count else 0
        pools = [rows]
        if kind == 'short' and len(eligible_rows):
            layer_rows=eligible_rows[np.abs(positions[eligible_rows,2]-center[2])<=policy.short_layer_tolerance+radius]
            pools = _short_pools(positions, layer_rows, start, end, axis, policy)
        initial = dict(center=center, axis=axis, radius=radius, low=-length / 2, high=length / 2,
                       type=3 if kind=='web' else (2 if unit.get('layerId',1)>1 else 1))
        for pool_number, pool in enumerate(pools):
          history=[]
          local_initial=dict(initial)
          if kind=='short' and len(pool):
              local_initial['center']=np.median(positions[pool],axis=0)
              along=(positions[pool]-local_initial['center'])@axis
              local_initial['low'],local_initial['high']=float(along.min()),float(along.max())
          # Baseline plus two actually different bounded support passes.
          for attempt in range(min(2, policy.max_retries) + 1):
            strategy = ('baseline', 'adjust_support_or_normals', 'compare_neighbors')[attempt]
            sample_rows = _sample(pool, positions, policy.voxel_size * (policy.retry_scale ** attempt), policy.max_fit_points)
            if len(sample_rows) < 3:
                attempts.append({'unitId': unit.get('designUnitId'), 'pool': pool_number, 'attempt': attempt, 'strategy': strategy, 'accepted': False, 'reason': 'no-observed-support', 'pointCount': int(len(sample_rows))})
                break
            model = _fit_model(positions[sample_rows], local_initial, radius)
            tolerance = policy.max_surface_error * (policy.retry_scale if attempt else 1.)
            support = _surface_rows(positions, pool, model, tolerance)
            # Refit once from surface-only support, so clutter from the broad
            # proposal tube cannot define either cylinder or plane evidence.
            if len(support) >= 3:
                model = _fit_model(positions[_sample(support, positions, policy.voxel_size, policy.max_fit_points)], model, radius)
                support = _surface_rows(positions, pool, model, tolerance)
            if not len(support):
                attempts.append({'unitId': unit.get('designUnitId'), 'pool': pool_number, 'attempt': attempt, 'strategy': strategy, 'accepted': False, 'reason': 'fit-has-no-surface-support', 'pointCount': 0})
                continue
            use_normals = normals[support]
            use_valid = valid[support]
            if attempt==1 and np.mean(use_valid)<.5 and len(support)>=12:
                # Independently recompute local kNN PCA normals; never broadcast a global plane normal.
                local_tree=cKDTree(positions[pool]);_,nn=local_tree.query(positions[support],k=min(12,len(pool)),workers=workers)
                near=positions[pool[nn]];near-=near.mean(1,keepdims=True)
                eigen,axes=np.linalg.eigh(np.einsum('nki,nkj->nij',near,near))
                use_normals=axes[:,:,0];use_valid=(eigen[:,1]>1e-12)&(eigen[:,0]<eigen[:,1]*.8)
            metrics = _metrics(positions[support], use_normals, use_valid, model, policy, fixture[support].mean())
            metrics['design_position_offset_m']=float(np.linalg.norm((model['center']-center)-axis*((model['center']-center)@axis)))
            metrics['offset_m']=abs(float(model['center'][2]-center[2])) if kind=='short' else metrics['design_position_offset_m']
            metrics['angle_degrees'] = float(np.degrees(np.arccos(np.clip(abs(model['axis'] @ axis), -1, 1))))
            metrics['diameter_error_m'] = 0.
            metrics['missing_endpoints']=metrics['span_m']<length-max(.020,.05*length)
            metrics['excluded_nearby_count'] = excluded_nearby
            metrics['branch_support_counts'] = _branch_support(context, support)
            # Measurements share the same source rows even where 02A/02B both
            # observed them.  A single provenance source prevents downstream
            # evidence scoring from double-counting those branch labels.
            metrics['source_ids'] = {name: ('observed-source-rows',) for name in
                                     ('point_count', 'occupied_cells', 'occupied_bins', 'span_m',
                                      'cylinder_error_m', 'radial_alignment', 'arc_degrees')}
            observed_t = (positions[support] - model['center']) @ model['axis']
            model['low'], model['high'] = (float(value) for value in np.quantile(observed_t, [.001, .999]))
            decision = evaluate_evidence(metrics, policy=policy)
            proposal = Candidate(str(unit['designUnitId']), support.astype(np.int64), model, metrics, attempt,
                                 provenance={'designBarId': unit.get('designBarId'), 'designUnitId': unit.get('designUnitId'),
                                             'sources': ('observed-source-rows',)})
            proposal.evidence=decision
            retry=retry_decision(proposal,history,policy=policy)
            final_attempt = attempt == min(2, policy.max_retries) or not retry['retry']
            history.append(dict(attempt=attempt,source_ids=support.tolist(),support_signature=(len(support),int(use_valid.sum()))))
            if decision.accepted or final_attempt:
                candidates.append(proposal)
            attempts.append({'unitId': unit.get('designUnitId'), 'pool': pool_number, 'attempt': attempt, 'strategy': strategy,
                             'accepted': bool(decision.accepted), 'reason': decision.reason.name.lower(),
                             'pointCount': int(len(support))})
            if decision.accepted or final_attempt:
                break
            if attempt==1:
                # Global independent-neighbor comparison occurs in the adapter; no identical local refit.
                if not decision.accepted and not final_attempt:candidates.append(proposal)
                attempts.append({'unitId':unit['designUnitId'],'attempt':2,'strategy':'stop','accepted':False,'reason':'no_new_local_support'})
                break
    report = {'version': 'design-candidates-v1', 'parameters': asdict(policy), 'candidateCount': len(candidates),
              'attempts': attempts, 'eligiblePointCount': int(eligible.sum()), 'hardExcludedPointCount': int(hard.sum()),
              'policy': 'finite observed windows; fixed design radii; no design-only support'}
    return candidates, report
