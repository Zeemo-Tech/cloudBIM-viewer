"""Conservative removal of fixture material stuck to observed rebar ends.

This pass deliberately works on the Step 06 result.  It does not use design
endpoints as cutters: design diameter only supplies a fixed-radius constraint
for an independently observed, central cylinder.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass

import numpy as np
from scipy.spatial import cKDTree

from .rebar_tracks import _fixed_radius

VERSION = "rebar-terminal-cleanup-v3-fragment-contacts"
ATTRIBUTES = {"terminal_removed": "u1", "terminal_reason": "u1",
              "terminal_previous_instance": "<u4", "terminal_previous_segment": "<u4",
              "terminal_fragment": "<u4", "terminal_origin": "u1"}


@dataclass(frozen=True)
class TerminalCleanupPolicy:
    minimum_body_points: int = 24
    minimum_fixture_neighbors: int = 3
    maximum_terminal_zone_m: float = .040
    terminal_fraction: float = .20
    fixture_radius_m: float = .010
    radial_tolerance_m: float = .0015
    radius_scatter_fraction: float = .35
    crossing_protection_m: float = .008


def _empty_output(count, output):
    output = output if output is not None else {}
    for name, dtype in ATTRIBUTES.items():
        if name not in output:
            output[name] = np.zeros(count, dtype=dtype)
        elif np.asarray(output[name]).shape != (count,):
            raise ValueError(f"{name} must align with source rows")
    return output


def _unit_by_instance(complete_report, inventory):
    units = {u.get("designUnitId"): u for u in inventory.get("units", [])}
    result = {}
    for item in complete_report.get("instances", []):
        unit = units.get(item.get("designUnitId"))
        if unit and np.isfinite(unit.get("diameterM", np.nan)) and unit["diameterM"] > 0:
            result[int(item["id"])] = unit
    return result


def _curved_clusters(complete_report):
    """Only Step 06's measured curved-exterior atoms are immutable.

    A design polyline also represents ordinary web members, so it is not shape
    evidence by itself and must not disable this pass.
    """
    return {int(c["id"]) for c in complete_report.get("clusters", [])
            if c.get("category") == "curved-exterior"}


def _fit_body(xyz, radius, policy):
    """Fixed-radius robust fit to the central observed body, never a nominal axis."""
    center = xyz.mean(0)
    _, vectors = np.linalg.eigh((xyz-center).T @ (xyz-center))
    axis = vectors[:, -1]
    t = (xyz-center) @ axis
    # The middle 60% excludes either terminal without assuming which is dirty.
    core = xyz[(t >= np.quantile(t, .20)) & (t <= np.quantile(t, .80))]
    if len(core) < policy.minimum_body_points:
        return None
    c = core.mean(0); tc = (core-c) @ axis
    model = dict(center=c, axis=axis, low=float(tc.min()), high=float(tc.max()), radius=radius)
    fitted = _fixed_radius(core, model, radius)
    if not np.isfinite(fitted["fitMedianErrorM"]) or fitted["fitMedianErrorM"] > max(.0006, radius*.20):
        return None
    axis, center = fitted["axis"], fitted["center"]
    t = (xyz-center) @ axis
    radial = np.linalg.norm((xyz-center)-t[:, None]*axis, axis=1)
    # A clean central body must actually support the supplied diameter.
    tol = max(policy.radial_tolerance_m, radius*policy.radius_scatter_fraction)
    support = np.abs(radial-radius) <= tol
    if np.count_nonzero(support & (t >= np.quantile(t, .25)) & (t <= np.quantile(t, .75))) < policy.minimum_body_points:
        return None
    return center, axis, t, radial, tol


def _fit_terminal_support(xyz, radius, policy):
    """Fit one end's inward observed collar with the fixed design radius."""
    if len(xyz) < policy.minimum_body_points:
        return None
    center = xyz.mean(0)
    _, vectors = np.linalg.eigh((xyz-center).T @ (xyz-center))
    axis = vectors[:, -1]
    along = (xyz-center) @ axis
    model = dict(center=center, axis=axis, low=float(along.min()), high=float(along.max()), radius=radius)
    fitted = _fixed_radius(xyz, model, radius)
    tol = max(policy.radial_tolerance_m, radius*policy.radius_scatter_fraction)
    if not np.isfinite(fitted["fitMedianErrorM"]) or fitted["fitMedianErrorM"] > max(.0006, radius*.20):
        return None
    delta = xyz-fitted["center"]; along = delta@fitted["axis"]
    radial = np.linalg.norm(delta-along[:, None]*fitted["axis"], axis=1)
    if np.count_nonzero(np.abs(radial-radius) <= tol) < policy.minimum_body_points:
        return None
    return fitted["center"], fitted["axis"], tol


def _rebuild(report, classes, instances, segments):
    report = deepcopy(report)
    original_noise = int(report.get("counts", {}).get("noise", 0))
    alive = classes == 3
    instance_ids, instance_counts = np.unique(instances[alive & (instances > 0)], return_counts=True)
    segment_ids, segment_counts = np.unique(segments[alive & (segments > 0)], return_counts=True)
    instance_count = dict(zip(map(int, instance_ids), map(int, instance_counts)))
    segment_count = dict(zip(map(int, segment_ids), map(int, segment_counts)))
    for item in report.get("instances", []):
        ident = int(item.get("id", 0)); item["pointCount"] = instance_count.get(ident, 0)
    report["instances"] = [i for i in report.get("instances", []) if i.get("pointCount", 0)]
    for item in report.get("segments", []):
        ident = int(item.get("id", 0)); item["pointCount"] = segment_count.get(ident, 0)
    report["segments"] = [s for s in report.get("segments", []) if s.get("pointCount", 0)]
    for item in report["instances"]:
        if "segmentIds" in item:
            item["segmentIds"] = [segment["id"] for segment in report["segments"] if segment.get("instanceId") == item["id"]]
    report["counts"] = dict(zip(("table", "fixture", "rebar", "noise"), map(int, np.bincount(classes, minlength=5)[1:5])))
    report["instanceCount"] = len(report["instances"])
    # These aliases occur in exported summaries; retain their meaning when a
    # producer included them rather than silently leaving stale counts behind.
    for item in report["instances"] + report["segments"]:
        if "rebarPoints" in item:
            item["rebarPoints"] = item["pointCount"]
    if "rebarPoints" in report:
        report["rebarPoints"] = report["counts"]["rebar"]
    if "unassignedRebarPointCount" in report:
        report["unassignedRebarPointCount"] = int(np.count_nonzero(alive & (instances == 0)))
    review = report.get("designReview")
    if isinstance(review, dict) and "filteredPoints" in review:
        review["filteredPoints"] = int(review["filteredPoints"]) + report["counts"]["noise"] - original_noise
    return report


def clean_terminals(context, complete_report, inventory, *, policy=None, workers=1, output=None, progress=None):
    """Remove only fixture-supported, radially incompatible points at both ends."""
    policy = policy or TerminalCleanupPolicy(); progress = progress or (lambda *args: None)
    positions = np.asarray(context.positions); count = len(positions)
    if getattr(context, "complete_class", None) is None:
        raise ValueError("terminal cleanup requires Step 06 complete arrays")
    out = _empty_output(count, output)
    classes = context.complete_class; owners = context.complete_instance; segments = context.complete_segment
    fixture_class = getattr(context, "refined_class", None)
    if fixture_class is None: fixture_class = getattr(context, "fused_class", None)
    fixture_rows = np.flatnonzero(np.asarray(fixture_class) == 2) if fixture_class is not None else np.empty(0, int)
    report = dict(version=VERSION, inputPolicy="Step 06 complete_class == 3 only; source XYZ and earlier arrays immutable",
                  removedPointCount=0, reviewedInstanceCount=0, skippedInstanceCount=0, decisions=[], policy=asdict(policy))
    if not len(fixture_rows):
        report["skippedReason"] = "no_measured_fixture_support"
        rebuilt = _rebuild(complete_report, classes, owners, segments)
        rebuilt["terminalCleanup"] = report
        return rebuilt
    fixture_tree = cKDTree(positions[fixture_rows])
    units = _unit_by_instance(complete_report, inventory)
    curved_clusters = _curved_clusters(complete_report)
    retained = (classes == 3) & (owners > 0)
    retained_rows = np.flatnonzero(retained)
    retained_tree = cKDTree(positions[retained_rows])
    retained_owners = owners[retained_rows]
    cluster_labels = getattr(context, "complete_cluster", np.zeros(count, np.uint32))
    curved_rows = np.isin(cluster_labels, list(curved_clusters)) if curved_clusters else np.zeros(count, bool)
    for owner in np.unique(owners[retained]):
        owner = int(owner); rows = np.flatnonzero(retained & (owners == owner)); unit = units.get(owner)
        # Web runs are a distinct design family: fixtures commonly meet their
        # nodes, and this terminal pass is intentionally limited to ordinary
        # and short bars.
        if unit is None or unit.get("kind") == "web":
            report["skippedInstanceCount"] += 1; continue
        straight_rows = rows[~curved_rows[rows]]
        if len(straight_rows) < policy.minimum_body_points:
            report["skippedInstanceCount"] += 1; continue
        fit = _fit_body(positions[straight_rows], float(unit["diameterM"])/2, policy)
        if fit is None:
            report["skippedInstanceCount"] += 1; continue
        center, axis, t, radial, tolerance = fit; length = float(t.max()-t.min())
        zone = min(policy.maximum_terminal_zone_m, length*policy.terminal_fraction)
        if zone <= 0:
            report["skippedInstanceCount"] += 1; continue
        report["reviewedInstanceCount"] += 1
        selected_parts = []
        radius = float(unit["diameterM"])/2
        # The global observed fit is used only to divide ends from body.  Each
        # end is judged against its own immediately inward collar, which keeps
        # a gently bent bar from looking radially broad at one end.
        for name, end_rows, support_rows in (
            ("low", straight_rows[t <= t.min()+zone], straight_rows[(t >= t.min()+zone) & (t <= t.min()+zone+.080)]),
            ("high", straight_rows[t >= t.max()-zone], straight_rows[(t <= t.max()-zone) & (t >= t.max()-zone-.080)]),
        ):
            local = _fit_terminal_support(positions[support_rows], radius, policy)
            if local is None:
                continue
            local_center, local_axis, local_tolerance = local
            delta = positions[end_rows]-local_center; local_t = delta@local_axis
            local_radial = np.linalg.norm(delta-local_t[:, None]*local_axis, axis=1)
            # A round end cap fills the cylinder interior. It is supported
            # steel, not radial contamination, even when a fixture is nearby.
            candidates = end_rows[local_radial > radius+local_tolerance]
            if not len(candidates):
                continue
            fixture_counts = fixture_tree.query_ball_point(positions[candidates], policy.fixture_radius_m,
                                                            return_length=True)
            selected_parts.append(candidates[np.asarray(fixture_counts) >= policy.minimum_fixture_neighbors])
        selected = np.unique(np.concatenate(selected_parts)) if selected_parts else np.empty(0, dtype=np.int64)
        if len(selected):
            # Crossing rods are independent observed support, not fixture glue.
            k = min(64, len(retained_rows))
            distances, neighbors = retained_tree.query(positions[selected], k=k,
                distance_upper_bound=policy.crossing_protection_m, workers=workers)
            distances = np.atleast_2d(distances); neighbors = np.atleast_2d(neighbors)
            valid = neighbors < len(retained_rows)
            other_owner = np.zeros(len(selected), bool)
            for index in range(len(selected)):
                neighbor_owners = retained_owners[neighbors[index, valid[index]]]
                other_owner[index] = np.any(neighbor_owners != owner)
            selected = selected[~other_owner]
        if not len(selected):
            continue
        out["terminal_previous_instance"][selected] = owners[selected]
        out["terminal_previous_segment"][selected] = segments[selected]
        out["terminal_removed"][selected] = 1; out["terminal_reason"][selected] = 1
        classes[selected] = 4; owners[selected] = 0; segments[selected] = 0
        context.complete_confidence[selected] = 0
        report["removedPointCount"] += int(len(selected))
        report["decisions"].append(dict(instanceId=owner, pointCount=int(len(selected)), reason="fixture_supported_radial_terminal_contradiction", terminalZoneM=zone))
        progress("第 06 步：清理端部工装残留", report["removedPointCount"], count)
    report["removedInstanceCount"] = 0
    rebuilt = _rebuild(complete_report, classes, owners, segments)
    rebuilt["terminalCleanup"] = report
    return rebuilt
