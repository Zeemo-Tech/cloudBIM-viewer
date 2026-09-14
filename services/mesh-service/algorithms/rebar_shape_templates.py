"""Canonical, dimension-only shape templates for design rebar units.

The inventory keeps one physical polyline per parent bar and separate straight
``units`` for the long runs.  A template assigns every piece of that physical
polyline to exactly one unit.  Gaps between adjacent runs are split at their
arc-length midpoint; terminal material belongs to its neighbouring run.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-8


def _points(value):
    """Return a finite, de-duplicated centerline or ``None``."""
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if result.ndim != 2 or result.shape[1] != 3 or len(result) < 2 or not np.isfinite(result).all():
        return None
    keep = np.r_[True, np.linalg.norm(np.diff(result, axis=0), axis=1) > _EPS]
    result = result[keep]
    return result if len(result) >= 2 else None


def _line_point(unit):
    try:
        start = np.asarray(unit['startM'], dtype=float)
        end = np.asarray(unit['endM'], dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    if start.shape != (3,) or end.shape != (3,) or not np.isfinite(start).all() or not np.isfinite(end).all():
        return None
    length = float(np.linalg.norm(end - start))
    return start, end, length


def _station(points, stations, point):
    """Closest arc station, including the exact projection on a polyline edge."""
    delta = np.diff(points, axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    offset = point - points[:-1]
    t = np.clip(np.einsum('ij,ij->i', offset, delta) / (lengths * lengths), 0., 1.)
    projected = points[:-1] + delta * t[:, None]
    index = int(np.argmin(np.einsum('ij,ij->i', projected - point, projected - point)))
    return float(stations[index] + lengths[index] * t[index])


def _slice(points, stations, left, right):
    """Extract the exact input polyline segment over a station interval."""
    left, right = float(left), float(right)
    if right < left:
        left, right = right, left
    def at(value):
        index = min(int(np.searchsorted(stations, value, side='right') - 1), len(points) - 2)
        index = max(index, 0)
        span = stations[index + 1] - stations[index]
        fraction = 0. if span <= _EPS else (value - stations[index]) / span
        return points[index] + (points[index + 1] - points[index]) * fraction
    rows = [at(left)]
    rows.extend(point for station, point in zip(stations[1:-1], points[1:-1]) if left < station < right)
    rows.append(at(right))
    return np.asarray(rows)


def _local_frame(points, origin, axis):
    """Use the first off-axis point to make a rigid-transform invariant frame."""
    relative = points - origin
    x = relative @ axis
    transverse = relative - x[:, None] * axis
    lengths = np.linalg.norm(transverse, axis=1)
    nonzero = np.flatnonzero(lengths > _EPS)
    if len(nonzero):
        return transverse[nonzero[0]] / lengths[nonzero[0]]
    return None


def _localize(points, origin, axis, y_axis):
    relative = points - origin
    x = relative @ axis
    if y_axis is None:
        y = z = np.zeros(len(points))
    else:
        y = relative @ y_axis
        z = relative @ np.cross(axis, y_axis)
    return np.column_stack((x, y, z))


def _straight_template(unit):
    line = _line_point(unit)
    if line is None:
        return None
    start, end, length = line
    if length <= _EPS:
        return None
    return {'localCenterlineM': [[0., 0., 0.], [length, 0., 0.]],
            'straightLengthM': length, 'shapeLengthM': length,
            'startTailM': [], 'endTailM': [], 'hasCurves': False}


def build_unit_shape_templates(inventory):
    """Build templates keyed by ``designUnitId`` from a design inventory.

    Missing parent geometry deliberately falls back to the unit's straight
    endpoints. Invalid units are omitted. Tail arrays follow the unit's
    start-to-end ordering and include their straight-run join.
    """
    if not isinstance(inventory, dict):
        return {}
    units = [u for u in inventory.get('units', []) if isinstance(u, dict) and u.get('designUnitId')]
    by_bar = {}
    for unit in units:
        by_bar.setdefault(str(unit.get('designBarId')), []).append(unit)
    bars = {str(bar.get('designBarId')): bar for bar in inventory.get('bars', []) if isinstance(bar, dict)}
    templates = {}
    for bar_id, group in by_bar.items():
        points = _points(bars.get(bar_id, {}).get('points', []))
        valid = [(unit, _line_point(unit)) for unit in group]
        if points is None:
            for unit, _ in valid:
                template = _straight_template(unit)
                if template is not None:
                    templates[unit['designUnitId']] = template
            continue
        stations = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        positioned = []
        for unit, line in valid:
            if line is None or line[2] <= _EPS:
                continue
            start, end, length = line
            start_s = _station(points, stations, start)
            end_s = _station(points, stations, end)
            positioned.append((unit, start, end, length, start_s, end_s))
        # Units that lack usable endpoints cannot describe a template at all.
        positioned.sort(key=lambda row: (min(row[4], row[5]), max(row[4], row[5]), str(row[0]['designUnitId'])))
        for index, (unit, start, end, length, start_s, end_s) in enumerate(positioned):
            lo, hi = sorted((start_s, end_s))
            previous_hi = max(positioned[index - 1][4:6]) if index else 0.
            own_lo = (lo + previous_hi) / 2. if index else 0.
            next_lo = min(positioned[index + 1][4:6]) if index + 1 < len(positioned) else stations[-1]
            own_hi = stations[-1] if index + 1 == len(positioned) else (hi + next_lo) / 2.
            # Degenerate or overlapping inventory runs are safer as straight templates.
            if own_lo > lo + _EPS or own_hi < hi - _EPS:
                template = _straight_template(unit)
                if template is not None:
                    templates[unit['designUnitId']] = template
                continue
            owned = _slice(points, stations, own_lo, own_hi)
            if end_s < start_s:
                owned = owned[::-1]
            axis = (end - start) / length
            y_axis = _local_frame(owned, start, axis)
            local = _localize(owned, start, axis, y_axis)
            left_tail = _slice(points, stations, own_lo, lo) if lo - own_lo > _EPS else np.empty((0, 3))
            right_tail = _slice(points, stations, hi, own_hi) if own_hi - hi > _EPS else np.empty((0, 3))
            start_tail = left_tail if start_s <= end_s else right_tail[::-1]
            end_tail = right_tail if start_s <= end_s else left_tail[::-1]
            start_local = _localize(start_tail, start, axis, y_axis).tolist() if len(start_tail) else []
            end_local = _localize(end_tail, start, axis, y_axis).tolist() if len(end_tail) else []
            shape_length = float(np.linalg.norm(np.diff(owned, axis=0), axis=1).sum())
            has_curves = bool(np.any(np.linalg.norm(local[:, 1:], axis=1) > _EPS))
            templates[unit['designUnitId']] = {'localCenterlineM': local.tolist(),
                'straightLengthM': length, 'shapeLengthM': shape_length,
                'startTailM': start_local, 'endTailM': end_local, 'hasCurves': has_curves}
        for unit, _ in valid:
            templates.setdefault(unit['designUnitId'], _straight_template(unit))
    return {key: value for key, value in templates.items() if value is not None}
