"""Read non-rebar IFC triangles in raw scan metres without inventing geometry.

IfcOpenShell applies the complete placement hierarchy and converts native IFC
units to metres. IfcConvert's application contract maps IFC (x,y,z) to GLB
(x,z,-y). The saved Three.js column-major scan->GLB matrix is then inverted.
The supplied GLB validates this convention, including origin and scale; no fit,
recentering, name inference, geometry deduplication or scan support is involved.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
import zipfile

import numpy as np


SCHEMA = "bim-scene-geometry-v1"
IFC_TO_GLB = np.array([[1., 0., 0., 0.], [0., 0., 1., 0.],
                       [0., -1., 0., 0.], [0., 0., 0., 1.]])
# Float32 GLB positions introduce sub-micrometre error in the metre-scale scene.
_GLB_TOLERANCE_M = 1e-5
_CATEGORY_TYPES = {"IfcPlate": "fixture", "IfcMember": "fixture",
                   "IfcMechanicalFastener": "fixture",
                   "IfcDiscreteAccessory": "embedded", "IfcFlowSegment": "pipe"}


class SceneGeometryError(ValueError):
    """Coordinate rejection retaining per-product parse diagnostics for callers."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def _scan_matrix(values: Any) -> np.ndarray:
    if (not isinstance(values, (list, tuple, np.ndarray)) or len(values) != 16
            or any(isinstance(value, (bool, np.bool_)) for value in values)):
        raise ValueError("scan_to_bim must contain 16 column-major numbers")
    try:
        matrix = np.asarray(values, dtype=np.float64).reshape(4, 4).T
    except (ValueError, TypeError) as exc:
        raise ValueError("scan_to_bim must contain finite numbers") from exc
    rotation = matrix[:3, :3]
    if (not np.isfinite(matrix).all()
            or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-9, rtol=0)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0)
            or not np.isclose(np.linalg.det(rotation), 1., atol=1e-6, rtol=0)):
        raise ValueError("scan_to_bim must be a finite proper rigid affine transform")
    return matrix


def _apply(vertices: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return vertices @ matrix[:3, :3].T + matrix[:3, 3]


def _glb_world_vertices(path: Path) -> dict[str, np.ndarray]:
    import trimesh

    scene = trimesh.load(str(path), force="scene", process=False)
    if not isinstance(scene, trimesh.Scene):
        raise ValueError("model_path must contain a GLB scene")
    result = {}
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph.get(node)
        geometry = scene.geometry[geometry_name]
        vertices = np.asarray(geometry.vertices, dtype=np.float64)
        if len(vertices) and np.isfinite(vertices).all():
            result[str(node)] = _apply(vertices, np.asarray(transform, dtype=np.float64))
    return result


def _validation_sample(vertices: np.ndarray) -> np.ndarray:
    """Include asymmetric extremes plus bounded spread through the vertex array."""
    ids = np.unique(np.r_[np.argmin(vertices, axis=0), np.argmax(vertices, axis=0),
                          np.linspace(0, len(vertices)-1, min(32, len(vertices)), dtype=int)])
    return vertices[ids]


def _load_scene_geometry(ifc_path: str | Path, model_path: str | Path,
                        scan_to_bim: list[float], *, workers=1) -> dict[str, Any]:
    """Return IFC objects with float64 Nx3 vertices / int64 Mx3 triangle faces.

    Categories use repaired IFC entity types only. ``diagnostics.errors`` lists
    each represented physical non-rebar product whose IFC geometry failed; no missing
    scan support is inferred. Spatial products without representations are
    separately counted. A missing/mismatched GLB or invalid alignment raises
    ValueError because the output coordinate frame cannot then be confirmed.
    Diagnostics are JSON serializable; the object geometry arrays are NumPy.
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.unit
    from scipy.spatial import cKDTree

    started = time.monotonic()
    matrix = _scan_matrix(scan_to_bim)
    inverse = np.linalg.inv(matrix)
    ifc_path, model_path = Path(ifc_path), Path(model_path)
    if not ifc_path.is_file() or not model_path.is_file():
        raise ValueError("existing ifc_path and model_path are required")
    model = ifcopenshell.open(str(ifc_path))
    glb_vertices = _glb_world_vertices(model_path)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.CONVERT_BACK_UNITS, False)
    # One iterator shares IFC mapping/shape caches across occurrences. Repeated
    # create_shape calls rebuilt that state for every bolt and plate.
    products = sorted(model.by_type("IfcProduct"), key=lambda row: row.id())
    # Openings/voids are Boolean cutters applied to their hosts, not physical
    # surfaces. IfcConvert omits them from GLB; including them both rejects a
    # correct export and would classify scan points inside empty holes as solid.
    represented = [p for p in products if not p.is_a("IfcReinforcingBar")
                   and not p.is_a("IfcFeatureElementSubtraction") and p.Representation is not None]
    shapes = {}
    if represented:
        iterator = ifcopenshell.geom.iterator(settings, model, max(1, int(workers)), include=represented)
        for shape in ifcopenshell.geom.consume_iterator(iterator):
            shapes[shape.id] = (np.asarray(shape.geometry.verts, dtype=np.float64).reshape(-1, 3),
                                np.asarray(shape.geometry.faces, dtype=np.int64).reshape(-1, 3))
    diagnostics: dict[str, Any] = {
        "schema": SCHEMA, "ifcPath": str(ifc_path), "modelPath": str(model_path),
        "ifcLengthUnitToM": float(ifcopenshell.util.unit.calculate_unit_scale(model)),
        "outputFrame": "raw_scan_metres", "ifcToGlb": "x,z,-y",
        "ifcToGlbMatrix": IFC_TO_GLB.T.ravel().tolist(),
        "scanToBimMatrix": matrix.T.ravel().tolist(),
        "bimToScanMatrix": inverse.T.ravel().tolist(),
        "ifcToGlbDeterminant": float(np.linalg.det(IFC_TO_GLB[:3, :3])),
        "scanToBimDeterminant": float(np.linalg.det(matrix[:3, :3])),
        "representedNonRebarProducts": 0, "excludedRebarProducts": 0,
        "excludedSubtractionProducts": 0, "excludedSubtractionGlobalIds": [],
        "productsWithoutRepresentation": 0, "errors": [],
        "coordinateValidation": {"toleranceM": _GLB_TOLERANCE_M,
            "method": "full world AABB and bidirectional sampled nearest vertices; no fitted transform",
            "objects": [], "missingGlbGlobalIds": []},
        "scanSupportAssessed": False,
    }
    objects = []
    validation = diagnostics["coordinateValidation"]
    category_counts, type_counts = Counter(), Counter()
    for product in products:
        if product.is_a("IfcReinforcingBar"):
            diagnostics["excludedRebarProducts"] += 1
            continue
        if product.is_a("IfcFeatureElementSubtraction"):
            diagnostics["excludedSubtractionProducts"] += 1
            diagnostics["excludedSubtractionGlobalIds"].append(str(product.GlobalId))
            continue
        if product.Representation is None:
            diagnostics["productsWithoutRepresentation"] += 1
            continue
        diagnostics["representedNonRebarProducts"] += 1
        gid, ifc_type = str(product.GlobalId), product.is_a()
        category = next((value for key, value in _CATEGORY_TYPES.items() if product.is_a(key)), "other")
        try:
            if product.id() in shapes:
                vertices, faces = shapes[product.id()]
            else:
                # Iterator omissions still get the original per-product error
                # diagnostics, including represented but non-triangulated items.
                shape = ifcopenshell.geom.create_shape(settings, product)
                vertices = np.asarray(shape.geometry.verts, dtype=np.float64).reshape(-1, 3)
                faces = np.asarray(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
            if (not len(vertices) or not len(faces) or not np.isfinite(vertices).all()
                    or np.any(faces < 0) or np.any(faces >= len(vertices))):
                raise ValueError("IFC mesh is empty, non-finite or has invalid triangle indices")
        except Exception as exc:
            diagnostics["errors"].append({"globalId": gid, "ifcId": product.id(),
                "ifcType": ifc_type, "category": category, "stage": "ifc_geometry",
                "error": f"{type(exc).__name__}: {exc}"})
            continue
        glb = _apply(vertices, IFC_TO_GLB)
        target = glb_vertices.get(gid)
        if target is None:
            validation["missingGlbGlobalIds"].append(gid)
        else:
            bounds_error = float(np.max(np.abs(
                np.array([glb.min(0), glb.max(0)]) - np.array([target.min(0), target.max(0)]))))
            sample = _validation_sample(glb)
            target_sample = _validation_sample(target)
            distance = max(float(cKDTree(target).query(sample)[0].max()),
                           float(cKDTree(glb).query(target_sample)[0].max()))
            validation["objects"].append({"globalId": gid, "bboxMaxErrorM": bounds_error,
                "sampleMaxVertexDistanceM": distance, "sampleIfcPointGlbM": sample[0].tolist(),
                "nearestGlbPointM": target[cKDTree(target).query(sample[0])[1]].tolist()})
        scan = _apply(glb, inverse)
        objects.append({"globalId": gid, "ifcId": product.id(), "name": product.Name or "",
                        "ifcType": ifc_type, "category": category,
                        "vertices": scan, "faces": faces})
        category_counts[category] += 1
        type_counts[ifc_type] += 1
    diagnostics.update({"loadedObjects": len(objects), "categoryCounts": dict(category_counts),
        "ifcTypeCounts": dict(type_counts), "parseFailureCount": len(diagnostics["errors"]),
        "vertexCount": sum(len(row["vertices"]) for row in objects),
        "triangleCount": sum(len(row["faces"]) for row in objects),
        "elapsedS": time.monotonic()-started})
    checks = validation["objects"]
    validation["matchedObjects"] = len(checks)
    validation["bboxMaxErrorM"] = max((row["bboxMaxErrorM"] for row in checks), default=None)
    validation["sampleMaxVertexDistanceM"] = max((row["sampleMaxVertexDistanceM"] for row in checks), default=None)
    validation["passed"] = bool(checks) and not validation["missingGlbGlobalIds"] and all(
        max(row["bboxMaxErrorM"], row["sampleMaxVertexDistanceM"]) <= _GLB_TOLERANCE_M for row in checks)
    if not validation["passed"]:
        raise SceneGeometryError("IFC to GLB coordinate validation failed: "
                         f"{len(checks)} matched objects, {len(validation['missingGlbGlobalIds'])} missing; "
                         f"bbox error {validation['bboxMaxErrorM']}, vertex error {validation['sampleMaxVertexDistanceM']}", diagnostics)
    return {"schema": SCHEMA, "objects": objects, "diagnostics": diagnostics}


def load_scene_geometry(ifc_path: str | Path, model_path: str | Path,
                        scan_to_bim: list[float], *, workers=1, cache_directory=None) -> dict[str, Any]:
    """Reuse validated IFC/GLB geometry, then apply this request's full inverse.

    The optional cache contains only design vertices, faces and parse evidence;
    it contains no scan rows, fitted poses, labels or registration results.
    Both file contents, reader version and extraction contract bind each entry.
    """
    started=time.monotonic()
    matrix=_scan_matrix(scan_to_bim);inverse=np.linalg.inv(matrix)
    ifc_path,model_path=Path(ifc_path),Path(model_path)
    if not ifc_path.is_file() or not model_path.is_file():
        raise ValueError('existing ifc_path and model_path are required')
    cached=None;path=None
    if cache_directory is not None:
        hashes=[]
        for source in (ifc_path,model_path):
            with source.open('rb') as stream:hashes.append(hashlib.file_digest(stream,'sha256').hexdigest())
        identity=['validated-scene-model-v2',SCHEMA,importlib.metadata.version('ifcopenshell'),*hashes]
        key=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
        directory=Path(cache_directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=directory/f'scene-{key}.npz'
        try:
            with np.load(path,allow_pickle=False) as archive:
                cached=json.loads(archive['metadata'].item())
                if cached['identity']!=identity or not cached['diagnostics']['coordinateValidation']['passed']:
                    raise ValueError('invalid scene cache identity or validation')
                for i,row in enumerate(cached['objects']):
                    row['vertices']=archive[f'v{i}'];row['faces']=archive[f'f{i}']
                    vertices,faces=row['vertices'],row['faces']
                    if (vertices.ndim!=2 or vertices.shape[1]!=3 or not np.isfinite(vertices).all()
                            or faces.ndim!=2 or faces.shape[1]!=3 or not np.issubdtype(faces.dtype,np.integer)
                            or np.any(faces<0) or np.any(faces>=len(vertices))):
                        raise ValueError('invalid scene cache arrays')
        except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile):
            cached=None
    hit=cached is not None
    if cached is None:
        cached=_load_scene_geometry(ifc_path,model_path,np.eye(4).T.ravel().tolist(),workers=workers)
        if path is not None:
            metadata={**cached,'identity':identity,
                      'objects':[{k:v for k,v in row.items() if k not in ('vertices','faces')} for row in cached['objects']]}
            arrays={'metadata':np.asarray(json.dumps(metadata,allow_nan=False))}
            for i,row in enumerate(cached['objects']):arrays.update({f'v{i}':row['vertices'],f'f{i}':row['faces']})
            descriptor,temporary=tempfile.mkstemp(prefix='.scene-',suffix='.npz',dir=path.parent)
            try:
                with os.fdopen(descriptor,'wb') as stream:np.savez(stream,**arrays)
                os.replace(temporary,path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    objects=[{**row,'vertices':_apply(row['vertices'],inverse)} for row in cached['objects']]
    diagnostics={**cached['diagnostics'],'ifcPath':str(ifc_path),'modelPath':str(model_path),
                 'scanToBimMatrix':matrix.T.ravel().tolist(),'bimToScanMatrix':inverse.T.ravel().tolist(),
                 'scanToBimDeterminant':float(np.linalg.det(matrix[:3,:3])),
                 'elapsedS':time.monotonic()-started,'modelCacheHit':hit}
    return {'schema':SCHEMA,'objects':objects,'diagnostics':diagnostics}
