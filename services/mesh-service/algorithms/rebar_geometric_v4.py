"""Bounded-memory 3-D geometric rebar and fixture segmentation (v4).

Unlike geometric-v2/v3, this detector does not assume two horizontal line
families.  It finds local 3-D line primitives after bounded table and fixture
surface removal, then joins only tangent-continuous endpoints.  A restartable
source context supplies spatial coverage and a full-resolution support pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, TYPE_CHECKING

import numpy as np
from rebar_bim_matching import match_bim_instances

from .rebar_base import RebarAlgorithm, RebarAnalysis, RebarPointAttributes
from .rebar_v4_geometry import (
    LocalFeatures,
    PlaneModel,
    build_segment_index,
    canonical_direction,
    fit_bounded_table_plane,
    line_primitives,
    local_features,
    orientation_modes,
    perpendicular_basis,
    project_top2,
    sparse_grid_components,
    table_mask,
    trace_primitive_graph,
)
from .rebar_v4_postprocess import refine_v4_evidence

if TYPE_CHECKING:
    from .rebar_base import RebarInputContext


SCENE_CLUTTER, SCENE_TABLE, SCENE_REBAR, SCENE_NOISE, SCENE_FIXTURE = range(5)
REBAR_STEEL, REBAR_AMBIGUOUS = 1, 2
FLAG_CROSSING, FLAG_AMBIGUOUS = 1, 2
AMBIGUOUS_INSTANCE = np.uint32(0xFFFFFFFF)


@dataclass(frozen=True)
class _Params:
    min_radius: float = 0.0025
    max_radius: float = 0.012
    support_distance: float = 0.006
    table_distance: float = 0.004
    table_ransac_iterations: int = 160
    table_min_ratio: float = 0.15
    table_max_tilt_degrees: float = 35.0
    coverage_voxel_size: float = 0.0035
    coverage_point_limit: int = 300_000
    feature_seed_step: float = 0.005
    feature_radius: float = 0.032
    feature_min_neighbors: int = 8
    feature_max_neighbors: int = 64
    min_linearity: float = 0.48
    min_planarity: float = 0.42
    orientation_tolerance_degrees: float = 11.0
    min_primitive_votes: int = 7
    max_orientation_modes: int = 48
    offset_cell_size: float = 0.003
    axial_gap: float = 0.070
    min_primitive_length: float = 0.045
    min_instance_length: float = 0.080
    join_gap: float = 0.090
    observed_join_gap: float = 0.040
    max_turn_degrees: float = 32.0
    fixture_normal_tolerance_degrees: float = 12.0
    fixture_offset_gap: float = 0.006
    fixture_grid_cell: float = 0.014
    fixture_min_points: int = 14
    fixture_min_length: float = 0.14
    fixture_min_width: float = 0.014
    fixture_surface_distance: float = 0.006
    fixture_fit_distance: float = 0.0015
    ambiguity_margin: float = 0.16
    crossing_angle_degrees: float = 40.0
    bim_match_gate: float = 0.060
    bim_registration_uncertainty: float = 0.020

    @classmethod
    def from_value(cls, raw: Mapping[str, Any] | None) -> "_Params":
        value = dict(raw or {})
        defaults = asdict(cls())
        unknown = sorted(set(value) - set(defaults))
        if unknown:
            raise ValueError(
                "unknown rebar segmentation parameters: " + ", ".join(unknown)
            )
        merged = {**defaults, **value}
        integers = {
            "table_ransac_iterations",
            "coverage_point_limit",
            "feature_min_neighbors",
            "feature_max_neighbors",
            "min_primitive_votes",
            "max_orientation_modes",
            "fixture_min_points",
        }
        for name, item in merged.items():
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not np.isfinite(item)
            ):
                raise ValueError(f"{name} must be a finite number")
            if name in integers:
                if int(item) != item or item < 1:
                    raise ValueError(f"{name} must be a positive integer")
                merged[name] = int(item)
            elif item <= 0:
                raise ValueError(f"{name} must be positive")
            else:
                merged[name] = float(item)
        if not merged["min_radius"] < merged["max_radius"] <= 0.1:
            raise ValueError(
                "min_radius must be less than max_radius and max_radius <= 0.1"
            )
        if merged["feature_min_neighbors"] > merged["feature_max_neighbors"]:
            raise ValueError(
                "feature_min_neighbors must not exceed feature_max_neighbors"
            )
        if not 0 < merged["table_min_ratio"] <= 1:
            raise ValueError("table_min_ratio must be in (0, 1]")
        for name in ("min_linearity", "min_planarity", "ambiguity_margin"):
            if not 0 < merged[name] < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        for name in (
            "table_max_tilt_degrees",
            "orientation_tolerance_degrees",
            "max_turn_degrees",
            "fixture_normal_tolerance_degrees",
            "crossing_angle_degrees",
        ):
            if not 0 < merged[name] < 90:
                raise ValueError(f"{name} must be in (0, 90)")
        return cls(**merged)


_VISUALIZATION = {
    "schema": "rebar-visualization-v2",
    "defaultMode": "rebar-class",
    "instanceStrategy": "golden-angle-v1",
    "attributes": {
        "sceneClass": "SCENE_CLASS",
        "direction": "REBAR_DIRECTION",
        "instance": "REBAR_INSTANCE",
        "flags": "REBAR_FLAGS",
    },
    "values": {
        "sceneClass": {
            "clutter": 0,
            "table": 1,
            "rebar": 2,
            "noise": 3,
            "fixture_formwork": 4,
            "fixture": 4,
        },
        "direction": {
            "none": 0,
            "directionA": 1,
            "directionB": 2,
            "intersection": 65535,
        },
        "instance": {"none": 0, "ambiguous": 4294967295, "intersection": 4294967295},
        "flags": {
            "crossing": FLAG_CROSSING,
            "ambiguity": FLAG_AMBIGUOUS,
            "intersection": FLAG_CROSSING,
        },
    },
    "colors": {
        "clutter": "#334155",
        "table": "#94a3b8",
        "noise": "#d946ef",
        "rebar": "#ef4444",
        "fixture": "#10b981",
        "fixture_formwork": "#10b981",
        "directionA": "#22d3ee",
        "directionB": "#f97316",
        "intersection": "#facc15",
    },
}


def _plane_dict(plane: PlaneModel | None) -> dict[str, Any] | None:
    if plane is None:
        return None
    return {
        "origin": plane.origin.tolist(),
        "normal": plane.normal.tolist(),
        "axes": plane.axes.tolist(),
        "hull": plane.hull.tolist(),
        "supportCount": int(len(plane.inliers)),
        "rmse": plane.rmse,
    }


def _surface_mask(points: np.ndarray, surfaces: list[dict[str, Any]]) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64)
    result = np.zeros(len(points), dtype=bool)
    for surface in surfaces:
        origin = np.asarray(surface["origin"], dtype=np.float64)
        axes = np.asarray(surface["axes"], dtype=np.float64)
        normal = np.asarray(surface["normal"], dtype=np.float64)
        extent = np.asarray(surface["halfExtent"], dtype=np.float64)
        local = points - origin
        distance = np.abs(local @ normal)
        in_bounds = (np.abs(local @ axes[0]) <= extent[0]) & (
            np.abs(local @ axes[1]) <= extent[1]
        )
        result |= in_bounds & (distance <= float(surface["distance"]))
    return result


def _detect_fixture_surfaces(
    features: LocalFeatures,
    p: _Params,
    support_points: np.ndarray | None = None,
    table_normal: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Recover finite, filled planar faces from local normal votes.

    Local PCA supplies only plane hypotheses.  Bounds and support are refit
    against the spatial coverage points so a few planar seeds can recover a
    complete square-tube face.  The minimum fitted width is larger than any
    configured rebar diameter; this prevents one cylindrical bar (or two close
    bars with an empty transverse slot) from becoming a fixture rectangle.
    """
    planar_rows = np.flatnonzero(
        (features.planarity >= p.min_planarity) & (features.linearity < 0.80)
    )
    if len(planar_rows) < p.fixture_min_points:
        return []
    normals = features.normals[planar_rows]
    modes = orientation_modes(
        normals,
        features.planarity[planar_rows],
        tolerance_degrees=p.fixture_normal_tolerance_degrees,
        minimum_votes=max(6, p.fixture_min_points // 2),
        maximum_modes=24,
    )
    # The table supplies an additional orientation hypothesis, never a fixture
    # label. All fitted faces still need independent planar support and bounds.
    if table_normal is not None:
        normal = np.asarray(table_normal, dtype=np.float64)
        rows = np.flatnonzero(
            np.abs(normals @ normal)
            >= np.cos(np.deg2rad(p.fixture_normal_tolerance_degrees))
        )
        if len(rows) >= max(6, p.fixture_min_points // 2):
            modes.append((normal, rows))
    support = (
        np.asarray(support_points, dtype=np.float64)
        if support_points is not None
        else features.points
    )
    surfaces: list[dict[str, Any]] = []
    for normal, mode_rows in modes:
        rows = planar_rows[mode_rows]
        offsets = features.points[rows] @ normal
        # Rounded bins avoid chaining unrelated parallel planes through sparse
        # clutter, which sorted-gap grouping does whenever no offset gap is
        # completely empty.
        offset_bin = max(p.fixture_surface_distance, p.fixture_offset_gap / 2)
        bins = np.rint(offsets / offset_bin).astype(np.int64)
        for value in np.unique(bins):
            layer = np.flatnonzero(bins == value)
            if len(layer) < max(6, p.fixture_min_points // 2):
                continue
            plane_offset = float(np.median(offsets[layer]))
            near = np.abs(support @ normal - plane_offset) <= p.fixture_surface_distance
            cloud = support[near]
            if len(cloud) < p.fixture_min_points:
                continue
            first, second = perpendicular_basis(normal)
            axes = np.vstack((first, second))
            center = normal * plane_offset
            local = np.column_stack(
                ((cloud - center) @ axes[0], (cloud - center) @ axes[1])
            )
            for component in sparse_grid_components(local, p.fixture_grid_cell):
                if len(component) < p.fixture_min_points:
                    continue
                patch = cloud[component]
                origin = np.mean(patch, axis=0)
                _, patch_singular, patch_vh = np.linalg.svd(
                    patch - origin, full_matrices=False
                )
                # A broad projection band gathers corners from adjacent tube
                # faces. Fit the surface to its genuinely planar core first;
                # an entire circular section must not qualify merely because
                # its diameter fits inside that projection band.
                planar_core = (
                    np.abs((patch - origin) @ patch_vh[2]) <= p.fixture_fit_distance
                )
                if (
                    planar_core.mean() < 0.50
                    or planar_core.sum() < p.fixture_min_points
                ):
                    continue
                patch = patch[planar_core]
                origin = np.mean(patch, axis=0)
                _, patch_singular, patch_vh = np.linalg.svd(
                    patch - origin, full_matrices=False
                )
                coordinates = np.column_stack(
                    ((patch - origin) @ patch_vh[0], (patch - origin) @ patch_vh[1])
                )
                lo, hi = np.percentile(coordinates, [1, 99], axis=0)
                dimensions = hi - lo
                major_dimension = float(np.max(dimensions))
                minor_dimension = float(np.min(dimensions))
                if major_dimension < p.fixture_min_length or minor_dimension < max(
                    p.fixture_min_width, 3.0 * p.max_radius
                ):
                    continue
                residual = np.abs((patch - origin) @ patch_vh[2])
                if float(np.percentile(residual, 95)) > p.fixture_fit_distance * 1.5:
                    continue
                # A sparse rebar grid can be globally planar.  Require observed
                # local surface coverage in a 2-D raster, rather than accepting
                # all points inside the fitted rectangle.
                cell = p.fixture_grid_cell
                occupied = len(
                    np.unique(np.floor(coordinates / cell).astype(np.int64), axis=0)
                )
                possible = max(
                    1,
                    int(np.ceil(dimensions[0] / cell))
                    * int(np.ceil(dimensions[1] / cell)),
                )
                # A face must also fill its short axis.  Close cylinders may
                # look planar from above, but retain an empty slot between their
                # projected circular bands.
                short_axis = int(np.argmin(np.ptp(coordinates, axis=0)))
                short = coordinates[:, short_axis]
                profile_cell = max(0.0025, p.fixture_grid_cell / 4)
                profile_bins = np.unique(
                    np.floor((short - short.min()) / profile_cell).astype(np.int64)
                )
                profile_possible = max(1, int(np.ceil(np.ptp(short) / profile_cell)))
                if (
                    occupied / possible < 0.70
                    or len(profile_bins) / profile_possible < 0.72
                ):
                    continue
                rectangle_center = origin + ((lo + hi) / 2) @ patch_vh[:2]
                surfaces.append(
                    {
                        "origin": rectangle_center.tolist(),
                        "normal": canonical_direction(patch_vh[2]).tolist(),
                        "axes": patch_vh[:2].tolist(),
                        "halfExtent": (dimensions / 2 + cell).tolist(),
                        "distance": p.fixture_surface_distance,
                        "supportCount": int(len(patch)),
                        "coverage": float(occupied / possible),
                    }
                )
    # Keep higher-support rectangles only when their bounded footprints overlap
    # on the same plane.  Parallel, spatially separate formwork faces are not
    # duplicates.
    accepted: list[dict[str, Any]] = []
    for surface in sorted(surfaces, key=lambda item: -int(item["supportCount"])):
        origin = np.asarray(surface["origin"])
        duplicate = False
        for existing in accepted:
            existing_origin = np.asarray(existing["origin"])
            existing_normal = np.asarray(existing["normal"])
            if (
                abs(float(existing_normal @ np.asarray(surface["normal"]))) <= 0.98
                or abs(float((origin - existing_origin) @ existing_normal))
                > 2 * p.fixture_surface_distance
            ):
                continue
            existing_axes = np.asarray(existing["axes"])
            existing_extent = np.asarray(existing["halfExtent"])
            surface_axes = np.asarray(surface["axes"])
            surface_extent = np.asarray(surface["halfExtent"])
            corners = np.array(
                [
                    origin
                    + sx * surface_extent[0] * surface_axes[0]
                    + sy * surface_extent[1] * surface_axes[1]
                    for sx in (-1, 1)
                    for sy in (-1, 1)
                ]
            )
            local_corners = (corners - existing_origin) @ existing_axes.T
            if np.all(np.abs(local_corners) <= existing_extent + 1e-5):
                duplicate = True
                break
        if not duplicate:
            accepted.append(surface)
    return accepted


def _direction_ids(instances: list[dict[str, Any]]) -> None:
    representatives: list[np.ndarray] = []
    for item in instances:
        tangent = canonical_direction(
            np.asarray(item.pop("_tangent"), dtype=np.float64)
        )
        matched = next(
            (
                index
                for index, representative in enumerate(representatives)
                if abs(float(tangent @ representative)) >= np.cos(np.deg2rad(15.0))
            ),
            None,
        )
        if matched is None:
            representatives.append(tangent)
            matched = len(representatives) - 1
        item["directionId"] = matched + 1


def _projection_entries(
    instances: list[dict[str, Any]], p: _Params
) -> list[dict[str, Any]]:
    result = []
    for item in instances:
        result.append(
            {
                "id": int(item["id"]),
                "directionId": int(item["directionId"]),
                "centerline": item["centerline"],
                "observedSegments": item.get("observedSegments", []),
                "radius": float(item["radius"]) + p.support_distance,
                "evidence": item["evidence"],
            }
        )
    return result


class GeometricV4Adapter(RebarAlgorithm):
    @property
    def descriptor(self) -> Mapping[str, Any]:
        defaults = asdict(_Params())
        length_names = {
            name
            for name in defaults
            if any(
                token in name
                for token in (
                    "radius",
                    "distance",
                    "gap",
                    "length",
                    "width",
                    "step",
                    "cell",
                    "voxel",
                    "uncertainty",
                    "gate",
                )
            )
        }
        return {
            "id": "geometric-v4",
            "version": "5",
            "name": "Geometric rebar v4 (experimental)",
            "analysisSchema": "rebar-analysis-v1",
            "capabilities": {
                "class": True,
                "direction": True,
                "instance": True,
                "confidence": False,
                "sceneClass": True,
                "rebarFlags": True,
                "rawLabels": True,
                "bimPrior": True,
            },
            "parameterSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    name: {
                        "type": "integer" if isinstance(value, int) else "number",
                        "default": value,
                        **({"unit": "m"} if name in length_names else {}),
                    }
                    for name, value in defaults.items()
                },
            },
            "visualization": _VISUALIZATION,
        }

    def normalize_parameters(self, raw: Mapping[str, Any] | None) -> Mapping[str, Any]:
        return asdict(_Params.from_value(raw))

    def _analyze_points(
        self, points: np.ndarray, p: _Params, plane: PlaneModel | None = None
    ) -> RebarAnalysis:
        points = np.asarray(points, dtype=np.float64)
        if (
            points.ndim != 2
            or points.shape[1:] != (3,)
            or (len(points) < 3 and plane is None)
            or not np.isfinite(points).all()
        ):
            raise ValueError("sample must contain at least three finite XYZ points")
        if plane is None:
            plane = fit_bounded_table_plane(
                points,
                distance=p.table_distance,
                iterations=p.table_ransac_iterations,
                minimum_ratio=p.table_min_ratio,
                max_tilt_degrees=p.table_max_tilt_degrees,
            )
        table = (
            table_mask(points, plane, p.table_distance)
            if plane is not None
            else np.zeros(len(points), dtype=bool)
        )
        residual = points[~table]
        features = local_features(
            residual,
            seed_step=p.feature_seed_step,
            radius=p.feature_radius,
            max_neighbours=p.feature_max_neighbors,
            min_neighbours=p.feature_min_neighbors,
        )
        fixture_local_features = local_features(
            residual,
            seed_step=p.feature_seed_step,
            radius=min(p.feature_radius, 0.018),
            max_neighbours=p.feature_max_neighbors,
            min_neighbours=min(p.feature_min_neighbors, 6),
        )
        fixture_surfaces = _detect_fixture_surfaces(
            fixture_local_features, p, residual, plane.normal if plane is not None else None
        )
        fixture_features = _surface_mask(features.points, fixture_surfaces)
        primitives = line_primitives(
            features.points[~fixture_features],
            features.tangents[~fixture_features],
            features.linearity[~fixture_features],
            minimum_linearity=p.min_linearity,
            orientation_tolerance_degrees=p.orientation_tolerance_degrees,
            minimum_votes=p.min_primitive_votes,
            maximum_modes=p.max_orientation_modes,
            offset_cell=p.offset_cell_size,
            axial_gap=p.axial_gap,
            minimum_length=p.min_primitive_length,
            min_radius=p.min_radius,
            max_radius=p.max_radius,
        )
        # A separate, deliberately narrow pass recovers sparse, very linear
        # observed candidates in every orientation.  Keeping this independent
        # of the world Z axis is important for auxiliary bars that are diagonal
        # in the table plane.  The pass is bounded more tightly than the main
        # detector and is never used to extend endpoints.
        recovery_rows = (
            ~fixture_features
            & (features.linearity >= max(0.72, p.min_linearity + 0.15))
        )
        recovery_candidates = line_primitives(
            features.points[recovery_rows],
            features.tangents[recovery_rows],
            features.linearity[recovery_rows],
            minimum_linearity=max(0.72, p.min_linearity + 0.15),
            orientation_tolerance_degrees=min(8.0, p.orientation_tolerance_degrees),
            minimum_votes=max(5, p.min_primitive_votes - 2),
            maximum_modes=min(12, p.max_orientation_modes),
            offset_cell=p.offset_cell_size,
            axial_gap=min(0.050, p.axial_gap),
            minimum_length=max(0.055, p.min_primitive_length * 0.75),
            min_radius=p.min_radius,
            max_radius=p.max_radius,
        ) if np.any(recovery_rows) else []
        # One bounded seam between primitive extraction and graph tracing.  It
        # may reject line-like fixture evidence and records conservative
        # recovery opportunities, but never creates unseen geometry.
        refined = refine_v4_evidence(
            fixture_surfaces,
            primitives,
            residual,
            max_radius=p.max_radius,
            recovery_candidates=recovery_candidates,
            planar_support=fixture_local_features.points[
                (fixture_local_features.planarity >= p.min_planarity)
                & (fixture_local_features.linearity < 0.80)
            ],
        )
        fixture_surfaces = list(refined.fixture_surfaces)
        primitives = list(refined.primitives)
        traced = trace_primitive_graph(
            primitives,
            join_gap=p.join_gap,
            observed_join_gap=p.observed_join_gap,
            maximum_turn_degrees=p.max_turn_degrees,
            minimum_instance_length=p.min_instance_length,
            join_overrides=refined.hook_join_overrides,
        )
        instances: list[dict[str, Any]] = []
        for ident, item in enumerate(
            sorted(traced, key=lambda value: tuple(value.centerline[0])), 1
        ):
            instances.append(
                {
                    "id": ident,
                    "centerline": item.centerline.tolist(),
                    "observedSegments": [
                        {"points": segment.tolist()}
                        for segment in item.observed_segments
                    ],
                    "radius": item.radius,
                    "evidence": "mixed" if item.inferred_segments else "observed",
                    "inferredSegments": [
                        {"points": segment.tolist(), "source": "geometric-gap"}
                        for segment in item.inferred_segments
                    ],
                    "pointCount": item.point_count,
                    "length": item.length,
                    "_tangent": item.tangent.tolist(),
                }
            )
        _direction_ids(instances)
        plane_data = _plane_dict(plane)
        details = {
            "parameters": asdict(p),
            "plane": plane_data,
            "fixture": {"surfaces": fixture_surfaces},
            "projection": {
                "kind": "world-polyline-tube-v2",
                "instances": _projection_entries(instances, p),
            },
        }
        diagnostics = {
            "detectionPointCount": int(len(points)),
            "tableSampleCount": int(table.sum()),
            "featureCount": int(len(features.points)),
            "fixtureSurfaceCount": len(fixture_surfaces),
            "linePrimitiveCount": len(primitives),
            "instanceCount": len(instances),
            "postprocess": refined.diagnostics,
        }
        return RebarAnalysis(
            {
                "schema": "rebar-analysis-v1",
                "samplePointCount": int(len(points)),
                "instances": instances,
                "algorithmDetails": details,
                "diagnostics": diagnostics,
                "visualization": _VISUALIZATION,
            }
        )

    def analyze(
        self, sample: np.ndarray, parameters: Mapping[str, Any]
    ) -> RebarAnalysis:
        return self._analyze_points(
            np.asarray(sample, dtype=np.float64), _Params.from_value(parameters)
        )

    @staticmethod
    def _spatial_coverage(
        context: "RebarInputContext", plane: PlaneModel | None, p: _Params
    ) -> tuple[np.ndarray, float, int]:
        anchor = np.min(np.asarray(context.sample, dtype=np.float64), axis=0)
        voxel = p.coverage_voxel_size
        passes = 0
        while True:
            passes += 1
            selected: dict[tuple[int, int, int], np.ndarray] = {}
            complete = True
            for _, xyz in context.iter_chunks():
                chunk = np.asarray(xyz, dtype=np.float64)
                if plane is not None:
                    chunk = chunk[~table_mask(chunk, plane, p.table_distance)]
                if not len(chunk):
                    continue
                keys = np.floor((chunk - anchor) / voxel).astype(np.int64)
                unique, first = np.unique(keys, axis=0, return_index=True)
                for key, row in zip(unique.tolist(), first.tolist()):
                    selected.setdefault(tuple(key), chunk[row])
                if len(selected) > int(p.coverage_point_limit * 1.12):
                    complete = False
                    break
            if complete and len(selected) <= p.coverage_point_limit:
                ordered = [selected[key] for key in sorted(selected)]
                coverage = (
                    np.asarray(ordered, dtype=np.float64).reshape((-1, 3))
                    if ordered
                    else np.empty((0, 3), dtype=np.float64)
                )
                return coverage, voxel, passes
            voxel *= 1.35
            if passes >= 8:
                # The eighth pass must still be exhaustive. Increase once more
                # and continue rather than truncating any spatial region.
                passes = 0

    @staticmethod
    def _raw_support_counts(
        context: "RebarInputContext", analysis: RebarAnalysis
    ) -> np.ndarray:
        entries = analysis.data["algorithmDetails"]["projection"]["instances"]
        index = build_segment_index(entries)
        counts = np.zeros(len(entries) + 1, dtype=np.int64)
        for _, xyz in context.iter_chunks():
            projected = project_top2(np.asarray(xyz, dtype=np.float64), index)
            competition = (projected.best_id > 0) & (projected.second_id > 0)
            separation = np.full(len(projected.best_id), np.inf, dtype=np.float64)
            separation[competition] = (
                projected.second_distance[competition]
                - projected.best_distance[competition]
            )
            decisive = (projected.best_id > 0) & (
                (projected.second_id == 0) | (separation > 0.12)
            )
            counts += np.bincount(projected.best_id[decisive], minlength=len(counts))[
                : len(counts)
            ]
        return counts

    def analyze_source(
        self, context: "RebarInputContext", parameters: Mapping[str, Any]
    ) -> RebarAnalysis:
        p = _Params.from_value(parameters)
        sample = np.asarray(context.sample, dtype=np.float64)
        if (
            sample.ndim != 2
            or sample.shape[1:] != (3,)
            or len(sample) < 3
            or not np.isfinite(sample).all()
        ):
            raise ValueError("sample must contain at least three finite XYZ points")
        plane = fit_bounded_table_plane(
            sample,
            distance=p.table_distance,
            iterations=p.table_ransac_iterations,
            minimum_ratio=p.table_min_ratio,
            max_tilt_degrees=p.table_max_tilt_degrees,
        )
        coverage, voxel, passes = self._spatial_coverage(context, plane, p)
        # Retain the sample-fitted table footprint while representing every
        # non-table source voxel in the bounded detection cloud.
        analysis = self._analyze_points(coverage, p, plane)
        analysis.data["diagnostics"]["tableSampleCount"] = (
            int(table_mask(sample, plane, p.table_distance).sum())
            if plane is not None
            else 0
        )
        counts = self._raw_support_counts(context, analysis)
        for item in analysis.data["instances"]:
            item["rawSupportCount"] = int(counts[int(item["id"])])
        bim = match_bim_instances(
            analysis.data["instances"],
            context.bim_prior,
            match_gate=p.bim_match_gate,
            registration_uncertainty=p.bim_registration_uncertainty,
        )
        analysis.data["algorithmDetails"]["projection"]["instances"] = (
            _projection_entries(analysis.data["instances"], p)
        )
        analysis.data["diagnostics"]["instanceCount"] = len(analysis.data["instances"])
        analysis.data["diagnostics"].update(
            {
                "coverageVoxelSize": voxel,
                "coveragePasses": passes,
                "sourceCoveragePointCount": int(len(coverage)),
                "fullResolutionProjectedSupportCount": int(counts.sum()),
                "bim": bim,
            }
        )
        return analysis

    @staticmethod
    def _project(points: np.ndarray, analysis: RebarAnalysis):
        details = analysis.data["algorithmDetails"]
        index = build_segment_index(details["projection"]["instances"])
        projected = project_top2(points, index)
        margin = float(details["parameters"]["ambiguity_margin"])
        competition = (projected.best_id > 0) & (projected.second_id > 0)
        difference = np.full(len(points), np.inf, dtype=np.float64)
        difference[competition] = (
            projected.second_distance[competition]
            - projected.best_distance[competition]
        )
        ambiguity = competition & (difference <= margin)
        dot = np.abs(
            np.einsum("ij,ij->i", projected.best_tangent, projected.second_tangent)
        )
        crossing = (
            (projected.best_id > 0)
            & (projected.second_id > 0)
            & (
                dot
                <= np.cos(
                    np.deg2rad(float(details["parameters"]["crossing_angle_degrees"]))
                )
            )
        )
        return projected, ambiguity, crossing

    def project_points(
        self, points_xyz: np.ndarray, analysis: RebarAnalysis
    ) -> RebarPointAttributes:
        points = np.asarray(points_xyz, dtype=np.float64)
        if (
            points.ndim != 2
            or points.shape[1:] != (3,)
            or not np.isfinite(points).all()
        ):
            raise ValueError("points must be finite XYZ")
        projected, ambiguity, crossing = self._project(points, analysis)
        details = analysis.data["algorithmDetails"]
        table = (
            table_mask(
                points, details["plane"], float(details["parameters"]["table_distance"])
            )
            if details.get("plane")
            else np.zeros(len(points), dtype=bool)
        )
        fixture = _surface_mask(points, details.get("fixture", {}).get("surfaces", []))
        # Physical scene ownership wins over a projected tube: table and
        # fixture points are always zero-labelled, so crossing/ambiguity flags
        # remain meaningful only on matched rebar.
        matched = (projected.best_id > 0) & ~table & ~fixture
        classes = np.zeros(len(points), np.uint8)
        classes[matched] = REBAR_STEEL
        classes[ambiguity & matched] = REBAR_AMBIGUOUS
        directions = np.where(matched, projected.best_direction, 0).astype(np.uint16)
        instances = np.where(matched, projected.best_id, 0).astype(np.uint32)
        instances[ambiguity & matched] = AMBIGUOUS_INSTANCE
        flags = np.zeros(len(points), np.uint8)
        flags[crossing & matched] |= FLAG_CROSSING
        flags[ambiguity & matched] |= FLAG_AMBIGUOUS
        scene = np.zeros(len(points), np.uint8)
        scene[table] = SCENE_TABLE
        scene[fixture] = SCENE_FIXTURE
        scene[matched] = SCENE_REBAR
        return RebarPointAttributes(classes, directions, instances, scene, flags)

    def project_candidates(
        self, points_xyz: np.ndarray, analysis: RebarAnalysis
    ) -> dict[str, np.ndarray]:
        points = np.asarray(points_xyz, dtype=np.float64)
        projected, ambiguity, _ = self._project(points, analysis)
        details = analysis.data["algorithmDetails"]
        table = (
            table_mask(
                points, details["plane"], float(details["parameters"]["table_distance"])
            )
            if details.get("plane")
            else np.zeros(len(points), dtype=bool)
        )
        fixture = _surface_mask(points, details.get("fixture", {}).get("surfaces", []))
        rows = np.flatnonzero(
            ambiguity
            & ~table
            & ~fixture
            & (projected.best_id > 0)
            & (projected.second_id > 0)
        ).astype(np.uint32)
        if len(rows) == 0:
            return {}
        ids = np.column_stack(
            (projected.best_id[rows], projected.second_id[rows])
        ).astype(np.uint32)
        return {
            "point_indices": rows,
            "offsets": np.arange(0, 2 * len(rows) + 1, 2, dtype=np.uint64),
            "instance_ids": ids.ravel(),
        }


__all__ = [
    "GeometricV4Adapter",
    "SCENE_CLUTTER",
    "SCENE_TABLE",
    "SCENE_REBAR",
    "SCENE_NOISE",
    "SCENE_FIXTURE",
    "FLAG_CROSSING",
    "FLAG_AMBIGUOUS",
]
