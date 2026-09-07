"""Minimal, strict PNTS attribute rewriter used by the rebar artifact pipeline."""
from __future__ import annotations
import json
import struct
from pathlib import Path
from typing import Callable
import numpy as np

from algorithms.rebar_base import RebarPointAttributes

class PntsError(ValueError): pass

def _pad_at_offset(data: bytes, start_offset: int, fill: bytes = b"\0") -> bytes:
    """Pad a section so its *absolute* end offset is eight-byte aligned."""
    return data + fill * ((-(start_offset + len(data))) % 8)

def _binary_pad(data: bytearray, alignment: int) -> None:
    data.extend(b"\0" * ((-len(data)) % alignment))

def rewrite_pnts(path: Path, project: Callable[[np.ndarray], RebarPointAttributes]) -> int:
    raw = path.read_bytes()
    if len(raw) < 28 or raw[:4] != b"pnts": raise PntsError(f"not a PNTS tile: {path}")
    version, length, ftj, ftb, btj, btb = struct.unpack_from("<6I", raw, 4)
    if version != 1: raise PntsError("invalid PNTS header length/version")
    end = 28 + ftj + ftb + btj + btb
    legacy_length = 28 + ftj + ftb
    if end != len(raw) or (length != len(raw) and not (length == legacy_length and end == len(raw))):
        raise PntsError("invalid PNTS section lengths")
    try:
        feature = json.loads(raw[28:28+ftj].decode("utf-8").rstrip(" \0"))
        batch = json.loads(raw[28+ftj+ftb:28+ftj+ftb+btj].decode("utf-8").rstrip(" \0")) if btj else {}
    except Exception as exc: raise PntsError("invalid PNTS JSON") from exc
    extensions = feature.get("extensions", {})
    if not isinstance(extensions, dict): raise PntsError("invalid PNTS feature extensions")
    if "3DTILES_draco_point_compression" in extensions: raise PntsError("Draco PNTS is unsupported")
    if "RTC_CENTER" in feature: raise PntsError("RTC_CENTER PNTS is unsupported; source-coordinate tiles are required")
    count = feature.get("POINTS_LENGTH")
    if not isinstance(count, int) or count < 0: raise PntsError("PNTS missing POINTS_LENGTH")
    binary = raw[28+ftj:28+ftj+ftb]
    if "POSITION" in feature:
        off = feature["POSITION"].get("byteOffset") if isinstance(feature["POSITION"], dict) else None
        if not isinstance(off, int) or off < 0 or off + count*12 > len(binary): raise PntsError("invalid POSITION payload")
        points = np.frombuffer(binary, dtype="<f4", count=count*3, offset=off).reshape(count, 3).astype(np.float64)
    elif "POSITION_QUANTIZED" in feature:
        q = feature["POSITION_QUANTIZED"]; off = q.get("byteOffset") if isinstance(q, dict) else None
        scale, offset = feature.get("QUANTIZED_VOLUME_SCALE"), feature.get("QUANTIZED_VOLUME_OFFSET")
        if not isinstance(off, int) or not isinstance(scale, list) or not isinstance(offset, list) or len(scale)!=3 or len(offset)!=3 or off < 0 or off+count*6 > len(binary): raise PntsError("invalid POSITION_QUANTIZED payload")
        points = np.frombuffer(binary, dtype="<u2", count=count*3, offset=off).reshape(count,3).astype(np.float64)
        points = points / 65535.0 * np.asarray(scale) + np.asarray(offset)
    else: raise PntsError("PNTS has no supported position payload")
    attrs = project(points); attrs.validate(count)
    # Existing batch table data is copied byte-for-byte; additions are typed binary properties.
    old_btb = raw[28+ftj+ftb+btj:]
    additions = [("REBAR_CLASS", attrs.rebar_class, "UNSIGNED_BYTE", 1),
                 ("REBAR_DIRECTION", attrs.rebar_direction.astype("<u2"), "UNSIGNED_SHORT", 2),
                 ("REBAR_INSTANCE", attrs.rebar_instance.astype("<u4"), "UNSIGNED_INT", 4)]
    if attrs.scene_class is not None:
        additions.append(("SCENE_CLASS", attrs.scene_class, "UNSIGNED_BYTE", 1))
    if attrs.rebar_flags is not None:
        additions.append(("REBAR_FLAGS", attrs.rebar_flags, "UNSIGNED_BYTE", 1))
    for name, values in (("FIXTURE_KIND", attrs.fixture_kind), ("REBAR_ROLE", attrs.rebar_role)):
        if values is not None:
            additions.append((name, values, "UNSIGNED_BYTE", 1))
    for name, values in (("CLASS_CONFIDENCE", attrs.class_confidence), ("INSTANCE_CONFIDENCE", attrs.instance_confidence)):
        if values is not None:
            additions.append((name, values.astype("<f4"), "FLOAT", 4))
    new_binary = bytearray(old_btb)
    for name, values, component, alignment in additions:
        if name in batch: raise PntsError(f"PNTS already contains {name}")
        _binary_pad(new_binary, alignment)
        batch[name] = {"byteOffset": len(new_binary), "componentType": component, "type": "SCALAR"}
        new_binary.extend(values.tobytes())
    _binary_pad(new_binary, 8)
    fjson = _pad_at_offset(json.dumps(feature, separators=(",", ":")).encode(), 28, b" ")
    feature_binary = _pad_at_offset(binary, 28 + len(fjson))
    bjson = _pad_at_offset(json.dumps(batch, separators=(",", ":")).encode(), 28 + len(fjson) + len(feature_binary), b" ")
    batch_binary = _pad_at_offset(bytes(new_binary), 28 + len(fjson) + len(feature_binary) + len(bjson))
    output = b"pnts" + struct.pack("<6I", 1, 28+len(fjson)+len(feature_binary)+len(bjson)+len(batch_binary), len(fjson),len(feature_binary),len(bjson),len(batch_binary)) + fjson + feature_binary + bjson + batch_binary
    path.write_bytes(output)
    return count
