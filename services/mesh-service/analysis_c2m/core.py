"""Tile-streaming C2M v1 result package builder."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from algorithms.c2m_distance import apply_transform, column_major_to_matrix4, load_and_downsample_las
from analysis_mesh.contracts import ContractError, mesh_position_hash, normalize_component_tree
from artifact_permissions import publish_shared_artifact_permissions

SCHEMA = "analysis-c2m-result-v1"
ALGORITHM = {"id": "c2m-tile-nearest-v1", "label": "C2M nearest scan points per analysis tile", "implementationVersion": "1.2.0", "contractVersion": "1", "capabilities": ["tile-streaming", "unknown-nan", "input-hash-binding", "source-model-frame", "optional-downsampling"]}
DEFAULTS = {"voxelSize": 0.02, "downsampleEnabled": True, "coverageMaxDistance": 0.2, "knnK": 1}
_SAFE_TILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class C2MContractError(ValueError): pass


def effective_parameters(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {}; unknown = set(value) - set(DEFAULTS)
    if unknown: raise C2MContractError("unknown parameters: " + ", ".join(sorted(unknown)))
    result = {**DEFAULTS, **value}
    if any(isinstance(result[k], bool) or not isinstance(result[k], (int, float)) or result[k] <= 0 for k in ("voxelSize", "coverageMaxDistance")):
        raise C2MContractError("voxelSize and coverageMaxDistance must be positive numbers")
    if not isinstance(result["downsampleEnabled"], bool):
        raise C2MContractError("downsampleEnabled must be a boolean")
    if isinstance(result["knnK"], bool) or not isinstance(result["knnK"], int) or not 1 <= result["knnK"] <= 64:
        raise C2MContractError("knnK must be an integer from 1 to 64")
    return result


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def _aggregate_hash(files: dict[str, dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for relative, row in sorted(files.items()):
        digest.update(relative.encode("utf-8")); digest.update(b"\0"); digest.update(row["sha256"].encode("ascii"))
    return digest.hexdigest()


def _regular_under(root: Path, relative: str) -> Path:
    path = root / relative
    try: resolved = path.resolve(strict=True); resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc: raise C2MContractError(f"artifact path invalid: {relative}") from exc
    # resolve() alone is insufficient: input artifacts must not hide links anywhere.
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink(): raise C2MContractError(f"artifact symlink is forbidden: {relative}")
    if not resolved.is_file(): raise C2MContractError(f"artifact file missing: {relative}")
    return resolved


def load_analysis_mesh(artifact: str | Path) -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]:
    root = Path(artifact)
    if root.is_symlink() or not root.is_dir(): raise C2MContractError("analysisMeshPath must be a non-symlink directory")
    manifest_path = _regular_under(root, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_frame = manifest.get("modelFrame")
    source_bounds = model_frame.get("sourceBounds") if isinstance(model_frame, dict) else None
    low = source_bounds.get("min") if isinstance(source_bounds, dict) else None
    high = source_bounds.get("max") if isinstance(source_bounds, dict) else None
    center = model_frame.get("normalizationCenter") if isinstance(model_frame, dict) else None
    vectors = (low, high, center)
    valid_frame = all(isinstance(row, list) and len(row) == 3 and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in row) for row in vectors)
    if valid_frame:
        valid_frame = all(low[index] <= high[index] and abs(center[index] - (low[index] + high[index]) / 2) <= 1e-9 for index in range(3))
    if manifest.get("artifactVersion") != "analysis-mesh-artifact-v1" or not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("contentHash"), str) or not valid_frame:
        raise C2MContractError("unsupported analysis mesh manifest")
    required = {"components.json", "tileset.json"}
    if not required <= set(manifest["files"]): raise C2MContractError("analysis mesh manifest missing required files")
    for relative, entry in manifest["files"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("byteLength"), int): raise C2MContractError("invalid analysis mesh file entry")
        path = _regular_under(root, relative)
        if path.stat().st_size != entry["byteLength"] or _sha(path) != entry["sha256"]: raise C2MContractError(f"analysis mesh hash mismatch: {relative}")
    if _aggregate_hash(manifest["files"]) != manifest["contentHash"]: raise C2MContractError("analysis mesh aggregate hash mismatch")
    components = json.loads(_regular_under(root, "components.json").read_text(encoding="utf-8"))
    rows = components.get("tiles") if isinstance(components, dict) else None
    if not isinstance(components, dict) or components.get("schema") != "analysis-mesh-components-v1" or not isinstance(rows, list) or len(rows) != manifest.get("tileCount"): raise C2MContractError("components.json has no valid tile registry")
    component_rows = components.get("components")
    if not isinstance(component_rows, list) or len(component_rows) != manifest.get("componentCount"):
        raise C2MContractError("components.json has no valid component registry")
    component_ids = [row.get("ifcGlobalId") for row in component_rows if isinstance(row, dict)]
    if len(component_ids) != len(component_rows) or any(not isinstance(value, str) or not value.strip() for value in component_ids) or len(set(component_ids)) != len(component_ids):
        raise C2MContractError("invalid analysis mesh component identity")
    try:
        if normalize_component_tree(components.get("tree"), set(component_ids)) != components.get("tree"):
            raise C2MContractError("analysis mesh IFC tree omits a geometry component")
    except ContractError as exc:
        raise C2MContractError(str(exc)) from exc
    tiles: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("tileId", "uri", "ifcGlobalId", "partId", "positionHash", "sha256")) or not isinstance(row.get("vertexCount"), int):
            raise C2MContractError("invalid analysis mesh tile binding")
        if not _SAFE_TILE_ID.fullmatch(row["tileId"]) or row["tileId"] in tiles: raise C2MContractError("invalid or duplicate analysis mesh tileId")
        declared = manifest["files"].get(row["uri"])
        if not isinstance(declared, dict) or declared.get("sha256") != row["sha256"] or declared.get("byteLength") != row.get("byteLength"): raise C2MContractError("analysis mesh tile registry differs from manifest")
        _regular_under(root, row["uri"]); tiles[row["tileId"]] = row
    return root, manifest, tiles


def _stats(values: np.ndarray) -> dict[str, Any]:
    known = values[np.isfinite(values)]
    result = {"knownCount": int(len(known)), "unknownCount": int(len(values) - len(known))}
    if len(known): result.update({"min": float(known.min()), "max": float(known.max()), "mean": float(known.mean()), "std": float(known.std())})
    return result


class _StatsAccumulator:
    """Streaming summary: retain no previous tile's vertex distances."""
    def __init__(self) -> None: self.known = self.unknown = 0; self.total = self.squares = 0.0; self.low = math.inf; self.high = -math.inf
    def add(self, values: np.ndarray) -> None:
        valid = values[np.isfinite(values)]; self.unknown += int(len(values) - len(valid)); self.known += int(len(valid))
        if len(valid): self.total += float(valid.sum()); self.squares += float(np.square(valid, dtype=np.float64).sum()); self.low = min(self.low, float(valid.min())); self.high = max(self.high, float(valid.max()))
    def result(self) -> dict[str, Any]:
        out = {"knownCount": self.known, "unknownCount": self.unknown}
        if self.known:
            mean = self.total / self.known; out.update({"min": self.low, "max": self.high, "mean": mean, "std": max(0.0, self.squares / self.known - mean * mean) ** .5})
        return out


def _tile_mesh(path: Path) -> trimesh.Trimesh:
    scene = trimesh.load(path, force="scene", process=False)
    meshes = scene.dump(concatenate=False)
    if len(meshes) != 1: raise C2MContractError("each analysis tile must contain exactly one mesh")
    return meshes[0]


def _signed_nearest(mesh: trimesh.Trimesh, scan_points: np.ndarray, tree: cKDTree, knn_k: int) -> np.ndarray:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if len(vertices) == 0: raise C2MContractError("analysis tile has no vertices")
    k = min(knn_k, len(scan_points))
    distances, indices = tree.query(vertices, k=k, workers=-1)
    if k > 1:
        # k is part of the stable contract for future constrained selection; v1
        # remains strict nearest-neighbour and therefore consumes column zero.
        distances, indices = distances[:, 0], indices[:, 0]
    nearest = scan_points[indices]
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    signs = np.where(np.einsum("ij,ij->i", normals, nearest - vertices) >= 0, 1.0, -1.0)
    return np.asarray(signs * distances, dtype=np.float32)


def build_c2m_artifact(scan_path: str | Path, analysis_mesh_path: str | Path, output_path: str | Path, transform: list[float], parameters: dict[str, Any] | None, algorithm_id: str = ALGORITHM["id"], *, scan_content_hash: str | None = None) -> dict[str, Any]:
    if algorithm_id != ALGORITHM["id"]: raise C2MContractError("unknown algorithm id")
    if len(transform) != 16 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in transform): raise C2MContractError("transform must contain 16 finite floats")
    params = effective_parameters(parameters); root, input_manifest, tile_bindings = load_analysis_mesh(analysis_mesh_path)
    actual_scan_hash = _sha(Path(scan_path))
    if scan_content_hash is not None and actual_scan_hash.lower() != scan_content_hash.lower():
        raise C2MContractError("scan content hash mismatch")
    output = Path(output_path)
    if output.exists() or output.is_symlink(): raise FileExistsError("outputPath already exists")
    output.parent.mkdir(parents=True, exist_ok=True); stage = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"; stage.mkdir(mode=0o750)
    try:
        scan, points_before, _bbox = load_and_downsample_las(
            str(scan_path),
            params["voxelSize"],
            downsample_enabled=params["downsampleEnabled"],
        )
        apply_transform(scan, column_major_to_matrix4(transform))
        scan_points = np.asarray(scan.points, dtype=np.float64)
        if len(scan_points) == 0: raise C2MContractError("scan contains no points after downsampling")
        scan_tree = cKDTree(scan_points)
        distances_dir = stage / "distances"; distances_dir.mkdir()
        tiles_out, per_component, global_stats = [], {}, _StatsAccumulator()
        for tile_id, binding in tile_bindings.items():
            mesh = _tile_mesh(_regular_under(root, binding["uri"]))
            if len(mesh.vertices) != binding["vertexCount"] or mesh_position_hash(mesh) != binding["positionHash"]: raise C2MContractError(f"tile binding mismatch: {tile_id}")
            values = _signed_nearest(mesh, scan_points, scan_tree, params["knnK"])
            values = np.asarray(values, dtype="<f4"); values[np.abs(values) > params["coverageMaxDistance"]] = np.nan
            relative = f"distances/{tile_id}.f32"; target = stage / relative; target.write_bytes(values.astype("<f4", copy=False).tobytes())
            row = {"tileId": tile_id, "ifcGlobalId": binding["ifcGlobalId"], "partId": binding["partId"], "positionHash": binding["positionHash"], "vertexCount": binding["vertexCount"], "distancePath": relative, "sha256": _sha(target), "byteLength": target.stat().st_size, "stats": _stats(values)}
            tiles_out.append(row); global_stats.add(values); per_component.setdefault(binding["ifcGlobalId"], _StatsAccumulator()).add(values)
        components_out = [{"ifcGlobalId": gid, "stats": accumulator.result()} for gid, accumulator in per_component.items()]
        files = {str(p.relative_to(stage)): {"sha256": _sha(p), "byteLength": p.stat().st_size} for p in stage.rglob("*") if p.is_file()}
        manifest = {"schema": SCHEMA, "immutable": True, "contentHash": _aggregate_hash(files), "unknownEncoding": {"type": "ieee754-float32", "value": "NaN", "byteOrder": "little-endian"}, "algorithm": {**ALGORITHM, "effectiveParameters": params}, "inputAnalysisMesh": {"contentHash": input_manifest.get("contentHash"), "artifactVersion": input_manifest.get("artifactVersion"), "modelFrame": input_manifest.get("modelFrame")}, "scan": {"contentHash": actual_scan_hash, "pointsBefore": points_before, "pointsAfter": len(scan.points)}, "transformColumnMajor": transform, "tiles": tiles_out, "components": components_out, "global": global_stats.result(), "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        publish_shared_artifact_permissions(stage)
        os.replace(stage, output)
        return json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        shutil.rmtree(stage, ignore_errors=True); raise
