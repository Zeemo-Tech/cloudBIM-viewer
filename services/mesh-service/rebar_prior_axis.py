"""Joint known-radius tube fits for the Scan-vs-BIM workbench.

The independent entry point uses only the design radius and a scan-derived
frame. The older design-frame entry point remains for historical experiments.
All owned points constrain one smooth axis; displayed sections are evaluations,
never separate circle fits. Inferred axis locations are not surface samples.
"""
import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import least_squares

METHOD = "design-prior-continuous-axis-v1"
INDEPENDENT_METHOD = "scan-cluster-independent-axis-v1"


def fit_prior_axis(points, start, tangent, length, radius, *, independent=False, initial_centerline=None):
    if radius is None or not np.isfinite(radius) or radius <= 0:
        return None, "missing-design-radius"
    helper = np.eye(3)[np.argmin(np.abs(tangent))]
    u = np.cross(tangent, helper); u /= np.linalg.norm(u)
    v = np.cross(tangent, u)
    delta = np.asarray(points) - start
    along = delta @ tangent
    xy = np.column_stack((delta @ u, delta @ v))
    margin = max(2 * radius, .05 * length)
    eligible = np.isfinite(delta).all(axis=1)
    if not independent:
        eligible &= ((along >= -margin) & (along <= length + margin)
                     & (np.linalg.norm(xy, axis=1) < .2))
    along, xy = along[eligible], xy[eligible]
    if len(xy) < 32 or np.ptp(along) < .25 * length:
        return None, "insufficient-axis-evidence"
    s = along / length
    # Balance axial density without throwing away input points. A dense patch
    # must not outweigh the rest of the already classified instance.
    bins = np.clip((s * 32).astype(int), 0, 31)
    counts = np.bincount(bins, minlength=32)
    weights = np.sqrt(len(s) / (np.count_nonzero(counts) * counts[bins]))
    normalized = xy / radius
    scale = max(.00015 / radius, .06)

    def solve(basis, target, weight, seed, penalty):
        n = basis.shape[1]
        def residual(flat):
            c = flat.reshape(n, 2)
            radial = basis @ c - target
            return np.r_[(np.linalg.norm(radial, axis=1) - 1) * weight,
                         (penalty @ c).ravel()]
        def jacobian(flat):
            radial = basis @ flat.reshape(n, 2) - target
            radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
            data = (basis[:, :, None] * radial[:, None, :] * weight[:, None, None]).reshape(-1, n * 2)
            return np.vstack((data, np.kron(penalty, np.eye(2))))
        return least_squares(residual, seed.ravel(), jac=jacobian, loss="soft_l1",
                             f_scale=scale, max_nfev=100, ftol=1e-8, xtol=1e-8, gtol=1e-8)

    # A few whole-instance starts avoid the mirror solution on a visible arc.
    # Only this initialization is sampled; the final solve uses every eligible point.
    ids = np.linspace(0, len(s) - 1, min(len(s), 2048), dtype=int)
    linear = np.column_stack((np.ones(len(s)), s - .5))
    seed, *_ = np.linalg.lstsq(linear[ids], normalized[ids], rcond=None)
    candidates = []
    for angle in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        initial = seed.copy(); initial[0] += [np.cos(angle), np.sin(angle)]
        candidates.append(solve(linear[ids], normalized[ids], weights[ids], initial, np.empty((0, 2))))
    best = min(candidates, key=lambda result: result.cost)
    # Six cubic B-spline coefficients describe smooth bow and tilt over the full
    # design length. No per-window gates or gap filling are involved.
    knots = np.r_[np.zeros(4), 1/3, 2/3, np.ones(4)]
    spline = BSpline(knots, np.eye(6), 3, extrapolate=True)
    basis = spline(s)
    seed, *_ = np.linalg.lstsq(basis, linear @ best.x.reshape(2, 2), rcond=None)
    smooth = spline(np.linspace(0, 1, 24), nu=2) * np.sqrt(len(s) / 24) * .003
    fitted = solve(basis, normalized, weights, seed, smooth)
    if initial_centerline is not None:
        observed = np.asarray(initial_centerline, dtype=float)
        if observed.ndim != 2 or observed.shape[1] != 3 or len(observed) < 4 or not np.isfinite(observed).all():
            raise ValueError("initial_centerline must contain at least four finite 3D centers")
        offset = observed-start
        station_basis = spline((offset@tangent)/length)
        target = np.column_stack((offset@u, offset@v))/radius
        observed_seed, *_ = np.linalg.lstsq(station_basis, target, rcond=None)
        observed_fit = solve(basis, normalized, weights, observed_seed, smooth)
        if observed_fit.success and (not fitted.success or observed_fit.cost < fitted.cost):
            fitted = observed_fit
    coeff = fitted.x.reshape(6, 2) * radius
    residual = np.abs(np.linalg.norm(xy - basis @ coeff, axis=1) - radius)
    inliers = residual <= max(.0005, .35 * radius)
    if not fitted.success or inliers.mean() < .55:
        return None, "inconsistent-design-radius"
    radial = xy[inliers] - (basis @ coeff)[inliers]
    radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
    # A near-single generator cannot determine a transverse centre, even with
    # a known diameter. Reject the unit honestly rather than inventing an axis.
    if np.linalg.eigvalsh(radial.T @ radial / len(radial))[0] < .005:
        return None, "ambiguous-axis"
    if not independent and np.max(np.linalg.norm(spline(np.linspace(0, 1, 64)) @ coeff, axis=1)) > .2:
        return None, "center-too-far"
    rmse = float(np.sqrt(np.mean(residual[inliers] ** 2)))
    return {"spline": spline, "coeff": coeff, "u": u, "v": v,
            "start": start, "tangent": tangent, "length": length, "radius": radius,
            "along": along, "xy": xy, "residual": residual, "inliers": inliers,
            "fitRmseM": rmse, "pointCount": len(xy)}, "prior-axis-supported"


def fit_independent_axis(points, radius):
    """Fit a classified cluster without a design position, direction or length.

    The diameter is the only design input. The robust scan frame is merely a
    numerical parameterization; neither its position nor its slope is penalized.
    """
    points = np.asarray(points, dtype=float)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) < 32:
        return None, "insufficient-axis-evidence"
    center = np.median(points, axis=0)
    # Sparse remote contamination must not set the frame or the observed extent.
    distance = np.linalg.norm(points - center, axis=1)
    core = points[distance <= np.quantile(distance, .95)]
    _, singular, vectors = np.linalg.svd(core - core.mean(axis=0), full_matrices=False)
    if singular[0] < 3 * singular[1]:
        return None, "ambiguous-axis"
    tangent = vectors[0]
    if tangent[np.argmax(np.abs(tangent))] < 0:
        tangent = -tangent
    along = (points - center) @ tangent
    low, high = np.quantile(along, [.005, .995])
    length = float(high - low)
    if length < max(.005, 4 * (radius or .004)):
        return None, "insufficient-axis-evidence"
    axis, reason = fit_prior_axis(points, center + low * tangent, tangent, length, radius, independent=True)
    if axis is not None:
        # Extent comes from radial inliers, never the expected design length.
        low, high = np.quantile(axis['along'][axis['inliers']], [.005, .995])
        axis['observedRange'] = (float(low), float(high))
        axis['method'] = INDEPENDENT_METHOD
    return axis, reason


def axis_section(axis, station, window):
    offset = axis["spline"](station / axis["length"]) @ axis["coeff"]
    center = (axis["start"] + station * axis["tangent"]
              + offset[0] * axis["u"] + offset[1] * axis["v"])
    local = np.abs(axis["along"] - station) <= window
    inliers = local & axis["inliers"]
    angles = np.sort(np.mod(np.arctan2((axis["xy"] - offset)[inliers, 1],
                                      (axis["xy"] - offset)[inliers, 0]), 2 * np.pi))
    arc = float(np.rad2deg(2 * np.pi - np.diff(np.r_[angles, angles[0] + 2*np.pi]).max())) if len(angles) > 1 else 0.
    reason = "prior-axis-supported" if inliers.sum() >= 12 else "prior-axis-inferred"
    return {"center": center, "radiusM": axis["radius"], "fitRmseM": axis["fitRmseM"],
            "arcCoverageDeg": arc, "inlierCount": int(inliers.sum()), "centerUncertaintyM": None}, {
                "reason": reason, "pointCount": int(local.sum()), "axisPointCount": axis["pointCount"],
                "fitRmseM": axis["fitRmseM"], "arcCoverageDeg": arc,
                "radiusSource": "design-prior", "axisMethod": METHOD}
