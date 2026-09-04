"""C2M 端到端集成测试

测试策略：
1. 用真实的 LAS + PLY 文件
2. 分三种 alignment 矩阵测试：
   A. 原始对齐矩阵（预期：小距离，高 IoU）
   B. 在原始矩阵 T 上额外加 1000m X 偏移（完全分离，预期：bboxIoU≈0，大距离）
   C. 在原始矩阵 T 上额外加 mesh 半宽偏移（一侧接触，预期：中等 IoU，中等距离）
"""

import json
import sys
import numpy as np

# ── 常量 ────────────────────────────────────────────────────────────────
LAS_PATH  = "/storage/project_files/org_1/project_3/scan/深圳湾工hong_2df28f2192c5/points.las"
PLY_PATH  = "/storage/mesh_remesh/7/remeshed_1774533968.ply"

# 从数据库读取的四元数 + 平移
QX = -0.7068577930838563
QY =  0.01876327147968164
QZ =  0.01876327147968164
QW =  0.7068577930838563
TX =  105647.56089233355
TY =  54.690423974867855
TZ = -17327.27976439454

SEPARATOR = "=" * 70


def quat_to_rotation_matrix(qx, qy, qz, qw):
    """四元数 → 3x3 旋转矩阵（行主序 numpy）。"""
    n = np.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    qx, qy, qz, qw = qx/n, qy/n, qz/n, qw/n
    R = np.array([
        [1 - 2*(qy*qy + qz*qz), 2*(qx*qy - qz*qw), 2*(qx*qz + qy*qw)],
        [2*(qx*qy + qz*qw), 1 - 2*(qx*qx + qz*qz), 2*(qy*qz - qx*qw)],
        [2*(qx*qz - qy*qw), 2*(qy*qz + qx*qw), 1 - 2*(qx*qx + qy*qy)],
    ])
    return R


def build_column_major_matrix16(qx, qy, qz, qw, tx, ty, tz):
    """复制 Go 后端的 buildColumnMajorMatrix4 逻辑，生成 16 元素列主序数组。"""
    R = quat_to_rotation_matrix(qx, qy, qz, qw)
    return [
        R[0][0], R[1][0], R[2][0], 0,  # col 0
        R[0][1], R[1][1], R[2][1], 0,  # col 1
        R[0][2], R[1][2], R[2][2], 0,  # col 2
        tx, ty, tz, 1                   # col 3
    ]


def add_translation_offset(m16: list, dx=0.0, dy=0.0, dz=0.0) -> list:
    """在现有列主序矩阵的平移分量上叠加额外偏移。
    Three.js 列主序中，indices 12, 13, 14 是平移 (tx, ty, tz)。
    """
    m = m16[:]
    m[12] += dx
    m[13] += dy
    m[14] += dz
    return m


def call_c2m(alignment_matrix: list, label: str, voxel_size: float = 0.1) -> dict:
    """直接调用 Python 算法（不走 HTTP，避免序列化）。"""
    import open3d as o3d
    import os, time

    sys.path.insert(0, "/app/algorithms")
    from c2m_distance import (
        load_and_downsample_las,
        column_major_to_matrix4,
        apply_transform,
        compute_signed_mesh_to_cloud_distances,
        compute_statistics,
        compute_bbox_overlap,
    )

    print(f"\n{SEPARATOR}")
    print(f"  测试: {label}")
    print(SEPARATOR)

    t_start = time.time()

    # 1. 加载 LAS + 降采样
    print("  [1] 加载 LAS + 降采样...", end="", flush=True)
    pcd, pts_before, scan_bbox_raw = load_and_downsample_las(LAS_PATH, voxel_size)
    pts_after = len(pcd.points)
    print(f" {pts_before:,} → {pts_after:,} 点")

    # 2. 变换 scan 到 BIM 坐标系
    print("  [2] 施加 alignment 变换...", end="", flush=True)
    matrix = column_major_to_matrix4(alignment_matrix)
    scan_bbox = apply_transform(pcd, matrix)
    print(f" scan bbox after: X[{scan_bbox['min'][0]:.2f}, {scan_bbox['max'][0]:.2f}]"
          f"  Y[{scan_bbox['min'][1]:.2f}, {scan_bbox['max'][1]:.2f}]"
          f"  Z[{scan_bbox['min'][2]:.2f}, {scan_bbox['max'][2]:.2f}]")

    # 3. 加载 mesh
    print("  [3] 加载 PLY mesh...", end="", flush=True)
    mesh = o3d.io.read_triangle_mesh(PLY_PATH)
    mesh_pts = np.asarray(mesh.vertices)
    mesh_bbox = {"min": mesh_pts.min(axis=0).tolist(), "max": mesh_pts.max(axis=0).tolist()}
    print(f" {len(mesh.triangles):,} 三角面")
    print(f"       mesh bbox: X[{mesh_bbox['min'][0]:.2f}, {mesh_bbox['max'][0]:.2f}]"
          f"  Y[{mesh_bbox['min'][1]:.2f}, {mesh_bbox['max'][1]:.2f}]"
          f"  Z[{mesh_bbox['min'][2]:.2f}, {mesh_bbox['max'][2]:.2f}]")

    # 4. bbox overlap
    overlap = compute_bbox_overlap(
        scan_bbox["min"], scan_bbox["max"],
        mesh_bbox["min"], mesh_bbox["max"],
    )

    # 5. 计算有符号 C2M 距离
    print("  [4] 计算有符号 C2M 距离...", end="", flush=True)
    distances = compute_signed_mesh_to_cloud_distances(mesh, pcd)
    print(f" {len(distances):,} 顶点（含正负值）")

    # 6. 统计（对称直方图 [-500, +500]）
    stats_result = compute_statistics(distances, max_hist_dist=500.0, n_bins=50)
    stats = stats_result["stats"]

    elapsed = time.time() - t_start

    print(f"\n  {'─'*50}")
    print(f"  BBox Overlap IoU : {overlap:.6f}")
    print(f"  Min    : {stats['min']:.4f} m  （负=内缩）")
    print(f"  Max    : {stats['max']:.4f} m  （正=外凸）")
    print(f"  Mean   : {stats['mean']:.4f} m")
    print(f"  Std    : {stats['std']:.4f} m")
    print(f"  P50    : {stats['p50']:.4f} m")
    print(f"  P90    : {stats['p90']:.4f} m")
    print(f"  P99    : {stats['p99']:.4f} m")
    print(f"  耗时   : {elapsed:.1f}s")

    return {
        "label": label,
        "bboxOverlap": overlap,
        "stats": stats,
        "scanBbox": scan_bbox,
        "meshBbox": mesh_bbox,
    }


def main():
    # ── 基础矩阵 ──────────────────────────────────────────────────────────
    m16_base = build_column_major_matrix16(QX, QY, QZ, QW, TX, TY, TZ)

    print(f"\n{'='*70}")
    print("  C2M End-to-End 测试：真实 LAS + PLY")
    print(f"{'='*70}")
    print(f"  LAS  : {LAS_PATH}")
    print(f"  PLY  : {PLY_PATH}")
    print(f"  矩阵平移: Tx={TX:.2f}, Ty={TY:.2f}, Tz={TZ:.2f}")

    results = []

    # ── 场景 A：原始对齐矩阵 ──────────────────────────────────────────────
    r_a = call_c2m(m16_base, "场景A：原始对齐矩阵（预期：IoU高，距离小）")
    results.append(r_a)

    # 从场景 A 计算 mesh bbox 半宽（用于构造有意义的偏移）
    mb = r_a["meshBbox"]
    mesh_half_x = (mb["max"][0] - mb["min"][0]) / 2.0
    mesh_half_y = (mb["max"][1] - mb["min"][1]) / 2.0

    sb = r_a["scanBbox"]
    scan_half_x = (sb["max"][0] - sb["min"][0]) / 2.0

    # 完全分离需要偏移量 > mesh宽 + scan宽
    full_sep_offset = mesh_half_x + scan_half_x + 50.0  # 额外 50m 保证
    print(f"\n  mesh X 半宽={mesh_half_x:.2f}m, scan X 半宽={scan_half_x:.2f}m")
    print(f"  完全分离需要 X 偏移 > {full_sep_offset:.2f}m → 使用 {full_sep_offset + 100:.0f}m")

    # ── 场景 B：完全分离（scan 移出 mesh bbox 外侧）──────────────────────
    m16_b = add_translation_offset(m16_base, dx=full_sep_offset + 100.0)
    r_b = call_c2m(m16_b, f"场景B：完全分离（X偏移+{full_sep_offset+100:.0f}m，预期：IoU≈0，距离大）")
    results.append(r_b)

    # ── 场景 C：mesh 边界仅一侧接触 scan ─────────────────────────────────
    # scan 移动到与 mesh 仅右边界接触处
    # scan_center_x after transform ≈ (sb["min"][0] + sb["max"][0]) / 2
    # mesh_max_x ≈ mb["max"][0]
    # 为了让 scan 左边界 = mesh 右边界：
    # scan_min_x_new = mesh_max_x
    # scan_min_x_new = sb["min"][0] + dx_c → dx_c = mesh_max_x - sb["min"][0]
    dx_c = mb["max"][0] - sb["min"][0] + 5.0  # 5m 余量确保仅一侧
    m16_c = add_translation_offset(m16_base, dx=dx_c)
    r_c = call_c2m(m16_c, f"场景C：一侧接触（X偏移+{dx_c:.1f}m，预期：低 IoU，部分距离大）")
    results.append(r_c)

    # ── 汇总 ──────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  汇总与预期对比")
    print(f"{'='*70}")
    print(f"  {'场景':<40} {'BBoxIoU':>10} {'Mean(m)':>10} {'P50(m)':>10} {'通过?':>6}")
    print(f"  {'─'*80}")

    all_pass = True
    for r in results:
        if "场景A" in r["label"]:
            expected = r["bboxOverlap"] > 0.1 and r["stats"]["mean"] < 5.0
            verdict = "✓" if expected else "✗"
            hint = f"IoU应>0.1, Mean应<5m"
        elif "场景B" in r["label"]:
            expected = r["bboxOverlap"] < 0.01 and r["stats"]["mean"] > 10.0
            verdict = "✓" if expected else "✗"
            hint = f"IoU应≈0, Mean应>>10m"
        elif "场景C" in r["label"]:
            expected = r["bboxOverlap"] < r_a["bboxOverlap"] and r["stats"]["mean"] > r_a["stats"]["mean"]
            verdict = "✓" if expected else "✗"
            hint = f"IoU应<场景A, Mean应>场景A"
        else:
            expected = True
            verdict = "?"
            hint = ""

        if not expected:
            all_pass = False

        print(f"  {r['label'][:40]:<40} {r['bboxOverlap']:>10.4f} {r['stats']['mean']:>10.4f} {r['stats']['p50']:>10.4f} {verdict:>6}")
        print(f"    → {hint}")

    print(f"\n  总体结论: {'✓ 所有场景符合预期，算法正确' if all_pass else '✗ 有场景不符合预期！'}")
    print(f"{'='*70}\n")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
