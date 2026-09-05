"""File ingestion and offline CLI for the geometric rebar PoC.

The geometry implementation intentionally lives in ``algorithms``.  This
module owns the operational contract around it: supported point-cloud files,
finite-coordinate filtering, bounded deterministic sampling, source-index
traceability, and atomic JSON publication.

Example (from the mesh-service directory)::

    ../../.cloudbim/mesh-venv/bin/python rebar_poc.py /data/slab.las \
      --max-input-points 250000 \
      --params-json '{"min_rebar_height": 0.01}' \
      --output-json /tmp/rebar-result.json

All input coordinates and algorithm parameters are expressed in metres.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import shutil
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from algorithms.rebar_segmentation import (
    RebarSegmentationError,
    RebarSegmentationParams,
    segment_rebar_points,
)


SUPPORTED_POINT_CLOUD_EXTENSIONS = frozenset({".las", ".laz", ".ply", ".pcd"})
DEFAULT_MAX_INPUT_POINTS = 200_000
LAS_CHUNK_SIZE = 250_000


class PointCloudInputError(ValueError):
    """The point-cloud file or its sampling options are not usable."""


class UnsupportedPointCloudFormatError(PointCloudInputError):
    """The requested file extension is outside the PoC contract."""


class StoragePathViolationError(PointCloudInputError):
    """A service request attempted to escape its shared-storage root."""


class InvalidRebarInputOptionsError(PointCloudInputError):
    """Pipeline-level sampling options are invalid or unsupported."""


class InvalidBimPriorError(PointCloudInputError):
    """Requested design geometry or saved alignment is unusable."""


@dataclass(frozen=True)
class LoadedPointCloud:
    """Bounded detection points plus their source-reader record indices."""

    points: np.ndarray
    source_indices: np.ndarray
    report: dict[str, Any]


def resolve_point_cloud_path(
    input_path: str | os.PathLike[str],
    *,
    storage_root: str | os.PathLike[str] | None = None,
    point_cloud_format: str | None = None,
) -> Path:
    """Resolve an existing point-cloud file and optionally confine it to a root.

    ``storage_root`` is mandatory at the HTTP layer and deliberately optional
    for the offline CLI.  Both the root and file are resolved through symlinks,
    so a symlink inside the volume cannot escape the shared-storage boundary.
    """

    candidate = Path(input_path)
    if storage_root is not None and not candidate.is_absolute():
        raise StoragePathViolationError(
            "point_cloud_path must be an absolute path inside shared storage"
        )

    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise PointCloudInputError(f"point-cloud file does not exist: {candidate}") from exc

    if not resolved.is_file():
        raise PointCloudInputError(f"point-cloud path is not a regular file: {candidate}")

    if storage_root is not None:
        root = Path(storage_root).expanduser().resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise StoragePathViolationError(
                f"point-cloud file must be inside shared storage root: {root}"
            ) from exc

    suffix = (f".{point_cloud_format.lower().lstrip('.')}" if point_cloud_format else candidate.suffix.lower())
    if suffix not in SUPPORTED_POINT_CLOUD_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_POINT_CLOUD_EXTENSIONS))
        raise UnsupportedPointCloudFormatError(
            f"unsupported point-cloud format {suffix or '<none>'}; supported: {supported}"
        )
    return resolved


def _read_source_points(path: Path, suffix: str | None = None) -> np.ndarray:
    """Decode PLY/PCD through Open3D (which currently materializes the file)."""

    suffix = suffix or path.suffix.lower()
    try:
        import open3d as o3d

        cloud = o3d.io.read_point_cloud(
            str(path),
            remove_nan_points=False,
            remove_infinite_points=False,
        )
        points = np.asarray(cloud.points, dtype=np.float64).copy()
    except Exception as exc:
        raise PointCloudInputError(
            f"failed to read {suffix[1:].upper()} point cloud: {exc}"
        ) from exc

    if points.ndim != 2 or points.shape[1:] != (3,):
        raise PointCloudInputError(
            f"point-cloud reader returned invalid XYZ shape: {points.shape}"
        )
    if points.shape[0] == 0:
        raise PointCloudInputError("point-cloud file contains no readable XYZ points")
    return np.ascontiguousarray(points, dtype=np.float64)


def _voxel_keys(
    points: np.ndarray,
    voxel_size: float,
    *,
    anchor: np.ndarray | None = None,
) -> np.ndarray:
    """Create stable int64 voxel coordinates around an explicit anchor."""

    if anchor is None:
        anchor = points.min(axis=0)
    scaled = (points - np.asarray(anchor, dtype=np.float64)) / voxel_size
    if not np.isfinite(scaled).all() or (
        scaled.size and np.max(np.abs(scaled)) > np.iinfo(np.int64).max
    ):
        raise PointCloudInputError("voxel_size is too small for the point-cloud extent")
    return np.floor(scaled).astype(np.int64)


def _first_point_per_voxel(
    points: np.ndarray,
    voxel_size: float,
    *,
    anchor: np.ndarray | None = None,
) -> np.ndarray:
    """Return stable indices for the first source point in each occupied voxel."""

    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise PointCloudInputError("voxel_size must be a finite positive number")

    # Anchoring at the finite cloud minimum avoids precision loss for projected
    # coordinate systems and makes the selected representatives translation
    # invariant.  The final sort restores source order after np.unique sorts
    # voxel keys lexicographically.
    voxel_keys = _voxel_keys(points, voxel_size, anchor=anchor)
    _, first_indices = np.unique(voxel_keys, axis=0, return_index=True)
    return np.sort(first_indices.astype(np.int64, copy=False))


def _las_chunk_points(chunk: Any) -> np.ndarray:
    return np.ascontiguousarray(
        np.column_stack((chunk.x, chunk.y, chunk.z)),
        dtype=np.float64,
    )


def _load_las_streaming(
    path: Path,
    *,
    max_input_points: int,
    voxel_size: float | None,
) -> LoadedPointCloud:
    """Bound LAS/LAZ memory with chunked, source-index-stable ingestion."""

    try:
        import laspy

        with laspy.open(path) as reader:
            raw_count = int(reader.header.point_count)
            if raw_count == 0:
                raise PointCloudInputError(
                    "point-cloud file contains no readable XYZ points"
                )

            anchor = np.asarray(reader.header.mins, dtype=np.float64)
            stride = (
                max(1, math.ceil(raw_count / max_input_points))
                if voxel_size is None and raw_count > max_input_points
                else None
            )
            method = "stable_stride" if stride is not None else "none"
            if voxel_size is not None:
                method = "voxel_first_point"

            point_parts: list[np.ndarray] = []
            index_parts: list[np.ndarray] = []
            finite_count = 0
            record_offset = 0
            selected_count = 0
            seen_voxels: set[tuple[int, int, int]] = set()
            voxel_selection_truncated = False

            for chunk in reader.chunk_iterator(LAS_CHUNK_SIZE):
                chunk_points = _las_chunk_points(chunk)
                chunk_count = int(chunk_points.shape[0])
                finite_mask = np.isfinite(chunk_points).all(axis=1)
                finite_local_indices = np.flatnonzero(finite_mask).astype(
                    np.int64, copy=False
                )
                finite_points = np.ascontiguousarray(chunk_points[finite_mask])
                finite_count += int(finite_points.shape[0])

                if voxel_size is not None:
                    # Once the source-order cap has been filled, remaining
                    # chunks are still scanned for the finite-point report but
                    # do not grow the voxel set or resident sample memory.
                    if selected_count < max_input_points and finite_points.size:
                        first_local = _first_point_per_voxel(
                            finite_points,
                            float(voxel_size),
                            anchor=anchor,
                        )
                        keys = _voxel_keys(
                            finite_points[first_local],
                            float(voxel_size),
                            anchor=anchor,
                        )
                        accepted: list[int] = []
                        for local_index, key in zip(first_local.tolist(), keys.tolist()):
                            voxel_key = (int(key[0]), int(key[1]), int(key[2]))
                            if voxel_key in seen_voxels:
                                continue
                            if selected_count >= max_input_points:
                                voxel_selection_truncated = True
                                break
                            seen_voxels.add(voxel_key)
                            accepted.append(local_index)
                            selected_count += 1
                        if accepted:
                            accepted_array = np.asarray(accepted, dtype=np.int64)
                            point_parts.append(finite_points[accepted_array])
                            index_parts.append(
                                record_offset
                                + finite_local_indices[accepted_array]
                            )
                    elif finite_points.size:
                        voxel_selection_truncated = True
                elif stride is not None:
                    # The modulo is against the original LAS record index, not
                    # chunk-local or finite-only order, so chunk size cannot
                    # change which records are selected.
                    source_indices = record_offset + finite_local_indices
                    selected_mask = source_indices % stride == 0
                    if np.any(selected_mask):
                        remaining = max_input_points - selected_count
                        selected_points = finite_points[selected_mask][:remaining]
                        selected_indices = source_indices[selected_mask][:remaining]
                        point_parts.append(selected_points)
                        index_parts.append(selected_indices)
                        selected_count += int(selected_points.shape[0])
                elif finite_points.size:
                    point_parts.append(finite_points)
                    index_parts.append(record_offset + finite_local_indices)
                    selected_count += int(finite_points.shape[0])

                record_offset += chunk_count
    except PointCloudInputError:
        raise
    except Exception as exc:
        raise PointCloudInputError(
            f"failed to read {path.suffix[1:].upper()} point cloud: {exc}"
        ) from exc

    if finite_count == 0:
        raise PointCloudInputError("point-cloud file contains no finite XYZ points")
    detection_points = (
        np.ascontiguousarray(np.concatenate(point_parts, axis=0))
        if point_parts
        else np.empty((0, 3), dtype=np.float64)
    )
    source_indices = (
        np.ascontiguousarray(np.concatenate(index_parts).astype(np.int64, copy=False))
        if index_parts
        else np.empty(0, dtype=np.int64)
    )
    if detection_points.shape[0] > max_input_points:
        raise AssertionError("bounded LAS loader exceeded max_input_points")

    report = _input_report(
        path=path,
        raw_count=raw_count,
        finite_count=finite_count,
        detection_count=int(detection_points.shape[0]),
        max_input_points=max_input_points,
        method=(
            "voxel_first_point_source_order_capped"
            if voxel_selection_truncated
            else method
        ),
        voxel_size=float(voxel_size) if voxel_size is not None else None,
        points_after_voxel=int(detection_points.shape[0]),
        stride=stride,
        source_indices=source_indices,
        voxel_selection_truncated=voxel_selection_truncated,
        reader="laspy.chunk_iterator",
        materialization_limitation=None,
    )
    return LoadedPointCloud(detection_points, source_indices, report)


def _automatic_voxel_indices(
    points: np.ndarray,
    max_input_points: int,
) -> tuple[np.ndarray, float]:
    """Choose a deterministic voxel size that satisfies the detection cap."""

    spans = np.ptp(points, axis=0)
    diagonal = float(np.linalg.norm(spans))
    if not math.isfinite(diagonal) or diagonal <= 0:
        # The geometry layer will provide the more specific degeneracy error;
        # stable stride is the only well-defined bounding operation here.
        stride = max(1, math.ceil(points.shape[0] / max_input_points))
        return np.arange(0, points.shape[0], stride, dtype=np.int64)[:max_input_points], 0.0

    voxel_size = max(diagonal / np.cbrt(max_input_points), np.finfo(np.float64).eps)
    selected = _first_point_per_voxel(points, voxel_size)
    for _ in range(32):
        if selected.size <= max_input_points:
            return selected, float(voxel_size)
        voxel_size *= 1.5
        selected = _first_point_per_voxel(points, voxel_size)

    # Defensive numerical fallback.  Normal finite clouds reach the cap well
    # before this branch; the stable stride still enforces the public limit.
    stride = max(1, math.ceil(selected.size / max_input_points))
    return selected[::stride][:max_input_points], float(voxel_size)


def _input_report(
    *,
    path: Path,
    raw_count: int,
    finite_count: int,
    detection_count: int,
    max_input_points: int,
    method: str,
    voxel_size: float | None,
    points_after_voxel: int,
    stride: int | None,
    source_indices: np.ndarray,
    voxel_selection_truncated: bool,
    reader: str,
    materialization_limitation: str | None,
) -> dict[str, Any]:
    if method == "stable_stride":
        representative = "every stride-th finite point in source-reader order"
    elif method.endswith("_then_stable_stride"):
        representative = (
            "every stride-th first-source-point voxel representative"
        )
    elif "voxel_first_point" in method:
        representative = "first finite point in source-reader order per voxel"
    else:
        representative = "all finite points in source-reader order"
    return {
        "source_path": str(path),
        "format": path.suffix.lower()[1:],
        "reader": reader,
        "raw_point_count": raw_count,
        "finite_point_count": finite_count,
        "dropped_non_finite_point_count": raw_count - finite_count,
        "detection_point_count": detection_count,
        "max_input_points": max_input_points,
        "sampling": {
            "method": method,
            "deterministic": True,
            "voxel_size": voxel_size,
            "points_after_voxel": points_after_voxel,
            "points_after_voxel_is_exact": not voxel_selection_truncated,
            "voxel_selection_truncated": voxel_selection_truncated,
            "stride": stride,
            "representative": representative,
        },
        "point_index_contract": {
            "segmentation_index_space": "detection_point_order",
            "source_index_space": "source-reader point order",
            "detection_to_source_index": source_indices.tolist(),
            "ply_pcd_limitation": (
                "source indices follow Open3D-decoded order; malformed or invalid "
                "records omitted by Open3D cannot be traced"
            ),
        },
        "materialization_limitation": materialization_limitation,
    }


def load_point_cloud(
    input_path: str | os.PathLike[str],
    *,
    max_input_points: int,
    voxel_size: float | None = None,
    storage_root: str | os.PathLike[str] | None = None,
    point_cloud_format: str | None = None,
) -> LoadedPointCloud:
    """Load, finite-filter, and deterministically bound a point cloud.

    Voxel downsampling retains an actual source point (the first finite point
    in reader order) instead of producing a centroid.  Consequently every
    detection point has an exact ``source_indices`` entry.  For PLY/PCD this
    refers to Open3D's decoded point order; invalid records that Open3D itself
    does not expose cannot be indexed.
    """

    if isinstance(max_input_points, bool) or not isinstance(max_input_points, int):
        raise PointCloudInputError("max_input_points must be an integer")
    if max_input_points <= 0:
        raise PointCloudInputError("max_input_points must be positive")
    if voxel_size is not None and (
        isinstance(voxel_size, bool)
        or not isinstance(voxel_size, (int, float))
        or not math.isfinite(float(voxel_size))
        or float(voxel_size) <= 0
    ):
        raise PointCloudInputError("voxel_size must be a finite positive number")

    requested_suffix = f".{point_cloud_format.lower().lstrip('.')}" if point_cloud_format else Path(input_path).suffix.lower()
    path = resolve_point_cloud_path(input_path, storage_root=storage_root, point_cloud_format=point_cloud_format)
    if requested_suffix in {".las", ".laz"}:
        return _load_las_streaming(
            path,
            max_input_points=max_input_points,
            voxel_size=float(voxel_size) if voxel_size is not None else None,
        )

    source_points = _read_source_points(path, requested_suffix)
    raw_count = int(source_points.shape[0])
    finite_mask = np.isfinite(source_points).all(axis=1)
    finite_source_indices = np.flatnonzero(finite_mask).astype(np.int64, copy=False)
    finite_points = np.ascontiguousarray(source_points[finite_mask])
    finite_count = int(finite_points.shape[0])
    if finite_count == 0:
        raise PointCloudInputError("point-cloud file contains no finite XYZ points")

    method = "none"
    effective_voxel_size: float | None = None
    stride: int | None = None
    points_after_voxel = finite_count

    if voxel_size is not None:
        effective_voxel_size = float(voxel_size)
        selected = _first_point_per_voxel(finite_points, effective_voxel_size)
        method = "voxel_first_point"
    elif finite_count > max_input_points:
        selected, effective_voxel_size = _automatic_voxel_indices(
            finite_points, max_input_points
        )
        method = "automatic_voxel_first_point"
        if effective_voxel_size == 0.0:
            method = "stable_stride"
            stride = max(1, math.ceil(finite_count / max_input_points))
    else:
        selected = np.arange(finite_count, dtype=np.int64)

    points_after_voxel = int(selected.size)
    if selected.size > max_input_points:
        stride = max(1, math.ceil(selected.size / max_input_points))
        selected = selected[::stride][:max_input_points]
        method = f"{method}_then_stable_stride"

    selected = selected.astype(np.int64, copy=False)
    detection_points = np.ascontiguousarray(finite_points[selected])
    source_indices = np.ascontiguousarray(finite_source_indices[selected])
    if detection_points.shape[0] > max_input_points:
        raise AssertionError("bounded point-cloud loader exceeded max_input_points")

    report = _input_report(
        path=path,
        raw_count=raw_count,
        finite_count=finite_count,
        detection_count=int(detection_points.shape[0]),
        max_input_points=max_input_points,
        method=method,
        voxel_size=effective_voxel_size,
        points_after_voxel=points_after_voxel,
        stride=stride,
        source_indices=source_indices,
        voxel_selection_truncated=False,
        reader="Open3D read_point_cloud",
        materialization_limitation=(
            "PLY/PCD are currently decoded in full by Open3D before finite filtering "
            "and bounded sampling; use LAS/LAZ for streaming multi-million-point input"
        ),
    )
    return LoadedPointCloud(
        points=detection_points,
        source_indices=source_indices,
        report=report,
    )


def run_segmentation_file(
    input_path: str | os.PathLike[str],
    *,
    params: RebarSegmentationParams | Mapping[str, Any],
    max_input_points: int,
    voxel_size: float | None = None,
    storage_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Run the shared file-to-result path used by both HTTP and CLI."""

    config = RebarSegmentationParams.from_value(params)
    effective_cap = min(max_input_points, config.max_point_count)
    loaded = load_point_cloud(
        input_path,
        max_input_points=effective_cap,
        voxel_size=voxel_size,
        storage_root=storage_root,
    )
    result = segment_rebar_points(loaded.points, config)
    result["input"] = {
        **loaded.report,
        "requested_max_input_points": max_input_points,
        "algorithm_max_point_count": config.max_point_count,
    }
    return result


def _confined_output_directory(output_directory: str | os.PathLike[str], storage_root: str | os.PathLike[str]) -> Path:
    root = Path(storage_root).expanduser().resolve(strict=False)
    candidate = Path(output_directory).expanduser()
    if not candidate.is_absolute():
        raise StoragePathViolationError("output_directory must be an absolute path inside shared storage")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StoragePathViolationError(f"output_directory must be inside shared storage root: {root}") from exc
    return resolved


def _publish_shared_artifact_permissions(root: Path) -> None:
    """Make a container-owned tree removable by the host backend group."""
    raw_gid = os.getenv(
        "ARTIFACT_OUTPUT_GID",
        os.getenv("C2M_OUTPUT_GID", str(os.getgid())),
    ).strip()
    try:
        output_gid = int(raw_gid)
    except ValueError as exc:
        raise RuntimeError("ARTIFACT_OUTPUT_GID must be a non-negative integer") from exc
    if output_gid < 0:
        raise RuntimeError("ARTIFACT_OUTPUT_GID must be a non-negative integer")

    for artifact in (root, *root.rglob("*")):
        os.chown(artifact, -1, output_gid, follow_symlinks=False)
        artifact.chmod(0o2770 if artifact.is_dir() else 0o660)


def _reject_tileset_transforms(source_dir: Path) -> None:
    """The current projector uses source coordinates, not tile transforms."""
    for tileset in source_dir.rglob("tileset.json"):
        try:
            parsed = json.loads(tileset.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PointCloudInputError(f"invalid tileset JSON: {tileset}") from exc
        pending = [parsed]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if "transform" in node:
                    raise PointCloudInputError(
                        f"tileset transform is unsupported for source-coordinate rebar projection: {tileset}"
                    )
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)


def normalize_rebar_input_options(
    value: Mapping[str, Any] | None,
) -> dict[str, int | float | None]:
    options = dict(value or {})
    aliases = {
        "max_input_points": "maxInputPoints",
        "voxel_size": "voxelSize",
    }
    unknown = sorted(set(options) - {"maxInputPoints", "voxelSize", *aliases})
    if unknown:
        raise InvalidRebarInputOptionsError(
            f"unknown rebar input options: {', '.join(unknown)}"
        )
    for legacy, canonical in aliases.items():
        if legacy not in options:
            continue
        if canonical in options:
            raise InvalidRebarInputOptionsError(
                f"input option {canonical} was provided more than once"
            )
        options[canonical] = options[legacy]

    max_points = options.get("maxInputPoints", DEFAULT_MAX_INPUT_POINTS)
    if isinstance(max_points, bool) or not isinstance(max_points, int):
        raise InvalidRebarInputOptionsError("maxInputPoints must be an integer")
    if not 3 <= max_points <= DEFAULT_MAX_INPUT_POINTS:
        raise InvalidRebarInputOptionsError(
            f"maxInputPoints must be between 3 and {DEFAULT_MAX_INPUT_POINTS}"
        )

    voxel_size = options.get("voxelSize")
    if voxel_size is not None:
        if (
            isinstance(voxel_size, bool)
            or not isinstance(voxel_size, (int, float))
            or not math.isfinite(float(voxel_size))
            or not 1e-6 <= float(voxel_size) <= 5.0
        ):
            raise InvalidRebarInputOptionsError(
                "voxelSize must be a finite number between 0.000001 and 5 metres"
            )
        voxel_size = float(voxel_size)
    return {"maxInputPoints": max_points, "voxelSize": voxel_size}


def _check_decoded_budget(path, file_format, limit=2_000_000):
    """Parse declarations, never comments, before whole-file Open3D decode."""
    count = None
    with Path(path).open('rb') as stream:
        consumed = 0
        while consumed < 1024 * 1024:
            line = stream.readline(65537)
            consumed += len(line)
            if not line or len(line) > 65536:
                break
            fields = line.decode('ascii', errors='replace').strip().split()
            if not fields:
                continue
            if (file_format == 'ply' and fields[0] == 'comment') or fields[0].startswith('#'):
                continue
            declaration = fields[:2] == ['element', 'vertex'] if file_format == 'ply' else fields[0].upper() == 'POINTS'
            if declaration:
                position = 2 if file_format == 'ply' else 1
                if count is not None or len(fields) != position + 1:
                    raise PointCloudInputError('duplicate or malformed point-count declaration')
                try: count = int(fields[position])
                except ValueError as exc: raise PointCloudInputError('invalid point count') from exc
                if not 3 <= count <= limit:
                    raise PointCloudInputError('V5 PLY/PCD decoder budget exceeded; use LAS/LAZ')
            terminal = fields == ['end_header'] if file_format == 'ply' else fields[0].upper() == 'DATA'
            if terminal and count is not None:
                return
    raise PointCloudInputError('point-count header unavailable within V5 header budget')


def compute_rebar_artifact(**kwargs):
    """Pin source identity across bootstrap and full-source streaming passes."""
    path = resolve_point_cloud_path(kwargs['point_cloud_path'], storage_root=kwargs['storage_root'], point_cloud_format=kwargs.get('point_cloud_format'))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        def identity(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
        original = identity(os.fstat(descriptor))
        def verify_source():
            if identity(os.fstat(descriptor)) != original or identity(path.stat()) != original or path.is_symlink():
                raise PointCloudInputError('source changed during analysis; result was not published')
        verify_source()
        kwargs['point_cloud_path'] = str(path)
        kwargs['point_cloud_format'] = (kwargs.get('point_cloud_format') or path.suffix).lower().lstrip('.')
        return _compute_rebar_artifact(**kwargs, _stable_reader=f'/proc/self/fd/{descriptor}', _verify_source=verify_source)
    finally:
        os.close(descriptor)


def _compute_rebar_artifact(*, point_cloud_path: str, point_cloud_format: str | None,
                           source_tileset_path: str, output_directory: str,
                           artifact_version: str, algorithm: str,
                           input_options: Mapping[str, Any] | None,
                           parameters: Mapping[str, Any] | None,
                           storage_root: str,
                           bim_prior: Mapping[str, Any] | None = None,
                           _stable_reader=None, _verify_source=lambda: None) -> dict[str, Any]:
    """Create a complete derived tileset in staging, then atomically publish it."""
    from algorithms import REBAR_ALGORITHM_REGISTRY
    from rebar_tiles import rewrite_pnts
    opts = normalize_rebar_input_options(input_options)
    max_points = int(opts["maxInputPoints"])
    voxel_size = opts["voxelSize"]
    algo = REBAR_ALGORITHM_REGISTRY.get(algorithm)
    effective = dict(algo.normalize_parameters(parameters))
    if algorithm == "geometric-v5" and (point_cloud_format or Path(point_cloud_path).suffix.lstrip('.')).lower() in ('ply', 'pcd'):
        # The existing Open3D decoder materializes these formats. Bound its
        # advertised point count before decoding; LAS/LAZ remains streaming.
        checked_path = resolve_point_cloud_path(point_cloud_path, storage_root=storage_root, point_cloud_format=point_cloud_format)
        _check_decoded_budget(checked_path, point_cloud_format.lower())
    loaded = load_point_cloud(point_cloud_path, max_input_points=max_points, voxel_size=voxel_size,
                              storage_root=storage_root, point_cloud_format=point_cloud_format)
    _verify_source()
    from algorithms.rebar_base import RebarInputContext
    from rebar_stream import iter_source_chunks, write_raw_labels
    prior = None
    if bim_prior is not None:
        if not algo.descriptor.get("capabilities", {}).get("bimPrior", False):
            raise InvalidRebarInputOptionsError("selected algorithm does not support BIM priors")
        from rebar_bim import load_bim_prior
        paths = {}
        boundary = Path(storage_root).resolve(strict=True)
        for key in ("ifc_path", "model_path", "metadata_path"):
            value = bim_prior.get(key)
            if not value:
                paths[key] = None
                continue
            candidate = Path(str(value))
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise InvalidBimPriorError("BIM input file is unavailable") from exc
            if candidate.is_symlink() or not resolved.is_relative_to(boundary) or not resolved.is_file():
                raise StoragePathViolationError("BIM inputs must be regular files inside shared storage")
            paths[key] = str(resolved)
        try:
            prior = load_bim_prior(**paths, scan_to_bim=list(bim_prior["scan_to_bim"]))
        except (ValueError, OSError, KeyError) as exc:
            raise InvalidBimPriorError("BIM geometry or alignment is unusable") from exc
        prior["fingerprint"] = bim_prior.get("fingerprint", "")
    context = RebarInputContext(loaded.points,
        lambda: iter_source_chunks(_stable_reader or point_cloud_path, point_cloud_format), prior)
    output = _confined_output_directory(output_directory, storage_root)
    if algorithm == 'geometric-v5' and output.exists():
        raise PointCloudInputError('V5 artifact versions are immutable; use a new output directory')
    source_candidate = Path(source_tileset_path).expanduser()
    if source_candidate.is_symlink():
        raise StoragePathViolationError("source tileset path must not be a symlink")
    source = source_candidate.resolve(strict=True)
    root = Path(storage_root).expanduser().resolve(strict=False)
    try: source.relative_to(root)
    except ValueError as exc: raise StoragePathViolationError("source_tileset_path must be inside shared storage") from exc
    source_dir = source if source.is_dir() else source.parent
    if not (source_dir / "tileset.json").is_file(): raise PointCloudInputError("source tileset must contain tileset.json")
    for walk_root, directories, filenames in os.walk(source_dir, followlinks=False):
        for entry in [*directories, *filenames]:
            if (Path(walk_root) / entry).is_symlink():
                raise StoragePathViolationError("source tileset tree must not contain symlinks")
    _reject_tileset_transforms(source_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    # The mesh service commonly runs as container root while the Go backend
    # validates and serves the bind-mounted artifact as the host user.
    stage.chmod(0o2770)
    analysis = None
    try:
        analyze_source = getattr(algo, "analyze_source", None)
        analysis = analyze_source(context, effective) if analyze_source else algo.analyze(loaded.points, effective)
        is_v5 = algo.descriptor.get("analysisSchema") == "rebar-analysis-v2"
        raw_summary = algo.export_sidecars(stage, analysis) if is_v5 else None
        shutil.copytree(source_dir, stage / "tiles", dirs_exist_ok=True)
        tile_root = stage / "tiles"
        total = 0
        rebar_total = 0
        intersection_total = 0
        scene_counts = np.zeros(5, dtype=np.int64)
        direction_point_counts = {"directionA": 0, "directionB": 0}
        direction_ids: set[int] = set()
        instance_ids: set[int] = set()
        for pnts in tile_root.rglob("*.pnts"):
            def project_and_count(points: np.ndarray):
                nonlocal rebar_total, intersection_total
                attrs = algo.project_points(points, analysis)
                attrs.validate(len(points))
                rebar_total += int(np.count_nonzero((attrs.rebar_class == 1) | (attrs.rebar_class == 2)))
                intersection_total += int(np.count_nonzero(
                    (attrs.rebar_flags & 1) if attrs.rebar_flags is not None else (attrs.rebar_class == 2)))
                direction_point_counts["directionA"] += int(np.count_nonzero(attrs.rebar_direction == 1))
                direction_point_counts["directionB"] += int(np.count_nonzero(attrs.rebar_direction == 2))
                if attrs.scene_class is not None:
                    if np.any(attrs.scene_class > 4): raise ValueError("unrecognized scene class")
                    scene_counts[:] += np.bincount(attrs.scene_class, minlength=5)
                direction_ids.update(int(value) for value in np.unique(attrs.rebar_direction)
                                     if value not in (0, np.uint16(65535)))
                instance_ids.update(int(value) for value in np.unique(attrs.rebar_instance)
                                    if value not in (0, np.uint32(0xffffffff)))
                return attrs
            total += rewrite_pnts(pnts, project_and_count)
        diagnostics = analysis.data.get("diagnostics", {})
        summary = {"totalPointCount": total, "rebarPointCount": rebar_total,
                   "directionCount": len(direction_ids), "instanceCount": len(instance_ids),
                   "diagnostics": diagnostics}
        if any(scene_counts):
            summary.update({"sceneClassCounts": {"clutter": int(scene_counts[0]), "table": int(scene_counts[1]),
                            "rebar": int(scene_counts[2]), "statisticalNoise": int(scene_counts[3])},
                            "directionPointCounts": direction_point_counts,
                            "intersectionPointCount": intersection_total})
            if algo.descriptor.get("visualization", {}).get("schema") == "rebar-visualization-v2":
                summary["sceneClassCounts"]["fixture_formwork"] = int(scene_counts[4])
        if is_v5:
            summary.pop("intersectionPointCount", None)
            summary["intersectionCount"] = len(analysis.data.get("intersections", []))
            summary["instanceCount"] = len(analysis.data.get("instances", []))
            summary["rawSource"] = raw_summary
            summary["source"] = raw_summary
            summary["display"] = {"totalPointCount": total, "rebarPointCount": rebar_total,
                "sceneClassCounts": dict(zip(("unknown", "table", "rebar", "noise", "fixture"), map(int, scene_counts)))}
            summary["sceneClassCounts"] = summary["display"]["sceneClassCounts"]
            summary["rawLabelsPath"] = "labels/manifest.json"
            summary["featuresPath"] = "features/manifest.json"
        elif algo.descriptor.get("capabilities", {}).get("rawLabels", False):
            summary["rawSource"] = write_raw_labels(stage / "labels", context, algo, analysis)
            summary["rawLabelsPath"] = "labels/manifest.json"
        if prior is not None:
            summary["bimPrior"] = {"fingerprint": prior.get("fingerprint"),
                                   "diagnostics": prior.get("diagnostics", {}),
                                   "designBarCount": len(prior.get("bars", []))}
        artifact_metadata = {
            "artifactVersion": artifact_version,
            "algorithm": {
                "id": algo.descriptor["id"],
                "version": algo.descriptor["version"],
            },
            "analysisSchema": algo.descriptor.get("analysisSchema", "rebar-analysis-v1"),
            "capabilities": algo.descriptor["capabilities"],
            "inputOptions": opts,
            "effectiveParameters": effective,
            "summary": summary,
        }
        if "visualization" in algo.descriptor:
            artifact_metadata["visualization"] = algo.descriptor["visualization"]
        # Keep the root manifest compact: the potentially multi-megabyte,
        # implementation-specific analysis belongs in the separately served
        # result document and is covered by the artifact hash below.
        result = {
            "schema": artifact_metadata["analysisSchema"],
            **artifact_metadata,
            "input": loaded.report,
            "analysis": analysis.data,
        }
        (stage / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        hasher = hashlib.sha256()
        for artifact in sorted((p for p in stage.rglob("*") if p.is_file()), key=lambda p: p.relative_to(stage).as_posix()):
            hasher.update(artifact.relative_to(stage).as_posix().encode("utf-8"))
            hasher.update(b"\0")
            with artifact.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
        digest = hasher.hexdigest()
        manifest = {
            "schema": "rebar-artifact-manifest-v2" if is_v5 else "rebar-artifact-manifest-v1",
            **artifact_metadata,
            "resultPath": "result.json",
            "tilesetPath": "tiles/tileset.json",
            "manifestPath": "manifest.json",
            "contentHash": digest,
            "byteSize": 0,
            **({"featuresPath": "features/manifest.json"} if is_v5 else {}),
        }
        # Include the manifest itself in byteSize; repeat until the encoded
        # decimal field reaches a stable length.
        for _ in range(3):
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            actual_size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
            if manifest["byteSize"] == actual_size: break
            manifest["byteSize"] = actual_size
        # copytree preserves the uploader's private 0700/0600 modes, but the
        # files are now owned by the container user. The shared setgid group
        # lets the separate Go process validate, serve, and retire the tree.
        _publish_shared_artifact_permissions(stage)
        _verify_source()
        if is_v5:
            if output.exists():
                raise PointCloudInputError('V5 artifact version already exists')
            stage.rename(output)
            return manifest
        # Publish by rename.  A prior consumable artifact is retained until the
        # replacement is fully built; restore it if the final rename fails.
        backup: Path | None = None
        if output.exists():
            backup = output.with_name(f".{output.name}.previous-{uuid.uuid4().hex}")
            output.replace(backup)
        try:
            stage.replace(output)
        except Exception:
            if backup is not None and backup.exists(): backup.replace(output)
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        if analysis is not None and hasattr(algo, "close"):
            algo.close(analysis)


def write_json_atomically(
    output_path: str | os.PathLike[str],
    payload: Mapping[str, Any],
) -> Path:
    """Publish UTF-8 JSON with fsync + same-directory atomic replacement."""

    final_path = Path(output_path).expanduser().resolve(strict=False)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=final_path.parent,
        prefix=f".{final_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, final_path)
        replaced = True
        return final_path
    finally:
        if not replaced:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _parse_params_json(raw: str) -> RebarSegmentationParams:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PointCloudInputError(f"params-json is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise PointCloudInputError("params-json must decode to a JSON object")
    return RebarSegmentationParams.from_value(value)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the training-free geometric rebar segmentation PoC"
    )
    parser.add_argument("point_cloud", help="input .las/.laz/.ply/.pcd file")
    parser.add_argument(
        "--params-json",
        default="{}",
        help="inline JSON object with RebarSegmentationParams overrides",
    )
    parser.add_argument("--output-json", required=True, help="atomic JSON output path")
    parser.add_argument(
        "--max-input-points",
        type=int,
        default=DEFAULT_MAX_INPUT_POINTS,
        help=f"hard detection-point cap (default: {DEFAULT_MAX_INPUT_POINTS})",
    )
    parser.add_argument(
        "--voxel-size",
        type=float,
        default=None,
        help=(
            "optional metre-valued voxel size; format-aware deterministic sampling "
            "is used above the cap"
        ),
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help="optional shared-storage root constraint (the HTTP API always sets one)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        params = _parse_params_json(args.params_json)
        input_path = resolve_point_cloud_path(
            args.point_cloud,
            storage_root=args.storage_root,
        )
        output_path = Path(args.output_json).expanduser().resolve(strict=False)
        if output_path == input_path:
            raise PointCloudInputError(
                "output-json must not overwrite the input point-cloud file"
            )
        result = run_segmentation_file(
            input_path,
            params=params,
            max_input_points=args.max_input_points,
            voxel_size=args.voxel_size,
            storage_root=args.storage_root,
        )
        output_path = write_json_atomically(output_path, result)
    except (PointCloudInputError, RebarSegmentationError, OSError, ValueError) as exc:
        print(f"rebar PoC failed: {exc}", file=sys.stderr)
        return 2

    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
