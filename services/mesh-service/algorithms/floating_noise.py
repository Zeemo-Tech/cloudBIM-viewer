"""Conservative Step 05 denoising against observed structural support.

Source rows stay intact. Scored candidates need a locally observed finite
surface or nearby frozen high-score observations, including in the lower band.
"""
import numpy as np
from scipy.spatial import cKDTree


VERSION = 'floating-support-v4-observed-surface'
HIGH_SCORE = .9
LOW_SCORE = .5
LOW_SCORE_SUPPORT_MARGIN = .006
SCORE_TOLERANCE = 1e-6
OBSERVED_SUPPORT_RADIUS = .008
LOCAL_SUPPORT_RADIUS = .006


def _finite_segment(segment):
    """Return normalized finite-cylinder data, or None for unusable evidence."""
    if segment.get('type') not in (1, 2, 3) or segment.get('pointCount', 0) <= 0:
        return None
    # New callers distinguish cylinders backed by measured fusion evidence
    # from prior-only fits. Absence of the field retains the legacy contract.
    if ('measuredSupportPointCount' in segment and
            segment.get('measuredSupportPointCount', 0) <= 0):
        return None
    try:
        start = np.asarray(segment['startM'], dtype=float)
        end = np.asarray(segment['endM'], dtype=float)
        radius = float(segment['radiusM'])
    except (KeyError, TypeError, ValueError):
        return None
    if start.shape != (3,) or end.shape != (3,) or radius < 0:
        return None
    if not (np.isfinite(start).all() and np.isfinite(end).all() and np.isfinite(radius)):
        return None
    return start, end, radius, segment


def _cylinder_support(points, segments, *, support_margin, sample_spacing, workers):
    """Mark points inside each finite cylinder's own protection envelope.

    Axis samples are only a spatial index. Every possible hit is checked using
    its exact distance to the finite axis segment, so neither sample spacing nor
    another cylinder's larger radius can enlarge the final envelope.
    """
    supported = np.zeros(len(points), bool)
    if not len(points) or not segments:
        return supported
    if sample_spacing <= 0:
        raise ValueError('sample_spacing 必须大于 0')
    tree = cKDTree(points)
    for start, end, radius, _ in segments:
        threshold = radius + support_margin
        if threshold < 0:
            continue
        delta = end - start
        length = float(np.linalg.norm(delta))
        count = max(2, int(np.ceil(length / sample_spacing)) + 1)
        samples = np.linspace(start, end, count)
        interval = length / (count - 1)
        # This broad-phase radius cannot miss a point inside the continuous
        # envelope. Exact checking below removes its discretization extras.
        broad_radius = float(np.hypot(threshold, interval / 2)) + 1e-12
        neighborhoods = tree.query_ball_point(samples, broad_radius, workers=workers)
        if not any(neighborhoods):
            continue
        nearby = np.unique(np.concatenate([np.asarray(ids, dtype=np.intp)
                                           for ids in neighborhoods if ids]))
        offsets = points[nearby] - start
        squared_length = float(delta @ delta)
        if squared_length:
            along = np.clip((offsets @ delta) / squared_length, 0., 1.)
            nearest = start + along[:, None] * delta
        else:
            nearest = np.broadcast_to(start, offsets.shape)
        distances = np.linalg.norm(points[nearby] - nearest, axis=1)
        supported[nearby[distances <= threshold + 1e-12]] = True
    return supported


def _surface_support(points, segments, margins, *, sample_spacing, workers):
    """Mark locally observed points close to a finite capsule surface."""
    supported = np.zeros(len(points), bool)
    if not len(points) or not segments:
        return supported
    if sample_spacing <= 0:
        raise ValueError('sample_spacing 必须大于 0')
    margins = np.asarray(margins, dtype=float)
    tree = cKDTree(points)
    # A lone point in a long fitted gap must not make that empty span observed.
    locally_observed = tree.query_ball_point(
        points, LOCAL_SUPPORT_RADIUS, return_length=True, workers=workers) >= 2
    for start, end, radius, _ in segments:
        maximum_margin = float(np.max(margins))
        delta = end - start
        length = float(np.linalg.norm(delta))
        count = max(2, int(np.ceil(length / sample_spacing)) + 1)
        samples = np.linspace(start, end, count)
        interval = length / (count - 1)
        broad_radius = float(np.hypot(radius + maximum_margin, interval / 2)) + 1e-12
        neighborhoods = tree.query_ball_point(samples, broad_radius, workers=workers)
        if not any(neighborhoods):
            continue
        nearby = np.unique(np.concatenate([np.asarray(ids, dtype=np.intp)
                                           for ids in neighborhoods if ids]))
        offsets = points[nearby] - start
        squared_length = float(delta @ delta)
        if squared_length:
            along = np.clip((offsets @ delta) / squared_length, 0., 1.)
            nearest = start + along[:, None] * delta
        else:
            nearest = np.broadcast_to(start, offsets.shape)
        axis_distance = np.linalg.norm(points[nearby] - nearest, axis=1)
        surface_error = np.abs(axis_distance - radius)
        hit = (surface_error <= margins[nearby] + 1e-12) & locally_observed[nearby]
        supported[nearby[hit]] = True
    return supported


def _score_surface_margins(scores, maximum):
    """Continuously widen measured-surface tolerance without admitting tube volume."""
    return np.minimum(maximum, .0015 + .003 * np.clip(scores, 0., HIGH_SCORE) / HIGH_SCORE)


def floating_noise_mask(points, types, bands, segments, *, workers=1,
                        lower_margin=.003, support_margin=.012, sample_spacing=.004,
                        protected=None, steel_scores=None,
                        observed_support_points=None):
    """Return points that lack observed spatial steel support.

    Without ``steel_scores`` the legacy scope is retained: only type 4 is
    reviewed. With scores, types 1--4 below 0.9 are reviewed, including the
    ordinary single-route score (0.65); scores at least 0.9 are hard protected.
    The lower height band alone does not protect scored points. Frozen observed
    neighbors preserve off-model bends and ends without changing their scores.
    """
    points = np.asarray(points)
    types = np.asarray(types)
    if len(types) != len(points):
        raise ValueError('types 必须与点逐行对应')
    protected = (np.zeros(len(points), bool) if protected is None else
                 np.asarray(protected, dtype=bool))
    if protected.shape != (len(points),):
        raise ValueError('protected 必须与点逐行对应')

    external_observed = np.empty((0, 3), dtype=float)
    if observed_support_points is not None:
        external_observed = np.asarray(observed_support_points, dtype=float)
        if (external_observed.ndim != 2 or external_observed.shape[1] != 3 or
                not np.isfinite(external_observed).all()):
            raise ValueError('observed_support_points 必须是有限的 N×3 坐标')

    scores = None
    if steel_scores is not None:
        scores = np.asarray(steel_scores, dtype=float)
        if scores.shape != (len(points),):
            raise ValueError('steel_scores 必须与点逐行对应')
        if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
            raise ValueError('steel_scores 必须在 0..1 范围内')

    eligible = types == 4 if scores is None else np.isin(types, (1, 2, 3, 4))
    hard_protected = protected.copy()
    if scores is not None:
        # Stored float32 levels (notably 0.9) round just below their decimal
        # contract when promoted to float64.
        hard_protected |= scores >= HIGH_SCORE - SCORE_TOLERANCE
    review = eligible & ~hard_protected
    removed = np.zeros(len(points), bool)

    lower_band = None
    if bands:
        try:
            low, high = float(bands[0]['low']), float(bands[0]['high'])
            if np.isfinite((low, high)).all() and low <= high:
                lower_band = (low, high)
        except (KeyError, TypeError, ValueError):
            pass
    support_types = ((2, 3) if scores is None and lower_band is not None else (1, 2, 3))
    normalized = [_finite_segment(segment) for segment in segments]
    reliable_segments = [segment for segment in normalized
                         if segment is not None and segment[3]['type'] in support_types]
    prior_only_count = sum(
        segment.get('type') in support_types and segment.get('pointCount', 0) > 0 and
        'measuredSupportPointCount' in segment and
        segment.get('measuredSupportPointCount', 0) <= 0
        for segment in segments
    )

    report = {
        'version': VERSION,
        'removedPointCount': 0,
        'candidatePointCount': int(np.count_nonzero(eligible)),
        'reviewedCandidatePointCount': int(np.count_nonzero(review)),
        'protectedCandidatePointCount': int(np.count_nonzero(eligible & hard_protected)),
        'blockedRemovalPointCount': 0,
        'lowScoreCandidatePointCount': (0 if scores is None else
                                        int(np.count_nonzero(
                                            eligible & (scores <= LOW_SCORE + SCORE_TOLERANCE)))),
        'lowerMarginM': lower_margin,
        'supportMarginM': support_margin,
        'lowScoreSupportMarginM': (min(support_margin, LOW_SCORE_SUPPORT_MARGIN) if scores is None else
                                   float(_score_surface_margins(np.array(LOW_SCORE), support_margin))),
        'observedSupportRadiusM': OBSERVED_SUPPORT_RADIUS,
        'externalObservedSupportPointCount': int(len(external_observed)),
        'reliableSegmentCount': len(reliable_segments),
        'priorOnlyRejectedSegmentCount': int(prior_only_count),
        'scope': ('unassigned inner steel only (legacy scoreless mode)' if scores is None else
                  'steel types 1-4 below fused score 0.9'),
        'rule': ('scoreless mode preserves the lower height band; scored mode requires '
                 'a locally observed finite cylinder surface or nearby frozen observation'),
    }
    if lower_band is None and not reliable_segments and not len(external_observed):
        report['skippedReason'] = 'no reliable lower-band or finite-cylinder evidence'
        return removed, report

    eligible_ids = np.flatnonzero(eligible)
    spatially_supported = np.zeros(len(eligible_ids), bool)
    if scores is None and lower_band is not None and len(eligible_ids):
        z = points[eligible_ids, 2]
        spatially_supported |= ((z >= lower_band[0] - lower_margin) &
                                (z <= lower_band[1] + lower_margin))
    outside_ids = eligible_ids[~spatially_supported]
    if len(outside_ids) and reliable_segments:
        if scores is None:
            spatially_supported[~spatially_supported] = _cylinder_support(
                points[outside_ids], reliable_segments, support_margin=support_margin,
                sample_spacing=sample_spacing, workers=workers)
        else:
            margins = _score_surface_margins(scores[outside_ids], support_margin)
            spatially_supported[~spatially_supported] = _surface_support(
                points[outside_ids], reliable_segments, margins,
                sample_spacing=sample_spacing, workers=workers)
            hard_slots = np.flatnonzero(hard_protected[eligible_ids])
            if len(hard_slots):
                spatially_supported[hard_slots] |= _cylinder_support(
                    points[eligible_ids[hard_slots]], reliable_segments,
                    support_margin=support_margin, sample_spacing=sample_spacing,
                    workers=workers)

    if scores is not None and len(eligible_ids):
        observed_ids = np.flatnonzero(hard_protected)
        observed = points[observed_ids]
        if len(external_observed):
            observed = np.vstack((observed, external_observed))
        if len(observed):
            unsupported_slots = np.flatnonzero(
                ~spatially_supported & ~hard_protected[eligible_ids])
            if len(unsupported_slots):
                distances = cKDTree(observed).query(
                    points[eligible_ids[unsupported_slots]], k=1, workers=workers)[0]
                spatially_supported[unsupported_slots[distances <= OBSERVED_SUPPORT_RADIUS]] = True

        # With a known lower height but no measured type-1 model, only isolated
        # points far from every frozen observation are strong enough negatives.
        has_lower_model = any(segment[3]['type'] == 1 for segment in reliable_segments)
        if lower_band is not None and not has_lower_model:
            z = points[eligible_ids, 2]
            lower_slots = np.flatnonzero((z >= lower_band[0] - lower_margin) &
                                         (z <= lower_band[1] + lower_margin) &
                                         ~spatially_supported)
            if len(lower_slots):
                counts = cKDTree(points[eligible_ids]).query_ball_point(
                    points[eligible_ids[lower_slots]], LOCAL_SUPPORT_RADIUS,
                    return_length=True, workers=workers)
                spatially_supported[lower_slots[counts >= 2]] = True

    unsupported = eligible_ids[~spatially_supported]
    blocked = unsupported[hard_protected[unsupported]]
    removable = unsupported[~hard_protected[unsupported]]
    removed[removable] = True
    in_lower = np.zeros(len(points), bool) if lower_band is None else (eligible &
        (points[:, 2] >= lower_band[0] - lower_margin) & (points[:, 2] <= lower_band[1] + lower_margin))
    report['outsideLowerBandPointCount'] = int(np.count_nonzero(eligible & ~in_lower))
    report['lowerBandReviewedPointCount'] = int(np.count_nonzero(in_lower & review))
    report['lowerBandRemovedPointCount'] = int(np.count_nonzero(in_lower & removed))
    report['scoreDecisions'] = ([] if scores is None else [
        {'band': name, 'candidatePoints': int(np.count_nonzero(eligible & mask)),
         'removedPoints': int(np.count_nonzero(removed & mask))}
        for name, mask in [('low', scores <= LOW_SCORE + SCORE_TOLERANCE),
                           ('medium', (scores > LOW_SCORE + SCORE_TOLERANCE) & (scores < HIGH_SCORE - SCORE_TOLERANCE)),
                           ('high', scores >= HIGH_SCORE - SCORE_TOLERANCE)]])
    report['spatiallySupportedPointCount'] = int(np.count_nonzero(spatially_supported))
    report['removedPointCount'] = int(len(removable))
    report['blockedRemovalPointCount'] = int(len(blocked))
    return removed, report
