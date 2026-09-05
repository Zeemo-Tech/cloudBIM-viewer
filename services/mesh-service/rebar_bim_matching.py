"""Associate observed stock bars and infer only uniquely supported design gaps.

This module never creates an observed instance from design geometry. Missing
segments remain outside observedSegments, the sole point-projection support.
"""

from __future__ import annotations

from copy import deepcopy
import numpy as np


def _line(value):
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if (
        array.ndim != 2
        or array.shape[1:] != (3,)
        or len(array) < 2
        or not np.isfinite(array).all()
    ):
        return None
    keep = np.r_[True, np.linalg.norm(np.diff(array, axis=0), axis=1) > 1e-9]
    return array[keep] if keep.sum() >= 2 else None


def _sample(line, spacing=0.025):
    vectors = np.diff(line, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    locations = np.linspace(
        0, cumulative[-1], min(256, max(12, int(cumulative[-1] / spacing) + 1))
    )
    index = np.minimum(
        np.searchsorted(cumulative, locations, side="right") - 1, len(lengths) - 1
    )
    fraction = (locations - cumulative[index]) / lengths[index]
    return line[index] + fraction[:, None] * vectors[index], vectors[index] / lengths[
        index, None
    ]


def _project(points, line):
    vectors = np.diff(line, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    # Design curves are capped at 4096 segments by extraction. Chunking bounds
    # temporary memory independently of the number of observed instances.
    distances = []
    arcs = []
    nearest = []
    tangents = []
    for start in range(0, len(points), 64):
        query = points[start : start + 64]
        relative = query[:, None, :] - line[None, :-1, :]
        fraction = np.clip(
            np.einsum("nsi,si->ns", relative, vectors) / lengths**2, 0, 1
        )
        projection = line[None, :-1, :] + fraction[:, :, None] * vectors
        squared = np.sum((query[:, None, :] - projection) ** 2, axis=2)
        choice = np.argmin(squared, axis=1)
        rows = np.arange(len(query))
        distances.extend(np.sqrt(squared[rows, choice]))
        arcs.extend(cumulative[choice] + fraction[rows, choice] * lengths[choice])
        nearest.extend(projection[rows, choice])
        tangents.extend(vectors[choice] / lengths[choice, None])
    return (
        np.asarray(distances),
        np.asarray(arcs),
        np.asarray(nearest),
        np.asarray(tangents),
    )


def _paths(instance):
    raw = instance.get("observedSegments")
    if raw is None:
        raw = [{"points": instance.get("centerline", [])}]
    lines = []
    for segment in raw:
        value = segment.get("points", []) if isinstance(segment, dict) else segment
        line = _line(value)
        if line is not None:
            lines.append(line)
    return lines


def match_bim_instances(
    instances, prior, *, match_gate=0.06, registration_uncertainty=0.02
):
    """Mutate instances with conservative design IDs and optional gap mergers."""
    diagnostics = {
        "usage": "unique-association-and-observed-fragment-gaps",
        "designCandidateCount": 0,
        "matchedCount": 0,
        "ambiguousCount": 0,
        "inferredGapCount": 0,
        "mergedFragmentCount": 0,
        "registrationUncertainty": registration_uncertainty,
    }
    bars = []
    for bar in (prior or {}).get("bars", []):
        line = _line(bar.get("points", []))
        if line is not None:
            bars.append((bar, line))
    diagnostics["designCandidateCount"] = len(bars)
    if not bars or not instances:
        return diagnostics
    proposals = {}
    competition_gate = match_gate + registration_uncertainty
    for index, instance in enumerate(instances):
        paths = _paths(instance)
        if not paths:
            continue
        samples = [_sample(line) for line in paths]
        points = np.concatenate([row[0] for row in samples])
        tangents = np.concatenate([row[1] for row in samples])
        observed_length = sum(
            np.linalg.norm(np.diff(line, axis=0), axis=1).sum() for line in paths
        )
        if observed_length < 0.08:
            continue
        candidates = []
        for design_index, (bar, design) in enumerate(bars):
            # Bounding boxes conservatively reject distant design rods first.
            if np.any(
                points.min(axis=0) > design.max(axis=0) + competition_gate
            ) or np.any(points.max(axis=0) < design.min(axis=0) - competition_gate):
                continue
            distance, arc, nearest, direction = _project(points, design)
            if np.quantile(np.abs(np.sum(direction * tangents, axis=1)), 0.2) < np.cos(
                np.deg2rad(25)
            ):
                continue
            cost = float(np.quantile(distance, 0.9))
            if cost > competition_gate:
                continue
            radius = float(bar.get("radius", instance.get("radius", 0.004)))
            if not np.isfinite(radius) or radius <= 0:
                continue
            if abs(radius - float(instance.get("radius", radius))) > max(
                0.004, radius * 0.65
            ):
                continue
            endpoints = np.vstack([paths[0][0], paths[-1][-1]])
            _, end_arcs, _, _ = _project(endpoints, design)
            candidates.append(
                (
                    cost,
                    design_index,
                    float(arc.min()),
                    float(arc.max()),
                    np.median(points - nearest, axis=0),
                    endpoints,
                    end_arcs,
                )
            )
        candidates.sort(key=lambda row: (row[0], row[1]))
        if not candidates:
            continue
        if candidates[0][0] > match_gate:
            continue
        if (
            len(candidates) > 1
            and candidates[1][0] - candidates[0][0] <= registration_uncertainty
        ):
            diagnostics["ambiguousCount"] += 1
            continue
        proposal = candidates[0]
        proposals.setdefault(proposal[1], []).append((index, proposal))
    replacements = {}
    removed = set()
    for design_index, group in proposals.items():
        bar, design = bars[design_index]
        group.sort(key=lambda row: row[1][2])
        # Overlapping scan instances competing for the same part of one design
        # rod remain unresolved; one-to-one matching must not collapse them.
        conflicts = set()
        for left, (i, a) in enumerate(group):
            for j, b in group[left + 1 :]:
                if min(a[3], b[3]) - max(a[2], b[2]) > 0.008:
                    conflicts.update((i, j))
        diagnostics["ambiguousCount"] += len(conflicts)
        accepted = []
        for index, proposal in group:
            if index in conflicts:
                continue
            instance = instances[index]
            instance["designId"] = str(bar.get("id") or bar.get("ifcGlobalId"))
            instance["ifcGlobalId"] = bar.get("ifcGlobalId")
            instance["bimResidualM"] = proposal[0]
            diagnostics["matchedCount"] += 1
            accepted.append((index, proposal))
        # Both endpoint support and a consistent local displacement are required
        # before a missing design interval can connect two observed fragments.
        chains = []
        for row in accepted:
            if not chains:
                chains.append([row])
                continue
            previous = chains[-1][-1]
            gap = row[1][2] - previous[1][3]
            offset_difference = np.linalg.norm(row[1][4] - previous[1][4])
            if (
                gap > 0.008
                and gap <= 0.5
                and offset_difference <= max(0.008, float(bar.get("radius", 0.004)) * 2)
            ):
                chains[-1].append(row)
            else:
                chains.append([row])
        cumulative = np.r_[
            0.0, np.cumsum(np.linalg.norm(np.diff(design, axis=0), axis=1))
        ]
        for chain in chains:
            if len(chain) < 2:
                continue
            target = chain[0][0]
            merged = deepcopy(instances[target])
            observed = []
            inferred = []
            complete = []
            for ordinal, (index, proposal) in enumerate(chain):
                item = instances[index]
                paths = _paths(item)
                if proposal[6][0] > proposal[6][1]:
                    paths = [line[::-1] for line in paths[::-1]]
                if ordinal:
                    previous = chain[ordinal - 1][1]
                    offset = (previous[4] + proposal[4]) / 2
                    middle = (
                        design[(cumulative > previous[3]) & (cumulative < proposal[2])]
                        + offset
                    )
                    bridge = np.vstack([complete[-1], middle, paths[0][0]])
                    inferred.append(
                        {
                            "points": bridge.tolist(),
                            "source": "bim",
                            "designId": merged["designId"],
                        }
                    )
                    complete.extend(bridge[1:])
                    removed.add(index)
                for line in paths:
                    observed.append({"points": line.tolist()})
                    complete.extend(line)
                inferred.extend(item.get("inferredSegments", []))
            merged.update(
                observedSegments=observed,
                inferredSegments=inferred,
                centerline=np.asarray(complete).tolist(),
                evidence="mixed",
                rawSupportCount=sum(
                    int(instances[i].get("rawSupportCount", 0)) for i, _ in chain
                ),
                pointCount=sum(
                    int(instances[i].get("pointCount", 0)) for i, _ in chain
                ),
                length=float(
                    sum(
                        np.linalg.norm(
                            np.diff(np.asarray(s["points"]), axis=0), axis=1
                        ).sum()
                        for s in observed
                    )
                ),
            )
            replacements[target] = merged
            diagnostics["mergedFragmentCount"] += len(chain) - 1
            diagnostics["inferredGapCount"] += len(chain) - 1
    instances[:] = [
        replacements.get(i, item)
        for i, item in enumerate(instances)
        if i not in removed
    ]
    for identifier, instance in enumerate(instances, 1):
        instance["id"] = identifier
    return diagnostics
