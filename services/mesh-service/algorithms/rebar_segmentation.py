"""Training-free geometric baseline for planar rebar point clouds.

The module deliberately has no dependency on the mesh-service HTTP layer.  It
implements the first PoC described in ``docs/research``: find a supporting
plane, establish a plane-local coordinate frame, select a height band, vote
for unoriented line directions with local PCA, then recover parallel bar axes
and bridge short axial visibility gaps.

All distances are expressed in metres.  A support point may belong to an axis
in each direction.  This soft membership at crossings is intentional: a
connected-component algorithm would otherwise merge an orthogonal grid into
one instance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from numbers import Integral, Real
from typing import Any, Mapping

import numpy as np
from scipy.spatial import cKDTree


class RebarSegmentationError(RuntimeError):
    """Base class for expected, user-actionable segmentation failures."""


class InvalidPointCloudError(RebarSegmentationError):
    """The input is not a finite, non-degenerate XYZ point cloud."""


class ScaleValidationError(RebarSegmentationError):
    """The point cloud scale or sampling density is outside the PoC contract."""


class PlaneDetectionError(RebarSegmentationError):
    """No sufficiently supported slab/formwork plane could be recovered."""


class InsufficientRebarEvidenceError(RebarSegmentationError):
    """The selected height band does not support the requested rebar model."""


@dataclass(frozen=True)
class RebarSegmentationParams:
    """Explicit parameters for :func:`segment_rebar_points`.

    Defaults target metre-valued, sub-centimetre samples of a roughly
    0.5--5 m slab patch.  Callers must override them for another acquisition
    scale rather than relying on implicit unit inference.
    """

    random_seed: int = 20260905
    min_point_count: int = 200
    max_point_count: int = 500_000
    min_scene_extent: float = 0.20
    max_scene_extent: float = 20.0
    max_median_nn_spacing: float = 0.030
    plane_ransac_iterations: int = 500
    plane_distance_threshold: float = 0.003
    min_plane_inliers: int = 80
    min_plane_inlier_ratio: float = 0.25
    ransac_confidence: float = 0.99
    max_plane_tilt_deg: float = 30.0
    up_hint_x: float = 0.0
    up_hint_y: float = 0.0
    up_hint_z: float = 1.0
    min_rebar_height: float = 0.008
    max_rebar_height: float = 0.080
    height_cluster_gap: float = 0.015
    min_height_band_points: int = 60
    pca_radius: float = 0.032
    pca_min_neighbors: int = 7
    pca_max_neighbors: int = 64
    min_linearity: float = 0.55
    direction_count: int = 2
    direction_bin_count: int = 180
    direction_tolerance_deg: float = 12.0
    min_direction_separation_deg: float = 45.0
    min_direction_votes: int = 15
    offset_cluster_gap: float = 0.014
    min_axis_spacing: float = 0.030
    axis_distance_threshold: float = 0.008
    min_axis_directional_support: int = 10
    axial_sample_gap: float = 0.035
    bridge_gap: float = 0.130
    min_segment_points: int = 3
    min_line_support: int = 16
    min_line_length: float = 0.25
    max_axis_candidates_per_direction: int = 256

    @classmethod
    def from_value(
        cls,
        value: "RebarSegmentationParams | Mapping[str, Any]",
    ) -> "RebarSegmentationParams":
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            known = {field.name for field in fields(cls)}
            unknown = sorted(str(key) for key in value if key not in known)
            if unknown:
                raise InvalidPointCloudError(
                    f"unknown rebar segmentation parameters: {', '.join(unknown)}"
                )
            try:
                result = cls(**dict(value))
            except (TypeError, ValueError) as exc:
                raise InvalidPointCloudError(f"invalid segmentation parameters: {exc}") from exc
        else:
            raise InvalidPointCloudError("params must be a mapping or RebarSegmentationParams")
        normalized: dict[str, Any] = {}
        for field in fields(cls):
            field_value = getattr(result, field.name)
            if isinstance(field_value, bool):
                normalized[field.name] = field_value
            elif isinstance(field_value, Integral):
                normalized[field.name] = int(field_value)
            elif isinstance(field_value, Real):
                normalized[field.name] = float(field_value)
            else:
                normalized[field.name] = field_value
        result = cls(**normalized)
        result.validate()
        return result

    def validate(self) -> None:
        integer_names = {
            "random_seed",
            "min_point_count",
            "max_point_count",
            "plane_ransac_iterations",
            "min_plane_inliers",
            "min_height_band_points",
            "pca_min_neighbors",
            "pca_max_neighbors",
            "direction_count",
            "direction_bin_count",
            "min_direction_votes",
            "min_axis_directional_support",
            "min_segment_points",
            "min_line_support",
            "max_axis_candidates_per_direction",
        }
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise InvalidPointCloudError(
                    f"parameter {field.name} must be a real number"
                )
            if field.name in integer_names and not isinstance(value, Integral):
                raise InvalidPointCloudError(
                    f"parameter {field.name} must be an integer"
                )
        positive = (
            "min_point_count",
            "max_point_count",
            "min_scene_extent",
            "max_scene_extent",
            "max_median_nn_spacing",
            "plane_ransac_iterations",
            "plane_distance_threshold",
            "min_plane_inliers",
            "max_plane_tilt_deg",
            "min_rebar_height",
            "max_rebar_height",
            "height_cluster_gap",
            "min_height_band_points",
            "pca_radius",
            "pca_min_neighbors",
            "pca_max_neighbors",
            "direction_count",
            "direction_bin_count",
            "direction_tolerance_deg",
            "min_direction_separation_deg",
            "min_direction_votes",
            "offset_cluster_gap",
            "min_axis_spacing",
            "axis_distance_threshold",
            "min_axis_directional_support",
            "axial_sample_gap",
            "bridge_gap",
            "min_segment_points",
            "min_line_support",
            "min_line_length",
            "max_axis_candidates_per_direction",
        )
        for name in positive:
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise InvalidPointCloudError(
                    f"parameter {name} must be finite and positive"
                )
        if self.random_seed < 0:
            raise InvalidPointCloudError("random_seed must be a non-negative integer")
        if self.min_point_count > self.max_point_count:
            raise InvalidPointCloudError(
                "min_point_count must not exceed max_point_count"
            )
        if self.min_scene_extent >= self.max_scene_extent:
            raise InvalidPointCloudError("min_scene_extent must be less than max_scene_extent")
        if not 0 < self.min_plane_inlier_ratio <= 1:
            raise InvalidPointCloudError("min_plane_inlier_ratio must be in (0, 1]")
        if not 0 < self.ransac_confidence < 1:
            raise InvalidPointCloudError("ransac_confidence must be in (0, 1)")
        required_iterations = int(
            np.ceil(
                np.log1p(-self.ransac_confidence)
                / np.log1p(-(self.min_plane_inlier_ratio ** 3))
            )
        )
        if self.plane_ransac_iterations < required_iterations:
            raise InvalidPointCloudError(
                "plane_ransac_iterations is too low for min_plane_inlier_ratio "
                f"and ransac_confidence; expected at least {required_iterations}"
            )
        if not 0 < self.max_plane_tilt_deg < 90:
            raise InvalidPointCloudError("max_plane_tilt_deg must be in (0, 90)")
        if self.min_rebar_height >= self.max_rebar_height:
            raise InvalidPointCloudError("min_rebar_height must be less than max_rebar_height")
        if self.pca_min_neighbors < 3:
            raise InvalidPointCloudError("pca_min_neighbors must be at least 3")
        if self.pca_min_neighbors > self.pca_max_neighbors:
            raise InvalidPointCloudError(
                "pca_min_neighbors must not exceed pca_max_neighbors"
            )
        if not 0 <= self.min_linearity < 1:
            raise InvalidPointCloudError("min_linearity must be in [0, 1)")
        if not 0 < self.direction_tolerance_deg < 45:
            raise InvalidPointCloudError("direction_tolerance_deg must be in (0, 45)")
        if not 0 < self.min_direction_separation_deg <= 90:
            raise InvalidPointCloudError(
                "min_direction_separation_deg must be in (0, 90]"
            )
        if self.axis_distance_threshold >= self.offset_cluster_gap:
            raise InvalidPointCloudError(
                "axis_distance_threshold must be less than offset_cluster_gap"
            )
        if self.offset_cluster_gap >= self.min_axis_spacing:
            raise InvalidPointCloudError(
                "offset_cluster_gap must be less than min_axis_spacing"
            )
        up_hint = np.array([self.up_hint_x, self.up_hint_y, self.up_hint_z], dtype=np.float64)
        if not np.isfinite(up_hint).all() or np.linalg.norm(up_hint) < 1e-12:
            raise InvalidPointCloudError("up_hint must be a finite non-zero vector")


@dataclass(frozen=True)
class _PlaneFrame:
    origin: np.ndarray
    x_axis: np.ndarray
    y_axis: np.ndarray
    normal: np.ndarray
    inlier_indices: np.ndarray
    residuals: np.ndarray

    def world_to_local(self, points: np.ndarray) -> np.ndarray:
        centered = points - self.origin
        return np.column_stack(
            (centered @ self.x_axis, centered @ self.y_axis, centered @ self.normal)
        )

    def local_to_world(self, points: np.ndarray) -> np.ndarray:
        return (
            self.origin
            + points[:, [0]] * self.x_axis
            + points[:, [1]] * self.y_axis
            + points[:, [2]] * self.normal
        )


def segment_rebar_points(
    points_xyz: Any,
    params: RebarSegmentationParams | Mapping[str, Any],
) -> dict[str, Any]:
    """Segment a planar, approximately orthogonal rebar grid.

    Parameters
    ----------
    points_xyz:
        Array-like with shape ``(N, 3)``.  Coordinates must be in metres.
    params:
        :class:`RebarSegmentationParams` or a mapping of explicit overrides.

    Returns
    -------
    dict
        A JSON-serializable result containing plane/frame metadata, main
        directions, individual line instances, point-index support sets,
        spacing measurements, and diagnostics.

    Raises
    ------
    RebarSegmentationError
        A domain-specific subclass describes validation, scale, plane, or
        rebar-evidence failures.  The function never returns a successful but
        empty result.
    """

    config = RebarSegmentationParams.from_value(params)
    points = _validate_points(points_xyz, config)
    median_nn_spacing, robust_extent = _validate_scale(points, config)
    frame = _fit_plane_frame(points, config)
    local = frame.world_to_local(points)

    height_mask = (
        (local[:, 2] >= config.min_rebar_height)
        & (local[:, 2] <= config.max_rebar_height)
    )
    height_indices = np.flatnonzero(height_mask)
    if height_indices.size < config.min_height_band_points:
        raise InsufficientRebarEvidenceError(
            "height band contains "
            f"{height_indices.size} points; expected at least {config.min_height_band_points}"
        )
    band_points = local[height_indices]

    pca = _local_pca_candidates(band_points, config)
    if pca["indices"].size < config.direction_count * config.min_direction_votes:
        raise InsufficientRebarEvidenceError(
            "local PCA found too few linear points for the requested direction model: "
            f"{pca['indices'].size}"
        )
    directions = _estimate_unoriented_directions(pca, config)

    instances: list[dict[str, Any]] = []
    direction_results: list[dict[str, Any]] = []
    next_instance_id = 0
    for direction_index, direction in enumerate(directions):
        recovered, axis_offsets = _extract_parallel_instances(
            local=local,
            height_indices=height_indices,
            band_points=band_points,
            pca=pca,
            direction=direction,
            direction_index=direction_index,
            frame=frame,
            config=config,
            first_instance_id=next_instance_id,
        )
        if not recovered:
            raise InsufficientRebarEvidenceError(
                f"direction {direction_index} did not yield a line instance"
            )
        instances.extend(recovered)
        next_instance_id += len(recovered)

        unique_offsets = sorted(set(float(offset) for offset in axis_offsets))
        spacings = np.diff(unique_offsets).tolist() if len(unique_offsets) > 1 else []
        world_direction = (
            direction["vector"][0] * frame.x_axis
            + direction["vector"][1] * frame.y_axis
        )
        spacing_summary = _spacing_summary(spacings)
        direction_results.append(
            {
                "direction_index": direction_index,
                "angle_degrees_mod_180": float(np.degrees(direction["angle"])),
                "angle_degrees_signed": float(
                    np.degrees(
                        np.arctan2(direction["vector"][1], direction["vector"][0])
                    )
                ),
                "local_vector": direction["vector"].tolist(),
                "world_vector": world_direction.tolist(),
                "pca_vote_count": int(direction["vote_count"]),
                "axis_count": len(unique_offsets),
                "axis_offsets": unique_offsets,
                "spacings": spacings,
                "spacing_summary": spacing_summary,
            }
        )

    membership_counts: dict[int, int] = {}
    membership_directions: dict[int, set[int]] = {}
    for instance in instances:
        for point_index in instance["support_point_indices"]:
            membership_counts[point_index] = membership_counts.get(point_index, 0) + 1
            membership_directions.setdefault(point_index, set()).add(
                int(instance["direction_index"])
            )
    soft_indices = sorted(
        index
        for index, directions_for_point in membership_directions.items()
        if len(directions_for_point) > 1
    )
    rebar_indices = sorted(membership_counts)

    plane_residuals = frame.residuals[frame.inlier_indices]
    result: dict[str, Any] = {
        "schema_version": "rebar-geometric-poc-v2",
        "units": "metre",
        "parameters": asdict(config),
        "plane": {
            "origin": frame.origin.tolist(),
            "normal": frame.normal.tolist(),
            "x_axis": frame.x_axis.tolist(),
            "y_axis": frame.y_axis.tolist(),
            "equation": [
                float(frame.normal[0]),
                float(frame.normal[1]),
                float(frame.normal[2]),
                float(-np.dot(frame.normal, frame.origin)),
            ],
            "support_count": int(frame.inlier_indices.size),
            "support_ratio": float(frame.inlier_indices.size / points.shape[0]),
            "rmse": float(np.sqrt(np.mean(np.square(plane_residuals)))),
        },
        "directions": direction_results,
        "instances": instances,
        "point_sets": {
            "plane_inlier_indices": frame.inlier_indices.tolist(),
            "height_band_indices": height_indices.tolist(),
            "linear_candidate_indices": height_indices[pca["indices"]].tolist(),
            "rebar_support_indices": rebar_indices,
            "soft_assigned_intersection_indices": soft_indices,
        },
        "diagnostics": {
            "input_point_count": int(points.shape[0]),
            "robust_scene_extent": float(robust_extent),
            "median_nearest_neighbor_spacing": float(median_nn_spacing),
            "height_band_point_count": int(height_indices.size),
            "height_band_quantiles": {
                "p05": float(np.percentile(band_points[:, 2], 5)),
                "median": float(np.median(band_points[:, 2])),
                "p95": float(np.percentile(band_points[:, 2], 95)),
            },
            "linear_candidate_count": int(pca["indices"].size),
            "instance_count": len(instances),
            "rebar_support_point_count": len(rebar_indices),
            "soft_assigned_point_count": len(soft_indices),
        },
    }
    return result


def _validate_points(points_xyz: Any, config: RebarSegmentationParams) -> np.ndarray:
    try:
        points = np.asarray(points_xyz, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise InvalidPointCloudError("points_xyz must be numeric XYZ coordinates") from exc
    if points.ndim != 2 or points.shape[1] != 3:
        raise InvalidPointCloudError(
            f"points_xyz must have shape (N, 3), got {points.shape}"
        )
    if points.shape[0] < config.min_point_count:
        raise InvalidPointCloudError(
            f"point cloud has {points.shape[0]} points; expected at least {config.min_point_count}"
        )
    if points.shape[0] > config.max_point_count:
        raise ScaleValidationError(
            f"point cloud has {points.shape[0]} points; downsample to at most "
            f"{config.max_point_count} detection points"
        )
    if not np.isfinite(points).all():
        raise InvalidPointCloudError("point cloud contains NaN or infinite coordinates")
    return np.ascontiguousarray(points)


def _validate_scale(
    points: np.ndarray,
    config: RebarSegmentationParams,
) -> tuple[float, float]:
    # Percentile extent does not let a few legitimate RANSAC outliers dictate
    # the unit decision.  Translation is intentionally irrelevant.
    low, high = np.percentile(points, [1.0, 99.0], axis=0)
    robust_extent = float(np.linalg.norm(high - low))
    if not config.min_scene_extent <= robust_extent <= config.max_scene_extent:
        raise ScaleValidationError(
            "robust scene extent is "
            f"{robust_extent:.6g} m, outside [{config.min_scene_extent}, "
            f"{config.max_scene_extent}] m; verify metre units"
        )

    tree = cKDTree(points)
    nearest_distances, _ = tree.query(points, k=2, workers=-1)
    positive = nearest_distances[:, 1][nearest_distances[:, 1] > 1e-12]
    if positive.size < config.min_point_count // 2:
        raise ScaleValidationError("too few distinct samples to estimate point spacing")
    median_spacing = float(np.median(positive))
    if median_spacing > config.max_median_nn_spacing:
        raise ScaleValidationError(
            "median nearest-neighbour spacing is "
            f"{median_spacing:.6g} m, above {config.max_median_nn_spacing} m; "
            "the cloud is too sparse or uses the wrong units"
        )
    return median_spacing, robust_extent


def _fit_plane_frame(points: np.ndarray, config: RebarSegmentationParams) -> _PlaneFrame:
    rng = np.random.default_rng(config.random_seed)
    centered_origin = np.mean(points, axis=0)
    centered = points - centered_origin
    up_hint = np.array(
        [config.up_hint_x, config.up_hint_y, config.up_hint_z], dtype=np.float64
    )
    up_hint /= np.linalg.norm(up_hint)
    minimum_up_alignment = float(np.cos(np.deg2rad(config.max_plane_tilt_deg)))
    best_indices: np.ndarray | None = None
    best_median = np.inf

    for _ in range(config.plane_ransac_iterations):
        sample_indices = rng.choice(points.shape[0], size=3, replace=False)
        a, b, c = centered[sample_indices]
        normal = np.cross(b - a, c - a)
        normal_norm = np.linalg.norm(normal)
        if normal_norm < 1e-12:
            continue
        normal /= normal_norm
        if abs(float(np.dot(normal, up_hint))) < minimum_up_alignment:
            continue
        distances = np.abs((centered - a) @ normal)
        indices = np.flatnonzero(distances <= config.plane_distance_threshold)
        median = float(np.median(distances[indices])) if indices.size else np.inf
        if best_indices is None or indices.size > best_indices.size or (
            indices.size == best_indices.size and median < best_median
        ):
            best_indices = indices
            best_median = median

    required = max(
        config.min_plane_inliers,
        int(np.ceil(config.min_plane_inlier_ratio * points.shape[0])),
    )
    if best_indices is None or best_indices.size < required:
        count = 0 if best_indices is None else best_indices.size
        raise PlaneDetectionError(
            f"best plane has {count} inliers; expected at least {required}"
        )

    # Refit and reselect twice so the output plane is not tied to one random
    # triplet.  SVD is performed around local means for large-coordinate safety.
    inlier_indices = best_indices
    for _ in range(2):
        plane_points = points[inlier_indices]
        origin = np.mean(plane_points, axis=0)
        _, _, vh = np.linalg.svd(plane_points - origin, full_matrices=False)
        normal = vh[-1]
        residuals = np.abs((points - origin) @ normal)
        inlier_indices = np.flatnonzero(residuals <= config.plane_distance_threshold)

    if inlier_indices.size < required:
        raise PlaneDetectionError(
            f"refitted plane has {inlier_indices.size} inliers; expected at least {required}"
        )
    plane_points = points[inlier_indices]
    origin = np.mean(plane_points, axis=0)
    _, _, vh = np.linalg.svd(plane_points - origin, full_matrices=False)
    normal = vh[-1]
    if abs(float(np.dot(normal, up_hint))) < minimum_up_alignment:
        raise PlaneDetectionError(
            "refitted plane exceeds max_plane_tilt_deg relative to up_hint"
        )
    if np.dot(normal, up_hint) < 0:
        normal = -normal

    # Use the projected global axis least parallel to the normal.  This makes
    # the frame deterministic even for a square plane whose PCA axes are tied.
    candidates = np.eye(3)
    seed_axis = candidates[np.argmin(np.abs(candidates @ normal))]
    x_axis = seed_axis - np.dot(seed_axis, normal) * normal
    x_axis /= np.linalg.norm(x_axis)
    if x_axis[np.argmax(np.abs(x_axis))] < 0:
        x_axis = -x_axis
    y_axis = np.cross(normal, x_axis)
    y_axis /= np.linalg.norm(y_axis)
    residuals = np.abs((points - origin) @ normal)
    return _PlaneFrame(
        origin=origin,
        x_axis=x_axis,
        y_axis=y_axis,
        normal=normal,
        inlier_indices=inlier_indices,
        residuals=residuals,
    )


def _local_pca_candidates(
    band_points: np.ndarray,
    config: RebarSegmentationParams,
) -> dict[str, np.ndarray]:
    tree = cKDTree(band_points)
    candidate_indices: list[int] = []
    angles: list[float] = []
    linearities: list[float] = []

    # A radius query returning every neighbourhood at once can approach O(N^2)
    # resident memory on dense scans or an accidentally large radius.  Query a
    # bounded number of nearest neighbours in batches and retain only those
    # inside the requested physical radius.
    neighbour_count = min(config.pca_max_neighbors, band_points.shape[0])
    batch_size = 4_096
    for batch_start in range(0, band_points.shape[0], batch_size):
        batch_end = min(batch_start + batch_size, band_points.shape[0])
        distances, neighbour_indices = tree.query(
            band_points[batch_start:batch_end],
            k=neighbour_count,
            distance_upper_bound=config.pca_radius,
            workers=-1,
        )
        if neighbour_count == 1:
            distances = distances[:, None]
            neighbour_indices = neighbour_indices[:, None]

        for local_index, (row_distances, row_indices) in enumerate(
            zip(distances, neighbour_indices)
        ):
            valid = np.isfinite(row_distances) & (row_indices < band_points.shape[0])
            if np.count_nonzero(valid) < config.pca_min_neighbors:
                continue
            neighbourhood = band_points[row_indices[valid]]
            covariance = np.cov(
                neighbourhood - np.mean(neighbourhood, axis=0), rowvar=False
            )
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            order = np.argsort(eigenvalues)[::-1]
            eigenvalues = np.maximum(eigenvalues[order], 0.0)
            direction_3d = eigenvectors[:, order[0]]
            if eigenvalues[0] <= 1e-15:
                continue
            linearity = float((eigenvalues[0] - eigenvalues[1]) / eigenvalues[0])
            projected = direction_3d[:2]
            projected_norm = np.linalg.norm(projected)
            if linearity < config.min_linearity or projected_norm < 0.80:
                continue
            projected /= projected_norm
            angle = float(np.arctan2(projected[1], projected[0]) % np.pi)
            candidate_indices.append(batch_start + local_index)
            angles.append(angle)
            linearities.append(linearity)

    return {
        "indices": np.asarray(candidate_indices, dtype=np.int64),
        "angles": np.asarray(angles, dtype=np.float64),
        "linearities": np.asarray(linearities, dtype=np.float64),
    }


def _estimate_unoriented_directions(
    pca: dict[str, np.ndarray],
    config: RebarSegmentationParams,
) -> list[dict[str, Any]]:
    angles = pca["angles"]
    weights = pca["linearities"]
    remaining = np.ones(angles.size, dtype=bool)
    tolerance = np.deg2rad(config.direction_tolerance_deg)
    grid = np.arange(config.direction_bin_count, dtype=np.float64)
    grid *= np.pi / config.direction_bin_count
    recovered: list[dict[str, Any]] = []

    for _ in range(config.direction_count):
        active_angles = angles[remaining]
        active_weights = weights[remaining]
        if active_angles.size < config.min_direction_votes:
            break
        # Accumulate one angular bin at a time to keep memory O(candidate_count)
        # instead of allocating candidate_count × direction_bin_count.
        scores = np.asarray(
            [
                np.sum(
                    active_weights
                    * (_angular_distance_pi(active_angles, grid_angle) <= tolerance)
                )
                for grid_angle in grid
            ],
            dtype=np.float64,
        )
        peak = float(grid[int(np.argmax(scores))])
        selected_active = _angular_distance_pi(active_angles, peak) <= tolerance
        if np.count_nonzero(selected_active) < config.min_direction_votes:
            break

        selected_angles = active_angles[selected_active]
        selected_weights = active_weights[selected_active]
        doubled_vector = np.array(
            [
                np.sum(selected_weights * np.cos(2.0 * selected_angles)),
                np.sum(selected_weights * np.sin(2.0 * selected_angles)),
            ]
        )
        refined = float(0.5 * np.arctan2(doubled_vector[1], doubled_vector[0]) % np.pi)
        selected_all = remaining & (_angular_distance_pi(angles, refined) <= tolerance)
        vote_count = int(np.count_nonzero(selected_all))
        if vote_count < config.min_direction_votes:
            break
        vector = np.array([np.cos(refined), np.sin(refined)], dtype=np.float64)
        dominant_component = int(np.argmax(np.abs(vector)))
        if vector[dominant_component] < 0:
            vector = -vector
        refined = float(np.arctan2(vector[1], vector[0]) % np.pi)
        recovered.append(
            {"angle": refined, "vector": vector, "vote_count": vote_count}
        )
        remaining[selected_all] = False

    if len(recovered) != config.direction_count:
        raise InsufficientRebarEvidenceError(
            f"recovered {len(recovered)} main directions; expected {config.direction_count}"
        )
    minimum_separation = np.deg2rad(config.min_direction_separation_deg)
    for left_index, left in enumerate(recovered):
        for right in recovered[left_index + 1 :]:
            separation = float(_angular_distance_pi(left["angle"], right["angle"]))
            if separation < minimum_separation:
                raise InsufficientRebarEvidenceError(
                    "main directions are separated by only "
                    f"{np.degrees(separation):.3f} degrees; expected at least "
                    f"{config.min_direction_separation_deg:.3f} degrees"
                )
    recovered.sort(
        key=lambda item: (
            abs(float(np.arctan2(item["vector"][1], item["vector"][0]))),
            float(item["angle"]),
        )
    )
    return recovered


def _extract_parallel_instances(
    *,
    local: np.ndarray,
    height_indices: np.ndarray,
    band_points: np.ndarray,
    pca: dict[str, np.ndarray],
    direction: dict[str, Any],
    direction_index: int,
    frame: _PlaneFrame,
    config: RebarSegmentationParams,
    first_instance_id: int,
) -> tuple[list[dict[str, Any]], list[float]]:
    vector = direction["vector"]
    perpendicular = np.array([-vector[1], vector[0]], dtype=np.float64)
    tolerance = np.deg2rad(config.direction_tolerance_deg)
    aligned_mask = _angular_distance_pi(pca["angles"], direction["angle"]) <= tolerance
    aligned_pca_indices = pca["indices"][aligned_mask]
    if aligned_pca_indices.size < config.min_direction_votes:
        return [], []

    aligned_offsets = band_points[aligned_pca_indices, :2] @ perpendicular
    aligned_weights = pca["linearities"][aligned_mask]
    clusters = _cluster_sorted_values(aligned_offsets, config.offset_cluster_gap)
    clusters = [
        cluster
        for cluster in clusters
        if cluster.size >= config.min_axis_directional_support
    ]
    axis_candidates = _merge_nearby_axis_clusters(
        clusters,
        aligned_offsets,
        aligned_weights,
        config,
    )
    if len(axis_candidates) > config.max_axis_candidates_per_direction:
        raise InsufficientRebarEvidenceError(
            f"direction {direction_index} yielded {len(axis_candidates)} axis candidates; "
            f"limit is {config.max_axis_candidates_per_direction}"
        )

    band_axial = band_points[:, :2] @ vector
    band_offsets = band_points[:, :2] @ perpendicular
    instances: list[dict[str, Any]] = []
    accepted_axis_offsets: list[float] = []
    next_instance_id = first_instance_id

    for cluster, axis_offset, tube_radius in axis_candidates:
        tube_mask = np.abs(band_offsets - axis_offset) <= tube_radius
        tube_indices = np.flatnonzero(tube_mask)
        if tube_indices.size < config.min_line_support:
            continue
        aligned_axis_mask = np.abs(aligned_offsets - axis_offset) <= tube_radius
        axis_aligned_indices = aligned_pca_indices[aligned_axis_mask]
        intervals = _bridged_intervals(band_axial[axis_aligned_indices], config)

        axis_accepted = False
        for interval_start, interval_end in intervals:
            interval_mask = (
                tube_mask
                & (band_axial >= interval_start)
                & (band_axial <= interval_end)
            )
            interval_band_indices = np.flatnonzero(interval_mask)
            length = float(interval_end - interval_start)
            if length < config.min_line_length:
                continue

            height_clusters = _cluster_indices_by_value_gap(
                band_points[interval_band_indices, 2],
                config.height_cluster_gap,
            )
            for layer_index, height_cluster in enumerate(height_clusters):
                layer_band_indices = interval_band_indices[height_cluster]
                if layer_band_indices.size < config.min_line_support:
                    continue
                layer_heights = band_points[layer_band_indices, 2]
                layer_min = float(np.min(layer_heights))
                layer_max = float(np.max(layer_heights))
                directional_in_interval = (
                    aligned_axis_mask
                    & (band_axial[aligned_pca_indices] >= interval_start)
                    & (band_axial[aligned_pca_indices] <= interval_end)
                    & (band_points[aligned_pca_indices, 2] >= layer_min)
                    & (band_points[aligned_pca_indices, 2] <= layer_max)
                )
                directional_support = int(np.count_nonzero(directional_in_interval))
                if directional_support < config.min_axis_directional_support:
                    continue

                source_indices = height_indices[layer_band_indices]
                center_height = float(np.median(layer_heights))
                local_start = np.array(
                    [
                        *(vector * interval_start + perpendicular * axis_offset),
                        center_height,
                    ],
                    dtype=np.float64,
                )
                local_end = np.array(
                    [
                        *(vector * interval_end + perpendicular * axis_offset),
                        center_height,
                    ],
                    dtype=np.float64,
                )
                world_endpoints = frame.local_to_world(
                    np.vstack((local_start, local_end))
                )
                residuals = np.abs(band_offsets[layer_band_indices] - axis_offset)
                instances.append(
                    {
                        "instance_id": f"rebar-{next_instance_id}",
                        "direction_index": direction_index,
                        "axis_offset": float(axis_offset),
                        "axis_support_radius": float(tube_radius),
                        "height_layer_index": layer_index,
                        "observed_height_range": [layer_min, layer_max],
                        "centerline": {
                            "local_start": local_start.tolist(),
                            "local_end": local_end.tolist(),
                            "world_start": world_endpoints[0].tolist(),
                            "world_end": world_endpoints[1].tolist(),
                        },
                        "length": length,
                        "support_count": int(source_indices.size),
                        "directional_support_count": directional_support,
                        "support_point_indices": source_indices.tolist(),
                        "axis_residual_rmse": float(
                            np.sqrt(np.mean(np.square(residuals)))
                        ),
                    }
                )
                next_instance_id += 1
                axis_accepted = True
        if axis_accepted:
            accepted_axis_offsets.append(float(axis_offset))

    return instances, accepted_axis_offsets


def _merge_nearby_axis_clusters(
    clusters: list[np.ndarray],
    offsets: np.ndarray,
    weights: np.ndarray,
    config: RebarSegmentationParams,
) -> list[tuple[np.ndarray, float, float]]:
    """Merge surface-side clusters that cannot be distinct physical bars."""

    if not clusters:
        return []
    summaries = sorted(
        (
            _weighted_median(offsets[cluster], weights[cluster]),
            float(np.sum(weights[cluster])),
            cluster,
        )
        for cluster in clusters
    )
    groups: list[list[tuple[float, float, np.ndarray]]] = [[summaries[0]]]
    for summary in summaries[1:]:
        if summary[0] - groups[-1][-1][0] < config.min_axis_spacing:
            groups[-1].append(summary)
        else:
            groups.append([summary])

    merged: list[tuple[np.ndarray, float, float]] = []
    for group in groups:
        group_offsets = np.asarray([item[0] for item in group], dtype=np.float64)
        group_weights = np.asarray([item[1] for item in group], dtype=np.float64)
        axis_offset = float(np.average(group_offsets, weights=group_weights))
        combined = np.concatenate([item[2] for item in group]).astype(
            np.int64, copy=False
        )
        deviations = np.abs(offsets[combined] - axis_offset)
        observed_radius = float(np.percentile(deviations, 95))
        tube_radius = min(
            0.5 * config.min_axis_spacing,
            max(
                config.axis_distance_threshold,
                observed_radius + config.plane_distance_threshold,
            ),
        )
        merged.append((combined, axis_offset, tube_radius))
    return merged


def _cluster_indices_by_value_gap(
    values: np.ndarray,
    max_gap: float,
) -> list[np.ndarray]:
    if values.size == 0:
        return []
    order = np.argsort(values, kind="stable")
    split_positions = np.flatnonzero(np.diff(values[order]) > max_gap) + 1
    return [cluster for cluster in np.split(order, split_positions) if cluster.size]


def _cluster_sorted_values(values: np.ndarray, max_gap: float) -> list[np.ndarray]:
    if values.size == 0:
        return []
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    split_positions = np.flatnonzero(np.diff(sorted_values) > max_gap) + 1
    return [cluster for cluster in np.split(order, split_positions) if cluster.size]


def _bridged_intervals(
    axial_values: np.ndarray,
    config: RebarSegmentationParams,
) -> list[tuple[float, float]]:
    order = np.argsort(axial_values, kind="stable")
    sorted_values = axial_values[order]
    split_positions = np.flatnonzero(np.diff(sorted_values) > config.axial_sample_gap) + 1
    raw_segments = [
        segment
        for segment in np.split(sorted_values, split_positions)
        if segment.size
    ]
    raw_segments = [
        segment for segment in raw_segments if segment.size >= config.min_segment_points
    ]
    if not raw_segments:
        return []

    bridged: list[list[float]] = [
        [float(raw_segments[0][0]), float(raw_segments[0][-1])]
    ]
    for segment in raw_segments[1:]:
        start, end = float(segment[0]), float(segment[-1])
        if start - bridged[-1][1] <= config.bridge_gap:
            bridged[-1][1] = end
        else:
            bridged.append([start, end])
    return [(start, end) for start, end in bridged]


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    sorted_weights = weights[order]
    cutoff = 0.5 * float(np.sum(sorted_weights))
    position = int(np.searchsorted(np.cumsum(sorted_weights), cutoff, side="left"))
    return float(sorted_values[min(position, sorted_values.size - 1)])


def _angular_distance_pi(first: np.ndarray | float, second: np.ndarray | float) -> np.ndarray:
    difference = np.abs(np.asarray(first) - np.asarray(second)) % np.pi
    return np.minimum(difference, np.pi - difference)


def _spacing_summary(spacings: list[float]) -> dict[str, float | int | None]:
    if not spacings:
        return {"count": 0, "mean": None, "median": None, "minimum": None, "maximum": None}
    values = np.asarray(spacings, dtype=np.float64)
    return {
        "count": len(spacings),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "minimum": float(np.min(values)),
        "maximum": float(np.max(values)),
    }


__all__ = [
    "InsufficientRebarEvidenceError",
    "InvalidPointCloudError",
    "PlaneDetectionError",
    "RebarSegmentationError",
    "RebarSegmentationParams",
    "ScaleValidationError",
    "segment_rebar_points",
]
