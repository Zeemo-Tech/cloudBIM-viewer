"""Step 01: full-resolution, unoriented PCA normals and reusable spatial context.

Only chunk scheduling is in Python. Neighbor search, covariance and symmetric
eigendecomposition execute in native code. One shared, immutable cKDTree serves
all workers and remains available to subsequent pipeline steps.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
import os
import time
from typing import Callable

import numpy as np
from scipy.spatial import cKDTree
from threadpoolctl import threadpool_limits


VERSION = "pca-normals-v1"


def available_workers() -> int:
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)


@dataclass
class PointCloudContext:
    """Row i always means source LAS record i; no filtering or resampling.

    The caller must not modify positions while the tree is alive. A fresh debug
    run creates a fresh context. Downstream steps reuse this exact object, or
    rebuild the index if they change geometry or select a different population.
    """
    positions: np.ndarray
    tree: cKDTree
    normals: np.ndarray | None = None
    normal_valid: np.ndarray | None = None
    curvature: np.ndarray | None = None
    neighbor_radius: np.ndarray | None = None
    geometry_class: np.ndarray | None = None
    geometry_support: np.ndarray | None = None
    geometry_recovered: np.ndarray | None = None
    classification_cache: dict | None = None
    projection_class: np.ndarray | None = None
    projection_layer: np.ndarray | None = None
    projection_cache: dict | None = None
    fused_class: np.ndarray | None = None
    fused_region: np.ndarray | None = None
    fused_recovered: np.ndarray | None = None
    fused_reason: np.ndarray | None = None
    fusion_cache: dict | None = None
    region_cache: dict | None = None
    refined_class: np.ndarray | None = None
    refined_region: np.ndarray | None = None
    refined_zone: np.ndarray | None = None
    refined_changed: np.ndarray | None = None
    refined_reason: np.ndarray | None = None
    refinement_cache: dict | None = None
    internal_type: np.ndarray | None = None
    internal_instance: np.ndarray | None = None
    internal_segment: np.ndarray | None = None
    internal_confidence: np.ndarray | None = None
    internal_rebar_cache: dict | None = None

    @classmethod
    def build(cls, positions: np.ndarray):
        if positions.ndim != 2 or positions.shape[1] != 3 or len(positions) < 3:
            raise ValueError("法向量计算至少需要 3 个 XYZ 点")
        if positions.dtype != np.float64 or not positions.flags.c_contiguous:
            positions = np.ascontiguousarray(positions, dtype=np.float64)
        if not np.isfinite(positions).all():
            raise ValueError("源点云包含非有限坐标；本步骤不删除或替换源点")
        positions.flags.writeable = False
        return cls(positions, cKDTree(positions, leafsize=32, copy_data=False))


def estimate_normals(
    context: PointCloudContext, *, k: int = 32, workers: int | None = None,
    chunk_size: int = 8192, output: dict[str, np.ndarray] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Exact kNN PCA (k includes the query point), without sign orientation.

    Degenerate/coincident/collinear neighborhoods and numerically tied smallest
    eigenvalues have no unique normal: store (0,0,0), normal_valid=0. Curvature
    is lambda_min / trace(C), not an accuracy/confidence probability.
    """
    workers = available_workers() if workers is None else workers
    if isinstance(k, bool) or not isinstance(k, int) or not 3 <= k <= 128:
        raise ValueError("k 必须是 3–128 的整数（包含查询点自身）")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= available_workers():
        raise ValueError(f"workers 必须是 1–{available_workers()} 的整数")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    count = len(context.positions)
    effective_k = min(k, count)
    shapes = {"normals": ((count, 3), np.float32), "normal_valid": ((count,), np.uint8),
              "curvature": ((count,), np.float32), "neighbor_radius": ((count,), np.float32)}
    if output is None:
        output = {key: np.empty(shape, dtype) for key, (shape, dtype) in shapes.items()}
    for name, (shape, dtype) in shapes.items():
        if output[name].shape != shape or output[name].dtype != dtype:
            raise ValueError(f"Invalid output array: {name}")

    def chunk(start: int):
        stop = min(start + chunk_size, count)
        t0 = time.perf_counter()
        # Parallelism is at chunk level for BOTH search and PCA; avoid nested
        # cKDTree workers and BLAS thread teams for tiny 3x3 matrices.
        distance, indices = context.tree.query(context.positions[start:stop], k=effective_k, workers=1)
        query_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        neighbors = context.positions[indices]
        neighbors -= context.positions[start:stop, None, :]
        neighbors -= neighbors.mean(axis=1, keepdims=True)
        covariance = neighbors.transpose(0, 2, 1) @ neighbors
        values, vectors = np.linalg.eigh(covariance)
        values = np.maximum(values, 0.)
        tolerance = np.maximum(values[:, 2] * 1.e-10, 1.e-24)
        valid = (values[:, 1] > tolerance) & ((values[:, 1] - values[:, 0]) > tolerance)
        normal = vectors[:, :, 0]
        normal[~valid] = 0
        output["normals"][start:stop] = normal
        output["normal_valid"][start:stop] = valid
        output["curvature"][start:stop] = values[:, 0] / np.maximum(values.sum(axis=1), 1.e-30)
        output["neighbor_radius"][start:stop] = distance[:, -1]
        return stop - start, query_s, time.perf_counter() - t0

    started = time.perf_counter()
    query_worker_s = pca_worker_s = 0.
    completed = 0
    starts = iter(range(0, count, chunk_size))
    # Bound queued tasks and temporary neighborhood storage independently of N.
    with threadpool_limits(limits=1), ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(chunk, start) for start in list_next(starts, workers * 2)}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                size, query_s, pca_s = future.result()
                completed += size
                query_worker_s += query_s
                pca_worker_s += pca_s
                next_start = next(starts, None)
                if next_start is not None:
                    pending.add(pool.submit(chunk, next_start))
            if progress:
                progress(completed, count)
    for name, array in output.items():
        setattr(context, name, array)
    elapsed = time.perf_counter() - started
    return {"elapsedS": elapsed, "effectiveK": effective_k, "workers": workers,
            "chunkSize": chunk_size, "pointsPerSecond": count / max(elapsed, 1.e-9),
            "queryWorkerS": query_worker_s, "pcaWorkerS": pca_worker_s,
            "workerTimingNote": "Accumulated worker wall seconds overlap; do not add to stage wall time.",
            "treeReused": True, "treeBuildsDuringNormals": 0}


def list_next(iterator, count):
    from itertools import islice
    return islice(iterator, count)
