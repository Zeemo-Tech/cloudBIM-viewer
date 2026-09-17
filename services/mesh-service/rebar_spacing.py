"""Conservative adjacent-bar spacing from design topology and control evidence."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def _vector(value):
    try:
        result = np.asarray(value, float)
    except (TypeError, ValueError):
        return None
    if result.shape != (3,) or not np.isfinite(result).all() or np.linalg.norm(result) <= 1e-9:
        return None
    return result / np.linalg.norm(result)


def _canonical(direction):
    direction = direction.copy()
    at = int(np.argmax(np.abs(direction)))
    if direction[at] < 0:
        direction *= -1
    return direction


def _family(row, direction):
    for key in ("spacingFamilyId", "familyId", "barFamilyId"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    quantized = ",".join(str(int(round(value * 1000))) for value in _canonical(direction))
    return f"derived:{row.get('kind', 'straight')}:{quantized}"


def _curve_at_plane(curve, direction, station):
    curve = np.asarray(curve, float)
    values = curve @ direction
    candidates = []
    for index, (left, right) in enumerate(zip(values, values[1:])):
        if station < min(left, right) - 1e-9 or station > max(left, right) + 1e-9:
            continue
        if abs(right - left) <= 1e-12:
            continue
        fraction = (station - left) / (right - left)
        candidates.append(curve[index] + fraction * (curve[index + 1] - curve[index]))
    if len(candidates) != 1:
        return None
    return candidates[0]


def _observed_center(rows, direction, station):
    values = []
    for row in rows:
        value = _curve_at_plane(row["curve"], direction, station)
        if value is not None:
            values.append(value)
    if len(values) != 1:
        return None
    return values[0]


def spacing_rows(inventory: dict[str, Any], evidence: dict[str, list[dict[str, Any]]],
                 bars_by_id: dict[str, dict[str, Any]], tolerance: float,
                 alignment: np.ndarray | None = None):
    """Measure only adjacent members of trustworthy one-dimensional families."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    reasons: dict[str, int] = {}
    for source in inventory.get("units", []):
        if not isinstance(source, dict) or not isinstance(source.get("designUnitId"), str):
            continue
        direction = _vector(source.get("direction"))
        if direction is None:
            try:
                direction = _vector(np.asarray(source["endM"], float) - np.asarray(source["startM"], float))
            except (KeyError, TypeError, ValueError):
                direction = None
        if direction is None:
            reasons["missing-explicit-design-direction"] = reasons.get("missing-explicit-design-direction", 0) + 1
            continue
        # start/end alone are geometry; production spacing requires the saved
        # explicit direction so that curved/multipart intent is not guessed.
        if source.get("direction") is None:
            reasons["missing-explicit-design-direction"] = reasons.get("missing-explicit-design-direction", 0) + 1
            continue
        if source.get("layerId") is None:
            reasons["missing-explicit-layer"] = reasons.get("missing-explicit-layer", 0) + 1
            continue
        try:
            start, end = np.asarray(source["startM"], float), np.asarray(source["endM"], float)
            radius = float(source["diameterM"]) / 2
        except (KeyError, TypeError, ValueError):
            reasons["invalid-design-geometry"] = reasons.get("invalid-design-geometry", 0) + 1
            continue
        if start.shape != (3,) or end.shape != (3,) or not np.isfinite([*start, *end, radius]).all() or radius <= 0:
            reasons["invalid-design-geometry"] = reasons.get("invalid-design-geometry", 0) + 1
            continue
        if alignment is not None:
            transformed = (alignment @ np.column_stack((np.vstack((start, end)), np.ones(2))).T).T
            if np.any(np.abs(transformed[:, 3]) <= 1e-12):
                reasons["invalid-alignment"] = reasons.get("invalid-alignment", 0) + 1
                continue
            start, end = transformed[:, :3] / transformed[:, 3, None]
            direction = _vector(alignment[:3, :3] @ direction)
            if direction is None:
                reasons["invalid-alignment"] = reasons.get("invalid-alignment", 0) + 1
                continue
        row = {"source": source, "unitId": source["designUnitId"],
               "barId": str(source.get("designBarId") or ""), "direction": _canonical(direction),
               "start": start, "end": end, "center": (start + end) / 2, "radius": radius}
        groups.setdefault((str(source["layerId"]), _family(source, direction)), []).append(row)

    output = []
    for (layer_id, family_id), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        reference = group[0]["direction"]
        if any(abs(row["direction"] @ reference) < .995 for row in group[1:]):
            reasons["ambiguous-family-direction"] = reasons.get("ambiguous-family-direction", 0) + len(group)
            continue
        centers = np.asarray([row["center"] for row in group])
        transverse = centers - (centers @ reference)[:, None] * reference
        centered = transverse - transverse.mean(axis=0)
        _, singular, vh = np.linalg.svd(centered, full_matrices=False)
        spacing_direction = vh[0] - reference * (vh[0] @ reference)
        spacing_norm = np.linalg.norm(spacing_direction)
        if spacing_norm <= 1e-9 or (len(singular) > 1 and singular[1] > max(.002, .12 * singular[0])):
            reasons["ambiguous-spacing-order"] = reasons.get("ambiguous-spacing-order", 0) + len(group)
            continue
        spacing_direction = _canonical(spacing_direction / spacing_norm)
        order = sorted(group, key=lambda row: float(row["center"] @ spacing_direction))
        for left, right in zip(order, order[1:]):
            if left["barId"] == right["barId"]:
                continue
            design_delta = right["center"] - left["center"]
            design_distance = abs(float(design_delta @ spacing_direction))
            low = max(min(left["start"] @ reference, left["end"] @ reference),
                      min(right["start"] @ reference, right["end"] @ reference))
            high = min(max(left["start"] @ reference, left["end"] @ reference),
                       max(right["start"] @ reference, right["end"] @ reference))
            stations = np.linspace(low, high, 9) if high > low + 1e-6 else np.asarray([low])
            samples = []
            actuals = []
            known_mask = []
            for station in stations:
                a = _observed_center(evidence.get(left["unitId"], []), reference, station)
                b = _observed_center(evidence.get(right["unitId"], []), reference, station)
                known = a is not None and b is not None
                known_mask.append(known)
                if known:
                    actual = abs(float((b - a) @ spacing_direction))
                    difference = actual - design_distance
                    actuals.append(actual)
                    samples.append({"stationM": float(station - low), "status": "supported",
                                    "centerA": a.tolist(), "centerB": b.tolist(),
                                    "actualCenterDistanceM": actual,
                                    "signedDifferenceM": difference,
                                    "netClearanceM": actual - left["radius"] - right["radius"],
                                    "withinTolerance": abs(difference) <= tolerance})
                else:
                    samples.append({"stationM": float(station - low), "status": "unknown",
                                    "centerA": None, "centerB": None,
                                    "actualCenterDistanceM": None, "signedDifferenceM": None,
                                    "netClearanceM": None, "withinTolerance": None})
            actual = float(np.median(actuals)) if actuals else None
            difference = actual - design_distance if actual is not None else None
            supported_span = sum(float(stations[i + 1] - stations[i]) for i in range(len(stations) - 1)
                                 if known_mask[i] and known_mask[i + 1])
            coverage = ("supported" if known_mask and all(known_mask) else
                        "partial" if any(known_mask) else "unavailable")
            left_bar, right_bar = bars_by_id.get(left["barId"], {}), bars_by_id.get(right["barId"], {})
            output.append({
                "pairId": "|".join(sorted((left["unitId"], right["unitId"]))),
                "designBarIds": [left["barId"], right["barId"]],
                "ifcGlobalIds": [str(left_bar.get("ifcGlobalId") or ""), str(right_bar.get("ifcGlobalId") or "")],
                "designUnitIds": [left["unitId"], right["unitId"]],
                "familyId": family_id, "layerId": layer_id,
                "designDirection": reference.tolist(), "spacingDirection": spacing_direction.tolist(),
                "designCenterDistanceM": design_distance, "radiusSource": "design-prior",
                "designRadiiM": [left["radius"], right["radius"]], "samples": samples,
                "actualCenterDistanceM": actual, "signedDifferenceM": difference,
                "netClearanceM": None if actual is None else actual - left["radius"] - right["radius"],
                "toleranceM": float(tolerance),
                "withinTolerance": None if difference is None else abs(difference) <= tolerance,
                "coverage": {"status": coverage, "sampleCount": len(actuals),
                             "sharedSpanM": max(0., float(high - low)),
                             "supportedSpanM": supported_span,
                             "reason": ("supported" if coverage == "supported" else
                                        "sparse-observation" if coverage == "partial" else "unmatched-unit")},
            })
    return output, reasons
