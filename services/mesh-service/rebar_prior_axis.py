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


class _SupportedSpline:
    """Continue endpoint tangents outside evidence, never cubic curvature."""
    def __init__(self, spline, low, high):
        self.spline, self.low, self.high = spline, low, high

    def __call__(self, station, nu=0):
        station = np.asarray(station)
        bounded = np.clip(station, self.low, self.high)
        result = self.spline(bounded, nu=nu)
        if nu == 0:
            result = result + (station-bounded)[..., None]*self.spline(bounded, nu=1)
        elif nu > 1:
            result = np.where((station == bounded)[..., None], result, 0.)
        return result


def fit_prior_axis(points, start, tangent, length, radius, *, independent=False, initial_centerline=None, straight=False, guard_bending=False):
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
    fit_mask = np.ones(len(s), dtype=bool)
    support_low, support_high = 0., 1.
    if guard_bending and not straight:
        # Equal station weighting must not promote a handful of terminal noise
        # points to the same authority as hundreds of body surface samples.
        minimum = max(24, .20*np.median(counts[counts > 0]))
        reliable = counts >= minimum
        runs = np.flatnonzero(np.convolve(reliable.astype(int), np.ones(3, int), mode='valid') == 3)
        if not len(runs):
            return None, 'insufficient-continuous-axis-evidence'
        first, last = int(runs[0]), int(runs[-1]+2)
        fit_mask = (bins >= first) & (bins <= last)
        support_low, support_high = np.quantile(s[fit_mask], [.01, .99])
        if support_high-support_low < .25:
            return None, 'insufficient-continuous-axis-evidence'
        floor = max(minimum, 1.)
    else:
        floor = 1.
    weights = np.sqrt(len(s) / (np.count_nonzero(counts) * np.maximum(counts[bins], floor)))
    weights *= fit_mask
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
    eligible_ids = np.flatnonzero(fit_mask)
    ids = eligible_ids[np.linspace(0, len(eligible_ids) - 1, min(len(eligible_ids), 2048), dtype=int)]
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
    spline = (BSpline([0., 0., 1., 1.], np.eye(2), 1, extrapolate=True)
              if straight else BSpline(knots, np.eye(6), 3, extrapolate=True))
    if guard_bending and not straight:
        spline = _SupportedSpline(spline, support_low, support_high)
    basis = spline(s)
    seed, *_ = np.linalg.lstsq(basis, linear @ best.x.reshape(2, 2), rcond=None)
    smooth = (np.empty((0, 2)) if straight else
              spline(np.linspace(0, 1, 24), nu=2) * np.sqrt(len(s) / 24) * .003)
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
    shape_model = 'straight' if straight else 'smooth-spline'
    if guard_bending and not straight:
        linear_spline = BSpline([0., 0., 1., 1.], np.eye(2), 1, extrapolate=True)
        linear_basis = linear_spline(s)
        linear_seed, *_ = np.linalg.lstsq(linear_basis[ids], linear[ids]@best.x.reshape(2, 2), rcond=None)
        linear_fit = solve(linear_basis, normalized, weights, linear_seed, np.empty((0, 2)))
        curved_error = np.abs(np.linalg.norm(normalized-basis@fitted.x.reshape(-1, 2), axis=1)-1)*radius
        linear_error = np.abs(np.linalg.norm(normalized-linear_basis@linear_fit.x.reshape(2, 2), axis=1)-1)*radius
        def band_error(errors):
            return np.median([np.median(errors[(bins == b) & fit_mask])
                              for b in np.unique(bins[fit_mask])])
        def band_support(errors):
            return np.median([np.mean(errors[(bins == b) & fit_mask] <= max(.0005, .35*radius))
                              for b in np.unique(bins[fit_mask])])
        straight_score, curved_score = band_error(linear_error), band_error(curved_error)
        straight_support = band_support(linear_error)
        if linear_fit.success and straight_support >= .80 and (
                straight_score-curved_score < max(.00012, .04*radius)
                or curved_score >= .8*straight_score):
            spline, basis, fitted = linear_spline, linear_basis, linear_fit
            shape_model = 'evidence-selected-straight'
        else:
            bow_spline = _SupportedSpline(BSpline([0., 0., 0., 1., 1., 1.], np.eye(3), 2),
                                           support_low, support_high)
            bow_basis = bow_spline(s)
            bow_seed, *_ = np.linalg.lstsq(bow_basis[ids], (basis@fitted.x.reshape(-1, 2))[ids], rcond=None)
            bow_penalty = bow_spline(np.linspace(0, 1, 24), nu=2)*np.sqrt(len(s)/24)*.003
            bow_fit = solve(bow_basis, normalized, weights, bow_seed, bow_penalty)
            bow_error = np.abs(np.linalg.norm(normalized-bow_basis@bow_fit.x.reshape(3, 2), axis=1)-1)*radius
            bow_score = band_error(bow_error)
            if bow_fit.success and band_support(bow_error) >= .80 and (
                    bow_score-curved_score < max(.00012, .04*radius) or curved_score >= .8*bow_score):
                spline, basis, fitted = bow_spline, bow_basis, bow_fit
                shape_model = 'evidence-selected-bow'
            else:
                shape_model = 'supported-spline'
    coeff = fitted.x.reshape(basis.shape[1], 2) * radius
    residual = np.abs(np.linalg.norm(xy - basis @ coeff, axis=1) - radius)
    inliers = (residual <= max(.0005, .35 * radius)) & fit_mask
    fit_support = (band_support(residual) if guard_bending and not straight else inliers.mean())
    if not fitted.success or fit_support < .55:
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
            "fitRmseM": rmse, "pointCount": len(xy), 'shapeModel': shape_model,
            'supportedRange': [float(support_low*length), float(support_high*length)]}, "prior-axis-supported"


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
