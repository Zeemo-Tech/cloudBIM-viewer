"""Extract conservative rebar diameter and straight-length families from IFC.

Production callers must provide the IFC selected by the owner-scoped backend.
A source path alone is never evidence of a BIM association.
"""

from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


_MAX_SHAPES = 20_000
_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}

def _stat_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path), int(stat.st_size), int(stat.st_mtime_ns)


def _sha256(path: Path) -> str:
    key = _stat_key(path)
    cached = _DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    _DIGEST_CACHE[key] = value
    return value


def _unavailable(reason: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "reason": reason,
        "provenance": None,
        "diametersM": [],
        "horizontalLengthsM": [],
        "families": [],
        "counts": {"shapeCount": 0, "straightCount": 0, "bentCount": 0, "horizontalStraightCount": 0},
        "cache": {"reused": False},
    }
    if details:
        result["details"] = details
    return result


def _resolve_ifc(source_path: str | None, ifc_path: str | None) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    if ifc_path is not None:
        candidate = Path(ifc_path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            return None, None, _unavailable("ifc_path_unavailable", error=type(exc).__name__)
        if not resolved.is_file():
            return None, None, _unavailable("ifc_path_not_file")
        return resolved, {"selection": "explicit"}, None

    reason = "no_unambiguous_ifc_association" if source_path is not None else "ifc_path_or_associated_source_required"
    return None, None, _unavailable(reason)


def _rounded(value: float, places: int = 9) -> float:
    return float(round(float(value), places))


def _shape_record(points: np.ndarray, radius: float, product: Any) -> dict[str, Any] | None:
    if len(points) < 2 or not np.isfinite(points).all() or not math.isfinite(radius) or radius <= 0:
        return None
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    total = float(np.sum(steps))
    chord_vector = points[-1] - points[0]
    chord = float(np.linalg.norm(chord_vector))
    if total <= 0 or chord <= 0:
        return None
    direction = chord_vector / chord
    offsets = points - points[0]
    deviation = np.linalg.norm(offsets - np.outer(offsets @ direction, direction), axis=1)
    straight = total <= chord * 1.002 and float(np.max(deviation)) <= max(1e-6, radius * 0.05)
    horizontal = bool(straight and abs(float(direction[2])) <= 0.10)
    return {
        "diameterM": _rounded(2 * radius),
        "lengthM": _rounded(chord if straight else total),
        "chordLengthM": _rounded(chord),
        "shape": "straight" if straight else "bent",
        "orientation": "horizontal" if horizontal else "nonHorizontal",
        "productType": str(product.is_a()),
        "ifcGlobalId": str(getattr(product, "GlobalId", "") or ""),
    }


def _extract(path: Path) -> dict[str, Any]:
    # Optional SQLite bindings may print an import warning even though geometry
    # parsing works. Keep the compute-only CLI's JSON stream machine-readable.
    with redirect_stdout(io.StringIO()):
        import ifcopenshell
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit

    from rebar_bim import _extruded_points, _scaled_transform, _swept_disk_points, _walk_items

    model = ifcopenshell.open(str(path))
    unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    if not math.isfinite(unit_scale) or unit_scale <= 0:
        raise ValueError("invalid IFC length unit")

    records: list[dict[str, Any]] = []
    truncated = False
    for product in model.by_type("IfcProduct"):
        representation = getattr(product, "Representation", None)
        if representation is None:
            continue
        try:
            placement = _scaled_transform(
                ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement), unit_scale
            )
        except Exception:
            continue
        for ordinal, shape in enumerate(representation.Representations or []):
            for item, transform, _ in _walk_items(shape.Items or [], placement, unit_scale, f"representation{ordinal}"):
                extracted = None
                if item.is_a("IfcExtrudedAreaSolid"):
                    extracted = _extruded_points(item, transform, unit_scale)
                elif item.is_a("IfcSweptDiskSolid"):
                    extracted = _swept_disk_points(item, transform, unit_scale)
                if extracted is None:
                    continue
                record = _shape_record(*extracted, product)
                if record is not None:
                    records.append(record)
                if len(records) >= _MAX_SHAPES:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break

    grouped: dict[tuple[float, float, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (record["diameterM"], record["lengthM"], record["shape"], record["orientation"])
        grouped[key].append(record)
    families = []
    for (diameter, length, shape, orientation), members in sorted(grouped.items()):
        family: dict[str, Any] = {
            "diameterM": diameter,
            "lengthM": length,
            "shape": shape,
            "orientation": orientation,
            "lengthSemantics": "endpointChord" if shape == "straight" else "centerlinePath",
            "count": len(members),
            "productTypes": sorted({member["productType"] for member in members}),
        }
        if shape == "bent":
            family["chordLengthsM"] = sorted({member["chordLengthM"] for member in members})
        families.append(family)

    straight = [record for record in records if record["shape"] == "straight"]
    horizontal = [record for record in straight if record["orientation"] == "horizontal"]
    internal_name = ""
    try:
        internal_name = str(model.header.file_name.name or "")
    except Exception:
        pass
    return {
        "unitScale": unit_scale,
        "schema": str(model.schema),
        "ifcFileName": internal_name,
        "diametersM": sorted({record["diameterM"] for record in records}),
        # Bent centerline length is intentionally excluded: its endpoint chord
        # could otherwise masquerade as the span of a straight diagonal web.
        "horizontalLengthsM": sorted({record["lengthM"] for record in horizontal}),
        "families": families,
        "counts": {
            "shapeCount": len(records),
            "straightCount": len(straight),
            "bentCount": len(records) - len(straight),
            "horizontalStraightCount": len(horizontal),
            "diameterFamilies": len({record["diameterM"] for record in records}),
            "horizontalLengthFamilies": len({record["lengthM"] for record in horizontal}),
        },
        "truncated": truncated,
    }


def load_dimension_priors(source_path: str | None = None, ifc_path: str | None = None) -> dict[str, Any]:
    """Return JSON-safe IFC dimension families, or a factual unavailable report."""
    resolved, association, error = _resolve_ifc(source_path, ifc_path)
    if error is not None:
        return error
    assert resolved is not None and association is not None
    key = _stat_key(resolved)
    cached = _CACHE.get(key)
    reused = cached is not None
    if cached is None:
        try:
            cached = _extract(resolved)
        except ImportError as exc:
            return _unavailable("ifcopenshell_unavailable", error=type(exc).__name__)
        except Exception as exc:
            return _unavailable("ifc_parse_failed", error=type(exc).__name__)
        _CACHE[key] = copy.deepcopy(cached)
    inventory = copy.deepcopy(cached)
    stat = resolved.stat()
    provenance = {
        "path": str(resolved),
        "name": resolved.name,
        "sha256": _sha256(resolved),
        "sizeBytes": int(stat.st_size),
        "mtimeNs": int(stat.st_mtime_ns),
        "unitScale": inventory.pop("unitScale"),
        "schema": inventory.pop("schema"),
        "ifcFileName": inventory.pop("ifcFileName"),
        **association,
    }
    return {
        "available": bool(inventory["families"]),
        **({} if inventory["families"] else {"reason": "no_circular_bar_dimensions_found"}),
        "provenance": provenance,
        **inventory,
        "cache": {"reused": reused, "key": {"path": key[0], "sizeBytes": key[1], "mtimeNs": key[2]}},
    }
