"""Conservative design priors for rebar segmentation.

The public function returns centre lines in the *raw scan* frame.  IFC geometry
is in the BIM frame, so the saved column-major scan->BIM transform is inverted
at the final boundary.  Product classes are deliberately not used as a filter:
some exporters encode reinforcement as IfcPlate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import math

import numpy as np


SCHEMA = "rebar-bim-prior-v1"
_MAX_BARS = 20_000
# IfcConvert's GLB coordinates used by this application are IFC x,z,-y.  The
# saved alignment is calibrated against that GLB, never against raw STEP axes.
_IFC_TO_GLB = np.array([[1., 0., 0., 0.], [0., 0., 1., 0.], [0., -1., 0., 0.], [0., 0., 0., 1.]])


def _matrix(value: list[float]) -> np.ndarray:
    if not isinstance(value, list) or len(value) != 16:
        raise ValueError("scan_to_bim must contain 16 column-major numbers")
    if any(isinstance(v, bool) for v in value):
        raise ValueError("scan_to_bim must contain numbers, not booleans")
    try:
        result = np.asarray(value, dtype=np.float64).reshape(4, 4).T
    except (TypeError, ValueError) as exc:
        raise ValueError("scan_to_bim must contain finite numbers") from exc
    rotation = result[:3, :3]
    if (not np.isfinite(result).all() or not np.allclose(result[3], [0, 0, 0, 1], atol=1e-9)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-6)):
        raise ValueError("scan_to_bim must be a finite rigid affine transform")
    return result


def _json_points(points: np.ndarray) -> list[list[float]]:
    return [[float(v) for v in row] for row in points]


def _curve_points(curve: Any, chord_error: float = .001) -> list[list[float]]:
    """Read explicit lines/arcs, with chord error expressed in IFC length units."""
    if curve is None:
        return []
    def arc(a: np.ndarray, mid: np.ndarray, b: np.ndarray) -> list[list[float]]:
        """Arc through three 3D points."""
        ab, ac = mid - a, b - a; normal = np.cross(ab, ac)
        if np.linalg.norm(normal) < 1e-12:
            return []
        n = normal / np.linalg.norm(normal)
        center = a + (np.cross(normal, ab) * np.dot(ac, ac) + np.cross(ac, normal) * np.dot(ab, ab)) / (2 * np.dot(normal, normal))
        u = (a - center) / np.linalg.norm(a - center); v = np.cross(n, u); radius = float(np.linalg.norm(a-center))
        if radius <= 1e-12: return []
        def angle(p: np.ndarray) -> float: return math.atan2(float(np.dot(p-center, v)), float(np.dot(p-center, u)))
        start, middle, end = angle(a), angle(mid), angle(b)
        positive = (middle-start) % (2*math.pi) <= (end-start) % (2*math.pi)
        delta = (end-start) % (2*math.pi) if positive else -((start-end) % (2*math.pi))
        max_step = 2 * math.acos(max(-1.0, min(1.0, 1 - min(chord_error, radius) / radius)))
        count = min(4096, max(2, int(math.ceil(abs(delta) / max(max_step, 1e-6))) + 1))
        return [(center + radius*(math.cos(start + delta*i/(count-1))*u + math.sin(start + delta*i/(count-1))*v)).tolist() for i in range(count)]
    if curve.is_a("IfcCompositeCurve"):
        out: list[list[float]] = []
        for segment in curve.Segments:
            part = _curve_points(segment.ParentCurve, chord_error)
            if not getattr(segment, "SameSense", True): part.reverse()
            if not part: return []
            out.extend(part if not out or out[-1] != part[0] else part[1:])
        return out
    if curve.is_a("IfcTrimmedCurve") and curve.BasisCurve.is_a("IfcCircle"):
        import ifcopenshell.util.placement
        circle = curve.BasisCurve; placement = np.asarray(ifcopenshell.util.placement.get_axis2placement(circle.Position), dtype=float)
        radius = float(circle.Radius)
        def trim(value: Any) -> float | None:
            for item in value or []:
                if item.is_a("IfcParameterValue"): return float(item.wrappedValue)
                if item.is_a("IfcCartesianPoint"):
                    xyz = list(item.Coordinates)[:3]
                    p = np.asarray(xyz + [0.] * (3-len(xyz)) + [1.0]); q = np.linalg.inv(placement) @ p
                    return math.atan2(q[1], q[0])
            return None
        start, end = trim(curve.Trim1), trim(curve.Trim2)
        if start is None or end is None or radius <= 0: return []
        delta = (end-start) % (2*math.pi)
        if not curve.SenseAgreement: delta -= 2*math.pi
        count = min(4096, max(2, int(math.ceil(abs(delta) / max(2*math.acos(max(-1., 1-min(chord_error,radius)/radius)),1e-6)))+1))
        return [(placement @ np.array([radius*math.cos(start+delta*i/(count-1)), radius*math.sin(start+delta*i/(count-1)), 0, 1]))[:3].tolist() for i in range(count)]
    points = getattr(curve, "Points", None)
    if points is None:
        return []
    if hasattr(points, "CoordList"):
        coords = [list(map(float, p[:3])) + [0.] * (3-len(p[:3])) for p in points.CoordList]
        segments = getattr(curve, "Segments", None)
        if not segments:
            return coords
        # ArcIndex is precisely three indices in IFC4.  It is sampled as a
        # circular arc; all other segment forms remain explicitly unsupported.
        result: list[list[float]] = []
        for segment in segments:
            indexes = list(getattr(segment, "Indices", None) or segment.wrappedValue)
            if len(indexes) < 2 or any(i < 1 or i > len(coords) for i in indexes):
                return []
            if segment.is_a("IfcLineIndex"):
                part = [coords[index - 1] for index in indexes]
            elif segment.is_a("IfcArcIndex") and len(indexes) == 3:
                part = arc(*(np.asarray(coords[index - 1], dtype=float) for index in indexes))
            else: return []
            if not part: return []
            result.extend(part if not result or result[-1] != part[0] else part[1:])
        return result
    result: list[list[float]] = []
    for point in points:
        coords = getattr(point, "Coordinates", None)
        if coords is not None:
            result.append(list(map(float, coords[:3])) + [0.] * (3-len(coords[:3])))
    return result


def _scaled_transform(value: Any, unit: float) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64).copy()
    result[:3, 3] *= unit
    return result


def _walk_items(items: Iterable[Any], transform: np.ndarray, unit: float, path: str) -> Iterable[tuple[Any, np.ndarray, str]]:
    """Expand mapped items while retaining the occurrence transform."""
    import ifcopenshell.util.placement

    for ordinal, item in enumerate(items):
        item_path = f"{path}/item{ordinal}:{item.id()}"
        if item.is_a("IfcMappedItem"):
            mapped = item.MappingSource.MappedRepresentation
            mapping = ifcopenshell.util.placement.get_mappeditem_transformation(item)
            yield from _walk_items(mapped.Items, transform @ _scaled_transform(mapping, unit), unit, item_path)
        else:
            yield item, transform, item_path


def _extruded_points(item: Any, transform: np.ndarray, unit: float) -> tuple[np.ndarray, float] | None:
    if not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-6):
        return None
    profile = getattr(item, "SweptArea", None)
    if profile is None or not profile.is_a("IfcCircleProfileDef"):
        return None
    radius = float(profile.Radius) * unit
    if not np.isfinite(radius) or radius <= 0:
        return None
    import ifcopenshell.util.placement

    # Product placement, representation map, profile position and solid position
    # are all distinct IFC coordinate systems.
    local = _scaled_transform(ifcopenshell.util.placement.get_axis2placement(item.Position), unit)
    profile_position = getattr(profile, "Position", None)
    if profile_position is not None:
        profile_local = _scaled_transform(ifcopenshell.util.placement.get_axis2placement(profile_position), unit)
    else:
        profile_local = np.eye(4)
    direction = np.asarray(item.ExtrudedDirection.DirectionRatios[:3], dtype=np.float64)
    length = float(item.Depth) * unit
    if not np.isfinite(length) or length <= 0 or np.linalg.norm(direction) < 1e-12:
        return None
    direction /= np.linalg.norm(direction)
    # IFC coordinates are scaled before the placement transform.  Scaling the
    # translation entries handles the usual Cartesian coordinates in millimetres.
    # A profile's 2D ref-direction can rotate its circle's parameterization but
    # must not rotate an oblique solid's ExtrudedDirection.
    base = (transform @ local @ profile_local @ np.array([0., 0., 0., 1.]))[:3]
    world_direction = transform[:3, :3] @ local[:3, :3] @ direction
    world_direction /= np.linalg.norm(world_direction)
    return (np.vstack((base, base + world_direction * length)), radius)


def _swept_disk_points(item: Any, transform: np.ndarray, unit: float) -> tuple[np.ndarray, float] | None:
    if not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-6):
        return None
    # Parametric subranges need curve-specific parameter evaluation; accepting
    # the whole directrix here would invent an unbuilt extension.
    if getattr(item, "StartParam", None) is not None or getattr(item, "EndParam", None) is not None:
        return None
    radius = float(getattr(item, "Radius", 0.0)) * unit
    raw = _curve_points(getattr(item, "Directrix", None), .001 / unit)
    if radius <= 0 or len(raw) < 2:
        return None
    pts = np.asarray(raw, dtype=np.float64) * unit
    homogeneous = np.c_[pts, np.ones(len(pts))]
    return ((transform @ homogeneous.T).T[:, :3], radius)


def _basis_for_glb(raw: list[tuple[str, np.ndarray, float]], model_path: Path | None, diagnostics: dict[str, Any]) -> np.ndarray | None:
    """Select an exporter axis convention only when its GLB surface agrees.

    This intentionally has no translation fit: a bad registration or exporter
    origin must not be hidden by an arbitrary best-fit translation.
    """
    if model_path is None or not model_path.is_file():
        diagnostics["ifcToGlbBasis"] = "x,z,-y (unvalidated: no GLB)"
        return _IFC_TO_GLB
    import trimesh
    from scipy.spatial import cKDTree
    scene = trimesh.load(str(model_path), force="scene", process=False)
    if not isinstance(scene, trimesh.Scene):
        diagnostics["basisRejected"] = "invalid_glb"
        return None
    vertices: dict[str, np.ndarray] = {}
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph.get(node); mesh = scene.geometry[name].copy(); mesh.apply_transform(transform)
        vertices[str(node)] = np.asarray(mesh.vertices)
    candidates = [("identity", np.eye(4)), ("x,z,-y", _IFC_TO_GLB)]
    scores: list[tuple[float, str, np.ndarray]] = []
    for label, basis in candidates:
        distances: list[float] = []
        for gid, points, radius in raw:
            target = vertices.get(gid)
            if target is None or len(target) == 0:
                continue
            transformed = (basis @ np.c_[points, np.ones(len(points))].T).T[:, :3]
            distances.extend(cKDTree(target).query(transformed)[0].tolist())
        if distances:
            scores.append((float(np.median(distances)), label, basis))
    if not scores:
        diagnostics["basisRejected"] = "no_matching_glb_nodes"
        return None
    score, label, basis = min(scores, key=lambda x: x[0])
    # Endpoint centres are normally one radius from the faceted GLB surface.
    if score > 0.03:
        diagnostics["basisRejected"] = "surface_residual"
        diagnostics["basisResidualM"] = score
        return None
    diagnostics["ifcToGlbBasis"] = label
    diagnostics["basisResidualM"] = score
    return basis


def _ifc_bars(path: Path, model_path: Path | None, inverse: np.ndarray, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    import ifcopenshell
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit

    model = ifcopenshell.open(str(path))
    unit = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    raw: list[tuple[str, str, str, np.ndarray, float]] = []
    seen: set[str] = set()
    products = [p for p in model.by_type("IfcProduct") if getattr(p, "Representation", None)]
    for product in products:
        if len(raw) >= _MAX_BARS:
            break
        representation = product.Representation
        try:
            placement = _scaled_transform(ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement), unit)
        except Exception:
            diagnostics["unsupportedProducts"] += 1
            continue
        for representation_ordinal, shape in enumerate(representation.Representations or []):
            if len(raw) >= _MAX_BARS:
                break
            for item, transform, occurrence in _walk_items(shape.Items or [], placement, unit, f"representation{representation_ordinal}"):
                extracted = _extruded_points(item, transform, unit) if item.is_a("IfcExtrudedAreaSolid") else _swept_disk_points(item, transform, unit) if item.is_a("IfcSweptDiskSolid") else None
                if extracted is None:
                    if item.is_a("IfcSweptDiskSolid"):
                        diagnostics["unsupportedDirectrices"] += 1
                    continue
                points, radius = extracted
                key = f"{product.GlobalId}:{occurrence}"
                if key in seen or not np.isfinite(points).all():
                    continue
                seen.add(key)
                raw.append((str(product.GlobalId), key, str(product.GlobalId), points, radius))
                if len(raw) >= _MAX_BARS:
                    diagnostics["truncated"] = True
                    break
    diagnostics["ifcUnitScale"] = unit
    basis = _basis_for_glb([(gid, points, radius) for gid, _, _, points, radius in raw], model_path, diagnostics)
    if basis is None:
        return []
    bars: list[dict[str, Any]] = []
    for gid, key, global_id, points, radius in raw:
        glb = (basis @ np.c_[points, np.ones(len(points))].T).T[:, :3]
        scan = (inverse @ np.c_[glb, np.ones(len(glb))].T).T[:, :3]
        bars.append({"id": key, "ifcGlobalId": global_id, "points": _json_points(scan), "radius": float(radius), "source": "ifc"})
    return bars


def _glb_fallback(path: Path, metadata_path: Path | None, inverse: np.ndarray, diagnostics: dict[str, Any], represented_products: set[str] | None = None) -> list[dict[str, Any]]:
    """Only accept isolated, approximately circular mesh components as bars."""
    import trimesh

    scene = trimesh.load(str(path), force="scene", process=False)
    if not isinstance(scene, trimesh.Scene):
        raise ValueError("model_path must be a GLB scene")
    metadata: dict[str, Any] = {}
    if metadata_path and metadata_path.is_file():
        import json
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    elements = metadata.get("elements", {})
    element_rows = elements.values() if isinstance(elements, dict) else elements if isinstance(elements, list) else []
    known = {str(row.get("id")): row for row in element_rows if isinstance(row, dict)}
    bars: list[dict[str, Any]] = []
    for node in scene.graph.nodes_geometry:
        if str(node) in (represented_products or set()):
            continue
        transform, geometry_name = scene.graph.get(node)
        mesh = scene.geometry[geometry_name].copy(); mesh.apply_transform(transform)
        # IFC GLB exports often duplicate vertices at each triangle boundary.
        # Weld exact coordinates before asking trimesh for disconnected pieces.
        mesh.merge_vertices()
        components = mesh.split(only_watertight=False)
        for index, component in enumerate(components):
            extents = np.asarray(component.extents, dtype=np.float64)
            axis = int(np.argmax(extents)); cross = np.delete(extents, axis)
            # A rod must be isolated, elongated, and have two comparable cross axes.
            if extents[axis] < 4 * max(cross) or min(cross) <= 0 or max(cross) / min(cross) > 1.2:
                diagnostics["glbUnsupportedComponents"] += 1
                continue
            normals = np.asarray(component.face_normals)
            side = normals[np.abs(normals[:, axis]) < 0.1]
            # Square/rectangular rods also have comparable cross dimensions.  A
            # circular export has many radial facet normals; four is not enough.
            if len(np.unique(np.round(side, 2), axis=0)) < 8:
                diagnostics["glbUnsupportedComponents"] += 1
                continue
            vertices = np.asarray(component.vertices)
            lo, hi = vertices[:, axis].min(), vertices[:, axis].max()
            endpoints = []
            for value in (lo, hi):
                plane = vertices[np.isclose(vertices[:, axis], value, atol=max(extents[axis] * 1e-6, 1e-7))]
                if len(plane) < 8:
                    break
                endpoints.append(plane.mean(axis=0))
            if len(endpoints) != 2:
                diagnostics["glbUnsupportedComponents"] += 1
                continue
            radius = float(np.mean(cross) / 2)
            scan = (inverse @ np.c_[np.asarray(endpoints), np.ones(2)].T).T[:, :3]
            gid = str(node)
            bars.append({"id": f"{gid}:component:{index}", "ifcGlobalId": gid, "points": _json_points(scan), "radius": radius, "source": "glb"})
    diagnostics["glbMetadataElements"] = len(known)
    return bars


def load_bim_prior(*, ifc_path: str | None, model_path: str | None, metadata_path: str | None, scan_to_bim: list[float]) -> dict[str, Any]:
    """Load IFC rods and conservative GLB rods for unrepresented products."""
    scan_to_bim_matrix = _matrix(scan_to_bim)
    inverse = np.linalg.inv(scan_to_bim_matrix)
    diagnostics: dict[str, Any] = {"ifcUsed": False, "fallbackUsed": False, "unsupportedProducts": 0, "unsupportedDirectrices": 0, "glbUnsupportedComponents": 0, "truncated": False}
    bars: list[dict[str, Any]] = []
    if ifc_path and Path(ifc_path).is_file():
        try:
            bars = _ifc_bars(Path(ifc_path), Path(model_path) if model_path else None, inverse, diagnostics)
            diagnostics["ifcUsed"] = True
        except Exception as exc:
            diagnostics["ifcError"] = type(exc).__name__
    if model_path and Path(model_path).is_file():
        fallback = _glb_fallback(Path(model_path), Path(metadata_path) if metadata_path else None, inverse, diagnostics,
                                 {bar["ifcGlobalId"] for bar in bars})
        bars.extend(fallback)
        diagnostics["fallbackUsed"] = bool(fallback)
    diagnostics["ifcBarCount"] = sum(bar["source"] == "ifc" for bar in bars)
    diagnostics["glbBarCount"] = sum(bar["source"] == "glb" for bar in bars)
    return {"schema": SCHEMA, "bars": bars, "diagnostics": diagnostics}
