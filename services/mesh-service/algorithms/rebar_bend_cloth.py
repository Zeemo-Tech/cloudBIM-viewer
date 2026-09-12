"""Continuous, cavity-preserving cloths for parallel rows of bent rebar.

Unlike a collection of tubes this exports one closed extrusion for a whole
row.  The extrusion follows the two offset curves, so a U bend remains a U:
the chord across its inside is never introduced as a face.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .rebar_curve_envelope import _arc_dense, build_curve_shells


def _path(path: dict[str, Any]) -> tuple[np.ndarray, float] | None:
    try:
        points = np.asarray(path["points"], float)
        radius = float(path["radiusM"])
    except (KeyError, TypeError, ValueError):
        return None
    if (points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2 or
            not np.isfinite(points).all() or not np.isfinite(radius) or radius <= 0):
        return None
    # Duplicate IFC samples make a zero tangent and a degenerate boundary.
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-9]]
    return (points, radius) if len(points) >= 2 else None


def _plane_normal(points: np.ndarray) -> np.ndarray | None:
    _, singular, vectors = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    # A line has no unique curve plane.  Do not invent one: its ordinary tube
    # is the correct conservative fallback below.  Likewise, a spatial hook
    # must not be flattened merely to qualify for a shared cloth.
    if len(singular) < 3 or singular[1] <= 1e-9:
        return None
    if singular[2] > max(1e-7, singular[0]*1e-4):
        return None
    normal = vectors[-1]
    return normal / np.linalg.norm(normal)


def _parallel_groups(paths: list[dict[str, Any]]) -> tuple[list[list[tuple[dict[str, Any], np.ndarray, float, np.ndarray]]], list[dict[str, Any]]]:
    """Only group literal translations along a common curve plane normal.

    Being deliberately strict here prevents unrelated hooks that merely happen
    to be close in 3D from being bridged by a cloth.
    """
    candidates, fallback = [], []
    for path in paths:
        parsed = _path(path)
        if parsed is None:
            continue
        points, radius = parsed
        normal = _plane_normal(points)
        if normal is None:
            fallback.append(path)
        else:
            candidates.append((path, points, radius, normal))
    groups: list[list[tuple[dict[str, Any], np.ndarray, float, np.ndarray]]] = []
    while candidates:
        seed = candidates.pop(0)
        group, remainder = [seed], []
        _, base, radius, normal = seed
        for item in candidates:
            _, other, other_radius, other_normal = item
            # Matching samples make the translation test exact and retain each
            # source path's 3D placement instead of projecting it to a grid.
            if len(other) != len(base) or abs(other_radius-radius) > 1e-7 or abs(abs(np.dot(normal, other_normal))-1) > 1e-5:
                remainder.append(item); continue
            delta = other - base
            shift = delta.mean(axis=0)
            if (np.max(np.linalg.norm(delta-shift, axis=1)) > 2e-5 or
                    np.linalg.norm(shift - normal*np.dot(shift, normal)) > 2e-5):
                remainder.append(item); continue
            group.append(item)
        groups.append(group); candidates = remainder
    return groups, fallback


def _closed_ribbon(points: np.ndarray, radius: float, normal: np.ndarray,
                   low: float, high: float, end_sides: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[np.ndarray, list[list[int]]]:
    """Extrude an offset centreline ribbon between two normal coordinates."""
    segments = np.diff(points, axis=0)
    tangent = segments / np.maximum(np.linalg.norm(segments, axis=1)[:, None], 1e-12)
    segment_side = np.cross(normal, tangent)
    segment_side /= np.maximum(np.linalg.norm(segment_side, axis=1)[:, None], 1e-12)
    # A mitered offset, rather than a central-difference normal, guarantees
    # that every straight source segment is at least ``radius`` from both
    # rails.  Thus the actual circular steel surface is covered even at the
    # joins between IFC samples.  A near reversal is not a usable ribbon join.
    side = np.empty_like(points); miter = np.ones(len(points))
    side[0], side[-1] = segment_side[0], segment_side[-1]
    if end_sides is not None:
        side[0], side[-1] = end_sides
        # Source endpoint tangents can differ slightly from an arc fit's final
        # chord.  Keep the endpoint centre fixed but orient its cap to the
        # source tangent, widening the miter enough to cover both surfaces.
        miter[0] = 1 / abs(np.dot(side[0], segment_side[0]))
        miter[-1] = 1 / abs(np.dot(side[-1], segment_side[-1]))
    for index in range(1, len(points)-1):
        candidate = segment_side[index-1] + segment_side[index]
        length = np.linalg.norm(candidate)
        if length <= 1e-8:
            raise ValueError("near-reversing bend cannot form a shared cloth")
        side[index] = candidate / length
        miter[index] = 1 / min(abs(np.dot(side[index], segment_side[index-1])),
                               abs(np.dot(side[index], segment_side[index])))
    plus, minus = points + (radius*miter)[:, None]*side, points - (radius*miter)[:, None]*side
    # All inputs in a group differ only in ``normal``.  Rebuild the two end
    # planes from the representative curve; this is exactly its own placement.
    origin = points[0]
    lo_plus = plus + (low-np.dot(plus-origin, normal))[:, None]*normal
    lo_minus = minus + (low-np.dot(minus-origin, normal))[:, None]*normal
    hi_plus = plus + (high-np.dot(plus-origin, normal))[:, None]*normal
    hi_minus = minus + (high-np.dot(minus-origin, normal))[:, None]*normal
    vertices = np.vstack((lo_plus, lo_minus, hi_plus, hi_minus))
    count = len(points); lp, lm, hp, hm = 0, count, 2*count, 3*count
    triangles: list[list[int]] = []
    for i in range(count-1):
        j = i+1
        # Two planar ribbon faces, then its two long curved boundaries.
        triangles.extend(([lp+i, lm+i, lp+j], [lp+j, lm+i, lm+j],
                          [hp+i, hp+j, hm+i], [hp+j, hm+j, hm+i],
                          [lp+i, lp+j, hp+i], [lp+j, hp+j, hp+i],
                          [lm+i, hm+i, lm+j], [lm+j, hm+i, hm+j]))
    for i in (0, count-1):
        triangles.extend(([lp+i, hp+i, lm+i], [lm+i, hp+i, hm+i]))
    # Make adjacent displayed triangles traverse their common edge in opposite
    # directions.  This also protects the winding predicate from a later
    # change to one of the four ribbon-face templates above.
    adjacency: dict[tuple[int, int], list[int]] = {}
    for face_id, face in enumerate(triangles):
        for a, b in zip(face, face[1:]+face[:1]):
            adjacency.setdefault(tuple(sorted((a, b))), []).append(face_id)
    visited = {0}; pending = [0]
    while pending:
        face_id = pending.pop()
        face = triangles[face_id]
        for a, b in zip(face, face[1:]+face[:1]):
            neighbours = adjacency[tuple(sorted((a, b)))]
            other = neighbours[0] if neighbours[1] == face_id else neighbours[1]
            if other in visited:
                continue
            other_face = triangles[other]
            same_direction = any(x == a and y == b for x, y in zip(other_face, other_face[1:]+other_face[:1]))
            if same_direction:
                triangles[other] = [other_face[0], other_face[2], other_face[1]]
            visited.add(other); pending.append(other)
    if len(visited) != len(triangles):
        raise ValueError("bend cloth mesh is unexpectedly disconnected")
    # Choose the globally outward sense after the local orientation pass.
    tri = np.asarray(triangles, int)
    volume6 = np.einsum("ij,ij->i", vertices[tri[:, 0]], np.cross(vertices[tri[:, 1]], vertices[tri[:, 2]])).sum()
    if volume6 < 0:
        triangles = [[a, c, b] for a, b, c in triangles]
    return vertices, triangles


def build_bend_cloths(paths: list[dict[str, Any]], clearance_m: float = .002,
                      lateral_clearance_m: float = .005) -> list[dict[str, Any]]:
    """Build one closed shared cloth per compatible bent-bar row.

    Each mesh is kept ``swept-curve`` so existing winding membership and the
    viewer can consume it unchanged.  ``surfaceRole`` distinguishes it from a
    per-bar tube, while ``designBarIds`` preserves the complete provenance.
    """
    if not np.isfinite(clearance_m) or clearance_m < 0 or not np.isfinite(lateral_clearance_m) or lateral_clearance_m < 0:
        raise ValueError("clearances must be finite and non-negative")
    cloths = []
    groups, fallback = _parallel_groups(paths)
    for group in groups:
        path, raw, bar_radius, normal = group[0]
        dense, _ = _arc_dense(raw, min(.008, max(.002, bar_radius)))
        # Translation coordinates of every actual curve define the row span.
        coordinates = [float(np.dot(points[0]-raw[0], normal)) for _, points, _, _ in group]
        # The row end is outside the *steel surface*, not merely outside its
        # centreline.  A 4 mm bar therefore receives 4 mm + 5 mm here.
        lateral_radius = bar_radius + lateral_clearance_m
        low, high = min(coordinates)-lateral_radius, max(coordinates)+lateral_radius
        try:
            raw_tangent = np.diff(raw, axis=0)
            raw_tangent /= np.linalg.norm(raw_tangent, axis=1)[:, None]
            raw_sides = np.cross(normal, raw_tangent[[0, -1]])
            raw_sides /= np.linalg.norm(raw_sides, axis=1)[:, None]
            vertices, triangles = _closed_ribbon(dense, bar_radius+clearance_m, normal, low, high,
                                                  (raw_sides[0], raw_sides[1]))
        except ValueError:
            fallback.extend(item for item, *_rest in group)
            continue
        low_box, high_box = vertices.min(axis=0), vertices.max(axis=0)
        ids = [str(item.get("designBarId", item.get("id", index))) for index, (item, *_rest) in enumerate(group)]
        cloths.append({"kind": "swept-curve", "closed": True, "surfaceRole": "shared-bend-cloth",
                       "designBarId": ids[0] if len(ids) == 1 else "", "designBarIds": ids,
                       "verticesM": vertices.tolist(), "triangles": triangles,
                       "centerlineM": dense.tolist(), "centerlinesM": [points.tolist() for _, points, _, _ in group],
                       "radiusM": bar_radius, "clearanceM": float(clearance_m),
                       "lateralClearanceM": float(lateral_clearance_m), "aabbM": [low_box.tolist(), high_box.tolist()],
                       "membership": "solid-angle-winding-on-displayed-triangles"})
    # A two-point tail, collinear run, or genuinely spatial bend cannot define
    # one safe common ribbon.  Preserve every such path with the established
    # closed tube instead of silently losing its protection.
    for shell in build_curve_shells(fallback, clearance_m):
        shell["surfaceRole"] = "shared-bend-cloth-fallback"
        shell["fallbackReason"] = "path-has-no-single-planar-bend-surface"
        shell["designBarIds"] = [str(shell.get("designBarId", ""))]
        cloths.append(shell)
    return cloths
