"""Reclaim detached extension tails using cached, pre-extension design evidence.

No design search, cylinder fitting, PCA or spatial tree is performed here.
Length triggers a review; only complete detached, newly attached blocks can
be discarded. Continuous overlength observations remain measured exceptions.
"""
from dataclasses import asdict, dataclass
import time

import numpy as np


VERSION = 'overlength-tails-v1'
DIRECT_EXTENSION = 1
CLUSTER_CARRY = 2


@dataclass(frozen=True)
class TailParameters:
    relative_allowance: float = .15
    absolute_allowance: float = .020
    bin_size: float = .005
    minimum_gap: float = .030
    diameter_gap_factor: float = 3.
    spacing_gap_factor: float = 6.
    maximum_candidate_cost: float = 2.
    candidate_margin: float = .12
    length_consensus_ratio: float = 1.05
    maximum_angle_degrees: float = 12.
    maximum_bins: int = 100_000
    maximum_blocks: int = 128
    maximum_instance_points: int = 1_000_000


def retained_instance_groups(out):
    """Stable source-row order, shared with the final shape diagnostics."""
    owners = out['complete_instance']
    rows = np.flatnonzero((out['complete_class'] == 3) & (owners > 0))
    order = np.argsort(owners[rows], kind='stable')
    ids, starts = np.unique(owners[rows[order]], return_index=True)
    return dict(zip(map(int, ids), np.split(rows[order], starts[1:]))) if len(ids) else {}


def _length_evidence(anchors, ranked, units, associated, params):
    """Only original interior candidates may justify a length, never tails."""
    candidates = set()
    cosine = np.cos(np.deg2rad(params.maximum_angle_degrees))
    for index, atom in anchors:
        row = ranked[index]
        if not row or row[0][0] > params.maximum_candidate_cost:
            return None, 'no_reliable_design_candidate'
        # Global count competition can select a different lane from the local
        # top choices. Include that selection only if the ORIGINAL core ranked
        # it as plausible, then require length/family consensus below.
        plausible = [u for cost, u in row if cost <= params.maximum_candidate_cost
                     and (cost <= row[0][0]+params.candidate_margin or u in associated)]
        for u in plausible:
            unit = units[u]
            length, diameter = float(unit['lengthM']), float(unit['diameterM'])
            axis = np.asarray(unit['direction'], float)
            norm = np.linalg.norm(axis)
            if (unit.get('coverage') != 'complete' or not np.isfinite(length+diameter+norm)
                    or length <= 0 or diameter <= 0 or norm < 1e-9):
                return None, 'incomplete_design'
            summary = atom['summary']
            if (abs(axis @ summary['axis'])/norm < cosine
                    or abs(diameter-2*summary['radius']) > max(.002, .35*diameter)):
                return None, 'incompatible_design_candidate'
        candidates.update(plausible)
    if len(associated) > 1:
        return None, 'multiple_design_units'
    if not candidates or (associated and not set(associated).issubset(candidates)):
        return None, 'assignment_without_core_support'
    selected = [units[u] for u in sorted(candidates)]
    lengths = [float(u['lengthM']) for u in selected]
    diameters = [float(u['diameterM']) for u in selected]
    if (max(lengths) > min(lengths)*params.length_consensus_ratio
            or len({u['kind'] for u in selected}) != 1
            or max(diameters) > min(diameters)*1.2):
        return None, 'ambiguous_length'
    return dict(designLengthM=max(lengths), diameterM=max(diameters),
                designUnitId=selected[0]['designUnitId'] if len(selected) == 1 else None,
                candidateUnitIds=[u['designUnitId'] for u in selected],
                lengthSource='matched_core' if len(selected) == 1 else 'length_consensus'), None


def tail_decision(along, core, eligible, protected, length, diameter, params=None):
    """Pure, bounded one-dimensional decision on a frozen ownership snapshot."""
    params = params or TailParameters()
    remove = np.zeros(len(along), bool)
    record = dict(status='retained', reason='within_length_allowance', tails=[])
    if len(along) > params.maximum_instance_points:
        record['reason'] = 'point_budget'
        return remove, record
    if not len(along) or not np.isfinite(along).all() or np.count_nonzero(core) < 12:
        record['reason'] = 'insufficient_core'
        return remove, record
    low, high = float(along.min()), float(along.max())
    allowed = length*(1+params.relative_allowance)+params.absolute_allowance
    record.update(beforeLengthM=high-low, afterLengthM=high-low, allowedLengthM=allowed)
    if high-low <= allowed:
        return remove, record
    record['status'] = 'review'
    a, b = float(along[core].min()), float(along[core].max())
    record.update(coreLengthM=b-a, coreIntervalM=[a, b], allowableUnionM=[b-allowed, a+allowed])
    if b-a > allowed:
        record['reason'] = 'core_already_overlong'
        return remove, record
    if np.any(protected):
        record['reason'] = 'protected_hook'
        return remove, record
    size = params.bin_size
    if (high-low)/size >= params.maximum_bins:
        record['reason'] = 'bin_budget'
        return remove, record
    bins = np.floor((along-low)/size).astype(np.int64)
    occupied = np.flatnonzero(np.bincount(bins))
    core_bins = np.flatnonzero(np.bincount(bins[core]))
    if len(core_bins) < 6:
        record['reason'] = 'insufficient_core_sampling'
        return remove, record
    spacing = float(np.median(np.diff(core_bins)))*size
    gap = max(params.minimum_gap, params.diameter_gap_factor*diameter,
              params.spacing_gap_factor*spacing)
    record.update(axialSpacingM=spacing, minimumGapM=gap)
    # Count only fully empty bins. A partial bin can never inflate a gap.
    breaks = np.flatnonzero((np.diff(occupied)-1)*size > gap+1e-12)
    if len(breaks)+1 > params.maximum_blocks:
        record['reason'] = 'block_budget'
        return remove, record
    if not len(breaks):
        record['reason'] = 'continuous_overlength'
        return remove, record
    labels = np.searchsorted(occupied[breaks+1], bins, side='right')
    blocks = []
    for label in range(len(breaks)+1):
        mask = labels == label
        blocks.append((mask, float(along[mask].min()), float(along[mask].max())))
    for i, (mask, start, end) in enumerate(blocks):
        side = 'start' if end < b-allowed else 'end' if start > a+allowed else None
        if side is None or np.any(core[mask]) or not np.all(eligible[mask]):
            continue
        inward = i+1 if side == 'start' else i-1
        if not 0 <= inward < len(blocks):
            continue
        measured_gap = blocks[inward][1]-end if side == 'start' else start-blocks[inward][2]
        if measured_gap <= gap:
            continue
        remove |= mask
        record['tails'].append(dict(side=side, gapM=measured_gap,
                                    intervalM=[start, end], pointCount=int(mask.sum())))
    if remove.any():
        record.update(status='reclaimed', reason='detached_extension_outside_core_length_envelope',
                      afterLengthM=float(np.ptp(along[~remove])))
    else:
        record['reason'] = 'no_unprotected_external_tail'
    return remove, record


def _refresh_segments(points, rows, out, segments, owner):
    """Update finite extents without fitting or altering the measured axis."""
    labels = out['complete_segment'][rows]
    order = np.argsort(labels, kind='stable')
    ids, starts = np.unique(labels[order], return_index=True)
    grouped = dict(zip(map(int, ids), np.split(rows[order], starts[1:])))
    for segment in segments:
        if segment['instanceId'] != owner:
            continue
        selected = grouped.get(segment['id'], [])
        segment['pointCount'] = len(selected)
        if not len(selected):
            continue
        start, end = np.asarray(segment['startM'], float), np.asarray(segment['endM'], float)
        delta = end-start
        norm = np.linalg.norm(delta)
        if norm < 1e-9:
            continue
        axis = delta/norm
        along = (points[selected]-start) @ axis
        segment['startM'] = (start+along.min()*axis).tolist()
        segment['endM'] = (start+along.max()*axis).tolist()


def filter_overlength_tails(context, out, segments, units, associations, atoms, ranked,
                           extension_source, *, protected=None, groups=None, params=None, enabled=True):
    params = params or TailParameters()
    started = time.perf_counter()
    report = dict(version=VERSION, enabled=enabled, parameters=asdict(params),
                  newDesignQueryCount=0, candidateInstanceCount=0, reviewedPointCount=0,
                  measuredPointCount=0, reclaimedInstanceCount=0, removedPointCount=0,
                  highScoreRemovedPointCount=0, decisions=[], skippedReasons={},
                  designEvidenceS=0., measurementS=0., decisionS=0., updateS=0.)
    grouping_started = time.perf_counter()
    groups = retained_instance_groups(out) if groups is None else groups
    report['groupingS'] = time.perf_counter()-grouping_started
    if not enabled:
        report['elapsedS'] = time.perf_counter()-started
        return [], report, groups
    anchor_groups = {}
    for index, atom in enumerate(atoms):
        summary = atom['summary']
        if (atom['instanceId'] and atom['originalInstanceId'] and summary['strong']
                and summary['length'] >= .12 and summary['interiorFraction'] >= .8
                and summary['fixtureNearFraction'] < .2):
            anchor_groups.setdefault(atom['instanceId'], []).append((index, atom))
    operations = []
    def skip(reason):
        report['skippedReasons'][reason] = report['skippedReasons'].get(reason, 0)+1
    for owner, rows in groups.items():
        anchors = anchor_groups.get(owner)
        if not anchors:
            skip('no_reliable_internal_core')
            continue
        stage_started = time.perf_counter()
        evidence, reason = _length_evidence(anchors, ranked, units, associations.get(owner, set()), params)
        report['designEvidenceS'] += time.perf_counter()-stage_started
        if evidence is None:
            skip(reason)
            report['decisions'].append(dict(instanceId=owner, status='skipped', reason=reason))
            continue
        stable = max(anchors, key=lambda item: item[1]['summary']['length'])[1]['summary']
        axis = np.asarray(stable['axis'], float)
        cosine = np.cos(np.deg2rad(params.maximum_angle_degrees))
        if any(abs(np.asarray(fit['axis']) @ axis) < cosine
               for _, atom in anchors for fit in atom['summary']['coreFits']):
            skip('curved_core')
            continue
        if len(rows) > params.maximum_instance_points:
            skip('point_budget')
            continue
        # Stable grouping keeps each owner's rows sorted; intersect original
        # anchors with final ownership, so earlier removals never support a cut.
        stage_started = time.perf_counter()
        core = np.zeros(len(rows), bool)
        for _, atom in anchors:
            original = atom['rows']
            original = original[(out['complete_instance'][original] == owner)
                                & (out['complete_class'][original] == 3)
                                & (context.refined_zone[original] == 1)]
            core[np.searchsorted(rows, original)] = True
        along = (context.positions[rows]-stable['center']) @ axis
        report['measuredPointCount'] += len(rows)
        eligible = ((extension_source[rows] > 0) & (context.internal_instance[rows] == 0)
                    & (context.refined_zone[rows] != 1))
        protected_rows = np.zeros(len(rows), bool) if protected is None else protected[rows]
        report['measurementS'] += time.perf_counter()-stage_started
        stage_started = time.perf_counter()
        remove, decision = tail_decision(along, core, eligible, protected_rows,
                                        evidence['designLengthM'], evidence['diameterM'], params)
        report['decisionS'] += time.perf_counter()-stage_started
        if decision['status'] == 'retained':
            skip(decision['reason'])
            continue
        report['candidateInstanceCount'] += 1
        report['reviewedPointCount'] += len(rows)
        decision.update(instanceId=owner, **evidence)
        report['decisions'].append(decision)
        if not remove.any():
            skip(decision['reason'])
            continue
        stage_started = time.perf_counter()
        selected = rows[remove]
        source = extension_source[selected]
        decision.update(removedPointCount=len(selected),
                        directExtensionPointCount=int(np.count_nonzero(source == DIRECT_EXTENSION)),
                        clusterCarryPointCount=int(np.count_nonzero(source == CLUSTER_CARRY)))
        scores = getattr(context, 'fused_steel_score', None)
        report['highScoreRemovedPointCount'] += int(np.count_nonzero(scores[selected] >= .9)) if scores is not None else 0
        out['complete_class'][selected] = 4
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][selected] = 0
        groups[owner] = rows[~remove]
        _refresh_segments(context.positions, groups[owner], out, segments, owner)
        report['reclaimedInstanceCount'] += 1
        report['removedPointCount'] += len(selected)
        operations.append(dict(action='filter', phase='overlength_tail_recovery',
                               pointCount=len(selected), **decision))
        report['updateS'] += time.perf_counter()-stage_started
    report['elapsedS'] = time.perf_counter()-started
    return operations, report, groups
