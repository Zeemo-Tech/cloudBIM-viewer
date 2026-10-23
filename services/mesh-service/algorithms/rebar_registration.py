"""Rigid scan registration from measured, instance-owned rebar sections.

Design lines and all observed points initially share the denoise scan frame.
Only observations move; fixed GUID correspondences never jump to a neighbour.
The correction is composed on the LEFT in BIM coordinates. No scale, local bar
deformation, extrapolated axis, or unobserved endpoint enters the solve.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import laspy
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from rebar_bim import _matrix
from rebar_metrics import fit_section

METHOD = "rebar-weighted-rigid-v1"


def _move(points, matrix):
    return np.asarray(points) @ matrix[:3, :3].T + matrix[:3, 3]


def collect_sections(points, owners, instance_map, initial):
    """Measure independent circles on raw owned rows, without a design-axis fit."""
    units = {r['designUnitId']: r for r in instance_map['inventory']['units']}
    # One principal straight segment per physical bar prevents hooks and split
    # inventory entries from multiplying that bar's registration influence.
    principal = {}
    for u in units.values():
        if u.get('kind') != 'straight' or float(u['lengthM']) < .45:
            continue
        old = principal.get(u['designBarId'])
        if old is None or u['lengthM'] > old['lengthM']:
            principal[u['designBarId']] = u
    observed, audit = [], []
    for instance in instance_map['instances']:
        unit = units.get(instance.get('designUnitId'))
        if unit is None or principal.get(unit['designBarId']) is not unit:
            continue
        record = dict(designBarId=unit['designBarId'], designUnitId=unit['designUnitId'],
                      instanceId=instance['id'], diameterM=unit['diameterM'], reason='insufficient-sections')
        audit.append(record)
        if instance.get('reviewStatus') != 'matched':
            record['reason'] = 'unconfirmed-instance'
            continue
        p = points[owners == instance['id']]
        a, b = np.asarray(unit['startM']), np.asarray(unit['endM'])
        length = float(np.linalg.norm(b - a)); tangent = (b - a) / length
        station = (p - a) @ tangent
        sections = []
        # Equal station sampling, never point density weighting. Separate the
        # windows so a dense patch cannot be counted repeatedly.
        grid = np.linspace(.025, length - .025, min(81, int(length / .02)))
        for s in grid:
            q = p[np.abs(station - s) <= .006]
            fit = fit_section(q, a + s * tangent, tangent, unit['diameterM'] / 2,
                              details=True, min_arc_coverage_deg=160)
            if fit is None:
                continue
            inner = fit_section(p[np.abs(station - s) <= .002], a + s * tangent,
                                tangent, unit['diameterM'] / 2, details=True,
                                min_arc_coverage_deg=160)
            if inner is None or np.linalg.norm(inner['center'] - fit['center']) > max(.0003, .1 * fit['radiusM']):
                continue
            sections.append(dict(stationM=float(s), centerScanM=fit['center'].tolist(),
                                 fitRmseM=fit['fitRmseM'], arcCoverageDeg=fit['arcCoverageDeg']))
        record.update(pointCount=len(p), sectionCount=len(sections), sampledSectionCount=len(grid))
        if len(sections) < 8:
            continue
        centers = np.asarray([s['centerScanM'] for s in sections])
        center = centers.mean(axis=0)
        _, _, vh = np.linalg.svd(centers - center, full_matrices=False)
        direction = vh[0]
        departure = centers - center - ((centers - center) @ direction)[:, None] * direction
        straightness = float(np.sqrt(np.mean(np.sum(departure ** 2, axis=1))))
        span = sections[-1]['stationM'] - sections[0]['stationM']
        coverage = len(sections) / len(grid)
        angle = float(np.degrees(np.arccos(np.clip(abs(direction @ tangent), 0, 1))))
        record.update(straightnessRmseM=straightness, observedSpanM=span,
                      sectionCoverage=coverage, directionDifferenceDeg=angle)
        if span < .4 or span / length < .35 or coverage < .25:
            record['reason'] = 'insufficient-span'; continue
        if straightness > .006 or angle > 8:
            record['reason'] = 'curved-or-inconsistent-axis'; continue
        # Diameter is a reliability prior, not a replication count. A cap keeps
        # a few thick bars from eliminating the second direction's evidence.
        diameter_weight = min(4., (unit['diameterM'] / .006) ** 2)
        straight_weight = 1. / (1. + (straightness / .002) ** 2)
        weight = diameter_weight * straight_weight * coverage * min(span / length, 1.)
        record.update(reason='eligible', diameterWeight=diameter_weight,
                      straightnessWeight=straight_weight, baseWeight=weight, sections=sections)
        start, end = _move(np.asarray([a, b]), initial)
        observed.append(dict(id=unit['designBarId'], unitId=unit['designUnitId'],
                             points=_move(centers, initial), start=start,
                             tangent=(end-start)/length, weight=weight, audit=record))
    return observed, audit


def _correction(x, pivot):
    rotation = Rotation.from_rotvec(x[:3]).as_matrix()
    m = np.eye(4); m[:3, :3] = rotation
    m[:3, 3] = pivot + x[3:] - rotation @ pivot
    return m


def _residuals(bar, correction):
    d = _move(bar['points'], correction) - bar['start']
    return d - (d @ bar['tangent'])[:, None] * bar['tangent']


def _stats(bars, correction):
    rms = np.asarray([np.sqrt(np.mean(np.sum(_residuals(b, correction)**2, axis=1))) for b in bars])
    means = [np.mean(np.linalg.norm(_residuals(b, correction), axis=1)) for b in bars]
    weight = np.asarray([b['weight'] for b in bars])
    return dict(barCount=len(bars), sectionCount=sum(len(b['points']) for b in bars),
                weightedRmseM=float(np.sqrt(np.average(rms**2, weights=weight))),
                equalBarRmseM=float(np.sqrt(np.mean(rms**2))),
                meanBarOffsetM=float(np.mean(means)), medianBarRmseM=float(np.median(rms)),
                within10mmBarFraction=float(np.mean(rms <= .01)))


def solve_rigid(bars, pivot=None):
    """IRLS point-to-line, with per-bar normalization and 6 mm Huber groups."""
    if len(bars) < 6:
        raise ValueError('精配准需要至少 6 根有足够截面支持的钢筋')
    if pivot is None:
        pivot = np.mean([np.mean(b['points'], axis=0) for b in bars], axis=0)
    robust = np.ones(len(bars)); x = np.zeros(6)
    for iteration in range(15):
        def residual(v):
            correction = _correction(v, pivot)
            return np.concatenate([(_residuals(b, correction) * np.sqrt(b['weight'] * w / len(b['points']))).ravel()
                                   for b, w in zip(bars, robust)])
        fit = least_squares(residual, x, bounds=(np.r_[[-.06]*3, [-.075]*3], np.r_[[.06]*3, [.075]*3]),
                            xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=100)
        correction = _correction(fit.x, pivot)
        rms = np.asarray([np.sqrt(np.mean(np.sum(_residuals(b, correction)**2, axis=1))) for b in bars])
        new_robust = np.minimum(1., .006 / np.maximum(rms, 1e-12))
        done = np.linalg.norm(fit.x - x) < 1e-9 and np.max(abs(new_robust - robust)) < 1e-5
        x, robust = fit.x, new_robust
        if done: break
    # Scale rotation columns by a one-metre lever arm. Exact rank, not mere low
    # residual, determines whether parallel bars constrain all six freedoms.
    singular = np.linalg.svd(fit.jac, compute_uv=False)
    condition = float(singular[0] / max(singular[-1], 1e-15))
    if singular[-1] < singular[0] * 1e-5:
        raise ValueError('钢筋方向或空间分布不足，无法约束六自由度精配准')
    rotation_deg = float(np.linalg.norm(x[:3]) * 180 / np.pi)
    translation = float(np.linalg.norm(x[3:]))
    if rotation_deg > 3 or translation > .075 or not fit.success:
        raise ValueError('精配准修正超出局部刚性调整范围')
    return correction, dict(pivotBimM=pivot.tolist(), pivotTranslationM=x[3:].tolist(),
                            deltaRotationDeg=rotation_deg, pivotShiftM=translation,
                            conditionNumber=condition, singularValues=singular.tolist(),
                            robustWeights=robust.tolist(), irlsIterations=iteration+1,
                            lastSolveEvaluations=int(fit.nfev))


def register_sections(bars):
    if len(bars) < 10:
        raise ValueError('可靠钢筋不足，无法分开拟合与留出复核')
    # Group by direction without assuming BIM Y-up or an engineering axis.
    families = []
    for b in sorted(bars, key=lambda b: b['id']):
        group = next((g for g in families if abs(g[0]['tangent'] @ b['tangent']) > .9), None)
        if group is None: families.append([b])
        else: group.append(b)
    if len(families) < 2 or sum(len(g) >= 3 for g in families) < 2:
        raise ValueError('精配准需要至少两个有足够支持的不同钢筋方向')
    # Reserve whole bars before fitting, spatially distributed in each family.
    train, held = [], []
    for group in families:
        axis = np.ptp(np.array([b['start'] for b in group]), axis=0).argmax()
        group.sort(key=lambda b: (float(b['start'][axis]), b['id']))
        for i, b in enumerate(group):
            (held if len(group) >= 4 and i % 5 == 2 else train).append(b)
    if len(held) < 2:
        raise ValueError('完整钢筋留出样本不足')
    correction, fit = solve_rigid(train)
    identity = np.eye(4)
    before, after = _stats(train, identity), _stats(train, correction)
    hold_before, hold_after = _stats(held, identity), _stats(held, correction)
    all_before, all_after = _stats(bars, identity), _stats(bars, correction)
    # Whole-bar deletion checks concentrated influence and local installation
    # bias. This is sensitivity on this scan, not independent field accuracy.
    pivot = np.asarray(fit['pivotBimM'])
    sensitivities = []
    for dropped in train:
        candidate, _ = solve_rigid([b for b in train if b is not dropped], pivot)
        displacement = np.linalg.norm(_move(pivot[None], candidate) - _move(pivot[None], correction))
        angle = np.linalg.norm(Rotation.from_matrix(candidate[:3, :3] @ correction[:3, :3].T).as_rotvec())
        sensitivities.append(dict(designBarId=dropped['id'], pivotShiftM=float(displacement), rotationDeg=float(np.degrees(angle))))
    for b, robust in zip(train, fit['robustWeights']):
        b['audit'].update(role='fit', robustWeight=robust)
    for b in held: b['audit'].update(role='held-out')
    for b in bars:
        b['audit'].update(beforeRmseM=_stats([b], identity)['equalBarRmseM'], afterRmseM=_stats([b], correction)['equalBarRmseM'])
    return correction, dict(fit=fit, trainingBefore=before, trainingAfter=after,
                            heldOutBefore=hold_before, heldOutAfter=hold_after,
                            allEligibleBefore=all_before, allEligibleAfter=all_after,
                            sensitivity=sensitivities, fitBarIds=[b['id'] for b in train],
                            heldOutBarIds=[b['id'] for b in held])


def fine_rebar_registration(denoise_dir, init_matrix_16, rmse_regress_ratio=1.05,
                            fitness_regress_ratio=.95, apply_when_regressed=False):
    start_time = time.perf_counter(); directory = Path(denoise_dir)
    initial = _matrix(init_matrix_16)
    if not 0 < rmse_regress_ratio or not 0 < fitness_regress_ratio:
        raise ValueError('invalid regression thresholds')
    control = json.loads((directory / 'control-net.json').read_text())['report']
    original = _matrix(control['modelInfo']['scanToBim'])
    if not np.allclose(initial, original, rtol=0, atol=1e-9):
        raise ValueError('钢筋分割使用的配准矩阵已过期，请先重新分割')
    instance_map = json.loads((directory / 'instance-map.json').read_text())
    if (instance_map['inventory']['units'] != control['inventory']['units'] or
            {b['designBarId'] for b in instance_map['inventory']['bars']} !=
            {b['designBarId'] for b in control['inventory']['bars']}):
        raise ValueError('钢筋拓扑与分割证据不一致')
    las = laspy.read(directory / 'cleaned.las')
    points = np.column_stack((las.x, las.y, las.z)).astype(float)
    owners = np.asarray(las.cloudbim_instance_id)
    bars, audit = collect_sections(points, owners, instance_map, initial)
    correction, diagnostics = register_sections(bars)
    before, after = diagnostics['trainingBefore'], diagnostics['trainingAfter']
    hb, ha = diagnostics['heldOutBefore'], diagnostics['heldOutAfter']
    # Apply the user's regression limits to both fitted and whole-bar holdout
    # sets. Fitness here is the fraction of bars with axis RMSE <= 10 mm.
    regressed = any(a[k] > b[k] * rmse_regress_ratio for b, a in [(before, after), (hb, ha)]
                    for k in ['weightedRmseM', 'equalBarRmseM'])
    regressed |= any(a['within10mmBarFraction'] < b['within10mmBarFraction'] * fitness_regress_ratio
                     for b, a in [(before, after), (hb, ha)])
    sensitivity = diagnostics['sensitivity']
    if max(s['pivotShiftM'] for s in sensitivity) > .005 or max(s['rotationDeg'] for s in sensitivity) > .3:
        raise ValueError('精配准受单根钢筋影响过大，无法稳定应用')
    applied = not regressed or bool(apply_when_regressed)
    candidate = correction @ initial
    final = candidate if applied else initial
    quaternion = Rotation.from_matrix(final[:3, :3]).as_quat()
    diagnostics.update(schema=METHOD, coordinateFrame='BIM-GLB-metres', correctionBimColumnMajor=correction.T.ravel().tolist(),
                       initialTransform=init_matrix_16, candidateTransform=candidate.T.ravel().tolist(), bars=audit,
                       metric='circle-centre perpendicular distance to the fixed design line',
                       denoiseVersion=directory.name,
                       sourceHashes={name: hashlib.file_digest((directory/name).open('rb'), 'sha256').hexdigest()
                                     for name in ['cleaned.las', 'instance-map.json', 'control-net.json']})
    return dict(method=METHOD, transform=final.T.ravel().tolist(),
                quaternion=dict(zip(['qx', 'qy', 'qz', 'qw'], map(float, quaternion))),
                translation=dict(zip(['tx', 'ty', 'tz'], map(float, final[:3, 3]))),
                regressed=regressed, appliedFineResult=applied, fallback=not applied,
                metrics=dict(initFitness=before['within10mmBarFraction'], fineFitness=after['within10mmBarFraction'],
                             initRmse=before['weightedRmseM'], fineRmse=after['weightedRmseM'],
                             deltaTranslationM=diagnostics['fit']['pivotShiftM'], deltaRotationDeg=diagnostics['fit']['deltaRotationDeg'],
                             elapsedS=time.perf_counter()-start_time, sourceTotalPoints=len(points),
                             targetPoints=diagnostics['allEligibleBefore']['sectionCount']), diagnostics=diagnostics)
