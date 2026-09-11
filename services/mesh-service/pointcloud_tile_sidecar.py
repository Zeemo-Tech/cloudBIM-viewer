"""Strict source-PNTS to pointcloud-debug final-stage sidecars.

The source tile tree stays immutable.  A sidecar is only useful when its row
order is *exactly* the PNTS POINTS_LENGTH order, so an uncertain coordinate
lookup is an error rather than a best-effort label assignment.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np


class TileSidecarError(ValueError):
    pass


HEADER = struct.Struct("<4sHHIHHI12s")
RECORD = np.dtype([
    ("complete_class", "u1"),
    ("complete_instance", "<u4"),
    ("complete_cluster", "<u4"),
], align=False)
MAGIC = b"PCTA"
VERSION = 1


def _safe_child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise TileSidecarError("invalid tileset content URI")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise TileSidecarError("tileset content escapes source tile root")
    return candidate


def _content_uri(node: dict) -> str | None:
    content = node.get("content")
    if not isinstance(content, dict):
        return None
    uri = content.get("uri", content.get("url"))
    return uri if isinstance(uri, str) else None


def referenced_pnts(source_tiles: Path) -> list[tuple[Path, Path]]:
    """Return (file, source-root-relative path), following nested tilesets."""
    root = Path(source_tiles).resolve()
    entry = root / "tileset.json"
    if not entry.is_file():
        raise TileSidecarError("source tiles must contain tileset.json")
    found: dict[Path, Path] = {}
    visited: set[Path] = set()

    def visit(tileset: Path) -> None:
        resolved = tileset.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        try:
            document = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TileSidecarError(f"invalid tileset JSON: {resolved}") from exc
        root_node = document.get("root")
        if not isinstance(root_node, dict):
            raise TileSidecarError(f"tileset has no root: {resolved}")

        def walk(node: dict) -> None:
            transform = node.get("transform")
            if transform is not None:
                identity = np.eye(4, dtype=np.float64).reshape(-1, order="F")
                try:
                    values = np.asarray(transform, dtype=np.float64) if isinstance(transform, list) else np.empty(0)
                except (TypeError, ValueError):
                    values = np.empty(0)
                if values.shape != (16,) or not np.allclose(values, identity):
                    raise TileSidecarError("transformed PNTS cannot map to native LAS coordinates")
            uri = _content_uri(node)
            if uri:
                # A content URI is relative to the tileset that declares it.
                content = _safe_child(resolved.parent, uri)
                suffix = content.suffix.lower()
                if suffix == ".pnts":
                    found[content] = content.relative_to(root)
                elif suffix == ".json":
                    visit(content)
                else:
                    raise TileSidecarError(f"unsupported source tile content: {content.name}")
            children = node.get("children", [])
            if not isinstance(children, list) or not all(isinstance(child, dict) for child in children):
                raise TileSidecarError("invalid tileset children")
            for child in children:
                walk(child)

        walk(root_node)

    visit(entry)
    if not found:
        raise TileSidecarError("source tileset contains no PNTS content")
    return sorted(found.items(), key=lambda item: item[1].as_posix())


def pnts_positions(path: Path) -> np.ndarray:
    """Decode uncompressed source-coordinate POSITION/QUANTIZED POSITION."""
    raw = Path(path).read_bytes()
    if len(raw) < 28 or raw[:4] != b"pnts":
        raise TileSidecarError(f"not a PNTS tile: {path}")
    version, length, ftj, ftb, btj, btb = struct.unpack_from("<6I", raw, 4)
    end = 28 + ftj + ftb + btj + btb
    legacy_length = 28 + ftj + ftb
    if version != 1 or end != len(raw) or (length != len(raw) and not (length == legacy_length and end == len(raw))):
        raise TileSidecarError("invalid PNTS section lengths")
    try:
        feature = json.loads(raw[28 : 28 + ftj].decode("utf-8").rstrip(" \0"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TileSidecarError("invalid PNTS feature table JSON") from exc
    if feature.get("RTC_CENTER") is not None:
        raise TileSidecarError("RTC_CENTER PNTS cannot map to native LAS coordinates")
    extensions = feature.get("extensions", {})
    if not isinstance(extensions, dict) or "3DTILES_draco_point_compression" in extensions:
        raise TileSidecarError("compressed PNTS cannot be mapped safely")
    count = feature.get("POINTS_LENGTH")
    if not isinstance(count, int) or count < 0:
        raise TileSidecarError("PNTS missing POINTS_LENGTH")
    binary = raw[28 + ftj : 28 + ftj + ftb]
    if "POSITION" in feature:
        item = feature["POSITION"]
        offset = item.get("byteOffset") if isinstance(item, dict) else None
        if not isinstance(offset, int) or offset < 0 or offset + count * 12 > len(binary):
            raise TileSidecarError("invalid PNTS POSITION")
        return np.frombuffer(binary, dtype="<f4", count=count * 3, offset=offset).reshape(count, 3).astype(np.float64)
    item = feature.get("POSITION_QUANTIZED")
    scale, origin = feature.get("QUANTIZED_VOLUME_SCALE"), feature.get("QUANTIZED_VOLUME_OFFSET")
    offset = item.get("byteOffset") if isinstance(item, dict) else None
    if (not isinstance(offset, int) or offset < 0 or not isinstance(scale, list) or not isinstance(origin, list)
            or len(scale) != 3 or len(origin) != 3 or offset + count * 6 > len(binary)):
        raise TileSidecarError("invalid PNTS POSITION_QUANTIZED")
    quantized = np.frombuffer(binary, dtype="<u2", count=count * 3, offset=offset).reshape(count, 3)
    return quantized.astype(np.float64) / 65535.0 * np.asarray(scale, dtype=np.float64) + np.asarray(origin, dtype=np.float64)


def _coordinate_tolerance(points: np.ndarray) -> float:
    # The gocesium source-coordinate writer stores f32 XYZ.  This upper bound
    # covers its worst f32 rounding at the observed coordinate magnitude.
    return max(1e-5, float(np.max(np.abs(points))) * np.finfo(np.float32).eps * 2.0)


def write_complete_rebar_sidecars(source_tiles: Path, run_directory: Path, context) -> dict:
    """Write final labels once per referenced PNTS tile and return manifest data."""
    required = ("complete_class", "complete_instance", "complete_cluster")
    if any(getattr(context, name, None) is None for name in required):
        raise TileSidecarError("complete rebar attributes are unavailable")
    point_count = len(context.positions)
    if any(len(getattr(context, name)) != point_count for name in required):
        raise TileSidecarError("complete rebar attribute length does not match source positions")
    output = Path(run_directory) / "tile-attributes" / "complete-rebar"
    written = 0
    tiles = referenced_pnts(source_tiles)
    for tile, relative in tiles:
        points = pnts_positions(tile)
        if not len(points):
            continue
        distance, nearest = context.tree.query(points, k=2, workers=1)
        tolerance = _coordinate_tolerance(points)
        if np.any(distance[:, 0] > tolerance):
            raise TileSidecarError(f"tile points do not match source LAS within {tolerance:g}: {relative}")
        # A second source row at the same (or near-indistinguishable) position
        # makes coordinate-only mapping non-deterministic.  SOURCE_INDEX would
        # remove this limitation in a future source-tiles schema.
        if np.any(distance[:, 1] - distance[:, 0] <= max(tolerance * 0.01, 1e-10)):
            raise TileSidecarError(f"ambiguous coordinate mapping in tile: {relative}")
        ids = np.asarray(nearest[:, 0], dtype=np.intp)
        records = np.empty(len(ids), dtype=RECORD)
        for name in required:
            records[name] = getattr(context, name)[ids]
        if records.nbytes != len(records) * RECORD.itemsize:
            raise TileSidecarError("unexpected sidecar record layout")
        destination = output / (relative.as_posix() + ".bin")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as stream:
            stream.write(HEADER.pack(MAGIC, VERSION, HEADER.size, len(records), RECORD.itemsize, len(RECORD.names), 0, b"\0" * 12))
            stream.write(records.tobytes(order="C"))
        written += len(records)
    return {
        "schema": "pointcloud-tile-attributes-v1",
        "attributeUrlTemplate": "tile-attributes/complete-rebar/{tilePath}.bin",
        "format": {"magic": MAGIC.decode(), "version": VERSION, "headerBytes": HEADER.size, "byteOrder": "littleEndian",
                   "recordBytes": RECORD.itemsize,
                   "properties": [{"name": "complete_class", "type": "uint8", "offset": 0},
                                  {"name": "complete_instance", "type": "uint32", "offset": 1},
                                  {"name": "complete_cluster", "type": "uint32", "offset": 5}]},
        "tileCount": len(tiles), "pointCount": written,
        "mapping": "nearest native LAS XYZ with f32-rounding distance and second-nearest ambiguity checks",
    }
