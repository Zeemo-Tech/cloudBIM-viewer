"""Workbench-only independent cluster fits and explicit design mesh partitions."""
import numpy as np

from rebar_prior_axis import fit_independent_axis, axis_section, INDEPENDENT_METHOD
from rebar_metrics import surface_summary


def measure_clusters(vertices, segments, unit_points, radii, *, max_samples=64, window_scale=1):
    profile, fits, bows = [], [], []
    remaining = max_samples
    for index, (unit, start, end) in enumerate(segments):
        length = float(np.linalg.norm(end - start))
        tangent = (end - start) / length
        points = unit_points.get(unit, np.empty((0, 3)))
        axis, reason = fit_independent_axis(points, radii.get(unit))
        budget = min(64, max(3, remaining // (len(segments) - index)))
        remaining -= budget
        low, high = axis['observedRange'] if axis else (0., length)
        window = min(.015, max(.001, (high-low) / (min(budget, 16) * 8))) * window_scale
        rows = []
        for station in np.linspace(low, high, budget):
            fit, evidence = axis_section(axis, station, window) if axis else (None, {'reason': reason, 'pointCount': len(points)})
            observed = fit['center'] if fit else None
            design_station = float((observed-start) @ tangent) if fit else float(station)
            design = start + design_station * tangent
            offset = observed - design if fit else None
            row = {'designUnitId': unit, 'stationM': design_station, 'designCenterM': design.tolist(),
                   'observedCenterM': observed.tolist() if fit else None,
                   'transverseOffsetM': float(np.linalg.norm(offset)) if fit else None,
                   'offsetVectorM': offset.tolist() if fit else None,
                   'radiusM': fit['radiusM'] if fit else None, 'radiusDeltaM': None,
                   'fitRmseM': fit['fitRmseM'] if fit else None,
                   'arcCoverageDeg': fit['arcCoverageDeg'] if fit else None,
                   'inlierCount': fit['inlierCount'] if fit else 0, 'centerUncertaintyM': None,
                   'windowM': window, 'quality': evidence['reason'], 'axisMethod': INDEPENDENT_METHOD,
                   'radiusSource': 'design-prior', 'fitEvidence': {**evidence, 'axisMethod': INDEPENDENT_METHOD}}
            if axis:
                row.update(axisStationM=float(station), axisStartM=axis['start'].tolist(),
                           axisTangent=axis['tangent'].tolist())
            rows.append(row)
        # The matcher uses design-axis stations; fitting used only scan stations.
        profile.extend(sorted(rows, key=lambda row: row['stationM']))
        fitted = [r for r in rows if r['observedCenterM'] is not None]
        if fitted:
            s = np.array([r['axisStationM'] for r in fitted])
            xyz = np.array([r['observedCenterM'] for r in fitted])
            basis = np.c_[s-s.mean(), np.ones(len(s))]
            trend = basis @ np.linalg.lstsq(basis, xyz-xyz.mean(axis=0), rcond=None)[0]
            bows.append(float(np.max(np.linalg.norm(xyz-xyz.mean(axis=0)-trend, axis=1))))
        fits.append({'designUnitId': unit, 'pointCount': len(points), 'status': reason,
                     'designLengthM': length, 'observedLengthM': high-low if axis else None,
                     'fitRmseM': axis['fitRmseM'] if axis else None,
                     'directionDifferenceDeg': float(np.degrees(np.arccos(np.clip(abs(axis['tangent'] @ tangent), 0, 1)))) if axis else None,
                     'positionConstrained': False, 'lengthConstrained': False, 'directionConstrained': False})
    supported = [r for r in profile if r['observedCenterM'] is not None]
    return {'surface': surface_summary(np.full(len(vertices), np.nan), vertices),
            'longitudinalProfile': profile, 'clusterFits': fits,
            'crossSection': {'maxAbsRadiusDeltaM': None, 'supportedSectionCount': len(supported), 'sectionCount': len(profile)},
            'bending': {'maxCentrelineDepartureM': max((r['transverseOffsetM'] for r in supported), default=None),
                        'residualBowM': max(bows, default=None), 'curvatureMInv': None,
                        'method': INDEPENDENT_METHOD, 'quality': 'supported' if supported else 'insufficient-coverage'}}


def partition_design_mesh(vertices, faces, segments):
    """Disjoint face groups in the original vertex/index space, no geometry loss.

    Existing analytic straight segments supply the same identities as the scan
    clusters. Bend faces belong to the nearest finite design segment. Missing
    clusters retain their design part, so missing evidence cannot hide geometry.
    """
    def owners(points):
        best = np.full(len(points), np.inf)
        result = np.full(len(points), -1, dtype=int)
        for index, (_, start, end) in enumerate(segments):
            v = end-start
            t = np.clip((points-start) @ v / (v @ v), 0, 1)
            delta = points-start-t[:, None]*v
            distance = np.einsum('ij,ij->i', delta, delta)
            take = distance < best
            result[take], best[take] = index, distance[take]
        return result
    faces = np.asarray(faces, dtype=int).reshape(-1, 3)
    vertex_owner = owners(vertices)
    face_owner = owners(vertices[faces].mean(axis=1))
    return ([segments[i][0] if i >= 0 else None for i in vertex_owner],
            [segments[i][0] if i >= 0 else None for i in face_owner],
            [{'unitId': unit, 'indices': faces[face_owner == index].ravel().tolist()}
             for index, (unit, _, _) in enumerate(segments)])
