"""Closed, three-dimensional swept shells for bent rebar tails.

The mesh is a tube whose rings share vertices at every centreline sample; only
the two true ends are capped.  Membership winding-tests that exported mesh itself,
so debug geometry and the hard no-flight decision cannot diverge.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np


def _hooked(points: np.ndarray, bar: dict[str, Any]) -> tuple[bool, np.ndarray]:
    delta = np.diff(points, axis=0)
    length = np.linalg.norm(delta, axis=1)
    hooked = bool(bar.get("excludedHookRunCount")) or (len(points) > 2 and len(points) <= 4 and np.any(np.abs(delta[:, 2]) > .02))
    incline = np.divide(np.abs(delta[:, 2]), length, out=np.zeros_like(length), where=length > 1e-9)
    return hooked, (length < .20) | (incline > .12)


def split_curve_bars(inventory: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Move recognised hooked runs out of the cloth inventory.

    A curve path retains its actual endpoint and its join with the long body.
    The remaining body is emitted as contiguous non-hook runs, avoiding an
    artificial chord across a removed bend.
    """
    body = deepcopy(inventory)
    body_bars: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    for ordinal, source in enumerate(inventory.get("bars", [])):
        if not isinstance(source, dict):
            continue
        try:
            points = np.asarray(source.get("points", []), float)
            radius = float(source.get("radiusM", source.get("radius", 0)))
        except (TypeError, ValueError):
            continue
        if (points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2 or not np.isfinite(points).all()
                or not np.isfinite(radius) or radius <= 0 or source.get("coverage") == "unresolved"):
            continue
        hooked, marked = _hooked(points, source)
        if not hooked or not marked.any():
            body_bars.append(deepcopy(source)); continue
        bar_id = str(source.get("designBarId", source.get("id", ordinal)))
        # Connected marked runs form a path. Include the first join endpoint.
        start = 0
        while start < len(marked):
            if not marked[start]: start += 1; continue
            end = start + 1
            while end < len(marked) and marked[end]: end += 1
            path = points[start:end+1]
            path = path[np.r_[True, np.linalg.norm(np.diff(path, axis=0), axis=1) > 1e-7]]
            if len(path) >= 2:
                paths.append({"designBarId": bar_id, "points": path.tolist(), "radiusM": radius})
            start = end
        # Keep every unmarked contiguous section as a real body bar.
        start = 0; part = 0
        while start < len(marked):
            if marked[start]: start += 1; continue
            end = start + 1
            while end < len(marked) and not marked[end]: end += 1
            copied = deepcopy(source); copied["points"] = points[start:end+1].tolist()
            if part: copied["designBarId"] = f"{bar_id}/body{part}"
            body_bars.append(copied); part += 1; start = end
    body["bars"] = body_bars
    return body, paths


def _fit_arc(points: np.ndarray, spacing: float) -> tuple[np.ndarray, float] | None:
    """Densify only a demonstrable planar circular arc; preserve sharp V bends."""
    if len(points) < 4:
        return None
    segments = np.diff(points, axis=0)
    segments /= np.maximum(np.linalg.norm(segments, axis=1)[:, None], 1e-12)
    # Four corners of a square are exactly cocircular.  Do not turn a sparse
    # rectilinear bend into an arc merely because its algebraic residual is 0.
    if np.any(np.sum(segments[:-1]*segments[1:], axis=1) < np.cos(np.deg2rad(45.))):
        return None
    origin = points.mean(axis=0)
    _, singular, vectors = np.linalg.svd(points-origin, full_matrices=False)
    if singular[2] > max(.0008, singular[0]*.002):
        return None
    axes = vectors[:2]; xy = (points-origin) @ axes.T
    matrix = np.c_[2*xy, np.ones(len(xy))]
    solution, *_ = np.linalg.lstsq(matrix, np.sum(xy*xy, axis=1), rcond=None)
    center = solution[:2]; radius2 = solution[2] + center@center
    if radius2 <= 1e-10:
        return None
    radius = float(np.sqrt(radius2)); residual = np.abs(np.linalg.norm(xy-center, axis=1)-radius)
    if residual.max() > max(.001, radius*.015):
        return None
    angles = np.unwrap(np.arctan2(xy[:, 1]-center[1], xy[:, 0]-center[0]))
    # A genuine sampled arc has one turning direction.  Avoid making a sharp,
    # sparse polyline into a round elbow merely because three points fit a circle.
    if np.any(np.diff(angles) == 0) or np.any(np.diff(angles)[1:]*np.diff(angles)[:-1] < 0):
        return None
    count = max(len(points), int(np.ceil(abs(angles[-1]-angles[0])*radius/max(spacing, .002)))+1)
    dense_angles = np.linspace(angles[0], angles[-1], count)
    dense_xy = center + radius*np.c_[np.cos(dense_angles), np.sin(dense_angles)]
    dense = origin + dense_xy @ axes
    dense[0], dense[-1] = points[0], points[-1]  # Never move actual joins/endpoints.
    return dense, float(residual.max())


def _arc_dense(points: np.ndarray, spacing: float) -> tuple[np.ndarray, list[float]]:
    """Densify contiguous curved subpaths only; leave straight tails untouched."""
    if len(points) < 4: return points, []
    direction = np.diff(points, axis=0); direction /= np.maximum(np.linalg.norm(direction, axis=1)[:, None], 1e-12)
    turning = np.linalg.norm(np.cross(direction[:-1], direction[1:]), axis=1) > .025
    groups: list[tuple[int, int]] = []; index = 0
    while index < len(turning):
        if not turning[index]: index += 1; continue
        end = index+1
        while end < len(turning) and turning[end]: end += 1
        # Turn i is at point i+1; include the adjacent tangential samples.
        groups.append((max(0, index), min(len(points)-1, end+2))); index = end
    output = [points[0]]; residuals: list[float] = []; cursor = 0
    for start, end in groups:
        if start < cursor: continue
        output.extend(points[cursor+1:start+1])
        fitted = _fit_arc(points[start:end+1], spacing)
        if fitted is None:
            # A hook often starts with one straight lead sample before its
            # short circular tail.  Select the longest demonstrable circular
            # subrun instead of rejecting the entire connected turn group.
            best = None
            for width in range(end-start+1, 3, -1):
                for substart in range(start, end-width+2):
                    trial = _fit_arc(points[substart:substart+width], spacing)
                    if trial is not None:
                        best = (substart, substart+width-1, trial); break
                if best is not None: break
            if best is not None:
                substart, subend, fitted = best
                output.extend(points[start+1:substart+1])
                dense, residual = fitted; output.extend(dense[1:]); residuals.append(residual)
                output.extend(points[subend+1:end+1])
                cursor = end
                continue
        if fitted is None:
            output.extend(points[start+1:end+1])
        else:
            dense, residual = fitted; output.extend(dense[1:]); residuals.append(residual)
        cursor = end
    output.extend(points[cursor+1:])
    return np.asarray(output), residuals


def _transport_frames(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tangent = np.empty_like(points)
    tangent[0] = points[1]-points[0]; tangent[-1] = points[-1]-points[-2]
    tangent[1:-1] = points[2:]-points[:-2]
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-12)
    seed = np.array([0., 0., 1.]) if abs(tangent[0, 2]) < .9 else np.array([1., 0., 0.])
    normal = seed-tangent[0]*(seed@tangent[0]); normal /= np.linalg.norm(normal)
    normals = [normal]
    for previous, current in zip(tangent[:-1], tangent[1:]):
        axis = np.cross(previous, current); sine = np.linalg.norm(axis); cosine = float(previous@current)
        if sine > 1e-10:
            axis /= sine
            n = normals[-1]*cosine + np.cross(axis, normals[-1])*sine + axis*(axis@normals[-1])*(1-cosine)
        elif cosine < 0:  # Degenerate reversal: retain a stable perpendicular frame.
            n = -normals[-1]
        else: n = normals[-1]
        n -= current*(n@current); n /= max(np.linalg.norm(n), 1e-12); normals.append(n)
    return tangent, np.asarray(normals)


def build_curve_shells(curve_paths: list[dict[str, Any]], clearance_m: float = .003) -> list[dict[str, Any]]:
    if not np.isfinite(clearance_m) or clearance_m < 0:
        raise ValueError("clearance_m must be finite and non-negative")
    shells = []
    sides = 12
    for path in curve_paths:
        try:
            raw = np.asarray(path["points"], float); radius = float(path["radiusM"])
        except (KeyError, TypeError, ValueError):
            continue
        if raw.ndim != 2 or raw.shape[1:] != (3,) or len(raw) < 2 or not np.isfinite(raw).all() or radius <= 0:
            continue
        points, arc_residuals = _arc_dense(raw, min(.008, max(.002, radius)))
        tangent, normal = _transport_frames(points)
        binormal = np.cross(tangent, normal)
        total_radius = radius+clearance_m
        angle = np.arange(sides)*2*np.pi/sides
        rings = np.array([point + total_radius*(np.cos(angle)[:, None]*n + np.sin(angle)[:, None]*b)
                          for point, n, b in zip(points, normal, binormal)])
        vertices = rings.reshape(-1, 3).tolist()
        triangles: list[list[int]] = []
        for row in range(len(points)-1):
            for side in range(sides):
                a, b = row*sides+side, row*sides+(side+1) % sides
                c, d = (row+1)*sides+side, (row+1)*sides+(side+1) % sides
                triangles.extend(([a, b, c], [b, d, c]))
        # True endpoints are capped; no cap is inserted at interior bend rings.
        first_center, last_center = len(vertices), len(vertices)+1
        vertices.extend((points[0].tolist(), points[-1].tolist()))
        for side in range(sides):
            following = (side+1) % sides
            triangles.append([first_center, following, side])
            triangles.append([last_center, (len(points)-1)*sides+side, (len(points)-1)*sides+following])
        low, high = np.min(np.asarray(vertices), axis=0), np.max(np.asarray(vertices), axis=0)
        shells.append({"kind": "swept-curve", "closed": True, "designBarId": path.get("designBarId", ""),
                       "verticesM": vertices, "triangles": triangles, "centerlineM": points.tolist(),
                       "radiusM": radius, "clearanceM": float(clearance_m), "ringSides": sides,
                       "aabbM": [low.tolist(), high.tolist()], "membership": "solid-angle-winding-on-displayed-triangles"})
        shells[-1]["arcFitCount"] = len(arc_residuals); shells[-1]["arcFitMaxResidualM"] = max(arc_residuals, default=0.)
    return shells


def _inside_mesh(points: np.ndarray, vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Winding membership against the exact exported triangles, in small batches."""
    result = np.zeros(len(points), bool)
    tri = vertices[triangles]; edge1, edge2 = tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]
    for start in range(0, len(points), 384):
        query = points[start:start+384]
        # Treat the displayed surface itself as inside.  This removes ray
        # ambiguity at shared triangle edges before parity testing.
        normal = np.cross(edge1, edge2); norm2 = np.einsum("ti,ti->t", normal, normal)
        relative = query[None, :, :]-tri[:, None, 0, :]
        plane = np.abs(np.einsum("tqi,ti->tq", relative, normal)) <= 1e-8*np.sqrt(norm2)[:, None]
        dot00 = np.einsum("ti,ti->t", edge1, edge1)[:, None]; dot01 = np.einsum("ti,ti->t", edge1, edge2)[:, None]
        dot11 = np.einsum("ti,ti->t", edge2, edge2)[:, None]
        dot20 = np.einsum("tqi,ti->tq", relative, edge1); dot21 = np.einsum("tqi,ti->tq", relative, edge2)
        denominator = dot00*dot11-dot01*dot01
        u_surface = (dot11*dot20-dot01*dot21)/np.maximum(denominator, 1e-18)
        v_surface = (dot00*dot21-dot01*dot20)/np.maximum(denominator, 1e-18)
        on_surface = plane & (u_surface >= -1e-8) & (v_surface >= -1e-8) & (u_surface+v_surface <= 1+1e-8)
        a, b, c = tri[:, None, 0, :]-query, tri[:, None, 1, :]-query, tri[:, None, 2, :]-query
        la, lb, lc = np.linalg.norm(a, axis=2), np.linalg.norm(b, axis=2), np.linalg.norm(c, axis=2)
        numerator = np.einsum("tqi,tqi->tq", a, np.cross(b, c))
        denominator = la*lb*lc + np.einsum("tqi,tqi->tq", a, b)*lc + np.einsum("tqi,tqi->tq", b, c)*la + np.einsum("tqi,tqi->tq", c, a)*lb
        winding = np.abs(np.sum(2*np.arctan2(numerator, denominator), axis=0))
        result[start:start+len(query)] = on_surface.any(axis=0) | (winding > 2*np.pi)
    return result


def inside_curve_shells(points: Any, shells: list[dict[str, Any]], chunk_size: int = 65536) -> np.ndarray:
    sample = np.asarray(points, float)
    if sample.ndim != 2 or sample.shape[1:] != (3,):
        raise ValueError("points must have shape (N, 3)")
    result = np.zeros(len(sample), bool)
    finite = np.isfinite(sample).all(axis=1)
    for shell in shells:
        if not isinstance(shell, dict) or not shell.get("closed") or shell.get("kind") != "swept-curve": continue
        vertices, triangles = np.asarray(shell.get("verticesM"), float), np.asarray(shell.get("triangles"), int)
        low, high = np.asarray(shell.get("aabbM"), float)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,) or triangles.ndim != 2 or triangles.shape[1:] != (3,): continue
        candidate = np.flatnonzero(finite & ~result & np.all(sample >= low, axis=1) & np.all(sample <= high, axis=1))
        for start in range(0, len(candidate), max(1, chunk_size)):
            ids = candidate[start:start+max(1, chunk_size)]
            result[ids] = _inside_mesh(sample[ids], vertices, triangles)
    return result
