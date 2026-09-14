"""Design-topology control-net fitting with progressive source-point ownership.

The implementation deliberately separates *evidence* from the design prior.
The prior supplies identities, topology, diameter and length; only observed
fixed-radius surface support is allowed to create a fitted centreline.
Post-table and legacy layered/fused inputs are supported. Candidate retrieval and every
quadratic operation have explicit sample caps for multi-million-point LAS input.
"""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
import math
import multiprocessing
import time

import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from scipy.optimize import least_squares

from rebar_prior_axis import fit_prior_axis


VERSION = "design-control-net-v21"
_MAX_TREE_QUERIES = 256
_NEIGHBOURS_PER_QUERY = 192
_MAX_UNIT_CANDIDATES = 30_000
_MAX_VOTE_POINTS = 2_500
_MAX_AUTO_FEATURES = 2_400


def _progress(callback, label, done, total):
    if callback is not None:
        callback(label, done, total)


def _unit_rows(inventory):
    rows = []
    for index, source in enumerate((inventory or {}).get("units", [])):
        try:
            start = np.asarray(source["startM"], dtype=float)
            end = np.asarray(source["endM"], dtype=float)
            length = float(source.get("lengthM", np.linalg.norm(end - start)))
            direction = np.asarray(source.get("direction", end - start), dtype=float)
            diameter = float(source["diameterM"])
        except (KeyError, TypeError, ValueError):
            continue
        norm = np.linalg.norm(direction)
        if (start.shape != (3,) or end.shape != (3,) or not np.isfinite(start).all()
                or not np.isfinite(end).all() or not np.isfinite(length) or length <= 0
                or not np.isfinite(diameter) or diameter <= 0 or norm <= 0):
            continue
        direction /= norm
        rows.append({"index": index, "source": source, "start": start,
                     "end": start + length * direction, "direction": direction,
                     "length": length, "radius": diameter / 2,
                     "kind": source.get("kind", "straight")})
    return rows


def _frame(tangent):
    helper = np.eye(3)[int(np.argmin(np.abs(tangent)))]
    u = np.cross(tangent, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(tangent, u)


def _rotation_from_frames(a0, a1, b0, b1):
    """Return a proper rotation mapping two (possibly parallel) line frames."""
    def basis(x, y):
        x = x / np.linalg.norm(x)
        y = y - x * np.dot(x, y)
        if np.linalg.norm(y) < 1e-7:
            return None
        y /= np.linalg.norm(y)
        return np.column_stack((x, y, np.cross(x, y)))
    left, right = basis(a0, a1), basis(b0, b1)
    if left is None or right is None:
        return None
    rotation = right @ left.T
    if np.linalg.det(rotation) < 0:
        right[:, 2] *= -1
        rotation = right @ left.T
    return rotation


def _bounded_points(points, limit):
    if len(points) <= limit:
        return points
    order = np.lexsort((points[:, 2], points[:, 1], points[:, 0]))
    return points[order[np.linspace(0, len(order) - 1, limit, dtype=int)]]


def _auto_line_features(points, normals, radius):
    """Extract a bounded set of scan line primitives without using design pose."""
    if len(points) < 24:
        return []
    tree = cKDTree(points)
    sample_ids = np.linspace(0, len(points) - 1, min(_MAX_AUTO_FEATURES, len(points)), dtype=int)
    scale = max(.015, 5.0 * radius)
    features = []
    for point_id in sample_ids:
        near = tree.query_ball_point(points[point_id], scale)
        if len(near) < 12:
            continue
        if len(near) > 128:
            near = np.asarray(near)[np.linspace(0, len(near) - 1, 128, dtype=int)]
        local = points[near]
        center = np.median(local, axis=0)
        core_distance = np.linalg.norm(local - center, axis=1)
        core = local[core_distance <= np.quantile(core_distance, .9)]
        if len(core) < 10:
            continue
        values, vectors = np.linalg.eigh(np.cov((core - core.mean(axis=0)).T))
        tangent = vectors[:, -1]
        linearity = values[-1] / max(values[-2], 1e-14)
        if normals is not None:
            local_normals = normals[np.asarray(near)]
            local_normals = local_normals[np.isfinite(local_normals).all(axis=1)]
            if len(local_normals) >= 8:
                _, normal_vectors = np.linalg.eigh(local_normals.T @ local_normals)
                normal_tangent = normal_vectors[:, 0]
                if abs(np.dot(normal_tangent, tangent)) > .7:
                    tangent = normal_tangent
                    linearity = max(linearity, 5.)
        if linearity < 3.2:
            continue
        tangent /= np.linalg.norm(tangent)
        if tangent[np.argmax(np.abs(tangent))] < 0:
            tangent = -tangent
        features.append((points[point_id], tangent, min(linearity, 20.)))
    if not features:
        return []

    # Greedy line grouping uses only the capped feature set.  A transverse gate
    # joins samples on one tube while allowing arbitrary gaps along its axis.
    remaining = set(range(len(features)))
    primitives = []
    gate = max(.018, 4.5 * radius)
    while remaining and len(primitives) < 32:
        seed = max(remaining, key=lambda i: features[i][2])
        p0, t0, _ = features[seed]
        group = []
        for i in sorted(remaining):
            p, tangent, _ = features[i]
            if abs(np.dot(tangent, t0)) < math.cos(math.radians(15)):
                continue
            delta = p - p0
            if np.linalg.norm(delta - t0 * np.dot(delta, t0)) <= gate:
                group.append(i)
        for i in group:
            remaining.discard(i)
        if len(group) < 5:
            continue
        xyz = np.asarray([features[i][0] for i in group])
        tangents = np.asarray([features[i][1] * np.sign(np.dot(features[i][1], t0)) for i in group])
        tangent = np.mean(tangents, axis=0)
        tangent /= np.linalg.norm(tangent)
        center = np.median(xyz, axis=0)
        along = (xyz - center) @ tangent
        low, high = np.quantile(along, [.03, .97])
        span = float(high - low)
        if span < max(.025, 6 * radius):
            continue
        primitives.append({"center": center + .5 * (low + high) * tangent,
                           "direction": tangent, "length": span,
                           "count": len(group)})
    return primitives


def _registration_score(rotation, translation, units, observed):
    if not units or not observed:
        return 0., 0, None
    direction = np.asarray([u['direction'] for u in units]) @ rotation.T
    centers = np.asarray([(u['start']+u['end'])/2 for u in units]) @ rotation.T + translation
    observed_centers = np.asarray([o['center'] for o in observed])
    angle = np.abs(direction @ np.asarray([o['direction'] for o in observed]).T)
    delta = observed_centers[None, :, :] - centers[:, None, :]
    axial = np.einsum('ijk,ik->ij', delta, direction)
    transverse = np.linalg.norm(delta - axial[:, :, None]*direction[:, None, :], axis=2)
    lengths = np.asarray([u['length'] for u in units])
    slack = .5*(lengths[:, None] + np.asarray([o['length'] for o in observed])[None, :]) + .08
    error = transverse + .2*np.maximum(0., np.abs(axial)-slack)
    weights = np.minimum(lengths[:, None], 1.) * np.exp(-error/.018)
    weights[(angle < math.cos(math.radians(18))) | (error >= .065)] = 0.
    # A measured line may not supply evidence for multiple design identities.
    rows, cols = linear_sum_assignment(-weights)
    accepted = weights[rows, cols] > 0
    rows, cols = rows[accepted], cols[accepted]
    return float(weights[rows, cols].sum()), len(rows), float(error[rows, cols].mean()) if len(rows) else None


def _auto_registration(points, normals, units):
    """RANSAC-like registration of line topology; saved absolute pose is unused."""
    radius = float(np.median([u["radius"] for u in units])) if units else .004
    features = _auto_line_features(points, normals, radius)
    if not units or not features:
        return np.eye(3), np.zeros(3), {"method": "geometry-line-ransac-v1",
            "status": "insufficient-global-evidence", "featureLines": len(features),
            "matchedUnits": 0, "rmseM": None}
    design = sorted(units, key=lambda u: (-u["length"], u["index"]))[:24]
    scan = sorted(features, key=lambda x: (-x["length"], -x["count"]))[:20]
    candidates = []
    # A single line leaves roll underdetermined, but is still a useful fallback.
    for unit in design[:8]:
        for line in scan[:12]:
            for sign in (-1., 1.):
                target = sign * line["direction"]
                cross = np.cross(unit["direction"], target)
                dot = np.clip(np.dot(unit["direction"], target), -1., 1.)
                if np.linalg.norm(cross) < 1e-9:
                    rotation = np.eye(3) if dot > 0 else _rotation_from_frames(
                        unit["direction"], _frame(unit["direction"])[0], target,
                        _frame(target)[0])
                else:
                    axis = cross / np.linalg.norm(cross)
                    angle = math.acos(dot)
                    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]],
                                  [-axis[1], axis[0], 0]])
                    rotation = np.eye(3) + math.sin(angle) * k + (1-math.cos(angle)) * (k @ k)
                center = (unit["start"] + unit["end"]) / 2
                candidates.append((rotation, line["center"] - rotation @ center))
    # Two lines resolve all rotation axes. Parallel pairs use their transverse
    # spacing as the second frame vector, preserving the design lane topology.
    budget = 3600
    for ia in range(len(design)):
        for ib in range(ia + 1, len(design)):
            da, db = design[ia], design[ib]
            ca, cb = (da["start"]+da["end"])/2, (db["start"]+db["end"])/2
            design_parallel = abs(np.dot(da["direction"], db["direction"])) > .94
            second_design = cb-ca if design_parallel else db["direction"]
            for ja in range(len(scan)):
                for jb in range(ja + 1, len(scan)):
                    if len(candidates) >= budget:
                        break
                    oa, ob = scan[ja], scan[jb]
                    scan_parallel = abs(np.dot(oa["direction"], ob["direction"])) > .94
                    if design_parallel != scan_parallel:
                        continue
                    if abs(abs(np.dot(da["direction"], db["direction"])) - abs(np.dot(oa["direction"], ob["direction"]))) > .12:
                        continue
                    for swap in (False, True):
                        la, lb = (ob, oa) if swap else (oa, ob)
                        for sa in (-1., 1.):
                            for sb in (-1., 1.):
                                second_scan = lb["center"]-la["center"] if scan_parallel else sb*lb["direction"]
                                rotation = _rotation_from_frames(sa*da["direction"], second_design,
                                                                 la["direction"], second_scan)
                                if rotation is None:
                                    continue
                                translation = .5*((la["center"]-rotation@ca) + (lb["center"]-rotation@cb))
                                candidates.append((rotation, translation))
                if len(candidates) >= budget:
                    break
            if len(candidates) >= budget:
                break
        if len(candidates) >= budget:
            break
    ranked = []
    for rotation, translation in candidates:
        value = _registration_score(rotation, translation, units, scan)
        key = (value[0], value[1], -(value[2] if value[2] is not None else 1e9))
        ranked.append((key, rotation, translation, value))
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, rotation, translation, (score, matched, error) = ranked[0]
    # Keep distinct transforms only.  Equivalent direction-sign hypotheses are
    # common and do not constitute an identity ambiguity.
    alternatives = []
    design_centers = np.asarray([(u["start"]+u["end"])/2 for u in units])
    best_centers = design_centers@rotation.T+translation
    identity_ambiguous = False
    for _, other_r, other_t, other_value in ranked[1:]:
        if len(alternatives) >= 3 or other_value[0] < .90*score:
            break
        other_centers = design_centers@other_r.T+other_t
        per_identity = float(np.sqrt(np.mean(np.sum((other_centers-best_centers)**2, axis=1))))
        # Is the same geometric set explained while individual design IDs move?
        nearest = cKDTree(best_centers).query(other_centers, k=1)[0]
        set_error = float(np.sqrt(np.mean(nearest**2)))
        transform_delta = np.linalg.norm(other_r-rotation)+np.linalg.norm(other_t-translation)
        if transform_delta < 1e-4 or per_identity < max(.003, radius):
            continue
        if any(np.linalg.norm(other_r-np.asarray(item["rotation"]))
               + np.linalg.norm(other_t-np.asarray(item["translationM"])) < 1e-4
               for item in alternatives):
            continue
        alternatives.append({"score": float(other_value[0]), "matchedUnits": int(other_value[1]),
            "rmseM": other_value[2], "rotation": other_r.tolist(), "translationM": other_t.tolist(),
            "identityDisplacementM": per_identity, "geometrySetRmseM": set_error})
        if other_value[0] >= .90*score and per_identity > max(.018, 4*radius):
            identity_ambiguous = True
    minimum = 1 if len(units) == 1 else 2
    status = "supported" if matched >= minimum else "insufficient-global-evidence"
    return rotation, translation, {"method": "geometry-line-ransac-v1", "status": status,
        "featureLines": len(features), "matchedUnits": matched, "score": float(score),
        "rmseM": error, "rotation": rotation.tolist(), "translationM": translation.tolist(),
        "identityStatus": "ambiguous" if identity_ambiguous else "supported" if status == "supported" else "unresolved",
        "confidence": float(max(0., min(1., 1.-(alternatives[0]["score"]/score)))) if alternatives and score > 0 else 1.,
        "alternatives": alternatives}


def _candidate_indices(tree, source_ids, start, end, gate, neighbours=None):
    length = np.linalg.norm(end-start)
    count = min(_MAX_TREE_QUERIES, max(3, int(math.ceil(length / max(.012, gate*.45))) + 1))
    stations = start + np.linspace(0, 1, count)[:, None] * (end-start)
    k = min(neighbours or _NEIGHBOURS_PER_QUERY, len(source_ids))
    if k == 0:
        return np.empty(0, dtype=int)
    distance, local = tree.query(stations, k=k, distance_upper_bound=gate)
    local = np.asarray(local).reshape(-1)
    local = local[local < len(source_ids)]
    if not len(local):
        return np.empty(0, dtype=int)
    result = source_ids[np.unique(local)]
    if len(result) > _MAX_UNIT_CANDIDATES:
        result = result[np.linspace(0, len(result)-1, _MAX_UNIT_CANDIDATES, dtype=int)]
    return result


def _full_model_candidates(tree, source_ids, curve, envelope):
    chunks = []
    for start, end in zip(curve[:-1], curve[1:]):
        distance = np.linalg.norm(end-start)
        count = max(2, int(math.ceil(distance/envelope))+1)
        stations = start + np.linspace(0., 1., count)[:, None]*(end-start)
        # Every point inside the tube envelope is within this radius of a
        # station, including halfway between stations. Query in small batches.
        for begin in range(0, len(stations), 64):
            balls = tree.query_ball_point(stations[begin:begin+64], r=envelope*1.12)
            chunks.extend(np.asarray(ids, dtype=np.int64) for ids in balls if len(ids))
    return source_ids[np.unique(np.concatenate(chunks))] if chunks else np.empty(0, dtype=np.int64)


def _circle_vote_groups(keys):
    """Return the same lexicographic groups as unique(keys, axis=0).

    Packing the bounded two-dimensional voting grid avoids a structured-row
    sort. Group order is retained because vote-count ties select seeds by it.
    Extreme integer ranges fall back to the row sort rather than overflow.
    """
    lo, hi = keys.min(axis=0), keys.max(axis=0)
    width = int(hi[1]) - int(lo[1]) + 1
    height = int(hi[0]) - int(lo[0]) + 1
    if height <= np.iinfo(np.int64).max // width:
        packed = (keys[:, 0] - lo[0]) * width + (keys[:, 1] - lo[1])
        _, inverse, counts = np.unique(packed, return_inverse=True, return_counts=True)
    else:
        _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    return inverse, counts


def _circle_hypotheses(xy, along, radius, gate, normals_xy=None, *, ranked=False, normal_alignment=False):
    if len(xy) < 24:
        return [] if ranked else (None, None)
    ids = np.linspace(0, len(xy)-1, min(_MAX_VOTE_POINTS, len(xy)), dtype=int)
    ids = ids[np.argsort(along[ids], kind="stable")]
    p = xy[ids]
    s = along[ids]
    centers = [np.zeros((1, 2)), np.median(p, axis=0, keepdims=True)]
    if normals_xy is not None:
        n = normals_xy[ids]
        nlen = np.linalg.norm(n, axis=1)
        valid = nlen > .35
        n = n[valid] / nlen[valid, None]
        q = p[valid]
        centers.append(q - radius*n)
        centers.append(q + radius*n)
    # Known-radius chord intersections create centre votes. Pair only nearby
    # axial samples, and cap fan-out to keep this linear in the sample budget.
    pair_centers = []
    axial_window = max(.012, 4*radius)
    # The former nested Python loop dominated full-inventory runtime. Each
    # offset below represents exactly the same bounded 12-neighbour fan-out,
    # but all valid chords at that offset are evaluated together in NumPy.
    for offset in range(1, min(13, len(p))):
        chord = p[offset:] - p[:-offset]
        distance = np.linalg.norm(chord, axis=1)
        valid = ((s[offset:] - s[:-offset] <= axial_window)
                 & (distance > .25*radius) & (distance < 1.98*radius))
        if not np.any(valid):
            continue
        chord = chord[valid]
        distance = distance[valid]
        midpoint = .5*(p[offset:][valid] + p[:-offset][valid])
        normal = np.column_stack((-chord[:, 1], chord[:, 0])) / distance[:, None]
        height = np.sqrt(np.maximum(radius*radius - .25*distance*distance, 0.))
        pair_centers.extend((midpoint + height[:, None]*normal,
                             midpoint - height[:, None]*normal))
    if pair_centers:
        centers.extend(pair_centers)
    centers = np.concatenate(centers, axis=0)
    centers = centers[np.isfinite(centers).all(axis=1)]
    centers = centers[np.linalg.norm(centers, axis=1) <= gate]
    if not len(centers):
        return [] if ranked else (None, None)
    cell = max(.00045, .14*radius)
    keys = np.round(centers/cell).astype(np.int64)
    inverse, counts = _circle_vote_groups(keys)
    top = np.argsort(counts, kind="stable")[-24:]
    seeds=[]
    for i in top:
        group=centers[inverse==i]
        middle=(len(group)-1)//2,len(group)//2
        ordered=np.partition(group,middle,axis=0)
        seeds.append((ordered[middle[0]]+ordered[middle[1]])*.5)
    seeds=np.asarray(seeds)
    tolerance = max(.0008, .32*radius)
    evaluated = []
    low, high = np.quantile(along, [.01, .99])
    span = max(high-low, 1e-9)
    bins = np.clip(((along-low)/span*23).astype(int), 0, 23)
    radial=xy[None,:,:]-seeds[:,None,:]
    distance=np.linalg.norm(radial,axis=2)
    residuals=np.abs(distance-radius)
    support=residuals<=tolerance
    if normal_alignment and normals_xy is not None:
        norm_product=distance*np.linalg.norm(normals_xy,axis=1)[None,:]
        agreement=np.abs(np.einsum('snj,nj->sn',radial,normals_xy))
        support&=(norm_product<1e-10)|(agreement>=.8*norm_product)
    occupied_bins=np.broadcast_to(np.arange(len(seeds))[:,None]*24+bins,support.shape)
    occupied=np.bincount(occupied_bins[support],minlength=len(seeds)*24).reshape(-1,24)
    balanced=np.minimum(occupied,24).sum(axis=1)
    fit_spans=np.max(np.where(support,along,-np.inf),axis=1)-np.min(np.where(support,along,np.inf),axis=1)
    enough=support.sum(axis=1)>=24
    fit_spans=np.where(enough,fit_spans,0.)
    proximity=np.maximum(.72,1.-.18*np.linalg.norm(seeds,axis=1)/max(gate,1e-9))
    scores=balanced*proximity*np.minimum(1.,fit_spans/max(.03,.25*span))
    for seed_id in np.flatnonzero(enough):
        center,residual,inliers=seeds[seed_id],residuals[seed_id],support[seed_id]
        fit_span,score=float(fit_spans[seed_id]),float(scores[seed_id])
        if ranked:
            cross_section = xy[inliers]-center
            cross_section -= np.mean(cross_section, axis=0)
            curvature = np.linalg.eigvalsh(cross_section.T@cross_section/len(cross_section))[0]/radius**2
            score *= min(1., curvature/.04)
        evaluated.append((score, center, inliers, residual, fit_span))
    if not evaluated:
        return [] if ranked else (None, None)
    evaluated.sort(key=lambda item: (-item[0], float(np.linalg.norm(item[1])), item[1][0], item[1][1]))
    if ranked:
        distinct = []
        for item in evaluated:
            if all(np.linalg.norm(item[1]-other[1]) > .65*radius for other in distinct):
                distinct.append(item)
        return distinct[:8]
    return evaluated[0], evaluated[1] if len(evaluated) > 1 else None


def _piecewise_shell(xy, along, radius, gate, normals_xy, fallback, centers_out=None, *, normal_alignment=False):
    """Select one continuous observed cylinder across the station bands."""
    low, high = np.quantile(along, [.01, .99])
    if high-low <= 0:
        return fallback
    bands = []
    edges = np.linspace(low, high, 13)
    for i in range(len(edges)-1):
        local = (along >= edges[i]) & (along <= edges[i+1] if i == len(edges)-2 else along < edges[i+1])
        if local.sum() < 24:
            continue
        hypotheses = _circle_hypotheses(xy[local], along[local], radius, gate,
            normals_xy[local] if normals_xy is not None else None, ranked=True,
            normal_alignment=normal_alignment or high-low > 2.)
        if hypotheses:
            bands.append((i, np.flatnonzero(local), hypotheses))
    if not bands:
        return fallback
    scores, parents = [], []
    for index, (band, ids, choices) in enumerate(bands):
        reward = np.array([item[0]/576. for item in choices])
        if index == 0:
            scores.append(reward)
            parents.append(None)
            continue
        previous = bands[index-1]
        step = max(.65*radius, .012*(edges[1]-edges[0])*(band-previous[0]))
        distance = np.linalg.norm(np.array([item[1] for item in choices])[:, None]
                                  -np.array([item[1] for item in previous[2]])[None, :], axis=2)
        transitions = scores[-1][None, :] - .35*(distance/step)**2
        parent = transitions.argmax(axis=1)
        scores.append(reward + transitions[np.arange(len(choices)), parent])
        parents.append(parent)
    choice = int(scores[-1].argmax())
    result = np.zeros(len(xy), dtype=bool)
    for index in range(len(bands)-1, -1, -1):
        _, ids, hypotheses = bands[index]
        result[ids[hypotheses[choice][2]]] = True
        if centers_out is not None:
            centers_out.append((float(np.median(along[ids])), hypotheses[choice][1]))
        if index:
            choice = int(parents[index][choice])
    return result if result.sum() >= max(32, .35*fallback.sum()) else fallback


def _polyline_distances(points, curve):
    best = np.full(len(points), np.inf)
    for start, end in zip(curve[:-1], curve[1:]):
        delta = end-start
        denom = float(np.dot(delta, delta))
        t = np.clip((points-start)@delta/max(denom, 1e-16), 0., 1.)
        d = np.linalg.norm(points-(start+t[:, None]*delta), axis=1)
        best = np.minimum(best, d)
    return best


def _exact_length(curve, length):
    distance = float(np.linalg.norm(np.diff(curve, axis=0), axis=1).sum())
    if distance <= 0:
        return curve
    return curve[0] + (curve-curve[0]) * (length/distance)


def _refine_straight_ends(points, normals, tree, source_ids, owner, model):
    """Use held-out tube evidence to correct unsupported spline extrapolation.

    Two transverse basis functions per end vanish with zero slope inside the
    supported body. Only four parameters and at most 2400 samples are solved;
    the confirmed interior, physical diameter and design arc length are kept.
    """
    curve, unit = model['curve'], model['unit']
    evidence = model.get('axisEvidenceRangeM')
    if unit['kind'] != 'straight' or unit['length'] < 2. or len(curve) < 5 or evidence is None:
        return
    radius, tolerance = unit['radius'], model['tolerance']
    tangent = curve[-1]-curve[0]
    length = float(np.linalg.norm(tangent))
    gaps = (float(evidence[0]), length-float(evidence[1]))
    # Small end gaps do not justify another fit; very long gaps have no nearby
    # reliable anchor and must remain extrapolated rather than guessed.
    sides = [(side, gap) for side, gap in zip(('start', 'end'), gaps) if .15 < gap < .75]
    if not sides:
        return
    tangent /= length
    u, v = _frame(tangent)
    origin = curve[0]
    stations = (curve-origin)@tangent
    if np.any(np.diff(stations) <= 0):
        return
    axis_xy = np.column_stack(((curve-origin)@u, (curve-origin)@v))
    ids = model['candidateIds']
    available = owner[ids] == 0
    ids = ids[available]
    delta = points[ids]-origin
    along = delta@tangent
    xy = np.column_stack((delta@u, delta@v))
    base = np.column_stack([np.interp(along, stations, axis_xy[:, j]) for j in (0, 1)])
    valid = np.linalg.norm(xy-base, axis=1) < max(.02, 4*radius)
    if normals is not None:
        normal = normals[ids]
        norm = np.linalg.norm(normal, axis=1)
        valid &= (norm < .5) | (np.abs(normal@tangent) < .45*norm)
    proposed, events = curve.copy(), []
    for side, gap in sides:
        extent = min(.9, max(.5, gap+.15))
        position = along if side == 'start' else length-along
        keep = valid & (position > .015) & (position < extent)
        if keep.sum() < 192:
            continue
        target = xy[keep]-base[keep]
        q = (stations if side == 'start' else length-stations)/extent
        fade = np.clip(1-q, 0., 1.)**2
        vertex_basis = np.column_stack((fade, np.clip(q, 0., 1.)*fade))
        basis = np.column_stack([np.interp(along[keep], stations, vertex_basis[:, j]) for j in (0, 1)])
        bins = np.clip((position[keep]/extent*16).astype(int), 0, 15)
        counts = np.bincount(bins, minlength=16)
        reliable = counts >= max(12, .15*np.median(counts[counts > 0]))
        if reliable.sum() < 12 or not reliable[:3].any() or not reliable[-3:].all():
            continue
        weights = np.sqrt(np.median(counts[reliable])/np.maximum(counts[bins], 24))
        if len(target) > 2400:
            sample = np.linspace(0, len(target)-1, 2400, dtype=int)
            target, basis, bins, weights = target[sample], basis[sample], bins[sample], weights[sample]
        train = (bins%2 == 0) & reliable[bins]
        held = (bins%2 == 1) & reliable[bins]
        if min(train.sum(), held.sum()) < 96:
            continue
        def residual(parameters):
            return np.linalg.norm(target-basis@parameters.reshape(2, 2), axis=1)-radius
        def solve(mask, seed):
            def jacobian(parameters):
                radial = basis@parameters.reshape(2, 2)-target
                radial /= np.maximum(np.linalg.norm(radial, axis=1)[:, None], 1e-12)
                return (basis[:, :, None]*radial[:, None, :]*weights[:, None, None]).reshape(-1, 4)[mask]
            return least_squares(lambda p: residual(p)[mask]*weights[mask], seed,
                jac=jacobian, bounds=(-5*radius, 5*radius), loss='soft_l1',
                f_scale=max(.0003, .1*radius), max_nfev=60)
        before = np.abs(residual(np.zeros(4)))
        # Avoid perturbing already-adherent ends (or paying for a solve) for
        # sub-millimetre fluctuations within the scanner's local surface spread.
        if np.median(before[held]) < max(.0005, .12*radius):
            continue
        checked = solve(train, np.zeros(4))
        after = np.abs(residual(checked.x))
        old_score, new_score = float(np.median(before[held])), float(np.median(after[held]))
        improving = sum(np.median(after[bins == b]) < np.median(before[bins == b])
                        for b in np.unique(bins[held]))
        if (not checked.success or old_score-new_score < max(.0001, .15*old_score)
                or improving < 5
                or np.mean(after[held] <= tolerance) < np.mean(before[held] <= tolerance)):
            continue
        # Reject flat surfaces / isolated streaks that happen to lie on a circle.
        # Verify curved cross-sections at several independent axial locations.
        centred = target-basis@checked.x.reshape(2, 2)
        curved_bands = 0
        for band in np.unique(bins[held]):
            section = centred[(bins == band) & (after <= tolerance)]
            if len(section) >= 12 and np.linalg.eigvalsh(np.cov(section.T))[0] > .001*radius*radius:
                curved_bands += 1
        if curved_bands < 5:
            continue
        fitted = solve(train | held, checked.x)
        offsets = vertex_basis@fitted.x.reshape(2, 2)
        change = float(np.linalg.norm(offsets, axis=1).max())
        stability = float(np.linalg.norm(vertex_basis@(fitted.x-checked.x).reshape(2, 2), axis=1).max())
        envelope = max(.02, 4*radius)
        if (not fitted.success or change > min(3*radius, envelope-radius-tolerance-.001)
                or stability > max(.001, .4*radius)):
            continue
        proposed += offsets@np.array([u, v])
        events.append({'side': side, 'correctionSpanM': extent,
            'validationMedianBeforeM': old_score, 'validationMedianAfterM': new_score,
            'maxShiftM': change, 'improvedValidationBands': int(improving)})
    if not events:
        return
    # Correct only the two terminal segments to retain design arc length exactly;
    # rescaling the entire curve would needlessly move its reliable interior.
    difference = unit['length']-float(np.linalg.norm(np.diff(proposed, axis=0), axis=1).sum())
    if abs(difference) > .5*radius:
        return
    for end, neighbour in ((0, 1), (-1, -2)):
        direction = proposed[end]-proposed[neighbour]
        proposed[end] += .5*difference*direction/np.linalg.norm(direction)
    model['curve'] = proposed
    model['axisRefinement'] = {'method': 'held-out-full-surface-end-correction', 'ends': events}
    direction = proposed[-1]-proposed[0]
    direction /= np.linalg.norm(direction)
    model['directionDifferenceDeg'] = float(np.degrees(np.arccos(np.clip(direction@unit['direction'], -1., 1.))))
    # The bounded shift keeps the entire corrected support tube inside the
    # original candidate envelope. Reuse its full-source IDs and unchanged
    # interior distances; only nearby points need projection again.
    radial = (model['radial'][available].copy() if 'radial' in model
              else _polyline_distances(points[ids], curve))
    moved = np.linalg.norm(proposed-curve, axis=1) > 1e-12
    affected = np.flatnonzero(moved[:-1] | moved[1:])
    near = np.zeros(len(ids), bool)
    for segment in affected:
        near |= ((along >= stations[segment]-.03) & (along <= stations[segment+1]+.03))
    radial[near] = _polyline_distances(points[ids[near]], proposed)
    residuals = np.abs(radial-radius)
    model.update(candidateIds=ids, radial=radial, residual=residuals, support=residuals <= tolerance)


def _refine_short_surface(points, normals, tree, source_ids, owner, model):
    """Correct small pose bias on an independent, wider tube surface sample.

    Initial circle voting only selects a shell; scoring that same shell can
    conceal tilt relative to the rest of the scan. Use four bounded transverse
    parameters, with spatially held-out evidence and unchanged physical radius.
    """
    curve, unit = model['curve'], model['unit']
    radius = unit['radius']
    tangent = curve[-1]-curve[0]
    length = float(np.linalg.norm(tangent))
    tangent /= length
    u, v = _frame(tangent)
    origin = curve.mean(axis=0)
    margin = min(.18, .6*unit['length'])
    search = np.array([curve[0]-margin*tangent, curve[-1]+margin*tangent])
    ids = _full_model_candidates(tree, source_ids, search, max(.012, 3*radius))
    ids = ids[owner[ids] == 0]
    delta = points[ids]-origin
    along = delta@tangent
    xy = np.column_stack((delta@u, delta@v))
    radial = np.linalg.norm(xy, axis=1)
    keep = radial <= 3*radius
    if normals is not None:
        normal = normals[ids]
        nlength = np.linalg.norm(normal, axis=1)
        keep &= (nlength < .5) | (np.abs(normal@tangent) < .45*nlength)
    near = keep & (np.abs(radial-radius) <= model['tolerance'])
    if near.sum() < 96:
        return
    shell_stations = along[near]
    cells = np.floor((shell_stations-shell_stations.min())/max(.005, radius)).astype(int)
    counts = np.bincount(cells)
    dense = counts >= max(6, .15*np.median(counts[counts > 0]))
    ordered = np.sort(shell_stations[dense[cells]])
    groups = np.split(ordered, np.flatnonzero(np.diff(ordered) > max(.012, 3*radius))+1)
    groups = [g for g in groups if len(g) >= 96 and g[0] < -.1*length and g[-1] > .1*length]
    if len(groups) != 1:
        return
    low, high = np.quantile(groups[0], [.01, .99])
    span = float(high-low)
    if not .70*length <= span <= 1.5*unit['length']:
        return
    keep &= (along > low+.05*span) & (along < high-.05*span)
    along, xy = along[keep], xy[keep]
    if len(along) < 192:
        return
    station = (along-.5*(low+high))/span
    bins = np.clip(((station+.5)*16).astype(int), 0, 15)
    density = np.bincount(bins, minlength=16)
    reliable = density >= max(12, .15*np.median(density[density > 0]))
    if reliable.sum() < 12:
        return
    weights = np.sqrt(np.median(density[reliable])/np.maximum(density[bins], 24))
    if len(station) > 3000:
        sample = np.linspace(0, len(station)-1, 3000, dtype=int)
        station, xy, bins, weights = station[sample], xy[sample], bins[sample], weights[sample]
    train = (bins%2 == 0) & reliable[bins]
    held = (bins%2 == 1) & reliable[bins]
    if min(train.sum(), held.sum()) < 96:
        return
    def residual(parameters):
        return np.linalg.norm(xy-parameters[:2]-station[:, None]*parameters[2:], axis=1)-radius
    def solve(mask, seed):
        return least_squares(lambda p: residual(p)[mask]*weights[mask], seed,
            bounds=(-2*radius, 2*radius), loss='soft_l1', f_scale=max(.0003, .1*radius), max_nfev=40)
    checked = solve(train, np.zeros(4))
    before, after = np.abs(residual(np.zeros(4))), np.abs(residual(checked.x))
    old_score, new_score = float(np.median(before[held])), float(np.median(after[held]))
    if (not checked.success or old_score-new_score < max(.00005, .1*old_score)
            or np.mean(after[held] <= model['tolerance']) < np.mean(before[held] <= model['tolerance'])):
        return
    # A genuine correction must improve several spatial bands, not one dense
    # crossing, and keep the originally supported surface as independent evidence.
    improving = sum(np.median(after[bins == b]) < np.median(before[bins == b])
                    for b in np.unique(bins[held]))
    if improving < 5 or np.mean(after[before <= model['tolerance']] <= model['tolerance']) < .95:
        return
    fitted = solve(train | held, checked.x)
    endpoints = ((np.array([-.5, .5])*length-.5*(low+high))/span)[:, None]
    offset = fitted.x[:2]+endpoints*fitted.x[2:]
    validation_offset = checked.x[:2]+endpoints*checked.x[2:]
    if (not fitted.success or np.linalg.norm(offset, axis=1).max() > 1.5*radius
            or np.linalg.norm(offset-validation_offset, axis=1).max() > max(.001, .4*radius)):
        return
    proposed = curve+offset@np.array([u, v])
    centre = proposed.mean(axis=0)
    direction = proposed[-1]-proposed[0]
    direction /= np.linalg.norm(direction)
    model['curve'] = centre+np.array([-.5, .5])[:, None]*length*direction
    model['axisRefinement'] = {'method': 'held-out-full-surface-short-pose',
        'validationMedianBeforeM': old_score, 'validationMedianAfterM': new_score,
        'maxEndpointShiftM': float(np.linalg.norm(offset, axis=1).max()),
        'improvedValidationBands': int(improving)}
    model['directionDifferenceDeg'] = float(np.degrees(np.arccos(np.clip(direction@unit['direction'], -1., 1.))))
    radial = _polyline_distances(points[ids], model['curve'])
    residuals = np.abs(radial-radius)
    model.update(candidateIds=ids, radial=radial, residual=residuals, support=residuals <= model['tolerance'])


def _short_surface_interval(points, normals, tree, source_ids, owner, model):
    """Measure a finite, continuous full-source tube interval or return None."""
    unit = model['unit']
    curve = model['curve']
    length = float(np.linalg.norm(curve[-1]-curve[0]))
    tangent = (curve[-1]-curve[0])/length
    radius, tolerance = unit['radius'], model['tolerance']
    margin = min(.18, .6*unit['length'])
    search = np.array([curve[0]-margin*tangent, curve[-1]+margin*tangent])
    ids = _full_model_candidates(tree, source_ids, search, radius+tolerance)
    ids = ids[owner[ids] == 0]
    delta = points[ids]-curve[0]
    station = delta@tangent
    transverse = delta-station[:, None]*tangent
    radial = np.linalg.norm(transverse, axis=1)
    shell = (np.abs(radial-radius) <= tolerance) & (station >= -margin) & (station <= length+margin)
    if normals is not None:
        normal = normals[ids]
        product = np.linalg.norm(normal, axis=1)*radial
        shell &= (product < 1e-10) | (np.abs(np.einsum('ij,ij->i', normal, transverse)) >= .8*product)
    if shell.sum() < 32:
        return
    s = station[shell]
    bin_width = max(.005, radius)
    cells = np.floor((s+margin)/bin_width).astype(int)
    density = np.bincount(cells)
    dense = density >= max(6, .15*np.median(density[density > 0]))
    # Each retained interval must show a curved cross-section, not a narrow
    # radial-coincident streak. Aggregate moments keep this linear in points.
    u, v = _frame(tangent)
    xy = np.column_stack((transverse[shell]@u, transverse[shell]@v))
    means = np.column_stack([np.bincount(cells, weights=xy[:, j], minlength=len(density))
                             for j in (0, 1)])/np.maximum(density[:, None], 1)
    covariance = np.empty((len(density), 2, 2))
    for a in (0, 1):
        for b in (0, 1):
            covariance[:, a, b] = (np.bincount(cells, weights=xy[:, a]*xy[:, b], minlength=len(density))
                / np.maximum(density, 1) - means[:, a]*means[:, b])
    dense &= np.linalg.eigvalsh(covariance)[:, 0] >= .0001*radius*radius
    supported = np.sort(s[dense[cells]])
    groups = np.split(supported, np.flatnonzero(np.diff(supported) > max(.012, 3*radius))+1)
    groups = [g for g in groups if len(g) >= 32 and g[0] < .4*length and g[-1] > .6*length]
    if len(groups) != 1:
        return
    low, high = np.quantile(groups[0], [.005, .995])
    span = float(high-low)
    if (not .70*unit['length'] <= span <= 1.50*unit['length']
            or low <= -margin+bin_width or high >= length+margin-bin_width):
        return
    return ids, float(low), float(high), groups[0], margin


def _extend_short(points, normals, tree, source_ids, owner, model):
    """Extend a confirmed straight short axis only through dense, finite tube evidence.

    The radius and transverse axis stay fixed. A single bounded full-source query
    measures both ends; disconnected speckles, crossings and clipped long tubes
    cannot determine the new endpoints. Design geometry remains immutable.
    """
    interval = _short_surface_interval(points, normals, tree, source_ids, owner, model)
    if interval is None:
        return
    ids, low, high, observed, margin = interval
    curve, unit = model['curve'], model['unit']
    length = float(np.linalg.norm(curve[-1]-curve[0]))
    tangent = (curve[-1]-curve[0])/length
    radius, tolerance = unit['radius'], model['tolerance']
    span = high-low
    # A translated equal-length tube is pose evidence, not overlength. When
    # the span really is longer, locate both ends from that observed interval;
    # retaining an unsupported old endpoint would overestimate the length.
    low, high = float(low), float(high)
    if span <= length+.002:
        return
    model.update(curve=curve[0]+np.array([low, high])[:, None]*tangent,
                 fittedLengthM=float(high-low), observedLength=span,
                 lengthCheck='extended-observed-span', axisModel='evidence-extended-straight-cylinder',
                 lengthEvidence={'method': 'continuous-fixed-radius-end-support',
                     'extensionStartM': max(0., -low), 'extensionEndM': max(0., high-length),
                     'endpointShiftStartM': low, 'endpointShiftEndM': high-length,
                     'observedRangeM': [float(observed[0]), float(observed[-1])],
                     'supportPoints': len(observed), 'searchMarginM': margin})
    # Reuse the query for complete assignment; no second spatial search.
    radial = _polyline_distances(points[ids], model['curve'])
    residual = np.abs(radial-radius)
    model.update(candidateIds=ids, radial=radial, residual=residual, support=residual <= tolerance)


def _fit_unit(points, normals, candidate_ids, unit, start, direction, auto=False, *, web_normal_evidence=False):
    length, radius = unit["length"], unit["radius"]
    midpoint = start + .5*length*direction
    u, v = _frame(direction)
    candidate = points[candidate_ids]
    delta = candidate-start
    along = delta@direction
    margin = max(.015, .12*length if unit["kind"] == "short" else .06*length)
    axial = (along >= -margin) & (along <= length+margin)
    candidate_ids, candidate, along = candidate_ids[axial], candidate[axial], along[axial]
    if len(candidate) < 32:
        return None, "insufficient-local-evidence", candidate_ids
    if normals is not None and unit["kind"] in ("straight", "web"):
        n = normals[candidate_ids]
        nlength = np.linalg.norm(n, axis=1)
        keep = (nlength < .5) | (np.abs(n@direction) <= .5*nlength)
        candidate_ids, candidate, along = candidate_ids[keep], candidate[keep], along[keep]
        if len(candidate) < 32:
            return None, "insufficient-local-evidence", candidate_ids
    xy = np.column_stack(((candidate-start)@u, (candidate-start)@v))
    normals_xy = None
    if normals is not None:
        normals_xy = np.column_stack((normals[candidate_ids]@u, normals[candidate_ids]@v))
    gate = max(.045, min(.18, .32*length), 7*radius)
    best, second = _circle_hypotheses(xy, along, radius, gate, normals_xy,
                                          normal_alignment=web_normal_evidence or (unit["kind"] == "straight" and length > 2.))
    if best is None:
        return None, "no-fixed-radius-support", candidate_ids
    score, center, shell, _, initial_span = best
    if second is not None and np.linalg.norm(second[1]-center) > 1.5*radius and second[0] >= .93*score:
        # Junctions and partial arcs can vote for competing projected circles.
        # Only unresolved webs ask for this extra evidence: noisy normals must
        # not become a blanket veto on an otherwise supported cylinder. The
        # bounded retry keeps every geometric/identity guard and runs once.
        normal_count = (int(np.count_nonzero(np.linalg.norm(normals_xy, axis=1) > .5))
                        if normals_xy is not None else 0)
        if unit["kind"] == "web" and not web_normal_evidence and normal_count >= 24:
            model, reason, considered = _fit_unit(points, normals, candidate_ids, unit,
                start, direction, auto, web_normal_evidence=True)
            if model is not None:
                model['webEvidence'] = {'method': 'radial-normal-ambiguity-resolution',
                    'trigger': 'ambiguous-parallel-support', 'normalAbsCosMin': .8,
                    'validNormalPoints': normal_count,
                    'scope': 'same candidates; global hypotheses and continuous station bands'}
                return model, reason, considered
        return None, "ambiguous-parallel-support", candidate_ids
    centers = []
    shell = _piecewise_shell(xy, along, radius, gate, normals_xy, shell, centers,
                             normal_alignment=web_normal_evidence)
    shell_points = candidate[shell]
    # Permit a bounded local direction change (especially at rotated short/web
    # runs), but accept it only when the shell itself has a clear long axis.
    core = shell_points - np.median(shell_points, axis=0)
    _, singular, vectors = np.linalg.svd(core, full_matrices=False)
    observed_direction = vectors[0]
    if np.dot(observed_direction, direction) < 0:
        observed_direction = -observed_direction
    angle = math.degrees(math.acos(np.clip(np.dot(observed_direction, direction), -1., 1.)))
    limit = 65. if unit["kind"] in ("short", "web") else 28.
    if singular[0] > 2.2*max(singular[1], 1e-12) and angle <= limit:
        direction = observed_direction
        u, v = _frame(direction)
        start = midpoint - .5*length*direction
        delta = candidate-start
        along = delta@direction
        xy = np.column_stack((delta@u, delta@v))
        normals_xy = None if normals is None else np.column_stack((normals[candidate_ids]@u, normals[candidate_ids]@v))
        best, second = _circle_hypotheses(xy, along, radius, gate, normals_xy,
                                          normal_alignment=web_normal_evidence or (unit["kind"] == "straight" and length > 2.))
        if best is None:
            return None, "no-fixed-radius-support", candidate_ids
        score, center, shell, _, initial_span = best
        centers = []
        shell = _piecewise_shell(xy, along, radius, gate, normals_xy, shell, centers,
                             normal_alignment=web_normal_evidence)
    axis_start = start + center[0]*u + center[1]*v
    if unit['kind'] == 'web':
        # IFC units describe the tangent-to-tangent straight span. Rounded
        # junctions and crossed bars at its ends must not tilt/bow this body.
        shell &= (along >= .12*length) & (along <= .88*length)
    selected = candidate[shell]
    initial_centerline = np.array([start + station*direction + offset[0]*u + offset[1]*v
                                   for station, offset in centers]) if len(centers) >= 4 and unit["kind"] == "straight" else None
    axis, reason = fit_prior_axis(selected, axis_start, direction, length, radius,
                                  initial_centerline=initial_centerline,
                                  straight=unit["kind"] in ("short", "web"),
                                  guard_bending=unit['kind'] == 'straight')
    if axis is None:
        return None, reason, candidate_ids
    # A narrow flat fixture tangent to the design cylinder can have tiny radial
    # residuals. Unlike the later classified-instance fitter, this early stage
    # must establish curved cross-section evidence before assigning steel IDs.
    transverse = axis['xy'] - axis['spline'](axis['along']/length) @ axis['coeff']
    transverse = transverse[axis['inliers']]
    centered = transverse - np.mean(transverse, axis=0)
    if np.linalg.eigvalsh(centered.T @ centered / len(centered))[0] < max(1e-12, .0001*radius*radius):
        return None, 'ambiguous-planar-support', candidate_ids
    if normals is not None:
        nxy = np.column_stack((normals[candidate_ids[shell]] @ axis['u'],
                               normals[candidate_ids[shell]] @ axis['v']))
        nlength = np.linalg.norm(nxy, axis=1)
        valid_normals = np.isfinite(nxy).all(axis=1) & (nlength > .5)
        if np.count_nonzero(valid_normals) >= 24:
            directions = nxy[valid_normals] / nlength[valid_normals, None]
            if np.linalg.eigvalsh(directions.T @ directions / len(directions))[0] < .005:
                return None, 'ambiguous-planar-normals', candidate_ids
    fit_along = axis["along"][axis["inliers"]]
    if len(fit_along) < 24:
        return None, "insufficient-cylinder-inliers", candidate_ids
    low, high = np.quantile(fit_along, [.01, .99])
    observed_length = float(high-low)
    required_span = .42*length if unit["kind"] == "short" else .24*length
    if observed_length < max(8*radius, required_span):
        return None, "insufficient-axial-coverage", candidate_ids
    if unit["kind"] == "short" and abs(.5*(low+high)-.5*length) > max(.12*length, 3*radius):
        return None, "short-interval-ambiguous", candidate_ids
    stations = np.linspace(0, 1, 2 if unit["kind"] in ("short", "web") else 17)
    offset = axis["spline"](stations)@axis["coeff"]
    curve = (axis["start"] + stations[:, None]*length*axis["tangent"]
             + offset[:, :1]*axis["u"] + offset[:, 1:]*axis["v"])
    curve = _exact_length(curve, length)
    radial = _polyline_distances(candidate, curve)
    residual = np.abs(radial-radius)
    tolerance = max(.0009, .36*radius)
    support = residual <= tolerance
    return {"curve": curve, "candidateIds": candidate_ids, "radial": radial, "residual": residual,
            "support": support, "tolerance": tolerance, "rmse": axis["fitRmseM"],
            "observedLength": observed_length, "directionDifferenceDeg": angle,
            "initialScore": score, "initialSpan": initial_span,
            "axisModel": "fixed-length-straight-cylinder" if unit["kind"] in ("short", "web") else axis['shapeModel'],
            'axisEvidenceRangeM': axis['supportedRange']}, reason, candidate_ids


def _transformed_inventory(inventory, rotation, translation):
    result = deepcopy(inventory or {})
    for unit in result.get("units", []):
        for field in ("startM", "endM"):
            if field in unit:
                unit[field] = (rotation@np.asarray(unit[field], float)+translation).tolist()
        if "direction" in unit:
            unit["direction"] = (rotation@np.asarray(unit["direction"], float)).tolist()
    for bar in result.get("bars", []):
        if bar.get("curvePrimitives"):
            from algorithms.rebar_design_curves import transform_primitives
            bar["curvePrimitives"] = transform_primitives(bar["curvePrimitives"], rotation, translation)
        if bar.get("points"):
            xyz = np.asarray(bar["points"], float)
            bar["points"] = (xyz@rotation.T+translation).tolist()
        if bar.get("boundsM"):
            lo, hi = np.asarray(bar["boundsM"], float)
            corners = np.array(np.meshgrid(*zip(lo, hi))).T.reshape(-1, 3)
            corners = corners@rotation.T+translation
            bar["boundsM"] = [corners.min(axis=0).tolist(), corners.max(axis=0).tolist()]
    return result


def _recover_short(points, normals, ids, unit):
    """Search unclaimed scan lines; design position is a bounded search prior.

    A finite observed interval must agree with the design length before it can
    move the template. A slice cut from a longer tube is not short-bar evidence.
    """
    length, radius = unit['length'], unit['radius']
    midpoint = .5*(unit['start']+unit['end'])
    cloud = points[ids]
    features = _auto_line_features(cloud, normals[ids] if normals is not None else None, radius)
    hypotheses = []
    for feature in features:
        direction = feature['direction'].copy()
        if direction@unit['direction'] < 0:
            direction *= -1
        angle = math.degrees(math.acos(np.clip(direction@unit['direction'], -1., 1.)))
        if angle > 65 or not .65*length <= feature['length'] <= 1.5*length:
            continue
        start = feature['center']-.5*length*direction
        delta = cloud-start
        along = delta@direction
        near = np.linalg.norm(delta-along[:, None]*direction, axis=1) < max(.012, 3*radius)
        model, _, _ = _fit_unit(points, normals, ids[near], unit, start, direction)
        if model is None:
            continue
        curve = model['curve']
        tangent = (curve[-1]-curve[0])/length
        delta = cloud-curve[0]
        station = delta@tangent
        radial = np.linalg.norm(delta-station[:, None]*tangent, axis=1)
        shell = np.abs(radial-radius) <= model['tolerance']
        if normals is not None:
            normal = normals[ids]
            transverse = delta-station[:, None]*tangent
            product = np.linalg.norm(normal, axis=1)*radial
            shell &= (product < 1e-10) | (np.abs(np.einsum('ij,ij->i', normal, transverse)) >= .8*product)
        if shell.sum() < 32:
            continue
        # Sparse radial-coincident speckles cannot extend an otherwise dense
        # observed interval. Crossings must also agree with cylinder normals.
        shell_station = station[shell]
        cells = np.floor((shell_station-shell_station.min())/.01).astype(int)
        density = np.bincount(cells)
        supported_cells = density >= max(6, .15*np.median(density[density > 0]))
        sorted_stations = np.sort(shell_station[supported_cells[cells]])
        if len(sorted_stations) < 32:
            continue
        groups = np.split(sorted_stations, np.flatnonzero(np.diff(sorted_stations) > max(.018, 4*radius))+1)
        groups = [g for g in groups if len(g) >= 32 and g[0] < .6*length and g[-1] > .4*length]
        if len(groups) != 1:
            continue
        low, high = np.quantile(groups[0], [.01, .99])
        span = float(high-low)
        if not .70*length <= span <= 1.50*length:
            continue
        # An interval touching the search boundary may be a clipped long bar.
        ends = curve[0]+np.asarray([low, high])[:, None]*tangent
        if np.max(np.abs((ends-midpoint)@unit['direction'])) >= .5*length+.16:
            continue
        # Solve again around the observed endpoints, never the old axial slot.
        # A pose search is not a length measurement. Keep the design template
        # rigid and report a discrepant visible span for review separately.
        fitted_length = length
        shifted_start = curve[0]+(.5*(low+high)-.5*fitted_length)*tangent
        observed_unit = dict(unit, length=fitted_length)
        model, _, _ = _fit_unit(points, normals, ids[near], observed_unit, shifted_start, tangent)
        if model is None:
            continue
        displacement = .5*(model['curve'][0]+model['curve'][-1])-midpoint
        if np.linalg.norm(displacement) > .50:
            continue
        score = min(span/length, 1.) * math.exp(-model['rmse']/model['tolerance'])
        model.update(recoveryScore=score, displacementM=displacement.tolist(),
                     fittedLengthM=fitted_length, observedLength=span,
                     lengthCheck='review-observed-span' if abs(span-length) > max(.01, .10*length) else 'consistent-visible-span',
                     directionDifferenceDeg=float(math.degrees(math.acos(np.clip(
                         np.dot((model['curve'][-1]-model['curve'][0])/fitted_length, unit['direction']), -1., 1.)))))
        if any(np.linalg.norm(.5*(other['curve'][0]+other['curve'][-1])-
                              .5*(model['curve'][0]+model['curve'][-1])) < 2*radius for other in hypotheses):
            continue
        hypotheses.append(model)
    hypotheses.sort(key=lambda m: -m['recoveryScore'])
    if not hypotheses:
        return None, 'no-unclaimed-short-support'
    if len(hypotheses) > 1 and hypotheses[1]['recoveryScore'] >= .9*hypotheses[0]['recoveryScore']:
        candidate = dict(hypotheses[0])
        candidate['recoveryAlternatives'] = hypotheses
        return candidate, 'ambiguous-short-recovery'
    return hypotheses[0], 'relocated-short-supported'


def _resolve_short_recovery(points, normals, tree, source_ids, owner, unit, candidate):
    """Recheck competing sampled poses against complete local finite surfaces."""
    alternatives = candidate.get('recoveryAlternatives', [])
    if not 2 <= len(alternatives) <= 4:
        return candidate, 'ambiguous-short-recovery'
    valid = []
    for model in alternatives:
        local = dict(model, unit=unit)
        interval = _short_surface_interval(points, normals, tree, source_ids, owner, local)
        if interval is not None:
            valid.append(model)
    if not valid or (len(valid) > 1 and valid[1]['recoveryScore'] >= .9*valid[0]['recoveryScore']):
        return candidate, 'ambiguous-short-recovery'
    result = dict(valid[0])
    result['recoveryEvidence'] = {'method': 'full-source-finite-interval',
                                'sampledAlternatives': len(alternatives), 'validAlternatives': len(valid)}
    return result, 'relocated-short-supported'


def _semantic_layer(unit, layering):
    """Map a design unit to the measured bottom/top/web fitting partition."""
    if unit["kind"] == "web":
        return 3
    z = float((unit["start"][2] + unit["end"][2]) / 2)
    horizontal = []
    for item in (layering or {}).get("layers", []):
        try:
            layer_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if layer_id not in (1, 2):
            continue
        low, high = item.get("lowM"), item.get("highM")
        height = item.get("heightM")
        if height is None and low is not None and high is not None:
            height = .5 * (float(low) + float(high))
        if height is None:
            continue
        contained = low is not None and high is not None and float(low) <= z <= float(high)
        horizontal.append((not contained, abs(z - float(height)), layer_id))
    if horizontal:
        return min(horizontal)[2]
    # This fallback is only report/scoping metadata for legacy calls without
    # measured layers. Design layer IDs are intentionally kept separate.
    return 1


def _layer_names(layering):
    names = {1: "底层钢筋", 2: "顶层钢筋", 3: "腹杆层"}
    for item in (layering or {}).get("layers", []):
        try:
            layer_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if layer_id in names and item.get("name"):
            names[layer_id] = str(item["name"])
    return names


def _merge_ids(*groups, limit=None):
    arrays = [np.asarray(group, dtype=np.int64) for group in groups if len(group)]
    if not arrays:
        return np.empty(0, dtype=np.int64)
    result = np.unique(np.concatenate(arrays))
    if limit is not None and len(result) > limit:
        result = result[np.linspace(0, len(result) - 1, limit, dtype=int)]
    return result


def _ambiguous_body_models(models):
    """Find duplicate supported tubes independently of stage priority."""
    ambiguous_models = set()
    bounds_low=np.array([model['curve'].min(axis=0) for model in models]).reshape(-1,3)
    bounds_high=np.array([model['curve'].max(axis=0) for model in models]).reshape(-1,3)
    radii=np.array([model['unit']['radius'] for model in models])
    for i, left in enumerate(models):
        # Box separation is a lower bound on every curve-to-curve distance,
        # in either direction. Distant pairs cannot be duplicates; avoid the
        # expensive polyline calculation while preserving the exact close test.
        gap=np.maximum(0.,np.maximum(bounds_low[i]-bounds_high[i+1:],bounds_low[i+1:]-bounds_high[i]))
        limit=1.4*np.maximum(radii[i],radii[i+1:])+1e-12
        near=np.flatnonzero(np.einsum('ij,ij->i',gap,gap)<limit*limit)+i+1
        for j in near:
            right=models[j]
            distance = float(np.mean(_polyline_distances(left["curve"], right["curve"])))
            if abs(np.dot(left["unit"]["direction"], right["unit"]["direction"])) > .98:
                # A long member's overhang must not hide duplication throughout
                # the shorter member's shared span. Check both directions.
                distance = min(distance, float(np.mean(_polyline_distances(right["curve"], left["curve"]))))
            if distance < 1.4*max(left["unit"]["radius"], right["unit"]["radius"]):
                ambiguous_models.update((left["number"], right["number"]))
    return ambiguous_models


def _endpoint_candidates(tree, source_ids, curve, radius):
    if tree is None or not len(source_ids):
        return np.empty(0, dtype=np.int64)
    local = tree.query_ball_point(np.asarray([curve[0], curve[-1]]), r=radius)
    return source_ids[_merge_ids(*local)] if len(local) else np.empty(0, dtype=np.int64)


def _surface_review(points, normals, curve, radius):
    """Axially balanced raw-surface check, independent of circle-vote inliers."""
    tangent = curve[-1]-curve[0]
    length = np.linalg.norm(tangent)
    tangent /= max(length, 1e-12)
    along = (points-curve[0])@tangent
    keep = (along > .01) & (along < length-.01)
    if normals is not None:
        norm = np.linalg.norm(normals, axis=1)
        keep &= (norm > .5) & (np.abs(normals@tangent) < .5*norm)
    residual = np.abs(_polyline_distances(points, curve)-radius)
    bins = np.clip((along/max(length, 1e-12)*24).astype(int), 0, 23)
    medians = np.full(24, np.nan)
    support = np.full(24, np.nan)
    tolerance = max(.0009, .36*radius)
    for band in range(24):
        selected = keep & (bins == band)
        if selected.sum() >= 24:
            medians[band] = np.median(residual[selected])
            support[band] = np.mean(residual[selected] <= tolerance)
    return medians, support


def _surface_improvement(held_points, held_normals, old_curve, new_curve, radius):
    """Score fixed raw records, never the candidate fit's selected inliers."""
    before, old_support = _surface_review(held_points, held_normals, old_curve, radius)
    after, new_support = _surface_review(held_points, held_normals, new_curve, radius)
    valid = np.isfinite(before) & np.isfinite(after)
    improved = valid & (before-after > .0002) & (new_support-old_support > .15)
    regressed = valid & (new_support < old_support-.15) & (after > before+.0003)
    if (valid.sum() < 6 or improved.sum() < 3 or regressed.sum() > max(1, .1*valid.sum())
            or np.mean(new_support[valid]-old_support[valid]) < .08):
        return None
    return {'validationPoints': len(held_points), 'validatedBands': int(valid.sum()),
        'improvedBands': int(improved.sum()), 'regressedBands': int(regressed.sum()),
        'supportBefore': float(np.mean(old_support[valid])),
        'supportAfter': float(np.mean(new_support[valid]))}


def _review_body_worker(points, normals, unit, auto, held_points, held_normals, old_curve):
    """Richer sampling must improve unseen surface bands, not just fit RMSE."""
    model, reason, _ = _fit_unit_worker(points, normals, unit, auto)
    if model is None:
        return None
    review = _surface_improvement(held_points, held_normals, old_curve, model['curve'], unit['radius'])
    if review is None:
        return None
    model['surfaceReview'] = dict(review, method='held-out-raw-surface-resampling')
    return model


def _review_long_body_worker(points, unit, held_points, held_normals, old_curve):
    """Try bounded axis capacity only after the raw long-body surface failed review.

    Raw training points restore the outside of a bow discarded by station votes.
    Independent records decide acceptance; a higher coefficient count alone is
    never evidence for replacing a supported axis. Prefer the simplest repair.
    """
    radius, length = unit['radius'], unit['length']
    direction = old_curve[-1]-old_curve[0]
    direction /= np.linalg.norm(direction)
    for coefficients in (6, 10, 14):
        axis, _ = fit_prior_axis(points, old_curve[0], direction, length, radius,
            initial_centerline=old_curve, guard_bending=True, spline_coefficients=coefficients)
        if axis is None:
            continue
        stations = np.linspace(0., 1., 17)
        offset = axis['spline'](stations)@axis['coeff']
        curve = _exact_length(axis['start'] + stations[:, None]*length*axis['tangent']
            + offset[:, :1]*axis['u'] + offset[:, 1:]*axis['v'], length)
        # Repair a local surface, not a different parallel bar or a remote tail.
        displacement = float(np.max(np.linalg.norm(curve-old_curve, axis=1)))
        if displacement > max(.008, 2*radius):
            continue
        review = _surface_improvement(held_points, held_normals, old_curve, curve, radius)
        if review is None:
            continue
        return {'curve': curve, 'rmse': axis['fitRmseM'], 'axisModel': axis['shapeModel'],
            'axisEvidenceRangeM': axis['supportedRange'],
            'observedLength': float(np.diff(np.quantile(axis['along'][axis['inliers']], [.01, .99]))[0]),
            'surfaceReview': dict(review, method='held-out-long-body-axis',
                splineCoefficients=coefficients, trainingPoints=len(points), maxDisplacementM=displacement)}
    return None


def _refine_body_samples(points, normals, scoped, owner, models, auto, executor=None, fallback_scope=None):
    jobs = []
    for model in models:
        unit = model['unit']
        if unit['kind'] != 'straight':
            continue
        long_body = unit['length'] > 2
        if not long_body and model.get('candidateSampling'):
            continue
        full = model['candidateIds']
        near = model['radial'] < (unit['radius']+.006 if long_body else max(.014, 3.5*unit['radius']))
        review = full[near]
        if len(review) > 8000:
            review = review[np.linspace(0, len(review)-1, 8000, dtype=int)]
        medians, support = _surface_review(points[review], None if normals is None else normals[review],
                                          model['curve'], unit['radius'])
        bad = (medians > model['tolerance']) & (support < .65)
        if bad.sum() < 3:
            continue
        if long_body and not np.any(bad[:-2] & bad[1:-1] & bad[2:]):
            continue
        tree, source_ids = scoped[unit['fitLayerId']]
        if fallback_scope is not None and (tree is None or model['fitScope'] == 'bounded-geometric-retry'):
            tree, source_ids = fallback_scope
        if long_body:
            if normals is None:
                continue
            raw = full[near & (owner[full] == 0)]
            tangent = model['curve'][-1]-model['curve'][0]
            span = np.linalg.norm(tangent)
            tangent /= span
            along = (points[raw]-model['curve'][0])@tangent
            norms = np.linalg.norm(normals[raw], axis=1)
            raw = raw[(along > .01) & (along < span-.01) & (norms > .5)
                      & (np.abs(normals[raw]@tangent) < .5*norms)]
            # Source-order split is deterministic and disjoint even after caps.
            held, training = raw[::5], raw[np.arange(len(raw))%5 != 0]
            if min(len(held), len(training)) < 192:
                continue
            held = _merge_ids(held, limit=8000)
            training = _merge_ids(training, limit=12000)
            args = (points[training], unit, points[held], normals[held], model['curve'])
            task = executor.submit(_review_long_body_worker, *args) if executor is not None else _review_long_body_worker(*args)
            jobs.append((model, task, len(training), tree, source_ids))
            continue
        gate = max(.05, min(.10, .22*unit['length']), 9*unit['radius'])
        richer = _candidate_indices(tree, source_ids, unit['start'], unit['end'], gate, neighbours=768)
        richer = richer[owner[richer] == 0]
        # These raw records never enter the new candidate fit or its shell selection.
        held = np.setdiff1d(review, richer, assume_unique=True)
        if len(held) < 192:
            continue
        args = (points[richer], None if normals is None else normals[richer], unit, auto,
                points[held], None if normals is None else normals[held], model['curve'])
        task = executor.submit(_review_body_worker, *args) if executor is not None else _review_body_worker(*args)
        jobs.append((model, task, len(richer), tree, source_ids))
    for model, task, count, tree, source_ids in jobs:
        result = task.result() if executor is not None else task
        if result is None:
            continue
        unit = model['unit']
        full = _full_model_candidates(tree, source_ids, result['curve'], max(.02, 4*unit['radius']))
        full = full[owner[full] == 0]
        radial = _polyline_distances(points[full], result['curve'])
        residual = np.abs(radial-unit['radius'])
        model.update(result)
        model.update(candidateIds=full, radial=radial, residual=residual, support=residual <= model['tolerance'])
        if model['surfaceReview']['method'] == 'held-out-raw-surface-resampling':
            model['surfaceReview'].update(retryNeighbours=768, retryPoints=count)


def _fit_unit_worker(points, normals, unit, auto):
    """Fit a bounded local sample; source ids and final ownership stay in parent."""
    return _fit_unit(points, normals, np.arange(len(points)), unit,
                     unit["start"], unit["direction"], auto)


def _recover_short_worker(points, normals, unit):
    return _recover_short(points, normals, np.arange(len(points)), unit)


def _fit_worker_init():
    # Each process owns independent fits; nested BLAS pools oversubscribe CPUs.
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)


def fit_control_net(points, table_mask, inventory, *, mode="aligned", normals=None,
                    progress=None, fused_classes=None, layer_ids=None, layering=None,
                    workers=1, curve_workers=1):
    """Fit physical design-unit control lines on post-table or fused steel input.

    Returns ``(JSON report, typed full-source attributes)``. Source coordinates
    and ordering are never modified.
    """
    started = time.perf_counter()
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("points must have shape (N, 3)")
    table_mask = np.asarray(table_mask, dtype=bool)
    if table_mask.shape != (len(points),):
        raise ValueError("table_mask must have shape (N,)")
    if mode not in ("aligned", "auto"):
        raise ValueError("mode must be aligned or auto")
    if normals is not None:
        normals = np.asarray(normals, dtype=float)
        if normals.shape != points.shape:
            raise ValueError("normals must have shape (N, 3)")
    if isinstance(workers, bool) or not isinstance(workers, (int, np.integer)) or workers < 1:
        raise ValueError("workers must be a positive integer")
    post_fusion = fused_classes is not None
    # The early post-table pass has no measured semantic layers. Keep that
    # absence explicit: design provenance must not invent a fitting partition
    # or a semantic execution order before the layer classifier has run.
    layer_independent = not post_fusion and layer_ids is None and layering is None
    if post_fusion:
        fused_classes = np.asarray(fused_classes)
        if fused_classes.shape != (len(points),):
            raise ValueError("fused_classes must have shape (N,)")
    if layer_ids is not None:
        layer_ids = np.asarray(layer_ids)
        if layer_ids.shape != (len(points),):
            raise ValueError("layer_ids must have shape (N,)")
        if not np.issubdtype(layer_ids.dtype, np.integer):
            raise ValueError("layer_ids must be an integer array")
    units = _unit_rows(inventory)
    candidate_class = ~table_mask if not post_fusion else (~table_mask & (fused_classes == 3))
    usable = candidate_class & np.isfinite(points).all(axis=1)
    source_ids = np.flatnonzero(usable)
    source_points = points[source_ids]
    source_normals = normals[source_ids] if normals is not None else None
    _progress(progress, "控制网：全局几何定位", 0, max(1, len(units)))
    if mode == "auto":
        bounded_ids = np.linspace(0, len(source_points)-1, min(len(source_points), 120_000), dtype=int) if len(source_points) else np.empty(0, int)
        rotation, translation, registration = _auto_registration(
            source_points[bounded_ids], source_normals[bounded_ids] if source_normals is not None else None, units)
        # Map feature-local normal indices consistently by fitting on the bounded
        # set only above; all unit evidence below still uses the complete source.
    else:
        rotation, translation = np.eye(3), np.zeros(3)
        registration = {"method": "saved-coarse-alignment-v1", "status": "provided",
                        "rotation": rotation.tolist(), "translationM": translation.tolist()}
    transformed = []
    for unit in units:
        item = dict(unit)
        item["start"] = rotation@unit["start"]+translation
        item["end"] = rotation@unit["end"]+translation
        item["direction"] = rotation@unit["direction"]
        item["fitLayerId"] = 0 if layer_independent else _semantic_layer(item, layering)
        transformed.append(item)

    # Pending is the conservative candidate-steel default. Post-fusion rows
    # outside class 3 remain explicitly excluded and are never fit or assigned.
    status = np.full(len(points), 2, dtype=np.uint8)
    if post_fusion:
        status[~table_mask & (fused_classes != 3)] = 4
    status[table_mask] = 0
    owner = np.zeros(len(points), dtype=np.uint32)
    pending = np.zeros(len(points), dtype=bool)
    models = []
    tree = cKDTree(source_points) if len(source_points) else None
    # Layer 0 is deliberately included in every semantic scope: it is the
    # geometric fallback for steel points that layering could not label.
    scoped = {}
    if layer_independent:
        scoped[0] = (tree, source_ids)
    elif layer_ids is not None:
        for layer_id in (1, 2, 3):
            ids = np.flatnonzero(usable & ((layer_ids == layer_id) | (layer_ids == 0)))
            scoped[layer_id] = (cKDTree(points[ids]) if len(ids) else None, ids)
    else:
        scoped = {layer_id: (tree, source_ids) for layer_id in (1, 2, 3)}
    instance_rows = []
    for number, unit in enumerate(transformed, 1):
        source = unit["source"]
        instance_rows.append({"id": number, "designUnitId": source.get("designUnitId"),
            "designBarId": source.get("designBarId"), "kind": unit["kind"],
            "status": "missing", "reason": None,
            "pointCount": 0, "diameterM": 2*unit["radius"],
            "designLengthM": unit["length"], "observedLengthM": None,
            "rmseM": None, "centerlineM": [],
            "layerId": source.get("layerId"), "fitLayerId": unit["fitLayerId"]})

    scope_elapsed = {0: 0., 1: 0., 2: 0., 3: 0.}

    def initial_candidates(task):
        number, unit = task
        layer_tree, layer_source_ids = scoped[unit["fitLayerId"]]
        gate = (max(.075, min(.15, .75*unit["length"]), 9*unit["radius"])
                if unit["kind"] in ("short", "web")
                else max(.05, min(.10, .22*unit["length"]), 9*unit["radius"]))
        neighbours = 768 if unit["kind"] == "straight" and unit["length"] > 2 else _NEIGHBOURS_PER_QUERY
        ids = (_candidate_indices(layer_tree, layer_source_ids, unit["start"], unit["end"], gate,
                    neighbours=neighbours)
               if layer_tree is not None else np.empty(0, int))
        if unit["kind"] == "web":
            # Web cylinders touch both horizontal layers. Admit a bounded halo
            # only at their endpoints rather than widening the whole web scope.
            halo = _endpoint_candidates(tree, source_ids,
                                        np.asarray([unit["start"], unit["end"]]),
                                        max(.025, 6*unit["radius"]))
            ids = _merge_ids(ids, halo, limit=_MAX_UNIT_CANDIDATES)
        available = ids[owner[ids] == 0]
        if len(available) < min(32, .4*len(ids)) and neighbours < 768 and layer_tree is not None:
            richer = _candidate_indices(layer_tree, layer_source_ids, unit["start"], unit["end"], gate,
                                        neighbours=768)
            available = _merge_ids(available, richer[owner[richer] == 0], limit=_MAX_UNIT_CANDIDATES)
            neighbours = 768
        return gate, neighbours, available

    def fit_one(task, prepared=None):
        number, unit = task
        layer_tree, layer_source_ids = scoped[unit["fitLayerId"]]
        if prepared is None:
            gate, neighbours, ids = initial_candidates(task)
            model, reason, considered = _fit_unit(points, normals, ids, unit,
                                                   unit["start"], unit["direction"], mode == "auto")
        else:
            gate, neighbours, ids, result = prepared
            model, reason, local_considered = result
            considered = ids[local_considered]
        # A dense, offset partial arc can fill every nearest-neighbour slot on
        # its near side. Two projected circle votes then look like parallel bars
        # before the continuous-axis solver ever sees the full cross-section.
        # Retry that rejection once with richer evidence in the same candidate
        # scope and gate. Keep all geometric/ambiguity checks and the 30k cap.
        if (model is None and reason == "ambiguous-parallel-support"
                and unit["kind"] == "straight" and neighbours < 768 and layer_tree is not None):
            expanded = _candidate_indices(layer_tree, layer_source_ids, unit["start"], unit["end"], gate,
                                          neighbours=768)
            expanded = expanded[owner[expanded] == 0]
            if not np.array_equal(expanded, ids):
                retry_model, retry_reason, retry_considered = _fit_unit(
                    points, normals, expanded, unit, unit["start"], unit["direction"], mode == "auto")
                if retry_model is not None:
                    sampling = {
                        'reason': reason, 'initialNeighbours': neighbours, 'retryNeighbours': 768,
                        'initialPoints': len(ids), 'retryPoints': len(expanded)}
                    sampling['sameCandidateScopeAndGate' if layer_independent else 'sameLayerAndGate'] = True
                    retry_model['candidateSampling'] = sampling
                    model, reason, considered = retry_model, retry_reason, retry_considered
        fit_scope = "bounded-geometry" if layer_independent else "semantic-layer"
        assignment_tree, assignment_ids = layer_tree, layer_source_ids
        # A shared-layer label is geometric guidance, not proof that every arc
        # of one tube received that label. Retry only unresolved units, within
        # the original bounded design-line gate, against all fused steel. The
        # same radius, planar and ambiguity guards still decide acceptance.
        if model is None and layer_ids is not None and tree is not None:
            retry_ids = _candidate_indices(tree, source_ids, unit["start"], unit["end"], gate,
                    neighbours=768 if unit["kind"] == "straight" and unit["length"] > 2 else None)
            retry_ids = retry_ids[owner[retry_ids] == 0]
            if not np.array_equal(retry_ids, ids):
                retry_model, retry_reason, retry_considered = _fit_unit(
                    points, normals, retry_ids, unit, unit["start"], unit["direction"], mode == "auto")
                if retry_model is not None:
                    model, reason, considered = retry_model, retry_reason, retry_considered
                    fit_scope = "bounded-geometric-retry"
                    assignment_tree, assignment_ids = tree, source_ids
        if model is not None:
            # Sampling establishes the model only. Classify every nearby raw
            # eligible source record afterwards, independently of fitting cap.
            full_ids = _full_model_candidates(assignment_tree, assignment_ids, model['curve'],
                                               max(.02, 4*unit['radius']))
            if unit["kind"] == "web" and fit_scope == "semantic-layer":
                halo = _endpoint_candidates(tree, source_ids, model["curve"],
                                            max(.025, 6*unit["radius"]))
                full_ids = _merge_ids(full_ids, halo)
            full_ids = full_ids[owner[full_ids] == 0]
            radial = _polyline_distances(points[full_ids], model['curve'])
            residual = np.abs(radial-unit['radius'])
            model.update(number=number, unit=unit, reason=reason, candidateIds=full_ids,
                         radial=radial, residual=residual, support=residual <= model['tolerance'],
                         fitScope=fit_scope)
        return number, model, reason, considered, fit_scope

    done = 0
    ownership_stages = []
    # Phase priority is not proof of identity. Detect overlapping parallel
    # design slots across kinds before any stage can hide the shared evidence
    # from a later duplicate. Same-stage fitted duplicates are checked below.
    cross_stage_ambiguous = set()
    for i, left in enumerate(transformed):
        a = np.array([left['start'], left['end']])
        for j in range(i+1, len(transformed)):
            right = transformed[j]
            if (left['kind'] == right['kind']
                    or abs(left['direction']@right['direction']) < .98
                    or abs(left['radius']-right['radius']) > .36*min(left['radius'], right['radius'])):
                continue
            b = np.array([right['start'], right['end']])
            gap = np.maximum(0., np.maximum(a.min(axis=0)-b.max(axis=0), b.min(axis=0)-a.max(axis=0)))
            limit = 1.4*max(left['radius'], right['radius'])
            if gap@gap >= limit*limit:
                continue
            distance = min(float(np.mean(_polyline_distances(a, b))),
                           float(np.mean(_polyline_distances(b, a))))
            if distance < limit:
                cross_stage_ambiguous.update((i+1, j+1))
    global_ambiguous = mode == "auto" and (registration.get("status") != "supported"
                                          or registration.get("identityStatus") == "ambiguous")
    # Each stage publishes only uniquely supported surfaces. Later fitting,
    # recovery and assignment all read the same immutable owner mask. Source
    # coordinates are never deleted, and ambiguous points remain available.
    with ExitStack() as stack:
        pool_stack = stack.enter_context(ExitStack())
        executor = None
        for stage_kind, stage_label in (("straight", "长直筋"), ("short", "短筋"), ("web", "腹杆")):
            stage_tasks = [(number, unit) for number, unit in enumerate(transformed, 1)
                           if (unit["kind"] if unit["kind"] in ('short', 'web') else 'straight') == stage_kind]
            if not stage_tasks:
                continue
            stage_started = time.perf_counter()
            models = []
            fitting_scopes = ((0, stage_tasks),) if layer_independent else tuple(
                (layer_id, [(number, unit) for number, unit in stage_tasks
                            if unit["fitLayerId"] == layer_id])
                for layer_id in (1, 2, 3))
            progress_label = f"控制网：{stage_label} · 确认后锁定归属"
            for scope_id, tasks in fitting_scopes:
                scope_started = time.perf_counter()
                def results_in_order():
                    nonlocal executor
                    if workers <= 1 or len(tasks) <= 1:
                        yield from map(fit_one, tasks)
                        return
                    # Send at most 30k candidate points per fit, never the full scan or
                    # its trees. Bounded batches prevent queued copies scaling with N.
                    count = min(8, int(workers), len(tasks))
                    if executor is None:
                        executor = pool_stack.enter_context(ProcessPoolExecutor(max_workers=min(8, int(workers)),
                            mp_context=multiprocessing.get_context("spawn"), initializer=_fit_worker_init))
                    if executor is not None:
                        for offset in range(0, len(tasks), 4*count):
                            batch = []
                            for task in tasks[offset:offset+4*count]:
                                gate, neighbours, ids = initial_candidates(task)
                                future = executor.submit(_fit_unit_worker, points[ids],
                                    normals[ids] if normals is not None else None, task[1], mode == "auto")
                                batch.append((task, gate, neighbours, ids, future))
                            for task, gate, neighbours, ids, future in batch:
                                yield fit_one(task, (gate, neighbours, ids, future.result()))

                for number, model, reason, considered, fit_scope in results_in_order():
                    unresolved = reason is not None and "ambiguous" in reason
                    instance_rows[number-1].update(
                        status="pending" if unresolved else "missing", reason=reason,
                        fitCandidateScope=fit_scope)
                    if model is not None:
                        models.append(model)
                    elif len(considered) and unresolved:
                        pending[considered] = True
                    done += 1
                    _progress(progress, progress_label, done, max(1, len(units)))
                scope_elapsed[scope_id] += float(time.perf_counter() - scope_started)

            # Recover only unresolved short bars, after known surfaces have been found.
            # All candidates use eligible source geometry, but none can borrow a
            # confirmed tube.
            claimed = owner > 0
            if stage_kind == 'short':
                # Only uniquely supported, nonduplicate provisional short models
                # may constrain pose recovery. Ambiguous hypotheses cannot hide
                # unclaimed evidence before the final stage competition.
                duplicates = _ambiguous_body_models(models) | cross_stage_ambiguous
                pool = _merge_ids(*(m['candidateIds'] for m in models if m['number'] not in duplicates))
                best = np.full(len(pool), np.inf)
                second = best.copy()
                winner = np.zeros(len(pool), np.uint32)
                for model in models:
                    if model['number'] in duplicates:
                        continue
                    at = np.searchsorted(pool, model['candidateIds'])
                    score = model['residual']/model['tolerance']
                    improve = score < best[at]
                    second[at] = np.where(improve, best[at], np.minimum(second[at], score))
                    best[at[improve]] = score[improve]
                    winner[at[improve]] = model['number']
                unique = (best <= 1.) & (second > best+.18)
                counts = np.bincount(winner[unique], minlength=len(units)+1)
                claimed[pool[unique & (counts[winner] >= 24)]] = True
            modeled = {model['number'] for model in models}
            recovery_tasks = [(number, unit) for number, unit in stage_tasks
                              if unit['kind'] == 'short' and number not in modeled and tree is not None]
            def recovery_results():
                # Independent pose searches share the same frozen evidence. Reuse
                # the body pool and consume bounded batches in design order.
                batch_size = max(1, 4*min(8, int(workers)))
                for offset in range(0, len(recovery_tasks), batch_size):
                    batch = []
                    for number, unit in recovery_tasks[offset:offset+batch_size]:
                        midpoint = .5*(unit['start']+unit['end'])
                        axial_gate = .5*unit['length']+.18
                        ids = source_ids[tree.query_ball_point(midpoint, r=math.hypot(.5, axial_gate))]
                        delta = points[ids]-midpoint
                        along = delta@unit['direction']
                        keep = (~claimed[ids] & (np.abs(along) < axial_gate)
                                & (np.linalg.norm(delta-along[:, None]*unit['direction'], axis=1) < .5))
                        ids = ids[keep]
                        # Stable source-order sampling is only for solving, never assignment.
                        ids = _merge_ids(ids, limit=_MAX_UNIT_CANDIDATES)
                        if executor is not None and len(recovery_tasks) > 1:
                            result = executor.submit(_recover_short_worker, points[ids],
                                None if normals is None else normals[ids], unit)
                        else:
                            result = _recover_short(points, normals, ids, unit)
                        batch.append((number, unit, result))
                    for number, unit, result in batch:
                        yield number, unit, result.result() if hasattr(result, 'result') else result
            recovery_started = time.perf_counter()
            for number, unit, (model, reason) in recovery_results():
                if model is not None and reason == 'ambiguous-short-recovery':
                    model, reason = _resolve_short_recovery(points, normals, tree, source_ids, claimed, unit, model)
                row = instance_rows[number-1]
                row.update(recoveryReason=reason, recoverySearchRadiusM=.5)
                if model is not None and reason == 'ambiguous-short-recovery':
                    row.update(status='pending', reason=reason, candidateCenterlineM=model['curve'].tolist(),
                               candidateRmseM=model['rmse'], fittedLengthM=model['fittedLengthM'])
                elif model is not None:
                    row.update(fittedLengthM=model['fittedLengthM'], fitCandidateScope='unclaimed-short-pose-search')
                    full_ids = _full_model_candidates(tree, source_ids, model['curve'], max(.02, 4*unit['radius']))
                    full_ids = full_ids[owner[full_ids] == 0]
                    radial = _polyline_distances(points[full_ids], model['curve'])
                    residual = np.abs(radial-unit['radius'])
                    model.update(number=number, unit=unit, reason=reason, candidateIds=full_ids,
                                 radial=radial, residual=residual, support=residual <= model['tolerance'],
                                 fitScope='unclaimed-short-pose-search')
                    models.append(model)
                now = time.perf_counter()
                scope_elapsed[unit['fitLayerId']] += now-recovery_started
                recovery_started = now

            # A shorter parallel member supplies an independent observed surface in
            # the shared span. Refit the longer member without borrowing that surface.
            # This only changes fitting evidence; full-source ownership still competes
            # below, and excluded evidence is never deleted from the scan.
            for longer in sorted(models, key=lambda item: item["unit"]["length"]):
                unit = longer["unit"]
                if unit["kind"] != "straight":
                    continue
                references = []
                for shorter in models:
                    other = shorter["unit"]
                    if other["kind"] != "straight" or other["length"] >= .9*unit["length"]:
                        continue
                    if abs(np.dot(unit["direction"], other["direction"])) < .995:
                        continue
                    delta = .5*(other["start"]+other["end"])-unit["start"]
                    along = float(delta@unit["direction"])
                    separation = np.linalg.norm(delta-along*unit["direction"])
                    if separation > max(.04, 8*unit["radius"]) or not 0 < along < unit["length"]:
                        continue
                    ends = sorted(float((end-unit["start"])@unit["direction"])
                                  for end in (other["start"], other["end"]))
                    overlap = max(0., min(unit["length"], ends[1])-max(0., ends[0]))
                    if overlap < .75*other["length"]:
                        continue
                    references.append(shorter)
                if not references:
                    continue
                refit_started = time.perf_counter()
                layer_tree, layer_source_ids = scoped[unit["fitLayerId"]]
                if longer["fitScope"] == "bounded-geometric-retry" or layer_tree is None:
                    layer_tree, layer_source_ids = tree, source_ids
                ids = _candidate_indices(layer_tree, layer_source_ids, unit["start"], unit["end"], .1, neighbours=768)
                keep = owner[ids] == 0
                for reference in references:
                    radial = _polyline_distances(points[ids], reference["curve"])
                    keep &= radial > reference["unit"]["radius"] + reference["tolerance"]
                fitted, reason, _ = _fit_unit(points, normals, ids[keep], unit, unit["start"], unit["direction"], mode == "auto")
                if fitted is not None:
                    full_ids = _full_model_candidates(layer_tree, layer_source_ids, fitted["curve"], max(.02, 4*unit["radius"]))
                    full_ids = full_ids[owner[full_ids] == 0]
                    radial = _polyline_distances(points[full_ids], fitted["curve"])
                    residual = np.abs(radial-unit["radius"])
                    longer.update(fitted)
                    longer.update(candidateIds=full_ids, radial=radial, residual=residual,
                                  support=residual <= fitted["tolerance"], reason=reason,
                                  fitScope="parallel-surface-refit")
                scope_elapsed[unit["fitLayerId"]] += time.perf_counter()-refit_started

            _refine_body_samples(points, normals, scoped, owner, models, mode == 'auto', executor, (tree, source_ids))
            for model in models:
                if model['unit']['kind'] == 'straight' and tree is not None:
                    _refine_straight_ends(points, normals, tree, source_ids, owner, model)
                if model['unit']['kind'] == 'short' and tree is not None:
                    _refine_short_surface(points, normals, tree, source_ids, owner, model)
                    _extend_short(points, normals, tree, source_ids, owner, model)

            # Identical supported tubes from competing design units are globally
            # ambiguous. Suppress both rather than manufacturing duplicate steel.
            ambiguous_models = _ambiguous_body_models(models) | ({m['number'] for m in models} & cross_stage_ambiguous)
            global_ambiguous = mode == "auto" and (registration.get("status") != "supported"
                                                  or registration.get("identityStatus") == "ambiguous")
            if global_ambiguous:
                ambiguous_models.update(model["number"] for model in models)
            for model in models:
                if model["number"] in ambiguous_models:
                    ids = model["candidateIds"]
                    pending[ids[model["support"]]] = True
            # A fitted hypothesis is publishable only if it retains enough unique
            # support after competition. Re-run without weak hypotheses so they cannot
            # leave status-1 owners or justify status-3 rejection on their own.
            active = {model["number"] for model in models} - ambiguous_models
            insufficient_unique = set()
            competition_ids = _merge_ids(*(model["candidateIds"] for model in models))
            while True:
                best = np.full(len(competition_ids), np.inf)
                second = np.full(len(competition_ids), np.inf)
                best_owner = np.zeros(len(competition_ids), np.uint32)
                locally_rejected = np.zeros(len(competition_ids), dtype=bool)
                for model in models:
                    if model["number"] not in active:
                        continue
                    ids = model["candidateIds"]
                    local_ids = np.searchsorted(competition_ids, ids)
                    score = model["residual"] / model["tolerance"]
                    local_envelope = model["radial"] <= max(.02, 4*model["unit"]["radius"])
                    locally_rejected[local_ids[local_envelope & ~model["support"]]] = True
                    improve = score < best[local_ids]
                    second[local_ids] = np.where(improve, best[local_ids],
                                                 np.minimum(second[local_ids], score))
                    best_owner[local_ids[improve]] = model["number"]
                    best[local_ids[improve]] = score[improve]
                unique = (best <= 1.) & (second > best + .18)
                count_by_owner = np.bincount(best_owner[unique], minlength=len(units)+1)
                unique_counts = {number: int(count_by_owner[number]) for number in active}
                weak = {number for number, count in unique_counts.items() if count < 24}
                if not weak:
                    break
                active -= weak
                insufficient_unique.update(weak)
            supported = best <= 1.
            ambiguous = supported & (second <= best + .18)
            pending_local = pending[competition_ids] if len(competition_ids) else np.empty(0, bool)
            reject_ids = competition_ids[locally_rejected & ~supported & ~pending_local]
            if len(reject_ids) and tree is not None:
                support_radius = max(.0015, .6*min(u['radius'] for u in transformed))
                coherence_tree = tree
                if post_fusion:
                    # Fusion exclusion limits fitting, not the conservative coherence
                    # guard. A nearby excluded fixture/crossing still proves that an
                    # unmatched boundary candidate is not an isolated removable burr.
                    raw_usable = ~table_mask & np.isfinite(points).all(axis=1)
                    coherence_tree = cKDTree(points[raw_usable])
                for begin in range(0, len(reject_ids), 65_536):
                    ids = reject_ids[begin:begin+65_536]
                    support_count = coherence_tree.query_ball_point(
                        points[ids], r=support_radius, return_length=True)
                    # Dense/coherent unmatched surfaces may be an unmodeled crossing,
                    # a rotated hook or fixture. Residual alone cannot reject them.
                    dense_ids = ids[support_count >= 3]
                    locally_rejected[np.searchsorted(competition_ids, dense_ids)] = False
            status[competition_ids[locally_rejected]] = 3
            status[pending & usable & (owner == 0)] = 2
            status[competition_ids[supported]] = 1
            status[competition_ids[ambiguous]] = 2
            assigned = supported & ~ambiguous
            owner[competition_ids[assigned]] = best_owner[assigned]
            owner_counts = np.bincount(owner, minlength=len(units)+1)
            for model in models:
                row = instance_rows[model["number"]-1]
                count = int(owner_counts[model["number"]])
                if model["number"] in ambiguous_models or model["number"] in insufficient_unique:
                    row.update(status="pending", reason="ambiguous-design-support" if model["number"] in ambiguous_models else "insufficient-unique-support")
                    if global_ambiguous:
                        row.update(candidateCenterlineM=model['curve'].tolist(),
                                   candidatePointCount=int(np.count_nonzero(model['support'])),
                                   candidateRmseM=model['rmse'])
                    continue
                row.update(status="fitted", reason="fixed-radius-surface-supported", pointCount=count,
                           observedLengthM=model["observedLength"], rmseM=model["rmse"],
                           centerlineM=model["curve"].tolist(),
                           fitCandidateScope=model["fitScope"],
                           directionDifferenceDeg=model["directionDifferenceDeg"],
                           axisModel=model['axisModel'], axisEvidenceRangeM=model['axisEvidenceRangeM'])
                if model.get('lengthEvidence') is not None:
                    row.update(fittedLengthM=model['fittedLengthM'], lengthCheck=model['lengthCheck'],
                               lengthEvidence=model['lengthEvidence'])
                if model.get('recoveryEvidence') is not None:
                    row['recoveryEvidence'] = model['recoveryEvidence']
                if model.get('axisRefinement') is not None:
                    row['axisRefinement'] = model['axisRefinement']
                if model.get('webEvidence') is not None:
                    row['webEvidence'] = model['webEvidence']
                if model.get('candidateSampling') is not None:
                    row['candidateSampling'] = model['candidateSampling']
                if model.get('surfaceReview') is not None:
                    row['surfaceReview'] = model['surfaceReview']
                if model.get('displacementM') is not None:
                    row.update(displacementM=model['displacementM'], reason='relocated-short-supported',
                               fittedLengthM=model['fittedLengthM'], lengthCheck=model['lengthCheck'])
            ownership_stages.append({"kind": stage_kind, "designUnits": len(stage_tasks),
                "fittedUnits": sum(instance_rows[n-1]['status'] == 'fitted' for n, _ in stage_tasks),
                "lockedPoints": int(np.count_nonzero(assigned)),
                "elapsedS": float(time.perf_counter()-stage_started)})
        for number in cross_stage_ambiguous:
            instance_rows[number-1].update(status='pending', reason='ambiguous-design-support')
        # Curves attach only after body identity and unique support are established.
        # Their ownership is additive; accepted body points and nominal axes remain.
        from algorithms.rebar_control_curves import fit_curved_pieces
        registered_inventory = _transformed_inventory(inventory, rotation, translation)
        # A different curve budget needs a different pool. Reap idle body
        # workers first so pools never coexist beyond the global cap and the
        # reported curve concurrency still matches the configured budget.
        if executor is not None and min(8, int(workers)) != min(8, int(curve_workers)):
            pool_stack.close()
            executor = None
        curved_pieces, curve_summary = fit_curved_pieces(
            points, normals, registered_inventory, instance_rows, status, owner,
            tree=tree, source_ids=source_ids, progress=progress, workers=curve_workers,
            executor=executor)
    fitted = sum(row["status"] == "fitted" for row in instance_rows)
    physical_bars = (len((inventory or {}).get("bars", []))
                     if "bars" in (inventory or {})
                     else len({u["source"].get("designBarId") for u in units
                               if u["source"].get("designBarId") is not None}))
    counts = {"input": len(points), "table": int(np.count_nonzero(status == 0)),
              "matched": int(np.count_nonzero(status == 1)),
              "pending": int(np.count_nonzero(status == 2)),
              "removed": int(np.count_nonzero(status == 3)),
              "excluded": int(np.count_nonzero(status == 4)),
              "designUnits": len(units), "fittedUnits": fitted,
              "designBars": physical_bars}
    if layer_ids is not None:
        counts["fallbackLayerPoints"] = int(np.count_nonzero(usable & (layer_ids == 0)))
    counts['candidateFittedUnits'] = sum(bool(row.get('candidateCenterlineM')) for row in instance_rows)
    counts['extendedShortUnits'] = sum(row.get('lengthCheck') == 'extended-observed-span' for row in instance_rows)
    counts['surfaceRefinedUnits'] = sum('axisRefinement' in row for row in instance_rows)
    counts['surfaceResampledUnits'] = sum('surfaceReview' in row for row in instance_rows)
    counts['longBodyRefinedUnits'] = sum(row.get('surfaceReview', {}).get('method') == 'held-out-long-body-axis'
                                       for row in instance_rows)
    counts['lengthReviewUnits'] = sum(row.get('lengthCheck') == 'review-observed-span' for row in instance_rows)
    warnings = []
    if not units:
        warnings.append("design inventory contains no valid fitting units")
    if mode == "auto" and registration.get("status") != "supported":
        warnings.append("automatic global pose has insufficient geometric support")
    if global_ambiguous:
        warnings.append('competing or insufficient global pose evidence; candidate axes are not confirmed identities')
    if fitted < len(units):
        warnings.append(f"{len(units)-fitted} design units remain pending or missing")
    if counts['lengthReviewUnits']:
        warnings.append(f"{counts['lengthReviewUnits']} short bars have discrepant visible spans; design length retained, endpoints require review")
    layer_rows = []
    if not layer_independent:
        names = _layer_names(layering)
        for layer_id in (1, 2, 3):
            exact = usable if layer_ids is None else (usable & (layer_ids == layer_id))
            unit_rows = [row for row in instance_rows if row["fitLayerId"] == layer_id]
            layer_rows.append({"id": layer_id, "name": names[layer_id],
                "inputPoints": int(np.count_nonzero(exact)),
                "matched": int(np.count_nonzero(exact & (status == 1))),
                "pending": int(np.count_nonzero(exact & (status == 2))),
                "designUnits": len(unit_rows),
                "fittedUnits": sum(row["status"] == "fitted" for row in unit_rows),
                "elapsedS": scope_elapsed[layer_id]})
    policy = {"ownershipStages": ownership_stages,
              "ownershipPolicy": "straight, short, web; unique supported points lock after each stage; later fitting and assignment use unclaimed source records",
              "fitSampleOnly": True, "assignmentUsesFullSource": True,
              "unmatchedPolicy": "pending; never removed by design mismatch alone",
              "webEndpointHaloM": "max(0.025, 6 * design radius)",
              "webAxisModel": "straight cylinder at body stations 0.12..0.88; shared parametric curved junctions attach after body identity",
              "shortRecoveryPolicy": (
                  "unresolved only; unclaimed eligible geometry within 0.50 m transverse and "
                  "0.18 m axial margin; finite interval, radius, planar and duplicate guards; "
                  "competing scores within 90 percent stay pending"),
              "shortLengthPolicy": (
                  "design length seeds pose recovery; short endpoints may extend through continuous fixed-radius "
                  "surface evidence up to 1.5 times design length; no shrink, no clipped or disconnected tails"),
              "bendPolicy": (
                  "station-density guard; straight preferred unless distributed evidence improves "
                  "the fit; unsupported ends use tangent continuation, then bounded held-out full-surface correction when independently supported"),
              "surfaceRefinementPolicy": "short pose and straight extrapolated ends use bounded transverse fits with held-out axial bands; confirmed interior, physical radius and non-short design length retained",
              "surfaceResamplingPolicy": "successful narrow samples with poor raw-surface bands retry 768 neighbours; accept only improved unseen bands; radius and identity guards unchanged",
              "longBodyRefinementPolicy": "continuous poor raw-surface bands trigger bounded 6/10/14 coefficient retries; disjoint held-out records must improve without broad regression; fixed radius, length and ownership competition retained",
              "inferredJoinPolicy": "design-only geometry has no scan ownership; scan-guided terminals may assign independently stable continuous surface intervals while full shape remains inferred",
              "workers": int(workers),
              "localOutlierPolicy": "supported cylinder residual plus fewer than 3 raw non-table neighbours",
              "identityAlternativeScoreRatio": .90,
              "webHypothesisEvidence": {"radialNormalAbsCosMin": .8,
                  "scope": "one bounded retry on ambiguous web hypotheses; global and continuous station evidence",
                  "normalOrientation": "unoriented; absolute dot product",
                  "missingNormals": "distance-only geometric fallback; ambiguity guards retained"},
              "curvedPiecePolicy": "fixed steel radius; circular terminal arcs with bounded roll, pitch, yaw, bend radius and sweep; all terminal tail lengths use independently checked end-face or continuous side evidence after pose fitting; complete segment queries; no body reassignment",
              "localOutlierNeighbourRadiusM": max(.0015, .6*min((u['radius'] for u in transformed), default=.004))}
    if layer_independent:
        policy.update(
            candidateScope="single bounded geometric search over every finite non-table source record",
            semanticLayerPolicy="not available at post-table stage; no layer partition or layer ordering applied")
    else:
        policy.update(
            fusionExclusionStatus=4,
            layerZeroPolicy="geometric fallback candidate in every semantic layer",
            layerSummaryAttribution=(
                "matched/pending/inputPoints use each source row's exact shared layer; "
                "candidate-scope endpoint halos and layer 0 fallback are not double counted"),
            layerRetryPolicy=(
                "unresolved units only; original bounded design-line gate over fusion class 3; "
                "unchanged radius, planar and ambiguity acceptance guards"))
    report = {"version": VERSION, "mode": mode,
              "inputStage": "post-fusion" if post_fusion else ("post-layering" if layer_ids is not None and layering is not None else "post-table"),
              "inputPolicy": (
                  "fusion class 3 non-table source records, partitioned by shared semantic layer"
                  if post_fusion else
                  "finite raw source XYZ after shared table exclusion; no class, layer or instance input"
                  if layer_independent else
                  "finite raw source XYZ after shared table exclusion, partitioned by shared semantic layer"),
              "registration": registration, "counts": counts, "instances": instance_rows,
              "inventory": registered_inventory,
              "curvedPieces": curved_pieces, "curveSummary": curve_summary,
              "policy": policy,
              "warnings": warnings, "elapsedS": float(time.perf_counter()-started)}
    if not layer_independent:
        report["layers"] = layer_rows
    if post_fusion and mode == "auto":
        report["registration"]["upstreamEvidenceFrame"] = (
            "fusion and shared layers were computed from the saved alignment before geometry-only auto registration")
    return report, {"control_status": status, "control_instance": owner}
