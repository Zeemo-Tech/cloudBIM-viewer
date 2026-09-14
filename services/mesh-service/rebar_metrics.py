"""Bounded cross-section circle fits; surface samples are never centreline data."""
from __future__ import annotations
import numpy as np


def fit_section(points, origin, tangent, radius=None, *, details=False, min_arc_coverage_deg=160.0, diagnostics=None):
    def reject(reason):
        if diagnostics is not None: diagnostics["reason"] = reason
        return None
    if diagnostics is not None: diagnostics["pointCount"] = len(points)
    if len(points) < 12:
        return reject("too-few-points")
    helper = np.eye(3)[np.argmin(np.abs(tangent))]
    u = np.cross(tangent, helper); u /= np.linalg.norm(u)
    v = np.cross(tangent, u)
    delta = points - origin
    xy = np.column_stack((delta @ u, delta @ v))
    # Rebase for numerical stability and normalize before solving a circle.
    base = np.median(xy, axis=0)
    scale = max(float(np.std(xy)), 1e-8)
    p = (xy - base) / scale
    keep = np.ones(len(p), bool)
    for _ in range(3):
        a = np.column_stack((2 * p[keep], np.ones(keep.sum())))
        if len(a) < 12 or np.linalg.cond(a) > 100:
            return reject("ill-conditioned-circle")
        coef, _, _, _ = np.linalg.lstsq(a, np.sum(p[keep] ** 2, axis=1), rcond=None)
        center = coef[:2] * scale + base
        r2 = coef[2] + np.dot(coef[:2], coef[:2])
        if r2 <= 0:
            return reject("non-positive-radius")
        fitted_radius = np.sqrt(r2) * scale
        residual = np.abs(np.linalg.norm(xy - center, axis=1) - fitted_radius)
        median = np.median(residual[keep]); mad = np.median(np.abs(residual[keep] - median))
        keep = residual <= max(0.0001, median + 3 * 1.4826 * mad)
    if diagnostics is not None:
        diagnostics.update(candidateCenterM=(origin + center[0] * u + center[1] * v).tolist(),
                           candidateRadiusM=float(fitted_radius), inlierCount=int(keep.sum()),
                           fitRmseM=float(np.sqrt(np.mean(residual[keep] ** 2))) if keep.any() else None)
    if keep.sum() < 12 or keep.mean() < .65:
        return reject("insufficient-inliers")
    if radius is not None and not .5 * radius <= fitted_radius <= 1.5 * radius:
        return reject("radius-out-of-range")
    if np.sqrt(np.mean(residual[keep] ** 2)) > max(.0005, .2 * fitted_radius):
        return reject("excessive-fit-residual")
    angles = np.sort(np.mod(np.arctan2((xy - center)[keep, 1], (xy - center)[keep, 0]), 2 * np.pi))
    arc = 2 * np.pi - np.diff(np.r_[angles, angles[0] + 2 * np.pi]).max()
    if diagnostics is not None: diagnostics["arcCoverageDeg"] = float(np.rad2deg(arc))
    if arc < np.deg2rad(min_arc_coverage_deg):
        return reject("insufficient-angular-coverage")
    observed = origin + center[0] * u + center[1] * v
    if np.linalg.norm(observed - origin) > .2:
        return reject("center-too-far")
    # Circle-centre uncertainty grows rapidly on a short/noisy visible arc.
    radial = xy[keep] - center
    jacobian = np.column_stack((-radial / np.linalg.norm(radial, axis=1)[:, None], -np.ones(keep.sum())))
    rmse = float(np.sqrt(np.mean(residual[keep] ** 2)))
    covariance = np.linalg.pinv(jacobian.T @ jacobian) * max(rmse, .00005) ** 2
    uncertainty = float(np.sqrt(np.linalg.eigvalsh(covariance[:2, :2]).max()))
    if diagnostics is not None: diagnostics["centerUncertaintyM"] = uncertainty
    if uncertainty > max(.0005, .15 * fitted_radius):
        return reject("uncertain-center")
    if diagnostics is not None: diagnostics["reason"] = "supported"
    if details:
        return {"center": observed, "radiusM": float(fitted_radius), "fitRmseM": rmse,
                "arcCoverageDeg": float(np.rad2deg(arc)), "inlierCount": int(keep.sum()),
                "centerUncertaintyM": uncertainty}
    return observed


def surface_summary(values, vertices):
    known = np.flatnonzero(np.isfinite(values))
    surface = {"maxAbs": None, "maxLocationM": None}
    if len(known):
        pick = known[np.argmax(np.abs(values[known]))]
        surface = {"maxAbs": float(abs(values[pick])), "maxLocationM": vertices[pick].tolist()}
    return surface


def measure_bar(values, vertices, segments, scan_points, *, unit_points=None, radii=None, max_samples=64, window_scale=1.0, min_arc_coverage_deg=160.0, trace=False, axis_method="sections"):
    if axis_method not in {"sections", "design-prior"}:
        raise ValueError("unknown axis method")
    from rebar_prior_axis import fit_prior_axis, axis_section, METHOD
    surface = surface_summary(values, vertices)
    profile, bows = [], []
    # At least 3 samples per represented unit; keep every unit when possible.
    remaining = max_samples
    for index, (unit_id, start, end) in enumerate(segments):
        vector = end - start; length = float(np.linalg.norm(vector))
        if length <= 1e-9 or (remaining <= 0 and axis_method == "sections"):
            continue
        budget = (min(64, max(3, remaining // (len(segments) - index))) if axis_method == "design-prior"
                  else min(16, max(1, remaining // (len(segments) - index))))
        remaining -= budget
        tangent = vector / length
        points = unit_points.get(unit_id, np.empty((0, 3))) if unit_points is not None else scan_points
        radius = (radii or {}).get(unit_id)
        window = min(.015, max(.001, length / (min(budget, 16) * 8))) * window_scale
        along = (points - start) @ tangent
        transverse = (points - start) - along[:, None] * tangent
        radial = np.linalg.norm(transverse, axis=1)
        eligible = radial < .2
        axis, axis_reason = fit_prior_axis(points, start, tangent, length, radius) if axis_method == "design-prior" else (None, None)
        rows = []
        for station in np.linspace(0, length, budget):
            design = start + station * tangent
            mask = eligible & (np.abs(along - station) <= window)
            evidence = {} if trace else None
            unstable = False
            if axis_method == "design-prior":
                fit, evidence = axis_section(axis, station, window) if axis is not None else (None, {"reason": axis_reason, "pointCount": int(mask.sum())})
            else:
                fit = fit_section(points[mask], design, tangent, radius, details=True,
                                  min_arc_coverage_deg=min_arc_coverage_deg, diagnostics=evidence)
                if fit is not None:
                    # A low residual covariance alone assumes one fixed circular
                    # section. Rapid centre motion inside the axial window violates
                    # that model and can produce a very confident, biased centre.
                    inner_mask = eligible & (np.abs(along - station) <= window / 3)
                    if np.count_nonzero(inner_mask) >= 12:
                        inner = fit_section(points[inner_mask], design, tangent, radius, details=True, min_arc_coverage_deg=min_arc_coverage_deg)
                        drift = float(np.linalg.norm(inner['center'] - fit['center'])) if inner is not None else np.inf
                        unstable = inner is None or drift > max(.00025, .1 * fit['radiusM'])
                        if unstable:
                            if evidence is not None: evidence.update(reason="unstable-section", windowCenterDriftM=float(drift))
                            fit = None
                        else:
                            # Include axial-window sensitivity and residual scale;
                            # this is a quality estimate, not a calibrated CI.
                            fit['centerUncertaintyM'] = max(fit['centerUncertaintyM'], inner['centerUncertaintyM'], drift, fit['fitRmseM'])
            observed = fit["center"] if fit is not None else None
            offset = float(np.linalg.norm(observed - design)) if observed is not None else None
            row = {"designUnitId": unit_id, "stationM": float(station), "designCenterM": design.tolist(),
                   "observedCenterM": observed.tolist() if observed is not None else None, "transverseOffsetM": offset,
                   "offsetVectorM": (observed - design).tolist() if observed is not None else None,
                   "radiusM": fit["radiusM"] if fit is not None else None,
                   "radiusDeltaM": fit["radiusM"] - radius if axis_method == "sections" and fit is not None and radius is not None else None,
                   "fitRmseM": fit["fitRmseM"] if fit is not None else None,
                   "arcCoverageDeg": fit["arcCoverageDeg"] if fit is not None else None,
                   "centerUncertaintyM": fit["centerUncertaintyM"] if fit is not None else None,
                   "inlierCount": fit["inlierCount"] if fit is not None else 0,
                   "windowM": window, "quality": "unstable-section" if unstable else "supported" if fit is not None else "insufficient-coverage"}
            if axis_method == "design-prior":
                row.update(axisMethod=METHOD, radiusSource="design-prior", quality=evidence["reason"])
            if trace: row["fitEvidence"] = evidence
            profile.append(row); rows.append(row)
        # Require a continuous covered run spanning at least half this unit.
        # Fit a VECTOR displacement trend, not its magnitude (which folds signs).
        run = []
        for row in rows + [None]:
            if row is not None and row['observedCenterM'] is not None:
                run.append(row); continue
            if len(run) >= 5 and run[-1]['stationM'] - run[0]['stationM'] >= length * .5:
                station = np.asarray([r['stationM'] for r in run])
                offsets = np.asarray([np.subtract(r['observedCenterM'], r['designCenterM']) for r in run])
                a = np.column_stack((station - station.mean(), np.ones(len(station))))
                coef, _, _, _ = np.linalg.lstsq(a, offsets, rcond=None)
                bows.append(float(np.max(np.linalg.norm(offsets - a @ coef, axis=1))))
            run = []
    supported = [row for row in profile if row['observedCenterM'] is not None]
    radii_delta = [abs(r['radiusDeltaM']) for r in supported if r.get('radiusDeltaM') is not None]
    return {"surface": surface, "longitudinalProfile": profile,
            "crossSection": {"maxAbsRadiusDeltaM": max(radii_delta, default=None),
                             "supportedSectionCount": len(supported), "sectionCount": len(profile)},
            "bending": {"maxCentrelineDepartureM": max((r['transverseOffsetM'] for r in supported), default=None),
                        "residualBowM": max(bows) if bows else None, "curvatureMInv": None,
                        "method": METHOD if axis_method == "design-prior" else "cross-section-circle-fit-v1", "quality": "supported" if bows else "insufficient-coverage"}}
