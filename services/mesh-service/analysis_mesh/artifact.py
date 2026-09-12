"""Immutable 3D Tiles artifact writer for component mesh streams."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from artifact_permissions import publish_shared_artifact_permissions

from .contracts import ComponentMeshStream, ContractError, mesh_position_hash, normalize_component_tree
from .quality import mesh_quality_metrics

ARTIFACT_VERSION = "analysis-mesh-artifact-v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _face_chunks(mesh: trimesh.Trimesh, cap: int):
    # Lexicographic center ordering keeps each bounded range reasonably local.
    centers = mesh.triangles_center
    order = np.lexsort((centers[:, 2], centers[:, 1], centers[:, 0]))
    for start in range(0, len(mesh.faces), cap):
        faces = mesh.faces[order[start:start + cap]]
        ids, inverse = np.unique(faces.reshape(-1), return_inverse=True)
        yield trimesh.Trimesh(vertices=mesh.vertices[ids], faces=inverse.reshape((-1, 3)), process=False)


def _node_extras_glb(path: Path, extras: dict[str, dict[str, Any]]) -> None:
    data = path.read_bytes()
    magic, version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2: raise ValueError("invalid GLB")
    json_len, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != b"JSON": raise ValueError("GLB JSON chunk missing")
    document = json.loads(data[20:20 + json_len].decode("utf-8"))
    for node in document.get("nodes", []):
        if node.get("name") in extras:
            node["extras"] = {**node.get("extras", {}), **extras[node["name"]]}
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    rest = data[20 + json_len:]
    rebuilt = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(payload) + len(rest)) + struct.pack("<I4s", len(payload), b"JSON") + payload + rest
    path.write_bytes(rebuilt)


def _box(mesh: trimesh.Trimesh) -> list[float]:
    low, high = mesh.bounds
    return _box_from_bounds(low, high)


def _box_from_bounds(low: np.ndarray, high: np.ndarray) -> list[float]:
    center, half = (low + high) / 2, (high - low) / 2
    return [*map(float, center), float(half[0]), 0, 0, 0, float(half[1]), 0, 0, 0, float(half[2])]


def build_artifact(stream: ComponentMeshStream, destination: str | Path, algorithm: dict[str, Any], *, face_cap: int = 250000) -> dict[str, Any]:
    if face_cap < 1: raise ValueError("face_cap must be positive")
    final = Path(destination)
    if final.exists() or final.is_symlink(): raise FileExistsError("artifact destination already exists")
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = final.parent / f".{final.name}.staging-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o750)
    try:
        tree = normalize_component_tree(stream.tree, {component.global_id for component in stream.components})
        tiles_dir = stage / "tiles"; tiles_dir.mkdir()
        component_rows, metric_rows, tile_rows = [], [], []
        root_low: np.ndarray | None = None
        root_high: np.ndarray | None = None
        tile_index = 0
        for component in stream.components:
            component_faces = 0
            parts = []
            part_metrics = []
            for part in component.parts:
                component_faces += len(part.mesh.faces)
                part_row = {"partId": part.part_id, "nodeName": part.node_name, "faceCount": len(part.mesh.faces), "positionHash": part.position_hash, "tiles": []}
                parts.append(part_row)
                analysis_quality = mesh_quality_metrics(part.mesh)
                metric = {"partId": part.part_id, "analysisQuality": analysis_quality}
                if part.remesh_diagnostics is not None:
                    metric["remeshDiagnostics"] = part.remesh_diagnostics
                if part.source_quality:
                    metric["sourceQuality"] = part.source_quality
                    source_cv = part.source_quality["edgeLength"]["coefficientOfVariation"]
                    metric["improvement"] = {
                        "edgeLengthCvRatio": analysis_quality["edgeLength"]["coefficientOfVariation"] / source_cv if source_cv > 0 else None,
                        "triangleQualityP05Delta": analysis_quality["triangleQuality"]["p05"] - part.source_quality["triangleQuality"]["p05"],
                    }
                part_metrics.append(metric)
                # chunks are per part, so neighbouring components can never be welded or co-resident by merge.
                for chunk in _face_chunks(part.mesh, face_cap):
                    tile_id = f"tile-{tile_index:06d}"; tile_index += 1
                    output = tiles_dir / f"{tile_id}.glb"
                    scene = trimesh.Scene(); node = f"{component.global_id}/{part.part_id}"
                    scene.add_geometry(chunk, node_name=node, geom_name="mesh")
                    output.write_bytes(trimesh.exchange.gltf.export_glb(scene))
                    position_hash = mesh_position_hash(chunk)
                    _node_extras_glb(output, {node: {"ifcGlobalId": component.global_id, "partId": part.part_id, "tileId": tile_id, "positionHash": position_hash}})
                    low, high = chunk.bounds
                    root_low = low.copy() if root_low is None else np.minimum(root_low, low)
                    root_high = high.copy() if root_high is None else np.maximum(root_high, high)
                    tile_row = {"tileId": tile_id, "uri": f"tiles/{tile_id}.glb", "ifcGlobalId": component.global_id, "partId": part.part_id, "positionHash": position_hash, "vertexCount": len(chunk.vertices), "faceCount": len(chunk.faces), "byteLength": output.stat().st_size, "sha256": _sha(output), "boundingVolume": {"box": _box(chunk)}}
                    tile_rows.append(tile_row)
                    part_row["tiles"].append(tile_id)
            component_rows.append({"ifcGlobalId": component.global_id, "parts": parts})
            metric_rows.append({"ifcGlobalId": component.global_id, "faceCount": component_faces, "partCount": len(parts), "parts": part_metrics})
        if not tile_rows or root_low is None or root_high is None:
            raise ContractError("analysis mesh contains no triangle tiles")
        if stream.source_bounds is None:
            source_low, source_high = root_low, root_high
        else:
            source_low = np.asarray(stream.source_bounds[0], dtype=np.float64)
            source_high = np.asarray(stream.source_bounds[1], dtype=np.float64)
        if source_low.shape != (3,) or source_high.shape != (3,) or not np.isfinite([source_low, source_high]).all() or np.any(source_low > source_high):
            raise ContractError("analysis mesh source bounds are invalid")
        normalization_center = (source_low + source_high) / 2.0
        (stage / "components.json").write_text(json.dumps({"schema": "analysis-mesh-components-v1", "tree": tree, "components": component_rows, "tiles": tile_rows}, indent=2), encoding="utf-8")
        (stage / "metrics.json").write_text(json.dumps({"schema": "analysis-mesh-metrics-v1", "components": metric_rows}, indent=2), encoding="utf-8")
        # Tile GLBs and their bounding volumes are both authored in the model frame.
        # Declare that frame explicitly so 3d-tiles-renderer does not apply its Y-up
        # fallback rotation to content while leaving the bounding volumes untouched.
        # The root has no content of its own, so it must have a positive error to
        # force traversal into the renderable leaf tiles. With zero error the
        # renderer can stop at the empty root and display no analysis mesh.
        root_geometric_error = max(float(np.linalg.norm(root_high - root_low)), 1e-6)
        tileset = {"asset": {"version": "1.1", "gltfUpAxis": "Z"}, "geometricError": root_geometric_error,
                   "root": {"boundingVolume": {"box": _box_from_bounds(root_low, root_high)}, "geometricError": root_geometric_error,
                            "refine": "ADD", "children": [{"boundingVolume": t["boundingVolume"], "geometricError": 0, "extras": {"tileId": t["tileId"], "ifcGlobalId": t["ifcGlobalId"], "partId": t["partId"], "positionHash": t["positionHash"]}, "content": {"uri": t["uri"]}} for t in tile_rows]}}
        (stage / "tileset.json").write_text(json.dumps(tileset, indent=2), encoding="utf-8")
        files = {str(p.relative_to(stage)): {"sha256": _sha(p), "byteLength": p.stat().st_size} for p in stage.rglob("*") if p.is_file()}
        aggregate = hashlib.sha256()
        for relative, row in sorted(files.items()):
            aggregate.update(relative.encode("utf-8")); aggregate.update(b"\0"); aggregate.update(row["sha256"].encode("ascii"))
        manifest = {"artifactVersion": ARTIFACT_VERSION, "immutable": True, "entryPath": "tileset.json", "componentsPath": "components.json", "metricsPath": "metrics.json", "algorithm": algorithm,
                    "componentCount": len(component_rows), "tileCount": len(tile_rows), "faceCap": face_cap,
                    "modelFrame": {"sourceBounds": {"min": source_low.tolist(), "max": source_high.tolist()}, "normalizationCenter": normalization_center.tolist()},
                    "contentHash": aggregate.hexdigest(), "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        publish_shared_artifact_permissions(stage)
        # A same-filesystem rename is the publication point: readers observe either no artifact or all files.
        os.replace(stage, final)
        return json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
