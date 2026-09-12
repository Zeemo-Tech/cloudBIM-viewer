"""Exact-boundary orthogonal footprint for the straight rebar cage."""
from __future__ import annotations

from typing import Any
import numpy as np


def _axes(bars: list[tuple[np.ndarray, float]]) -> np.ndarray:
    votes = 0j
    for delta, weight in bars:
        if np.linalg.norm(delta[:2]) < 1e-9: continue
        angle = np.arctan2(delta[1], delta[0]); votes += weight*np.exp(4j*angle)
    angle = .25*np.angle(votes) if abs(votes) > 1e-10 else 0.
    first = np.array([np.cos(angle), np.sin(angle)])
    # Stable sign makes equivalent translated inventories serialize identically.
    if first[np.argmax(np.abs(first))] < 0: first = -first
    return np.vstack((first, [-first[1], first[0]]))


def _nodes(low: float, high: float, exact: list[float], spacing: float) -> np.ndarray:
    count = int(np.floor((high-low)/spacing + 1e-12))
    regular = low + np.arange(count+1)*spacing  # Never extend a cell beyond the domain.
    return np.unique(np.round(np.r_[low, high, regular, exact], 9))


def build_orthogonal_footprint(body_inventory: dict[str, Any], lateral_clearance_m: float = .005,
                               external_length_tolerance_m: float = .012, target_spacing_m: float = .008,
                               max_cells: int = 60000) -> dict[str, Any]:
    """Union physical segment rectangles then close only orthogonal cage gaps."""
    values = (lateral_clearance_m, external_length_tolerance_m, target_spacing_m)
    if any(not np.isfinite(v) or v < 0 for v in values) or target_spacing_m <= 0 or max_cells < 1:
        raise ValueError("invalid footprint parameters")
    raw: list[tuple[np.ndarray, np.ndarray, float, bool, bool]] = []
    direction_votes = []
    origins = []
    for bar in body_inventory.get("bars", []):
        if not isinstance(bar, dict) or bar.get("coverage") == "unresolved": continue
        try: points = np.asarray(bar.get("points", []), float); radius = float(bar.get("radiusM", bar.get("radius", 0)))
        except (TypeError, ValueError): continue
        if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2 or not np.isfinite(points).all() or not np.isfinite(radius) or radius <= 0: continue
        origins.append(points[:, :2])
        for index, (start, end) in enumerate(zip(points[:-1], points[1:])):
            delta = end-start; length = np.linalg.norm(delta)
            if length <= 1e-9: continue
            raw.append((start, end, radius, index == 0, index == len(points)-2))
            # Long predominantly planar runs establish the four-fold frame.
            if length >= .10 and abs(delta[2])/length < .25: direction_votes.append((delta, length))
    if not raw: return {"xyOriginM": [0., 0.], "xyAxes": [[1.,0.],[0.,1.]], "gridXM": [], "gridYM": [], "activeCells": [], "footprintLoopsLocalM": [], "params": {"empty": True}}
    origin = np.vstack(origins).mean(axis=0); axes = _axes(direction_votes or [(end-start, np.linalg.norm(end-start)) for start,end,*_ in raw])
    rectangles = []
    for start, end, radius, first, last in raw:
        local = (np.vstack((start[:2], end[:2]))-origin) @ axes.T
        vector3 = end-start; length = np.linalg.norm(vector3); tangent = vector3/length
        # Cylinder projection radius by each orthogonal coordinate, plus the
        # requested 5 mm lateral surface clearance.
        expansion = radius*np.sqrt(np.maximum(0., 1-(axes @ tangent[:2])**2)) + lateral_clearance_m
        vector = local[1]-local[0]; xy_length = np.linalg.norm(vector)
        if xy_length > 1e-10:
            direction = vector/xy_length
            # The axis-aligned physical rectangle already reaches along the
            # bar direction.  Extend only the remaining allowance, so an end
            # is 12 mm total rather than 12 mm plus the 5 mm lateral sheath.
            remaining = max(0., external_length_tolerance_m-float(np.abs(direction) @ expansion))
            if first: local[0] -= remaining*direction
            if last: local[1] += remaining*direction
        rectangles.append(tuple(np.round((np.minimum(local[0], local[1])-expansion,
                                          np.maximum(local[0], local[1])+expansion), 9)))
    low = np.min(np.vstack([r[0] for r in rectangles]), axis=0); high = np.max(np.vstack([r[1] for r in rectangles]), axis=0)
    exact_x = [value for rect_low, rect_high in rectangles for value in (rect_low[0], rect_high[0])]
    exact_y = [value for rect_low, rect_high in rectangles for value in (rect_low[1], rect_high[1])]
    spacing = target_spacing_m
    while True:
        xs, ys = _nodes(low[0], high[0], exact_x, spacing), _nodes(low[1], high[1], exact_y, spacing)
        if (len(xs)-1)*(len(ys)-1) <= max_cells: break
        spacing *= 1.5
        if spacing > max(high-low)*2: raise ValueError("exact footprint boundary budget exceeded")
    active = np.zeros((len(ys)-1, len(xs)-1), bool)
    for rect_low, rect_high in rectangles:
        xx = (xs[:-1] >= rect_low[0]-1e-12) & (xs[1:] <= rect_high[0]+1e-12)
        yy = (ys[:-1] >= rect_low[1]-1e-12) & (ys[1:] <= rect_high[1]+1e-12)
        active |= yy[:, None] & xx[None, :]
    # Orthogonal closure preserves an L concavity while bridging cage interior
    # only where both ends in a row/column are supported.
    changed = True
    while changed:
        changed = False
        for row in active:
            ids = np.flatnonzero(row)
            if len(ids) > 1 and not row[ids[0]:ids[-1]+1].all(): row[ids[0]:ids[-1]+1] = True; changed = True
        for column in active.T:
            ids = np.flatnonzero(column)
            if len(ids) > 1 and not column[ids[0]:ids[-1]+1].all(): column[ids[0]:ids[-1]+1] = True; changed = True
    return {"xyOriginM": origin.tolist(), "xyAxes": axes.tolist(), "gridXM": xs.tolist(), "gridYM": ys.tolist(),
            "activeCells": active.tolist(), "footprintLoopsLocalM": [],
            "params": {"lateralClearanceM": lateral_clearance_m, "externalLengthToleranceM": external_length_tolerance_m,
                       "targetSpacingM": target_spacing_m, "effectiveSpacingM": spacing, "maxCells": max_cells,
                       "rectangleCount": len(rectangles)}}
