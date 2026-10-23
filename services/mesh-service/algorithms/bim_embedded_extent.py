"""Measured outer-cylinder continuation for already-supported embedded parts.

The finite IFC mesh seeds identity/transverse geometry. Only unclaimed source
rows may be added; model bounds remain distinct from measured surface bounds.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

VERSION = 'bim-embedded-extent-v2'
_MAX_EXTENSION = .20
_STATION = .01
_SURFACE = .003
_LINK = .006


def _extent(t):
    return [float(np.min(t)), float(np.max(t))] if len(t) else None


def _shape(t, radius, theta, normals, ids, reference_radius):
    if len(ids) < 20:
        return False, 'insufficient-measured-station-points'
    r = radius[ids]
    if abs(float(np.median(r))-reference_radius) > .0012:
        return False, 'radius-transition-or-axis-mismatch'
    if float(np.median(np.abs(r-np.median(r)))) > .001:
        return False, 'inconsistent-cross-section-radius'
    angle = np.sort(theta[ids] % (2*np.pi))
    span = 2*np.pi - np.max(np.diff(np.r_[angle, angle[0]+2*np.pi]))
    if span < np.pi/2:
        return False, 'insufficient-curved-cross-section'
    n = normals[ids]
    length = np.linalg.norm(n, axis=1)
    n = n/np.maximum(length, 1e-12)[:, None]
    # A planar strip can intersect a cylindrical distance band. It cannot supply
    # two appreciable normal directions, even if its apparent angular span grows.
    eigenvalues = np.linalg.eigvalsh(n.T@n/len(n))
    if eigenvalues[1] < .03:
        return False, 'planar-or-parallel-normal-strip'
    return True, None


def extend_embedded_axes(points, normals, scene, scene_report, labels, scene_owner, protected):
    """Extend supported outer shells in-place, returning source-row evidence.

    ``labels`` and ``scene_owner`` must be writable source-aligned arrays.
    Existing ownership is preserved. ``protected`` includes table and steel rows
    after structural fitting; this function never promotes their inner-hole
    continuation to an embedded component.
    """
    p = np.asarray(points)
    normal = np.asarray(normals)
    labels = np.asarray(labels)
    owner = np.asarray(scene_owner)
    protected = np.asarray(protected, bool)
    if p.ndim != 2 or p.shape[1] != 3 or normal.shape != p.shape:
        raise ValueError('embedded extent points/normals must be Nx3')
    if any(x.shape != (len(p),) for x in (labels, owner, protected)):
        raise ValueError('embedded extent masks must be source aligned')
    rows = {r['globalId']: r for r in scene_report.get('objects', [])}
    results = []
    added_total = 0
    for obj in scene.get('objects', []):
        if obj.get('category') != 'embedded':
            continue
        prior = rows.get(obj['globalId'])
        record = {'globalId': obj['globalId'], 'name': obj.get('name', ''),
                  'addedPointCount': 0, 'status': 'unsupported-seed', 'ends': []}
        results.append(record)
        if not prior or not prior.get('pointCount') or 'transform' not in prior:
            continue
        sid = int(prior['id'])
        v = np.asarray(obj['vertices'], float)
        if len(v) < 8:
            record['status'] = 'insufficient-model-geometry'
            continue
        pivot = v.mean(axis=0)
        values, vectors = np.linalg.eigh(np.cov(v.T))
        if values[-1] < 3*max(values[-2], 1e-14):
            record['status'] = 'thin-or-non-axial-part'
            continue
        axis = vectors[:, -1]
        axis *= 1 if axis[np.argmax(np.abs(axis))] >= 0 else -1
        transform = np.asarray(prior['transform'], float)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            record['status'] = 'invalid-local-transform'
            continue
        v = v@transform[:3, :3].T + transform[:3, 3]
        origin = transform[:3, :3]@pivot + transform[:3, 3]
        axis = transform[:3, :3]@axis
        axis /= np.linalg.norm(axis)
        d = v-origin
        model_t = d@axis
        lo, hi = float(model_t.min()), float(model_t.max())
        radii = np.linalg.norm(d-model_t[:, None]*axis, axis=1)
        outer_radius = float(np.max(radii))
        record.update(sceneId=sid, axisScan=axis.tolist(), originScanM=origin.tolist(),
                      modelAxialBoundsM=[lo, hi], modelLengthM=hi-lo,
                      modelOuterRadiusM=outer_radius, maxExtensionPerEndM=_MAX_EXTENSION)
        if outer_radius <= .002 or hi-lo < 3*outer_radius:
            record['status'] = 'short-or-non-tubular-part'
            continue
        ends = np.array([origin+(lo-_MAX_EXTENSION)*axis,
                         origin+(hi+_MAX_EXTENSION)*axis])
        reach = outer_radius+_SURFACE
        bbox = np.all((p >= ends.min(axis=0)-reach) & (p <= ends.max(axis=0)+reach), axis=1)
        ids = np.flatnonzero(bbox)
        if not len(ids):
            continue
        d = p[ids]-origin
        t = d@axis
        radial = d-t[:, None]*axis
        radius = np.linalg.norm(radial, axis=1)
        radial /= np.maximum(radius, 1e-12)[:, None]
        n = normal[ids]
        seed = (owner[ids] == sid) & (labels[ids] == 5) & ~protected[ids]
        record['currentOwnedAxialBoundsM'] = _extent(t[seed])
        allowed = seed | ((labels[ids] == 0) & (owner[ids] == 0) & ~protected[ids])
        shell = (allowed & (np.abs(radius-outer_radius) < _SURFACE)
                 & (np.abs(n@axis) < .5)
                 & (np.abs(np.einsum('ij,ij->i', n, radial)) > .75)
                 & (t >= lo-_MAX_EXTENSION) & (t <= hi+_MAX_EXTENSION))
        chosen = np.flatnonzero(shell)
        core_seed = seed[chosen] & (t[chosen] >= lo-_SURFACE) & (t[chosen] <= hi+_SURFACE)
        if np.count_nonzero(core_seed) < 50:
            record['status'] = 'insufficient-owned-outer-sidewall'
            continue
        reference = float(np.median(radius[chosen][core_seed]))
        record['observedSeedRadiusM'] = reference
        if abs(reference-outer_radius) > .0015:
            record['status'] = 'seed-radius-mismatch'
            continue
        pair = cKDTree(p[ids[chosen]]).query_pairs(_LINK, output_type='ndarray')
        graph = coo_matrix((np.ones(2*len(pair)),
                            (np.r_[pair[:, 0], pair[:, 1]], np.r_[pair[:, 1], pair[:, 0]])),
                           shape=(len(chosen), len(chosen))).tocsr()
        _, component = connected_components(graph, directed=False)
        connected = np.isin(component, np.unique(component[core_seed]))
        available = chosen[connected]
        u = np.cross(axis, np.eye(3)[np.argmin(np.abs(axis))])
        u /= np.linalg.norm(u)
        w = np.cross(axis, u)
        theta = np.arctan2(d@w, d@u)
        promoted = []
        for sign, cap in [(-1, lo), (1, hi)]:
            end = {'direction': sign, 'modelCapM': cap, 'addedPointCount': 0,
                   'observedAddedBoundsM': None, 'stations': []}
            record['ends'].append(end)
            # A supported tube far from a model cap does not authorize bridging
            # the unobserved interval between it and that cap.
            anchor = available[((t[available]-cap)*sign >= -_STATION)
                               & ((t[available]-cap)*sign <= 0)]
            ok, reason = _shape(t, radius, theta, n, anchor, reference)
            if not ok or not np.any(seed[anchor]):
                end['stopReason'] = 'no-supported-owned-sidewall-at-model-cap'
                continue
            accepted = []
            for step in range(int(round(_MAX_EXTENSION/_STATION))):
                a, b = step*_STATION, (step+1)*_STATION
                station = available[((t[available]-cap)*sign >= a)
                                    & ((t[available]-cap)*sign < b)]
                ok, reason = _shape(t, radius, theta, n, station, reference)
                if not ok:
                    end['stopReason'] = reason
                    break
                take = station[(labels[ids[station]] == 0) & (owner[ids[station]] == 0)
                               & ~protected[ids[station]]]
                accepted.extend(take.tolist())
                end['stations'].append({'outwardStartM': a, 'outwardEndM': b,
                                        'measuredPointCount': len(station),
                                        'radiusMedianM': float(np.median(radius[station]))})
            else:
                end['stopReason'] = 'maximum-measured-traversal-reached'
            if accepted:
                accepted = np.asarray(accepted, int)
                end['addedPointCount'] = len(accepted)
                end['observedAddedBoundsM'] = _extent(t[accepted])
                promoted.extend(accepted.tolist())
        if promoted:
            promoted = np.unique(promoted)
            actual = ids[promoted]
            # Recheck at write time; a prior object may already have claimed rows.
            actual = actual[(labels[actual] == 0) & (owner[actual] == 0) & ~protected[actual]]
            labels[actual] = 5
            owner[actual] = sid
            record['addedPointCount'] = len(actual)
            added_total += len(actual)
        record['status'] = 'measured-outer-shell-extended' if record['addedPointCount'] else 'no-continuous-extension'
        observed = (owner[ids] == sid) & (labels[ids] == 5)
        record['observedAxialBoundsM'] = _extent(t[observed])
        # Fit two IFC axial-end DOFs with a minimum-change prior. Connected,
        # shape-validated source support constrains the ends from below; an
        # unobserved end keeps its design value, not a fictitious scan endpoint.
        fitted_bounds = [lo, hi]
        for end in record['ends']:
            bounds = end['observedAddedBoundsM']
            if bounds:
                if end['direction'] < 0:
                    fitted_bounds[0] = min(lo, bounds[0])
                else:
                    fitted_bounds[1] = max(hi, bounds[1])
        record['flexibleRegistration'] = {
            'schema': 'ifc-axial-extent-registration-v1',
            'sourceGlobalId': obj['globalId'],
            'model': 'IFC cylindrical outer surface with two local axial end DOFs',
            'objective': 'minimum squared end displacement from design subject to covering contiguous validated outer-shell source support',
            'rigidTransformScan': transform.tolist(), 'originScanM': origin.tolist(),
            'axisScan': axis.tolist(), 'nominalOuterRadiusM': outer_radius,
            'radiusChanged': False, 'designAxialBoundsM': [lo, hi],
            'fittedAxialBoundsM': fitted_bounds,
            'endDisplacementsM': [fitted_bounds[0]-lo, fitted_bounds[1]-hi],
            'fittedAxialLengthM': fitted_bounds[1]-fitted_bounds[0],
            'observedAxialBoundsM': record['observedAxialBoundsM'],
            'physicalEndFacesConfirmed': [False, False],
            'unsupportedModelPolicy': 'retain design prior; inferred geometry has no point ownership',
        }
    return {'schema': 'bim-embedded-extent-v1', 'version': VERSION,
            'addedPointCount': int(added_total), 'objects': results,
            'policy': {'outerShellOnly': True, 'surfaceToleranceM': _SURFACE,
                       'connectionRadiusM': _LINK, 'stationLengthM': _STATION,
                       'maximumExtensionPerEndM': _MAX_EXTENSION,
                       'sourceRowsOnly': True, 'existingOwnershipPreserved': True,
                       'designBoundsPreserved': True,
                       'modelPolicy': 'fit IFC local axial-end DOFs to validated contiguous support; original IFC remains unchanged',
                       'unobservedPolicy': 'stop; do not bridge or synthesize'}}
