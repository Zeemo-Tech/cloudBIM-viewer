"""Control-net evidence for production scan-to-design rebar comparison.

Only real scan records become surface correspondences.  Fitted control curves
provide local centre/tangent geometry and are clipped to intervals supported by
those records; inferred geometry never fills an unobserved station or side.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.spatial import cKDTree


CONTROL_SCHEMA = "rebar-control-net-evidence-v1"
INSPECTION_SCHEMA = "rebar-inspection-v1"
METHOD = "control-net-real-point-radial-correspondence-v1"


def control_envelope(instance_map: dict[str, Any]) -> dict[str, Any] | None:
    value = instance_map.get("controlNet")
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("schema") != CONTROL_SCHEMA
            or value.get("coordinateFrame") != "scan"
            or not isinstance(value.get("algorithmVersion"), str)
            or not value["algorithmVersion"]
            or not isinstance(value.get("report"), dict)):
        raise ValueError("instance map controlNet envelope is invalid")
    return value


def transform_points(points: Any, matrix: np.ndarray) -> np.ndarray:
    """Apply the already-decoded scan-to-BIM matrix exactly once."""
    xyz = np.asarray(points, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not np.isfinite(xyz).all():
        raise ValueError("control geometry must contain finite XYZ rows")
    homogeneous = (matrix @ np.column_stack((xyz, np.ones(len(xyz)))).T).T
    if np.any(np.abs(homogeneous[:, 3]) <= 1e-12):
        raise ValueError("control geometry has invalid homogeneous coordinates")
    return homogeneous[:, :3] / homogeneous[:, 3, None]


def _polyline_projection(points: np.ndarray, curve: np.ndarray):
    segments = np.diff(curve, axis=0)
    lengths = np.linalg.norm(segments, axis=1)
    valid = lengths > 1e-9
    segments, lengths = segments[valid], lengths[valid]
    starts = curve[:-1][valid]
    if not len(segments):
        return None
    tangents = segments / lengths[:, None]
    prefix = np.r_[0., np.cumsum(lengths)]
    delta = points[:, None, :] - starts[None, :, :]
    local = np.clip(np.einsum("nsi,si->ns", delta, tangents), 0., lengths)
    projected = starts[None, :, :] + local[:, :, None] * tangents[None, :, :]
    squared = np.einsum("nsi,nsi->ns", points[:, None, :] - projected,
                        points[:, None, :] - projected)
    segment = np.argmin(squared, axis=1)
    row = np.arange(len(points))
    return (np.sqrt(squared[row, segment]), projected[row, segment],
            tangents[segment], prefix[segment] + local[row, segment], prefix[-1])


def _supported_intervals(stations: np.ndarray, radius: float, explicit=None) -> list[tuple[float, float]]:
    if explicit is not None:
        result = []
        for value in explicit:
            try:
                a, b = map(float, value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(a) and math.isfinite(b) and b > a:
                result.append((a, b))
        return result
    stations = np.sort(np.asarray(stations, float))
    if not len(stations):
        return []
    groups = np.split(stations, np.flatnonzero(np.diff(stations) > max(.018, 4 * radius)) + 1)
    # Six real records is deliberately modest: angular/radius gates below still
    # decide whether any record can become correspondence evidence.
    return [(float(group[0]), float(group[-1])) for group in groups
            if len(group) >= 6 and group[-1] - group[0] > 1e-6]


def _slice_polyline(curve: np.ndarray, start: float, stop: float) -> np.ndarray:
    projection = _polyline_projection(np.asarray([[0., 0., 0.]]), curve)
    if projection is None:
        return np.empty((0, 3))
    lengths = np.linalg.norm(np.diff(curve, axis=0), axis=1)
    prefix = np.r_[0., np.cumsum(lengths)]
    start, stop = np.clip([start, stop], 0., prefix[-1])
    if stop <= start:
        return np.empty((0, 3))

    def at(station):
        index = min(np.searchsorted(prefix, station, side="right") - 1, len(lengths) - 1)
        index = max(index, 0)
        fraction = (station - prefix[index]) / max(lengths[index], 1e-12)
        return curve[index] + fraction * (curve[index + 1] - curve[index])

    inside = curve[(prefix >= start) & (prefix <= stop)]
    return np.vstack((at(start), inside, at(stop)))


def build_control_units(instance_map: dict[str, Any], matrix: np.ndarray,
                        unit_points: dict[str, np.ndarray]) -> dict[str, list[dict[str, Any]]]:
    """Return trusted, support-clipped observed geometry by stable design unit."""
    envelope = control_envelope(instance_map)
    if envelope is None:
        return {}
    report = envelope["report"]
    inventory = instance_map["inventory"]
    radii = {str(row.get("designUnitId")): float(row["diameterM"]) / 2
             for row in inventory.get("units", [])
             if isinstance(row, dict) and isinstance(row.get("designUnitId"), str)
             and isinstance(row.get("diameterM"), (int, float)) and row["diameterM"] > 0}
    output: dict[str, list[dict[str, Any]]] = {}
    report_ids: dict[int, str] = {}

    def add(unit_id: str, kind: str, curve_value: Any, point_count: int,
            explicit_intervals=None):
        if unit_id not in radii or unit_id not in unit_points:
            return
        try:
            curve = transform_points(curve_value, matrix)
        except (TypeError, ValueError):
            return
        if len(curve) < 2:
            return
        points = unit_points[unit_id]
        projected = _polyline_projection(points, curve)
        if projected is None:
            return
        radial, centers, tangents, stations, length = projected
        radius = radii[unit_id]
        surface = np.abs(radial - radius) <= max(.001, .36 * radius)
        intervals = _supported_intervals(stations[surface], radius, explicit_intervals)
        for start, stop in intervals:
            start, stop = max(0., start), min(length, stop)
            clipped = _slice_polyline(curve, start, stop)
            if len(clipped) < 2:
                continue
            in_interval = surface & (stations >= start - 1e-9) & (stations <= stop + 1e-9)
            if not np.any(in_interval):
                continue
            output.setdefault(unit_id, []).append({
                "designUnitId": unit_id, "kind": kind,
                "evidence": "fitted-control-net" if explicit_intervals is None else "local-control-support",
                "curve": clipped, "points": points[in_interval],
                "centers": centers[in_interval], "tangents": tangents[in_interval],
                "stations": stations[in_interval] - start,
                "normals": (points[in_interval] - centers[in_interval])
                           / np.maximum(radial[in_interval, None], 1e-12),
                "radius": radius, "pointCount": min(int(point_count), int(np.count_nonzero(in_interval))),
            })

    for row in report.get("instances", []):
        if not isinstance(row, dict) or not isinstance(row.get("designUnitId"), str):
            continue
        if type(row.get("id")) is int:
            report_ids[row["id"]] = row["designUnitId"]
        if row.get("status") == "fitted" and isinstance(row.get("centerlineM"), list):
            add(row["designUnitId"], "body", row["centerlineM"], int(row.get("pointCount") or 0))

    for row in report.get("curvedPieces", []):
        if not isinstance(row, dict):
            continue
        unit_ids = [report_ids[value] for value in row.get("unitIds", []) if value in report_ids]
        if row.get("status") == "fitted" and isinstance(row.get("centerlineM"), list):
            for unit_id in unit_ids:
                add(unit_id, "curve", row["centerlineM"], int(row.get("pointCount") or 0))
        elif (isinstance(row.get("inferredCenterlineM"), list)
              and isinstance(row.get("localSupport"), dict)):
            intervals = row["localSupport"].get("intervalsM")
            for unit_id in unit_ids:
                add(unit_id, "curve", row["inferredCenterlineM"],
                    int(row["localSupport"].get("pointCount") or 0), intervals)
    return output


class ControlNetObservedSurface:
    """Same-side matcher driven by trusted control geometry and actual points."""

    def __init__(self, design_segments, units):
        self.design = {unit_id: (np.asarray(start, float), np.asarray(end, float))
                       for unit_id, start, end in design_segments}
        self.units = units
        self.diagnostics = {
            "method": METHOD,
            "trustedSegmentCount": sum(map(len, units.values())),
            "supportedPointCount": sum(len(item["points"]) for rows in units.values() for item in rows),
            "inferredGeometryUsed": False,
        }

    def match(self, vertices, normals, *, k=32, max_angle_deg=30.,
              max_search_distance=.2, half_space_only=False, trace=False):
        del normals  # signed direction comes from immutable design centreline geometry
        values = np.full(len(vertices), np.nan)
        selected = np.full(len(vertices), -1, np.int64)
        reasons = np.full(len(vertices), "no-trusted-control-geometry", dtype=object)
        cosine = math.cos(math.radians(max_angle_deg))
        owner = np.full(len(vertices), "", dtype=object)
        best = np.full(len(vertices), np.inf)
        design_radials: dict[str, np.ndarray] = {}
        for unit_id, (start, end) in self.design.items():
            axis = end - start
            length = np.linalg.norm(axis)
            if length <= 1e-9:
                continue
            tangent = axis / length
            station = np.clip((vertices - start) @ tangent, 0., length)
            center = start + station[:, None] * tangent
            radial = vertices - center
            squared = np.einsum("ij,ij->i", radial, radial)
            take = squared < best
            owner[take], best[take] = unit_id, squared[take]
            design_radials[unit_id] = radial

        point_offset = 0
        for unit_id, rows in self.units.items():
            ids = np.flatnonzero(owner == unit_id)
            if not len(ids) or unit_id not in design_radials:
                point_offset += sum(len(row["points"]) for row in rows)
                continue
            radial = design_radials[unit_id][ids]
            norm = np.linalg.norm(radial, axis=1)
            valid_vertex = norm > 1e-8
            direction = radial / np.maximum(norm[:, None], 1e-12)
            local_best = np.full(len(ids), np.inf)
            local_value = np.full(len(ids), np.nan)
            local_selected = np.full(len(ids), -1, np.int64)
            local_reason = np.full(len(ids), "unsupported-station-or-side", dtype=object)
            for item in rows:
                projection = _polyline_projection(vertices[ids], item["curve"])
                if projection is None:
                    point_offset += len(item["points"])
                    continue
                _, _query_centers, query_tangents, query_stations, _ = projection
                # Project the immutable design radial into the observed local
                # transverse plane.  Using vertex-minus-observed-centre flips
                # sides once centre displacement exceeds a diameter.
                query_radial = direction - np.einsum("ij,ij->i", direction, query_tangents)[:, None] * query_tangents
                qnorm = np.linalg.norm(query_radial, axis=1)
                qdirection = query_radial / np.maximum(qnorm[:, None], 1e-12)
                # Design radial fixes the outward sign.  The control tangent fixes
                # the local angular side, including acute bends and bad mesh normals.
                aligned_frame = qnorm > 1e-8
                tree = cKDTree(np.column_stack((item["stations"] / max(.002, 4 * item["radius"]),
                                                 item["normals"] / max(1e-8, math.sqrt(2 - 2*cosine)))))
                query = np.column_stack((query_stations / max(.002, 4 * item["radius"]),
                                         qdirection / max(1e-8, math.sqrt(2 - 2*cosine))))
                batch = min(max(1, int(k)), 64, len(item["points"]))
                distance, index = tree.query(query, k=batch)
                distance = np.asarray(distance).reshape(len(ids), batch)
                index = np.asarray(index).reshape(len(ids), batch)
                candidate = item["points"][index]
                delta = candidate - vertices[ids, None, :]
                angular = np.einsum("ijk,ik->ij", item["normals"][index], qdirection)
                spatial = np.linalg.norm(delta, axis=2)
                valid = (angular >= cosine - 1e-12) & (spatial <= max_search_distance + 1e-12)
                signed = np.einsum("ijk,ik->ij", delta, direction)
                if half_space_only:
                    valid &= signed >= 0
                score = np.where(valid, spatial, np.inf)
                chosen = np.argmin(score, axis=1)
                score = score[np.arange(len(ids)), chosen]
                improve = valid_vertex & aligned_frame & (score < local_best)
                local_best[improve] = score[improve]
                local_value[improve] = signed[np.flatnonzero(improve), chosen[improve]]
                local_selected[improve] = point_offset + index[np.flatnonzero(improve), chosen[improve]]
                local_reason[improve] = "matched"
                point_offset += len(item["points"])
            values[ids] = local_value
            selected[ids] = local_selected
            reasons[ids] = local_reason
        if trace:
            return values, selected, np.isfinite(values), reasons.tolist()
        return values, selected, np.isfinite(values)


def finite_or_none(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def build_inspection(instance_map: dict[str, Any], instance_hash: str,
                     alignment_matrix: list[float], comparison_rows: list[dict[str, Any]],
                     units: dict[str, list[dict[str, Any]]], spacing: list[dict[str, Any]],
                     spacing_unavailable: dict[str, int], tolerance: float,
                     *, same_side_surface: bool) -> dict[str, Any]:
    envelope = control_envelope(instance_map)
    if envelope is None:
        raise ValueError("inspection requires control-net evidence")
    unit_sources = {str(row.get("designUnitId")): row
                    for row in instance_map["inventory"].get("units", [])
                    if isinstance(row, dict) and isinstance(row.get("designUnitId"), str)}
    units_by_bar: dict[str, list[str]] = {}
    for unit_id, source in unit_sources.items():
        if isinstance(source.get("designBarId"), str):
            units_by_bar.setdefault(source["designBarId"], []).append(unit_id)
    bars = []
    for comparison in comparison_rows:
        bar_id = comparison["designBarId"]
        unit_ids = units_by_bar.get(bar_id, [])
        segments = []
        supported_units = set()
        for unit_id in unit_ids:
            for item in units.get(unit_id, []):
                curve = item["curve"]
                supported_units.add(unit_id)
                segments.append({
                    "designUnitId": unit_id, "kind": item["kind"], "evidence": item["evidence"],
                    "centerline": curve.tolist(), "supportedIntervalsM": [[0., float(np.linalg.norm(np.diff(curve, axis=0), axis=1).sum())]],
                    "pointCount": int(len(item["points"])), "radiusM": float(item["radius"]),
                    "radiusSource": "design-prior",
                })
        if comparison["status"] == "review":
            status, reason = "review", "review-required"
        elif not supported_units:
            status = "missing" if comparison["status"] == "missing" else "unavailable"
            reason = "no-trusted-control-geometry"
        elif len(supported_units) == len(unit_ids):
            status, reason = "supported", "supported"
        else:
            status, reason = "partial", "partial-control-net"
        stats = comparison.get("stats") or {}
        bars.append({
            "designBarId": bar_id, "ifcGlobalId": comparison["ifcGlobalId"], "status": status,
            "unitIds": unit_ids, "observedSegments": segments,
            "knownVertexCount": int(comparison["knownCount"]),
            "unknownVertexCount": int(comparison["unknownCount"]),
            "toleranceM": float(tolerance),
            "withinToleranceRatio": finite_or_none(stats.get("withinToleranceRatio")),
            "quality": {"supportedUnitCount": len(supported_units), "designUnitCount": len(unit_ids),
                        "reason": reason},
        })
    counts = {name: sum(row["status"] == name for row in bars)
              for name in ("supported", "partial", "missing", "review", "unavailable")}
    spacing_counts = {name: sum(row["coverage"]["status"] == name for row in spacing)
                      for name in ("supported", "partial", "unavailable")}
    return {
        "schema": INSPECTION_SCHEMA, "coordinateFrame": "bim", "lengthUnit": "m",
        "method": (METHOD if same_side_surface else
                   "control-net-inspection-with-unconstrained-nearest-surface-v1"),
        "provenance": {"instanceMapHash": instance_hash,
                       "controlNetAlgorithmVersion": envelope["algorithmVersion"],
                       "alignmentMatrix": [float(value) for value in alignment_matrix]},
        "bars": bars, "spacing": spacing,
        "summary": {
            "barCount": len(bars), "supportedBarCount": counts["supported"],
            "partialBarCount": counts["partial"],
            "unavailableBarCount": counts["missing"] + counts["review"] + counts["unavailable"],
            "spacingPairCount": len(spacing),
            "supportedSpacingPairCount": spacing_counts["supported"],
            "partialSpacingPairCount": spacing_counts["partial"],
            "unavailableSpacingPairCount": spacing_counts["unavailable"],
            "spacingUnavailableReasonCounts": spacing_unavailable,
            "toleranceM": float(tolerance), "toleranceBasis": "request.toleranceLimit",
        },
    }


def refresh_inspection_tolerance(inspection: dict[str, Any], tolerance: float) -> None:
    inspection["summary"]["toleranceM"] = float(tolerance)
    for bar in inspection["bars"]:
        bar["toleranceM"] = float(tolerance)
    for spacing in inspection["spacing"]:
        spacing["toleranceM"] = float(tolerance)
        for sample in spacing["samples"]:
            difference = sample.get("signedDifferenceM")
            sample["withinTolerance"] = None if difference is None else abs(difference) <= tolerance
        difference = spacing.get("signedDifferenceM")
        spacing["withinTolerance"] = None if difference is None else abs(difference) <= tolerance
