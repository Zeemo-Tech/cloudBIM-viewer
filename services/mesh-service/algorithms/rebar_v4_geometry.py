"""Bounded geometry primitives for geometric-v4 rebar segmentation.

Raw source points are spatially thinned before these helpers run.  Projection
uses a compact segment index, so neither detection nor labeling builds a graph
whose size is proportional to every source point and every candidate bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy.spatial import cKDTree


EPS = 1e-12


def canonical_direction(value: np.ndarray) -> np.ndarray:
    direction = np.asarray(value, dtype=np.float64)
    norm = float(np.linalg.norm(direction))
    if norm <= EPS:
        return direction
    direction = direction / norm
    major = int(np.argmax(np.abs(direction)))
    return -direction if direction[major] < 0 else direction


def perpendicular_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    direction = canonical_direction(direction)
    seed = np.eye(3)[int(np.argmin(np.abs(direction)))]
    first = seed - float(seed @ direction) * direction
    first /= np.linalg.norm(first)
    return first, np.cross(direction, first)


def convex_hull_2d(points: np.ndarray) -> np.ndarray:
    unique = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if len(unique) <= 2:
        return unique
    ordered = unique[np.lexsort((unique[:, 1], unique[:, 0]))]

    def cross(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        ab, bc = b - a, c - b
        return float(ab[0] * bc[1] - ab[1] * bc[0])

    lower: list[np.ndarray] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[np.ndarray] = []
    for point in ordered[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return np.asarray(lower[:-1] + upper[:-1], dtype=np.float64)


def inside_convex_polygon(
    points: np.ndarray, hull: np.ndarray, pad: float = 0.0
) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64)
    hull = np.asarray(hull, dtype=np.float64)
    if len(hull) == 0:
        return np.zeros(len(points), dtype=bool)
    if len(hull) == 1:
        return np.linalg.norm(points - hull[0], axis=1) <= pad + EPS
    if len(hull) == 2:
        vector = hull[1] - hull[0]
        length2 = float(vector @ vector)
        t = np.clip(((points - hull[0]) @ vector) / max(length2, EPS), 0.0, 1.0)
        return (
            np.linalg.norm(points - (hull[0] + t[:, None] * vector), axis=1)
            <= pad + EPS
        )
    edges = np.roll(hull, -1, axis=0) - hull
    rel = points[:, None, :] - hull[None, :, :]
    cross = edges[None, :, 0] * rel[:, :, 1] - edges[None, :, 1] * rel[:, :, 0]
    if pad > 0:
        cross += pad * np.linalg.norm(edges, axis=1)[None, :]
    return np.all(cross >= -1e-10, axis=1)


@dataclass(frozen=True)
class PlaneModel:
    origin: np.ndarray
    normal: np.ndarray
    axes: np.ndarray
    inliers: np.ndarray
    hull: np.ndarray
    rmse: float


def fit_bounded_table_plane(
    points: np.ndarray,
    *,
    distance: float,
    iterations: int,
    minimum_ratio: float,
    max_tilt_degrees: float,
    seed: int = 20260905,
) -> PlaneModel | None:
    """RANSAC a near-horizontal table and retain its observed footprint."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        return None
    if len(points) > 80_000:
        rng = np.random.default_rng(seed)
        work = points[np.sort(rng.choice(len(points), 80_000, replace=False))]
    else:
        work = points
    center = np.mean(work, axis=0)
    centered = work - center
    rng = np.random.default_rng(seed)
    minimum_up = float(np.cos(np.deg2rad(max_tilt_degrees)))
    best = np.empty(0, dtype=np.intp)
    best_median = np.inf
    for _ in range(iterations):
        a, b, c = centered[rng.choice(len(work), 3, replace=False)]
        normal = np.cross(b - a, c - a)
        norm = float(np.linalg.norm(normal))
        if norm <= EPS:
            continue
        normal /= norm
        if abs(float(normal[2])) < minimum_up:
            continue
        residual = np.abs((centered - a) @ normal)
        inliers = np.flatnonzero(residual <= distance)
        median = float(np.median(residual[inliers])) if len(inliers) else np.inf
        if len(inliers) > len(best) or (
            len(inliers) == len(best) and median < best_median
        ):
            best, best_median = inliers, median
    required = max(30, int(np.ceil(minimum_ratio * len(work))))
    if len(best) < required:
        return None
    origin = np.mean(work[best], axis=0)
    _, _, vh = np.linalg.svd(work[best] - origin, full_matrices=False)
    normal = vh[-1]
    if normal[2] < 0:
        normal = -normal
    if abs(float(normal[2])) < minimum_up:
        return None
    residual = np.abs((points - origin) @ normal)
    inliers = np.flatnonzero(residual <= distance)
    if len(inliers) < max(30, int(np.ceil(minimum_ratio * len(points)))):
        return None
    origin = np.mean(points[inliers], axis=0)
    _, singular, vh = np.linalg.svd(points[inliers] - origin, full_matrices=False)
    if singular[1] / max(float(singular[0]), EPS) < 0.12:
        return None
    normal = vh[-1]
    if normal[2] < 0:
        normal = -normal
    seed_axis = np.eye(3)[int(np.argmin(np.abs(normal)))]
    x_axis = seed_axis - float(seed_axis @ normal) * normal
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(normal, x_axis)
    local = np.column_stack(
        ((points[inliers] - origin) @ x_axis, (points[inliers] - origin) @ y_axis)
    )
    hull = convex_hull_2d(local)
    residual = np.abs((points[inliers] - origin) @ normal)
    return PlaneModel(
        origin,
        normal,
        np.vstack((x_axis, y_axis)),
        inliers,
        hull,
        float(np.sqrt(np.mean(np.square(residual)))),
    )


def table_mask(
    points: np.ndarray, plane: PlaneModel | dict, distance: float, pad: float = 0.0
) -> np.ndarray:
    if isinstance(plane, PlaneModel):
        origin, normal, axes, hull = plane.origin, plane.normal, plane.axes, plane.hull
    else:
        origin = np.asarray(plane.get("origin", []), dtype=np.float64)
        normal = np.asarray(plane.get("normal", []), dtype=np.float64)
        axes = np.asarray(plane.get("axes", []), dtype=np.float64)
        hull = np.asarray(plane.get("hull", []), dtype=np.float64)
        if origin.shape != (3,) or normal.shape != (3,) or axes.shape != (2, 3):
            return np.zeros(len(points), dtype=bool)
    centered = np.asarray(points, dtype=np.float64) - origin
    local = np.column_stack((centered @ axes[0], centered @ axes[1]))
    return (np.abs(centered @ normal) <= distance) & inside_convex_polygon(
        local, hull, pad
    )


def unique_voxel_indices(
    points: np.ndarray, size: float, anchor: np.ndarray | None = None
) -> np.ndarray:
    if len(points) == 0:
        return np.empty(0, dtype=np.intp)
    if anchor is None:
        anchor = points.min(axis=0)
    keys = np.floor((points - anchor) / size).astype(np.int64)
    _, indices = np.unique(keys, axis=0, return_index=True)
    return np.sort(indices.astype(np.intp))


@dataclass(frozen=True)
class LocalFeatures:
    points: np.ndarray
    source_indices: np.ndarray
    tangents: np.ndarray
    linearity: np.ndarray
    planarity: np.ndarray
    normals: np.ndarray
    neighbour_count: np.ndarray


def local_features(
    points: np.ndarray,
    *,
    seed_step: float,
    radius: float,
    max_neighbours: int,
    min_neighbours: int,
) -> LocalFeatures:
    """Compute batched full-3D PCA at spatially unique seed points."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) == 0:
        empty3 = np.empty((0, 3), dtype=np.float64)
        return LocalFeatures(
            empty3,
            np.empty(0, np.intp),
            empty3,
            np.empty(0),
            np.empty(0),
            empty3,
            np.empty(0, np.int32),
        )
    seed_indices = unique_voxel_indices(points, seed_step)
    seeds = points[seed_indices]
    tree = cKDTree(points)
    k = min(max_neighbours, len(points))
    tangent_parts: list[np.ndarray] = []
    normal_parts: list[np.ndarray] = []
    linear_parts: list[np.ndarray] = []
    planar_parts: list[np.ndarray] = []
    count_parts: list[np.ndarray] = []
    for start in range(0, len(seeds), 8192):
        query = seeds[start : start + 8192]
        distances, neighbours = tree.query(
            query, k=k, distance_upper_bound=radius, workers=-1
        )
        if k == 1:
            distances, neighbours = distances[:, None], neighbours[:, None]
        valid = np.isfinite(distances) & (neighbours < len(points))
        counts = valid.sum(axis=1)
        safe = np.where(valid, neighbours, 0)
        cloud = points[safe]
        weights = valid[..., None]
        means = (cloud * weights).sum(axis=1) / np.maximum(counts[:, None], 1)
        centered = (cloud - means[:, None, :]) * weights
        covariance = np.einsum("nki,nkj->nij", centered, centered)
        covariance /= np.maximum(counts - 1, 1)[:, None, None]
        values, vectors = np.linalg.eigh(covariance)
        high = np.maximum(values[:, 2], EPS)
        linearity = np.clip((values[:, 2] - values[:, 1]) / high, 0.0, 1.0)
        planarity = np.clip((values[:, 1] - values[:, 0]) / high, 0.0, 1.0)
        tangents = vectors[:, :, 2]
        normals = vectors[:, :, 0]
        for array in (tangents, normals):
            major = np.argmax(np.abs(array), axis=1)
            signs = array[np.arange(len(array)), major] < 0
            array[signs] *= -1
        invalid = counts < min_neighbours
        linearity[invalid] = 0.0
        planarity[invalid] = 0.0
        tangent_parts.append(tangents)
        normal_parts.append(normals)
        linear_parts.append(linearity)
        planar_parts.append(planarity)
        count_parts.append(counts.astype(np.int32))
    return LocalFeatures(
        seeds,
        seed_indices,
        np.vstack(tangent_parts),
        np.concatenate(linear_parts),
        np.concatenate(planar_parts),
        np.vstack(normal_parts),
        np.concatenate(count_parts),
    )


def orientation_modes(
    tangents: np.ndarray,
    weights: np.ndarray,
    *,
    tolerance_degrees: float,
    minimum_votes: int,
    maximum_modes: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Greedy unoriented spherical voting with deterministic refinement."""
    if len(tangents) == 0:
        return []
    remaining = np.ones(len(tangents), dtype=bool)
    cosine = float(np.cos(np.deg2rad(tolerance_degrees)))
    modes: list[tuple[np.ndarray, np.ndarray]] = []
    for seed in np.argsort(-weights, kind="stable"):
        if len(modes) >= maximum_modes:
            break
        if not remaining[seed]:
            continue
        selected = remaining & (np.abs(tangents @ tangents[seed]) >= cosine)
        if int(selected.sum()) < minimum_votes:
            remaining[seed] = False
            continue
        chosen, chosen_weights = tangents[selected], weights[selected]
        scatter = np.einsum("n,ni,nj->ij", chosen_weights, chosen, chosen)
        _, vectors = np.linalg.eigh(scatter)
        refined = canonical_direction(vectors[:, -1])
        selected = remaining & (np.abs(tangents @ refined) >= cosine)
        if int(selected.sum()) < minimum_votes:
            remaining[seed] = False
            continue
        indices = np.flatnonzero(selected)
        modes.append((refined, indices))
        remaining[selected] = False
    return modes


class _DisjointSet:
    def __init__(self, count: int):
        self.parent = np.arange(count, dtype=np.intp)

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = int(self.parent[value])
        return value

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def sparse_grid_components(
    values: np.ndarray, cell: float, dimensions: int = 2
) -> list[np.ndarray]:
    """Connected occupied-cell components without a dense raster."""
    if len(values) == 0:
        return []
    keys = np.floor(np.asarray(values)[:, :dimensions] / cell).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    lookup = {tuple(row): index for index, row in enumerate(unique.tolist())}
    dsu = _DisjointSet(len(unique))
    offsets = np.array(np.meshgrid(*([[-1, 0, 1]] * dimensions))).T.reshape(
        -1, dimensions
    )
    offsets = offsets[np.any(offsets != 0, axis=1)]
    for index, key in enumerate(unique):
        for offset in offsets:
            other = lookup.get(tuple((key + offset).tolist()))
            if other is not None and other > index:
                dsu.union(index, other)
    groups: dict[int, list[int]] = {}
    for row, cell_index in enumerate(inverse.tolist()):
        groups.setdefault(dsu.find(cell_index), []).append(row)
    return [np.asarray(rows, dtype=np.intp) for _, rows in sorted(groups.items())]


def deterministic_two_means(
    values: np.ndarray, iterations: int = 16
) -> tuple[np.ndarray, np.ndarray] | None:
    values = np.asarray(values, dtype=np.float64)
    if len(values) < 4:
        return None
    center = np.mean(values, axis=0)
    _, _, vh = np.linalg.svd(values - center, full_matrices=False)
    scalar = (values - center) @ vh[0]
    centers = np.array([np.percentile(scalar, 20), np.percentile(scalar, 80)])
    labels = np.zeros(len(values), dtype=bool)
    for _ in range(iterations):
        new_labels = np.abs(scalar - centers[1]) < np.abs(scalar - centers[0])
        if new_labels.all() or (~new_labels).all():
            return None
        next_centers = np.array([scalar[~new_labels].mean(), scalar[new_labels].mean()])
        if np.array_equal(new_labels, labels) and np.allclose(next_centers, centers):
            labels = new_labels
            break
        labels, centers = new_labels, next_centers
    return np.flatnonzero(~labels), np.flatnonzero(labels)


@dataclass(frozen=True)
class LinePrimitive:
    start: np.ndarray
    end: np.ndarray
    tangent: np.ndarray
    radius: float
    point_count: int
    score: float

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.end - self.start))


def _split_transverse_cloud(
    transverse: np.ndarray,
    indices: np.ndarray,
    *,
    max_radius: float,
    minimum_points: int,
    depth: int = 0,
    separate_slots: bool = False,
) -> list[np.ndarray]:
    values = transverse[indices]
    # An empty transverse slot is stronger evidence of separate rods than a
    # global maximum diameter. This also resolves thin rods whose whole bundle
    # fits inside the largest permitted cylinder.
    if separate_slots and depth < 8 and len(indices) >= 2 * minimum_points:
        _, _, basis = np.linalg.svd(values - values.mean(axis=0), full_matrices=False)
        scalar = values @ basis[0]
        order = np.argsort(scalar)
        gaps = np.diff(scalar[order])
        eligible = np.arange(minimum_points - 1, len(order) - minimum_points)
        if len(eligible):
            cut = eligible[np.argmax(gaps[eligible])]
            if gaps[cut] > 0.0015 and np.min(np.ptp(values, axis=0)) < 0.006:
                return [
                    part
                    for rows in (indices[order[: cut + 1]], indices[order[cut + 1 :]])
                    for part in _split_transverse_cloud(
                        transverse,
                        rows,
                        max_radius=max_radius,
                        minimum_points=minimum_points,
                        depth=depth + 1,
                        separate_slots=True,
                    )
                ]
    center = np.median(values, axis=0)
    radial = np.linalg.norm(values - center, axis=1)
    extent = np.ptp(values, axis=0)
    needs_split = (
        depth < 3
        and len(indices) >= 2 * minimum_points
        and (
            float(np.max(extent)) > 2.35 * max_radius
            or float(np.percentile(radial, 75)) > 1.15 * max_radius
        )
    )
    if not needs_split:
        return [indices]
    split = deterministic_two_means(values)
    if split is None:
        return [indices]
    left, right = indices[split[0]], indices[split[1]]
    if len(left) < minimum_points or len(right) < minimum_points:
        return [indices]
    separation = float(
        np.linalg.norm(
            np.mean(transverse[left], axis=0) - np.mean(transverse[right], axis=0)
        )
    )
    if separation < 0.8 * max_radius:
        return [indices]
    return [
        *_split_transverse_cloud(
            transverse,
            left,
            max_radius=max_radius,
            minimum_points=minimum_points,
            depth=depth + 1,
        ),
        *_split_transverse_cloud(
            transverse,
            right,
            max_radius=max_radius,
            minimum_points=minimum_points,
            depth=depth + 1,
        ),
    ]


def line_primitives(
    feature_points: np.ndarray,
    tangents: np.ndarray,
    linearity: np.ndarray,
    *,
    minimum_linearity: float,
    orientation_tolerance_degrees: float,
    minimum_votes: int,
    maximum_modes: int,
    offset_cell: float,
    axial_gap: float,
    minimum_length: float,
    min_radius: float,
    max_radius: float,
) -> list[LinePrimitive]:
    selected = np.flatnonzero(linearity >= minimum_linearity)
    if len(selected) == 0:
        return []
    points, local_tangents, weights = (
        feature_points[selected],
        tangents[selected],
        linearity[selected],
    )
    modes = orientation_modes(
        local_tangents,
        weights,
        tolerance_degrees=orientation_tolerance_degrees,
        minimum_votes=minimum_votes,
        maximum_modes=maximum_modes,
    )
    primitives: list[LinePrimitive] = []
    for mode, mode_rows in modes:
        mode_points = points[mode_rows]
        mode_axial = mode_points @ mode
        window_length = max(0.27, 4.0 * minimum_length)
        window_step = 2.0 * window_length / 3.0
        if float(np.ptp(mode_axial)) <= window_length:
            windows = [np.arange(len(mode_points), dtype=np.intp)]
        else:
            starts = np.arange(
                float(np.min(mode_axial)), float(np.max(mode_axial)), window_step
            )
            windows = [
                np.flatnonzero(
                    (mode_axial >= start) & (mode_axial <= start + window_length)
                )
                for start in starts
            ]
        first, second = perpendicular_basis(mode)
        for window_rows in windows:
            if len(window_rows) < minimum_votes:
                continue
            window_points = mode_points[window_rows]
            transverse = np.column_stack(
                (window_points @ first, window_points @ second)
            )
            for component in sparse_grid_components(transverse, offset_cell):
                for physical in _split_transverse_cloud(
                    transverse,
                    component,
                    max_radius=max_radius,
                    minimum_points=minimum_votes,
                ):
                    if len(physical) < minimum_votes:
                        continue
                    axial = window_points[physical] @ mode
                    order = np.argsort(axial, kind="stable")
                    split_at = np.flatnonzero(np.diff(axial[order]) > axial_gap) + 1
                    for interval in np.split(order, split_at):
                        if len(interval) < minimum_votes:
                            continue
                        rows = physical[interval]
                        cloud = window_points[rows]
                        center = np.mean(cloud, axis=0)
                        _, _, vh = np.linalg.svd(cloud - center, full_matrices=False)
                        axis = canonical_direction(vh[0])
                        if abs(float(axis @ mode)) < np.cos(
                            np.deg2rad(orientation_tolerance_degrees * 1.5)
                        ):
                            continue
                        along = (cloud - center) @ axis
                        lo, hi = np.percentile(along, [1.0, 99.0])
                        length = float(hi - lo)
                        if length < minimum_length:
                            continue
                        cross = (cloud - center) - np.outer(along, axis)
                        radial = np.linalg.norm(cross, axis=1)
                        radius = float(np.percentile(radial, 55))
                        radial_mad = float(
                            np.median(np.abs(radial - np.median(radial)))
                        )
                        if radius > 1.35 * max_radius or radial_mad > max(
                            0.003, 0.65 * max(radius, min_radius)
                        ):
                            continue
                        radius = float(np.clip(radius, min_radius, max_radius))
                        source_rows = selected[mode_rows[window_rows[rows]]]
                        primitives.append(
                            LinePrimitive(
                                center + lo * axis,
                                center + hi * axis,
                                axis,
                                radius,
                                len(cloud),
                                float(np.mean(linearity[source_rows])),
                            )
                        )
    consolidated = consolidate_collinear_primitives(
        deduplicate_primitives(primitives), axial_gap=axial_gap
    )
    return refit_straight_families(
        consolidated,
        feature_points,
        tangents,
        linearity,
        offset_cell=offset_cell,
        axial_gap=axial_gap,
        minimum_votes=minimum_votes,
        min_radius=min_radius,
        max_radius=max_radius,
    )


def refit_straight_families(
    primitives,
    points,
    tangents,
    linearity,
    *,
    offset_cell,
    axial_gap,
    minimum_votes,
    min_radius,
    max_radius,
):
    """Calibrate long-axis voting from long observations, then split offsets.

    A one-degree local tangent error smears a 3m bar by 52mm. Long observations
    provide the angular precision needed to separate nearby parallel stock.
    Short curved primitives remain available to the endpoint tracer.
    """
    long = [item for item in primitives if item.length >= 0.20]
    if not long:
        return primitives
    modes = orientation_modes(
        np.array([item.tangent for item in long]),
        np.array([item.length**2 for item in long]),
        tolerance_degrees=4.0,
        minimum_votes=1,
        maximum_modes=16,
    )
    recovered = []
    for _, rows in modes:
        axes = np.array([long[i].tangent for i in rows])
        weights = np.array([long[i].length ** 2 for i in rows])
        reference = axes[np.argmax(weights)]
        axes *= np.where(axes @ reference < 0, -1.0, 1.0)[:, None]
        direction = []
        for coordinate in range(3):
            order = np.argsort(axes[:, coordinate])
            chosen = order[
                np.searchsorted(np.cumsum(weights[order]), weights.sum() / 2)
            ]
            direction.append(axes[chosen, coordinate])
        axis = canonical_direction(np.array(direction))
        # For one narrow bundle, longitudinally separated window centers offer
        # a much longer baseline than individual surface-biased tangents.
        centers = np.array([(long[i].start + long[i].end) / 2 for i in rows])
        if len(centers) >= 3:
            _, singular, basis = np.linalg.svd(
                centers - centers.mean(axis=0), full_matrices=False
            )
            if (
                singular[0] > 0.3
                and singular[0] > 8 * max(singular[1], EPS)
                and abs(basis[0] @ axis) > np.cos(np.deg2rad(3))
            ):
                axis = canonical_direction(basis[0])
        selected = np.flatnonzero(
            (np.abs(tangents @ axis) >= np.cos(np.deg2rad(11.0))) & (linearity >= 0.48)
        )
        cloud = points[selected]
        u, v = perpendicular_basis(axis)
        transverse = np.column_stack((cloud @ u, cloud @ v))
        full_transverse = np.column_stack((points @ u, points @ v))
        support_tree = cKDTree(full_transverse)
        for component in sparse_grid_components(transverse, offset_cell):
            for physical in _split_transverse_cloud(
                transverse,
                component,
                max_radius=max_radius,
                minimum_points=minimum_votes,
                separate_slots=True,
            ):
                if len(physical) < minimum_votes:
                    continue
                cross_center = transverse[physical].mean(axis=0)
                cross_radius = float(
                    np.quantile(
                        np.linalg.norm(transverse[physical] - cross_center, axis=1),
                        0.55,
                    )
                )
                support_rows = np.asarray(
                    support_tree.query_ball_point(
                        cross_center, min(max_radius, max(0.005, 2 * cross_radius))
                    ),
                    dtype=np.intp,
                )
                source_cloud = points[support_rows]
                scalar = source_cloud @ axis
                order = np.argsort(scalar)
                splits = np.flatnonzero(np.diff(scalar[order]) > axial_gap) + 1
                for interval in np.split(order, splits):
                    rows2 = interval
                    if len(rows2) < minimum_votes:
                        continue
                    support = source_cloud[rows2]
                    center = support.mean(axis=0)
                    along = (support - center) @ axis
                    lo, hi = np.percentile(along, [0.5, 99.5])
                    if hi - lo < 0.32:
                        continue
                    radial = np.linalg.norm(
                        support - center - along[:, None] * axis, axis=1
                    )
                    if np.quantile(radial, 0.9) > max_radius * 1.1:
                        continue
                    radius = float(
                        np.clip(np.quantile(radial, 0.55), min_radius, max_radius)
                    )
                    recovered.append(
                        LinePrimitive(
                            center + lo * axis,
                            center + hi * axis,
                            axis,
                            radius,
                            len(support),
                            float(linearity[support_rows[rows2]].mean()),
                        )
                    )
    if not recovered:
        return primitives
    retained = []
    for item in primitives:
        sample = item.start + np.linspace(0, 1, 13)[:, None] * (item.end - item.start)
        distance = np.full(len(sample), np.inf)
        compatible = False
        for line in recovered:
            if abs(item.tangent @ line.tangent) < np.cos(np.deg2rad(5.0)):
                continue
            compatible = True
            distance = np.minimum(
                distance, _segment_distance(sample, line.start, line.end)
            )
        if not compatible or np.mean(distance <= 0.007) < 0.7:
            retained.append(item)
    return [
        item
        for item in deduplicate_primitives(recovered + retained)
        if item.point_count >= 2 * minimum_votes
    ]


def _segment_distance(
    points: np.ndarray, start: np.ndarray, end: np.ndarray
) -> np.ndarray:
    vector = end - start
    length2 = float(vector @ vector)
    if length2 <= EPS:
        return np.linalg.norm(points - start, axis=1)
    t = np.clip(((points - start) @ vector) / length2, 0.0, 1.0)
    return np.linalg.norm(points - (start + t[:, None] * vector), axis=1)


def deduplicate_primitives(primitives: list[LinePrimitive]) -> list[LinePrimitive]:
    accepted: list[LinePrimitive] = []
    for item in sorted(
        primitives, key=lambda p: (-p.point_count, -p.length, *p.start.tolist())
    ):
        midpoint = (item.start + item.end) / 2
        duplicate = any(
            abs(float(item.tangent @ existing.tangent)) >= np.cos(np.deg2rad(8.0))
            and float(
                _segment_distance(midpoint[None, :], existing.start, existing.end)[0]
            )
            <= 0.6 * max(item.radius, existing.radius)
            for existing in accepted
        )
        if not duplicate:
            accepted.append(item)
    return accepted


def consolidate_collinear_primitives(
    primitives: list[LinePrimitive],
    *,
    transverse_tolerance: float = 0.009,
    axial_gap: float = 0.09,
    angle_degrees: float = 7.0,
) -> list[LinePrimitive]:
    """Union overlapping observations of one axis before endpoint tracing.

    Orientation voting can describe one long rod several times from overlapping
    axial windows.  Endpoint-only matching cannot join those observations
    because their ends are deliberately staggered.  This consolidation uses a
    radius-scaled transverse gate and only unions observations whose axial
    intervals overlap or nearly touch. Straight-family refitting subsequently
    checks transverse gaps against the observed points again.
    """
    if len(primitives) < 2:
        return primitives
    dsu = _DisjointSet(len(primitives))
    cosine = float(np.cos(np.deg2rad(angle_degrees)))
    for left, a in enumerate(primitives):
        amid = (a.start + a.end) / 2
        for right in range(left + 1, len(primitives)):
            b = primitives[right]
            if abs(float(a.tangent @ b.tangent)) < cosine:
                continue
            axis = canonical_direction(
                a.tangent + np.sign(float(a.tangent @ b.tangent)) * b.tangent
            )
            values_a = np.asarray([a.start @ axis, a.end @ axis])
            values_b = np.asarray([b.start @ axis, b.end @ axis])
            overlap_lo = max(float(np.min(values_a)), float(np.min(values_b)))
            overlap_hi = min(float(np.max(values_a)), float(np.max(values_b)))
            if overlap_lo <= overlap_hi:
                compare_at = (overlap_lo + overlap_hi) / 2
            else:
                compare_at = (
                    min(float(np.max(values_a)), float(np.max(values_b)))
                    + max(float(np.min(values_a)), float(np.min(values_b)))
                ) / 2
            bmid = (b.start + b.end) / 2
            denominator_a = float(a.tangent @ axis)
            denominator_b = float(b.tangent @ axis)
            point_a = amid + a.tangent * (
                (compare_at - float(amid @ axis)) / denominator_a
            )
            point_b = bmid + b.tangent * (
                (compare_at - float(bmid @ axis)) / denominator_b
            )
            transverse = point_b - point_a
            transverse -= float(transverse @ axis) * axis
            separation = float(np.linalg.norm(transverse))
            if separation > min(transverse_tolerance, 0.75 * (a.radius + b.radius)):
                continue
            gap = max(
                float(np.min(values_b) - np.max(values_a)),
                float(np.min(values_a) - np.max(values_b)),
                0.0,
            )
            if gap <= axial_gap:
                dsu.union(left, right)
    groups: dict[int, list[int]] = {}
    for index in range(len(primitives)):
        groups.setdefault(dsu.find(index), []).append(index)
    result: list[LinePrimitive] = []
    for rows in groups.values():
        if len(rows) == 1:
            result.append(primitives[rows[0]])
            continue
        items = [primitives[row] for row in rows]
        weights = np.asarray([item.point_count for item in items], dtype=np.float64)
        reference = items[0].tangent
        oriented = np.vstack(
            [item.tangent * np.sign(float(item.tangent @ reference)) for item in items]
        )
        axis = canonical_direction(np.average(oriented, axis=0, weights=weights))
        midpoints = np.vstack([(item.start + item.end) / 2 for item in items])
        center = np.average(midpoints, axis=0, weights=weights)
        centered_midpoints = midpoints - center
        scatter = np.einsum(
            "n,ni,nj->ij", weights, centered_midpoints, centered_midpoints
        )
        values, vectors = np.linalg.eigh(scatter)
        midpoint_axis = canonical_direction(vectors[:, -1])
        if (
            values[-1] > max(values[-2] * 4.0, transverse_tolerance**2)
            and abs(float(midpoint_axis @ axis)) >= cosine
        ):
            # The locus of overlapping window centres is a more stable global
            # axis estimate than averaging their individually noisy PCA axes.
            axis = midpoint_axis
        endpoints = np.vstack([(item.start, item.end) for item in items])
        axial = (endpoints - center) @ axis
        result.append(
            LinePrimitive(
                center + float(np.min(axial)) * axis,
                center + float(np.max(axial)) * axis,
                axis,
                float(np.average([item.radius for item in items], weights=weights)),
                int(weights.sum()),
                float(np.average([item.score for item in items], weights=weights)),
            )
        )
    return deduplicate_primitives(result)


@dataclass(frozen=True)
class TracedInstance:
    centerline: np.ndarray
    observed_segments: tuple[np.ndarray, ...]
    inferred_segments: tuple[np.ndarray, ...]
    radius: float
    point_count: int
    length: float
    tangent: np.ndarray


def trace_primitive_graph(
    primitives: list[LinePrimitive],
    *,
    join_gap: float,
    observed_join_gap: float,
    maximum_turn_degrees: float,
    minimum_instance_length: float,
) -> list[TracedInstance]:
    """Match endpoints one-to-one so welded crossings cannot fuse a network."""
    if not primitives:
        return []
    endpoints = np.vstack([(item.start, item.end) for item in primitives])
    pairs = cKDTree(endpoints).query_pairs(join_gap, output_type="ndarray")
    candidates: list[tuple[float, int, int]] = []
    max_turn = np.deg2rad(maximum_turn_degrees)
    for left, right in pairs:
        a, b = int(left // 2), int(right // 2)
        if a == b:
            continue
        pa, pb = primitives[a], primitives[b]
        # Each primitive tangent is canonical rather than oriented along the
        # prospective chain.  At an endpoint the outward tangent therefore
        # depends on whether it is the start or end.  A valid continuation has
        # opposing outward tangents; abs(dot) incorrectly joins bars that leave
        # a junction in the same direction.
        left_outward = pa.tangent * (1.0 if left % 2 else -1.0)
        right_outward = pb.tangent * (1.0 if right % 2 else -1.0)
        angle = float(
            np.arccos(np.clip(float(-left_outward @ right_outward), -1.0, 1.0))
        )
        if angle > max_turn:
            continue
        delta = endpoints[right] - endpoints[left]
        distance = float(np.linalg.norm(delta))
        if distance > EPS:
            advance = delta / distance
            # Segments can overlap: the endpoint displacement then points
            # backwards even though the two outward tangents oppose correctly.
            # Lateral adjacency of separate parallel bars is never a join.
            if angle < np.deg2rad(8):
                lateral = max(
                    np.linalg.norm(delta - (delta @ left_outward) * left_outward),
                    np.linalg.norm(delta - (delta @ right_outward) * right_outward),
                )
                if lateral > 0.006:
                    continue
            if distance > 0.012 and (
                abs(float(advance @ left_outward)) < 0.65
                or abs(float(advance @ right_outward)) < 0.65
            ):
                continue
        candidates.append(
            (distance + join_gap * angle / max(max_turn, EPS), int(left), int(right))
        )
    partner: dict[int, int] = {}
    for _, left, right in sorted(candidates):
        if left not in partner and right not in partner:
            partner[left], partner[right] = right, left
    adjacency: dict[int, list[tuple[int, int, int]]] = {
        i: [] for i in range(len(primitives))
    }
    for endpoint, other in partner.items():
        if endpoint > other:
            continue
        a, b = endpoint // 2, other // 2
        adjacency[a].append((b, endpoint % 2, other % 2))
        adjacency[b].append((a, other % 2, endpoint % 2))
    visited: set[int] = set()
    traced: list[TracedInstance] = []
    starts = sorted(range(len(primitives)), key=lambda i: (len(adjacency[i]) == 2, i))
    for first in starts:
        if first in visited:
            continue
        chain: list[tuple[int, bool]] = []
        previous: int | None = None
        current = first
        entry = (
            next((1 - edge[1] for edge in adjacency[current]), 0)
            if len(adjacency[current]) == 1
            else 0
        )
        while current not in visited:
            visited.add(current)
            chain.append((current, entry == 1))
            exits = [edge for edge in adjacency[current] if edge[0] != previous]
            if not exits:
                break
            nxt, _, other_endpoint = sorted(exits)[0]
            previous, current, entry = current, nxt, other_endpoint
        observed: list[np.ndarray] = []
        inferred: list[np.ndarray] = []
        combined: list[np.ndarray] = []
        previous_end: np.ndarray | None = None
        for primitive_index, reverse in chain:
            primitive = primitives[primitive_index]
            segment = (
                np.vstack((primitive.end, primitive.start))
                if reverse
                else np.vstack((primitive.start, primitive.end))
            )
            observed.append(segment)
            if previous_end is None:
                combined.extend(segment)
                previous_end = segment[1]
                continue
            gap = float(np.linalg.norm(previous_end - segment[0]))
            delta = segment[0] - previous_end
            previous_direction = observed[-2][1] - observed[-2][0]
            next_direction = segment[1] - segment[0]
            overlap = delta @ previous_direction < 0 and delta @ next_direction < 0
            if overlap:
                # Two detected pieces overlap on an observed curve; this is
                # not missing design geometry and needs no inferred bridge.
                combined[-1] = (previous_end + segment[0]) / 2
                combined.append(segment[1])
            elif gap <= observed_join_gap:
                if gap > EPS:
                    combined.append(segment[0])
                combined.append(segment[1])
            else:
                inferred.append(np.vstack((previous_end, segment[0])))
                combined.extend(segment)
            previous_end = segment[1]
        total_length = float(
            sum(np.linalg.norm(segment[1] - segment[0]) for segment in observed)
        )
        if total_length < minimum_instance_length:
            continue
        longest = max(
            observed, key=lambda segment: float(np.linalg.norm(segment[1] - segment[0]))
        )
        # The public centerline preserves chain order. Renderers and projectors
        # use observed_segments; gaps are separately identified as inferred.
        centerline = np.asarray(combined, dtype=np.float64)
        tangent = canonical_direction(longest[-1] - longest[0])
        weights = np.asarray(
            [primitives[i].point_count for i, _ in chain], dtype=np.float64
        )
        radius = float(
            np.average([primitives[i].radius for i, _ in chain], weights=weights)
        )
        traced.append(
            TracedInstance(
                centerline,
                tuple(observed),
                tuple(inferred),
                radius,
                int(weights.sum()),
                total_length,
                tangent,
            )
        )
    return traced


@dataclass(frozen=True)
class SegmentIndex:
    starts: np.ndarray
    ends: np.ndarray
    midpoints: np.ndarray
    instance_ids: np.ndarray
    direction_ids: np.ndarray
    radii: np.ndarray
    tangents: np.ndarray
    tree: cKDTree | None
    reach: float


def build_segment_index(
    entries: Iterable[dict], *, piece_length: float = 0.04
) -> SegmentIndex:
    starts: list[np.ndarray] = []
    ends: list[np.ndarray] = []
    instance_ids: list[int] = []
    direction_ids: list[int] = []
    radii: list[float] = []
    for entry in entries:
        segments = entry.get("observedSegments")
        if not segments:
            line = np.asarray(entry.get("centerline", []), dtype=np.float64)
            segments = [np.vstack((a, b)) for a, b in zip(line[:-1], line[1:])]
        for raw in segments:
            line = np.asarray(
                raw.get("points", []) if isinstance(raw, dict) else raw,
                dtype=np.float64,
            )
            for a, b in zip(line[:-1], line[1:]):
                length = float(np.linalg.norm(b - a))
                if length <= EPS:
                    continue
                count = max(1, int(np.ceil(length / piece_length)))
                for ordinal in range(count):
                    lo, hi = ordinal / count, (ordinal + 1) / count
                    starts.append(a + lo * (b - a))
                    ends.append(a + hi * (b - a))
                    instance_ids.append(int(entry["id"]))
                    direction_ids.append(int(entry.get("directionId", 0)))
                    radii.append(float(entry["radius"]))
    if not starts:
        empty3 = np.empty((0, 3), dtype=np.float64)
        return SegmentIndex(
            empty3,
            empty3,
            empty3,
            np.empty(0, np.uint32),
            np.empty(0, np.uint16),
            np.empty(0),
            empty3,
            None,
            0.0,
        )
    start_array, end_array = np.asarray(starts), np.asarray(ends)
    vectors = end_array - start_array
    lengths = np.linalg.norm(vectors, axis=1)
    tangents = vectors / lengths[:, None]
    midpoints = (start_array + end_array) / 2
    radius_array = np.asarray(radii, dtype=np.float64)
    reach = float(np.max(lengths / 2 + radius_array))
    return SegmentIndex(
        start_array,
        end_array,
        midpoints,
        np.asarray(instance_ids, np.uint32),
        np.asarray(direction_ids, np.uint16),
        radius_array,
        tangents,
        cKDTree(midpoints),
        reach,
    )


@dataclass(frozen=True)
class ProjectionResult:
    best_distance: np.ndarray
    best_id: np.ndarray
    best_direction: np.ndarray
    best_tangent: np.ndarray
    second_distance: np.ndarray
    second_id: np.ndarray
    second_tangent: np.ndarray


def project_top2(
    points: np.ndarray, index: SegmentIndex, neighbours: int = 20
) -> ProjectionResult:
    """Vectorized exact point-to-segment projection for distinct physical IDs."""
    points = np.asarray(points, dtype=np.float64)
    n = len(points)
    k = min(max(1, neighbours), len(index.starts))
    # Keep candidate matrices bounded even at a densely sampled junction.
    batch_size = max(1, 1_000_000 // max(1, k))
    if n > batch_size:
        batches = [
            project_top2(points[start : start + batch_size], index, neighbours)
            for start in range(0, n, batch_size)
        ]
        return ProjectionResult(
            *(
                np.concatenate([getattr(batch, name) for batch in batches])
                for name in ProjectionResult.__dataclass_fields__
            )
        )
    best_distance = np.full(n, np.inf)
    second_distance = np.full(n, np.inf)
    best_id = np.zeros(n, np.uint32)
    second_id = np.zeros(n, np.uint32)
    best_direction = np.zeros(n, np.uint16)
    best_tangent = np.zeros((n, 3), np.float64)
    second_tangent = np.zeros((n, 3), np.float64)
    if index.tree is None or len(index.starts) == 0 or n == 0:
        return ProjectionResult(
            best_distance,
            best_id,
            best_direction,
            best_tangent,
            second_distance,
            second_id,
            second_tangent,
        )
    midpoint_distance, candidates = index.tree.query(
        points, k=k, distance_upper_bound=index.reach, workers=-1
    )
    if k == 1:
        midpoint_distance, candidates = midpoint_distance[:, None], candidates[:, None]
    valid = np.isfinite(midpoint_distance) & (candidates < len(index.starts))
    safe = np.where(valid, candidates, 0)
    starts = index.starts[safe]
    vectors = index.ends[safe] - starts
    length2 = np.einsum("nki,nki->nk", vectors, vectors)
    rel = points[:, None, :] - starts
    t = np.clip(
        np.einsum("nki,nki->nk", rel, vectors) / np.maximum(length2, EPS), 0.0, 1.0
    )
    distance = np.linalg.norm(
        points[:, None, :] - (starts + t[..., None] * vectors), axis=2
    )
    distance[~valid] = np.inf
    candidate_ids = index.instance_ids[safe]
    candidate_directions = index.direction_ids[safe]
    normalized = distance / np.maximum(index.radii[safe], EPS)
    normalized[distance > index.radii[safe]] = np.inf
    for column in range(k):
        value = normalized[:, column]
        ident = candidate_ids[:, column]
        direction = candidate_directions[:, column]
        tangent = index.tangents[safe[:, column]]
        finite = np.isfinite(value) & (ident > 0)
        same_best = finite & (ident == best_id) & (value < best_distance)
        best_distance[same_best] = value[same_best]
        best_tangent[same_best] = tangent[same_best]
        new_best = finite & (ident != best_id) & (value < best_distance)
        if np.any(new_best):
            second_distance[new_best] = best_distance[new_best]
            second_id[new_best] = best_id[new_best]
            second_tangent[new_best] = best_tangent[new_best]
            best_distance[new_best] = value[new_best]
            best_id[new_best] = ident[new_best]
            best_direction[new_best] = direction[new_best]
            best_tangent[new_best] = tangent[new_best]
        second = finite & (ident != best_id) & (value < second_distance)
        second_distance[second] = value[second]
        second_id[second] = ident[second]
        second_tangent[second] = tangent[second]
    result = ProjectionResult(
        best_distance,
        best_id,
        best_direction,
        best_tangent,
        second_distance,
        second_id,
        second_tangent,
    )
    # Twenty nearby pieces can all belong to one bent or overlapping instance.
    # Exhaust saturated neighborhoods before declaring a unique membership.
    if k < len(index.starts):
        saturated = np.flatnonzero(np.isfinite(midpoint_distance[:, -1]))
        if len(saturated):
            expanded = project_top2(
                points[saturated], index, min(2 * k, len(index.starts))
            )
            for name in ProjectionResult.__dataclass_fields__:
                getattr(result, name)[saturated] = getattr(expanded, name)
    return result


def polyline_distance(
    points: np.ndarray, polylines: list[np.ndarray], radii: list[float]
) -> tuple[np.ndarray, np.ndarray]:
    entries = [
        dict(id=i + 1, directionId=0, centerline=line, radius=radius)
        for i, (line, radius) in enumerate(zip(polylines, radii))
    ]
    result = project_top2(points, build_segment_index(entries))
    owner = np.where(result.best_id > 0, result.best_id.astype(np.intp) - 1, -1)
    physical = result.best_distance.copy()
    matched = result.best_id > 0
    if np.any(matched):
        physical[matched] *= np.asarray(radii)[owner[matched]]
    return physical, owner


def trace_centerline(points: np.ndarray, axis: np.ndarray, step: float) -> np.ndarray:
    """Stable ordered median bins retained as a small public helper."""
    points = np.asarray(points, dtype=np.float64)
    axis = canonical_direction(axis)
    projected = points @ axis
    lo = float(projected.min())
    bins = np.floor((projected - lo) / max(step, 1e-4)).astype(np.int64)
    line = [np.median(points[bins == value], axis=0) for value in np.unique(bins)]
    if len(line) < 2:
        return np.vstack((points[np.argmin(projected)], points[np.argmax(projected)]))
    return np.asarray(line, dtype=np.float64)
