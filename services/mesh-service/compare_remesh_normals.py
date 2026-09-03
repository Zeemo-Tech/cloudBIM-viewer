#!/usr/bin/env python3
"""统计 remesh 后网格与原始网格在「逐三角面重心」处的法向偏差。

对 remesh 网格的每个三角形取重心，用三个顶点法线（1/3,1/3,1/3）插值后归一化得到 n_r；
在原始网格上对重心做最近三角形查询，在最近点上用重心坐标插值原始顶点法线得到 n_o；
统计 |n_r·n_o| 与夹角（度）的分位数。

复杂度（令 F_r = remesh 面数，F_o = 原始面数，V 为相应顶点数）：
  - 建 BVH / RaycastingScene：Open3D 内部对原始三角网格建树，典型期望 O(F_o log F_o) 量级（实现相关）。
  - 每个查询点最近三角形：期望约 O(log F_o)（BVH 遍历）；共 F_r 个点 → 总体期望 O(F_r log F_o)。
  - 重心坐标与法线插值：向量化后对每批 O(B)，B 为 batch 大小，全量 O(F_r)。
  - 内存：O(V_o + V_r + F_r) 量级（存全部采样点的 dot/angle 数组用于分位数）。

并行：
  - Open3D 底层可能使用 OpenMP；可通过环境变量 OMP_NUM_THREADS 限制/放开 CPU 线程数。
  - 本脚本可选 --jobs N：多进程分块处理查询点，每进程独立加载原始网格并建树（避免 Scene 跨进程序列化问题），
    适合 F_r 很大且单机多核场景；N>1 时有额外进程启动与数组 pickle 开销，小网格用 --jobs 1 更快。

性能注意：
  - 适当增大 --batch 可减少 Python/Open3D 边界调用次数；过大可能增加峰值内存。
  - 主流程对已预分配 dot/angle 缓冲区按批写入，避免 list + concatenate 的额外峰值内存。

用法（建议在 mesh-service 容器内执行，与生产依赖一致）：
  python compare_remesh_normals.py <原始网格> <remesh 后网格> [--batch 200000] [--jobs 4]

  docker compose run --rm mesh-service python3 compare_remesh_normals.py /data/a.glb /data/b.ply

网格格式：Open3D 可读即可（glb/gltf/ply/obj/stl 等）。
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import numpy as np
import open3d as o3d
import open3d.core as o3c

# 多进程 worker 全局（由 initializer 填充）
_W_SCENE: Any = None
_W_VO: np.ndarray | None = None
_W_FO: np.ndarray | None = None
_W_NO: np.ndarray | None = None


def _load_mesh_triangles(path: str) -> o3d.geometry.TriangleMesh:
    """读取三角网格；若为 Scene 等多物体，需先合并（此处仅单文件单 mesh）。"""
    mesh = o3d.io.read_triangle_mesh(path)
    if mesh.is_empty():
        raise SystemExit(f"空网格或无法读取: {path}")
    mesh.remove_duplicated_vertices()
    mesh.remove_duplicated_triangles()
    mesh.remove_unreferenced_vertices()
    if not mesh.has_vertex_normals() or len(mesh.vertex_normals) == 0:
        mesh.compute_vertex_normals()
    return mesh


def _face_centroids_and_normals(
    vertices: np.ndarray, faces: np.ndarray, vertex_normals: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """每个面的重心与重心处顶点法线插值（等权 1/3）后归一化。返回 (F,3), (F,3)。"""
    i0, i1, i2 = faces[:, 0], faces[:, 1], faces[:, 2]
    c = (vertices[i0] + vertices[i1] + vertices[i2]) / 3.0
    n = vertex_normals[i0] + vertex_normals[i1] + vertex_normals[i2]
    norms = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, np.maximum(norms, 1e-30))
    return c.astype(np.float32, copy=False), n.astype(np.float32, copy=False)


def _barycentric_batch(
    v0: np.ndarray, v1: np.ndarray, v2: np.ndarray, p: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """p 相对三角形 v0v1v2 的重心坐标 (u,v,w)，u 对应 v0。向量化，形状 (N,3)。"""
    v0v1 = v1 - v0
    v0v2 = v2 - v0
    v0p = p - v0
    d00 = np.sum(v0v1 * v0v1, axis=1)
    d01 = np.sum(v0v1 * v0v2, axis=1)
    d11 = np.sum(v0v2 * v0v2, axis=1)
    d20 = np.sum(v0p * v0v1, axis=1)
    d21 = np.sum(v0p * v0v2, axis=1)
    denom = d00 * d11 - d01 * d01
    eps = 1e-20
    safe = np.abs(denom) > eps
    v = np.zeros_like(d00)
    w = np.zeros_like(d00)
    inv = np.zeros_like(d00)
    inv[safe] = 1.0 / denom[safe]
    v[safe] = (d11[safe] * d20[safe] - d01[safe] * d21[safe]) * inv[safe]
    w[safe] = (d00[safe] * d21[safe] - d01[safe] * d20[safe]) * inv[safe]
    u = 1.0 - v - w
    u = np.clip(u, 0.0, 1.0)
    v = np.clip(v, 0.0, 1.0)
    w = np.clip(w, 0.0, 1.0)
    s = u + v + w
    s = np.maximum(s, 1e-30)
    u, v, w = u / s, v / s, w / s
    return u, v, w


def _build_closest_point_scene(mesh: o3d.geometry.TriangleMesh):
    t_mesh = o3d.t.geometry.TriangleMesh.from_legacy(mesh)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(t_mesh)
    return scene


def _tensor_to_numpy_f64(t: o3c.Tensor) -> np.ndarray:
    return np.asarray(t.numpy(), dtype=np.float64)


def _process_centroids_batch(
    scene: Any,
    vo: np.ndarray,
    fo: np.ndarray,
    no: np.ndarray,
    centroids: np.ndarray,
    n_remesh: np.ndarray,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """对一批查询点（整块 centroids）做子批最近点 + 法线对比。返回 dots, angles, neg_count, degenerate_count。"""
    n = centroids.shape[0]
    dots_parts: list[np.ndarray] = []
    ang_parts: list[np.ndarray] = []
    neg_dot_count = 0
    degenerate_closest = 0

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        chunk = centroids[start:end]
        q = o3c.Tensor(chunk, dtype=o3c.Dtype.Float32)
        ans = scene.compute_closest_points(q)

        pts = _tensor_to_numpy_f64(ans["points"])
        tri_ids = np.asarray(ans["primitive_ids"].numpy()).reshape(-1).astype(np.int64)

        v0 = vo[fo[tri_ids, 0]]
        v1 = vo[fo[tri_ids, 1]]
        v2 = vo[fo[tri_ids, 2]]
        nn0 = no[fo[tri_ids, 0]]
        nn1 = no[fo[tri_ids, 1]]
        nn2 = no[fo[tri_ids, 2]]

        u, v, w = _barycentric_batch(v0, v1, v2, pts)
        n_orig = u[:, np.newaxis] * nn0 + v[:, np.newaxis] * nn1 + w[:, np.newaxis] * nn2
        on = np.linalg.norm(n_orig, axis=1, keepdims=True)
        degenerate_closest += int(np.sum(on.flatten() < 1e-15))
        n_orig = np.divide(n_orig, np.maximum(on, 1e-30))

        nr_chunk = n_remesh[start:end]
        dots = np.sum(nr_chunk * n_orig, axis=1)
        neg_dot_count += int(np.sum(dots < 0.0))
        ad = np.abs(dots)
        ad = np.clip(ad, 0.0, 1.0)
        ang = np.degrees(np.arccos(ad))

        dots_parts.append(dots.astype(np.float64, copy=False))
        ang_parts.append(ang)

    if not dots_parts:
        return np.array([]), np.array([]), 0, 0
    return (
        np.concatenate(dots_parts),
        np.concatenate(ang_parts),
        neg_dot_count,
        degenerate_closest,
    )


def _worker_init(orig_path: str) -> None:
    """子进程入口：只加载原始网格并建树一次。"""
    global _W_SCENE, _W_VO, _W_FO, _W_NO
    orig = _load_mesh_triangles(orig_path)
    _W_VO = np.asarray(orig.vertices)
    _W_FO = np.asarray(orig.triangles)
    _W_NO = np.asarray(orig.vertex_normals)
    if _W_NO.shape[0] != _W_VO.shape[0]:
        orig.compute_vertex_normals()
        _W_NO = np.asarray(orig.vertex_normals)
    _W_SCENE = _build_closest_point_scene(orig)


def _worker_task(payload: tuple[np.ndarray, np.ndarray, int]) -> tuple[np.ndarray, np.ndarray, int, int]:
    """子进程任务：(centroids, n_remesh, internal_batch_size)。"""
    centroids, n_remesh, internal_batch = payload
    assert _W_SCENE is not None and _W_VO is not None and _W_FO is not None and _W_NO is not None
    return _process_centroids_batch(
        _W_SCENE, _W_VO, _W_FO, _W_NO, centroids, n_remesh, internal_batch
    )


def compare_normals(
    orig: o3d.geometry.TriangleMesh,
    remesh: o3d.geometry.TriangleMesh,
    batch_size: int = 200_000,
    jobs: int = 1,
    orig_path_for_workers: str | None = None,
) -> dict:
    vo = np.asarray(orig.vertices)
    fo = np.asarray(orig.triangles)
    no = np.asarray(orig.vertex_normals)
    if no.shape[0] != vo.shape[0]:
        orig.compute_vertex_normals()
        no = np.asarray(orig.vertex_normals)

    vr = np.asarray(remesh.vertices)
    fr = np.asarray(remesh.triangles)
    nr = np.asarray(remesh.vertex_normals)
    if nr.shape[0] != vr.shape[0]:
        remesh.compute_vertex_normals()
        nr = np.asarray(remesh.vertex_normals)

    centroids, n_remesh = _face_centroids_and_normals(vr, fr, nr)
    n_faces = centroids.shape[0]

    t0 = time.time()

    if jobs <= 1:
        scene = _build_closest_point_scene(orig)
        dots_all, angles_all, neg_dot_count, degenerate_closest = _process_centroids_batch(
            scene, vo, fo, no, centroids, n_remesh, batch_size
        )
    else:
        if not orig_path_for_workers:
            raise ValueError("多进程模式需要 orig_path_for_workers")
        # 将 [0, n_faces) 尽量均分到各进程（避免 linspace 取整产生空段）
        base, rem = divmod(n_faces, jobs)
        chunks: list[tuple[int, int]] = []
        lo = 0
        for j in range(jobs):
            sz = base + (1 if j < rem else 0)
            hi = lo + sz
            if sz > 0:
                chunks.append((lo, hi))
            lo = hi
        dots_all = np.empty(n_faces, dtype=np.float64)
        angles_all = np.empty(n_faces, dtype=np.float64)
        neg_dot_count = 0
        degenerate_closest = 0

        # 父进程已加载 Open3D 时，Linux 默认 fork 子进程可能继承不安全状态；spawn 子进程干净启动
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=jobs,
            mp_context=ctx,
            initializer=_worker_init,
            initargs=(orig_path_for_workers,),
        ) as ex:
            futures = {}
            for lo, hi in chunks:
                if lo >= hi:
                    continue
                fut = ex.submit(
                    _worker_task,
                    (centroids[lo:hi].copy(), n_remesh[lo:hi].copy(), batch_size),
                )
                futures[fut] = (lo, hi)
            for fut in as_completed(futures):
                lo, hi = futures[fut]
                d, a, neg, deg = fut.result()
                dots_all[lo:hi] = d
                angles_all[lo:hi] = a
                neg_dot_count += neg
                degenerate_closest += deg

    dt = time.time() - t0

    def pct(x: np.ndarray, p: float) -> float:
        return float(np.percentile(x, p)) if x.size else float("nan")

    return {
        "orig_vertices": int(vo.shape[0]),
        "orig_faces": int(fo.shape[0]),
        "remesh_vertices": int(vr.shape[0]),
        "remesh_faces": int(fr.shape[0]),
        "sample_points": int(n_faces),
        "batch_size": int(batch_size),
        "jobs": int(max(1, jobs)),
        "elapsed_sec": float(dt),
        "dot_mean": float(np.mean(dots_all)) if n_faces else float("nan"),
        "dot_std": float(np.std(dots_all)) if n_faces else float("nan"),
        "angle_deg_mean": float(np.mean(angles_all)) if n_faces else float("nan"),
        "angle_deg_std": float(np.std(angles_all)) if n_faces else float("nan"),
        "angle_deg_p50": pct(angles_all, 50),
        "angle_deg_p90": pct(angles_all, 90),
        "angle_deg_p95": pct(angles_all, 95),
        "angle_deg_p99": pct(angles_all, 99),
        "angle_deg_max": float(np.max(angles_all)) if n_faces else float("nan"),
        "pct_dot_negative": 100.0 * neg_dot_count / n_faces if n_faces else float("nan"),
        "degenerate_interp_vertices": int(degenerate_closest),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="remesh 与原始网格逐面重心法向偏差统计")
    ap.add_argument("original_mesh", help="原始网格路径")
    ap.add_argument("remeshed_mesh", help="remesh 后网格路径")
    ap.add_argument(
        "--batch",
        type=int,
        default=200_000,
        help="每批最近点查询数量（过大占内存；多进程下为各进程内子批大小）",
    )
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="并行进程数；1 为单进程。>1 时每进程单独加载原始网格并建 BVH（需重复读盘与建树开销）",
    )
    args = ap.parse_args()

    orig_abs = os.path.abspath(args.original_mesh)
    rem_abs = os.path.abspath(args.remeshed_mesh)

    for p in (orig_abs, rem_abs):
        if not os.path.isfile(p):
            print(f"文件不存在: {p}", file=sys.stderr)
            sys.exit(1)

    omp = os.environ.get("OMP_NUM_THREADS", "")
    if omp:
        print(f"OMP_NUM_THREADS={omp}", flush=True)

    print("加载原始网格…", flush=True)
    orig = _load_mesh_triangles(orig_abs)
    print("加载 remesh 网格…", flush=True)
    rem = _load_mesh_triangles(rem_abs)

    stats = compare_normals(
        orig,
        rem,
        batch_size=max(1024, args.batch),
        jobs=max(1, args.jobs),
        orig_path_for_workers=orig_abs if args.jobs > 1 else None,
    )

    print()
    print("=== 网格规模 ===")
    print(f"  原始:   V={stats['orig_vertices']:,}  F={stats['orig_faces']:,}")
    print(f"  remesh: V={stats['remesh_vertices']:,}  F={stats['remesh_faces']:,}")
    print(f"  采样:   每个 remesh 三角形重心 1 点，共 {stats['sample_points']:,} 点")
    print()
    print("=== n_r·n_o（有向，理想为 +1）===")
    print(f"  mean={stats['dot_mean']:.6f}  std={stats['dot_std']:.6f}")
    print(f"  点积<0 占比: {stats['pct_dot_negative']:.4f}% （整体法向可能反向或薄壁两侧）")
    print()
    print("=== 无向夹角 arccos(|n_r·n_o|)（度）===")
    print(f"  mean={stats['angle_deg_mean']:.4f}  std={stats['angle_deg_std']:.4f}")
    print(f"  p50={stats['angle_deg_p50']:.4f}  p90={stats['angle_deg_p90']:.4f}  "
          f"p95={stats['angle_deg_p95']:.4f}  p99={stats['angle_deg_p99']:.4f}  max={stats['angle_deg_max']:.4f}")
    print()
    print("=== 性能 ===")
    print(f"  jobs={stats['jobs']}  batch={stats['batch_size']:,}  耗时 {stats['elapsed_sec']:.2f}s")
    if stats["degenerate_interp_vertices"]:
        print(f"  警告: {stats['degenerate_interp_vertices']} 个样本上插值法线长度过小（退化三角形附近）")


if __name__ == "__main__":
    main()
