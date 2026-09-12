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
from .design_evidence import evaluate_evidence
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
    order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
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
    bins = np.unique(np.floor((angles + np.pi) / (2 * np.pi) * 24).astype(int))
    arc = float(len(bins) * 15.)
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
    span = float(np.ptp(t)) if len(t) else 0.
    return dict(point_count=int(len(points)), occupied_cells=int(len(cells)), occupied_bins=int(len(axial)),
                span_m=span, cylinder_error_m=cylinder_error, plane_error_m=plane_error,
                radial_alignment=alignment, normal_valid_fraction=normal_fraction,
                arc_degrees=arc, angle_degrees=0., offset_m=0., diameter_error_m=0.,
                plane_coherence=plane_coherence,
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
    t = (points[rows] - start) @ axis
    rows = rows[(t >= -policy.endpoint_margin) & (t <= np.linalg.norm(end-start) + policy.endpoint_margin)]
    if not len(rows):
        return []
    cross = _basis(axis); lateral = points[rows] @ cross.T
    # Deterministic coarse XY cells intentionally allow arbitrary XY translation
    # while preventing a whole layer from becoming one fit.
    keys = np.floor(lateral / max(.03, policy.max_offset)).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0])); keys, rows = keys[order], rows[order]
    _, starts = np.unique(keys, axis=0, return_index=True)
    ends = np.r_[starts[1:], len(rows)]
    groups = [rows[a:b] for a, b in zip(starts, ends) if b-a >= 3]
    return sorted(groups, key=lambda group: (-len(group), int(group.min())))[:policy.max_unit_candidates]


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
        tree = cKDTree(positions[eligible])
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
        excluded_nearby = int(_finite_window(positions[hard & ~table], start, end, reach).sum())
        pools = [rows]
        if kind == 'short' and len(eligible_rows):
            layer = getattr(context, 'shared_layer', None)
            layer_rows = eligible_rows
            if layer is not None:
                layer_rows = layer_rows[np.asarray(layer)[layer_rows] == np.asarray(layer)[np.argmin(np.abs(positions[:, 2]-center[2]))]]
            pools = _short_pools(positions, layer_rows, start, end, axis, policy)
        initial = dict(center=center, axis=axis, radius=radius, low=-length / 2, high=length / 2,
                       type=TYPE.get(kind, 1))
        for pool_number, pool in enumerate(pools):
          # Baseline plus two actually different bounded support passes.
          for attempt in range(min(2, policy.max_retries) + 1):
            strategy = ('baseline', 'widened-local-scale', 'local-normal-reestimate')[attempt]
            sample_rows = _sample(pool, positions, policy.voxel_size * (policy.retry_scale ** attempt), policy.max_fit_points)
            if len(sample_rows) < 3:
                attempts.append({'unitId': unit.get('designUnitId'), 'pool': pool_number, 'attempt': attempt, 'strategy': strategy, 'accepted': False, 'reason': 'no-observed-support', 'pointCount': int(len(sample_rows))})
                continue
            model = _fit_model(positions[sample_rows], initial, radius)
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
            if attempt == 2 and not use_valid.any():
                # PCA supplies a local plane normal only as a measured-normal
                # substitute for reporting; it never manufactures support.
                _, _, vh = np.linalg.svd(positions[support]-positions[support].mean(0), full_matrices=False)
                use_normals = np.tile(vh[-1], (len(support), 1)); use_valid = np.ones(len(support), bool)
            metrics = _metrics(positions[support], use_normals, use_valid, model, policy, fixture[support].mean())
            metrics['offset_m'] = float(np.linalg.norm((model['center']-center) -
                                              axis * ((model['center']-center) @ axis)))
            metrics['angle_degrees'] = float(np.degrees(np.arccos(np.clip(abs(model['axis'] @ axis), -1, 1))))
            metrics['diameter_error_m'] = 0.
            metrics['excluded_nearby_count'] = excluded_nearby
            metrics['branch_support_counts'] = _branch_support(context, support)
            # Measurements share the same source rows even where 02A/02B both
            # observed them.  A single provenance source prevents downstream
            # evidence scoring from double-counting those branch labels.
            metrics['source_ids'] = {name: ('observed-source-rows',) for name in
                                     ('point_count', 'occupied_cells', 'occupied_bins', 'span_m',
                                      'cylinder_error_m', 'radial_alignment', 'arc_degrees')}
            observed_t = (positions[support] - model['center']) @ model['axis']
            model['low'], model['high'] = (float(value) for value in np.quantile(observed_t, [.01, .99]))
            decision = evaluate_evidence(metrics, policy=policy)
            proposal = Candidate(str(unit['designUnitId']), support.astype(np.int64), model, metrics, attempt,
                                 provenance={'designBarId': unit.get('designBarId'), 'designUnitId': unit.get('designUnitId'),
                                             'sources': ('observed-source-rows',)})
            final_attempt = attempt == min(2, policy.max_retries)
            if decision.accepted or final_attempt:
                candidates.append(proposal)
            attempts.append({'unitId': unit.get('designUnitId'), 'pool': pool_number, 'attempt': attempt, 'strategy': strategy,
                             'accepted': bool(decision.accepted), 'reason': decision.reason.name.lower(),
                             'pointCount': int(len(support))})
            if decision.accepted or final_attempt:
                break
    report = {'version': 'design-candidates-v1', 'parameters': asdict(policy), 'candidateCount': len(candidates),
              'attempts': attempts, 'eligiblePointCount': int(eligible.sum()), 'hardExcludedPointCount': int(hard.sum()),
              'policy': 'finite observed windows; fixed design radii; no design-only support'}
    return candidates, report
