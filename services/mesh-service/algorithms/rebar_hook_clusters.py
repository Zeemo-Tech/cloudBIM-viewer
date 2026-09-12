"""Protect observed bends while cleaning their opposite straight terminals."""
import numpy as np
from scipy.spatial import cKDTree
from .rebar_extension import exterior_clusters, ExtensionParameters


def freeze_hook_clusters(context, out, inventory, segments):
    rows = np.flatnonzero(out['complete_class'] == 3)
    locked = np.zeros(len(context.positions), bool)
    groups = []
    units = {u['designUnitId']: u for u in inventory['units']}
    bars = [b for b in inventory.get('bars', []) if b.get('excludedHookRunCount')
            and len(b.get('unitIds', [])) == 1]
    report = dict(classification='curved-exterior', policy='merge-only',
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
            groups.append(dict(rows=selected, designUnitId=unit['designUnitId'],
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
                              workers=1, fixture_distance=.012, surface_tolerance=.0025,
                              terminal_span=.30, endpoint_margin=.012, bridge_gap=.08):
    """Remove clamp contact left outside the observed cylinder at a hook's other end.

    The curved atom remains immutable.  Only the opposite, straight terminal is
    reviewed, and only where an already classified fixture is nearby.  Using the
    measured piecewise axes (plus short bridges across clamp occlusion) allows a
    bowed bar to survive while a glued plate/edge cannot borrow the bar identity.
    """
    summary = dict(policy='fixture-contact outside measured cylinder surface',
                   reviewedOwnerCount=0, reviewedPointCount=0, fixtureContactPointCount=0,
                   removedPointCount=0, surfaceToleranceM=surface_tolerance,
                   fixtureDistanceM=fixture_distance, terminalSpanM=terminal_span,
                   maximumBridgeGapM=bridge_gap)
    report['nonHookTerminalPolish'] = summary
    if fixture_tree is None:
        return []
    unit_by_id = {u['designUnitId']: u for u in units}
    operations = []
    processed = set()
    for group in groups:
        owner = int(group.get('record', {}).get('finalInstanceId', 0))
        if not owner or owner in processed:
            continue
        processed.add(owner)
        unit = unit_by_id.get(group['designUnitId'])
        if unit is None:
            continue
        axis = np.asarray(unit['direction'], float)
        axis /= max(np.linalg.norm(axis), 1e-9)
        parts = []
        for segment in segments:
            if segment['instanceId'] != owner:
                continue
            a, b = np.asarray(segment['startM'], float), np.asarray(segment['endM'], float)
            delta = b-a; length = np.linalg.norm(delta)
            if length < .008 or abs(delta @ axis)/length < .90:
                continue
            parts.append((a, b, float(segment['radiusM'])))
        if not parts:
            continue
        endpoints = np.asarray([point for a, b, _ in parts for point in (a, b)])
        low, high = np.min(endpoints @ axis), np.max(endpoints @ axis)
        hook_projection = float(np.mean(context.positions[group['rows']] @ axis))
        non_hook_projection = low if abs(hook_projection-high) <= abs(hook_projection-low) else high
        inward_sign = 1. if non_hook_projection == low else -1.
        local_parts = [part for part in parts if min(
            inward_sign*(part[0] @ axis-non_hook_projection),
            inward_sign*(part[1] @ axis-non_hook_projection),
        ) <= terminal_span+.08]
        if not local_parts:
            continue
        local_parts.sort(key=lambda part: inward_sign*((part[0]+part[1])*.5 @ axis-non_hook_projection))
        # The regularized body sections provide a robust physical radius even if
        # a short terminal fit itself swallowed a small attached fixture patch.
        radii = np.asarray([radius for _, _, radius in parts])
        design_radius = float(group.get('radiusM', np.median(radii)))
        plausible = radii[(radii >= max(.0012, .5*design_radius)) &
                          (radii <= min(.012, 1.5*design_radius))]
        radius = float(np.median(plausible)) if len(plausible) else design_radius
        primitives = [(a, b) for a, b, _ in local_parts]
        for first, second in zip(local_parts[:-1], local_parts[1:]):
            pairs = [(a, b) for a in first[:2] for b in second[:2]]
            a, b = min(pairs, key=lambda pair: np.linalg.norm(pair[1]-pair[0]))
            gap = np.linalg.norm(b-a)
            if .001 < gap <= bridge_gap and abs((b-a) @ axis)/gap >= .85:
                primitives.append((a, b))
        rows = np.flatnonzero((out['complete_class'] == 3) & (out['complete_instance'] == owner))
        if not len(rows):
            continue
        distance_from_terminal = inward_sign*(context.positions[rows] @ axis-non_hook_projection)
        rows = rows[(distance_from_terminal >= -endpoint_margin) &
                    (distance_from_terminal <= terminal_span)]
        if not len(rows):
            continue
        points = context.positions[rows]
        surface_error = np.full(len(rows), np.inf)
        for a, b in primitives:
            delta = b-a; length = np.linalg.norm(delta); direction = delta/length
            along = (points-a) @ direction
            radial = np.linalg.norm((points-a)-along[:, None]*direction, axis=1)
            error = np.abs(radial-radius)
            error[(along < -endpoint_margin) | (along > length+endpoint_margin)] = np.inf
            surface_error = np.minimum(surface_error, error)
        near_fixture = fixture_tree.query(points, workers=workers)[0] <= fixture_distance
        selected = rows[near_fixture & (surface_error > surface_tolerance)]
        summary['reviewedOwnerCount'] += 1
        summary['reviewedPointCount'] += len(rows)
        summary['fixtureContactPointCount'] += int(np.count_nonzero(near_fixture))
        group['region']['nonHookReviewedPointCount'] = len(rows)
        group['region']['nonHookPolishedPointCount'] = len(selected)
        if not len(selected):
            continue
        out['complete_class'][selected] = 4
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][selected] = 0
        summary['removedPointCount'] += len(selected)
        operations.append(dict(action='filter', phase='after_hook_merge', instanceIds=[owner],
            clusterId=group['record']['id'], pointCount=len(selected),
            reason='non_hook_terminal_fixture_contact_outside_measured_cylinder',
            surfaceToleranceM=surface_tolerance, fixtureDistanceM=fixture_distance,
            terminalSpanM=terminal_span))
    return operations


def verify_hook_clusters(out, groups):
    for group in groups:
        rows = group['rows']
        if np.any(out['complete_class'][rows] != 3) or len(np.unique(out['complete_instance'][rows])) != 1:
            raise RuntimeError('第六步违反弯曲外筋整簇保护：不允许拆分或删除')
