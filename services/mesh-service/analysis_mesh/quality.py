"""Mesh-uniformity measurements shared by builders and artifact reports."""

from __future__ import annotations

from typing import Any

import numpy as np
import trimesh

from .contracts import ContractError


def _percentile(values: np.ndarray, value: float) -> float:
    return float(np.percentile(values, value))


def mesh_quality_metrics(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """Return scale-aware uniformity metrics without assuming a primitive family."""
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ContractError("analysis mesh has invalid vertices")
    if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0:
        raise ContractError("analysis mesh has no triangle faces")
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ContractError("analysis mesh has out-of-range face indices")

    triangles = vertices[faces]
    face_edges = np.stack(
        (
            np.linalg.norm(triangles[:, 1] - triangles[:, 0], axis=1),
            np.linalg.norm(triangles[:, 2] - triangles[:, 1], axis=1),
            np.linalg.norm(triangles[:, 0] - triangles[:, 2], axis=1),
        ),
        axis=1,
    )
    areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    ) / 2.0
    unique_edges = np.asarray(mesh.edges_unique_length, dtype=np.float64)
    positive_edges = unique_edges[unique_edges > np.finfo(np.float64).eps]
    if len(positive_edges) == 0:
        raise ContractError("analysis mesh has no positive-length edges")

    longest = face_edges.max(axis=1)
    shortest = face_edges.min(axis=1)
    valid_faces = (shortest > np.finfo(np.float64).eps) & (areas > np.finfo(np.float64).eps)
    aspect = np.divide(longest, shortest, out=np.full_like(longest, np.inf), where=valid_faces)
    quality = np.divide(
        4.0 * np.sqrt(3.0) * areas,
        np.square(face_edges).sum(axis=1),
        out=np.zeros_like(areas),
        where=valid_faces,
    )
    edge_mean = float(positive_edges.mean())
    finite_aspect = aspect[np.isfinite(aspect)]
    return {
        "vertexCount": int(len(vertices)),
        "faceCount": int(len(faces)),
        "degenerateFaceCount": int((~valid_faces).sum()),
        "edgeLength": {
            "mean": edge_mean,
            "std": float(positive_edges.std()),
            "coefficientOfVariation": float(positive_edges.std() / edge_mean),
            "p05": _percentile(positive_edges, 5),
            "p50": _percentile(positive_edges, 50),
            "p95": _percentile(positive_edges, 95),
        },
        "triangleQuality": {
            "mean": float(quality.mean()),
            "p05": _percentile(quality, 5),
            "minimum": float(quality.min()),
        },
        "triangleAspectRatio": {
            "mean": float(finite_aspect.mean()) if len(finite_aspect) else None,
            "p95": _percentile(finite_aspect, 95) if len(finite_aspect) else None,
            "maximum": float(finite_aspect.max()) if len(finite_aspect) else None,
        },
    }
