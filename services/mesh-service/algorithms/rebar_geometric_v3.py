"""Geometric v3 scene-class adapter around the immutable v2 geometric core."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
from scipy.spatial import cKDTree

from .rebar_base import RebarAlgorithm, RebarAnalysis, RebarPointAttributes
from .rebar_geometric import GeometricV2Adapter, _parameter_descriptor


_V3_DEFAULTS = {"noise_neighbor_count": 8, "noise_mad_multiplier": 4.5,
                "noise_projection_radius_multiplier": 2.0}
_VISUALIZATION = {
    "schema": "rebar-visualization-v1", "defaultMode": "rebar-class",
    "instanceStrategy": "golden-angle-v1",
    "attributes": {"sceneClass": "SCENE_CLASS", "direction": "REBAR_DIRECTION",
                   "instance": "REBAR_INSTANCE", "flags": "REBAR_FLAGS"},
    "values": {
        "sceneClass": {"clutter": 0, "table": 1, "rebar": 2, "noise": 3},
        "direction": {"none": 0, "directionA": 1, "directionB": 2, "intersection": 65535},
        "instance": {"none": 0, "intersection": 4294967295},
        "flags": {"intersection": 1},
    },
    "colors": {"clutter": "#334155", "table": "#94a3b8", "noise": "#d946ef",
               "rebar": "#ef4444", "directionA": "#22d3ee", "directionB": "#f97316",
               "intersection": "#facc15"},
}


def _hull(points: np.ndarray) -> np.ndarray:
    """Deterministic 2-D convex hull with safe degenerate fallbacks."""
    unique = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if len(unique) <= 2:
        return unique
    ordered = unique[np.lexsort((unique[:, 1], unique[:, 0]))]
    def cross(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        ab = b - a
        bc = c - b
        return float(ab[0] * bc[1] - ab[1] * bc[0])
    lower: list[np.ndarray] = []
    for p in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0: lower.pop()
        lower.append(p)
    upper: list[np.ndarray] = []
    for p in ordered[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0: upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1] + upper[:-1])


def _inside_footprint(points: np.ndarray, hull: np.ndarray) -> np.ndarray:
    if len(hull) == 0: return np.zeros(len(points), dtype=bool)
    if len(hull) == 1: return np.linalg.norm(points - hull[0], axis=1) <= 1e-9
    if len(hull) == 2:
        vector = hull[1] - hull[0]; length2 = float(vector @ vector)
        t = np.clip(((points - hull[0]) @ vector) / length2, 0, 1)
        return np.linalg.norm(points - (hull[0] + t[:, None] * vector), axis=1) <= 1e-8
    edge = np.roll(hull, -1, axis=0) - hull
    rel = points[:, None, :] - hull[None, :, :]
    return np.all(edge[None, :, 0] * rel[:, :, 1] - edge[None, :, 1] * rel[:, :, 0] >= -1e-10, axis=1)


class GeometricV3Adapter(RebarAlgorithm):
    """Adds bounded scene/noise projection while delegating detection to v2."""
    _v2 = GeometricV2Adapter()

    @property
    def descriptor(self) -> Mapping[str, Any]:
        core = dict(self._v2.descriptor)
        defaults = dict(core["parameterSchema"]["properties"])
        defaults.update({name: _parameter_descriptor(name, value) for name, value in _V3_DEFAULTS.items()})
        return {**core, "id": "geometric-v3", "version": "3", "name": "Geometric rebar v3",
                "capabilities": {**core["capabilities"], "sceneClass": True, "rebarFlags": True},
                "parameterSchema": {**core["parameterSchema"], "properties": defaults},
                "uiHints": {**core["uiHints"], "advanced": [*core["uiHints"]["advanced"], *_V3_DEFAULTS],
                            "order": [*core["uiHints"]["order"], *_V3_DEFAULTS]},
                "visualization": _VISUALIZATION}

    def normalize_parameters(self, raw: Mapping[str, Any] | None) -> Mapping[str, Any]:
        value = dict(raw or {}); unknown = sorted(set(value) - set(self._v2.normalize_parameters({})) - set(_V3_DEFAULTS))
        if unknown: raise ValueError("unknown rebar segmentation parameters: " + ", ".join(unknown))
        extra = {**_V3_DEFAULTS, **{k: value[k] for k in _V3_DEFAULTS if k in value}}
        count = extra["noise_neighbor_count"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 64: raise ValueError("noise_neighbor_count must be an integer from 1 to 64")
        for key in ("noise_mad_multiplier", "noise_projection_radius_multiplier"):
            if isinstance(extra[key], bool) or not isinstance(extra[key], (int, float)) or not np.isfinite(extra[key]) or extra[key] <= 0: raise ValueError(f"{key} must be a positive finite number")
            extra[key] = float(extra[key])
        core = self._v2.normalize_parameters({k: v for k, v in value.items() if k not in _V3_DEFAULTS})
        return {**core, **extra}

    def analyze(self, sample: np.ndarray, parameters: Mapping[str, Any]) -> RebarAnalysis:
        core_params = {k: v for k, v in parameters.items() if k not in _V3_DEFAULTS}
        base = self._v2.analyze(sample, core_params)
        details = base.data["algorithmDetails"]
        plane = details["plane"]; point_sets = details["point_sets"]
        inliers = sample[np.asarray(point_sets["plane_inlier_indices"], dtype=np.intp)]
        origin = np.asarray(plane["origin"], dtype=float); x_axis = np.asarray(plane["x_axis"], dtype=float); y_axis = np.asarray(plane["y_axis"], dtype=float)
        hull = _hull(np.column_stack(((inliers - origin) @ x_axis, (inliers - origin) @ y_axis)))
        k = min(int(parameters["noise_neighbor_count"]), max(1, len(sample) - 1))
        tree = cKDTree(sample); distances = tree.query(sample, k=k + 1)[0][:, 1:]
        means = distances.mean(axis=1); median = float(np.median(means)); mad = float(np.median(np.abs(means - median)))
        noise_indices = np.flatnonzero(means > median + float(parameters["noise_mad_multiplier"]) * 1.4826 * mad)
        nearest = tree.query(sample, k=2)[0][:, 1]
        radius = float(parameters["noise_projection_radius_multiplier"]) * float(np.median(nearest))
        v3 = {"planeFootprint": {"kind": "convex-hull-local-xy-v1", "vertices": hull.tolist()},
              "noise": {"worldPoints": sample[noise_indices].tolist(), "projectionRadius": radius,
                        "neighborCount": k, "threshold": median + float(parameters["noise_mad_multiplier"]) * 1.4826 * mad}}
        data = {**base.data, "algorithmDetails": {**details, "v3": v3}, "visualization": _VISUALIZATION}
        return RebarAnalysis(data)

    def project_points(self, points_xyz: np.ndarray, analysis: RebarAnalysis) -> RebarPointAttributes:
        points = np.asarray(points_xyz, dtype=np.float64); count = len(points)
        if points.ndim != 2 or points.shape[1:] != (3,) or not np.isfinite(points).all(): raise ValueError("tile points must be finite XYZ")
        details = analysis.data["algorithmDetails"]; instances = details["projection"]["instances"]
        best_dist = np.full((2, count), np.inf); best_id = np.zeros((2, count), dtype=np.uint32)
        for item in instances:
            direction = int(item["direction"]) - 1
            if direction not in (0, 1): continue
            start = np.asarray(item["worldStart"], dtype=float); vector = np.asarray(item["worldEnd"], dtype=float) - start; length2 = float(vector @ vector)
            if not np.isfinite(length2) or length2 <= 0: raise ValueError("canonical projection contains a degenerate segment")
            t = np.clip(((points - start) @ vector) / length2, 0, 1); distance = np.linalg.norm(points - (start + t[:, None] * vector), axis=1)
            better = (distance <= float(item["radius"])) & ((distance < best_dist[direction]) | ((distance == best_dist[direction]) & (int(item["instance"]) < best_id[direction])))
            best_dist[direction, better] = distance[better]; best_id[direction, better] = int(item["instance"])
        a, b = np.isfinite(best_dist[0]), np.isfinite(best_dist[1]); cross = a & b; single_a, single_b = a & ~b, b & ~a
        klass = np.zeros(count, dtype=np.uint8); direction = np.zeros(count, dtype=np.uint16); instance = np.zeros(count, dtype=np.uint32); flags = np.zeros(count, dtype=np.uint8)
        klass[single_a | single_b] = 1; direction[single_a] = 1; direction[single_b] = 2; instance[single_a] = best_id[0, single_a]; instance[single_b] = best_id[1, single_b]
        klass[cross] = 2; direction[cross] = np.uint16(65535); instance[cross] = np.uint32(0xffffffff); flags[cross] = 1
        plane = details["plane"]; origin = np.asarray(plane["origin"], dtype=float); normal = np.asarray(plane["normal"], dtype=float)
        v3 = details["v3"]; hull = np.asarray(v3["planeFootprint"]["vertices"], dtype=float)
        xy = np.column_stack(((points - origin) @ np.asarray(plane["x_axis"], dtype=float), (points - origin) @ np.asarray(plane["y_axis"], dtype=float)))
        on_plane = (np.abs((points - origin) @ normal) <= float(details["parameters"]["plane_distance_threshold"])) & _inside_footprint(xy, hull)
        scene = np.zeros(count, dtype=np.uint8); scene[on_plane] = 1; scene[klass > 0] = 2
        noise = np.asarray(v3["noise"]["worldPoints"], dtype=float)
        if len(noise):
            near_noise = cKDTree(noise).query(points)[0] <= float(v3["noise"]["projectionRadius"])
            scene[(scene == 0) & near_noise] = 3
        return RebarPointAttributes(klass, direction, instance, scene, flags)
