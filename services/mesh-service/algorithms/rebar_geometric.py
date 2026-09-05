"""Adapter that makes the existing geometric PoC a rebar algorithm."""
from __future__ import annotations
from dataclasses import asdict
from typing import Any, Mapping
import numpy as np

from .rebar_base import RebarAlgorithm, RebarAnalysis, RebarPointAttributes
from .rebar_segmentation import RebarSegmentationParams, segment_rebar_points


_ADVANCED_PARAMETERS = (
    "plane_distance_threshold",
    "min_rebar_height",
    "max_rebar_height",
    "pca_radius",
    "min_axis_spacing",
    "bridge_gap",
)
_LENGTH_PARAMETERS = {
    "min_scene_extent", "max_scene_extent", "max_median_nn_spacing",
    "plane_distance_threshold", "min_rebar_height", "max_rebar_height",
    "height_cluster_gap", "pca_radius", "offset_cluster_gap",
    "min_axis_spacing", "axis_distance_threshold", "axial_sample_gap",
    "bridge_gap", "min_line_length",
}
_PARAMETER_TITLES = {
    "plane_distance_threshold": "平面距离阈值",
    "min_rebar_height": "最小钢筋高度",
    "max_rebar_height": "最大钢筋高度",
    "pca_radius": "PCA 邻域半径",
    "min_axis_spacing": "最小钢筋间距",
    "bridge_gap": "断点桥接距离",
}


def _parameter_descriptor(name: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        value_type = "boolean"
    elif isinstance(value, int):
        value_type = "integer"
    elif isinstance(value, float):
        value_type = "number"
    else:
        value_type = "string"
    descriptor: dict[str, Any] = {"type": value_type, "default": value}
    if name in _LENGTH_PARAMETERS:
        descriptor["unit"] = "m"
    if name in _PARAMETER_TITLES:
        descriptor["title"] = _PARAMETER_TITLES[name]
    return descriptor


class GeometricV2Adapter(RebarAlgorithm):
    @property
    def descriptor(self) -> Mapping[str, Any]:
        defaults = asdict(RebarSegmentationParams())
        return {"id": "geometric-v2", "version": "2", "name": "Geometric rebar v2",
                "analysisSchema": "rebar-analysis-v1",
                "capabilities": {"class": True, "direction": True, "instance": True, "confidence": False},
                "parameterSchema": {"type": "object", "additionalProperties": False,
                                    "properties": {key: _parameter_descriptor(key, value) for key, value in defaults.items()}},
                "inputOptionSchema": {"type": "object", "additionalProperties": False,
                    "properties": {"maxInputPoints": {"type": "integer", "minimum": 3, "maximum": 200000, "default": 200000},
                                   "voxelSize": {"type": "number", "minimum": 0.000001, "maximum": 5}}},
                "uiHints": {"kind": "advanced-geometric", "order": list(_ADVANCED_PARAMETERS),
                            "advanced": list(_ADVANCED_PARAMETERS)}}

    def normalize_parameters(self, raw: Mapping[str, Any] | None) -> Mapping[str, Any]:
        return asdict(RebarSegmentationParams.from_value(raw or {}))

    def analyze(self, sample: np.ndarray, parameters: Mapping[str, Any]) -> RebarAnalysis:
        legacy = segment_rebar_points(sample, parameters)
        # Projection data is deliberately algorithmDetails: the stable analysis
        # envelope does not prescribe how an algorithm represents geometry.
        projection = []
        for ordinal, item in enumerate(legacy["instances"], start=1):
            centerline = item["centerline"]
            projection.append({"instance": ordinal,
                               "direction": int(item["direction_index"]) + 1,
                               "worldStart": centerline["world_start"],
                               "worldEnd": centerline["world_end"],
                               # Be conservative when a fitted tube is tiny;
                               # this is the geometric core's membership bound.
                               "radius": max(float(item["axis_support_radius"]), float(parameters["axis_distance_threshold"]))})
        legacy["projection"] = {"kind": "world-segment-tube-v1", "instances": projection}
        return RebarAnalysis({"schema": "rebar-analysis-v1",
                              "diagnostics": legacy.get("diagnostics", {}),
                              "algorithmDetails": legacy,
                              "samplePointCount": int(len(sample))})

    def project_points(self, points_xyz: np.ndarray, analysis: RebarAnalysis) -> RebarPointAttributes:
        count = len(points_xyz)
        classes = np.zeros(count, dtype=np.uint8)
        directions = np.zeros(count, dtype=np.uint16)
        instances = np.zeros(count, dtype=np.uint32)
        details = analysis.data["algorithmDetails"]
        if points_xyz.ndim != 2 or points_xyz.shape[1:] != (3,) or not np.isfinite(points_xyz).all():
            raise ValueError("tile points must be finite XYZ")
        matches: list[list[dict[str, Any]]] = [[] for _ in range(count)]
        for item in details["projection"]["instances"]:
            start = np.asarray(item["worldStart"], dtype=np.float64)
            end = np.asarray(item["worldEnd"], dtype=np.float64)
            vector = end - start
            length2 = float(vector @ vector)
            if not np.isfinite(length2) or length2 <= 0:
                raise ValueError("canonical projection contains a degenerate segment")
            t = np.clip(((points_xyz - start) @ vector) / length2, 0.0, 1.0)
            closest = start + t[:, None] * vector
            selected = np.flatnonzero(np.linalg.norm(points_xyz - closest, axis=1) <= float(item["radius"]))
            for index in selected.tolist(): matches[index].append(item)
        for index, assigned in enumerate(matches):
            if len(assigned) == 1:
                classes[index] = 1
                directions[index] = int(assigned[0]["direction"])
                instances[index] = int(assigned[0]["instance"])
            elif len(assigned) > 1:
                classes[index] = 2; directions[index] = np.uint16(65535); instances[index] = np.uint32(0xffffffff)
        return RebarPointAttributes(classes, directions, instances)
