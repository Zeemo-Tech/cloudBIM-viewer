"""Fragment-local, evidence-only terminal review.

Unlike :mod:`rebar_terminal_cleanup`, this module never changes a pipeline
context.  It reviews separate internal and exterior observations before ownership joins
them, and can revisit their preserved source groups after reconciliation.
"""
from dataclasses import asdict

import numpy as np
from scipy.spatial import cKDTree

from .rebar_terminal_cleanup import (
    TerminalCleanupPolicy, _fit_terminal_support,
)


VERSION = "rebar-fragment-terminals-v1"


def _fixture_rows(context, count):
    classes = getattr(context, "refined_class", None)
    if classes is None:
        classes = getattr(context, "fused_class", None)
    if classes is None:
        return np.empty(0, dtype=np.int64)
    values = np.asarray(classes)
    return np.flatnonzero(values[:count] == 2).astype(np.int64, copy=False)


def _radius(group, inventory, points):
    """Propose a design diameter by observed lane; the local collar validates it."""
    units = [u for u in inventory.get('units',[]) if u.get('kind')!='web' and u.get('diameterM',0)>0]
    hint = group.get('diameterM')
    if hint is not None:
        try: hint=float(hint)
        except (TypeError,ValueError): return None
        if not np.isfinite(hint) or hint<=0:return None
        # Explicit matched inputs used by isolated tooling may carry no inventory.
        if not units:return hint/2
        nearest=min(units,key=lambda u:abs(u['diameterM']-hint))['diameterM']
        return nearest/2 if abs(nearest-hint)<=.001 else None
    if len(points)<24:return None
    center=points.mean(0)
    _,v=np.linalg.eigh((points-center).T@(points-center));axis=v[:,-1]
    proposals=[]
    for unit in units:
        if 'startM' not in unit or 'endM' not in unit:continue
        start,end=np.asarray(unit['startM']),np.asarray(unit['endM'])
        delta=end-start;length=np.linalg.norm(delta)
        if length<=0:continue
        direction=delta/length;alignment=abs(float(axis@direction))
        if alignment<np.cos(np.deg2rad(15)):continue
        relative=center-start;along=float(relative@direction)
        offset=float(np.linalg.norm(relative-along*direction))
        if unit.get('kind')=='short':
            offset=abs(float(center[2]-(start[2]+end[2])/2))
            if offset>.010:continue
        elif offset>.025 or along<-.080 or along>length+.080:continue
        proposals.append((offset+.02*(1-alignment),unit['diameterM']/2))
    return min(proposals)[1] if proposals else None


def _fragments(points, minimum_gap=.012):
    """Return contiguous axial components, splitting only measured empty gaps."""
    center = points.mean(0)
    _, vectors = np.linalg.eigh((points - center).T @ (points - center))
    axis = vectors[:, -1]
    t = (points - center) @ axis
    # Ring samples share nearly equal t.  Coalesce them before deriving local
    # density so a dense ring does not hide a real 12 mm gap.
    levels = np.unique(np.round(t, 5))
    gaps = np.diff(levels)
    positive = gaps[gaps > 1e-5]
    # A gap must beat both the physical lower bound and ordinary axial sample
    # spacing.  A large multiplier would miss a real contact gap on sparse
    # scans, which is precisely where global extrema are least useful.
    density_gap = 3.0 * float(np.median(positive)) if len(positive) else minimum_gap
    threshold = max(minimum_gap, density_gap)
    # Midpoints ensure neither side of a measured gap loses a boundary ring.
    cuts = (levels[:-1] + levels[1:]) / 2.0
    cuts = cuts[gaps >= threshold]
    labels = np.searchsorted(cuts, t, side="right")
    return axis, t, labels, cuts, threshold


def _near_other_support(tree, point_group, group_index, points, radius, workers):
    if tree is None:
        return np.zeros(len(points), dtype=bool)
    distances, near = tree.query(points, k=min(32, len(point_group)), distance_upper_bound=radius, workers=workers)
    distances = np.atleast_2d(distances)
    near = np.atleast_2d(near)
    answer = np.zeros(len(points), dtype=bool)
    for i, neighbors in enumerate(near):
        valid = neighbors < len(point_group)
        answer[i] = bool(np.any(point_group[neighbors[valid]] != group_index))
    return answer


def review_fragment_terminals(context, groups, inventory, *, workers=1, policy=None):
    """Review contact contamination at *observed* fragment ends.

    ``removed_mask`` and ``fragment_labels`` align with ``context.positions``.
    A positive label means the corresponding eligible source row was assigned
    to an observed contiguous fragment.  The returned mask is only a proposal;
    callers retain ownership of applying it to context arrays.
    """
    policy = policy or TerminalCleanupPolicy()
    positions = np.asarray(context.positions, dtype=float)
    count = len(positions)
    removed = np.zeros(count, dtype=bool)
    labels = np.zeros(count, dtype=np.uint32)
    fixture_rows = _fixture_rows(context, count)
    report = dict(version=VERSION, policy=asdict(policy), sourceCount=len(groups), fragmentCount=0,
                  reviewedEndpointCount=0, removedPointCount=0, skippedSourceCount=0, decisions=[], sources=[], fragments=[])
    fixture_tree = cKDTree(positions[fixture_rows]) if len(fixture_rows) else None
    fixture_set = np.zeros(count, dtype=bool)
    fixture_set[fixture_rows] = True

    # Build this once.  It protects a candidate from a separately observed rod
    # at crossings/branches without a quadratic group-by-group search.
    all_rows, all_groups = [], []
    normalized = []
    for gi, group in enumerate(groups):
        rows = np.asarray(group.get("rows", ()), dtype=np.int64)
        rows = np.unique(rows[(rows >= 0) & (rows < count)])
        normalized.append(rows)
        all_rows.append(rows); all_groups.append(np.full(len(rows), gi, dtype=np.int32))
    support_rows = np.concatenate(all_rows) if all_rows else np.empty(0, dtype=np.int64)
    support_groups = np.concatenate(all_groups) if all_groups else np.empty(0, dtype=np.int32)
    support_tree = cKDTree(positions[support_rows]) if len(support_rows) else None

    next_label = 1
    for gi, (group, rows) in enumerate(zip(groups, normalized)):
        kind = group.get("kind")
        source = dict(sourceId=group.get("sourceId"), origin=group.get("origin"), kind=kind,
                      pointCount=int(len(rows)), fragments=0, removedPointCount=0, skippedReason=None)
        report["sources"].append(source)
        if kind == "web" or kind == "curved" or len(rows) < policy.minimum_body_points:
            source["skippedReason"] = "web_or_curved_or_sparse"; report["skippedSourceCount"] += 1; continue
        # Stable geometry ordering prevents tiny floating accumulation changes
        # in PCA/fixed-radius fits when a producer permutes source rows.
        order = np.lexsort((positions[rows, 2], positions[rows, 1], positions[rows, 0]))
        rows = rows[order]
        points = positions[rows]
        radius = _radius(group, inventory, points)
        if radius is None:
            source["skippedReason"] = "no_conservative_radius"; report["skippedSourceCount"] += 1; continue
        axis, t, part, cuts, gap_threshold = _fragments(points)
        parts = np.unique(part)
        # A fixture smear can make a dense, flat component in the empty gap.
        # It is evidence to test, not an observed rod fragment.  Requiring an
        # appreciable axial run also prevents arbitrary ring/sample boundaries
        # from becoming persisted fragment ids.
        structural = [item for item in parts if float(np.ptp(t[part == item])) >= .040]
        source["fragments"] = int(len(structural)); source["radiusM"] = radius; source["gapThresholdM"] = gap_threshold
        source["fitRadiusM"] = radius
        report["fragmentCount"] += len(structural)
        structural_labels = []
        for item in structural:
            fragment_rows = rows[part == item]
            labels[fragment_rows] = next_label
            report["fragments"].append(dict(id=next_label, origin=group.get("origin"), sourceId=group.get("sourceId"),
                pointCount=int(len(fragment_rows)), axialLengthM=float(np.ptp(t[part == item])), radiusM=radius))
            structural_labels.append((item, next_label))
            next_label += 1
        # Keep every eligible, untouched source row traceable even if PCA
        # leaves a tiny end ring on the far side of a density cut.  Fixture
        # rows deliberately receive no survivor label.
        unlabeled = (labels[rows] == 0) & ~fixture_set[rows]
        if structural_labels and np.any(unlabeled):
            item_centers = np.array([np.median(t[part == item]) for item, _ in structural_labels])
            item_ids = np.array([ident for _, ident in structural_labels], dtype=np.uint32)
            nearest = np.argmin(np.abs(t[unlabeled, None] - item_centers[None, :]), axis=1)
            labels[rows[unlabeled]] = item_ids[nearest]
        # Every independently observed fragment has two local ends.  This is
        # essential before internal/exterior observations are merged; paired
        # gap faces are simply reviewed twice, once from each fragment.
        for component in structural:
            for face in ("low", "high"):
                local_rows = rows[part == component]
                local_t = t[part == component]
                length = float(local_t.max() - local_t.min())
                zone = min(policy.maximum_terminal_zone_m, length * policy.terminal_fraction)
                if zone <= 0:
                    continue
                if face == "high":
                    end_limit = float(local_t.max())
                    # Include an outward collar in the measured empty gap:
                    # contamination is often no longer part of the clean
                    # component after pre-merge clustering.
                    end = rows[(t >= end_limit - zone) & (t <= end_limit + zone)]
                    collar = local_rows[(local_t >= local_t.max() - zone - .080) & (local_t < local_t.max() - zone)]
                else:
                    end_limit = float(local_t.min())
                    end = rows[(t >= end_limit - zone) & (t <= end_limit + zone)]
                    collar = local_rows[(local_t <= local_t.min() + zone + .080) & (local_t > local_t.min() + zone)]
                local_fit = _fit_terminal_support(positions[collar], radius, policy)
                if local_fit is None:
                    continue
                report["reviewedEndpointCount"] += 1
                center, local_axis, tolerance = local_fit
                delta = positions[end] - center
                axial = delta @ local_axis
                radial = np.linalg.norm(delta - axial[:, None] * local_axis, axis=1)
                candidates = end[radial > radius + tolerance]
                if not len(candidates):
                    continue
                if fixture_tree is None:
                    continue
                counts = fixture_tree.query_ball_point(positions[candidates], policy.fixture_radius_m, return_length=True)
                candidates = candidates[np.asarray(counts) >= policy.minimum_fixture_neighbors]
                candidates = candidates[~fixture_set[candidates]]
                if not len(candidates):
                    continue
                # Independent observed support wins over the fixture proposal.
                protected = _near_other_support(support_tree, support_groups, gi, positions[candidates],
                                                 policy.crossing_protection_m, workers)
                candidates = candidates[~protected]
                if not len(candidates):
                    continue
                candidates = candidates[~removed[candidates]]
                if not len(candidates): continue
                removed[candidates] = True
                source["removedPointCount"] += int(len(candidates))
                report["decisions"].append(dict(sourceId=group.get("sourceId"), origin=group.get("origin"),
                    face=face, pointCount=int(len(candidates)), radiusM=radius, fitToleranceM=float(tolerance),
                    reason="fixture_supported_outward_radial_fragment_terminal"))
    report["removedPointCount"] = int(np.count_nonzero(removed))
    return removed, labels, report
