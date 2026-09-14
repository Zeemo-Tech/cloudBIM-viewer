"""Validate and load immutable upload-time point-cloud preprocessing data."""
from __future__ import annotations

import json
from pathlib import Path

import laspy
import numpy as np


PROVENANCE_USER_ID = "cloudbim"
PROVENANCE_RECORD_ID = 2201
PROVENANCE_CONTRACT = "cloudbim-pointcloud-preprocess-v1"
NORMAL_DIMENSIONS = {
    "normal_x": np.dtype("<f4"),
    "normal_y": np.dtype("<f4"),
    "normal_z": np.dtype("<f4"),
    "normal_valid": np.dtype("u1"),
    "normal_curvature": np.dtype("<f4"),
    "normal_radius": np.dtype("<f4"),
    "shared_table_mask": np.dtype("u1"),
}


def provenance_vlr(value: dict) -> laspy.VLR:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return laspy.VLR(
        user_id=PROVENANCE_USER_ID,
        record_id=PROVENANCE_RECORD_ID,
        description="CloudBIM preprocessing provenance",
        record_data=payload,
    )


def _read_provenance(header, count: int, normal_k: int) -> dict | None:
    matches = [vlr for vlr in header.vlrs
               if vlr.user_id.rstrip("\x00") == PROVENANCE_USER_ID and vlr.record_id == PROVENANCE_RECORD_ID]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("预处理 LAS 的 provenance VLR 数量非法")
    try:
        value = json.loads(bytes(matches[0].record_data).decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("预处理 LAS 的 provenance VLR 无法解析") from exc
    if not isinstance(value, dict) or value.get("contract") != PROVENANCE_CONTRACT:
        raise ValueError("预处理 LAS 的 provenance 契约不受支持")
    if value.get("artifactRole") not in ("annotated", "cleaned"):
        raise ValueError("预处理 LAS 的 artifactRole 非法")
    if not isinstance(value.get("algorithmVersion"), str) or not value["algorithmVersion"]:
        raise ValueError("预处理 LAS 的 algorithmVersion 非法")
    if value.get("normalK") != normal_k:
        raise ValueError(f"预处理 LAS 法向量使用 normalK={value.get('normalK')}，与请求的 {normal_k} 不一致")
    if value.get("pointCount") != count:
        raise ValueError("预处理 LAS 的 provenance 点数与 LAS 头不一致")
    digest = value.get("sourceSha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("预处理 LAS 的 sourceSha256 非法")
    before, after, table = value.get("pointsBefore"), value.get("pointsAfter"), value.get("tablePoints")
    if (not all(type(v) is int and v >= 0 for v in (before, after, table))
            or before - table != after):
        raise ValueError("预处理 LAS 的点数 provenance 非法")
    expected = before if value["artifactRole"] == "annotated" else after
    if count != expected:
        raise ValueError("预处理 LAS 的 artifactRole 与点数不一致")
    plane = value.get("plane")
    if type(value.get("detected")) is not bool or value["detected"] != (plane is not None):
        raise ValueError("预处理 LAS 的台面检测 provenance 非法")
    if plane is not None:
        try:
            origin = np.asarray(plane["origin"], dtype=np.float64)
            slopes = np.asarray(plane["slopes"], dtype=np.float64)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("预处理 LAS 的台面模型非法") from exc
        if origin.shape != (3,) or slopes.shape != (2,) or not np.isfinite(origin).all() or not np.isfinite(slopes).all():
            raise ValueError("预处理 LAS 的台面模型非法")
    names = set(header.point_format.dimension_names)
    for name, dtype in NORMAL_DIMENSIONS.items():
        if name not in names or header.point_format.dimension_by_name(name).dtype != dtype:
            raise ValueError(f"预处理 LAS 缺少兼容属性 {name}")
    return value


def load_preprocessed_las(
    source: str | Path,
    *,
    count: int,
    normal_k: int,
    normal_output: dict[str, np.ndarray],
    table_output: np.ndarray | None = None,
) -> dict | None:
    """Load persisted normals/table ownership, or return None for a legacy LAS."""
    with laspy.open(source) as reader:
        value = _read_provenance(reader.header, count, normal_k)
        if value is None:
            return None
        offset = 0
        cleaned_has_table = False
        invalid_table_mask = False
        for chunk in reader.chunk_iterator(262144):
            stop = offset + len(chunk)
            for axis, name in enumerate(("normal_x", "normal_y", "normal_z")):
                normal_output["normals"][offset:stop, axis] = chunk[name]
            normal_output["normal_valid"][offset:stop] = chunk["normal_valid"]
            normal_output["curvature"][offset:stop] = chunk["normal_curvature"]
            normal_output["neighbor_radius"][offset:stop] = chunk["normal_radius"]
            mask = np.asarray(chunk["shared_table_mask"], dtype=np.uint8)
            if table_output is not None:
                table_output[offset:stop] = mask
            cleaned_has_table |= bool(np.any(mask))
            invalid_table_mask |= bool(np.any(mask > 1))
            offset = stop
        if offset != count:
            raise ValueError("预处理 LAS 读取点数与头部声明不一致")
    normals = normal_output["normals"]
    valid = normal_output["normal_valid"]
    curvature = normal_output["curvature"]
    radius = normal_output["neighbor_radius"]
    if (not np.isfinite(normals).all() or not np.isfinite(curvature).all()
            or not np.isfinite(radius).all() or np.any(valid > 1)
            or np.any(curvature < 0) or np.any(curvature > 1) or np.any(radius < 0)):
        raise ValueError("预处理 LAS 的持久化法向量属性非法")
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(np.abs(lengths[valid.astype(bool)] - 1) > 2e-4) or np.any(lengths[~valid.astype(bool)] != 0):
        raise ValueError("预处理 LAS 的持久化法向量属性非法")
    if value["artifactRole"] == "cleaned" and cleaned_has_table:
        raise ValueError("cleaned LAS 不得包含台面点标记")
    if invalid_table_mask:
        raise ValueError("预处理 LAS 的 shared_table_mask 非法")
    return value
