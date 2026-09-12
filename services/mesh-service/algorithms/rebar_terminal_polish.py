"""Lightweight final polishing for fixture-truncated straight rebar ends.

The final instance graph may join inner and exterior observations across a
clamp.  Consequently, the two observations next to the clamp are local open
ends even though neither is an end of the merged instance.  This module uses
the finite observed cylinder segments to find those local ends, then removes
only fixture-contact points outside the expected cylindrical surface.
"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy import optimize


def _basis(axis):
    auxiliary = np.eye(3)[np.argmin(np.abs(axis))]
    first = np.cross(axis, auxiliary)
    first /= max(np.linalg.norm(first), 1e-12)
    return np.array([first, np.cross(axis, first)])


def _weighted_median(values, weights):
    order = np.argsort(values, kind='stable')
    values, weights = values[order], weights[order]
    return float(values[np.searchsorted(np.cumsum(weights), weights.sum()*.5)])


def _live_models(out, segments):
    labels = np.asarray(out['complete_segment'])
    counts = np.bincount(labels, minlength=max([0]+[int(s['id']) for s in segments])+1)
    result = []
    for segment in segments:
        sid = int(segment['id'])
        owner = int(segment['instanceId'])
        radius = float(segment['radiusM'])
        if not owner or sid >= len(counts) or counts[sid] < 1 or not np.isfinite(radius) or radius <= 0:
            continue
        start, end = np.asarray(segment['startM'], float), np.asarray(segment['endM'], float)
        delta = end-start; length = float(np.linalg.norm(delta))
        if start.shape != (3,) or end.shape != (3,) or not np.isfinite(start).all() or not np.isfinite(end).all() or length < .004:
            continue
        result.append(dict(id=sid, owner=owner, start=start, end=end, axis=delta/length,
                           length=length, radius=radius, pointCount=int(counts[sid])))
    return result


def _continued(endpoint, outward, model, others):
    """True only for an observed, near-touching continuation of this local end."""
    for other in others:
        if other is model:
            continue
        if abs(other['radius']-model['radius']) > max(.001, .3*model['radius']):
            continue
        # A same-diameter observed segment meeting this endpoint is a measured
        # bend/junction, not clamp glue.  Suppress the shared endpoint regardless
        # of angle; a T-shaped fixture tail normally has no valid round segment.
        join = max(.0035, .55*(model['radius']+other['radius']))
        if min(np.linalg.norm(endpoint-other['start']),
               np.linalg.norm(endpoint-other['end'])) <= join:
            return True
        if abs(float(outward @ other['axis'])) < .92:
            continue
        delta = np.stack((other['start']-endpoint, other['end']-endpoint))
        along = delta @ outward
        # A clamp-sized empty interval must remain two local terminal candidates.
        if along.max() <= .001 or along.min() > join:
            continue
        closest = np.clip(float((endpoint-other['start']) @ other['axis']), 0., other['length'])
        distance = np.linalg.norm(endpoint-(other['start']+closest*other['axis']))
        if distance <= max(.003, .65*(model['radius']+other['radius'])):
            return True
    return False


def _design_radius(owner, associations, units, observed):
    choices = list((associations or {}).get(owner, ()))
    matched = [units[int(choice)] for choice in choices
               if isinstance(choice, (int, np.integer)) and 0 <= int(choice) < len(units)]
    matched = [unit for unit in matched
               if np.isfinite(float(unit.get('diameterM', 0))) and float(unit.get('diameterM', 0)) > 0]
    if len(matched) == 1:
        return float(matched[0]['diameterM'])/2, 'matched-design-unit', matched[0].get('designUnitId')
    candidates = matched or [unit for unit in units
        if np.isfinite(float(unit.get('diameterM', 0))) and float(unit.get('diameterM', 0)) > 0]
    if candidates:
        unit = min(candidates, key=lambda item: abs(float(item['diameterM'])/2-observed))
        source = 'associated-design-family' if matched else 'nearest-design-family'
        return float(unit['diameterM'])/2, source, unit.get('designUnitId') if matched else None
    return observed, 'observed-fallback-no-design-diameter', None


def _round_transverse_branch(points, normals, parent_axis, radius):
    """Protect an observed round bend/cross-member from a T-tail decision."""
    if len(points) < 24:
        return False
    center = points.mean(axis=0)
    values, vectors = np.linalg.eigh((points-center).T @ (points-center)/len(points))
    axis = vectors[:, -1]
    if abs(float(axis @ parent_axis)) > .65 or values[-1] < 3*max(values[-2], 1e-12):
        return False
    span = float(np.ptp((points-center) @ axis))
    if span < max(.015, 6*radius):
        return False
    basis = _basis(axis); projected = (points-center) @ basis.T
    cross_values = np.linalg.eigvalsh(np.cov(projected.T))
    spatial_round = (cross_values[0] > (.15*radius)**2 and
                     cross_values[1] > (.35*radius)**2)
    normal_round = False
    if normals is not None:
        normals = np.asarray(normals, float)
        valid = np.linalg.norm(normals, axis=1) > .5
        if np.count_nonzero(valid) >= 16:
            local = normals[valid]
            covariance = local.T @ local/len(local)
            normal_values = np.linalg.eigvalsh(covariance)
            normal_round = (np.mean(np.abs(local @ axis) < .55) >= .65 and
                            normal_values[-2] > .08 and normal_values[-1] < .88)
    if not (spatial_round or normal_round):
        return False
    fit = optimize.least_squares(lambda offset: np.linalg.norm(projected-offset, axis=1)-radius,
        np.zeros(2), bounds=(-2.5*radius, 2.5*radius), loss='soft_l1',
        f_scale=.0006, max_nfev=25)
    error = np.abs(np.linalg.norm(projected-fit.x, axis=1)-radius)
    return bool(np.median(error) <= max(.0012, .3*radius))


def polish_fixture_terminals(context, out, segments, units, associations, fixture_tree, *,
                             workers=1, protected=None, fixture_distance=.012,
                             surface_tolerance=.001, inside_depth=.055,
                             outside_margin=.012, center_adjustment=.012):
    """Polish every fixture-truncated *local* end after all instance merging.

    Work is bounded by the number of live cylinder ends, not source point count.
    Candidate fits are independent and run concurrently.  Writes are applied
    once, in deterministic candidate order, after all workers finish.
    """
    summary = dict(
        policy='local open segment ends near fixture; fixed-axis design-diameter cylinder with fixed radial tolerance',
        parallel=True, workerCount=max(1, int(workers)), liveSegmentCount=0,
        openEndpointCount=0, fixtureTruncatedEndpointCount=0, fittedEndpointCount=0,
        reviewedPointCount=0, fixtureContactPointCount=0, removedPointCount=0,
        fixtureDistanceM=float(fixture_distance), surfaceToleranceM=float(surface_tolerance),
        insideDepthM=float(inside_depth), outsideMarginM=float(outside_margin),
        maximumCenterAdjustmentM=float(center_adjustment), diameterSources={},
        preservedRoundTransverseBranchCount=0, terminals=[])
    if fixture_tree is None:
        return [], summary

    models = _live_models(out, segments)
    summary['liveSegmentCount'] = len(models)
    if not models:
        return [], summary
    by_owner = {}
    for model in models:
        by_owner.setdefault(model['owner'], []).append(model)
    protected = np.zeros(len(context.positions), bool) if protected is None else np.asarray(protected, bool)

    candidates = []
    for owner, parts in sorted(by_owner.items()):
        owner_candidates = []
        radii = np.array([p['radius'] for p in parts])
        weights = np.array([p['pointCount'] for p in parts], float)
        observed = _weighted_median(radii, weights)
        radius, radius_source, design_unit = _design_radius(owner, associations, units, observed)
        for model in parts:
            for side, endpoint, outward in (
                    ('start', model['start'], -model['axis']),
                    ('end', model['end'], model['axis'])):
                if _continued(endpoint, outward, model, parts):
                    continue
                summary['openEndpointCount'] += 1
                center_distance = float(fixture_tree.query(endpoint, workers=1)[0])
                if center_distance > fixture_distance+1.5*radius:
                    continue
                candidate = dict(owner=owner, segmentId=model['id'], side=side,
                    endpoint=endpoint, outward=outward, radius=radius,
                    observedRadius=observed, localRadius=model['radius'],
                    radiusSource=radius_source, designUnitId=design_unit,
                    centerFixtureDistance=center_distance)
                # Overlapping local fits can expose the same end more than once.
                duplicate = any(old['owner'] == owner and old['outward'] @ outward > .8 and
                                np.linalg.norm(old['endpoint']-endpoint) < max(.004, radius)
                                for old in owner_candidates)
                if not duplicate:
                    candidates.append(candidate)
                    owner_candidates.append(candidate)
    summary['fixtureTruncatedEndpointCount'] = len(candidates)
    if not candidates:
        return [], summary

    def fit_candidate(candidate):
        endpoint, outward, radius = candidate['endpoint'], candidate['outward'], candidate['radius']
        spatial_tree = getattr(context, 'tree', None)
        if spatial_tree is None:
            rows = np.flatnonzero((out['complete_class'] == 3) &
                                  (out['complete_instance'] == candidate['owner']))
        else:
            reach = float(np.hypot(max(inside_depth, outside_margin), max(.06, 12*radius)))
            rows = np.asarray(spatial_tree.query_ball_point(endpoint, reach, workers=1), dtype=np.int64)
            rows = rows[(out['complete_class'][rows] == 3) &
                        (out['complete_instance'][rows] == candidate['owner'])]
        points = context.positions[rows]
        delta = points-endpoint; axial = delta @ outward
        radial = np.linalg.norm(delta-axial[:, None]*outward, axis=1)
        local = ((axial >= -inside_depth) & (axial <= outside_margin) &
                 (radial <= max(.06, 12*radius)))
        local_rows = rows[local]
        if len(local_rows) < 16:
            return candidate, np.empty(0, np.int64), 0, 0, None, False, False
        local_points = context.positions[local_rows]
        near_fixture = fixture_tree.query(local_points, workers=1)[0] <= fixture_distance
        local_axial = axial[local]
        seed_near = max(.008, 2*radius)
        seed_far = max(.035, min(inside_depth, 10*radius))
        seed = ((local_axial <= -seed_near) & (local_axial >= -seed_far) &
                ~near_fixture & (radial[local] <= max(.018, 3.5*radius)))
        if np.count_nonzero(seed) < 12:
            return candidate, np.empty(0, np.int64), len(local_rows), int(near_fixture.sum()), None, False, False
        seed_points = local_points[seed]
        if len(seed_points) > 1024:
            seed_points = seed_points[np.linspace(0, len(seed_points)-1, 1024, dtype=int)]
        basis = _basis(outward)
        projected = (seed_points-endpoint) @ basis.T
        fit = optimize.least_squares(lambda center: np.linalg.norm(projected-center, axis=1)-radius,
            np.zeros(2), bounds=(-center_adjustment, center_adjustment),
            loss='soft_l1', f_scale=.0006, max_nfev=35)
        fit_error = np.abs(np.linalg.norm(projected-fit.x, axis=1)-radius)
        median_error = float(np.median(fit_error))
        if median_error > max(.0015, .35*radius):
            return candidate, np.empty(0, np.int64), len(local_rows), int(near_fixture.sum()), median_error, False, False
        centerline = endpoint+fit.x @ basis
        d = local_points-centerline; t = d @ outward
        fitted_radial = np.linalg.norm(d-t[:, None]*outward, axis=1)
        # Preserve true end-cap/interior samples.  A T tail is outside the known
        # radius; points merely inside the ideal surface are not extra material.
        selected = local_rows[near_fixture & (fitted_radial > radius+surface_tolerance) & ~protected[local_rows]]
        normals = getattr(context, 'normals', None)
        round_branch = _round_transverse_branch(
            context.positions[selected], None if normals is None else np.asarray(normals)[selected],
            outward, radius)
        if round_branch:
            selected = np.empty(0, np.int64)
        return candidate, selected.astype(np.int64, copy=False), len(local_rows), int(near_fixture.sum()), median_error, True, round_branch

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        results = list(pool.map(fit_candidate, candidates))

    operations = []; removed = np.zeros(len(context.positions), bool)
    for candidate, selected, reviewed, fixture_count, fit_error, fit_accepted, round_branch in results:
        summary['reviewedPointCount'] += reviewed
        summary['fixtureContactPointCount'] += fixture_count
        summary['fittedEndpointCount'] += int(fit_accepted)
        summary['preservedRoundTransverseBranchCount'] += int(round_branch)
        selected = selected[~removed[selected]]
        removed[selected] = True
        source = candidate['radiusSource']
        summary['diameterSources'][source] = summary['diameterSources'].get(source, 0)+1
        record = dict(instanceId=candidate['owner'], segmentId=candidate['segmentId'],
            side=candidate['side'], endpointM=candidate['endpoint'].tolist(),
            axis=candidate['outward'].tolist(), diameterM=2*candidate['radius'],
            diameterSource=source, designUnitId=candidate['designUnitId'],
            localDiameterM=2*candidate['localRadius'], observedInstanceDiameterM=2*candidate['observedRadius'],
            fixtureCenterDistanceM=candidate['centerFixtureDistance'], reviewedPointCount=reviewed,
            fixtureContactPointCount=fixture_count, removedPointCount=len(selected), fitMedianErrorM=fit_error)
        record['fitAccepted'] = bool(fit_accepted)
        record['preservedRoundTransverseBranch'] = bool(round_branch)
        summary['terminals'].append(record)
        if len(selected):
            operations.append(dict(action='filter', phase='final_terminal_polish',
                instanceIds=[candidate['owner']], sourceInstanceId=candidate['owner'],
                segmentId=candidate['segmentId'], pointCount=len(selected),
                reason='fixture_contact_outside_local_terminal_cylinder',
                endpointM=candidate['endpoint'].tolist(), diameterM=2*candidate['radius'],
                diameterSource=source, designUnitId=candidate['designUnitId']))
    selected = np.flatnonzero(removed)
    if len(selected):
        out['complete_class'][selected] = 4
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][selected] = 0
    summary['removedPointCount'] = len(selected)
    return operations, summary
