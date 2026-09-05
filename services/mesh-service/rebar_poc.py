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

    suffix = resolved.suffix.lower()
    if suffix not in SUPPORTED_POINT_CLOUD_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_POINT_CLOUD_EXTENSIONS))
        raise UnsupportedPointCloudFormatError(
            f"unsupported point-cloud format {suffix or '<none>'}; supported: {supported}"
        )
    return resolved


def _read_source_points(path: Path) -> np.ndarray:
    """Decode PLY/PCD through Open3D (which currently materializes the file)."""

    suffix = path.suffix.lower()
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

    path = resolve_point_cloud_path(input_path, storage_root=storage_root)
    if path.suffix.lower() in {".las", ".laz"}:
        return _load_las_streaming(
            path,
            max_input_points=max_input_points,
            voxel_size=float(voxel_size) if voxel_size is not None else None,
        )

    source_points = _read_source_points(path)
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
