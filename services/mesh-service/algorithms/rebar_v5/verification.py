"""Raw-source support verification for provisional V5 rod instances."""
from __future__ import annotations

from copy import deepcopy
from heapq import merge
from itertools import groupby

import numpy as np


def _ownership_key(item):
    """Stable tie-breaker independent of detector/candidate iteration order."""
    line = np.asarray(item.get("centerline", ()), dtype=float)
    if line.ndim != 2 or line.shape[1:] != (3,) or not len(line):
        line = np.empty((0, 3))
    return (str(item.get("role", "")), int(item.get("id", 0)),
            float(item.get("radius", 0.)), tuple(np.round(line.ravel(), 7)))


def _source_records(runtime, item, p):
    """Re-read a candidate's bounded verified source records for comparison."""
    queried, records, accumulated = set(), [], 0
    for path in item.get("observedSegments", []):
        points = np.asarray(path.get("points", ()), dtype=float)
        if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2:
            continue
        for start, end in zip(points[:-1], points[1:]):
            rows, _, _ = _raw_tube(runtime, start, end, float(item["radius"]), p, queried)
            if len(rows):
                accumulated += len(rows)
                if accumulated > 2*p.neighbourhood_point_limit:
                    raise ValueError('V5 stripe verification neighbourhood exceeds memory budget')
                records.append(rows)
    if not records:
        return np.empty(0, dtype=[("source_index", "<u8"), ("xyz", "<f8", (3,))])
    combined = np.concatenate(records)
    _, first = np.unique(combined["source_index"], return_index=True)
    combined = combined[first]
    support = item["_rawSupportIndices"]
    positions = np.searchsorted(support, combined["source_index"])
    return combined[(positions < len(support)) &
                    (support[np.minimum(positions, len(support)-1)] == combined["source_index"])]


def _plausible_owner(stripe, owner, p):
    """Cheap finite-segment prefilter before re-reading any raw stripe points."""
    lateral = float(stripe["radius"]) + float(owner["radius"]) + p.support_distance
    for stripe_path in stripe.get("observedSegments", []):
        stripe_points = np.asarray(stripe_path.get("points", ()), dtype=float)
        if stripe_points.ndim != 2 or stripe_points.shape[1:] != (3, ) or len(stripe_points) < 2:
            continue
        for a, b in zip(stripe_points[:-1], stripe_points[1:]):
            direction = b-a; length = float(np.linalg.norm(direction))
            if length <= 1e-10: continue
            direction /= length
            for owner_path in owner.get("observedSegments", []):
                owner_points = np.asarray(owner_path.get("points", ()), dtype=float)
                if owner_points.ndim != 2 or owner_points.shape[1:] != (3, ) or len(owner_points) < 2:
                    continue
                for c, d in zip(owner_points[:-1], owner_points[1:]):
                    axis = d-c; owner_length = float(np.linalg.norm(axis))
                    if owner_length <= 1e-10: continue
                    axis /= owner_length
                    if abs(float(direction @ axis)) < np.cos(np.deg2rad(8.)): continue
                    projected = (np.vstack((a, b))-c) @ axis
                    overlap = min(owner_length, max(projected))-max(0., min(projected))
                    if overlap < min(p.min_primitive_length, .25*length): continue
                    offset = np.linalg.norm((a-c)-float((a-c) @ axis)*axis)
                    if offset <= lateral: return True
    return False


def _supported_by_owner(records, owner, p):
    """Return finite measured extension evidence for the best owner edge."""
    if not len(records):
        return None
    tolerance = max(.002, .60*p.support_distance)
    best = None
    for path_index, path in enumerate(owner.get("observedSegments", [])):
        points = np.asarray(path.get("points", ()), dtype=float)
        if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2:
            continue
        for edge_index, (start, end) in enumerate(zip(points[:-1], points[1:])):
            axis = end-start
            length = float(np.linalg.norm(axis))
            if length <= 1e-10:
                continue
            direction = axis/length
            delta = records["xyz"]-start
            axial = delta @ direction
            radial = np.linalg.norm(delta-axial[:, None]*direction, axis=1)
            accepted = np.abs(radial-float(owner["radius"])) <= tolerance
            if float(np.mean(accepted)) < .98:
                continue
            values = axial[accepted]; lo, hi = float(values.min()), float(values.max())
            overlap = max(0., min(length, hi)-max(0., lo))
            if overlap < min(p.min_primitive_length, .25*(hi-lo)):
                continue
            count = int(accepted.sum())
            if best is None or count > best[0]:
                best = (count, path_index, edge_index,
                        records["source_index"][accepted], axial[accepted],
                        np.abs(radial[accepted]-float(owner["radius"])), start, direction, length)
    return best


def _append_transferred_support(runtime, owner, evidence, p):
    """Extend an owner only along its own axis and actual raw-supported bounds."""
    if not evidence:
        return
    _, path_index, edge_index, source_ids, axial, residuals, start, direction, length = evidence
    lo, hi = min(0., float(axial.min())), max(length, float(axial.max()))
    if lo == 0. and hi == length:
        return source_ids
    # Reuse the raw verifier on the proposed parent-axis extent. It enforces
    # point count, measured interval length and gaps, including a tail group
    # crossing the previous endpoint; the proposal alone is never observed.
    observed, inferred, records, _ = _verified_segments(
        runtime, start+lo*direction, start+hi*direction, float(owner['radius']), p, set())
    if not observed:
        return np.empty(0, np.uint64)
    paths = owner['observedSegments']
    original = paths[path_index]['points']
    replacement = []
    if edge_index:
        replacement.append({'points': original[:edge_index+1]})
    replacement.extend(observed)
    if edge_index+2 < len(original):
        replacement.append({'points': original[edge_index+1:]})
    owner['observedSegments'] = paths[:path_index]+replacement+paths[path_index+1:]
    owner["inferredSegments"] = [*owner.get("inferredSegments", []), *inferred]
    from .ownership import _observed_length, _stitch
    lines = [np.asarray(path['points']) for path in owner['observedSegments']]
    owner['centerline'], pending = _stitch(lines, owner['inferredSegments'], p)
    if pending:
        owner['associationPending'] = True
    owner['length'] = _observed_length(lines, p)
    # The extended model is supported by the whole reverified parent surface,
    # not just the biased stripe that proposed this extension.
    return records['source_index']


def _pieces(a, b, p):
    length = float(np.linalg.norm(b-a))
    # A diagonal tube's enclosing box can be much larger than its support.
    # Query bounded pieces instead of one world-scale diagonal box.
    count = max(1, int(np.ceil(length / max(.20, p.block_size))))
    return [(a + (b-a)*i/count, a + (b-a)*(i+1)/count) for i in range(count)]


def _raw_tube(runtime, a, b, radius, p, queried):
    direction = b-a
    length = float(np.linalg.norm(direction))
    if length <= 1e-10:
        empty = np.empty(0, dtype=[("source_index", "<u8"), ("xyz", "<f8", (3,))])
        return empty, np.empty(0), np.empty(0)
    direction /= length
    padding = radius + p.support_distance
    extension = p.axial_gap if length > .20 else 0.
    parts=[];accumulated=0
    for start, end in _pieces(a, b, p):
        lo, hi = np.minimum(start, end)-padding-np.abs(direction)*extension, np.maximum(start, end)+padding+np.abs(direction)*extension
        records = runtime.store.query(lo, np.nextafter(hi, np.inf))
        if len(records):
            queried.update(map(int,records['source_index']))
            accumulated+=len(records)
            if len(queried)>p.neighbourhood_point_limit or accumulated>2*p.neighbourhood_point_limit:
                raise ValueError("V5 raw verification neighbourhood exceeds memory budget")
            parts.append(records)
    if not parts:
        empty = np.empty(0, dtype=[("source_index", "<u8"), ("xyz", "<f8", (3,))])
        return empty, np.empty(0), np.empty(0)
    records=np.concatenate(parts)
    _,first=np.unique(records['source_index'],return_index=True)
    records=records[first]
    noise = np.asarray(runtime.masks(records), dtype=bool)
    xyz = records["xyz"]
    axial = (xyz-a) @ direction
    # Evaluate surface residual against the infinite axis first; axial bounds
    # below decide whether long straight candidates may extend to observed ends.
    radial = np.linalg.norm(xyz-(a+axial[:, None]*direction), axis=1)
    radial_residual = np.abs(radial-radius)
    surface_tolerance = max(.002, .60*p.support_distance)
    keep = ~noise & (axial >= -extension) & (axial <= length+extension) & (radial <= padding) & (radial_residual <= surface_tolerance)
    return records[keep], axial[keep], radial_residual[keep]


def _verified_segments(runtime, a, b, radius, p, queried, connected_terminal=False):
    records, axial, residual = _raw_tube(runtime, a, b, radius, p, queried)
    if len(records) < p.min_primitive_votes:
        return [], [], records, None
    direction = (b-a) / np.linalg.norm(b-a)
    order = np.argsort(axial, kind="stable")
    differences=np.diff(axial[order]);positive=differences[differences>1e-9]
    observed_gap=min(p.axial_gap,max(4*radius,3*float(np.quantile(positive,.95)) if len(positive) else 0))
    groups = np.split(order, np.flatnonzero(differences > observed_gap)+1)
    observed, inferred, accepted = [], [], []
    previous = None
    residuals = []
    for group in groups:
        if len(group) < p.min_primitive_votes:
            continue
        values = axial[group]
        # The tube and radial gates already reject outliers. Min/max preserves
        # actual support ends and avoids repeated percentile shrinkage when a
        # long candidate was split into several bounded raw queries.
        lo, hi = float(values.min()), float(values.max())
        minimum_length=max(radius,p.min_primitive_length*.25) if connected_terminal else p.min_primitive_length
        if hi-lo < minimum_length:
            continue
        start, end = a+lo*direction, a+hi*direction
        if previous is not None and np.linalg.norm(start-previous) > observed_gap:
            inferred.append({"points": [previous.tolist(), start.tolist()], "source": "raw-support-gap"})
        observed.append({"points": [start.tolist(), end.tolist()]})
        accepted.append(group)
        previous = end
        residuals.extend(residual[group].tolist())
    records = records[np.concatenate(accepted)] if accepted else records[:0]
    return observed, inferred, records, float(np.quantile(residuals, .8)) if residuals else None


def verify_raw_instances(runtime, instances, p, *, preserve_association=False):
    """Replace detection-only observed paths with raw-supported path pieces.

    The returned instances retain their provisional IDs. Callers may assign
    final stable IDs after deduplication, but must never turn inferred bridges
    back into observed evidence.
    """
    verified, diagnostics = [], {"candidateCount": len(instances), "verifiedCount": 0, "discardedCount": 0, "instances": []}
    support_bytes=0
    for item in instances:
        current = deepcopy(item)
        observed, inferred, residuals = [], [], []
        support_indices, queried = set(), set()
        original_length = verified_length = 0.
        edges=[(np.asarray(a,float),np.asarray(b,float)) for path in item.get('observedSegments',[]) for a,b in zip(path['points'][:-1],path['points'][1:])]
        for path in item.get("observedSegments", []):
            points = np.asarray(path.get("points", []), dtype=float)
            if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 2 or not np.isfinite(points).all():
                continue
            for a, b in zip(points[:-1], points[1:]):
                original_length += float(np.linalg.norm(b-a))
                attached=any(min(np.linalg.norm(a-c),np.linalg.norm(a-d),np.linalg.norm(b-c),np.linalg.norm(b-d))<=p.join_gap
                             for c,d in edges if not (np.array_equal(a,c) and np.array_equal(b,d)))
                pieces, gaps, records, residual = _verified_segments(runtime, a, b, float(item["radius"]), p, queried,attached)
                observed.extend(pieces); inferred.extend(gaps)
                support_indices.update(int(value) for value in records["source_index"])
                verified_length += sum(float(np.linalg.norm(np.asarray(piece["points"])[1]-np.asarray(piece["points"])[0])) for piece in pieces)
                if residual is not None: residuals.append(residual)
        if not observed:
            diagnostics["discardedCount"] += 1
            diagnostics["instances"].append({"id": item.get("id", 0), "rawSupportCount": len(support_indices), "verified": False})
            continue
        # Existing inferred segments are retained as inference, never promoted.
        inferred.extend(deepcopy(item.get("inferredSegments", [])))
        current["observedSegments"] = observed
        current["inferredSegments"] = inferred
        current["rawSupportCount"] = len(support_indices)
        current["rawSupportResidual"] = float(np.median(residuals)) if residuals else None
        current["rawSupportCoverage"] = float(np.clip(verified_length / max(original_length, 1e-12), 0, 1))
        # Kept only while this function establishes ownership.  It is never
        # exported in the public instance schema.
        current["_rawSupportIndices"] = np.sort(np.fromiter(support_indices,dtype=np.uint64))
        support_bytes+=current["_rawSupportIndices"].nbytes
        if support_bytes>512*1024**2:
            raise ValueError('V5 raw candidate support exceeds 512 MiB evidence budget')
        centerline = [observed[0]["points"][0]]
        for path in observed:
            if centerline[-1] != path["points"][0]: centerline.append(path["points"][0])
            centerline.append(path["points"][-1])
        current["centerline"] = centerline
        verified.append(current); diagnostics["verifiedCount"] += 1
        diagnostics["instances"].append({"id": item.get("id", 0), "rawSupportCount": current["rawSupportCount"], "residual": current["rawSupportResidual"], "coverage": current["rawSupportCoverage"], "verified": True})
    # A broad or duplicate fit can have excellent radial residual while every
    # raw surface observation already belongs to more specific candidates.
    # Remove only candidates with no material unique support.  This does not
    # use scene labels, does not choose between nearby rods, and deliberately
    # evaluates the union once so the two 16 mm bars cannot erase each other.
    # Select local physical parents before evaluating unique source ownership.
    # Only shorter, locally plausible stripes are re-read, keeping this extra
    # evidence check bounded and letting the raw surface test make the decision.
    dominated_by: dict[int, int] = {}
    ranked = sorted(range(len(verified)), key=lambda index: (
        -len(verified[index]['_rawSupportIndices']), -float(verified[index].get('length', 0.)),
        _ownership_key(verified[index])))
    rank = {index: position for position, index in enumerate(ranked)}
    for index in ranked:
        stripe = verified[index]
        choices = []
        for position, owner in enumerate(verified):
            if rank[position] >= rank[index] or position in dominated_by or owner.get("role") != stripe.get("role"):
                continue
            owner_count = len(owner["_rawSupportIndices"])
            owner_length = float(owner.get("length", 0.))
            if not _plausible_owner(stripe, owner, p):
                continue
            choices.append((position, owner_count, owner_length, _ownership_key(owner)))
        if not choices:
            continue
        records = _source_records(runtime, stripe, p)
        if len(records) < .98*len(stripe["_rawSupportIndices"]):
            continue
        for parent, _, _, _ in sorted(choices, key=lambda value: (-value[1], -value[2], value[3])):
            evidence = _supported_by_owner(records, verified[parent], p)
            if evidence is None:
                continue
            evidence_bytes = sum(value.nbytes for value in evidence if isinstance(value, np.ndarray))
            if support_bytes + evidence_bytes > 512*1024**2:
                raise ValueError('V5 raw candidate support exceeds 512 MiB evidence budget')
            # Publish measured support to this already-ranked physical root
            # immediately. Later tails may overlap only the newly verified
            # extension, not the original shorter proposal.
            owner = verified[parent]
            accepted_ids = _append_transferred_support(runtime, owner, evidence, p)
            old_support = owner['_rawSupportIndices']
            if support_bytes + evidence_bytes + old_support.nbytes + 3*accepted_ids.nbytes > 512*1024**2:
                raise ValueError('V5 raw candidate support exceeds 512 MiB evidence budget')
            sources = merge(old_support, np.sort(accepted_ids))
            new_support = np.fromiter((source for source, _ in groupby(sources)), dtype=np.uint64)
            support_bytes += new_support.nbytes-old_support.nbytes
            owner['_rawSupportIndices'] = new_support
            owner['rawSupportCount'] = int(len(new_support))
            # A prior residual summary is not one extra raw observation. Keep
            # that fit-quality evidence instead of biasing it toward the tail.
            dominated_by[index] = parent
            break

    retained = []
    for index, item in enumerate(verified):
        support = item["_rawSupportIndices"]
        residual = item.get("rawSupportResidual")
        residual = float(residual) if residual is not None else np.inf
        # Source ownership goes to a materially more precise fit.  Stable
        # ordering retains one exact duplicate instead of deleting both.
        owners = []
        for position, candidate in enumerate(verified):
            # A physically dominated stripe cannot erase its own parent (or
            # another physical owner) through a better aggregate residual.
            if position == index or position in dominated_by or candidate.get("role") != item.get("role"):
                continue
            candidate_residual = float(candidate["rawSupportResidual"]) if candidate.get("rawSupportResidual") is not None else np.inf
            materially_precise = candidate_residual < residual*.80
            earlier = (_ownership_key(candidate) < _ownership_key(item) or
                       (_ownership_key(candidate) == _ownership_key(item) and position < index))
            stable_near_tie = earlier and candidate_residual <= residual*1.02
            if materially_precise or stable_near_tie or dominated_by.get(index) == position:
                owners.append(candidate["_rawSupportIndices"])
        unique=np.ones(len(support),bool)
        for owner in owners:
            if not len(owner) or not len(support) or owner[-1]<support[0] or support[-1]<owner[0]:continue
            positions=np.searchsorted(owner,support)
            valid=positions<len(owner)
            unique[valid]&=owner[positions[valid]]!=support[valid]
        unique_ratio = int(unique.sum()) / max(len(support), 1)
        item["rawSupportUniqueRatio"] = float(unique_ratio)
        if len(support) and unique_ratio < .05:
            diagnostics["discardedCount"] += 1
            diagnostics["verifiedCount"] -= 1
            diagnostics["instances"].append({"id": item.get("id", 0), "rawSupportCount": len(support),
                                                "uniqueSupportRatio": float(unique_ratio), "verified": False,
                                                "reason": "non-unique-raw-support"})
            if preserve_association:
                item['_associationOnly']=True;retained.append(item)
            continue
        retained.append(item)
    if not preserve_association:
        for item in retained:item.pop("_rawSupportIndices", None)
    return retained, diagnostics
