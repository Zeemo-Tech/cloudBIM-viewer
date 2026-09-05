"""Raw-source support verification for provisional V5 rod instances."""
from __future__ import annotations

from copy import deepcopy

import numpy as np


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
    observed, inferred = [], []
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
        previous = end
        residuals.extend(residual[group].tolist())
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
    retained = []
    for index, item in enumerate(verified):
        support = item["_rawSupportIndices"]
        residual = item.get("rawSupportResidual")
        residual = float(residual) if residual is not None else np.inf
        # Source ownership goes to a materially more precise fit.  Stable
        # ordering retains one exact duplicate instead of deleting both.
        owners = [candidate["_rawSupportIndices"] for position, candidate in enumerate(verified)
                  if position != index and candidate.get("role") == item.get("role") and
                  ((float(candidate["rawSupportResidual"]) if candidate.get("rawSupportResidual") is not None else np.inf) < residual*.80 or
                   (position < index and (float(candidate["rawSupportResidual"]) if candidate.get("rawSupportResidual") is not None else np.inf) <= residual*1.02))]
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
