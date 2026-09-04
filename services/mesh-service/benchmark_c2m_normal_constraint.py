"""法向量约束 kNN 方案 benchmark 脚本

使用真实 BIM 网格 + 真实 LAS 点云对比：
- k=1（原始行为，无约束）
- k=8 / k=16（启用约束，半空间 + 夹角 75° / 60°）

输出：耗时、回退顶点比例、统计量（mean/std/p90）对比。
运行方式（容器内）:
  docker exec zhongjian-back-mesh-service-1 python3 /app/benchmark_c2m_normal_constraint.py
"""

from __future__ import annotations

import sys
import os
import time

import numpy as np
import open3d as o3d

sys.path.insert(0, "/app/algorithms")
from c2m_distance import (
    compute_signed_mesh_to_cloud_distances,
    compute_statistics,
    load_and_downsample_las,
    column_major_to_matrix4,
    apply_transform,
)

SEP = "=" * 72

# 真实数据路径（容器内）
PLY_PATH = "/storage/mesh_remesh/14/remeshed_4294560682.ply"
LAS_PATH = "/storage/project_files/org_2/project_3/scan/深圳湾工地_2025-10-17-150539_7ff44386bc18/points.las"
VOXEL_SIZE = 0.05

# 单位矩阵（不做坐标变换，仅测算法本身）
IDENTITY_M16 = list(np.eye(4).T.flatten())


def run_case(label: str, mesh, pcd, **kwargs) -> dict:
    t0 = time.time()
    dists = compute_signed_mesh_to_cloud_distances(mesh, pcd, **kwargs)
    elapsed = time.time() - t0
    stats = compute_statistics(dists, max_hist_dist=1.0, n_bins=20)["stats"]
    return {
        "label": label,
        "elapsed": elapsed,
        "mean": stats["mean"],
        "std": stats["std"],
        "p90": stats["p90"],
        "min": stats["min"],
        "max": stats["max"],
    }


def main():
    print(f"\n{SEP}")
    print("  C2M 法向量约束 benchmark（真实 BIM + 真实 LAS）")
    print(SEP)

    # 读取 mesh
    print("  [1/2] 读取 BIM 网格...")
    mesh = o3d.io.read_triangle_mesh(PLY_PATH)
    mesh.compute_vertex_normals()
    print(f"        顶点数={len(mesh.vertices):,}  面数={len(mesh.triangles):,}")

    # 读取 LAS + 降采样
    print(f"  [2/2] 读取 LAS + 降采样 (voxel={VOXEL_SIZE})...")
    pcd, pts_before, _ = load_and_downsample_las(LAS_PATH, VOXEL_SIZE)
    print(f"        原始点数={pts_before:,}  降采样后={len(pcd.points):,}")

    cases = [
        dict(label="k=1  无约束（原始行为）",
             knn_k=1, normal_constraint_enabled=False),
        dict(label="k=8  约束 半空间+75°",
             knn_k=8, normal_constraint_enabled=True,
             normal_half_space_only=True, normal_max_angle_deg=75.0),
        dict(label="k=8  约束 半空间+60°",
             knn_k=8, normal_constraint_enabled=True,
             normal_half_space_only=True, normal_max_angle_deg=60.0),
        dict(label="k=16 约束 半空间+75°",
             knn_k=16, normal_constraint_enabled=True,
             normal_half_space_only=True, normal_max_angle_deg=75.0),
        dict(label="k=16 约束 半空间+60°",
             knn_k=16, normal_constraint_enabled=True,
             normal_half_space_only=True, normal_max_angle_deg=60.0),
    ]

    print(f"\n  开始跑各场景（共 {len(cases)} 个）...\n")
    results = []
    for case in cases:
        label = case.pop("label")
        print(f"  → {label}")
        r = run_case(label, mesh, pcd, **case)
        results.append(r)
        print(f"     耗时={r['elapsed']:.1f}s  mean={r['mean']:.4f}  std={r['std']:.4f}  p90={r['p90']:.4f}")

    print(f"\n{SEP}")
    print(f"  {'场景':<33} {'耗时(s)':>8} {'倍率':>6} {'mean':>8} {'std':>8} {'p90':>8} {'min':>8} {'max':>8}")
    print("  " + "-" * 90)
    base_elapsed = results[0]["elapsed"]
    for r in results:
        ratio = r["elapsed"] / max(base_elapsed, 1e-6)
        print(
            f"  {r['label']:<33} {r['elapsed']:>8.1f} {ratio:>5.1f}x "
            f"{r['mean']:>8.4f} {r['std']:>8.4f} {r['p90']:>8.4f} "
            f"{r['min']:>8.4f} {r['max']:>8.4f}"
        )

    print(f"\n{SEP}")
    print(f"  基准耗时（k=1 无约束）: {base_elapsed:.1f}s")
    fastest = min(results[1:], key=lambda r: r["elapsed"])
    print(f"  最快约束方案: {fastest['label'].strip()}  ({fastest['elapsed']:.1f}s, {fastest['elapsed']/max(base_elapsed,1e-6):.1f}x)")
    print(f"\n  建议默认：k=8，半空间+75°（平衡精度与性能）\n")
    print(SEP + "\n")


if __name__ == "__main__":
    main()
