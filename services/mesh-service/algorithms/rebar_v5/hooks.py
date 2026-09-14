"""Observed, endpoint-constrained recovery of planar reinforcing-bar U-hooks."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class _HookFit:
    anchor: np.ndarray
    outward: np.ndarray
    up: np.ndarray
    normal: np.ndarray
    radius: float
    bar_radius: float
    theta_bins: int
    occupied: np.ndarray
    support_rows: np.ndarray
    score: tuple[float, float, float, float, float]


def _unit(value):
    value = np.asarray(value, dtype=float)
    length = float(np.linalg.norm(value))
    return value / length if np.isfinite(length) and length > 1e-12 else None


def _parent_axis(item):
    """Return the dominant horizontal edge; curved tail edges cannot rotate it."""
    line = np.asarray(item.get("centerline", ()), dtype=float)
    if line.ndim != 2 or line.shape[1:] != (3,) or len(line) < 2:
        return None
    edges = np.diff(line, axis=0)
    horizontal = edges.copy()
    horizontal[:, 2] = 0.0
    lengths = np.linalg.norm(horizontal, axis=1)
    if not len(lengths) or float(lengths.max(initial=0.0)) <= 1e-9:
        return None
    axis = _unit(horizontal[int(np.argmax(lengths))])
    if axis is None:
        return None
    axial = line @ axis
    return axis, line[int(np.argmin(axial))], line[int(np.argmax(axial))]


def _runs(occupied):
    """Return inclusive consecutive occupied-bin runs without bridging holes."""
    rows = np.flatnonzero(occupied)
    if not len(rows):
        return []
    return [part for part in np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1) if len(part)]


def _trim_parent_to_anchor(parent, fit, p):
    """Shorten the attached straight observation to the fitted physical bend."""
    candidates = []
    for path_index, path in enumerate(parent.get("observedSegments", ())):
        points = np.asarray(path.get("points", ()), dtype=float)
        if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2:
            continue
        for endpoint_index, neighbour_index in ((0, 1), (-1, -2)):
            endpoint, neighbour = points[endpoint_index], points[neighbour_index]
            toward = _unit(endpoint-neighbour)
            if toward is None or float(toward @ fit.outward) < math.cos(math.radians(12.0)):
                continue
            edge_length = float(np.linalg.norm(endpoint-neighbour))
            delta = fit.anchor-neighbour
            axial = float(delta @ toward)
            lateral = float(np.linalg.norm(delta-axial*toward))
            if (lateral <= fit.bar_radius+float(p.support_distance)
                    and -float(p.support_distance) <= axial <= edge_length+float(p.support_distance)):
                candidates.append((float(np.linalg.norm(endpoint-fit.anchor)), path_index,
                                   endpoint_index, endpoint.copy()))
    if not candidates:
        return 0.0
    _, path_index, endpoint_index, old_endpoint = min(candidates)
    path_points = np.asarray(parent["observedSegments"][path_index]["points"], dtype=float)
    path_points[endpoint_index] = fit.anchor
    # The raw verifier extends edges longer than 200 mm by axial_gap.  On a
    # terminal bend that would rediscover the surface projection which caused
    # the overshoot.  Subdivide the same observed path (without changing its
    # list position or count) so every edge is verified only on its finite span.
    subdivided = [path_points[0]]
    for start, end in zip(path_points[:-1], path_points[1:]):
        count = max(1, int(math.ceil(np.linalg.norm(end-start)/.18)))
        subdivided.extend(start+(end-start)*step/count for step in range(1, count+1))
    parent["observedSegments"][path_index]["points"] = np.asarray(subdivided).tolist()

    line = np.asarray(parent.get("centerline", ()), dtype=float)
    if line.ndim == 2 and line.shape[1:] == (3,) and len(line) >= 2:
        line_endpoint = 0 if np.linalg.norm(line[0]-old_endpoint) <= np.linalg.norm(line[-1]-old_endpoint) else -1
        line[line_endpoint] = fit.anchor
        parent["centerline"] = line.tolist()
    return float(np.linalg.norm(old_endpoint-fit.anchor))


def _arc_fit(points, terminal, outward, vertical_sign, bar_radius, p):
    up = np.array([0.0, 0.0, float(vertical_sign)])
    normal = _unit(np.cross(outward, up))
    if normal is None:
        return None

    fit_step = max(float(p.detection_voxel_size), 0.002)
    fit_tolerance = max(float(p.fixture_fit_distance), 0.75 * fit_step)
    anchor_limit = max(2.0 * float(p.axis_radius), 5.0 * bar_radius)
    minimum_radius = max(2.0 * bar_radius, 0.012)
    maximum_radius = max(4.0 * float(p.axis_radius), 2.5 * float(p.layer_gap), minimum_radius + fit_step)
    maximum_radius = min(maximum_radius, 0.16)

    relative = np.asarray(points, dtype=float) - terminal
    terminal_u = relative @ outward
    vertical = relative @ up
    transverse = relative @ normal
    # A physical hook stays in the parent's vertical plane.  This gate separates
    # the observed 6 mm hook from a parallel 6 mm bar whose axis is 16 mm away.
    plane_gate = bar_radius + fit_tolerance
    return_reach = max(2.0 * float(p.join_gap), 4.0 * maximum_radius)
    local = ((np.abs(transverse) <= plane_gate)
             & (terminal_u >= -return_reach-anchor_limit-bar_radius)
             & (terminal_u <= maximum_radius+bar_radius+fit_tolerance)
             & (np.abs(vertical) <= 2.0*maximum_radius+bar_radius+fit_tolerance))
    local_rows = np.flatnonzero(local)
    if len(local_rows) < int(p.min_primitive_votes):
        return None
    if len(local_rows) > int(p.neighbourhood_point_limit):
        raise ValueError("V5 terminal hook neighbourhood exceeds point budget")
    tu, w, v = terminal_u[local_rows], vertical[local_rows], transverse[local_rows]

    anchor_values = np.arange(0.0, anchor_limit + 0.5*fit_step, fit_step)
    radius_values = np.arange(minimum_radius, maximum_radius + 0.5*fit_step, fit_step)
    best = None
    for anchor_shift in anchor_values:
        u = tu + anchor_shift
        for radius in radius_values:
            radial = np.hypot(u, w-radius)
            distance = np.hypot(radial-radius, v)
            theta = np.arctan2(u, radius-w)
            theta_bins = max(12, int(math.ceil(math.pi*radius/fit_step)))
            supported = ((theta >= 0.0) & (theta <= math.pi)
                         & (np.abs(distance-bar_radius) <= fit_tolerance))
            if int(supported.sum()) < max(int(p.min_primitive_votes), theta_bins):
                continue
            bins = np.minimum(theta_bins-1,
                              np.floor(theta[supported] / math.pi * theta_bins).astype(int))
            counts = np.bincount(bins, minlength=theta_bins)
            occupied = counts > 0
            coverage = float(np.mean(occupied))
            runs = _runs(occupied)
            longest = max((len(run) for run in runs), default=0) / theta_bins
            edge = max(1, int(math.ceil(theta_bins * 0.12)))
            if (coverage < 0.78 or longest < 0.72
                    or not occupied[:edge].any() or not occupied[-edge:].any()):
                continue
            supported_rows = local_rows[np.flatnonzero(supported)]
            broad = ((theta >= 0.0) & (theta <= math.pi)
                     & (distance <= max(3.0*bar_radius, bar_radius+4.0*fit_tolerance)))
            physical_loss = (float(np.quantile(np.abs(distance[broad]-bar_radius), .8))
                             if np.any(broad) else float("inf"))
            return_axial = -u
            return_distance = np.hypot(w-2.0*radius, v)
            return_support = ((return_axial >= 0.0) & (return_axial <= return_reach)
                              & (np.abs(return_distance-bar_radius) <= fit_tolerance))
            return_length = 0.0
            if np.any(return_support):
                return_bins = np.unique(np.floor(return_axial[return_support]/fit_step).astype(int))
                return_occupied = np.zeros(int(return_bins.max())+1, dtype=bool)
                return_occupied[return_bins] = True
                for run in _runs(return_occupied):
                    if run[0] <= 2:
                        return_length = float((run[-1]+1-run[0])*fit_step)
                        break
            # Prefer complete angular support, then the physical shell residual
            # across its broad neighbourhood.  Consensus density and the return
            # length only break comparably good physical fits.
            support_density = float(supported.sum()) / theta_bins
            score = (coverage, -physical_loss, support_density, return_length, -float(anchor_shift))
            if best is None or score > best.score:
                best = _HookFit(
                    anchor=terminal-outward*anchor_shift,
                    outward=outward.copy(), up=up, normal=normal,
                    radius=float(radius), bar_radius=float(bar_radius),
                    theta_bins=theta_bins, occupied=occupied,
                    support_rows=supported_rows, score=score,
                )
    return best


def _arc_segments(fit, p):
    """Create centreline runs only where every angular bin is observed."""
    segments = []
    for run in _runs(fit.occupied):
        if len(run) < 2:
            continue
        theta_lo, theta_hi = np.asarray([run[0], run[-1]+1])/fit.theta_bins*math.pi
        arc_length = fit.radius*(theta_hi-theta_lo)
        # Raw verification requires a finite connected edge of at least this
        # length.  Equal subdivisions keep each chord above that threshold;
        # their sagitta stays far below support_distance at the fitted radii.
        minimum_edge = max(fit.bar_radius, .25*float(p.min_primitive_length))*1.05
        edge_count = int(math.floor(arc_length/minimum_edge))
        if edge_count < 1:
            continue
        theta = np.linspace(theta_lo, theta_hi, edge_count+1)
        points = (fit.anchor
                  + (fit.radius*np.sin(theta))[:, None]*fit.outward
                  + (fit.radius*(1.0-np.cos(theta)))[:, None]*fit.up)
        segments.append({"points": points.tolist()})
    return segments


def _return_segments(points, fit, p):
    """Grow the reversed arm from the fitted arc end, splitting every raw gap."""
    top = fit.anchor + 2.0*fit.radius*fit.up
    relative = np.asarray(points, dtype=float)-top
    axial = relative @ (-fit.outward)
    vertical = relative @ fit.up
    transverse = relative @ fit.normal
    distance = np.hypot(vertical, transverse)
    step = max(float(p.detection_voxel_size), 0.002)
    tolerance = max(float(p.fixture_fit_distance), 0.75*step)
    reach = max(2.0*float(p.join_gap), 4.0*fit.radius)
    supported = ((axial >= 0.0) & (axial <= reach)
                 & (np.abs(distance-fit.bar_radius) <= tolerance)
                 & (np.abs(transverse) <= fit.bar_radius+tolerance))
    rows = np.flatnonzero(supported)
    if len(rows) < int(p.min_primitive_votes):
        return [], np.empty(0, dtype=np.intp), 0.0
    bins = np.floor(axial[rows]/step).astype(int)
    occupied_ids = np.unique(bins)
    # Association requires observed support immediately beside the predicted
    # pi endpoint.  A remote collinear bar cannot start a return arm.
    if not np.any(occupied_ids <= 2):
        return [], np.empty(0, dtype=np.intp), 0.0
    occupied = np.zeros(int(occupied_ids.max())+1, dtype=bool)
    occupied[occupied_ids] = True
    result = []
    minimum_bins = max(3, int(math.ceil(max(2.0*fit.bar_radius, 0.012)/step)))
    for run in _runs(occupied):
        if run[0] > 2 or len(run) < minimum_bins:
            continue
        positions = np.asarray([run[0], run[-1]+1])*step
        line = top + positions[:, None]*(-fit.outward)
        result.append({"points": line.tolist()})
        selected_rows = rows[np.isin(bins, run)]
        observed_length = float((run[-1]+1-run[0])*step)
        return result, selected_rows, observed_length
    return result, np.empty(0, dtype=np.intp), 0.0


def recover_terminal_hooks(points, _features, p, planar, _layers):
    """Attach only fully observed endpoint-constrained 180-degree U-hooks.

    Surface PCA tangents are deliberately not used: on a dense torus they vary
    across the tube section.  The fitted curve is accepted by raw tube-shell
    support in angular bins and each exported observed segment is split at every
    unsupported bin.
    """
    result = deepcopy(planar)
    diagnostic = {"candidateCount": 0, "attachedCount": 0, "rejected": {}}
    cloud = np.asarray(points, dtype=float)
    if cloud.ndim != 2 or cloud.shape[1:] != (3,) or not len(cloud):
        return result, diagnostic
    finite = np.isfinite(cloud).all(axis=1)
    cloud = cloud[finite]
    if not len(cloud):
        return result, diagnostic

    for parent_ordinal, parent in enumerate(result):
        parent_axis = _parent_axis(parent)
        if parent_axis is None:
            continue
        axis, low, high = parent_axis
        bar_radius = float(np.clip(parent.get("radius", p.min_radius), p.min_radius, p.max_radius))
        for terminal_ordinal, (terminal, outward) in enumerate(((low, -axis), (high, axis))):
            candidates = []
            for vertical_sign in (-1.0, 1.0):
                fit = _arc_fit(cloud, terminal, outward, vertical_sign, bar_radius, p)
                if fit is not None:
                    diagnostic["candidateCount"] += 1
                    returns, return_rows, return_length = _return_segments(cloud, fit, p)
                    if returns:
                        candidates.append((fit, returns, return_rows, return_length))
            rejection_key = f"{parent_ordinal}:{terminal_ordinal}"
            if not candidates:
                diagnostic["rejected"][rejection_key] = "no-complete-supported-turn-and-return"
                continue
            fit, returns, return_rows, _ = max(candidates, key=lambda item: item[0].score)
            arc = _arc_segments(fit, p)
            if not arc:
                diagnostic["rejected"][rejection_key] = "supported-turn-has-no-contiguous-run"
                continue
            additions = arc + returns
            trimmed_length = _trim_parent_to_anchor(parent, fit, p)
            parent.setdefault("observedSegments", []).extend(additions)
            support_count = len(np.union1d(fit.support_rows, return_rows))
            parent["pointCount"] = int(parent.get("pointCount", 0) + support_count)
            parent["length"] = float(max(0.0, parent.get("length", 0.0)-trimmed_length) + sum(
                np.linalg.norm(np.diff(np.asarray(segment["points"], dtype=float), axis=0), axis=1).sum()
                for segment in additions))
            parent["evidence"] = "mixed" if parent.get("inferredSegments") else "observed"
            parent["associationPending"] = True
            diagnostic["attachedCount"] += 1
            fit_tolerance = max(float(p.fixture_fit_distance), .75*float(p.detection_voxel_size))
            physical_loss = max(0.0, -fit.score[1])
            diagnostic.setdefault("fits", []).append({
                "parentOrdinal": int(parent_ordinal),
                "terminalOrdinal": int(terminal_ordinal),
                "radius": fit.radius,
                "angularCoverage": fit.score[0],
                "shellResidualP80": physical_loss,
                "confidence": float(np.clip(fit.score[0]*(1.0-physical_loss/max(fit_tolerance, 1e-12)), 0., 1.)),
                "supportCount": int(support_count),
                "anchor": fit.anchor.tolist(),
            })
    return result, diagnostic
