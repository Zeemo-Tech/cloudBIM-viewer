"""Conservative, scan-frame shape denoising for completed rebar instances.

The inventory supplies dimensions and intrinsic straight/bend shape only.
World-space design pose is discarded by the template canonicalization; the
axis, endpoint placement and bend roll come from owned scan points. Uncertain
curved regions retain points for review.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from rebar_prior_axis import fit_independent_axis
from algorithms.rebar_shape_templates import build_unit_shape_templates
from algorithms.rebar_shape_fit import fit_bent_shape


VERSION = "instance-cylinder-denoise-v3"
_MIN_POINTS = 48


def _empty_report(point_count: int, reason: str, instances: list[dict[str, Any]], *, expected: int = 0,
                  observed: int = 0, unparsed: int = 0) -> dict[str, Any]:
    return {
        "version": VERSION, "pointCount": int(point_count), "instances": instances,
        "removedPointCount": 0,
        "validation": {"status": "not_applied", "reason": reason,
                       "observedInstanceCount": int(observed), "expectedMatchingUnitCount": int(expected),
                       "observedInstanceCountAfter": int(observed),
                       "unparsedDesignBarCount": int(unparsed), "candidateInstanceCount": 0,
                       "appliedInstanceCount": 0},
    }


def _json_number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _report_instances(complete_report: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(complete_report, dict):
        return {}
    return {int(row["id"]): row for row in complete_report.get("instances", [])
            if isinstance(row, dict) and isinstance(row.get("id"), (int, np.integer))}


def _bent_or_hook(instance_id: int, complete_report: Any, unit: dict[str, Any] | None) -> bool:
    """Do not fit a whole hooked/bent ownership group as a straight cylinder."""
    if not isinstance(complete_report, dict):
        return False
    segments = [s for s in complete_report.get("segments", [])
                if isinstance(s, dict) and s.get("instanceId") == instance_id]
    directions, lengths = [], []
    for segment in segments:
        try:
            delta = np.asarray(segment["endM"], float) - np.asarray(segment["startM"], float)
            length = np.linalg.norm(delta)
            if length > 1e-8 and np.isfinite(delta).all():
                directions.append(delta / length)
                lengths.append(length)
        except (KeyError, TypeError, ValueError):
            continue
    if len(directions) > 1:
        # Compare local directions with the length-weighted consensus axis.
        # Pairwise comparisons double opposite small errors (+6/-6 degrees)
        # and incorrectly veto otherwise well-supported straight bars.
        directions = np.asarray(directions)
        _, vectors = np.linalg.eigh(directions.T @ (np.asarray(lengths)[:, None] * directions))
        if np.any(np.abs(directions @ vectors[:, -1]) < math.cos(math.radians(8))):
            return True
    # A multi-run parent without an exact unit association may contain a hook;
    # retaining it is safer than making an ungrounded straight-cylinder claim.
    return unit is None and len(segments) > 1


def _axis_centerline(axis: dict[str, Any], design_length: float) -> list[list[float]]:
    """Constrain polyline arc length without importing the design pose.

    Centre the known length on the observed arc midpoint. Trim within the
    fitted span; extend missing ends along endpoint tangents, not unbounded
    spline extrapolation. These modelled ends are not measured scan points.
    """
    low, high = axis["observedRange"]
    stations = np.linspace(low, high, 9)
    spline = axis["spline"]
    centers = []
    for station in stations:
        offset = spline(station / axis["length"]) @ axis["coeff"]
        center = axis["start"] + station * axis["tangent"] + offset[0] * axis["u"] + offset[1] * axis["v"]
        centers.append([float(v) for v in center])
    centers = np.asarray(centers)
    distances = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    cumulative = np.r_[0., np.cumsum(distances)]
    first = (cumulative[-1] - design_length) / 2
    last = first + design_length

    def at(station):
        if station < 0:
            return centers[0] + station * (centers[1] - centers[0]) / distances[0]
        if station > cumulative[-1]:
            return centers[-1] + (station - cumulative[-1]) * (centers[-1] - centers[-2]) / distances[-1]
        return np.array([np.interp(station, cumulative, centers[:, dimension]) for dimension in range(3)])

    interior = centers[(cumulative > first) & (cumulative < last)]
    return np.vstack((at(first), interior, at(last))).tolist()


def fit_instance_denoise(points, classes, instance_ids, complete_report, inventory, *, cluster_ids=None):
    """Return ``(report, keep, removed)`` with masks aligned to source points.

    Only class-3 points owned by an existing completed instance can be removed.
    A design-unit count disagreement, missing parsed design, or an ambiguous
    cylinder fit makes this a no-op.  The returned report remains useful for UI
    review even when no candidate is applied.
    """
    points = np.asarray(points, dtype=float)
    classes = np.asarray(classes)
    instance_ids = np.asarray(instance_ids)
    if points.ndim != 2 or points.shape[1] != 3 or classes.shape != (len(points),) or instance_ids.shape != (len(points),):
        raise ValueError("points, classes and instance_ids must be source-aligned")
    if cluster_ids is not None and np.shape(cluster_ids) != (len(points),):
        raise ValueError('cluster_ids must be source-aligned')
    protected_clusters = [row['id'] for row in (complete_report or {}).get('clusters', [])
                          if row.get('locked') and row.get('category') == 'curved-exterior']
    protected = np.isin(cluster_ids, protected_clusters) if cluster_ids is not None else np.zeros(len(points), bool)
    keep = (classes == 3).astype(np.uint8)
    removed = np.zeros(len(points), dtype=np.uint8)
    observed_ids = sorted(int(value) for value in np.unique(instance_ids[(instance_ids > 0) & (classes == 3)]))
    instance_meta = _report_instances(complete_report)
    inventory = inventory if isinstance(inventory, dict) else {}
    units = [row for row in inventory.get("units", []) if isinstance(row, dict)]
    by_id = {str(row.get("designUnitId")): row for row in units if row.get("designUnitId") is not None}
    bars = [row for row in inventory.get("bars", []) if isinstance(row, dict)]
    unparsed = max(sum(not row.get("unitIds") for row in bars), int(inventory.get("coverage", {}).get("unresolvedBars", 0)))
    expected = len(units)
    templates = build_unit_shape_templates(inventory)

    rows: list[dict[str, Any]] = []
    count_ok = bool(units) and len(observed_ids) == expected
    associations = [str(instance_meta.get(i, {}).get("designUnitId")) for i in observed_ids]
    association_ok = len(set(associations)) == expected and set(associations) == set(by_id)
    for instance_id in observed_ids:
        owned = (instance_ids == instance_id) & (classes == 3)
        before = int(np.count_nonzero(owned))
        meta = instance_meta.get(instance_id, {})
        unit = by_id.get(str(meta.get("designUnitId")))
        template = templates.get(str(meta.get('designUnitId')))
        curved = bool(template and template['hasCurves'])
        diameter = _json_number(unit.get("diameterM")) if unit else None
        expected_length = _json_number(unit.get("lengthM")) if unit else None
        row: dict[str, Any] = {"id": instance_id, "pointsBefore": before, "pointsAfter": before,
                               "removedPointCount": 0, "status": "retained", "reason": None,
                               "diameterM": diameter, "expectedLengthM": expected_length,
                               "fitRmseM": None, "centerlineM": None, "radiusM": None,
                               "fitStatus": "not_fitted", "fittedLengthM": None,
                               "observedLengthM": None, "lengthSource": "design-prior",
                               "poseSource": "scan-only", "lengthPlacement": "observed-arc-midpoint",
                               "shapeKind": "straight", "expectedShapeLengthM": expected_length,
                               "fittedStraightLengthM": None, "protectedBendPointCount": 0}
        if not count_ok:
            row["reason"] = "matching_unit_count_mismatch"
        elif not association_ok:
            row["reason"] = "design_unit_assignment_mismatch"
        elif unparsed:
            row["reason"] = "unparsed_design_present"
        elif unit is None or diameter is None or diameter <= 0 or expected_length is None or expected_length <= 0:
            row["reason"] = "missing_matched_dimension"
        elif _bent_or_hook(instance_id, complete_report, unit) and not curved:
            row["reason"] = "bent_or_hook_protected"
        elif before < _MIN_POINTS or np.count_nonzero(owned & ~protected) < _MIN_POINTS:
            row["reason"] = "insufficient_axis_evidence"
        else:
            local_points = points[owned]
            local_protected = protected[owned]
            # Hooks are a separate shape component, not samples of the main axis.
            axis_points = local_points[~local_protected] if np.count_nonzero(~local_protected) >= _MIN_POINTS else local_points
            axis, reason = fit_independent_axis(axis_points, diameter / 2.)
            if axis is None:
                row["reason"] = reason
            else:
                observed_length = float(axis["observedRange"][1] - axis["observedRange"][0])
                centerline = _axis_centerline(axis, expected_length)
                shape_residual, curve_region = None, local_protected.copy()
                row['fittedStraightLengthM'] = float(np.linalg.norm(np.diff(centerline, axis=0), axis=1).sum())
                if curved:
                    shape, shape_residual, curve_region = fit_bent_shape(local_points, centerline, template, diameter / 2., local_protected)
                    row.update(shape)
                    centerline = shape['centerlineM']
                fitted_length = float(np.linalg.norm(np.diff(centerline, axis=0), axis=1).sum())
                row.update(fitStatus="fitted", fitRmseM=float(axis["fitRmseM"]),
                           centerlineM=centerline, radiusM=float(axis["radius"]),
                           fittedLengthM=fitted_length, observedLengthM=observed_length)
                # An overlong scan remains a valid fitted model. Keep the
                # dimensional discrepancy visible without treating its ends
                # as noise just to make the point cloud match the design.
                if observed_length > expected_length * 1.25 + max(.01, diameter):
                    row["reason"] = "observed_span_exceeds_dimension"
                else:
                    residual = np.full(before, np.inf)
                    valid = np.isfinite(local_points).all(axis=1)
                    # Straight denoising stays tied to the supported infinite
                    # scan axis. Finite design ends must not become clipping
                    # planes when the hook anchors the model axially.
                    residual[valid & ~local_protected] = axis['residual']
                    if shape_residual is not None:
                        residual[valid] = np.minimum(residual[valid], shape_residual)
                    tolerance = max(.0008, diameter * .20, axis["fitRmseM"] * 3.)
                    reject = residual > tolerance
                    reject[curve_region] = False
                    row['protectedBendPointCount'] = int(curve_region.sum())
                    # Never call a fit useful unless it has strong support.
                    if int(np.count_nonzero(~reject)) < max(_MIN_POINTS, int(before * .55)):
                        row["reason"] = "inadequate_cylinder_support"
                    else:
                        row.update(status="candidate", reason="radial_outlier", _reject=reject)
        rows.append(row)

    candidates = [row for row in rows if row["status"] == "candidate"]
    # Count/design failures are global: do not turn a partial input into a
    # deceptively clean result.  Fit failures are local and retain only their
    # own owner; a short/occluded bar must not veto a well-supported neighbour.
    apply = count_ok and association_ok and not unparsed and bool(candidates)
    if apply:
        for row in candidates:
            owned_indices = np.flatnonzero((instance_ids == row["id"]) & (classes == 3))
            rejected = row.pop("_reject")
            dropped = owned_indices[rejected]
            keep[dropped] = 0
            removed[dropped] = 1
            row["pointsAfter"] -= int(len(dropped))
            row["removedPointCount"] = int(len(dropped))
            row["status"] = "applied"
    else:
        for row in rows:
            row.pop("_reject", None)
            if row["status"] == "candidate":
                row["status"] = "retained"
                row["reason"] = "global_validation_not_applied"
    reason = "applied" if apply else ("matching_unit_count_mismatch" if not count_ok else
                                       "design_unit_assignment_mismatch" if not association_ok else
                                       "unparsed_design_present" if unparsed else "inadequate_instance_evidence")
    report = _empty_report(len(points), reason, rows, expected=expected, observed=len(observed_ids), unparsed=unparsed)
    after_ids = sorted(int(value) for value in np.unique(instance_ids[(instance_ids > 0) & (keep == 1)]))
    report["pointsBefore"] = int(np.count_nonzero(classes == 3))
    report["pointsAfter"] = int(np.count_nonzero(keep))
    report["physicalBarCount"] = int(inventory.get("coverage", {}).get("physicalBarCount", len(bars)))
    report["removedPointCount"] = int(np.count_nonzero(removed))
    fitted = [row for row in rows if row["fitStatus"] == "fitted"]
    all_fitted = bool(rows) and len(fitted) == len(rows)
    lengths_match = all_fitted and all(abs(row['fittedStraightLengthM'] - row['expectedLengthM']) <= 1e-8
                                     and abs(row['fittedLengthM'] - row['expectedShapeLengthM']) <= 1e-8 for row in fitted)
    report["validation"].update(status="applied" if apply else "not_applied",
                                 candidateInstanceCount=len(candidates),
                                 appliedInstanceCount=len(candidates) if apply else 0,
                                 actualMatchingUnitCount=expected,
                                 actualMatchingUnitCountAfter=expected,
                                 observedInstanceIds=observed_ids,
                                 observedInstanceCountAfter=len(after_ids),
                                 instanceIdsPreserved=observed_ids == after_ids,
                                 countMatchesDesign=count_ok,
                                 designAssignmentsUnique=association_ok,
                                 fittedInstanceCount=len(fitted),
                                 unfittedInstanceIds=[row["id"] for row in rows if row["fitStatus"] != "fitted"],
                                 allInstancesFitted=all_fitted,
                                 fittedCountMatchesDesign=count_ok and association_ok and all_fitted,
                                 cylinderLengthsMatchDesign=lengths_match,
                                 curvedInstanceCount=sum(row['shapeKind'] == 'straight-with-bends' for row in fitted),
                                 scanSupportedCurvedInstanceCount=sum(row.get('bendFitStatus') == 'scan-supported' for row in fitted),
                                 limitedEvidenceCurvedInstanceIds=[row['id'] for row in fitted if row.get('bendFitStatus') == 'limited-evidence'],
                                 protectedBendPointCount=sum(row['protectedBendPointCount'] for row in rows),
                                 lockedBendPointsPreserved=not bool(np.any(removed[protected])),
                                 retainedInstanceCount=len(rows) - len(candidates),
                                 pendingPointCount=int(np.count_nonzero((classes == 3) & (instance_ids == 0))))
    return report, keep, removed
