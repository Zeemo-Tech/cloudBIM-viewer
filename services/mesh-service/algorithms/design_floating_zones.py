"""Continuous design-guided outer envelope with legacy OBB report support.

The closed triangulated shell encloses the whole cage, including layer gaps.
Within the finite review domain, eligible points outside this same displayed
surface are forbidden. Legacy OBB membership remains readable for old reports.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class FloatingZoneParameters:
    """Metre-valued surface clearances for the shared design cloth."""
    in_plane_half_width_m: float = 0.005
    # A centerline slab must contain the physical bar surface.  Nearby design
    # layers may therefore overlap; layer ids are evidence/display metadata,
    # never a reason to delete material in their physical overlap.
    layer_half_height_m: float = 0.0035
    layer_registration_allowance_m: float = 0.004
    web_plane_half_width_m: float = 0.095
    web_plane_half_thickness_m: float = 0.045
    end_halo_m: float = 0.040
    external_length_tolerance_m: float = 0.012
    web_registration_allowance_m: float = 0.004
    hook_surface_allowance_m: float = 0.002
    envelope_grid_spacing_m: float = 0.008
    envelope_support_radius_m: float = 0.004
    envelope_relaxation_passes: int = 0
    envelope_expansion_m: float = 0.002
    review_margin_m: float = 0.22
    layer_cluster_gap_m: float = 0.004
    min_horizontal_run_m: float = 0.20
    query_chunk_size: int = 65536


def _array(value: Any) -> np.ndarray | None:
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    return result if result.ndim == 2 and result.shape[1:] == (3,) and len(result) >= 2 and np.isfinite(result).all() else None


def _unit_box(start: np.ndarray, end: np.ndarray, *, half_width: float, half_height: float,
              length_tolerance: float, kind: str, layer_id: int, identifier: str) -> dict[str, Any] | None:
    direction = end - start
    length = float(np.linalg.norm(direction))
    if length <= 1e-8:
        return None
    axis = direction / length
    # For nearly vertical members, use an arbitrary stable horizontal normal.
    side = np.cross(axis, np.array([0.0, 0.0, 1.0]))
    if np.linalg.norm(side) < 1e-7:
        side = np.cross(axis, np.array([1.0, 0.0, 0.0]))
    side /= np.linalg.norm(side)
    normal = np.cross(axis, side)
    if kind == "web":
        # A web is protected as a generous *plane*, broad across its local
        # lateral direction, rather than as a narrow cylindrical tube.
        half = [length / 2 + length_tolerance, half_width, half_height]
    else:
        # Horizontal/straight bars admit lane and height deviations.
        half = [length / 2 + length_tolerance, half_width, half_height]
    center = (start + end) * .5
    axes = np.vstack((axis, side, normal))
    vertices = [
        (center + axes.T @ (np.array([sx, sy, sz]) * half)).tolist()
        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
    ]
    planes = []
    for axis_index in range(3):
        for sign in (-1, 1):
            n = axes[axis_index] * sign
            point = center + n * half[axis_index]
            planes.append({"normal": n.tolist(), "offset": float(n @ point)})
    low, high = np.min(np.asarray(vertices), axis=0), np.max(np.asarray(vertices), axis=0)
    return {"id": identifier, "kind": "web-plane" if kind == "web" else "layer-slab",
            "layerId": int(layer_id), "centerM": center.tolist(), "axes": axes.tolist(),
            "halfSizeM": [float(v) for v in half], "aabbM": [low.tolist(), high.tolist()], "verticesM": vertices, "planes": planes}


def _halo(point: np.ndarray, radius: float, layer_id: int, identifier: str) -> dict[str, Any]:
    axes = np.eye(3)
    vertices = [(point + np.array([x, y, z]) * radius).tolist()
                for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    planes = [{"normal": (axes[i] * sign).tolist(), "offset": float((axes[i] * sign) @ (point + axes[i] * sign * radius))}
              for i in range(3) for sign in (-1, 1)]
    return {"id": identifier, "kind": "end-halo", "layerId": int(layer_id), "centerM": point.tolist(),
            "axes": axes.tolist(), "halfSizeM": [float(radius)] * 3, "aabbM": [(point-radius).tolist(), (point+radius).tolist()],
            "verticesM": vertices, "planes": planes}


def _layer_box(points: np.ndarray, *, layer_id: int, height: float, half_height: float,
               params: FloatingZoneParameters) -> dict[str, Any]:
    """One broad finite XY slab per coplanar design layer, never a bar tube."""
    low = points.min(axis=0); high = points.max(axis=0)
    low[:2] -= params.in_plane_half_width_m; high[:2] += params.in_plane_half_width_m
    low[2] = height-half_height; high[2] = height+half_height
    center, half = (low+high)*.5, (high-low)*.5
    axes = np.eye(3)
    vertices = [(center + np.array([x, y, z]) * half).tolist() for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    return {"id": f"layer-{layer_id}", "kind": "layer-plane", "layerId": int(layer_id), "centerM": center.tolist(),
            "axes": axes.tolist(), "halfSizeM": half.tolist(), "aabbM": [low.tolist(), high.tolist()], "verticesM": vertices,
            "planes": [{"normal": (axes[i]*s).tolist(), "offset": float((axes[i]*s) @ (high if s > 0 else low))}
                       for i in range(3) for s in (-1, 1)]}


def _point_segment_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    vector = end-start
    fraction = float(np.clip((point-start) @ vector / max(vector @ vector, 1e-12), 0., 1.))
    return float(np.linalg.norm(point-(start+fraction*vector)))


def _inside_boxes(points: np.ndarray, envelopes: list[dict[str, Any]], chunk: int) -> tuple[np.ndarray, np.ndarray]:
    """Return membership and owning layer without an NxM temporary."""
    allowed = np.zeros(len(points), dtype=bool)
    layers = np.zeros(len(points), dtype=np.uint8)
    if not len(points):
        return allowed, layers
    # A point KD-tree gives each convex volume a finite candidate set.  This
    # avoids both a full source NxM matrix and repeatedly scanning all points
    # for the 100+ small web/halo volumes.  Exact OBB checks remain chunked.
    from scipy.spatial import cKDTree
    tree = cKDTree(points)
    geometry = [(np.asarray(e["centerM"], float), np.asarray(e["axes"], float), np.asarray(e["halfSizeM"], float),
                 int(e["layerId"]), str(e.get("kind", ""))) for e in envelopes]
    geometry.sort(key=lambda item: 0 if item[4] == "layer-plane" else 1)
    for center, axes, half, layer_id, _kind in geometry:
        candidate_ids = np.asarray(tree.query_ball_point(center, float(np.linalg.norm(half))), dtype=np.intp)
        for low in range(0, len(candidate_ids), max(1, chunk)):
            ids = candidate_ids[low:low+max(1, chunk)]
            ids = ids[~allowed[ids]]
            if not len(ids):
                continue
            local = (points[ids] - center) @ axes.T
            hit_ids = ids[np.all(np.abs(local) <= half + 1e-10, axis=1)]
            layers[hit_ids] = min(255, max(0, layer_id))
            allowed[hit_ids] = True
    return allowed, layers


def build_floating_zones(inventory: dict[str, Any] | None, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a fail-open, JSON-serializable finite protection report.

    Only resolved, positively circular centerlines are used.  Unresolved BRep
    records are ignored rather than treating their bounding box as a fixture or
    globally disabling otherwise complete steel evidence.
    """
    try:
        options = FloatingZoneParameters(**(params or {}))
    except TypeError as exc:
        return {"version": "floating-zones-v1", "enabled": False, "reason": "invalid-params", "detail": str(exc), "params": params or {}}
    if (any(not np.isfinite(value) or value <= 0 for key, value in asdict(options).items()
            if key not in ('envelope_relaxation_passes', 'envelope_expansion_m'))
            or not np.isfinite(options.envelope_expansion_m) or options.envelope_expansion_m < 0
            or type(options.envelope_relaxation_passes) is not int or options.envelope_relaxation_passes < 0):
        return {"version": "floating-zones-v1", "enabled": False, "reason": "invalid-params", "params": params or {}}
    base = {"version": "floating-zones-v1", "enabled": False, "params": asdict(options), "layers": [], "designSegments": [],
            "allowedEnvelopes": [], "forbiddenCells": [], "forbiddenRule": {"kind": "domain-minus-allowed-envelopes"}}
    if not isinstance(inventory, dict):
        return {**base, "reason": "absent-inventory"}
    bars = inventory.get("bars")
    if not isinstance(bars, list) or not bars:
        return {**base, "reason": "absent-steel-geometry"}
    candidates: list[tuple[dict[str, Any], np.ndarray, bool]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        points = _array(bar.get("points"))
        radius = bar.get("radiusM", bar.get("radius"))
        try:
            circular = float(radius) > 0 and np.isfinite(float(radius))
        except (TypeError, ValueError):
            circular = False
        coverage = str(bar.get("coverage", "complete")).lower()
        if points is None or not circular or coverage == "unresolved":
            continue
        candidates.append((bar, points, coverage == "complete"))
    trusted = [candidate for candidate in candidates if candidate[2]]
    if not trusted:
        return {**base, "reason": "partial-steel-coverage" if candidates else "unresolved-or-noncircular-steel",
                "usableBarCount": 0, "protectivePartialBarCount": len(candidates)}
    if not candidates:
        return {**base, "reason": "unresolved-or-noncircular-steel"}

    envelopes: list[dict[str, Any]] = []
    all_points: list[np.ndarray] = []
    segments: list[dict[str, Any]] = []
    horizontal: list[tuple[np.ndarray, np.ndarray, float, float]] = []
    raw_webs: list[tuple[str, int, np.ndarray, np.ndarray]] = []
    complete_by_bar = {str(bar.get("designBarId", bar.get("id", index))): complete
                       for index, (bar, _points, complete) in enumerate(candidates)}
    for bar_index, (bar, points, complete) in enumerate(candidates):
        bar_id = str(bar.get("designBarId", bar.get("id", bar_index)))
        if complete:
            all_points.append(points)
        for ordinal, (start, end) in enumerate(zip(points[:-1], points[1:])):
            direction = end - start
            length = float(np.linalg.norm(direction))
            if length <= 1e-8:
                continue
            segments.append({"designBarId": bar_id, "ordinal": ordinal, "startM": start.tolist(), "endM": end.tolist(),
                             "coverage": "complete" if complete else "partial"})
            is_horizontal = length >= options.min_horizontal_run_m and abs(direction[2] / length) <= .12
            if is_horizontal and complete:
                horizontal.append((start, end, float((start[2] + end[2]) * .5), float(bar.get("radiusM", bar.get("radius", 0.)))))
            elif not is_horizontal:
                raw_webs.append((bar_id, ordinal, start, end))
            else:  # Partial horizontal evidence may protect but cannot create a band/domain.
                envelope = _unit_box(start, end, half_width=options.in_plane_half_width_m,
                                     half_height=options.layer_half_height_m,
                                     length_tolerance=options.external_length_tolerance_m, kind="straight",
                                     layer_id=0, identifier=f"{bar_id}/run{ordinal}")
                if envelope:
                    envelopes.append(envelope)
        # Keep bent/hooked ends even when inventory's matching units exclude a hook.
        envelopes.extend((_halo(points[0], options.end_halo_m, 0, f"{bar_id}/start-halo"),
                          _halo(points[-1], options.end_halo_m, 0, f"{bar_id}/end-halo")))
        for ordinal in range(1, len(points)-1):
            prior_vector, next_vector = points[ordinal]-points[ordinal-1], points[ordinal+1]-points[ordinal]
            prior_length, next_length = np.linalg.norm(prior_vector), np.linalg.norm(next_vector)
            previous, following = prior_vector/max(prior_length, 1e-12), next_vector/max(next_length, 1e-12)
            # Curved directrices have many small sampled turns and already get
            # overlapping web planes.  Halo only the horizontal-to-hook/web
            # elbow, where a layer slab meets an inclined run.
            prior_horizontal = prior_length >= options.min_horizontal_run_m and abs(previous[2]) <= .12
            next_horizontal = next_length >= options.min_horizontal_run_m and abs(following[2]) <= .12
            if prior_horizontal != next_horizontal and previous @ following < .90:
                envelopes.append(_halo(points[ordinal], options.end_halo_m, 0, f"{bar_id}/corner-halo{ordinal}"))
    # Parsed web units are analytic straight runs and collapse the many raw
    # directrix samples into a small set of genuine web planes.  They are used
    # by geometry, never by ordinal matching.  Old/minimal inventories fall
    # back to raw segments when no usable web units are available.
    web_count = 0
    analytic_webs: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for unit in inventory.get("units", []) if isinstance(inventory.get("units"), list) else []:
        if not isinstance(unit, dict) or str(unit.get("kind")) != "web":
            continue
        bar_id = str(unit.get("designBarId", ""))
        if bar_id not in complete_by_bar:
            continue
        try:
            start, end = np.asarray(unit["startM"], float), np.asarray(unit["endM"], float)
        except (KeyError, TypeError, ValueError):
            continue
        if start.shape != (3,) or end.shape != (3,) or not np.isfinite(np.r_[start, end]).all():
            continue
        envelope = _unit_box(start, end, half_width=options.web_plane_half_width_m,
                             half_height=options.web_plane_half_thickness_m,
                             length_tolerance=options.external_length_tolerance_m, kind="web",
                             layer_id=0, identifier=f"{bar_id}/web-{web_count}")
        if envelope:
            envelopes.append(envelope); web_count += 1
            analytic_webs.setdefault(bar_id, []).append((start, end))
    # Keep raw pieces that are not geometrically covered by an analytic web
    # unit.  This retains hooks/directrix tails on bars that also have a parsed
    # web, without globally restoring every sampled curve segment.
    for bar_id, ordinal, start, end in raw_webs:
        covered = any(_point_segment_distance(start, unit_start, unit_end) <= .012 and
                      _point_segment_distance(end, unit_start, unit_end) <= .012
                      for unit_start, unit_end in analytic_webs.get(bar_id, []))
        if not covered:
            envelope = _unit_box(start, end, half_width=options.web_plane_half_width_m,
                                 half_height=options.web_plane_half_thickness_m,
                                 length_tolerance=options.external_length_tolerance_m, kind="web",
                                 layer_id=0, identifier=f"{bar_id}/run{ordinal}")
            if envelope:
                envelopes.append(envelope)
    # Cluster actual long horizontal geometry by height.  This deliberately
    # does not join raw polyline ordinal to parsed matching-unit ordinal.
    groups: list[list[tuple[np.ndarray, np.ndarray, float]]] = []
    for item in sorted(horizontal, key=lambda entry: entry[2]):
        if not groups or item[2] - groups[-1][-1][2] > options.layer_cluster_gap_m:
            groups.append([item])
        else:
            groups[-1].append(item)
    layers = []
    for layer_id, group in enumerate(groups, 1):
        height = float(np.mean([item[2] for item in group]))
        physical_radius = max(item[3] for item in group)
        half_height = max(options.layer_half_height_m, physical_radius + options.layer_registration_allowance_m)
        plane_points = np.vstack([np.vstack((item[0], item[1])) for item in group])
        envelopes.append(_layer_box(plane_points, layer_id=layer_id, height=height, half_height=half_height, params=options))
        layers.append({"id": layer_id, "kind": "horizontal-band", "heightM": height, "centerHeightM": height,
                       "normal": [0.0, 0.0, 1.0], "physicalRadiusM": physical_radius,
                       "registrationAllowanceM": options.layer_registration_allowance_m, "halfHeightM": half_height,
                       "lowM": height-half_height, "highM": height+half_height})
    # Give web and hook protection the nearest actual horizontal layer label
    # for downstream display without using it to infer geometry.
    if layers:
        for envelope in envelopes:
            if envelope["layerId"] == 0:
                envelope["layerId"] = min(layers, key=lambda layer: abs(envelope["centerM"][2]-layer["heightM"]))["id"]
    if not envelopes:
        return {**base, "reason": "no-finite-steel-runs"}
    cloud = np.vstack(all_points)
    low = cloud.min(axis=0) - options.review_margin_m
    high = cloud.max(axis=0) + options.review_margin_m
    domain = {"kind": "axis-aligned-box", "lowM": low.tolist(), "highM": high.tolist(),
              "planes": [{"normal": (np.eye(3)[i] * sign).tolist(), "offset": float((np.eye(3)[i] * sign) @ (high if sign > 0 else low))}
                         for i in range(3) for sign in (-1, 1)]}
    report = {**base, "enabled": True, "reason": None, "usableBarCount": len(trusted),
            "protectivePartialBarCount": len(candidates) - len(trusted), "designSegments": segments, "layers": layers,
            "allowedEnvelopes": envelopes, "reviewDomain": domain,
            "forbiddenRule": {"kind": "domain-minus-allowed-envelopes", "domain": "reviewDomain",
                              "allowedEnvelopeIds": [item["id"] for item in envelopes],
                              "requiresEligibleMask": True,
                              "exact": True, "debugCellPolicy": "none-nonconvex-complement"}}

    from .rebar_outer_envelope import build_outer_envelope
    report.update(version='floating-zones-v10-step05-steel-boundary',
        outerEnvelope=build_outer_envelope(inventory, asdict(options)), allowedEnvelopes=[],
        forbiddenRule={'kind': 'outside-continuous-outer-envelope', 'scope': 'non-table-source-points',
                       'requiresEligibleMask': True, 'exact': True,
                       'action': 'step05-steel-boundary', 'applyAtStage': '05',
                       'meaning': 'step 05 removes all classified steel outside the displayed cloth; no instance, score, partition or distance exceptions'})
    return report


def classify_floating_zones(points: Any, report: dict[str, Any], *, eligible_mask: Any = None) -> tuple[np.ndarray, np.ndarray]:
    """Locate review candidates; preserve persisted semantics for historical reports."""
    sample = np.asarray(points, dtype=float)
    if sample.ndim != 2 or sample.shape[1:] != (3,):
        raise ValueError("points must have shape (N, 3)")
    layers = np.zeros(len(sample), dtype=np.uint8)
    forbidden = np.zeros(len(sample), dtype=bool)
    if not isinstance(report, dict) or not report.get("enabled"):
        return layers, forbidden
    envelopes = report.get("allowedEnvelopes")
    domain = report.get("reviewDomain")
    global_exclusion = report.get('forbiddenRule', {}).get('scope') == 'all-source-points'
    if not global_exclusion and (not isinstance(envelopes, list) or not isinstance(domain, dict) or eligible_mask is None):
        return layers, forbidden
    eligible = np.ones(len(sample), bool) if global_exclusion else np.asarray(eligible_mask, dtype=bool)
    if eligible.shape != (len(sample),):
        raise ValueError("eligible_mask must have shape (N,)")
    finite = np.isfinite(sample).all(axis=1)
    if report.get('outerEnvelope', {}).get('kind') in ('triangulated-cloth', 'composite-steel-envelope'):
        from .rebar_outer_envelope import inside_outer_envelope
        allowed = inside_outer_envelope(np.where(finite[:, None], sample, 0.), report['outerEnvelope'],
                                       int(report.get('params', {}).get('query_chunk_size', 65536)))
    else:
        allowed, layers = _inside_boxes(np.where(finite[:, None], sample, 0.), envelopes,
                                        int(report.get("params", {}).get("query_chunk_size", 65536)))
    reported_layers = report.get("layers")
    if isinstance(reported_layers, list) and reported_layers and allowed.any():
        heights = np.asarray([item.get("heightM") for item in reported_layers], float)
        ids = np.asarray([item.get("id", 0) for item in reported_layers], int)
        if np.isfinite(heights).all():
            selected = np.flatnonzero(allowed)
            layers[selected] = np.clip(ids[np.argmin(np.abs(sample[selected, 2, None]-heights), axis=1)], 0, 255).astype(np.uint8)
    if global_exclusion:
        return layers, ~allowed | ~finite
    if report.get('forbiddenRule', {}).get('scope') == 'non-table-source-points':
        return layers, eligible & (~allowed | ~finite)
    low, high = np.asarray(domain.get("lowM"), float), np.asarray(domain.get("highM"), float)
    within = finite & np.all(sample >= low, axis=1) & np.all(sample <= high, axis=1)
    forbidden = eligible & within & ~allowed
    return layers, forbidden
