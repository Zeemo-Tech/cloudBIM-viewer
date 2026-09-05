"""Versioned, metre-based contracts for the independent V5 pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import numpy as np

UNKNOWN, TABLE, REBAR, NOISE, FIXTURE = range(5)
AMBIGUOUS = 2  # Instance uncertainty only; bit zero is never a crossing flag.
VERSION = "1"


@dataclass(frozen=True)
class Params:
    random_seed: int = 20260905
    block_size: float = 0.25
    block_point_limit: int = 300_000
    neighbourhood_point_limit: int = 1_000_000
    query_batch_size: int = 4096
    noise_radius: float = 0.025
    noise_distance_ratio: float = 4.5
    surface_radius: float = 0.012
    axis_radius: float = 0.032
    feature_min_neighbors: int = 6
    feature_max_neighbors: int = 64
    detection_voxel_size: float = 0.0035
    detection_point_limit: int = 1_000_000
    min_radius: float = 0.0025
    max_radius: float = 0.012
    support_distance: float = 0.004
    table_distance: float = 0.004
    table_height_bin: float = 0.006
    table_grid_size: float = 0.025
    table_min_area: float = 0.12
    table_max_tilt_degrees: float = 12.0
    table_ransac_iterations: int = 160
    fixture_fit_distance: float = 0.0015
    fixture_surface_distance: float = 0.004
    fixture_min_width: float = 0.025
    fixture_min_length: float = 0.035
    fixture_grid_cell: float = 0.012
    fixture_min_points: int = 14
    fixture_normal_tolerance_degrees: float = 12.0
    fixture_offset_gap: float = 0.006
    min_planarity: float = 0.42
    min_linearity: float = 0.48
    orientation_tolerance_degrees: float = 11.0
    max_orientation_modes: int = 48
    min_primitive_votes: int = 7
    offset_cell_size: float = 0.003
    axial_gap: float = 0.070
    min_primitive_length: float = 0.045
    min_instance_length: float = 0.080
    join_gap: float = 0.090
    observed_join_gap: float = 0.004
    max_turn_degrees: float = 32.0
    layer_gap: float = 0.025
    planar_angle_degrees: float = 15.0
    hough_angle_step_degrees: float = 1.0
    web_strip_width: float = 0.16
    web_strip_overlap: float = 0.064
    ambiguity_margin: float = 0.12
    intersection_tolerance: float = 0.001
    intersection_min_angle_degrees: float = 5.0

    @classmethod
    def from_value(cls, raw=None):
        if isinstance(raw, cls):
            return raw
        raw = dict(raw or {})
        defaults = asdict(cls())
        if set(raw) - set(defaults):
            raise ValueError("unknown V5 parameters: " + ", ".join(sorted(set(raw)-set(defaults))))
        defaults.update(raw)
        for f in fields(cls):
            value = defaults[f.name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{f.name} must be finite and positive")
            if isinstance(getattr(cls(), f.name), int):
                if int(value) != value:
                    raise ValueError(f"{f.name} must be an integer")
                defaults[f.name] = int(value)
        p = cls(**defaults)
        if p.min_radius >= p.max_radius or p.surface_radius >= p.axis_radius:
            raise ValueError("invalid radius ordering")
        if not 3 <= p.feature_min_neighbors <= p.feature_max_neighbors <= 256:
            raise ValueError("invalid feature neighbour bounds")
        if p.block_point_limit > p.neighbourhood_point_limit or p.neighbourhood_point_limit > 2_000_000:
            raise ValueError("invalid bounded neighbourhood budget")
        if p.query_batch_size > 8192 or p.detection_point_limit > 1_000_000:
            raise ValueError("V5 work budget exceeded")
        if p.table_ransac_iterations>1000 or p.max_orientation_modes>128 or p.hough_angle_step_degrees<.25:
            raise ValueError("V5 candidate search budget exceeded")
        if p.intersection_tolerance>.001 or p.intersection_min_angle_degrees<5:
            raise ValueError("V5 intersections require at most 1 mm and at least 5 degrees")
        if p.web_strip_overlap >= p.web_strip_width or p.web_strip_overlap < 2*p.axis_radius:
            raise ValueError("web overlap must cover growth neighbourhood and be less than strip width")
        if any(getattr(p, name) >= 90 for name in ("table_max_tilt_degrees", "planar_angle_degrees", "intersection_min_angle_degrees")):
            raise ValueError("angles must be less than 90 degrees")
        return p

    @property
    def halo(self):
        return 2 * max(self.axis_radius, self.noise_radius)


VISUALIZATION = {
    "schema": "rebar-visualization-v3", "defaultMode": "rebar-class",
    "attributes": {"class":"REBAR_CLASS", "direction":"REBAR_DIRECTION", "instance":"REBAR_INSTANCE", "sceneClass":"SCENE_CLASS", "flags":"REBAR_FLAGS"},
    "values": {"sceneClass":{"unknown":0,"table":1,"rebar":2,"noise":3,"fixture":4}, "flags":{"ambiguous":2}, "unassignedInstance":0},
    "colors": {"unknown":"#64748b","table":"#cbd5e1","rebar":"#22d3ee","noise":"#e879f9","fixture":"#f97316", "directionA":"#22d3ee", "directionB":"#f97316"},
    "instanceStrategy":"golden-angle-v1",
}
