"""Design-topology control-net fitting on fused, semantically layered steel.

The implementation deliberately separates *evidence* from the design prior.
The prior supplies identities, topology, diameter and length; only observed
fixed-radius surface support is allowed to create a fitted centreline.
Legacy post-table calls remain supported. Candidate retrieval and every
quadratic operation have explicit sample caps for multi-million-point LAS input.
"""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import math
import time

import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment

from rebar_prior_axis import fit_prior_axis


VERSION = "design-control-net-v6"
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
    seeds = [np.median(centers[inverse == i], axis=0) for i in top]
    tolerance = max(.0008, .32*radius)
    evaluated = []
    low, high = np.quantile(along, [.01, .99])
    span = max(high-low, 1e-9)
    bins = np.clip(((along-low)/span*23).astype(int), 0, 23)
    for center in seeds:
        residual = np.abs(np.linalg.norm(xy-center, axis=1)-radius)
        inliers = residual <= tolerance
        if normal_alignment and normals_xy is not None:
            radial = xy-center
            norm_product = np.linalg.norm(radial, axis=1)*np.linalg.norm(normals_xy, axis=1)
            agreement = np.abs(np.einsum('ij,ij->i', radial, normals_xy))
            inliers &= (norm_product < 1e-10) | (agreement >= .8*norm_product)
        if inliers.sum() < 24:
            continue
        occupied = np.bincount(bins[inliers], minlength=24)
        balanced = float(np.minimum(occupied, 24).sum())
        fit_span = float(np.ptp(along[inliers]))
        proximity = max(.72, 1.-.18*np.linalg.norm(center)/max(gate, 1e-9))
        score = balanced * proximity * min(1., fit_span/max(.03, .25*span))
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


def _piecewise_shell(xy, along, radius, gate, normals_xy, fallback, centers_out=None):
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
            normal_alignment=high-low > 2.)
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


def _fit_unit(points, normals, candidate_ids, unit, start, direction, auto=False):
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
    if normals is not None and unit["kind"] == "straight":
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
                                          normal_alignment=unit["kind"] == "straight" and length > 2.)
    if best is None:
        return None, "no-fixed-radius-support", candidate_ids
    score, center, shell, _, initial_span = best
    if second is not None and np.linalg.norm(second[1]-center) > 1.5*radius and second[0] >= .93*score:
        return None, "ambiguous-parallel-support", candidate_ids
    centers = []
    shell = _piecewise_shell(xy, along, radius, gate, normals_xy, shell, centers)
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
                                          normal_alignment=unit["kind"] == "straight" and length > 2.)
        if best is None:
            return None, "no-fixed-radius-support", candidate_ids
        score, center, shell, _, initial_span = best
        centers = []
        shell = _piecewise_shell(xy, along, radius, gate, normals_xy, shell, centers)
    axis_start = start + center[0]*u + center[1]*v
    selected = candidate[shell]
    initial_centerline = np.array([start + station*direction + offset[0]*u + offset[1]*v
                                   for station, offset in centers]) if len(centers) >= 4 and unit["kind"] == "straight" else None
    axis, reason = fit_prior_axis(selected, axis_start, direction, length, radius,
                                  initial_centerline=initial_centerline)
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
    stations = np.linspace(0, 1, 17)
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
            "initialScore": score, "initialSpan": initial_span}, reason, candidate_ids


def _transformed_inventory(inventory, rotation, translation):
    result = deepcopy(inventory or {})
    for unit in result.get("units", []):
        for field in ("startM", "endM"):
            if field in unit:
                unit[field] = (rotation@np.asarray(unit[field], float)+translation).tolist()
        if "direction" in unit:
            unit["direction"] = (rotation@np.asarray(unit["direction"], float)).tolist()
    for bar in result.get("bars", []):
        if bar.get("points"):
            xyz = np.asarray(bar["points"], float)
            bar["points"] = (xyz@rotation.T+translation).tolist()
        if bar.get("boundsM"):
            lo, hi = np.asarray(bar["boundsM"], float)
            corners = np.array(np.meshgrid(*zip(lo, hi))).T.reshape(-1, 3)
            corners = corners@rotation.T+translation
            bar["boundsM"] = [corners.min(axis=0).tolist(), corners.max(axis=0).tolist()]
    return result


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


def _endpoint_candidates(tree, source_ids, curve, radius):
    if tree is None or not len(source_ids):
        return np.empty(0, dtype=np.int64)
    local = tree.query_ball_point(np.asarray([curve[0], curve[-1]]), r=radius)
    return source_ids[_merge_ids(*local)] if len(local) else np.empty(0, dtype=np.int64)


def fit_control_net(points, table_mask, inventory, *, mode="aligned", normals=None,
                    progress=None, fused_classes=None, layer_ids=None, layering=None,
                    workers=1):
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
        item["fitLayerId"] = _semantic_layer(item, layering)
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
    if layer_ids is not None:
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

    layer_elapsed = {1: 0., 2: 0., 3: 0.}

    def fit_one(task):
        number, unit = task
        layer_tree, layer_source_ids = scoped[unit["fitLayerId"]]
        gate = (max(.075, min(.15, .75*unit["length"]), 9*unit["radius"])
                if unit["kind"] in ("short", "web")
                else max(.05, min(.10, .22*unit["length"]), 9*unit["radius"]))
        ids = (_candidate_indices(layer_tree, layer_source_ids, unit["start"], unit["end"], gate,
                    neighbours=768 if unit["kind"] == "straight" and unit["length"] > 2 else None)
               if layer_tree is not None else np.empty(0, int))
        if unit["kind"] == "web":
            # Web cylinders touch both horizontal layers. Admit a bounded halo
            # only at their endpoints rather than widening the whole web scope.
            halo = _endpoint_candidates(tree, source_ids,
                                        np.asarray([unit["start"], unit["end"]]),
                                        max(.025, 6*unit["radius"]))
            ids = _merge_ids(ids, halo, limit=_MAX_UNIT_CANDIDATES)
        model, reason, considered = _fit_unit(points, normals, ids, unit,
                                               unit["start"], unit["direction"], mode == "auto")
        fit_scope = "semantic-layer"
        assignment_tree, assignment_ids = layer_tree, layer_source_ids
        # A shared-layer label is geometric guidance, not proof that every arc
        # of one tube received that label. Retry only unresolved units, within
        # the original bounded design-line gate, against all fused steel. The
        # same radius, planar and ambiguity guards still decide acceptance.
        if model is None and layer_ids is not None and tree is not None:
            retry_ids = _candidate_indices(tree, source_ids, unit["start"], unit["end"], gate,
                    neighbours=768 if unit["kind"] == "straight" and unit["length"] > 2 else None)
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
            radial = _polyline_distances(points[full_ids], model['curve'])
            residual = np.abs(radial-unit['radius'])
            model.update(number=number, unit=unit, reason=reason, candidateIds=full_ids,
                         radial=radial, residual=residual, support=residual <= model['tolerance'],
                         fitScope=fit_scope)
        return number, model, reason, considered, fit_scope

    done = 0
    for layer_id in (1, 2, 3):
        tasks = [(number, unit) for number, unit in enumerate(transformed, 1)
                 if unit["fitLayerId"] == layer_id]
        layer_started = time.perf_counter()
        if workers > 1 and len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=min(int(workers), len(tasks))) as executor:
                results = executor.map(fit_one, tasks)
                for number, model, reason, considered, fit_scope in results:
                    unresolved = reason is not None and "ambiguous" in reason
                    instance_rows[number-1].update(
                        status="pending" if unresolved else "missing", reason=reason,
                        fitCandidateScope=fit_scope)
                    if model is not None:
                        models.append(model)
                    elif len(considered) and unresolved:
                        pending[considered] = True
                    done += 1
                    _progress(progress, "控制网：逐层固定直径拟合", done, max(1, len(units)))
        else:
            for task in tasks:
                number, model, reason, considered, fit_scope = fit_one(task)
                unresolved = reason is not None and "ambiguous" in reason
                instance_rows[number-1].update(
                    status="pending" if unresolved else "missing", reason=reason,
                    fitCandidateScope=fit_scope)
                if model is not None:
                    models.append(model)
                elif len(considered) and unresolved:
                    pending[considered] = True
                done += 1
                _progress(progress, "控制网：逐层固定直径拟合", done, max(1, len(units)))
        layer_elapsed[layer_id] = float(time.perf_counter() - layer_started)

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
        keep = np.ones(len(ids), dtype=bool)
        for reference in references:
            radial = _polyline_distances(points[ids], reference["curve"])
            keep &= radial > reference["unit"]["radius"] + reference["tolerance"]
        fitted, reason, _ = _fit_unit(points, normals, ids[keep], unit, unit["start"], unit["direction"], mode == "auto")
        if fitted is not None:
            full_ids = _full_model_candidates(layer_tree, layer_source_ids, fitted["curve"], max(.02, 4*unit["radius"]))
            radial = _polyline_distances(points[full_ids], fitted["curve"])
            residual = np.abs(radial-unit["radius"])
            longer.update(fitted)
            longer.update(candidateIds=full_ids, radial=radial, residual=residual,
                          support=residual <= fitted["tolerance"], reason=reason,
                          fitScope="parallel-surface-refit")
        layer_elapsed[unit["fitLayerId"]] += time.perf_counter()-refit_started

    # Identical supported tubes from competing design units are globally
    # ambiguous. Suppress both rather than manufacturing duplicate steel.
    ambiguous_models = set()
    for i, left in enumerate(models):
        for right in models[i+1:]:
            distance = float(np.mean(_polyline_distances(left["curve"], right["curve"])))
            if abs(np.dot(left["unit"]["direction"], right["unit"]["direction"])) > .98:
                # A long member's overhang must not hide duplication throughout
                # the shorter member's shared span. Check both directions.
                distance = min(distance, float(np.mean(_polyline_distances(right["curve"], left["curve"]))))
            if distance < 1.4*max(left["unit"]["radius"], right["unit"]["radius"]):
                ambiguous_models.update((left["number"], right["number"]))
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
    status[pending & usable] = 2
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
                   directionDifferenceDeg=model["directionDifferenceDeg"])
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
    warnings = []
    if not units:
        warnings.append("design inventory contains no valid fitting units")
    if mode == "auto" and registration.get("status") != "supported":
        warnings.append("automatic global pose has insufficient geometric support")
    if global_ambiguous:
        warnings.append('competing or insufficient global pose evidence; candidate axes are not confirmed identities')
    if fitted < len(units):
        warnings.append(f"{len(units)-fitted} design units remain pending or missing")
    names = _layer_names(layering)
    layer_rows = []
    for layer_id in (1, 2, 3):
        exact = usable if layer_ids is None else (usable & (layer_ids == layer_id))
        unit_rows = [row for row in instance_rows if row["fitLayerId"] == layer_id]
        layer_rows.append({"id": layer_id, "name": names[layer_id],
            "inputPoints": int(np.count_nonzero(exact)),
            "matched": int(np.count_nonzero(exact & (status == 1))),
            "pending": int(np.count_nonzero(exact & (status == 2))),
            "designUnits": len(unit_rows),
            "fittedUnits": sum(row["status"] == "fitted" for row in unit_rows),
            "elapsedS": layer_elapsed[layer_id]})
    report = {"version": VERSION, "mode": mode,
              "inputStage": "post-fusion" if post_fusion else "post-table",
              "inputPolicy": ("fusion class 3 non-table source records, partitioned by shared semantic layer"
                              if post_fusion else
                              "raw source XYZ after shared table exclusion; no class or instance input"),
              "registration": registration, "counts": counts, "instances": instance_rows,
              "layers": layer_rows,
              "inventory": _transformed_inventory(inventory, rotation, translation),
              "policy": {"fitSampleOnly": True, "assignmentUsesFullSource": True,
                         "unmatchedPolicy": "pending; never removed by design mismatch alone",
                         "fusionExclusionStatus": 4,
                         "layerZeroPolicy": "geometric fallback candidate in every semantic layer",
                         "layerSummaryAttribution": (
                             "matched/pending/inputPoints use each source row's exact shared layer; "
                             "candidate-scope endpoint halos and layer 0 fallback are not double counted"),
                         "webEndpointHaloM": "max(0.025, 6 * design radius)",
                         "layerRetryPolicy": (
                             "unresolved units only; original bounded design-line gate over fusion class 3; "
                             "unchanged radius, planar and ambiguity acceptance guards"),
                         "workers": int(workers),
                         "localOutlierPolicy": "supported cylinder residual plus fewer than 3 raw non-table neighbours",
                         "identityAlternativeScoreRatio": .90,
                         "localOutlierNeighbourRadiusM": max(.0015, .6*min((u['radius'] for u in transformed), default=.004))},
              "warnings": warnings, "elapsedS": float(time.perf_counter()-started)}
    if post_fusion and mode == "auto":
        report["registration"]["upstreamEvidenceFrame"] = (
            "fusion and shared layers were computed from the saved alignment before geometry-only auto registration")
    return report, {"control_status": status, "control_instance": owner}
