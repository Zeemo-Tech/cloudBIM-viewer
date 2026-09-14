"""Upload-time normal estimation and tabletop removal artifact publisher."""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import laspy
import numpy as np
from threadpoolctl import threadpool_limits

from algorithms.pointcloud_normals import PointCloudContext, estimate_normals, available_workers, VERSION as NORMAL_VERSION
from algorithms.projection_geometry_classifier import _table_plane, ProjectionParameters, VERSION as PROJECTION_VERSION
from algorithms.preprocessed_las import (
    NORMAL_DIMENSIONS,
    PROVENANCE_CONTRACT,
    PROVENANCE_RECORD_ID,
    PROVENANCE_USER_ID,
    provenance_vlr,
)
from artifact_permissions import publish_shared_artifact_permissions


VERSION = f"pointcloud-preprocess-v1+{NORMAL_VERSION}+{PROJECTION_VERSION}"
NORMAL_K = 32
MAX_SOURCE_POINTS = 20_000_000


def _source_stamp(path: Path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _atomic_json(path: Path, value: dict):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _load_positions(source: Path, scratch: Path) -> np.ndarray:
    with laspy.open(source) as reader:
        count = reader.header.point_count
        positions = np.lib.format.open_memmap(scratch / "positions.npy", mode="w+", dtype="<f8", shape=(count, 3))
        offset = 0
        for chunk in reader.chunk_iterator(262144):
            stop = offset + len(chunk)
            positions[offset:stop] = np.column_stack((chunk.x, chunk.y, chunk.z))
            offset = stop
        if offset != count:
            raise ValueError("LAS 记录数量与头部声明不符")
    return positions


def _table_mask(positions: np.ndarray, plane: dict | None, params: ProjectionParameters) -> np.ndarray:
    result = np.zeros(len(positions), dtype=np.uint8)
    if plane is None:
        return result
    origin = np.asarray(plane["origin"], dtype=np.float64)
    slopes = np.asarray(plane["slopes"], dtype=np.float64)
    for start in range(0, len(positions), 262144):
        stop = min(start + 262144, len(positions))
        points = positions[start:stop]
        height = points[:, 2] - origin[2] - (points[:, :2] - origin[:2]) @ slopes
        result[start:stop] = height <= params.table_clearance
    return result


def _artifact_provenance(manifest: dict, role: str, point_count: int) -> dict:
    return {
        "contract": PROVENANCE_CONTRACT,
        "artifactRole": role,
        "algorithmVersion": manifest["algorithmVersion"],
        "sourceSha256": manifest["sourceSha256"],
        "normalK": manifest["normalK"],
        "pointsBefore": manifest["pointsBefore"],
        "pointsAfter": manifest["pointsAfter"],
        "tablePoints": manifest["tablePoints"],
        "pointCount": point_count,
        "detected": manifest["detected"],
        "plane": manifest["plane"],
    }


def _expanded_header(reader, provenance: dict):
    header = deepcopy(reader.header)
    header.vlrs[:] = [vlr for vlr in header.vlrs
                      if not (vlr.user_id.rstrip("\x00") == PROVENANCE_USER_ID
                              and vlr.record_id == PROVENANCE_RECORD_ID)]
    names = set(header.point_format.dimension_names)
    additions = []
    for name, dtype in NORMAL_DIMENSIONS.items():
        if name in names:
            if header.point_format.dimension_by_name(name).dtype != dtype:
                raise ValueError(f"源 LAS 已有不兼容属性 {name}")
        else:
            additions.append(laspy.ExtraBytesParams(name=name, type=dtype))
    header.add_extra_dims(additions)
    header.vlrs.append(provenance_vlr(provenance))
    return header, names


def _write_artifacts(source: Path, destination: Path, context: PointCloudContext,
                     table_mask: np.ndarray, manifest: dict):
    with laspy.open(source) as reader:
        annotated_header, original_names = _expanded_header(
            reader, _artifact_provenance(manifest, "annotated", len(table_mask)))
        cleaned_header, _ = _expanded_header(
            reader, _artifact_provenance(manifest, "cleaned", manifest["pointsAfter"]))
        with ExitStack() as stack:
            annotated = stack.enter_context(laspy.open(destination / "annotated.las", mode="w", header=annotated_header, do_compress=False))
            cleaned = stack.enter_context(laspy.open(destination / "cleaned.las", mode="w", header=cleaned_header, do_compress=False))
            offset = 0
            for chunk in reader.chunk_iterator(262144):
                stop = offset + len(chunk)
                points = laspy.ScaleAwarePointRecord.zeros(len(chunk), header=annotated_header)
                for name in original_names:
                    points[name] = chunk[name]
                for axis, name in enumerate(("normal_x", "normal_y", "normal_z")):
                    points[name] = context.normals[offset:stop, axis]
                points["normal_valid"] = context.normal_valid[offset:stop]
                points["normal_curvature"] = context.curvature[offset:stop]
                points["normal_radius"] = context.neighbor_radius[offset:stop]
                points["shared_table_mask"] = table_mask[offset:stop]
                annotated.write_points(points)
                keep = table_mask[offset:stop] == 0
                if np.any(keep):
                    cleaned.write_points(points[keep])
                offset = stop
            if offset != len(table_mask):
                raise ValueError("导出期间源点云发生变化")
            if reader.evlrs:
                annotated.write_evlrs(reader.evlrs)
                cleaned.write_evlrs(reader.evlrs)


def build_preprocess(source: Path, destination: Path) -> dict:
    """Publish an annotated source plus the source-record subset above the table."""
    source = Path(source)
    destination = Path(destination)
    stamp = _source_stamp(source)
    with source.open("rb") as stream:
        if stream.read(4) != b"LASF":
            raise ValueError("点云上传前处理仅支持 LAS/LAZ")
    with laspy.open(source) as reader:
        count = reader.header.point_count
        if not 3 <= count <= MAX_SOURCE_POINTS:
            raise ValueError("点云上传前处理支持 3 至 2000 万个 LAS/LAZ 源点")
    if destination.exists():
        raise FileExistsError("outputPath 必须是尚不存在的新目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="pointcloud-preprocess-") as scratch_value:
            scratch = Path(scratch_value)
            positions = _load_positions(source, scratch)
            context = PointCloudContext.build(positions)
            outputs = {
                "normals": np.lib.format.open_memmap(scratch / "normals.npy", mode="w+", dtype="<f4", shape=(count, 3)),
                "normal_valid": np.lib.format.open_memmap(scratch / "normal_valid.npy", mode="w+", dtype="u1", shape=(count,)),
                "curvature": np.lib.format.open_memmap(scratch / "curvature.npy", mode="w+", dtype="<f4", shape=(count,)),
                "neighbor_radius": np.lib.format.open_memmap(scratch / "neighbor_radius.npy", mode="w+", dtype="<f4", shape=(count,)),
            }
            workers = max(1, min(available_workers(), int(os.environ.get("REBAR_SPATIAL_WORKERS", "4"))))
            with threadpool_limits(limits=1):
                estimate_normals(context, k=NORMAL_K, workers=workers, output=outputs)
                params = ProjectionParameters()
                plane = _table_plane(positions, context.normals, context.normal_valid, positions[:, 2].min(), params)
                mask = _table_mask(positions, plane, params)
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            table_points = int(np.count_nonzero(mask))
            if count - table_points < 3:
                raise ValueError("台面移除后不足 3 个点，无法发布可处理点云")
            manifest = {
                "algorithmVersion": VERSION,
                "sourceSha256": digest,
                "normalK": NORMAL_K,
                "pointsBefore": count,
                "pointsAfter": count - table_points,
                "tablePoints": table_points,
                "detected": plane is not None,
                "plane": plane,
                "annotatedFile": "annotated.las",
                "cleanedFile": "cleaned.las",
            }
            _write_artifacts(source, staging, context, mask, manifest)
        if stamp != _source_stamp(source):
            raise ValueError("计算期间源点云发生变化，请重新处理")
        manifest["elapsedSeconds"] = time.perf_counter() - started
        _atomic_json(staging / "manifest.json", manifest)
        publish_shared_artifact_permissions(staging)
        staging.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
