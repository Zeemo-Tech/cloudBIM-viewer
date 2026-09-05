"""V5 observed surface ownership; centreline tubes are only lookup bounds."""
from dataclasses import dataclass
import numpy as np
from ..rebar_v4_geometry import SegmentIndex, ProjectionResult, EPS


@dataclass(frozen=True)
class SurfaceProjectionResult(ProjectionResult):
    best_normal: np.ndarray


def project_surface_top2(
    points: np.ndarray, index: SegmentIndex, tolerance: float, neighbours: int = 20
) -> SurfaceProjectionResult:
    """Compete by cylindrical surface residual, with bounded finite-segment lookup."""
    points = np.asarray(points, dtype=np.float64)
    n = len(points)
    k = min(max(1, neighbours), len(index.starts))
    # Keep candidate matrices bounded even at a densely sampled junction.
    batch_size = max(1, 1_000_000 // max(1, k))
    if n > batch_size:
        batches = [
            project_surface_top2(points[start : start + batch_size], index, tolerance, neighbours)
            for start in range(0, n, batch_size)
        ]
        return SurfaceProjectionResult(
            *(
                np.concatenate([getattr(batch, name) for batch in batches])
                for name in SurfaceProjectionResult.__dataclass_fields__
            )
        )
    best_distance = np.full(n, np.inf)
    second_distance = np.full(n, np.inf)
    best_id = np.zeros(n, np.uint32)
    second_id = np.zeros(n, np.uint32)
    best_direction = np.zeros(n, np.uint16)
    best_tangent = np.zeros((n, 3), np.float64)
    best_normal = np.zeros((n, 3), np.float64)
    second_tangent = np.zeros((n, 3), np.float64)
    if index.tree is None or len(index.starts) == 0 or n == 0:
        return SurfaceProjectionResult(
            best_distance,
            best_id,
            best_direction,
            best_tangent,
            second_distance,
            second_id,
            second_tangent,
            best_normal,
        )
    midpoint_distance, candidates = index.tree.query(
        points, k=k, distance_upper_bound=index.reach, workers=1
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
    radial = points[:, None, :] - (starts + t[..., None] * vectors)
    distance = np.linalg.norm(radial, axis=2)
    radial_normal = np.divide(radial,distance[...,None],out=np.zeros_like(radial),where=distance[...,None]>EPS)
    distance[~valid] = np.inf
    candidate_ids = index.instance_ids[safe]
    candidate_directions = index.direction_ids[safe]
    physical_radius = np.maximum(index.radii[safe] - tolerance, EPS)
    residual = np.abs(distance - physical_radius)
    normalized = residual / tolerance
    normalized[(distance > index.radii[safe]) | (residual > tolerance)] = np.inf
    for column in range(k):
        value = normalized[:, column]
        ident = candidate_ids[:, column]
        direction = candidate_directions[:, column]
        tangent = index.tangents[safe[:, column]]
        normal = radial_normal[:, column]
        finite = np.isfinite(value) & (ident > 0)
        same_best = finite & (ident == best_id) & (value < best_distance)
        best_distance[same_best] = value[same_best]
        best_tangent[same_best] = tangent[same_best]
        best_normal[same_best] = normal[same_best]
        new_best = finite & (ident != best_id) & (value < best_distance)
        if np.any(new_best):
            second_distance[new_best] = best_distance[new_best]
            second_id[new_best] = best_id[new_best]
            second_tangent[new_best] = best_tangent[new_best]
            best_distance[new_best] = value[new_best]
            best_id[new_best] = ident[new_best]
            best_direction[new_best] = direction[new_best]
            best_tangent[new_best] = tangent[new_best]
            best_normal[new_best] = normal[new_best]
        second = finite & (ident != best_id) & (value < second_distance)
        second_distance[second] = value[second]
        second_id[second] = ident[second]
        second_tangent[second] = tangent[second]
    result = SurfaceProjectionResult(
        best_distance,
        best_id,
        best_direction,
        best_tangent,
        second_distance,
        second_id,
        second_tangent,
        best_normal,
    )
    # Twenty nearby pieces can all belong to one bent or overlapping instance.
    # Exhaust saturated neighborhoods before declaring a unique membership.
    if k < len(index.starts):
        saturated = np.flatnonzero(np.isfinite(midpoint_distance[:, -1]))
        if len(saturated):
            expanded = project_surface_top2(
                points[saturated], index, tolerance, min(2 * k, len(index.starts))
            )
            for name in SurfaceProjectionResult.__dataclass_fields__:
                getattr(result, name)[saturated] = getattr(expanded, name)
    return result
