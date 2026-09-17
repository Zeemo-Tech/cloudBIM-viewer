"""Read-only, single-bar diagnostics using the production v3 comparison path."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import laspy
import numpy as np

from analysis_c2m.core import C2MContractError, _regular_under, _tile_mesh, load_analysis_mesh
from analysis_mesh.contracts import mesh_position_hash
from rebar_comparison import (_load_instance_map, _topology_segments, _validated_directory,
                              COVERAGE_MAX_DISTANCE)
from rebar_metrics import surface_summary
from rebar_scan_surface import ObservedRebarSurface
from rebar_solid import RebarSolid
from rebar_cluster_axis import measure_clusters, partition_design_mesh

SCHEMA = "scan-bim-workbench-v1"
INSTANCE_DIMENSION = "cloudbim_instance_id"


def _error(message: str):
    raise C2MContractError(message)


def load_inputs(config_path: Path):
    """Load a local config; geometry stays on disk until a bar is selected."""
    path = Path(config_path)
    if path.is_symlink() or not path.is_file():
        _error("config_path must be a regular non-symlink JSON file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise C2MContractError("workbench config cannot be parsed") from exc
    if not isinstance(value, dict):
        _error("workbench config must be an object")
    required = ("scan_path", "analysis_mesh_path", "instance_map_path", "alignment_matrix", "parameters")
    if any(key not in value for key in required):
        _error("workbench config is missing required inputs")
    if not all(isinstance(value[key], str) and value[key] for key in required[:3]):
        _error("workbench input paths must be non-empty strings")
    matrix = value["alignment_matrix"]
    if not isinstance(matrix, list) or len(matrix) != 16:
        _error("alignment_matrix must contain 16 numbers")
    try:
        matrix_np = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError) as exc:
        raise C2MContractError("alignment_matrix must contain finite numbers") from exc
    if not np.isfinite(matrix_np).all() or not isinstance(value["parameters"], dict):
        _error("alignment_matrix must be finite and parameters must be an object")
    return deepcopy(value)


def _scan(loaded):
    try:
        with laspy.open(loaded["scan_path"]) as reader:
            if INSTANCE_DIMENSION not in reader.header.point_format.dimension_names:
                _error("scan has no rebar instance IDs; rerun denoise")
        las = laspy.read(loaded["scan_path"])
    except C2MContractError:
        raise
    except Exception as exc:
        raise C2MContractError("scan cannot be read") from exc
    points = np.column_stack((las.x, las.y, las.z)).astype(np.float64, copy=False)
    if not len(points): _error("scan contains no rebar points")
    return points, np.asarray(las[INSTANCE_DIMENSION], dtype=np.uint32)


def _context(loaded):
    mapping, mapping_hash = _load_instance_map(loaded["instance_map_path"])
    bars, _owner, matched, review, intrinsic = _validated_directory(mapping)
    points, owners = _scan(loaded)
    declared = {row["id"] for row in mapping["instances"]}
    if set(map(int, np.unique(owners))) - declared - {0}:
        _error("scan contains instance IDs absent from the instance map")
    matrix = np.asarray(loaded["alignment_matrix"], dtype=float).reshape(4, 4).T
    transformed = (matrix @ np.column_stack((points, np.ones(len(points)))).T).T[:, :3]
    return mapping, mapping_hash, bars, matched, review, intrinsic, owners, transformed, matrix


def catalog(loaded):
    mapping, _, bars, matched, review, intrinsic, owners, _points, _matrix = _context(loaded)
    topology = _topology_segments(mapping, _matrix)
    result = []
    for bar in bars:
        design_id = bar["designBarId"]
        candidate_ids = matched[design_id] + review[design_id]
        status = "review" if intrinsic[design_id] or review[design_id] else ("matched" if matched[design_id] else "missing")
        result.append({"ifcGlobalId": bar["ifcGlobalId"], "designBarId": design_id, "name": bar["name"],
                       "status": status, "pointCount": int(np.count_nonzero(np.isin(owners, candidate_ids))),
                       "unitCount": len(topology.get(design_id, []))})
    params = loaded["parameters"]
    return {"bars": result,
            "defaults": {"normalMaxAngleDeg": params.get("normal_max_angle_deg", 30),
                         "maxSearchDistanceMm": params.get("max_search_distance", COVERAGE_MAX_DISTANCE) * 1000,
                         "knnK": params.get("knn_k", 32), "maxSamples": 64, "windowScale": 1.0, "minArcCoverageDeg": 160.0},
            "source": {"scanName": Path(loaded["scan_path"]).name,
                       "algorithm": "scan-bim-independent-clusters-v1", "instanceMapSchema": mapping.get("schema")}}


def _parameters(loaded, requested):
    if not isinstance(requested, dict): _error("parameters must be an object")
    defaults = loaded["parameters"]
    values = {"normalMaxAngleDeg": defaults.get("normal_max_angle_deg", 30.0),
              "maxSearchDistanceMm": defaults.get("max_search_distance", COVERAGE_MAX_DISTANCE) * 1000,
              "knnK": defaults.get("knn_k", 32), "maxSamples": 64, "windowScale": 1.0, "minArcCoverageDeg": 160.0}
    unknown = set(requested) - set(values)
    if unknown: _error("unsupported workbench parameter: " + sorted(unknown)[0])
    values.update(requested)
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            _error(key + " must be finite numeric")
        if key in {"knnK", "maxSamples"} and int(value) != value:
            _error(key + " must be an integer")
    if not .25 <= values["windowScale"] <= 4: _error("windowScale must be between 0.25 and 4")
    if not 30 <= values["minArcCoverageDeg"] <= 300: _error("minArcCoverageDeg must be between 30 and 300")
    try:
        values["normalMaxAngleDeg"] = float(values["normalMaxAngleDeg"])
        values["maxSearchDistanceMm"] = float(values["maxSearchDistanceMm"])
        values["knnK"] = int(values["knnK"]); values["maxSamples"] = int(values["maxSamples"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise C2MContractError("workbench parameters must be numeric") from exc
    if not np.isfinite(values["normalMaxAngleDeg"]) or not 1 <= values["normalMaxAngleDeg"] <= 90: _error("normalMaxAngleDeg must be between 1 and 90")
    if not np.isfinite(values["maxSearchDistanceMm"]) or not .1 <= values["maxSearchDistanceMm"] <= 200: _error("maxSearchDistanceMm must be between 0.1 and 200")
    if not 1 <= values["knnK"] <= 64: _error("knnK must be between 1 and 64")
    if not 4 <= values["maxSamples"] <= 128: _error("maxSamples must be between 4 and 128")
    return values


def _safe(value):
    if isinstance(value, np.ndarray): return _safe(value.tolist())
    if isinstance(value, np.generic): return _safe(value.item())
    if isinstance(value, float): return value if np.isfinite(value) else None
    if isinstance(value, dict): return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_safe(v) for v in value]
    return value


def _relative_profile(profile, origin):
    rows = deepcopy(profile)
    for row in rows:
        for key in ("designCenterM", "observedCenterM", "axisStartM"):
            if row.get(key) is not None: row[key] = (np.asarray(row[key]) - origin).tolist()
        evidence = row.get("fitEvidence") or {}
        if evidence.get("candidateCenterM") is not None:
            evidence["candidateCenterM"] = (np.asarray(evidence["candidateCenterM"]) - origin).tolist()
    return rows


def run_bar(loaded, ifc_global_id: str, parameters: dict):
    started = perf_counter(); params = _parameters(loaded, parameters)
    mapping, mapping_hash, bars, matched, review, intrinsic, owners, points, matrix = _context(loaded)
    bar = next((row for row in bars if row["ifcGlobalId"] == ifc_global_id), None)
    if bar is None: _error("ifcGlobalId is not in the rebar directory")
    design_id = bar["designBarId"]; topology = _topology_segments(mapping, matrix); segments = topology.get(design_id, [])
    root, _manifest, bindings = load_analysis_mesh(loaded["analysis_mesh_path"])
    tiles = [] ; part_ids = []
    for binding in bindings.values():
        if binding["ifcGlobalId"] != ifc_global_id: continue
        tile = _tile_mesh(_regular_under(root, binding["uri"]))
        if len(tile.vertices) != binding["vertexCount"] or mesh_position_hash(tile) != binding["positionHash"]:
            _error("analysis tile binding mismatch: " + binding["tileId"])
        tiles.append(tile); part_ids.append(binding["partId"])
    candidate_ids = matched[design_id] + review[design_id]
    scan_mask = np.isin(owners, candidate_ids)
    scan_points, scan_ids = points[scan_mask], owners[scan_mask]
    status = "review" if intrinsic[design_id] or review[design_id] or not tiles else ("matched" if matched[design_id] and len(scan_points) else "missing")
    used_ids = matched[design_id] if status == "matched" else []
    used_mask = np.isin(scan_ids, used_ids)
    unit_by_instance = {row["id"]: row.get("designUnitId") for row in mapping["instances"]}
    scan_units = [unit_by_instance.get(int(instance)) for instance in scan_ids]
    unit_points, unit_indices = {}, {}
    for instance_id in used_ids:
        unit = unit_by_instance.get(instance_id)
        ix = np.flatnonzero(scan_ids == instance_id)
        if unit and len(ix): unit_points[unit] = scan_points[ix]; unit_indices[unit] = ix
    if len(segments) == 1 and len(scan_points[used_mask]):
        unit_points[segments[0][0]] = scan_points[used_mask]; unit_indices[segments[0][0]] = np.flatnonzero(used_mask)
    radii = {row["designUnitId"]: row.get("diameterM", 0) / 2 for row in mapping["inventory"]["units"]
             if isinstance(row.get("diameterM"), (int, float)) and row["diameterM"] > 0}
    solid = RebarSolid(tiles, part_ids) if tiles else None
    vertices = np.concatenate([np.asarray(tile.vertices, dtype=float) for tile in tiles]) if tiles else np.empty((0, 3))
    normals = np.concatenate([np.asarray(tile.vertex_normals, dtype=float) for tile in tiles]) if tiles else np.empty((0, 3))
    measurement = measure_clusters(vertices, segments, unit_points, radii,
                                   max_samples=params["maxSamples"], window_scale=params["windowScale"])
    surface = ObservedRebarSurface(segments, unit_points, measurement["longitudinalProfile"],
                                  unit_point_indices=unit_indices, independent_axes=True)
    values = np.full(len(vertices), np.nan); selected = np.full(len(vertices), -1, dtype=np.int64)
    reasons = ["bar-not-matched" if status != "matched" else "no-valid-candidate"] * len(vertices)
    if status == "matched" and len(vertices):
        values, selected, _known, reasons = surface.match(vertices, normals, k=params["knnK"],
            max_angle_deg=params["normalMaxAngleDeg"], max_search_distance=params["maxSearchDistanceMm"] / 1000,
            trace=True)
    # The persisted production artifact stores signed values as float32.
    values = np.asarray(values, dtype=np.float32)
    measurement["surface"] = surface_summary(values, vertices)
    origin = vertices.mean(axis=0) if len(vertices) else (scan_points.mean(axis=0) if len(scan_points) else np.zeros(3))
    scan_normals, scan_supported = surface.display_normals(len(scan_points))
    faces = []; offset = 0
    for tile in tiles:
        faces.extend((np.asarray(tile.faces, dtype=np.int64) + offset).ravel().tolist()); offset += len(tile.vertices)
    vertex_units, face_units, mesh_parts = partition_design_mesh(vertices, faces, segments)
    units = [{"id": unit, "start": (start-origin).tolist(), "end": (end-origin).tolist(), "radiusM": radii.get(unit),
              "instanceIds": [i for i in candidate_ids if unit_by_instance.get(i) == unit],
              "pointCount": sum(scan_unit == unit for scan_unit in scan_units)} for unit, start, end in segments]
    reason_counts = {reason: reasons.count(reason) for reason in sorted(set(reasons))}
    stages = [{"id": "input", "label": "Loaded full transformed scan points", "pointCount": int(len(scan_points))},
              {"id": "surface", "label": "Independent scan-cluster axis; design diameter only", "supportedPointCount": surface.diagnostics["supportedPointCount"]},
              {"id": "match", "label": "Production normal-constrained correspondence", "knownCount": int(np.isfinite(values).sum())}]
    return _safe({"schema": SCHEMA, "ifcGlobalId": ifc_global_id, "name": bar["name"], "designBarId": design_id,
      "status": status, "parameters": params, "origin": origin,
      "mesh": {"positions": (vertices-origin).ravel(), "normals": normals.ravel(), "indices": faces,
               "unitIds": vertex_units, "faceUnitIds": face_units, "parts": mesh_parts},
      "scan": {"positions": (scan_points-origin).ravel(), "normals": scan_normals.ravel(),
               "supported": scan_supported,
               "instanceIds": scan_ids, "unitIds": scan_units}, "units": units,
      "profile": _relative_profile(measurement["longitudinalProfile"], origin), "distances": values,
      "matchedScanIndices": selected, "reasons": reasons, "measurement": measurement,
      "vertexEvidence": getattr(surface, "last_trace", None),
      "stats": {"vertexCount": len(vertices), "pointCount": len(scan_points), "knownCount": int(np.isfinite(values).sum()),
                "unknownCount": int(np.isnan(values).sum()), "reasonCounts": reason_counts,
                "elapsedSeconds": perf_counter()-started},
      "provenance": {"algorithm": "scan-bim-independent-clusters-v1", "instanceMapHash": mapping_hash,
                     "axisDesignInputs": ["diameterM"], "designPartition": "analytic-unit-nearest-face",
                     "normalConstraintEnabled": True, "downsampleEnabled": False,
                     "alignmentMatrix": loaded["alignment_matrix"], "instanceMapPath": loaded["instance_map_path"],
                     "coordinateFrame": "model; mesh/scan/units/profile coordinates relative to origin; measurement coordinates absolute",
                     "solid": solid.diagnostics if solid else None, "scanPath": loaded["scan_path"], "analysisMeshPath": loaded["analysis_mesh_path"]},
      "stages": stages})
