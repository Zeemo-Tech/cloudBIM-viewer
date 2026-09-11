"""Conservative Step 05 denoising against observed structural support.

Source rows stay intact. A reviewable steel point becomes type 5 only when it
is outside the observed lower layer and every reliable, finite cylinder.
"""
import numpy as np
from scipy.spatial import cKDTree


VERSION = 'floating-support-v3-spatial-evidence'
HIGH_SCORE = .9
LOW_SCORE = .5
LOW_SCORE_SUPPORT_MARGIN = .006
SCORE_TOLERANCE = 1e-6


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


def floating_noise_mask(points, types, bands, segments, *, workers=1,
                        lower_margin=.003, support_margin=.012, sample_spacing=.004,
                        protected=None, steel_scores=None):
    """Return points that lack observed spatial steel support.

    Without ``steel_scores`` the legacy scope is retained: only type 4 is
    reviewed. With scores, types 1--4 below 0.9 are reviewed, including the
    ordinary single-route score (0.65); scores at least 0.9 are hard protected.
    A low score expands review scope but never removes a point that is close to
    a reliable finite cylinder or the observed lower height band.
    """
    points = np.asarray(points)
    types = np.asarray(types)
    if len(types) != len(points):
        raise ValueError('types 必须与点逐行对应')
    protected = (np.zeros(len(points), bool) if protected is None else
                 np.asarray(protected, dtype=bool))
    if protected.shape != (len(points),):
        raise ValueError('protected 必须与点逐行对应')

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
    support_types = (2, 3) if lower_band is not None else (1, 2, 3)
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
        'lowScoreSupportMarginM': min(support_margin, LOW_SCORE_SUPPORT_MARGIN),
        'reliableSegmentCount': len(reliable_segments),
        'priorOnlyRejectedSegmentCount': int(prior_only_count),
        'scope': ('unassigned inner steel only (legacy scoreless mode)' if scores is None else
                  'steel types 1-4 below fused score 0.9'),
        'rule': ('remove reviewed points outside the lower band and every reliable '
                 'finite observed cylinder; each cylinder uses its own radius'),
    }
    if lower_band is None and not reliable_segments:
        report['skippedReason'] = 'no reliable lower-band or finite-cylinder evidence'
        return removed, report

    eligible_ids = np.flatnonzero(eligible)
    spatially_supported = np.zeros(len(eligible_ids), bool)
    if lower_band is not None and len(eligible_ids):
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
            outside_slots = np.flatnonzero(~spatially_supported)
            low = scores[outside_ids] <= LOW_SCORE + SCORE_TOLERANCE
            low_slots = outside_slots[low]
            regular_slots = outside_slots[~low]
            if len(low_slots):
                spatially_supported[low_slots] = _cylinder_support(
                    points[eligible_ids[low_slots]], reliable_segments,
                    support_margin=min(support_margin, LOW_SCORE_SUPPORT_MARGIN),
                    sample_spacing=sample_spacing, workers=workers)
            if len(regular_slots):
                spatially_supported[regular_slots] = _cylinder_support(
                    points[eligible_ids[regular_slots]], reliable_segments,
                    support_margin=support_margin, sample_spacing=sample_spacing,
                    workers=workers)

    unsupported = eligible_ids[~spatially_supported]
    blocked = unsupported[hard_protected[unsupported]]
    removable = unsupported[~hard_protected[unsupported]]
    removed[removable] = True
    report['outsideLowerBandPointCount'] = int(len(outside_ids))
    report['spatiallySupportedPointCount'] = int(np.count_nonzero(spatially_supported))
    report['removedPointCount'] = int(len(removable))
    report['blockedRemovalPointCount'] = int(len(blocked))
    return removed, report
