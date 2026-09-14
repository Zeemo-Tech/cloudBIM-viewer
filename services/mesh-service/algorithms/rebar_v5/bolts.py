"""Conservative, fixture-bounded bolt candidates for the V5 scene stage."""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree


MAX_CANDIDATE_POINTS = 50_000


def _feature(features: dict[str, Any], name: str, count: int, default: np.ndarray) -> np.ndarray:
    value = np.asarray(features.get(name, default))
    return value if len(value) == count else default


def _face_data(face: dict[str, Any]):
    try:
        origin = np.asarray(face["origin"], dtype=float)
        normal = np.asarray(face["normal"], dtype=float)
        axes = np.asarray(face["axes"], dtype=float)
        extent = np.asarray(face["halfExtent"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    length = np.linalg.norm(normal)
    if origin.shape != (3,) or normal.shape != (3,) or axes.shape != (2, 3) or extent.shape != (2,) or length <= 1e-9:
        return None
    return origin, normal / length, axes, extent


def _components(points: np.ndarray, radius: float, maximum: int = MAX_CANDIDATE_POINTS) -> list[np.ndarray]:
    """Bounded components. Exceeding the explicit budget is never truncated."""
    if len(points) > maximum:
        raise ValueError(f"bolt candidate budget exceeded: {len(points)} > {maximum}")
    if not len(points):
        return []
    tree = cKDTree(points)
    seen = np.zeros(len(points), dtype=bool)
    groups: list[np.ndarray] = []
    for start in range(len(points)):
        if seen[start]:
            continue
        seen[start] = True
        todo = deque([start])
        group: list[int] = []
        while todo:
            item = todo.popleft()
            group.append(item)
            for neighbour in tree.query_ball_point(points[item], radius):
                if not seen[neighbour]:
                    seen[neighbour] = True
                    todo.append(int(neighbour))
        groups.append(np.asarray(group, dtype=int))
    return groups


def _pending(diagnostic: dict[str, Any], reason: str, fixture_index: int, support_count: int) -> None:
    diagnostic["pending"].append({"reason": reason, "fixtureIndex": fixture_index, "shaftSupportCount": int(support_count)})


def _fit_circle(local: np.ndarray, minimum: float, maximum: float):
    """Return a bounded physical circle or ``None`` for unsupported support.

    Bounds follow the candidate's own coordinates.  A fixed world/local ±30 mm
    box made an otherwise valid initial median infeasible in large scans.
    """
    local = np.asarray(local, dtype=float)
    if local.ndim != 2 or local.shape[1] != 2 or len(local) < 3 or not np.isfinite(local).all():
        return None
    centre = np.median(local, axis=0)
    radius0 = float(np.median(np.linalg.norm(local - centre, axis=1)))
    epsilon = max(1e-9, (maximum - minimum) * 1e-8)
    lower = np.array([local[:, 0].min() - maximum, local[:, 1].min() - maximum, minimum])
    upper = np.array([local[:, 0].max() + maximum, local[:, 1].max() + maximum, maximum])
    if np.any(lower >= upper):
        return None
    initial = np.array([
        np.clip(centre[0], lower[0] + epsilon, upper[0] - epsilon),
        np.clip(centre[1], lower[1] + epsilon, upper[1] - epsilon),
        np.clip(radius0, lower[2] + epsilon, upper[2] - epsilon),
    ])
    try:
        fit = least_squares(
            lambda value: np.linalg.norm(local - value[:2], axis=1) - value[2],
            initial, bounds=(lower, upper), loss="soft_l1", f_scale=0.001, max_nfev=60,
        )
    except (ValueError, FloatingPointError, np.linalg.LinAlgError):
        return None
    if not fit.success or not np.isfinite(fit.x).all():
        return None
    residuals = np.abs(np.linalg.norm(local - fit.x[:2], axis=1) - fit.x[2])
    return fit.x[:2], float(fit.x[2]), float(np.quantile(residuals, 0.8)), True


def _shaft_cells(local: np.ndarray, axial: np.ndarray, radius: float):
    grid = 0.004
    angles = np.mod(np.arctan2(local[:, 1], local[:, 0]), 2 * np.pi)
    bins = 24
    cells = np.column_stack((np.floor(axial / grid).astype(int), np.floor(angles / (2 * np.pi) * bins).astype(int)))
    return grid, bins, [tuple(item) for item in np.unique(cells, axis=0).tolist()]


def _head_plane(points: np.ndarray, axis: np.ndarray, expected: float, tolerance: float):
    centre = np.mean(points, axis=0)
    _, _, vectors = np.linalg.svd(points - centre, full_matrices=False)
    normal = vectors[-1]
    if abs(float(normal @ axis)) < math.cos(math.radians(15)):
        return None
    residual = float(np.quantile(np.abs((points - centre) @ normal), 0.8))
    if residual > tolerance:
        return None
    return centre, normal, residual


def detect_bolts(points, features, fixtures, p):
    """Confirm only headed short cylinders beside a *finite* fixture face.

    Both normal signs are evaluated because stored fixture normals have no stable
    outward convention. Returned models retain only actual observed surface cells.
    """
    cloud = np.asarray(points, dtype=float)
    diagnostic: dict[str, Any] = {"candidateCount": 0, "pending": []}
    models: list[dict[str, Any]] = []
    if cloud.ndim != 2 or cloud.shape[1] != 3 or not len(cloud):
        return models, diagnostic
    finite = np.isfinite(cloud).all(axis=1)
    tangent = _feature(features, "axis_tangent", len(cloud), np.zeros((len(cloud), 3)))
    linearity = _feature(features, "axis_linearity", len(cloud), np.zeros(len(cloud)))
    if tangent.shape != (len(cloud), 3):
        tangent = np.zeros((len(cloud), 3))
    shaft_radius_max = min(float(getattr(p, "max_radius", .012)), .010)
    shaft_radius_min = max(float(getattr(p, "min_radius", .0025)), .0015)
    surface_tolerance = max(float(getattr(p, "support_distance", .004)), .0015)
    axis_limit, candidate_distance = float(getattr(p, "bolt_max_length", .080)), max(float(getattr(p, "bolt_max_length", .080)), float(getattr(p, "bolt_candidate_distance", .20)))
    tangent_length = np.linalg.norm(tangent, axis=1)
    valid_tangent = tangent_length > 1e-9
    normalized_tangent = np.zeros_like(tangent, dtype=float)
    normalized_tangent[valid_tangent] = tangent[valid_tangent] / tangent_length[valid_tangent, None]
    all_cloud, all_finite, all_linearity = cloud, finite, linearity
    all_valid_tangent, all_normalized_tangent = valid_tangent, normalized_tangent
    from .candidates import PointBoundsIndex
    point_index = PointBoundsIndex(cloud) if len(fixtures or []) > 4 else None

    for fixture_index, face in enumerate(fixtures or []):
        parsed = _face_data(face)
        if parsed is None:
            continue
        origin, face_normal, axes, extent = parsed
        bounds = _fixture_candidate_bounds(origin, face_normal, axes, extent,
            max(candidate_distance, surface_tolerance) + max(.006, surface_tolerance * 1.5))
        if point_index is not None and bounds is not None:
            candidate_rows = point_index.query(*bounds)
            cloud, finite = all_cloud[candidate_rows], all_finite[candidate_rows]
            linearity = all_linearity[candidate_rows]
            valid_tangent = all_valid_tangent[candidate_rows]
            normalized_tangent = all_normalized_tangent[candidate_rows]
        else:
            cloud, finite, linearity = all_cloud, all_finite, all_linearity
            valid_tangent, normalized_tangent = all_valid_tangent, all_normalized_tangent
        local3 = cloud - origin
        local2 = np.column_stack((local3 @ axes[0], local3 @ axes[1]))
        within_face = np.all(np.abs(local2) <= extent + .012, axis=1)
        for direction_sign in (1., -1.):
            axis = face_normal * direction_sign
            signed = local3 @ axis
            aligned = np.zeros(len(cloud), dtype=bool)
            aligned[valid_tangent] = np.abs(normalized_tangent[valid_tangent] @ axis) >= math.cos(math.radians(22))
            shaft_rows = np.flatnonzero(finite & within_face & (signed >= -surface_tolerance) & (signed <= candidate_distance) & aligned & (linearity >= .45))
            for component in _components(cloud[shaft_rows], max(shaft_radius_max * 2.5, .010)):
                rows = shaft_rows[component]
                if len(rows) < 12:
                    continue
                diagnostic["candidateCount"] += 1
                height = signed[rows]
                lo, hi = float(np.quantile(height, .03)), float(np.quantile(height, .97))
                length = hi - lo
                circle = _fit_circle(local2[rows], shaft_radius_min, shaft_radius_max)
                if circle is None:
                    _pending(diagnostic, "short-cylinder-circle-fit-unsupported", fixture_index, len(rows))
                    continue
                centre2, radius, circle_residual, circle_ok = circle
                if not circle_ok or circle_residual > max(.0015, surface_tolerance * .75) or length < .008 or length > axis_limit:
                    _pending(diagnostic, "short-cylinder-geometry-insufficient", fixture_index, len(rows))
                    continue
                # Fitted physical centre must still lie inside this finite face.
                if np.any(np.abs(centre2) > extent + .002):
                    _pending(diagnostic, "short-cylinder-outside-finite-fixture-face", fixture_index, len(rows))
                    continue
                if lo > max(.012, surface_tolerance * 2.5):
                    _pending(diagnostic, "short-cylinder-not-fixture-adjacent", fixture_index, len(rows))
                    continue
                radial_all = np.linalg.norm(local2 - centre2, axis=1)
                head_rows = np.flatnonzero(finite & within_face & (np.abs(signed - hi) <= max(.006, surface_tolerance * 1.5)) & (radial_all >= radius * 1.30) & (radial_all <= max(radius * 3.5, radius + .004)))
                if len(head_rows) < 10:
                    _pending(diagnostic, "short-cylinder-without-wider-observed-head", fixture_index, len(rows))
                    continue
                head_radius = float(np.quantile(radial_all[head_rows], .90))
                fitted_plane = _head_plane(cloud[head_rows], axis, hi, max(.0015, surface_tolerance * .75))
                if head_radius < radius * 1.35 or fitted_plane is None:
                    _pending(diagnostic, "short-cylinder-head-not-planar-and-wider", fixture_index, len(rows))
                    continue
                _, _, plane_residual = fitted_plane
                shaft_local = local2[rows] - centre2
                grid, angle_bins, shaft_observed = _shaft_cells(shaft_local, height, radius)
                head_grid = .003
                head_local = local2[head_rows] - centre2
                head_cells = [tuple(item) for item in np.unique(np.floor(head_local / head_grid).astype(int), axis=0).tolist()]
                confidence = float(np.clip(.45 + min(len(rows) / 120, .25) + min(len(head_rows) / 100, .20) + min((head_radius / radius - 1) / 3, .10) - plane_residual / .01, 0, .99))
                model = {
                    "id": len(models) + 1, "source": "fixture-bounded-headed-cylinder-v1", "fixtureIndex": fixture_index,
                    "axisOrigin": (origin + centre2[0] * axes[0] + centre2[1] * axes[1]).tolist(), "axis": axis.tolist(),
                    "radialAxes": axes.tolist(), "axialRange": [lo, hi], "shaftRadius": radius,
                    "shaft": {"axialGrid": grid, "angleBins": angle_bins, "observedCells": shaft_observed, "residualTolerance": max(.0015, surface_tolerance * .75)},
                    "head": {"axial": hi, "radius": head_radius, "planeTolerance": max(.0015, surface_tolerance * .75), "gridSize": head_grid, "occupiedCells": head_cells},
                    "support": {"shaftPointCount": int(len(rows)), "headPointCount": int(len(head_rows))}, "confidence": confidence,
                }
                # A face can be represented twice or normals can be duplicated; retain one local model.
                if not any(np.linalg.norm(np.asarray(existing["axisOrigin"]) - np.asarray(model["axisOrigin"])) < .003 and abs(abs(float(np.dot(existing["axis"], model["axis"]))) - 1) < 1e-6 for existing in models):
                    models.append(model)
    return models, diagnostic


def _fixture_candidate_bounds(origin, normal, axes, extent, depth):
    """Enclose both signed shafts and every possible head observation."""
    projection = np.vstack((normal, axes))
    try:
        inverse = np.linalg.inv(projection)
        condition = np.linalg.cond(projection)
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(inverse).all() or not np.isfinite(condition) or condition > 1e8:
        return None
    reach = np.abs(inverse) @ np.r_[depth, extent + .012]
    scale = max(1., float(np.max(np.abs(origin))), float(np.max(reach)))
    padding = max(1e-12, np.finfo(float).eps * scale * 32 * condition)
    return origin - reach - padding, origin + reach + padding


_CELL_DTYPE = np.dtype([("first", np.int64), ("second", np.int64)])


def _cells(value: Any) -> np.ndarray:
    cells = np.asarray(value, dtype=np.int64)
    if cells.size == 0:
        return np.empty((0, 2), dtype=np.int64)
    if cells.ndim != 2 or cells.shape[1] != 2:
        raise ValueError("bolt cells must be pairs")
    return cells


def _cell_membership(cells: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Query integer cell pairs in NumPy, after geometric range rejection."""
    if not len(cells) or not len(observed):
        return np.zeros(len(cells), dtype=bool)
    keys = np.ascontiguousarray(cells, dtype=np.int64).view(_CELL_DTYPE).ravel()
    known = np.ascontiguousarray(observed, dtype=np.int64).view(_CELL_DTYPE).ravel()
    return np.isin(keys, known)


def _shaft_cell_membership(axial: np.ndarray, local: np.ndarray, grid: float, angle_bins: int, observed: np.ndarray) -> np.ndarray:
    angle = np.mod(np.arctan2(local[:, 1], local[:, 0]), 2 * np.pi)
    cells = np.column_stack((np.floor(axial / grid).astype(np.int64), np.floor(angle / (2 * np.pi) * angle_bins).astype(np.int64)))
    return _cell_membership(cells, observed)


def _head_cell_membership(local: np.ndarray, grid: float, observed: np.ndarray) -> np.ndarray:
    return _cell_membership(np.floor(local / grid).astype(np.int64), observed)


def _mask_model(model: dict[str, Any]):
    """Decode a model without retaining views that could mutate caller data."""
    try:
        origin = np.asarray(model["axisOrigin"], dtype=float)
        axis = np.asarray(model["axis"], dtype=float)
        axes = np.asarray(model["radialAxes"], dtype=float)
        lo, hi = map(float, model["axialRange"])
        radius = float(model["shaftRadius"])
        shaft, head = model["shaft"], model["head"]
        grid, angle_bins = float(shaft["axialGrid"]), int(shaft["angleBins"])
        residual = float(shaft["residualTolerance"])
        shaft_cells = _cells(shaft["observedCells"])
        head_cells, head_grid = _cells(head["occupiedCells"]), float(head["gridSize"])
        head_axial, head_tolerance = float(head["axial"]), float(head["planeTolerance"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    axis_length = np.linalg.norm(axis)
    if (origin.shape != (3,) or axis.shape != (3,) or axes.shape != (2, 3)
            or not np.isfinite(np.concatenate((origin, axis, axes.ravel()))).all()
            or not np.isfinite([lo, hi, radius, grid, residual, head_grid, head_axial, head_tolerance]).all()
            or axis_length <= 1e-12 or grid <= 0 or head_grid <= 0 or angle_bins <= 0):
        return None
    return origin, axis / axis_length, axes, lo, hi, radius, grid, angle_bins, residual, shaft_cells, head_axial, head_tolerance, head_grid, head_cells


def _model_candidates(cloud: np.ndarray, finite_rows: np.ndarray, decoded):
    """Return geometrically plausible rows and local coordinates for one model."""
    origin, axis, axes, lo, hi, radius, grid, _, residual, _, head_axial, head_tolerance, head_grid, head_cells = decoded
    delta = cloud[finite_rows] - origin
    axial = delta @ axis
    local = np.column_stack((delta @ axes[0], delta @ axes[1]))
    radial = np.linalg.norm(local, axis=1)
    shaft = (axial >= lo - grid) & (axial <= hi + grid) & (np.abs(radial - radius) <= residual)
    # The observed head-cell box is a safe coarse range. Do not use head.radius:
    # real observed cells can legitimately extend beyond the fitted disk estimate.
    head = np.zeros(len(finite_rows), dtype=bool)
    if len(head_cells):
        lower = head_cells.min(axis=0) * head_grid
        upper = (head_cells.max(axis=0) + 1) * head_grid
        head = ((np.abs(axial - head_axial) <= head_tolerance)
                & np.all((local >= lower) & (local <= upper), axis=1))
    return shaft, head, axial, local


def _model_aabb(decoded):
    """Conservative world AABB for the shaft/head ranges, or ``None`` if unsafe."""
    origin, axis, axes, lo, hi, radius, grid, _, residual, _, head_axial, head_tolerance, head_grid, head_cells = decoded
    ranges = []
    if radius >= 0 and residual >= 0:
        reach = radius + residual
        ranges.append((np.array([lo - grid, -reach, -reach]), np.array([hi + grid, reach, reach])))
    if head_tolerance >= 0 and len(head_cells):
        # This is only a broad-phase box. Keep both neighbouring IEEE-754
        # values around a cell edge; exact membership still uses floor below.
        local_lower = head_cells.min(axis=0) * head_grid - 1e-12
        local_upper = (head_cells.max(axis=0) + 1) * head_grid + 1e-12
        ranges.append((
            np.array([head_axial - head_tolerance, *local_lower]),
            np.array([head_axial + head_tolerance, *local_upper]),
        ))
    if not ranges:
        return origin, origin
    lower = np.minimum.reduce([item[0] for item in ranges])
    upper = np.maximum.reduce([item[1] for item in ranges])
    projection = np.vstack((axis, axes))
    try:
        inverse = np.linalg.inv(projection)
        condition = np.linalg.cond(projection)
        if not np.isfinite(inverse).all() or not np.isfinite(condition) or condition > 1e8:
            return None
    except np.linalg.LinAlgError:
        return None
    centre, half = (lower + upper) / 2, (upper - lower) / 2
    reach = np.abs(inverse) @ half
    world_lower, world_upper = origin + inverse @ centre - reach, origin + inverse @ centre + reach
    scale = max(1.0, float(np.max(np.abs(origin))), float(np.max(np.abs(world_lower))), float(np.max(np.abs(world_upper))))
    padding = max(1e-12, np.finfo(float).eps * scale * 32 * condition)
    if not np.isfinite(padding):
        return None
    return world_lower - padding, world_upper + padding


def bolt_candidates(points, models, p):
    """Discard models whose conservative world AABB misses this point block."""
    cloud = np.asarray(points, dtype=float)
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return []
    finite = np.isfinite(cloud).all(axis=1)
    if not np.any(finite):
        return []
    lower, upper = cloud[finite].min(axis=0), cloud[finite].max(axis=0)
    candidates = []
    for model in models or []:
        decoded = _mask_model(model)
        if decoded is None:
            continue
        bounds = _model_aabb(decoded)
        # A singular or badly conditioned projection cannot safely produce a
        # world box. Keep that model and let the exact path decide its points.
        if bounds is None:
            candidates.append(model)
            continue
        model_lower, model_upper = bounds
        if np.any(upper < model_lower) or np.any(lower > model_upper):
            continue
        candidates.append(model)
    return candidates


def bolt_mask(points, models, p, point_index=None):
    """Mask only observed shaft/head cells on narrow fitted surfaces."""
    cloud = np.asarray(points, dtype=float)
    result = np.zeros(len(cloud), dtype=bool)
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return result
    finite_rows = np.flatnonzero(np.isfinite(cloud).all(axis=1))
    candidates = bolt_candidates(cloud, models, p)
    if point_index is None and len(candidates) > 4:
        from .candidates import PointBoundsIndex
        point_index = PointBoundsIndex(cloud)
    for model in candidates:
        decoded = _mask_model(model)
        if decoded is None or not len(finite_rows):
            continue
        bounds = _model_aabb(decoded) if point_index is not None else None
        model_rows = finite_rows if bounds is None else point_index.query(*bounds)
        if not len(model_rows):
            continue
        shaft, head, axial, local = _model_candidates(cloud, model_rows, decoded)
        _, _, _, _, _, _, grid, angle_bins, _, shaft_cells, _, _, head_grid, head_cells = decoded
        if shaft.any():
            rows = model_rows[shaft]
            result[rows] |= _shaft_cell_membership(axial[shaft], local[shaft], grid, angle_bins, shaft_cells)
        if head.any():
            rows = model_rows[head]
            result[rows] |= _head_cell_membership(local[head], head_grid, head_cells)
    return result
