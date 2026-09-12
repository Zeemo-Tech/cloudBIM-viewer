"""Measured hook ownership and final design-relative cluster review for Step 06."""
import numpy as np
from scipy.spatial import cKDTree

from .rebar_extension import exterior_clusters, ExtensionParameters


def final_fragment_filter(context, out, *, workers=1, protected=None):
    """Last pass: small, disconnected, low-score residuals relative to live rods."""
    report = dict(removedPointCount=0, removedComponentCount=0, reviewedComponentCount=0,
                  maximumSpanM=.020, maximumRelativeSpan=.08, maximumScore=.5,
                  assignedSatellitePointCount=0, minimumSeparationM=.012,
                  rule='tiny residuals or detached instance satellites; all scores low; separated from retained steel; largest owned component preserved')
    scores = getattr(context, 'fused_steel_score', None)
    if scores is None:
        report['skippedReason'] = 'fusion scores unavailable'
        return report
    retained = out['complete_class'] == 3
    protected = np.zeros(len(retained), bool) if protected is None else protected
    pending = np.flatnonzero(retained & (out['complete_instance'] == 0))
    owned = np.flatnonzero(retained & (out['complete_instance'] > 0))
    if not len(owned):
        return report
    owners = out['complete_instance'][owned]
    order = np.argsort(owners, kind='stable')
    _, starts = np.unique(owners[order], return_index=True)
    groups = np.split(owned[order], starts[1:])
    spans = [np.linalg.norm(np.ptp(context.positions[rows], axis=0)) for rows in groups]
    typical = float(np.median(spans))
    maximum = min(report['maximumSpanM'], typical*report['maximumRelativeSpan'])
    report.update(medianObservedSpanM=typical, effectiveMaximumSpanM=maximum)
    candidates = []
    candidate_mask = np.zeros(len(retained), bool)
    for population in ([pending] if len(pending) else []) + groups:
        labels = exterior_clusters(context.positions[population], ExtensionParameters())
        order = np.argsort(labels, kind='stable')
        _, starts = np.unique(labels[order], return_index=True)
        parts = np.split(population[order], starts[1:])
        largest = int(np.argmax([len(rows) for rows in parts]))
        assigned = out['complete_instance'][population[0]] > 0
        for index, rows in enumerate(parts):
            report['reviewedComponentCount'] += 1
            if protected[rows].any():
                continue
            if assigned and index == largest:
                continue
            if np.any(~np.isfinite(scores[rows])) or np.max(scores[rows]) > .5:
                continue
            if np.linalg.norm(np.ptp(context.positions[rows], axis=0)) > maximum:
                continue
            candidates.append(rows)
            candidate_mask[rows] = True
    # Freeze support before filtering; candidate islands cannot support each other.
    anchors = np.flatnonzero(retained & ~candidate_mask)
    tree = cKDTree(context.positions[anchors])
    for rows in candidates:
        distances, _ = tree.query(context.positions[rows], workers=workers)
        if np.min(distances) <= report['minimumSeparationM']:
            continue
        out['complete_class'][rows] = 4
        report['assignedSatellitePointCount'] += int(np.count_nonzero(out['complete_instance'][rows]))
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][rows] = 0
        report['removedPointCount'] += len(rows)
        report['removedComponentCount'] += 1
    return report


def design_cluster_quality(context, out, instances, inventory, *, groups=None):
    """Count and shape diagnostics use final source XYZ, including carried hooks."""
    units = {u['designUnitId']: u for u in inventory['units']}
    bars = {b['designBarId']: b for b in inventory.get('bars', [])}
    records = []
    if groups is None:
        from .rebar_overlength_tails import retained_instance_groups
        groups = retained_instance_groups(out)
    for item in instances:
        unit = units.get(item.get('designUnitId'))
        if unit is None:
            records.append(dict(instanceId=item['id'], status='unmatched'))
            continue
        axis = np.asarray(unit['direction'], float)
        auxiliary = np.eye(3)[np.argmin(np.abs(axis))]
        cross = np.cross(axis, auxiliary); cross /= np.linalg.norm(cross)
        basis = np.array([axis, cross, np.cross(axis, cross)])
        bar = bars.get(unit['designBarId'], {})
        whole = len(bar.get('unitIds', [])) == 1 and unit['kind'] != 'web'
        design = np.asarray(bar['points'] if whole and bar.get('points') else [unit['startM'], unit['endM']])
        # Use a local origin for large survey coordinates.
        expected = np.ptp((design-design[0]) @ basis.T, axis=0)
        expected[1:] += unit['diameterM']
        points = context.positions[groups[item['id']]]
        centered = points-points.mean(axis=0)
        _, vectors = np.linalg.eigh(centered.T @ centered)
        measured_axis = vectors[:, -1]
        if measured_axis @ axis < 0:
            measured_axis = -measured_axis
        # Width is shape, not registration/tilt error amplified by bar length.
        # Rotate the transverse design basis minimally onto the measured axis.
        v = np.cross(axis, measured_axis)
        skew = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        rotation = np.eye(3)+skew+skew@skew/(1+float(axis@measured_axis))
        observed = np.ptp(centered @ (basis @ rotation.T).T, axis=0)
        length_ratio = float(observed[0]/max(expected[0], 1e-9))
        width_ratio = float(max(observed[1:]/np.maximum(expected[1:], 1e-9)))
        partial = unit.get('coverage') != 'complete'
        flags = []
        if not partial and length_ratio < .65:
            flags.append('too_short')
        if not partial and observed[0] > expected[0]*1.15+.02:
            flags.append('too_long')
        if np.any(observed[1:] > expected[1:]*1.6+.008):
            flags.append('too_wide')
        if whole and bar.get('excludedHookRunCount') and np.any((expected[1:] > .02) & (observed[1:] < expected[1:]*.5)):
            flags.append('hook_width_missing')
        record = dict(instanceId=item['id'], designUnitId=unit['designUnitId'],
            observedLengthM=float(observed[0]), designLengthM=float(expected[0]), lengthRatio=length_ratio,
            observedWidthsM=observed[1:].tolist(), designWidthsM=expected[1:].tolist(), widthRatio=width_ratio,
            status='shape_mismatch' if flags else 'partial_design' if partial else 'consistent', flags=flags)
        records.append(record)
        item['shapeReview'] = record
    matched = {i['designUnitId'] for i in instances if i.get('designUnitId')}
    count_delta = len(instances)-len(units)
    complete_inventory = not any(b.get('coverage') == 'unresolved' for b in bars.values())
    shape_mismatch = sum(r['status'] == 'shape_mismatch' for r in records)
    count_matches = count_delta == 0 and len(matched) == len(units)
    return dict(expectedClusterCount=len(units), observedClusterCount=len(instances), countDelta=count_delta,
        countMatches=count_matches, inventoryComplete=complete_inventory,
        missingUnitCount=len(units)-len(matched), unmatchedInstanceCount=sum(r['status']=='unmatched' for r in records),
        shapeMismatchCount=shape_mismatch, tooShortCount=sum('too_short' in r.get('flags', []) for r in records),
        hookWidthMissingCount=sum('hook_width_missing' in r.get('flags', []) for r in records),
        status='consistent' if count_matches and not shape_mismatch and complete_inventory else 'needs_review',
        policy='one cluster per physical bar; each straight web diagonal counted separately; observed shortening down to 65% allowed; no count-only deletion',
        lengthLowerRatio=.65, lengthUpperRatio=1.15, widthUpperRatio=1.6, instances=records)
