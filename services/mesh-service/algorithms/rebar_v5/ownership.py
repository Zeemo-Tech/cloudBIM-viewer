"""Associate verified rod fragments without borrowing unsupported geometry."""
from __future__ import annotations

from copy import deepcopy
from heapq import merge
from itertools import groupby

import numpy as np
from scipy.spatial import cKDTree


def _line(item):
    points = np.asarray(item.get("centerline", ()), dtype=float)
    if points.ndim == 2 and points.shape[1:] == (3,) and len(points) >= 2 and np.isfinite(points).all():
        return points
    for path in item.get("observedSegments", ()):
        points = np.asarray(path.get("points", ()), dtype=float)
        if points.ndim == 2 and points.shape[1:] == (3,) and len(points) >= 2 and np.isfinite(points).all():
            return points
    return None


def _compatible(a, b):
    if a.get("role") != b.get("role"):
        return False
    ar, br = float(a["radius"]), float(b["radius"])
    return abs(ar-br) <= max(.0015, .35*min(ar, br))


def _distinct_parallel_axes(a, b):
    first, second = _line(a), _line(b)
    if first is None or second is None: return False
    tolerance = max(.0025, .45*(float(a["radius"])+float(b["radius"])))
    for x0, x1, xd, xl in _segments(first):
        for y0, y1, yd, yl in _segments(second):
            if abs(float(xd @ yd)) < np.cos(np.deg2rad(8.)): continue
            ya, yb = float((y0-x0) @ xd), float((y1-x0) @ xd)
            lo, hi = max(0., min(ya, yb)), min(xl, max(ya, yb))
            if hi-lo < .30*min(xl, yl): continue
            probes = x0+np.asarray([lo, (lo+hi)/2, hi])[:, None]*xd
            fraction = ((probes-x0) @ xd-ya)/(yb-ya)
            other = y0+fraction[:, None]*(y1-y0)
            if np.linalg.norm(probes-other, axis=1).max() > tolerance: return True
    return False


def _observed_paths_connected(item, p):
    """Observed finite paths must connect without borrowing inferred gaps."""
    paths = [np.asarray(path.get("points", ()), dtype=float)
             for path in item.get("observedSegments", ())]
    paths = [path for path in paths if path.ndim == 2 and path.shape[1:] == (3,) and len(path) >= 2 and np.isfinite(path).all()]
    if len(paths) < 2:
        return True
    parent = list(range(len(paths)))
    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]; value = parent[value]
        return value
    endpoints = np.asarray([point for path in paths for point in (path[0], path[-1])])
    for left, right in cKDTree(endpoints).query_pairs(p.join_gap, output_type="ndarray"):
        a, b = int(left//2), int(right//2)
        if a != b:
            a, b = find(a), find(b)
            if a != b: parent[b] = a
    return len({find(index) for index in range(len(paths))}) == 1


def _segments(points):
    for a, b in zip(points[:-1], points[1:]):
        vector = b-a
        length = float(np.linalg.norm(vector))
        if length > 1e-9:
            yield a, b, vector/length, length


def _overlap_same_axis(a, b):
    """Detect duplicate long, co-linear fragments of one physical bar.

    This centreline gate is deliberately far below the 16 mm close-pair case;
    broad raw support tubes are never used to establish common ownership.
    """
    first, second = _line(a), _line(b)
    if first is None or second is None:
        return False
    tolerance = max(.0025, .45*(float(a["radius"])+float(b["radius"])))
    for x0, x1, xd, xl in _segments(first):
        for y0, y1, yd, yl in _segments(second):
            if abs(float(xd @ yd)) < np.cos(np.deg2rad(8.0)):
                continue
            ya, yb = float((y0-x0) @ xd), float((y1-x0) @ xd)
            overlap = max(0., min(xl, max(ya, yb))-max(0., min(ya, yb)))
            if overlap < .30*min(xl, yl):
                continue
            # Compare inside the shared axial interval.  A midpoint of the
            # *whole* second segment can be far outside a short overlap and
            # falsely extrapolate its lateral error into this local decision.
            lo, hi = max(0., min(ya, yb)), min(xl, max(ya, yb))
            probes = x0+np.asarray([lo, (lo+hi)/2, hi])[:, None]*xd
            other_lo, other_hi = sorted((ya, yb))
            projected = np.clip((probes-x0) @ xd, other_lo, other_hi)
            fraction = (projected-ya)/(yb-ya)
            other = y0+fraction[:, None]*(y1-y0)
            lateral = np.linalg.norm(probes-other, axis=1).max()
            if lateral <= tolerance:
                return True
    return False


class _Union:
    def __init__(self, count): self.parent = list(range(count))
    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value
    def join(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right: self.parent[right] = left


def _support_overlap(first, second):
    """Fraction of the smaller sorted raw-source support set in common."""
    left = np.asarray(first.get("_rawSupportIndices", ()), dtype=np.uint64)
    right = np.asarray(second.get("_rawSupportIndices", ()), dtype=np.uint64)
    if not len(left) or not len(right):
        return 0.
    positions = np.searchsorted(right, left)
    common = np.count_nonzero((positions < len(right)) & (right[np.minimum(positions, len(right)-1)] == left))
    return float(common/min(len(left), len(right)))


def _raw_support_union_count(instances, indices):
    """Count verified source records once; legacy callers may lack private IDs."""
    evidence = [np.asarray(instances[index].get("_rawSupportIndices", ()), dtype=np.uint64)
                for index in indices]
    if evidence and all(len(values) for values in evidence):
        return sum(1 for _ in groupby(merge(*(iter(values) for values in evidence))))
    # ``merge_fragments`` is also a small public test seam. Production callers
    # always arrive from verification with source IDs; retain old semantics for
    # synthetic/legacy inputs where an exact union cannot be reconstructed.
    return max(int(instances[index].get("rawSupportCount", 0)) for index in indices)


def _endpoint_links(instances, lines, p):
    endpoints, outward = [], []
    for points in lines:
        ends = points[[0, -1]]
        directions = np.vstack((points[0]-points[1], points[-1]-points[-2]))
        directions /= np.maximum(np.linalg.norm(directions, axis=1)[:, None], 1e-12)
        endpoints.extend(ends); outward.extend(directions)
    endpoints, outward = np.asarray(endpoints), np.asarray(outward)
    options = {}
    for left, right in cKDTree(endpoints).query_pairs(p.join_gap, output_type="ndarray"):
        ia, ib = int(left//2), int(right//2)
        a, b = instances[ia], instances[ib]
        if ia == ib or not _compatible(a, b):
            continue
        delta = endpoints[right]-endpoints[left]
        gap = float(np.linalg.norm(delta))
        angle = float(np.degrees(np.arccos(np.clip(-outward[left] @ outward[right], -1, 1))))
        maximum = p.max_turn_degrees if a["role"] == "planar" else 8.
        if angle > maximum and not (a["role"] == "planar" and gap <= .012 and angle <= 1.65*maximum):
            continue
        if angle < 8.:
            lateral = np.linalg.norm(delta-float(delta @ outward[left])*outward[left])
            if lateral > max(.0025, .45*min(float(a["radius"]), float(b["radius"]))):
                continue
        elif gap > .012 and min(abs(float(delta @ outward[left])), abs(float(delta @ outward[right]))) < .55*gap:
            continue
        score = gap+.02*angle/max(p.max_turn_degrees, 1)
        options.setdefault(int(left), []).append((score, int(right)))
        options.setdefault(int(right), []).append((score, int(left)))
    choice = {}
    for endpoint, choices in options.items():
        choices.sort()
        if len(choices) > 1 and choices[1][0]-choices[0][0] < max(.001, .10*choices[0][0]):
            continue
        choice[endpoint] = choices[0][1]
    return [(left, right) for left, right in choice.items() if left < right and choice.get(right) == left]


def _path_length(points):
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def _geometry_key(value):
    """Stable geometric ordering key; never changes emitted path direction."""
    points = np.asarray(value, dtype=float)
    if points.ndim != 2 or points.shape[1:] != (3,):
        return ()
    forward = tuple(np.round(points.ravel(), 8))
    backward = tuple(np.round(points[::-1].ravel(), 8))
    return min(forward, backward)


def _path_key(path):
    return (str(path.get("source", "")), _geometry_key(path.get("points", ())))


def _item_key(item):
    return (_geometry_key(_line(item)), str(item.get("role", "")),
            float(item.get("radius", 0.)), int(item.get("id", 0)))


def _collinear_union(first, second, p):
    """Return a physical interval union, or None for distinct observations."""
    if len(first) != 2 or len(second) != 2:
        return None
    direction = first[1]-first[0]
    length = float(np.linalg.norm(direction))
    other = second[1]-second[0]
    if length <= 1e-9 or np.linalg.norm(other) <= 1e-9:
        return None
    direction /= length
    if abs(float(direction @ (other/np.linalg.norm(other)))) < np.cos(np.deg2rad(8.)):
        return None
    projection = (second-first[0]) @ direction
    lateral = np.linalg.norm((second-first[0])-projection[:, None]*direction, axis=1).max()
    tolerance = max(.0025, .45*p.max_radius)
    if lateral > tolerance:
        return None
    lo, hi = min(0., *projection), max(length, *projection)
    # A genuine raw gap remains two observed paths and is represented only by
    # an inferred bridge later.
    if min(length, max(projection))-max(0., min(projection)) < -p.observed_join_gap:
        return None
    return np.vstack((first[0]+lo*direction, first[0]+hi*direction))


def _curve_append(first, second, p):
    """Append overlapping coarse arc pieces while retaining their centre path."""
    choices = []
    for base, extension in ((first, second), (first, second[::-1]),
                            (second, first), (second, first[::-1])):
        if len(base) < 2 or len(extension) < 2:
            continue
        a, b = base[-2], base[-1]
        direction = b-a
        length = float(np.linalg.norm(direction))
        vector = extension[1]-extension[0]
        other_length = float(np.linalg.norm(vector))
        if length <= 1e-9 or other_length <= 1e-9:
            continue
        direction /= length
        if float(direction @ (vector/other_length)) < np.cos(np.deg2rad(p.max_turn_degrees)):
            continue
        projected = (extension[:2]-a) @ direction
        overlap = min(length, max(projected))-max(0., min(projected))
        lateral = np.linalg.norm((extension[:2]-a)-projected[:, None]*direction, axis=1).max()
        if overlap < .25*min(length, other_length) or lateral > p.max_radius+p.observed_join_gap:
            continue
        # The last endpoint adds the non-overlapping observed continuation;
        # the shared portion remains represented by the existing path.
        extra = extension[-1]
        if float((extra-a) @ direction) <= length+p.observed_join_gap:
            # Extension lies wholly inside the existing support; appending it
            # would make the display path reverse over a duplicate stripe.
            continue
        if np.linalg.norm(base[-1]-extra) <= 1e-9:
            continue
        choices.append((float(np.linalg.norm(base[-1]-extra)), np.vstack((base, extra))))
    return min(choices, key=lambda item: item[0])[1] if choices else None


def _merge_observed(paths, p):
    """Preserve each finite raw-supported polyline verbatim.

    A curve fit's intermediate vertices are support geometry.  Replacing them
    with one chord may shorten the supported arc by centimetres, so overlap
    association belongs to identity/length accounting, never this output.
    """
    lines = [np.asarray(path.get("points", ()), dtype=float) for path in paths]
    return [line for line in lines if line.ndim == 2 and line.shape[1:] == (3,) and len(line) >= 2 and np.isfinite(line).all()]


def _straight_union_lines(lines, p):
    """Display/measure straight overlap unions without altering observations."""
    remaining = sorted(lines, key=_geometry_key)
    result = []
    while remaining:
        first = remaining.pop(0)
        if len(first) != 2:
            result.append(first)
            continue
        merged = first
        changed = True
        while changed:
            changed = False
            for index, other in enumerate(remaining):
                union = _collinear_union(merged, other, p)
                if union is not None:
                    merged = union
                    remaining.pop(index)
                    changed = True
                    break
        result.append(merged)
    return sorted(result, key=_geometry_key)


def _observed_length(lines, p):
    """Count overlapping straight support once without changing its vertices."""
    return float(sum(_path_length(line) for line in _straight_union_lines(lines, p)))


def _stitch(lines, bridges, p):
    """Stitch exactly one unbranched path, otherwise leave evidence separate."""
    if not lines:
        return [], True
    lines = sorted(lines, key=_geometry_key)
    bridges = sorted(bridges, key=_path_key)
    endpoints = np.asarray([point for line in lines for point in (line[0], line[-1])])
    outward = []
    for line in lines:
        directions = np.vstack((line[0]-line[1], line[-1]-line[-2]))
        directions /= np.maximum(np.linalg.norm(directions, axis=1)[:, None], 1e-12)
        outward.extend(directions)
    candidates = {}
    # Coarse curve pieces can have a small fit-to-fit endpoint discontinuity;
    # tangent agreement makes this a continuation rather than a nearest-neighbour
    # shortcut across a crossing.
    continuation_gap = max(p.observed_join_gap, 2.*p.max_radius)
    for left, right in cKDTree(endpoints).query_pairs(continuation_gap, output_type="ndarray"):
        if left//2 == right//2:
            continue
        distance = float(np.linalg.norm(endpoints[left]-endpoints[right]))
        angle = float(np.degrees(np.arccos(np.clip(-outward[left] @ outward[right], -1, 1))))
        if angle > p.max_turn_degrees:
            continue
        score = distance+.0005*angle
        candidates.setdefault(int(left), []).append((score, int(right), False))
        candidates.setdefault(int(right), []).append((score, int(left), False))
    # Inferred bridges are explicit associations, never inferred merely from
    # closest geometry. They may connect a genuine raw-support gap.
    for bridge in bridges:
        points = np.asarray(bridge.get("points", ()), dtype=float)
        if points.shape != (2, 3):
            continue
        left = int(np.argmin(np.linalg.norm(endpoints-points[0], axis=1)))
        right = int(np.argmin(np.linalg.norm(endpoints-points[1], axis=1)))
        if left != right and np.linalg.norm(endpoints[left]-points[0]) <= p.observed_join_gap and np.linalg.norm(endpoints[right]-points[1]) <= p.observed_join_gap:
            candidates.setdefault(left, []).append((float(np.linalg.norm(points[1]-points[0])), right, True))
            candidates.setdefault(right, []).append((float(np.linalg.norm(points[1]-points[0])), left, True))
    choices = {}
    for endpoint, options in candidates.items():
        options.sort()
        # A branch/crossing is not a valid single centreline.
        if len(options) > 1 and options[1][0]-options[0][0] <= .002:
            return max(lines, key=lambda line: (_path_length(line), _geometry_key(line))).tolist(), True
        choices[endpoint] = options[0]
    links = {endpoint: option for endpoint, option in choices.items()
             if choices.get(option[1], (None, None, None))[1] == endpoint}
    if len(links) != len(choices):
        return max(lines, key=lambda line: (_path_length(line), _geometry_key(line))).tolist(), True
    starts = [i for i in range(len(endpoints)) if i not in links]
    if len(starts) != 2:
        return max(lines, key=lambda line: (_path_length(line), _geometry_key(line))).tolist(), True
    current = starts[0]
    used, result = set(), []
    while current//2 not in used:
        index, entry = current//2, current % 2
        line = lines[index][::-1] if entry else lines[index]
        if result:
            result.extend(line[1:] if np.linalg.norm(np.asarray(result[-1])-line[0]) <= 1e-9 else line)
        else:
            result.extend(line)
        used.add(index)
        exit_endpoint = 2*index+(1-entry)
        if exit_endpoint not in links:
            break
        _, target, _ = links[exit_endpoint]
        current = target
    if len(used) != len(lines):
        return max(lines, key=lambda line: (_path_length(line), _geometry_key(line))).tolist(), True
    return np.asarray(result).tolist(), False


def merge_fragments(instances, p):
    """Merge uniquely associated verified pieces, preserving support semantics."""
    if len(instances) < 2:
        result = []
        for original in instances:
            if original.get("_associationOnly"):
                continue
            item = deepcopy(original)
            item.pop("_associationOnly", None); item.pop("_rawSupportIndices", None)
            result.append(item)
        return result
    lines = [_line(item) for item in instances]
    valid = [i for i, line in enumerate(lines) if line is not None]
    union = _Union(len(instances))
    endpoint_pairs = []
    association_only = [i for i in valid if instances[i].get("_associationOnly")]
    ordinary = [i for i in valid if i not in association_only]
    # Establish finite ordinary continuity before evaluating overlap identity.
    # A hook's turn is therefore proven by endpoint evidence, not by a broad
    # overlapping surface stripe.
    if len(ordinary) > 1:
        local = [instances[i] for i in ordinary]
        local_lines = [lines[i] for i in ordinary]
        for left, right in _endpoint_links(local, local_lines, p):
            global_left, global_right = ordinary[left//2], ordinary[right//2]
            union.join(global_left, global_right)
            endpoint_pairs.append((global_left, left % 2, global_right, right % 2))
    # Association-only candidates are support-transfer evidence, never direct
    # geometry edges. In particular, a stripe crossing two close rods must not
    # join them before its stricter association evidence is considered.
    endpoint_component = {item: union.find(item) for item in ordinary}
    overlap_pairs = []
    targets = {item: [] for item in ordinary}
    for at, left in enumerate(ordinary):
        for right in ordinary[at+1:]:
            if _compatible(instances[left], instances[right]) and _overlap_same_axis(instances[left], instances[right]):
                overlap_pairs.append((left, right)); targets[left].append(right); targets[right].append(left)
    allowed_components = {}
    for item, neighbours in targets.items():
        # A connected measured path can be a hook even when the global
        # endpoint matcher has not selected its turn. Only disconnected raw
        # observations need protection from an inferred bridge joining rods.
        if _observed_paths_connected(instances[item], p):
            continue
        components = {endpoint_component[other] for other in neighbours}
        conflicting = any(_distinct_parallel_axes(instances[left], instances[right])
                          for left in neighbours for right in neighbours
                          if endpoint_component[left] != endpoint_component[right])
        if len(components) > 1 and conflicting:
            choices = [(int(instances[other].get("rawSupportCount", 0)), float(instances[other].get("length", 0.)),
                        _item_key(instances[other]), endpoint_component[other]) for other in neighbours]
            allowed_components[item] = min(choices, key=lambda value: (-value[0], -value[1], value[2]))[-1]
    for left, right in overlap_pairs:
        if (left in allowed_components and endpoint_component[right] != allowed_components[left]) or \
           (right in allowed_components and endpoint_component[left] != allowed_components[right]):
            continue
        union.join(left, right)
    # A disconnected candidate that spans incompatible endpoint components is
    # identity-only evidence. It may attach to its deterministic component for
    # bookkeeping, but neither raw count nor observed/inferred geometry may
    # leak into that physical parent.
    output_suppressed = set(allowed_components)
    # Verification can retain a low-unique candidate solely as a bridge.  Its
    # raw observations may establish parent ownership, but its stripe geometry
    # must never be emitted as observed support on the final instance.
    for bridge in association_only:
        targets = []
        for item in ordinary:
            if not _compatible(instances[bridge], instances[item]):
                continue
            overlap = _support_overlap(instances[bridge], instances[item])
            if overlap < .50:
                continue
            if _overlap_same_axis(instances[bridge], instances[item]):
                geometry = tuple(np.round(np.asarray(lines[item], dtype=float).ravel(), 7))
                targets.append((overlap, int(instances[item].get("rawSupportCount", 0)),
                                float(instances[item].get("length", 0.)), geometry, item))
        if targets:
            target = min(targets, key=lambda value: (-value[0], -value[1], -value[2], value[3]))[-1]
            union.join(bridge, target)
    groups = {}
    for index in range(len(instances)):
        groups.setdefault(union.find(index), []).append(index)
    result = []
    for indices in groups.values():
        output_indices = [i for i in indices if not instances[i].get("_associationOnly") and i not in output_suppressed]
        if not output_indices:
            continue
        if len(output_indices) == 1:
            item = deepcopy(instances[output_indices[0]])
            item.pop("_associationOnly", None); item.pop("_rawSupportIndices", None)
            result.append(item)
            continue
        primary = min(output_indices, key=lambda i: (-int(instances[i].get("rawSupportCount", 0)),
                                                     -float(instances[i].get("length", 0)), _item_key(instances[i])))
        combined = deepcopy(instances[primary])
        observed_lines = sorted(_merge_observed([path for i in output_indices for path in instances[i].get("observedSegments", [])], p), key=_geometry_key)
        observed = [{"points": line.tolist()} for line in observed_lines]
        inferred = [deepcopy(path) for i in output_indices for path in instances[i].get("inferredSegments", [])]
        association_links = []
        for left, left_end, right, right_end in sorted(endpoint_pairs, key=lambda pair: (
                _geometry_key(lines[pair[0]][[0, -1][pair[1]]][None, :]),
                _geometry_key(lines[pair[2]][[0, -1][pair[3]]][None, :]))):
            if left not in output_indices or right not in output_indices:
                continue
            a, b = lines[left][[0, -1][left_end]], lines[right][[0, -1][right_end]]
            if tuple(a) > tuple(b): a, b = b, a
            association_links.append({"points": [a.tolist(), b.tolist()], "source": "unique-fragment-association"})
            if np.linalg.norm(a-b) > p.observed_join_gap:
                inferred.append({"points": [a.tolist(), b.tolist()], "source": "unique-fragment-association"})
        weights = np.asarray([max(float(instances[i].get("length", 0)), 1e-9) for i in output_indices])
        radii = np.asarray([float(instances[i]["radius"]) for i in output_indices])
        confidence = np.asarray([float(instances[i].get("confidence", .5)) for i in output_indices])
        raw_count = _raw_support_union_count(instances, output_indices)
        inferred = sorted(inferred, key=_path_key)
        association_links = sorted(association_links, key=_path_key)
        centreline, pending = _stitch(_straight_union_lines(observed_lines, p), inferred+association_links, p)
        combined.update(observedSegments=observed, inferredSegments=inferred, centerline=centreline,
                        length=_observed_length(observed_lines, p),
                        radius=float(np.average(radii, weights=weights)), confidence=float(np.average(confidence, weights=weights)),
                        rawSupportCount=raw_count, evidence="mixed" if inferred else "observed")
        if pending:
            combined["associationPending"] = True
        combined.pop("_associationOnly", None); combined.pop("_rawSupportIndices", None)
        result.append(combined)
    return result
