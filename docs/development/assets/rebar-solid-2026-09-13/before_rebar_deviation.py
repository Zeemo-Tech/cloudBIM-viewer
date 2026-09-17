"""Geometry primitives for the instance-constrained rebar deviation metric."""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy.spatial import cKDTree


def local_tangents(vertices: np.ndarray, segments: Iterable[tuple[np.ndarray, np.ndarray]]) -> np.ndarray | None:
    """Return the tangent of the closest declared centreline segment per vertex.

    This intentionally uses the design topology, rather than a global PCA axis: a
    curved bar changes tangent at every declared segment.
    """
    valid = []
    for start, end in segments:
        delta = np.asarray(end, float) - np.asarray(start, float)
        length = np.linalg.norm(delta)
        if np.isfinite(length) and length > 1e-9:
            valid.append((np.asarray(start, float), delta / length, length))
    if not valid:
        return None
    answer = np.empty((len(vertices), 3), float)
    best = np.full(len(vertices), np.inf)
    for start, direction, length in valid:
        along = np.clip((vertices - start) @ direction, 0.0, length)
        projected = start + along[:, None] * direction
        squared = np.einsum("ij,ij->i", vertices - projected, vertices - projected)
        take = squared < best
        answer[take] = direction
        best[take] = squared[take]
    return answer


def constrained_nearest(
    tree: cKDTree, scan_points: np.ndarray, vertices: np.ndarray, tangents: np.ndarray | None,
    normals: np.ndarray, *, k: int, max_angle_deg: float, half_space_only: bool,
    fallback_mode: str, chunk_size: int = 8192,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Find accepted points and signed transverse distances.

    Return distance, selected index, axial component, and a constrained-success
    mask.  Unknown mode never substitutes a nearest point when the constraint
    fails.  k is clamped by the actual point count, including k=1.
    """
    count = len(vertices)
    distance = np.full(count, np.nan, float)
    selected = np.full(count, -1, np.int64)
    axial = np.full(count, np.nan, float)
    constrained = np.zeros(count, bool)
    if tangents is None or len(scan_points) == 0:
        return distance, selected, axial, constrained
    neighbours = min(max(1, int(k)), 64, len(scan_points))
    cosine_limit = math.cos(math.radians(max_angle_deg))
    for offset in range(0, count, chunk_size):
        stop = min(count, offset + chunk_size)
        d, ix = tree.query(vertices[offset:stop], k=neighbours, workers=-1)
        if neighbours == 1:
            d, ix = d[:, None], ix[:, None]
        query = vertices[offset:stop]
        tangent = tangents[offset:stop]
        candidate = scan_points[ix]
        delta = candidate - query[:, None, :]
        candidate_norm = np.linalg.norm(delta, axis=2)
        candidate_axial = np.einsum("ijk,ik->ij", delta, tangent)
        transverse = np.sqrt(np.maximum(0.0, candidate_norm * candidate_norm - candidate_axial * candidate_axial))
        # A bidirectional cone around the surface normal projected into the
        # transverse plane rejects BOTH axial and circumferential neighbours.
        normal = normals[offset:stop]
        radial_normal = normal - np.einsum("ij,ij->i", normal, tangent)[:, None] * tangent
        normal_length = np.linalg.norm(radial_normal, axis=1)
        radial_normal = radial_normal / np.maximum(normal_length[:, None], 1e-12)
        normal_dot = np.einsum("ijk,ik->ij", delta, radial_normal)
        accepted = (np.abs(normal_dot) + 1e-12 >= candidate_norm * cosine_limit)
        accepted &= (normal_length > 1e-8)[:, None]
        accepted &= candidate_norm <= 0.2
        if half_space_only:
            accepted &= np.einsum("ijk,ik->ij", delta, normals[offset:stop]) >= 0.0
        first = np.argmax(accepted, axis=1)
        exists = accepted[np.arange(stop - offset), first]
        if fallback_mode == "nearest":
            first = np.where(exists, first, 0)
        else:
            first = np.where(exists, first, -1)
        local = np.arange(stop - offset)
        good = first >= 0
        chosen = np.maximum(first, 0)
        chosen_transverse = transverse[local, chosen]
        signs = np.where(normal_dot[local, chosen] >= 0.0, 1.0, -1.0)
        local_distance = distance[offset:stop]
        local_selected = selected[offset:stop]
        local_axial = axial[offset:stop]
        local_distance[good] = signs[good] * chosen_transverse[good]
        local_selected[good] = ix[local[good], chosen[good]]
        local_axial[good] = candidate_axial[local[good], chosen[good]]
        constrained[offset:stop] = exists
    return distance, selected, axial, constrained
