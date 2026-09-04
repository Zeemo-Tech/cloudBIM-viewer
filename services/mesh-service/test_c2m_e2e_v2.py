"""C2M 端到端集成测试 v2

修正测试策略（基于 v1 运行结果）：
- 当前 DB 中保存的对齐矩阵已经是用户"故意偏移到角落"的状态（IoU=0, MeanAbs=19m）
- 需要手动构造一个"理论上对齐"的矩阵（将 scan 中心对准 mesh 中心）来完整测试算法

测试场景：
A. 理论对齐矩阵（scan 中心 = mesh 中心）→ 预期: IoU 高, MeanAbs < 5m
B. 当前 DB 矩阵（故意偏移）→ 预期: IoU=0, MeanAbs≈19m (确认算法对不重合有正确响应)
C. 完全分离（再额外 +200m）→ 预期: IoU=0, MeanAbs≈200+m
D. 部分重合（当前偏移的一半）→ 预期: IoU 中等, MeanAbs < 场景B
"""

import json
import sys
import numpy as np

LAS_PATH  = "/storage/project_files/org_1/project_3/scan/深圳湾工hong_2df28f2192c5/points.las"
PLY_PATH  = "/storage/mesh_remesh/7/remeshed_1774533968.ply"

# DB 中存储的当前（偏移状态）四元数 + 平移
QX = -0.7068577930838563
QY =  0.01876327147968164
QZ =  0.01876327147968164
QW =  0.7068577930838563
TX =  105647.56089233355
TY =  54.690423974867855
TZ = -17327.27976439454

# v1 测量得到的 bbox：
# scan after transform: X[105619.11, 105685.03]  Y[51.89, 61.69]  Z[-17359.06, -17279.98]
# mesh:                 X[105586.18, 105617.55]  Y[51.84, 57.45]  Z[-17367.75, -17322.53]
SCAN_X_MIN_ORIG = 105619.11
SCAN_X_MAX_ORIG = 105685.03
MESH_X_MIN = 105586.18
MESH_X_MAX = 105617.55

# 让 scan 中心对准 mesh 中心需要的 X 偏移
SCAN_CENTER_X = (SCAN_X_MIN_ORIG + SCAN_X_MAX_ORIG) / 2.0   # 105652.07
MESH_CENTER_X = (MESH_X_MIN + MESH_X_MAX) / 2.0              # 105601.87
DX_ALIGN = MESH_CENTER_X - SCAN_CENTER_X   # ≈ -50.2m (左移 scan 对准 mesh)

SEPARATOR = "=" * 70


def quat_to_rotation_matrix(qx, qy, qz, qw):
    n = np.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    qx, qy, qz, qw = qx/n, qy/n, qz/n, qw/n
    return np.array([
        [1 - 2*(qy*qy + qz*qz), 2*(qx*qy - qz*qw), 2*(qx*qz + qy*qw)],
        [2*(qx*qy + qz*qw), 1 - 2*(qx*qx + qz*qz), 2*(qy*qz - qx*qw)],
        [2*(qx*qz - qy*qw), 2*(qy*qz + qx*qw), 1 - 2*(qx*qx + qy*qy)],
    ])


def build_column_major_matrix16(qx, qy, qz, qw, tx, ty, tz):
    R = quat_to_rotation_matrix(qx, qy, qz, qw)
    return [
        R[0][0], R[1][0], R[2][0], 0,
        R[0][1], R[1][1], R[2][1], 0,
        R[0][2], R[1][2], R[2][2], 0,
        tx, ty, tz, 1
    ]


def add_translation_offset(m16: list, dx=0.0, dy=0.0, dz=0.0) -> list:
    m = m16[:]
    m[12] += dx
    m[13] += dy
    m[14] += dz
    return m


def call_c2m(alignment_matrix: list, label: str, voxel_size: float = 0.1) -> dict:
    import open3d as o3d
    import time

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

    pcd, pts_before, _ = load_and_downsample_las(LAS_PATH, voxel_size)
    pts_after = len(pcd.points)
    print(f"  [1] LAS: {pts_before:,} → {pts_after:,} 点")

    matrix = column_major_to_matrix4(alignment_matrix)
    scan_bbox = apply_transform(pcd, matrix)
    print(f"  [2] scan bbox X=[{scan_bbox['min'][0]:.2f}, {scan_bbox['max'][0]:.2f}]")

    mesh = o3d.io.read_triangle_mesh(PLY_PATH)
    mesh_pts = np.asarray(mesh.vertices)
    mesh_bbox = {"min": mesh_pts.min(axis=0).tolist(), "max": mesh_pts.max(axis=0).tolist()}
    print(f"  [3] mesh bbox X=[{mesh_bbox['min'][0]:.2f}, {mesh_bbox['max'][0]:.2f}]")

    overlap = compute_bbox_overlap(
        scan_bbox["min"], scan_bbox["max"],
        mesh_bbox["min"], mesh_bbox["max"],
    )

    distances = compute_signed_mesh_to_cloud_distances(mesh, pcd)
    stats_result = compute_statistics(distances, max_hist_dist=500.0, n_bins=50)
    stats = stats_result["stats"]

    elapsed = time.time() - t_start
    print(f"  [4] BBoxIoU={overlap:.4f}  MeanAbs={stats['meanAbs']:.3f}m  RMSE={stats['rmse']:.3f}m  "
          f"P95Abs={stats['p95Abs']:.3f}m  SignedMean={stats['mean']:.3f}m  ({elapsed:.1f}s)")

    return {"label": label, "bboxOverlap": overlap, "stats": stats,
            "scanBbox": scan_bbox, "meshBbox": mesh_bbox}


def main():
    m16_base = build_column_major_matrix16(QX, QY, QZ, QW, TX, TY, TZ)

    print(f"\n{SEPARATOR}")
    print("  C2M E2E 测试 v2：理论对齐 + 偏移梯度验证")
    print(SEPARATOR)
    print(f"  DX_ALIGN = {DX_ALIGN:.2f}m（将 scan 中心对准 mesh 中心）")

    results = []

    # ── 场景 A：理论对齐（scan 中心 ≈ mesh 中心）────────────────────────
    m16_aligned = add_translation_offset(m16_base, dx=DX_ALIGN)
    r_a = call_c2m(m16_aligned, f"场景A：理论对齐（X偏移{DX_ALIGN:.1f}m）→预期 IoU高 MeanAbs小")
    results.append(r_a)

    # ── 场景 B：当前 DB 矩阵（故意偏移状态，scan 刚好在 mesh 外侧）──────
    r_b = call_c2m(m16_base, "场景B：当前DB矩阵（故意偏到角落）→预期 IoU≈0 MeanAbs≈19m")
    results.append(r_b)

    # ── 场景 C：完全分离（再额外 +200m）────────────────────────────────
    m16_far = add_translation_offset(m16_base, dx=200.0)
    r_c = call_c2m(m16_far, "场景C：完全分离（+200m）→预期 IoU=0 MeanAbs≈200+m")
    results.append(r_c)

    # ── 场景 D：一半偏移（部分重合）──────────────────────────────────────
    # 在 A（完全对齐）和 B（完全错开）之间取中间，看是否部分重合
    half_dx = DX_ALIGN / 2.0
    m16_half = add_translation_offset(m16_base, dx=half_dx)
    r_d = call_c2m(m16_half, f"场景D：半偏移（X偏移{half_dx:.1f}m）→预期 IoU中等 MeanAbs介于A/B之间")
    results.append(r_d)

    # ── 汇总 ────────────────────────────────────────────────────────────
    print(f"\n{SEPARATOR}")
    print("  汇总")
    print(SEPARATOR)
    print(f"  {'场景':<45} {'IoU':>8} {'MeanAbs':>8} {'RMSE':>8} {'P95Abs':>8}")
    print(f"  {'─'*80}")
    for r in results:
        print(f"  {r['label'][:45]:<45} {r['bboxOverlap']:>8.4f} "
              f"{r['stats']['meanAbs']:>8.3f} {r['stats']['rmse']:>8.3f} {r['stats']['p95Abs']:>8.3f}")

    print(f"\n{'─'*70}")

    # 验证核心性质：距离单调性
    mean_abs_values = [r["stats"]["meanAbs"] for r in results]
    ious  = [r["bboxOverlap"] for r in results]

    checks = []

    # 1. 理论对齐时 IoU 应 > 0
    check1 = ious[0] > 0
    checks.append(("场景A IoU > 0（对齐时有重叠）", check1, f"IoU={ious[0]:.4f}"))

    # 2. 理论对齐时 MeanAbs 应小于场景B（偏移状态）
    check2 = mean_abs_values[0] < mean_abs_values[1]
    checks.append(("场景A MeanAbs < 场景B MeanAbs（对齐时距离更小）", check2,
                   f"A={mean_abs_values[0]:.3f}m, B={mean_abs_values[1]:.3f}m"))

    # 3. 完全分离时 MeanAbs 应远大于场景 B
    check3 = mean_abs_values[2] > mean_abs_values[1] + 50.0
    checks.append(("场景C MeanAbs >> 场景B MeanAbs（更远时距离显著更大）", check3,
                   f"C={mean_abs_values[2]:.3f}m, B={mean_abs_values[1]:.3f}m"))

    # 4. 场景D（半偏移）的 MeanAbs 应介于 A 和 B 之间
    check4 = mean_abs_values[0] <= mean_abs_values[3] <= mean_abs_values[1] + 5.0
    checks.append(("场景D MeanAbs 在 A 和 B 之间（单调性）", check4,
                   f"A={mean_abs_values[0]:.3f}m, D={mean_abs_values[3]:.3f}m, B={mean_abs_values[1]:.3f}m"))

    print()
    all_pass = True
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}")
        print(f"      {detail}")
        if not ok:
            all_pass = False

    print(f"\n{SEPARATOR}")
    print(f"  总体结论: {'✓ 算法行为完全符合预期！' if all_pass else '✗ 存在不符合预期的场景！请排查'}")
    print(SEPARATOR + "\n")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
