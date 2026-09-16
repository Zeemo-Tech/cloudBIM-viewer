"""Conservative, additive ownership of observed flat steel cut faces."""
import numpy as np


def _face_support(points, normals, endpoint, outward, radius, same_owner):
    empty = np.zeros(len(points), bool)
    if normals is None or len(points) < 36:
        return empty, None
    delta = points-endpoint
    axial = delta@outward
    radial = delta-axial[:, None]*outward
    distance = np.linalg.norm(radial, axis=1)
    length = np.linalg.norm(normals, axis=1)
    normal = normals/np.maximum(length[:, None], 1e-12)
    finite = np.isfinite(points).all(axis=1)&np.isfinite(normals).all(axis=1)&(length > .5)
    # Identity must come from an already owned, distributed cylinder behind
    # the cut. A disk or fixture on its own cannot manufacture a steel instance.
    side = (finite & same_owner & (axial < -radius*.5) & (axial > -4*radius)
            & (np.abs(distance-radius) < max(.0009, radius*.36))
            & (np.abs(normal@outward) < .35))
    if side.sum() < 24 or np.ptp(axial[side]) < 2*radius:
        return empty, None
    if np.linalg.eigvalsh(normal[side].T@normal[side]/side.sum())[-2] < .005:
        return empty, None
    tol = max(.0003, .15*radius)
    cap = finite & (np.abs(axial) <= max(.001, .5*radius)) & (distance <= 1.15*radius) & (np.abs(normal@outward) >= .7)
    if cap.sum() < 12:
        return empty, None
    cloud = delta[cap]
    center = np.median(cloud, axis=0)
    _, _, axes = np.linalg.svd(cloud-center, full_matrices=False)
    plane = axes[-1]
    inlier = np.abs((cloud-center)@plane) <= tol
    if inlier.sum() < 12 or inlier.mean() < .8:
        return empty, None
    cloud = cloud[inlier]
    center = cloud.mean(axis=0)
    values, vectors = np.linalg.eigh((cloud-center).T@(cloud-center)/len(cloud))
    plane = vectors[:, 0]
    if (abs(plane@outward) < .7 or values[1] < (.2*radius)**2
            or np.sqrt(max(0., values[0])) > max(.0003, .12*radius)
            or np.linalg.norm(center-(center@outward)*outward) > .65*radius):
        return empty, None
    # Independently distributed source subsets must locate the same plane.
    for half in (cloud[::2], cloud[1::2]):
        if len(half) < 6 or np.max(np.abs((half.mean(axis=0)-center)@plane)) > tol:
            return empty, None
        vals = np.linalg.eigvalsh((half-half.mean(axis=0)).T@(half-half.mean(axis=0))/len(half))
        if vals[1] < (.15*radius)**2:
            return empty, None
    on_plane = finite & (np.abs((delta-center)@plane) <= tol) & (np.abs(normal@plane) >= .7)
    # A larger coplanar fixture is not the finite disk at the steel cut.
    annulus = on_plane & (distance > 1.5*radius) & (distance < 2.5*radius)
    if annulus.sum() >= max(12, len(cloud)*.3):
        return empty, None
    selected = cap & on_plane
    return selected, {'centerM': (endpoint+center).tolist(), 'normal': plane.tolist(),
                      'planeRmseM': float(np.sqrt(max(0., values[0])))}


def complete_end_faces(points, normals, rows, pieces, owner, status, tree, source_ids):
    """Compete face hypotheses on frozen ownership; never move prior owners."""
    if tree is None or normals is None:
        return 0
    hypotheses = []
    for row in rows:
        if row['status'] != 'fitted':
            continue
        curve = np.asarray(row['centerlineM'])
        hypotheses.extend((row, label, curve[index], curve[index]-curve[other])
                          for label, index, other in [('start', 0, 1), ('end', -1, -2)])
    by_id = {row['id']: row for row in rows}
    for piece in pieces:
        if piece['kind'] != 'terminal' or len(piece['unitIds']) != 1:
            continue
        if piece['status'] == 'fitted':
            curve = np.asarray(piece['centerlineM'])
        elif piece.get('localSupport') and piece['inferredLengthM']-piece['localSupport']['intervalsM'][-1][1] < piece['diameterM']:
            curve = np.asarray(piece['inferredCenterlineM'])
        else:
            continue
        hypotheses.append((by_id[piece['unitIds'][0]], piece['id'], curve[-1], curve[-1]-curve[-2]))
    proposals = []
    for row, label, endpoint, outward in hypotheses:
        radius = row['diameterM']/2
        norm = np.linalg.norm(outward)
        if norm < 1e-10:
            continue
        outward = outward/norm
        ids = source_ids[tree.query_ball_point(endpoint, r=5*radius)]
        mask, evidence = _face_support(points[ids], normals[ids], endpoint, outward, radius, owner[ids] == row['id'])
        mask &= (owner[ids] == 0)&np.isin(status[ids], [2, 3])
        if mask.sum() >= 6:
            proposals.append((row, label, ids[mask], evidence))
    # All candidates remain ambiguous if different physical owners claim them.
    first = {}; ambiguous = set()
    for row, _, ids, _ in proposals:
        for i in ids.tolist():
            if i in first and first[i] != row['id']:
                ambiguous.add(i)
            first[i] = row['id']
    total = 0
    for row, label, ids, evidence in proposals:
        selected = np.asarray([i for i in ids if i not in ambiguous and owner[i] == 0], dtype=np.int64)
        if len(selected) < 6:
            continue
        owner[selected] = row['id']; status[selected] = 1
        row.setdefault('endFaceSupport', []).append(dict(evidence, end=label, pointCount=len(selected)))
        row['pointCount'] += len(selected)
        total += len(selected)
    return total
