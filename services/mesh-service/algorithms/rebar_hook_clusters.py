"""Protect observed bends while cylindrically cleaning their straight collars."""
import numpy as np
from scipy import optimize
from scipy.spatial import cKDTree
from .rebar_extension import exterior_clusters, ExtensionParameters


def _basis(axis):
    auxiliary = np.eye(3)[np.argmin(np.abs(axis))]
    first = np.cross(axis, auxiliary)
    first /= max(np.linalg.norm(first), 1e-9)
    return np.array([first, np.cross(axis, first)])


def freeze_hook_clusters(context, out, inventory, segments):
    rows = np.flatnonzero(out['complete_class'] == 3)
    locked = np.zeros(len(context.positions), bool)
    groups = []
    units = {u['designUnitId']: u for u in inventory['units']}
    bars = [b for b in inventory.get('bars', []) if b.get('excludedHookRunCount')
            and len(b.get('unitIds', [])) == 1]
    report = dict(classification='curved-exterior',
                  policy='bend-core-locked; straight-collar-cylinder-polish',
                  expectedRegionCount=0, detectedClusterCount=0, protectedPointCount=0,
                  mergedClusterCount=0, splitClusterCount=0, filteredPointCount=0, regions=[])
    if not len(rows):
        return groups, locked, report
    tree = cKDTree(context.positions[rows])
    scores = getattr(context, 'fused_steel_score', None)
    for bar in bars:
        unit = units[bar['unitIds'][0]]
        if unit['kind'] == 'web':
            continue
        axis = np.asarray(unit['direction']); origin = np.asarray(unit['startM'])
        path = np.asarray(bar['points'], float)
        lengths = np.linalg.norm(np.diff(path, axis=0), axis=1)
        # Each end of the long body can have a separate bend region.
        body = int(np.argmax(lengths))
        paths = [p for p in (path[:body+1], path[body+1:]) if len(p) > 1
                 and np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1)) > .015]
        shifts = []
        for s in segments:
            a, b = np.asarray(s['startM']), np.asarray(s['endM'])
            delta = b-a; length = np.linalg.norm(delta)
            if not s.get('pointCount') or length < .03 or abs(delta @ axis)/length < .98:
                continue
            center = (a+b)/2; offset = center-origin; offset -= (offset @ axis)*axis
            if np.linalg.norm(offset) <= .035:
                shifts.append((np.linalg.norm(offset), offset))
        shift = min(shifts, key=lambda x:x[0])[1] if shifts else np.zeros(3)
        for curve in paths:
            report['expectedRegionCount'] += 1
            curve = curve+shift
            # Include a short stem collar to retain the complete bent component.
            body_end = curve[-1] if np.linalg.norm(curve[-1]-(path[body]+shift)) < .001 else curve[0]
            at_start = np.linalg.norm(body_end-(path[body]+shift)) < .001
            radial = curve-body_end
            radial -= (radial @ axis)[:, None]*axis
            bend = radial[np.argmax(np.linalg.norm(radial, axis=1))]
            bend /= max(np.linalg.norm(bend), 1e-9)
            side = np.cross(axis, bend); side /= max(np.linalg.norm(side), 1e-9)
            # Register each measured bend locally. A long slightly bowed stem
            # can place its end centimetres away from the global design lane.
            coarse = rows[tree.query_ball_point(curve.mean(0), np.max(np.linalg.norm(curve-curve.mean(0), axis=1))+.065)]
            delta = context.positions[coarse]-body_end
            turned = (delta @ bend > .02) & (delta @ bend < np.max((curve-body_end)@bend)+.03)
            turned &= np.abs(delta @ side) < .045
            along = (curve-body_end) @ axis
            turned &= (delta @ axis > along.min()-.035) & (delta @ axis < along.max()+.035)
            if scores is not None:
                turned &= scores[coarse] >= .5
            if np.count_nonzero(turned) >= 12:
                correction = float(np.median(delta[turned] @ side))*side
                curve = curve+correction; body_end = body_end+correction
            toward_body = (path[body+1]-path[body])*(1 if at_start else -1)
            toward_body /= max(np.linalg.norm(toward_body), 1e-9)
            pieces = list(zip(curve[:-1], curve[1:]))+[(body_end, body_end+.25*toward_body)]
            candidates = set()
            for a, b in pieces:
                length = np.linalg.norm(b-a)
                if length < 1e-8:
                    continue
                local = rows[tree.query_ball_point((a+b)/2, length/2+bar['radiusM']+.024)]
                delta = context.positions[local]-a
                t = np.clip(delta @ ((b-a)/length), 0, length)
                distance = np.linalg.norm(delta-t[:, None]*(b-a)/length, axis=1)
                candidates.update(map(int, local[distance <= bar['radiusM']+.022]))
            selected = np.asarray(sorted(candidates), dtype=np.int64)
            selected = selected[~locked[selected]]
            region = dict(designBarId=bar['designBarId'], designUnitId=unit['designUnitId'],
                          boundsM=[(curve.min(0)-.018).tolist(), (curve.max(0)+.018).tolist()],
                          observedPointCount=len(selected))
            report['regions'].append(region)
            if len(selected) < 12:
                continue
            # A region alone is insufficient: the measured component must include
            # the turned part, rather than only points along the straight stem.
            offset = context.positions[selected]-body_end
            turned = offset @ bend > max(.012, 2*bar['radiusM'])
            if np.count_nonzero(turned) < 12:
                continue
            labels = exterior_clusters(context.positions[selected], ExtensionParameters(cluster_connection_radius=.0065))
            turned_counts = np.bincount(labels[turned], minlength=int(labels.max())+1)
            # Occlusion can disconnect the straight collar from the turn. Keep
            # its measured longitudinal components in the same immutable atom.
            axial = offset @ axis
            spans = np.zeros(len(turned_counts))
            for label in np.unique(labels):
                spans[label] = np.ptp(axial[labels == label])
            selected = selected[np.isin(labels, np.flatnonzero((turned_counts >= 12) | (spans >= .025)))]
            offset = context.positions[selected]-body_end
            turned = offset @ bend > max(.012, 2*bar['radiusM'])
            if scores is not None and np.mean(scores[selected[turned]] >= .5) < .25:
                continue
            region['observedPointCount'] = len(selected)
            locked[selected] = True
            stem_along = (context.positions[selected]-body_end) @ toward_body
            groups.append(dict(rows=selected, bendRows=selected[stem_along < .03],
                bodyEndM=body_end.tolist(), towardBody=toward_body.tolist(), stemCollarLengthM=.25,
                designUnitId=unit['designUnitId'],
                designBarId=bar['designBarId'], radiusM=float(bar['radiusM']), region=region,
                originalOwners=np.unique(out['complete_instance'][selected]).tolist()))
            # Remove the whole atom from ordinary pointwise ownership competition.
            for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
                out[name][selected] = 0
    report.update(detectedClusterCount=len(groups), protectedPointCount=int(locked.sum()))
    return groups, locked, report


def merge_hook_clusters(context, out, groups, associations, units, segments, report, *, workers=1):
    """Assign every row of a frozen atom together, or leave every row pending."""
    unit_owners = {}
    for owner, choices in associations.items():
        if len(choices) == 1:
            unit_owners.setdefault(units[next(iter(choices))]['designUnitId'], []).append(owner)
    operations = []
    for group in groups:
        rows = group['rows']; owners = unit_owners.get(group['designUnitId'], [])
        viable = []
        for owner in owners:
            own = np.flatnonzero((out['complete_class'] == 3) & (out['complete_instance'] == owner))
            if not len(own):
                continue
            distances, _ = cKDTree(context.positions[own]).query(context.positions[rows], workers=workers)
            unit = next(u for u in units if u['designUnitId'] == group['designUnitId'])
            stem_span = np.ptp(context.positions[rows] @ np.asarray(unit['direction']))
            # A measured long collar supplies direction through a short occluded
            # gap; a detached turn alone still requires immediate contact.
            maximum_gap = .16 if stem_span >= .12 else .025
            if np.min(distances) <= maximum_gap:
                viable.append(owner)
        if len(viable) != 1:
            group['record']['status'] = 'protected-pending'
            continue
        owner = viable[0]
        parts = [s for s in segments if s['instanceId'] == owner]
        if not parts:
            continue
        centers = [(np.asarray(s['startM'])+s['endM'])/2 for s in parts]
        _, nearest = cKDTree(centers).query(context.positions[rows], workers=workers)
        out['complete_instance'][rows] = owner
        out['complete_segment'][rows] = np.asarray([s['id'] for s in parts], np.uint32)[nearest]
        out['complete_confidence'][rows] = .55
        group['record'].update(status='merged', finalInstanceId=int(owner))
        report['mergedClusterCount'] += 1
        operations.append(dict(action='attach', phase='after_inner_outer_connection',
            clusterId=group['record']['id'], instanceIds=[int(owner)], pointCount=len(rows),
            reason='locked_curved_exterior_cluster_merged_whole'))
    return operations


def polish_non_hook_terminals(context, out, groups, units, segments, fixture_tree, report, *,
                              workers=1, fixture_distance=.012, surface_tolerance=.001,
                              bend_clearance=.03, fit_start=.04, fit_end=.19,
                              center_adjustment=.015):
    """Cylinder-polish the straight end of each protected hook-region atom.

    ``freeze_hook_clusters`` deliberately captures a 25 cm straight collar so an
    occluded bend can still be joined to its measured parent.  At the collar's
    non-bent end, however, a clamp edge can be connected to the steel component
    and was previously frozen with it.  Keep the bend core immutable, fit a fixed-
    radius cylinder to fixture-free collar evidence, and remove only fixture-
    contact rows that fall outside the design-diameter surface.
    """
    summary = dict(policy='bend locked; fixture-contact outside design-diameter straight-collar cylinder',
                   reviewedClusterCount=0, reviewedPointCount=0, fixtureContactPointCount=0,
                   fittedClusterCount=0, removedPointCount=0,
                   surfaceToleranceM=surface_tolerance, fixtureDistanceM=fixture_distance,
                   bendClearanceM=bend_clearance, fitRangeM=[fit_start, fit_end],
                   maximumCenterAdjustmentM=center_adjustment)
    report['nonHookTerminalPolish'] = summary
    if fixture_tree is None:
        return []
    operations = []
    for group in groups:
        owner = int(group.get('record', {}).get('finalInstanceId', 0))
        if not owner:
            continue
        rows = np.asarray(group['rows'], dtype=np.int64)
        body_end = np.asarray(group.get('bodyEndM'), float)
        toward_body = np.asarray(group.get('towardBody'), float)
        if body_end.shape != (3,) or toward_body.shape != (3,):
            continue
        toward_body /= max(np.linalg.norm(toward_body), 1e-9)
        points = context.positions[rows]
        stem_along = (points-body_end) @ toward_body
        in_collar = (stem_along >= bend_clearance) & (
            stem_along <= float(group.get('stemCollarLengthM', .25))+.02)
        if np.count_nonzero(in_collar) < 12:
            continue
        near_fixture = fixture_tree.query(points, workers=workers)[0] <= fixture_distance
        seed = (stem_along >= fit_start) & (stem_along <= fit_end) & ~near_fixture
        if np.count_nonzero(seed) < 24:
            continue
        parts = []
        for segment in segments:
            if segment['instanceId'] != owner:
                continue
            a, b = np.asarray(segment['startM'], float), np.asarray(segment['endM'], float)
            delta = b-a; length = np.linalg.norm(delta)
            if length < .008 or abs(delta @ toward_body)/length < .90:
                continue
            line_distance = np.linalg.norm(np.cross(body_end-a, delta/length))
            axial_distance = abs(((a+b)*.5-body_end) @ toward_body)
            parts.append((axial_distance+.25*line_distance, a, b, float(segment['radiusM'])))
        if not parts:
            continue
        _, a, b, fitted_radius = min(parts, key=lambda part: part[0])
        design_radius = float(group.get('radiusM', fitted_radius))
        radius = design_radius if np.isfinite(design_radius) and design_radius > 0 else fitted_radius
        axis = (b-a)/np.linalg.norm(b-a)
        if axis @ toward_body < 0:
            axis = -axis
        seed_points = points[seed]
        seed_center = seed_points.mean(axis=0)
        line_center = a+axis*((seed_center-a) @ axis)
        cross = _basis(axis)
        projected = (seed_points-seed_center) @ cross.T
        initial = (line_center-seed_center) @ cross.T
        fit = optimize.least_squares(
            lambda center: np.linalg.norm(projected-center, axis=1)-radius,
            initial, bounds=(initial-center_adjustment, initial+center_adjustment),
            loss='soft_l1', f_scale=.0006, max_nfev=50)
        cylinder_center = seed_center+fit.x @ cross
        seed_error = np.abs(np.linalg.norm(projected-fit.x, axis=1)-radius)
        if np.median(seed_error) > .002:
            continue
        delta = points-cylinder_center
        along = delta @ axis
        surface_error = np.abs(np.linalg.norm(delta-along[:, None]*axis, axis=1)-radius)
        selected = rows[in_collar & near_fixture & (surface_error > surface_tolerance)]
        summary['reviewedClusterCount'] += 1
        summary['reviewedPointCount'] += int(np.count_nonzero(in_collar))
        summary['fixtureContactPointCount'] += int(np.count_nonzero(in_collar & near_fixture))
        summary['fittedClusterCount'] += 1
        group['region']['nonHookReviewedPointCount'] = int(np.count_nonzero(in_collar))
        group['region']['nonHookPolishedPointCount'] = len(selected)
        group['region']['nonHookCylinderFitMedianErrorM'] = float(np.median(seed_error))
        if not len(selected):
            continue
        out['complete_class'][selected] = 4
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][selected] = 0
        summary['removedPointCount'] += len(selected)
        report['filteredPointCount'] += len(selected)
        report['splitClusterCount'] += 1
        group['polishedRows'] = selected
        operations.append(dict(action='filter', phase='after_hook_merge', sourceInstanceId=0,
            instanceIds=[owner],
            clusterId=group['record']['id'], pointCount=len(selected),
            reason='hook_straight_collar_fixture_contact_outside_local_cylinder',
            surfaceToleranceM=surface_tolerance, fixtureDistanceM=fixture_distance,
            bendClearanceM=bend_clearance, fitMedianErrorM=float(np.median(seed_error))))
    return operations


def verify_hook_clusters(out, groups):
    for group in groups:
        rows = np.asarray(group['rows'], dtype=np.int64)
        polished = np.asarray(group.get('polishedRows', []), dtype=np.int64)
        retained = rows[~np.isin(rows, polished)]
        bend_rows = np.asarray(group.get('bendRows', rows), dtype=np.int64)
        if (np.any(out['complete_class'][retained] != 3)
                or len(np.unique(out['complete_instance'][retained])) != 1
                or np.any(out['complete_class'][bend_rows] != 3)):
            raise RuntimeError('第六步违反弯曲外筋保护：弯曲核心不允许拆分或删除')
        if len(polished) and (np.any(out['complete_class'][polished] != 4)
                              or np.any(out['complete_instance'][polished] != 0)):
            raise RuntimeError('第六步弯钩直段圆柱打磨状态不一致')
