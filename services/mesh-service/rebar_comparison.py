"""Instance-constrained rebar cloud-to-mesh comparison helpers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree

from algorithms.c2m_distance import colorize_mesh_by_signed_distance, compute_statistics
from analysis_c2m.core import C2MContractError, _regular_under, _tile_mesh, load_analysis_mesh
from analysis_mesh.contracts import mesh_position_hash

SCHEMA = "rebar-comparison-v1"
INSTANCE_SCHEMA = "rebar-instance-map-v1"
INSTANCE_DIMENSION = "cloudbim_instance_id"
ALGORITHM_VERSION = "c2m-rebar-instance-v1"
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
        elif isinstance(design_id, str) and design_id in review_by_bar:
            review_by_bar[design_id].append(row["id"])
    return bars, matched_owner, matched_by_bar, review_by_bar, intrinsic_review


def _downsample(points: np.ndarray, voxel_size: float, enabled: bool) -> np.ndarray:
    if not enabled or len(points) == 0:
        return points
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    return np.asarray(cloud.voxel_down_sample(voxel_size).points, dtype=np.float64)


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
    result.compute_vertex_normals()
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
) -> dict[str, Any]:
    """Build one contiguous mesh and distance vector without cross-bar neighbours."""
    instance_map, instance_hash = _load_instance_map(instance_map_path)
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
    transformed_by_instance: dict[int, np.ndarray] = {}
    for instance_id in matched_owner:
        selected = points[owners == instance_id]
        selected = _downsample(selected, voxel_size, downsample_enabled)
        if len(selected):
            selected = (matrix @ np.column_stack((selected, np.ones(len(selected)))).T).T[:, :3]
        transformed_by_instance[instance_id] = selected
    all_transformed = (matrix @ np.column_stack((points, np.ones(len(points)))).T).T[:, :3]
    transformed_bbox = {"min": all_transformed.min(axis=0).tolist(), "max": all_transformed.max(axis=0).tolist()}

    meshes: list[trimesh.Trimesh] = []
    all_distances: list[np.ndarray] = []
    rows: list[dict[str, Any]] = []
    used_instance_ids: set[int] = set()
    vertex_start = 0
    desired_guids = {bar["ifcGlobalId"] for bar in bars}
    for bar in bars:
        tile_meshes: list[trimesh.Trimesh] = []
        for binding in by_guid.get(bar["ifcGlobalId"], []):
            tile = _tile_mesh(_regular_under(root, binding["uri"]))
            if len(tile.vertices) != binding["vertexCount"] or mesh_position_hash(tile) != binding["positionHash"]:
                raise C2MContractError(f"analysis tile binding mismatch: {binding['tileId']}")
            tile_meshes.append(tile)
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
        if status == "matched" and bar_vertex_count:
            scan_points = np.concatenate(groups, axis=0)
            tree = cKDTree(scan_points)
            cursor = 0
            for tile in tile_meshes:
                vertices = np.asarray(tile.vertices, dtype=np.float64)
                distances, indices = tree.query(vertices, k=1, workers=-1)
                normals = np.asarray(tile.vertex_normals, dtype=np.float64)
                signs = np.where(np.einsum("ij,ij->i", normals, scan_points[indices] - vertices) >= 0, 1.0, -1.0)
                tile_values = np.asarray(signs * distances, dtype=np.float32)
                tile_values[distances > COVERAGE_MAX_DISTANCE] = np.nan
                values[cursor:cursor + len(vertices)] = tile_values
                cursor += len(vertices)
        known = int(np.count_nonzero(np.isfinite(values)))
        rows.append({
            **{key: bar[key] for key in ("designBarId", "ifcGlobalId", "name")},
            "instanceIds": used_ids, "reviewInstanceIds": deferred_ids,
            "pointCount": int(raw_count), "reviewPointCount": int(review_count), "pointsAfter": int(after_count),
            "vertexStart": vertex_start, "vertexCount": bar_vertex_count,
            "knownCount": known, "unknownCount": int(bar_vertex_count - known), "status": status,
            "stats": _bar_stats(values, max_histogram_distance, histogram_bins, tolerance),
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
        "excludedComponentCount": len(set(by_guid) - desired_guids),
        "unassignedPointCount": int(np.count_nonzero(~np.isin(owners, np.fromiter(used_instance_ids, dtype=np.uint32)))),
        "knownVertexCount": known_count, "unknownVertexCount": int(len(distances) - known_count),
        "instanceMapHash": instance_hash,
    }
    mesh_points = np.asarray(combined_mesh.vertices)
    return {
        "mesh": combined_mesh, "distances": distances, "rebarComparison": diagnostics,
        "pointsBefore": len(points), "pointsAfter": sum(row["pointsAfter"] for row in rows),
        "scanBboxRaw": raw_bbox, "scanBboxAfterTransform": transformed_bbox,
        "meshBbox": {"min": mesh_points.min(axis=0).tolist(), "max": mesh_points.max(axis=0).tolist()},
        **statistics_for_finite(distances, max_histogram_distance, histogram_bins, tolerance),
    }


def refresh_rebar_comparison(report: dict[str, Any], distances: np.ndarray, max_histogram_distance: float, histogram_bins: int, tolerance: float) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or not isinstance(report.get("bars"), list):
        raise ValueError("rebar_comparison is not a rebar-comparison-v1 report")
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
    return result
