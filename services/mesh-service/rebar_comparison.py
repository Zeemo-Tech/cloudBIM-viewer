"""Instance-constrained rebar cloud-to-mesh comparison helpers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import laspy
import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree

from algorithms.c2m_distance import colorize_mesh_by_signed_distance, compute_statistics
from analysis_c2m.core import C2MContractError, _regular_under, _tile_mesh, load_analysis_mesh
from analysis_mesh.contracts import mesh_position_hash
from rebar_metrics import measure_bar, surface_summary
from rebar_scan_surface import ObservedRebarSurface
from rebar_control_comparison import (
    ControlNetObservedSurface, build_control_units, build_inspection, control_envelope,
    refresh_inspection_tolerance,
)
from rebar_spacing import spacing_rows
from rebar_solid import RebarSolid

SCHEMA = "rebar-comparison-v1"
INSTANCE_SCHEMA = "rebar-instance-map-v1"
INSTANCE_DIMENSION = "cloudbim_instance_id"
ALGORITHM_VERSION = "c2m-rebar-instance-v3"
CONTROL_ALGORITHM_VERSION = "c2m-rebar-instance-v4"
COVERAGE_MAX_DISTANCE = 0.2
UNKNOWN_COLOR = np.array([168, 178, 193], dtype=np.float64) / 255.0


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load_instance_map(path: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise C2MContractError("instanceMapPath must be a regular non-symlink file")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise C2MContractError("instance map cannot be parsed") from exc
    if not isinstance(value, dict) or value.get("schema") != INSTANCE_SCHEMA:
        raise C2MContractError("unsupported instance map; rerun denoise")
    inventory = value.get("inventory")
    instances = value.get("instances")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("bars"), list) or not isinstance(inventory.get("units"), list) or not isinstance(instances, list):
        raise C2MContractError("instance map has no valid inventory")
    return value, _sha(source)


def _validated_directory(value: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, str], dict[str, list[int]], dict[str, list[int]], dict[str, bool]]:
    inventory = value["inventory"]
    bars: list[dict[str, Any]] = []
    ids: set[str] = set()
    guids: set[str] = set()
    for row in inventory["bars"]:
        if not isinstance(row, dict):
            raise C2MContractError("rebar directory contains an invalid row")
        design_id, guid = row.get("designBarId"), row.get("ifcGlobalId")
        if not isinstance(design_id, str) or not design_id or not isinstance(guid, str) or not guid:
            raise C2MContractError("resolved rebar directory contains an invalid identity")
        if design_id in ids or guid in guids:
            raise C2MContractError("rebar design identity is ambiguous")
        ids.add(design_id); guids.add(guid)
        bars.append({"designBarId": design_id, "ifcGlobalId": guid, "name": str(row.get("name") or ""),
                     "coverage": str(row.get("coverage") or "complete")})
    if not bars:
        raise C2MContractError("instance map contains no physical rebar")

    bar_ids = {row["designBarId"] for row in bars}
    unit_owner: dict[str, str] = {}
    units_by_bar = {design_id: [] for design_id in bar_ids}
    for row in inventory["units"]:
        if not isinstance(row, dict) or not isinstance(row.get("designUnitId"), str) or not row["designUnitId"] or not isinstance(row.get("designBarId"), str):
            raise C2MContractError("rebar topology contains an invalid unit")
        unit_id, design_id = row["designUnitId"], row["designBarId"]
        if unit_id in unit_owner or design_id not in bar_ids:
            raise C2MContractError("rebar topology contains a duplicate or orphan unit")
        unit_owner[unit_id] = design_id; units_by_bar[design_id].append(unit_id)

    matched_owner: dict[int, str] = {}
    matched_by_bar = {row["designBarId"]: [] for row in bars}
    review_by_bar = {row["designBarId"]: [] for row in bars}
    intrinsic_review = {
        row["designBarId"]: not units_by_bar[row["designBarId"]] or row["coverage"] == "unresolved"
        for row in bars
    }
    seen_instances: set[int] = set()
    matched_units: set[str] = set()
    for row in value["instances"]:
        if not isinstance(row, dict) or type(row.get("id")) is not int or not 0 < row["id"] <= np.iinfo(np.uint32).max or row["id"] in seen_instances:
            raise C2MContractError("instance map contains an invalid or duplicate instance ID")
        seen_instances.add(row["id"])
        design_id = row.get("designBarId")
        unit_id = row.get("designUnitId")
        status = row.get("reviewStatus")
        if status == "matched" and (not isinstance(design_id, str) or design_id not in matched_by_bar):
            raise C2MContractError("matched instance has no physical rebar")
        if status == "matched" and isinstance(design_id, str) and design_id in matched_by_bar:
            if unit_id is not None:
                if not isinstance(unit_id, str) or unit_owner.get(unit_id) != design_id or unit_id in matched_units:
                    raise C2MContractError("matched instance conflicts with rebar topology")
                matched_units.add(unit_id)
            matched_owner[row["id"]] = design_id
            matched_by_bar[design_id].append(row["id"])
        elif (status != "missing" or (isinstance(row.get("pointCount"), (int, float))
                                      and row.get("pointCount", 0) > 0)) and isinstance(design_id, str) and design_id in review_by_bar:
            review_by_bar[design_id].append(row["id"])
    return bars, matched_owner, matched_by_bar, review_by_bar, intrinsic_review


def _downsample(points: np.ndarray, voxel_size: float, enabled: bool) -> np.ndarray:
    if not enabled or len(points) == 0:
        return points
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    return np.asarray(cloud.voxel_down_sample(voxel_size).points, dtype=np.float64)


def _topology_segments(instance_map: dict[str, Any], alignment: np.ndarray) -> dict[str, list[tuple[str, np.ndarray, np.ndarray]]]:
    """Read only declared local topology; absent geometry means no constraint."""
    result: dict[str, list[tuple[str, np.ndarray, np.ndarray]]] = {}
    for unit in instance_map["inventory"]["units"]:
        if not isinstance(unit, dict):
            continue
        design_id = unit.get("designBarId")
        try:
            start = np.asarray(unit["startM"], dtype=float)
            end = np.asarray(unit["endM"], dtype=float)
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(design_id, str) or start.shape != (3,) or end.shape != (3,) or not np.isfinite(start).all() or not np.isfinite(end).all():
            continue
        # Inventory is in the scan/design coordinate frame used by denoise.
        # The C2M alignment applies only its rigid linear part to directions.
        if np.linalg.norm(end - start) <= 1e-9:
            continue
        start4 = alignment @ np.r_[start, 1.0]
        end4 = alignment @ np.r_[end, 1.0]
        if abs(start4[3]) <= 1e-12 or abs(end4[3]) <= 1e-12:
            continue
        result.setdefault(design_id, []).append((str(unit.get("designUnitId") or ""), start4[:3] / start4[3], end4[:3] / end4[3]))
    return result



def _as_open3d(meshes: list[trimesh.Trimesh]) -> o3d.geometry.TriangleMesh:
    vertices: list[np.ndarray] = []
    triangles: list[np.ndarray] = []
    offset = 0
    for mesh in meshes:
        xyz = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int32)
        vertices.append(xyz); triangles.append(faces + offset); offset += len(xyz)
    result = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(np.concatenate(vertices, axis=0)),
        o3d.utility.Vector3iVector(np.concatenate(triangles, axis=0)),
    )
    result.vertex_normals = o3d.utility.Vector3dVector(np.concatenate([m.vertex_normals for m in meshes]))
    return result


def statistics_for_finite(distances: np.ndarray, max_histogram_distance: float, histogram_bins: int, tolerance: float) -> dict[str, Any]:
    finite = np.asarray(distances)[np.isfinite(distances)]
    if len(finite):
        return compute_statistics(finite, max_histogram_distance, histogram_bins, tolerance=tolerance)
    edges = np.linspace(-max_histogram_distance, max_histogram_distance, histogram_bins + 1)
    return {"stats": None, "histogram": {"binEdges": edges.tolist(), "counts": [0] * histogram_bins, "overflowCount": 0}}


def colorize_with_unknown(mesh: o3d.geometry.TriangleMesh, distances: np.ndarray, cap: float, tolerance: float) -> None:
    values = np.asarray(distances, dtype=np.float64)
    if np.isinf(values).any():
        raise ValueError("distances must not contain infinity")
    known = np.isfinite(values)
    safe = np.where(known, values, 0.0)
    colorize_mesh_by_signed_distance(mesh, safe, cap, tolerance)
    colors = np.asarray(mesh.vertex_colors)
    colors[~known] = UNKNOWN_COLOR
    mesh.vertex_colors = o3d.utility.Vector3dVector(colors)


def _bar_stats(values: np.ndarray, max_histogram_distance: float, histogram_bins: int, tolerance: float) -> dict[str, Any] | None:
    finite = values[np.isfinite(values)]
    return compute_statistics(finite, max_histogram_distance, histogram_bins, tolerance=tolerance)["stats"] if len(finite) else None


def compute_instance_comparison(
    scan_path: str, analysis_mesh_path: str, instance_map_path: str,
    alignment_matrix: list[float], *, voxel_size: float, downsample_enabled: bool,
    max_histogram_distance: float, histogram_bins: int, tolerance: float,
    knn_k: int = 32, normal_constraint_enabled: bool = False,
    normal_half_space_only: bool = False, normal_max_angle_deg: float = 30.0,
    normal_fallback_mode: str = "unknown",
    max_search_distance: float = COVERAGE_MAX_DISTANCE,
) -> dict[str, Any]:
    """Build one contiguous mesh and distance vector without cross-bar neighbours."""
    if not np.isfinite(max_search_distance) or not 0.0001 <= max_search_distance <= COVERAGE_MAX_DISTANCE:
        raise C2MContractError("max_search_distance must be between 0.0001 and 0.2 m")
    if normal_constraint_enabled and (normal_half_space_only or normal_fallback_mode != "unknown"):
        raise C2MContractError("same-side normal mode requires bidirectional search and unknown fallback")
    started = perf_counter()
    instance_map, instance_hash = _load_instance_map(instance_map_path)
    try:
        control = control_envelope(instance_map)
    except ValueError as exc:
        raise C2MContractError(str(exc)) from exc
    algorithm_version = CONTROL_ALGORITHM_VERSION if control is not None else ALGORITHM_VERSION
    bars, matched_owner, matched_by_bar, review_by_bar, intrinsic_review = _validated_directory(instance_map)
    root, _analysis_manifest, tile_bindings = load_analysis_mesh(analysis_mesh_path)
    by_guid: dict[str, list[dict[str, Any]]] = {}
    for binding in tile_bindings.values():
        by_guid.setdefault(binding["ifcGlobalId"], []).append(binding)

    with laspy.open(scan_path) as reader:
        if INSTANCE_DIMENSION not in reader.header.point_format.dimension_names:
            raise C2MContractError("scan has no rebar instance IDs; rerun denoise")
    las = laspy.read(scan_path)
    points = np.column_stack((las.x, las.y, las.z)).astype(np.float64, copy=False)
    owners = np.asarray(las[INSTANCE_DIMENSION], dtype=np.uint32)
    if len(points) == 0:
        raise C2MContractError("scan contains no rebar points")
    raw_bbox = {"min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist()}
    declared_ids = {row["id"] for row in instance_map["instances"]}
    undeclared = set(map(int, np.unique(owners))) - declared_ids - {0}
    if undeclared:
        raise C2MContractError("scan contains instance IDs absent from the instance map")
    matrix = np.asarray(alignment_matrix, dtype=np.float64).reshape(4, 4).T
    topology = _topology_segments(instance_map, matrix)
    transformed_by_instance: dict[int, np.ndarray] = {}
    for instance_id in matched_owner:
        selected = points[owners == instance_id]
        selected = _downsample(selected, voxel_size, downsample_enabled)
        if len(selected):
            selected = (matrix @ np.column_stack((selected, np.ones(len(selected)))).T).T[:, :3]
        transformed_by_instance[instance_id] = selected
    matched_unit_points: dict[str, np.ndarray] = {}
    for instance in instance_map["instances"]:
        unit_id = instance.get("designUnitId")
        instance_id = instance.get("id")
        if (instance.get("reviewStatus") == "matched" and isinstance(unit_id, str)
                and instance_id in transformed_by_instance):
            matched_unit_points[unit_id] = transformed_by_instance[instance_id]
    try:
        control_units = build_control_units(instance_map, matrix, matched_unit_points) if control is not None else {}
    except ValueError as exc:
        raise C2MContractError(str(exc)) from exc
    all_transformed = (matrix @ np.column_stack((points, np.ones(len(points)))).T).T[:, :3]
    transformed_bbox = {"min": all_transformed.min(axis=0).tolist(), "max": all_transformed.max(axis=0).tolist()}

    prepared = perf_counter()
    meshes: list[trimesh.Trimesh] = []
    all_distances: list[np.ndarray] = []
    rows: list[dict[str, Any]] = []
    used_instance_ids: set[int] = set()
    phase_times = {"meshLoad": 0., "distanceQuery": 0., "measurements": 0.}
    radii = {u["designUnitId"]: u.get("diameterM", 0) / 2 for u in instance_map["inventory"]["units"] if isinstance(u.get("diameterM"), (int, float)) and u["diameterM"] > 0}
    vertex_start = 0
    desired_guids = {bar["ifcGlobalId"] for bar in bars}
    for bar in bars:
        bar_started = perf_counter()
        tile_meshes: list[trimesh.Trimesh] = []
        part_ids: list[str] = []
        for binding in by_guid.get(bar["ifcGlobalId"], []):
            tile = _tile_mesh(_regular_under(root, binding["uri"]))
            if len(tile.vertices) != binding["vertexCount"] or mesh_position_hash(tile) != binding["positionHash"]:
                raise C2MContractError(f"analysis tile binding mismatch: {binding['tileId']}")
            tile_meshes.append(tile)
            part_ids.append(binding["partId"])
        solid = RebarSolid(tile_meshes, part_ids) if tile_meshes else None
        phase_times["meshLoad"] += perf_counter() - bar_started
        query_started = perf_counter()
        matched_ids = matched_by_bar[bar["designBarId"]]
        review_ids = review_by_bar[bar["designBarId"]]
        groups = [transformed_by_instance[instance_id] for instance_id in matched_ids if len(transformed_by_instance[instance_id])]
        after_count = sum(len(group) for group in groups)
        has_tiles = bool(tile_meshes)
        requires_review = intrinsic_review[bar["designBarId"]] or bool(review_ids) or not has_tiles
        status = "review" if requires_review else "matched" if matched_ids and after_count else "missing"
        used_ids = matched_ids if status == "matched" else []
        deferred_ids = review_ids + ([] if status == "matched" else matched_ids)
        raw_count = int(np.count_nonzero(np.isin(owners, np.asarray(used_ids, dtype=np.uint32))))
        review_count = int(np.count_nonzero(np.isin(owners, np.asarray(deferred_ids, dtype=np.uint32))))
        if status != "matched":
            groups = []; after_count = 0
        used_instance_ids.update(used_ids)
        bar_vertex_count = sum(len(tile.vertices) for tile in tile_meshes)
        values = np.full(bar_vertex_count, np.nan, dtype=np.float32)
        measurement_points = np.concatenate(groups, axis=0) if groups else np.empty((0, 3), dtype=np.float64)
        all_vertices = np.concatenate([np.asarray(tile.vertices, dtype=np.float64) for tile in tile_meshes], axis=0) if tile_meshes else np.empty((0, 3))
        bar_segments = topology.get(bar["designBarId"], [])
        unit_points = {}
        for instance in instance_map["instances"]:
            if instance["id"] in used_ids and instance.get("designUnitId"):
                unit_points[instance["designUnitId"]] = transformed_by_instance[instance["id"]]
        # A bar-level instance is unambiguous only for a single design unit.
        # Multi-limb bars require explicit unit ownership; never borrow a limb.
        if len(bar_segments) == 1:
            unit_points[bar_segments[0][0]] = measurement_points
        measure_started = perf_counter()
        measurement = measure_bar(values, all_vertices, bar_segments, measurement_points,
                                  unit_points=unit_points, radii=radii)
        if control is not None and normal_constraint_enabled:
            observed_surface = ControlNetObservedSurface(
                bar_segments, {unit_id: control_units[unit_id] for unit_id in unit_points if unit_id in control_units})
        else:
            observed_surface = ObservedRebarSurface(bar_segments, unit_points, measurement['longitudinalProfile']) if normal_constraint_enabled else None
        phase_times["measurements"] += perf_counter() - measure_started
        query_started = perf_counter()
        constraint_known = 0
        constraint_fallback = 0
        if status == "matched" and bar_vertex_count:
            scan_points = np.concatenate(groups, axis=0)
            measurement_points = scan_points
            tree = cKDTree(scan_points) if not normal_constraint_enabled else None
            cursor = 0
            for tile in tile_meshes:
                vertices = np.asarray(tile.vertices, dtype=np.float64)
                normals = np.asarray(tile.vertex_normals, dtype=np.float64)
                if observed_surface is not None:
                    tile_values, _indices, constrained = observed_surface.match(
                        vertices, normals, k=min(knn_k, 64),
                        max_angle_deg=normal_max_angle_deg, half_space_only=normal_half_space_only,
                        max_search_distance=max_search_distance,
                    )
                else:
                    distances, indices = tree.query(vertices, k=1, workers=-1)
                    signs = np.where(np.einsum("ij,ij->i", normals, scan_points[indices] - vertices) >= 0, 1.0, -1.0)
                    tile_values = signs * distances
                tile_values[np.abs(tile_values) > max_search_distance] = np.nan
                if observed_surface is not None:
                    valid = np.isfinite(tile_values)
                    constraint_known += int(np.count_nonzero(constrained & valid))
                    constraint_fallback += int(np.count_nonzero(~constrained & valid))
                tile_values = np.asarray(tile_values, dtype=np.float32)
                values[cursor:cursor + len(vertices)] = tile_values
                cursor += len(vertices)
        phase_times["distanceQuery"] += perf_counter() - query_started
        measure_started = perf_counter()
        known = int(np.count_nonzero(np.isfinite(values)))
        measurement['surface'] = surface_summary(values, all_vertices)
        phase_times["measurements"] += perf_counter() - measure_started
        rows.append({
            **{key: bar[key] for key in ("designBarId", "ifcGlobalId", "name")},
            "instanceIds": used_ids, "reviewInstanceIds": deferred_ids,
            "pointCount": int(raw_count), "reviewPointCount": int(review_count), "pointsAfter": int(after_count),
            "vertexStart": vertex_start, "vertexCount": bar_vertex_count,
            "knownCount": known, "unknownCount": int(bar_vertex_count - known), "status": status,
            "stats": _bar_stats(values, max_histogram_distance, histogram_bins, tolerance),
            "measurement": measurement,
            "timingSeconds": {"total": round(perf_counter() - bar_started, 6)},
            "constraint": {"enabled": normal_constraint_enabled,
                           "controlNetEnabled": control is not None,
                           "topologyAvailable": bool(topology.get(bar["designBarId"])),
                           "constrainedKnownCount": constraint_known,
                           "fallbackKnownCount": constraint_fallback},
            "solid": solid.diagnostics if solid is not None else None,
            "scanSurface": observed_surface.diagnostics if observed_surface is not None else None,
        })
        vertex_start += bar_vertex_count
        meshes.extend(tile_meshes); all_distances.append(values)
    if not meshes:
        raise C2MContractError("analysis mesh contains no tiles for the rebar directory")
    combined_mesh = _as_open3d(meshes)
    distances = np.concatenate(all_distances).astype(np.float32, copy=False)
    known_count = int(np.count_nonzero(np.isfinite(distances)))
    diagnostics = {
        "schema": SCHEMA, "bars": rows,
        "algorithmVersion": algorithm_version,
        "excludedComponentCount": len(set(by_guid) - desired_guids),
        "unassignedPointCount": int(np.count_nonzero(~np.isin(owners, np.fromiter(used_instance_ids, dtype=np.uint32)))),
        "knownVertexCount": known_count, "unknownVertexCount": int(len(distances) - known_count),
        "instanceMapHash": instance_hash,
        "measurement": {"schema": "rebar-measurement-v1", "coordinateFrame": "model",
                        "method": ("control-net-real-point-radial-correspondence-v1" if control is not None and normal_constraint_enabled else
                                   "observed-axis-same-side-normal-v3" if normal_constraint_enabled else "outward-signed-nearest-v2")},
        "effective": {"knnK": min(max(1, knn_k), 64), "normalConstraintEnabled": normal_constraint_enabled,
                      "normalHalfSpaceOnly": normal_half_space_only, "normalMaxAngleDeg": normal_max_angle_deg,
                      "normalFallbackMode": normal_fallback_mode, "maxSearchDistance": max_search_distance},
        "timings": {**{key: round(value, 6) for key, value in phase_times.items()}, "loadAndTransform": round(prepared - started, 6),
                    "barComparison": round(perf_counter() - prepared, 6),
                    "total": round(perf_counter() - started, 6)},
    }
    if control is not None:
        bars_by_id = {row["designBarId"]: row for row in bars}
        spacing, spacing_unavailable = spacing_rows(instance_map["inventory"], control_units,
                                                     bars_by_id, tolerance, alignment=matrix)
        diagnostics["inspection"] = build_inspection(
            instance_map, instance_hash, alignment_matrix, rows, control_units,
            spacing, spacing_unavailable, tolerance,
            same_side_surface=normal_constraint_enabled)
    mesh_points = np.asarray(combined_mesh.vertices)
    return {
        "mesh": combined_mesh, "distances": distances, "rebarComparison": diagnostics,
        "pointsBefore": len(points), "pointsAfter": sum(row["pointsAfter"] for row in rows),
        "scanBboxRaw": raw_bbox, "scanBboxAfterTransform": transformed_bbox,
        "meshBbox": {"min": mesh_points.min(axis=0).tolist(), "max": mesh_points.max(axis=0).tolist()},
        **statistics_for_finite(distances, max_histogram_distance, histogram_bins, tolerance),
    }


def refresh_rebar_comparison(report: dict[str, Any], distances: np.ndarray, max_histogram_distance: float, histogram_bins: int, tolerance: float) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("schema") not in {"rebar-comparison-v1", SCHEMA} or not isinstance(report.get("bars"), list):
        raise ValueError("rebar_comparison has an unsupported schema")
    result = deepcopy(report); cursor = 0
    for row in result["bars"]:
        if not isinstance(row, dict) or type(row.get("vertexStart")) is not int or type(row.get("vertexCount")) is not int or row["vertexStart"] != cursor or row["vertexCount"] < 0:
            raise ValueError("rebar comparison vertex ranges are invalid")
        stop = cursor + row["vertexCount"]
        if stop > len(distances):
            raise ValueError("rebar comparison vertex range exceeds distances")
        values = distances[cursor:stop]; known = int(np.count_nonzero(np.isfinite(values)))
        row["knownCount"] = known; row["unknownCount"] = int(len(values) - known)
        row["stats"] = _bar_stats(values, max_histogram_distance, histogram_bins, tolerance)
        cursor = stop
    if cursor != len(distances):
        raise ValueError("rebar comparison vertex ranges do not cover distances")
    result["knownVertexCount"] = int(np.count_nonzero(np.isfinite(distances)))
    result["unknownVertexCount"] = int(len(distances) - result["knownVertexCount"])
    if isinstance(result.get("inspection"), dict):
        refresh_inspection_tolerance(result["inspection"], tolerance)
        by_id = {row.get("designBarId"): row for row in result["bars"]}
        for bar in result["inspection"].get("bars", []):
            stats = by_id.get(bar.get("designBarId"), {}).get("stats") or {}
            value = stats.get("withinToleranceRatio")
            bar["withinToleranceRatio"] = float(value) if isinstance(value, (int, float)) and np.isfinite(value) else None
    return result
