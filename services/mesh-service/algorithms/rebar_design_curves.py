"""Exact and sampled design-centerline pieces between straight matching units.

The inventory's matching units remain straight and keep their historical
identities.  This module describes only the physical centerline material that
lies outside those straight ranges: terminal continuations and one join for
each adjacent pair of ranges.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import numpy as np


_EPS = 1e-9


def _vector(value: Any, field: str) -> np.ndarray:
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite numbers") from exc
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{field} must contain three finite numbers")
    return result


def transform_primitives(primitives, rotation, translation):
    """Apply a rigid transform to ordered JSON line/arc primitives.

    ``rotation`` is a proper 3x3 rotation and ``translation`` is a 3-vector.
    Arc normals rotate as directions; radii and signed sweeps are unchanged.
    The input is never mutated.
    """
    try:
        r = np.asarray(rotation, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("rotation must be a finite rigid 3x3 matrix") from exc
    t = _vector(translation, "translation")
    if (r.shape != (3, 3) or not np.isfinite(r).all()
            or not np.allclose(r.T @ r, np.eye(3), atol=1e-7)
            or not np.isclose(np.linalg.det(r), 1., atol=1e-7)):
        raise ValueError("rotation must be a finite rigid 3x3 matrix")
    if not isinstance(primitives, (list, tuple)):
        raise ValueError("primitives must be an ordered list")
    result = []
    for primitive in primitives:
        if not isinstance(primitive, dict):
            raise ValueError("each curve primitive must be an object")
        kind = primitive.get("kind")
        if kind not in ("line", "arc"):
            raise ValueError("unsupported curve primitive")
        row = deepcopy(primitive)
        source_start = _vector(primitive.get("startM"), "startM")
        source_end = _vector(primitive.get("endM"), "endM")
        if np.linalg.norm(source_end-source_start) <= _EPS:
            raise ValueError("curve primitives must be non-degenerate")
        for field in ("startM", "endM"):
            row[field] = (r @ _vector(primitive.get(field), field) + t).tolist()
        if kind == "arc":
            center = _vector(primitive.get("centerM"), "centerM")
            normal = _vector(primitive.get("normal"), "normal")
            norm = float(np.linalg.norm(normal))
            try:
                radius = float(primitive.get("radiusM"))
                sweep = float(primitive.get("sweepRad"))
            except (TypeError, ValueError) as exc:
                raise ValueError("arc dimensions must be finite numbers") from exc
            if (norm <= _EPS or not math.isfinite(radius) or radius <= _EPS
                    or not math.isfinite(sweep) or abs(sweep) <= _EPS):
                raise ValueError("arc dimensions must be finite and non-degenerate")
            row["centerM"] = (r @ center + t).tolist()
            row["normal"] = (r @ (normal / norm)).tolist()
            row["radiusM"] = radius
            row["sweepRad"] = sweep
        result.append(row)
    return result


def _primitive_path(primitives):
    if not isinstance(primitives, list) or not primitives:
        return None
    rows = []
    previous = None
    for source in primitives:
        if not isinstance(source, dict) or source.get("kind") not in ("line", "arc"):
            return None
        try:
            start = _vector(source.get("startM"), "startM")
            end = _vector(source.get("endM"), "endM")
        except ValueError:
            return None
        if previous is not None and np.linalg.norm(start - previous) > 1e-5:
            return None
        if source["kind"] == "line":
            length = float(np.linalg.norm(end - start))
            if length <= _EPS:
                return None
            row = {"kind": "line", "startM": start, "endM": end, "length": length}
        else:
            try:
                center = _vector(source.get("centerM"), "centerM")
                normal = _vector(source.get("normal"), "normal")
                radius = float(source.get("radiusM"))
                sweep = float(source.get("sweepRad"))
            except (TypeError, ValueError):
                return None
            norm = float(np.linalg.norm(normal))
            if (norm <= _EPS or not math.isfinite(radius) or radius <= _EPS
                    or not math.isfinite(sweep) or abs(sweep) <= _EPS):
                return None
            normal /= norm
            tolerance = max(1e-6, radius * 1e-5)
            if (abs(np.linalg.norm(start-center)-radius) > tolerance
                    or abs(np.linalg.norm(end-center)-radius) > tolerance):
                return None
            predicted = _rotate(start-center, normal, sweep) + center
            if np.linalg.norm(predicted-end) > tolerance:
                return None
            length = radius * abs(sweep)
            row = {"kind": "arc", "startM": start, "endM": end,
                   "centerM": center, "normal": normal, "radiusM": radius,
                   "sweepRad": sweep, "length": length}
        rows.append(row)
        previous = end
    return rows


def _sampled_path(value):
    try:
        points = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2 or not np.isfinite(points).all():
        return None
    keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > _EPS]
    points = points[keep]
    if len(points) < 2:
        return None
    return [{"kind": "line", "startM": a, "endM": b,
             "length": float(np.linalg.norm(b-a))} for a, b in zip(points[:-1], points[1:])]


def _rotate(vector, normal, angle):
    return (vector * math.cos(angle) + np.cross(normal, vector) * math.sin(angle)
            + normal * float(np.dot(normal, vector)) * (1. - math.cos(angle)))


def _point_at(row, fraction):
    fraction = min(1., max(0., float(fraction)))
    if row["kind"] == "line":
        return row["startM"] + fraction * (row["endM"] - row["startM"])
    if fraction <= 0.:
        return row["startM"].copy()
    if fraction >= 1.:
        return row["endM"].copy()
    return row["centerM"] + _rotate(
        row["startM"] - row["centerM"], row["normal"], row["sweepRad"] * fraction)


def _project_fraction(row, point):
    if row["kind"] == "line":
        delta = row["endM"] - row["startM"]
        fraction = float(np.clip(np.dot(point-row["startM"], delta) / np.dot(delta, delta), 0., 1.))
    else:
        start = row["startM"] - row["centerM"]
        radial = point - row["centerM"]
        radial -= row["normal"] * float(np.dot(radial, row["normal"]))
        if np.linalg.norm(radial) <= _EPS:
            candidates = [0., 1.]
        else:
            radial *= row["radiusM"] / np.linalg.norm(radial)
            angle = math.atan2(float(np.dot(np.cross(start, radial), row["normal"])),
                               float(np.dot(start, radial)))
            sweep = row["sweepRad"]
            travelled = angle % (2*math.pi) if sweep > 0 else -((-angle) % (2*math.pi))
            candidates = [0., 1., float(np.clip(travelled / sweep, 0., 1.))]
        fraction = min(candidates, key=lambda f: float(np.linalg.norm(_point_at(row, f)-point)))
    return fraction, float(np.linalg.norm(_point_at(row, fraction)-point))


def _station(path, starts, point):
    candidates = []
    for index, row in enumerate(path):
        fraction, distance = _project_fraction(row, point)
        candidates.append((distance, float(starts[index] + row["length"] * fraction)))
    return min(candidates, key=lambda item: item[0])[1]


def _clip(path, starts, left, right):
    result = []
    for index, row in enumerate(path):
        begin, end = starts[index], starts[index] + row["length"]
        lo, hi = max(left, begin), min(right, end)
        if hi-lo <= _EPS:
            continue
        first = _point_at(row, (lo-begin)/row["length"])
        last = _point_at(row, (hi-begin)/row["length"])
        if row["kind"] == "line":
            result.append({"kind": "line", "startM": first.tolist(), "endM": last.tolist()})
        else:
            fraction = (hi-lo)/row["length"]
            result.append({"kind": "arc", "startM": first.tolist(), "endM": last.tolist(),
                           "centerM": row["centerM"].tolist(), "normal": row["normal"].tolist(),
                           "radiusM": row["radiusM"], "sweepRad": row["sweepRad"] * fraction})
    return result


def _densify(primitives, chord_error):
    points = []
    for primitive in primitives:
        if primitive["kind"] == "line":
            part = [primitive["startM"], primitive["endM"]]
        else:
            radius, sweep = primitive["radiusM"], primitive["sweepRad"]
            ratio = min(1., chord_error / radius)
            max_step = 2. * math.acos(max(-1., min(1., 1.-ratio)))
            segments = max(1, int(math.ceil(abs(sweep) / max(max_step, 1e-12))))
            row = {**primitive, "startM": np.asarray(primitive["startM"]),
                   "endM": np.asarray(primitive["endM"]),
                   "centerM": np.asarray(primitive["centerM"]),
                   "normal": np.asarray(primitive["normal"])}
            part = [_point_at(row, index/segments).tolist() for index in range(segments+1)]
        if points and np.linalg.norm(np.asarray(points[-1])-part[0]) <= 1e-8:
            part = part[1:]
        points.extend(part)
    return points


def _piece(bar_id, kind, suffix, units, anchors, path, starts, left, right,
           radius, exact, chord_error):
    clipped = _clip(path, starts, left, right)
    if not clipped:
        return None
    centerline = _densify(clipped, chord_error)
    if len(centerline) < 2:
        return None
    length = float(right-left)
    if length <= _EPS or not math.isfinite(length):
        return None
    return {"id": f"{bar_id}/curve/{suffix}", "designBarId": bar_id,
            "kind": kind, "unitIds": [str(u) for u in units], "anchors": anchors,
            "centerlineM": centerline, "primitives": clipped if exact else [],
            "designLengthM": length, "radiusM": radius,
            "geometrySource": "ifc-analytic" if exact else "sampled-design"}


def build_design_curve_pieces(inventory, chord_error_m=0.0001):
    """Return terminal and between-unit material in physical parent order.

    Exact IFC primitives are clipped analytically.  Historical inventories use
    their original sampled polyline and are explicitly labelled
    ``sampled-design``.
    """
    try:
        chord_error = float(chord_error_m)
    except (TypeError, ValueError) as exc:
        raise ValueError("chord_error_m must be a finite positive number") from exc
    if not math.isfinite(chord_error) or chord_error <= 0:
        raise ValueError("chord_error_m must be a finite positive number")
    if not isinstance(inventory, dict):
        return []
    all_units = [row for row in inventory.get("units", []) if isinstance(row, dict)]
    units_by_bar = {}
    for unit in all_units:
        units_by_bar.setdefault(str(unit.get("designBarId")), []).append(unit)
    result = []
    for bar in inventory.get("bars", []):
        if not isinstance(bar, dict) or bar.get("designBarId") is None:
            continue
        bar_id = str(bar["designBarId"])
        exact_path = _primitive_path(bar.get("curvePrimitives"))
        exact = exact_path is not None
        path = exact_path or _sampled_path(bar.get("points"))
        if not path:
            continue
        starts = np.r_[0., np.cumsum([row["length"] for row in path[:-1]])]
        total = float(sum(row["length"] for row in path))
        positioned = []
        radii = []
        for unit in units_by_bar.get(bar_id, []):
            uid = unit.get("designUnitId")
            try:
                start = _vector(unit.get("startM"), "startM")
                end = _vector(unit.get("endM"), "endM")
            except ValueError:
                continue
            if uid is None or np.linalg.norm(end-start) <= _EPS:
                continue
            start_s, end_s = _station(path, starts, start), _station(path, starts, end)
            if abs(end_s-start_s) <= _EPS:
                continue
            lo, hi = sorted((start_s, end_s))
            positioned.append({"id": str(uid), "lo": lo, "hi": hi,
                               "loSide": "start" if start_s <= end_s else "end",
                               "hiSide": "end" if start_s <= end_s else "start"})
            try:
                unit_radius = float(unit.get("diameterM")) / 2.
                if math.isfinite(unit_radius) and unit_radius > 0:
                    radii.append(unit_radius)
            except (TypeError, ValueError):
                pass
        positioned.sort(key=lambda row: (row["lo"], row["hi"], row["id"]))
        if not positioned:
            continue
        try:
            radius = float(bar.get("radiusM"))
        except (TypeError, ValueError):
            radius = radii[0] if radii else math.nan
        if not math.isfinite(radius) or radius <= 0:
            radius = radii[0] if radii else math.nan
        if not math.isfinite(radius) or radius <= 0:
            continue
        first = positioned[0]
        if first["lo"] > _EPS:
            piece = _piece(bar_id, "terminal", f"terminal-start/{first['id']}",
                           [first["id"]], [{"designUnitId": first["id"], "side": first["loSide"]}],
                           path, starts, 0., first["lo"], radius, exact, chord_error)
            if piece:
                result.append(piece)
        # Track the furthest covered station so overlapping/nested ranges can
        # never create a false gap through material owned by another unit.
        frontier = first
        for right in positioned[1:]:
            if right["lo"]-frontier["hi"] > _EPS:
                piece = _piece(bar_id, "join", f"join/{frontier['id']}--{right['id']}",
                               [frontier["id"], right["id"]],
                               [{"designUnitId": frontier["id"], "side": frontier["hiSide"]},
                                {"designUnitId": right["id"], "side": right["loSide"]}],
                               path, starts, frontier["hi"], right["lo"], radius, exact, chord_error)
                if piece:
                    result.append(piece)
                frontier = right
            elif right["hi"] > frontier["hi"]:
                frontier = right
        last = frontier
        if total-last["hi"] > _EPS:
            piece = _piece(bar_id, "terminal", f"terminal-end/{last['id']}",
                           [last["id"]], [{"designUnitId": last["id"], "side": last["hiSide"]}],
                           path, starts, last["hi"], total, radius, exact, chord_error)
            if piece:
                result.append(piece)
    return result
