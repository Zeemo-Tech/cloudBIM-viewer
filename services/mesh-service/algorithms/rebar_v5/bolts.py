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
    return grid, bins, [tuple(item) for item in np.unique(cells, axis=0)]


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

    for fixture_index, face in enumerate(fixtures or []):
        parsed = _face_data(face)
        if parsed is None:
            continue
        origin, face_normal, axes, extent = parsed
        local3 = cloud - origin
        local2 = np.column_stack((local3 @ axes[0], local3 @ axes[1]))
        within_face = np.all(np.abs(local2) <= extent + .012, axis=1)
        for direction_sign in (1., -1.):
            axis = face_normal * direction_sign
            signed = local3 @ axis
            aligned = np.zeros(len(cloud), dtype=bool)
            valid_tangent = tangent_length > 1e-9
            aligned[valid_tangent] = np.abs((tangent[valid_tangent] / tangent_length[valid_tangent, None]) @ axis) >= math.cos(math.radians(22))
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
                head_cells = [tuple(item) for item in np.unique(np.floor(head_local / head_grid).astype(int), axis=0)]
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


def bolt_mask(points, models, p):
    """Mask only observed shaft/head cells on narrow fitted surfaces."""
    cloud = np.asarray(points, dtype=float)
    result = np.zeros(len(cloud), dtype=bool)
    if cloud.ndim != 2 or cloud.shape[1] != 3:
        return result
    finite = np.isfinite(cloud).all(axis=1)
    for model in models or []:
        try:
            origin, axis, axes = np.asarray(model["axisOrigin"], float), np.asarray(model["axis"], float), np.asarray(model["radialAxes"], float)
            lo, hi, radius = *map(float, model["axialRange"]), float(model["shaftRadius"])
            shaft, head = model["shaft"], model["head"]
            grid, angle_bins = float(shaft["axialGrid"]), int(shaft["angleBins"])
            shaft_cells = {tuple(cell) for cell in shaft["observedCells"]}
            head_cells, head_grid = {tuple(cell) for cell in head["occupiedCells"]}, float(head["gridSize"])
        except (KeyError, TypeError, ValueError):
            continue
        axis /= max(np.linalg.norm(axis), 1e-12)
        delta = cloud - origin
        axial = delta @ axis
        local = np.column_stack((delta @ axes[0], delta @ axes[1]))
        radial = np.linalg.norm(local, axis=1)
        angle = np.mod(np.arctan2(local[:, 1], local[:, 0]), 2 * np.pi)
        shaft_keys = list(zip(np.floor(axial / grid).astype(int), np.floor(angle / (2 * np.pi) * angle_bins).astype(int)))
        observed_shaft = np.fromiter((key in shaft_cells for key in shaft_keys), bool, count=len(cloud))
        shaft_surface = (axial >= lo - grid) & (axial <= hi + grid) & (np.abs(radial - radius) <= float(shaft["residualTolerance"])) & observed_shaft
        head_keys = [tuple(cell) for cell in np.floor(local / head_grid).astype(int)]
        observed_head = np.fromiter((key in head_cells for key in head_keys), bool, count=len(cloud))
        head_surface = (np.abs(axial - float(head["axial"])) <= float(head["planeTolerance"])) & observed_head
        result |= finite & (shaft_surface | head_surface)
    return result
