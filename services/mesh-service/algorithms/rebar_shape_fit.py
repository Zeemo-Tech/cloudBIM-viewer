"""Attach dimension-only bends to a scan-fitted straight run.

Only intrinsic template coordinates enter this module. Endpoint translation,
end reversal and roll are estimated from owned scan points, never BIM pose.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares


def polyline_distance(points, centers):
    points, centers = np.asarray(points, float), np.asarray(centers, float)
    distance2 = np.full(len(points), np.inf)
    for a, b in zip(centers[:-1], centers[1:]):
        direction = b - a
        square = float(direction @ direction)
        if square <= 1e-18:
            continue
        delta = points - a
        along = np.clip(delta @ direction / square, 0., 1.)
        distance2 = np.minimum(distance2, np.sum((delta - along[:, None] * direction)**2, axis=1))
    return np.sqrt(distance2)


def fit_bent_shape(points, main_centerline, template, radius, protected=None):
    points = np.asarray(points, float)
    main = np.asarray(main_centerline, float)
    finite = np.isfinite(points).all(axis=1)
    protected = np.zeros(len(points), bool) if protected is None else np.asarray(protected, bool)
    tangent = main[-1] - main[0]; tangent /= np.linalg.norm(tangent)
    helper = np.eye(3)[np.argmin(np.abs(tangent))]
    u = np.cross(tangent, helper); u /= np.linalg.norm(u)
    v = np.cross(tangent, u)
    frame = np.column_stack((tangent, u, v))
    tails = [np.asarray(template[key], float).reshape(-1, 3) for key in ('startTailM', 'endTailM')]
    length = template['straightLengthM']
    extra = max(template['shapeLengthM'] - length, 0.)
    along = (points - main[0]) @ tangent
    endpoint_window = max(extra + 4 * radius, .02)
    near_ends = (along < endpoint_window) | (along > length - endpoint_window)
    # Isolate the bend evidence instead of letting thousands of straight points
    # swamp a short hook. Locked clusters come from step 06 source identities.
    off_axis = polyline_distance(points, main) > radius * 1.5
    evidence = finite & (protected | (near_ends & off_axis))
    evidence_ids = np.flatnonzero(evidence)
    if len(evidence_ids) < 24:
        evidence_ids = np.flatnonzero(finite & near_ends)
    sample_ids = evidence_ids[np.linspace(0, len(evidence_ids)-1, min(700, len(evidence_ids)), dtype=int)] if len(evidence_ids) else evidence_ids
    sample = points[sample_ids]

    def model(parameters, reverse):
        shift = frame @ parameters[:3]
        straight = main[::-1] if reverse else main
        roll = parameters[3]
        pieces = []
        for index, tail in enumerate(tails):
            if len(tail) < 2:
                pieces.append(np.empty((0, 3)))
                continue
            endpoint = straight[0] if index == 0 else straight[-1]
            local_t = straight[1] - straight[0] if index == 0 else straight[-1] - straight[-2]
            local_t /= np.linalg.norm(local_t)
            local_u = u - np.dot(u, local_t) * local_t; local_u /= np.linalg.norm(local_u)
            local_v = np.cross(local_t, local_u)
            rolled_u = np.cos(roll)*local_u + np.sin(roll)*local_v
            rolled_v = -np.sin(roll)*local_u + np.cos(roll)*local_v
            local = tail - np.array([0. if index == 0 else length, 0., 0.])
            pieces.append(endpoint + local @ np.column_stack((local_t, rolled_u, rolled_v)).T)
        combined = np.vstack((pieces[0][:-1], straight, pieces[1][1:]))
        return combined + shift, straight + shift, [part + shift for part in pieces]

    max_shift = max(.15, length * .1)
    bounds = ([-max_shift, -2*radius, -2*radius, -4*np.pi], [max_shift, 2*radius, 2*radius, 4*np.pi])
    candidates = []
    for reverse in (False, True):
        for angle in np.linspace(-np.pi, np.pi, 8, endpoint=False):
            def objective(parameters):
                centers, _, _ = model(parameters, reverse)
                surface = polyline_distance(sample, centers) - radius
                # Axis fit fixes transverse position well. These weak constraints
                # stabilize sparse/one-sided end data without any BIM coordinates.
                return np.r_[surface, parameters[1:3] * np.sqrt(max(1, len(sample))) * .35,
                             parameters[0] * .005]
            initial = np.array([0., 0., 0., angle])
            if len(sample) >= 24:
                result = least_squares(objective, initial, bounds=bounds, loss='soft_l1',
                                       f_scale=max(.0005, radius*.2), max_nfev=45)
                parameters, score = result.x, result.cost
            else:
                parameters, score = initial, np.inf
            candidates.append((score, parameters, reverse))
    _, parameters, reverse = min(candidates, key=lambda candidate: candidate[0])
    centers, straight, fitted_tails = model(parameters, reverse)
    residual = np.abs(polyline_distance(points[finite], centers) - radius)
    bend_errors = np.abs(polyline_distance(points[evidence & off_axis], centers) - radius)
    support_tolerance = max(.001, radius * .5)
    bend_support = float(np.mean(bend_errors <= support_tolerance)) if len(bend_errors) else 0.
    # A known curved domain is never subjected to a straight-only noise gate.
    # Keep uncertain end evidence visible for review, including incomplete scans.
    straight_along = (points - straight[0]) @ ((straight[-1]-straight[0]) / np.linalg.norm(straight[-1]-straight[0]))
    curve_region = protected.copy()
    for index, tail in enumerate(tails):
        if len(tail) >= 2:
            reach = max(np.ptp(tail[:, 0]), 2 * radius) + 3 * radius
            curve_region |= straight_along < reach if index == 0 else straight_along > length - reach
    curve_region &= finite
    return {
        'centerlineM': centers.tolist(), 'straightCenterlineM': straight.tolist(),
        'bendCenterlinesM': [part.tolist() for part in fitted_tails if len(part) >= 2],
        'shapeKind': 'straight-with-bends', 'expectedShapeLengthM': float(template['shapeLengthM']),
        'fittedStraightLengthM': float(np.linalg.norm(np.diff(straight, axis=0), axis=1).sum()),
        'bendFitStatus': 'scan-supported' if len(bend_errors) >= 24 and bend_support >= .55 else 'limited-evidence',
        'bendFitRmseM': float(np.sqrt(np.mean(bend_errors**2))) if len(bend_errors) else None,
        'bendSupportFraction': bend_support, 'bendEvidencePointCount': int(len(bend_errors)),
        'protectedBendPointCount': int(curve_region.sum()), 'lengthPlacement': 'scan-end-shape-fit',
        'shapeSource': 'design-local-polyline', 'poseSource': 'scan-only',
    }, residual, curve_region
