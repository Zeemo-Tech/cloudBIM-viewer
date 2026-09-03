"""C2M 算法正确性单元测试

测试逻辑：
1. 构造一个简单的合成场景（立方体 mesh + 对应点云）
2. 测试对齐情况：预期 mean 距离应接近 0
3. 测试完全分离情况（mesh 沿 X 轴平移 100m 到点云 bbox 外侧）：
   预期 mean 距离应接近平移距离（~100m）
4. 如果分离情况下仍然返回小距离，说明 C2M 计算存在 bug
"""

import numpy as np
import open3d as o3d

# 把 algorithms 目录加入路径
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "algorithms"))

from c2m_distance import (
    column_major_to_matrix4,
    apply_transform,
    compute_mesh_to_cloud_distances,
    compute_statistics,
    compute_bbox_overlap,
)

SEPARATOR = "=" * 60


def make_unit_cube_mesh() -> o3d.geometry.TriangleMesh:
    """生成一个单位立方体（边长 1m，中心在原点）。"""
    mesh = o3d.geometry.TriangleMesh.create_box(width=1.0, height=1.0, depth=1.0)
    # 将中心移到原点
    mesh.translate(np.array([-0.5, -0.5, -0.5]))
    return mesh


def make_point_cloud_in_bbox(
    center: np.ndarray,
    half_size: np.ndarray,
    n_points: int = 20000,
    seed: int = 42,
) -> o3d.geometry.PointCloud:
    """在指定 bbox 内均匀随机生成点云。"""
    rng = np.random.default_rng(seed)
    pts = rng.uniform(center - half_size, center + half_size, size=(n_points, 3)).astype(np.float32)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    return pcd


def make_identity_matrix16() -> list[float]:
    """返回 Three.js 列主序单位矩阵（16 元素）。"""
    m = np.eye(4, dtype=np.float64)
    # Three.js 列主序：按列展开
    return m.T.flatten().tolist()


def make_translation_matrix16(tx: float, ty: float, tz: float) -> list[float]:
    """返回沿 (tx, ty, tz) 平移的 Three.js 列主序矩阵（16 元素）。

    Three.js Matrix4 列主序：[col0, col1, col2, col3]
    col3 = [tx, ty, tz, 1]
    """
    return [
        1, 0, 0, 0,   # col 0
        0, 1, 0, 0,   # col 1
        0, 0, 1, 0,   # col 2
        tx, ty, tz, 1  # col 3
    ]


def run_c2m(mesh: o3d.geometry.TriangleMesh,
            scan_pcd: o3d.geometry.PointCloud,
            matrix16: list[float]) -> dict:
    """执行一次完整 C2M 计算并返回结果字典。"""
    # 复制点云（apply_transform 会修改原对象）
    pcd_copy = o3d.geometry.PointCloud()
    pcd_copy.points = o3d.utility.Vector3dVector(np.asarray(scan_pcd.points).copy())

    matrix = column_major_to_matrix4(matrix16)
    scan_bbox = apply_transform(pcd_copy, matrix)

    mesh_pts = np.asarray(mesh.vertices)
    mesh_bbox = {"min": mesh_pts.min(axis=0).tolist(), "max": mesh_pts.max(axis=0).tolist()}

    overlap = compute_bbox_overlap(
        scan_bbox["min"], scan_bbox["max"],
        mesh_bbox["min"], mesh_bbox["max"],
    )

    distances = compute_mesh_to_cloud_distances(mesh, pcd_copy)
    stats_result = compute_statistics(distances, max_hist_dist=200.0, n_bins=50)

    return {
        "stats": stats_result["stats"],
        "bboxOverlap": overlap,
        "scanBbox": scan_bbox,
        "meshBbox": mesh_bbox,
    }


def test_aligned(verbose: bool = True) -> bool:
    """测试 1：点云和 mesh 完全重合，期望 C2M 距离应非常小（< 1m）。"""
    print(SEPARATOR)
    print("测试 1：对齐场景（点云与 mesh 重合）")
    print(SEPARATOR)

    mesh = make_unit_cube_mesh()
    # 点云覆盖 mesh 同一区域：[-0.5, 0.5]^3，高密度
    scan_pcd = make_point_cloud_in_bbox(
        center=np.array([0.0, 0.0, 0.0]),
        half_size=np.array([0.5, 0.5, 0.5]),
        n_points=50000,
    )

    # 单位矩阵：不做变换
    matrix16 = make_identity_matrix16()
    result = run_c2m(mesh, scan_pcd, matrix16)

    stats = result["stats"]
    overlap = result["bboxOverlap"]

    print(f"  BBox Overlap IoU : {overlap:.4f}")
    print(f"  Mean  : {stats['mean']:.6f} m")
    print(f"  P50   : {stats['p50']:.6f} m")
    print(f"  Max   : {stats['max']:.6f} m")

    passed = stats["mean"] < 0.1 and overlap > 0.5
    print(f"\n  结论: {'✓ PASS' if passed else '✗ FAIL'} - 均值距离 {stats['mean']:.4f}m，{'符合预期（< 0.1m）' if stats['mean'] < 0.1 else '不符合预期（应 < 0.1m）'}")
    return passed


def test_completely_separated(offset_x: float = 100.0, verbose: bool = True) -> bool:
    """测试 2：mesh 完全移到点云 bbox 外侧，期望 C2M 距离接近平移量。

    实现方式：alignment matrix 将点云沿 X 轴平移 offset_x，
    使变换后的点云与 mesh（仍在原点）完全分离。
    期望 mean ≈ offset_x - 0.5（点云边缘到 mesh 的距离）。
    """
    print(SEPARATOR)
    print(f"测试 2：完全分离场景（点云 X 轴平移 {offset_x}m 到 mesh 外侧）")
    print(SEPARATOR)

    mesh = make_unit_cube_mesh()
    # 点云仍在原点附近生成（模拟原始 LAS 坐标）
    scan_pcd = make_point_cloud_in_bbox(
        center=np.array([0.0, 0.0, 0.0]),
        half_size=np.array([5.0, 5.0, 2.0]),  # 10x10x4m 点云
        n_points=50000,
    )

    # alignment matrix 将点云平移 +offset_x，使其到 x = offset_x 附近
    # 此时 mesh 在 [-0.5, 0.5]，点云在 [offset_x-5, offset_x+5]，完全分离
    matrix16 = make_translation_matrix16(offset_x, 0.0, 0.0)
    result = run_c2m(mesh, scan_pcd, matrix16)

    stats = result["stats"]
    overlap = result["bboxOverlap"]
    scan_bbox = result["scanBbox"]
    mesh_bbox = result["meshBbox"]

    print(f"  Mesh  X 范围    : [{mesh_bbox['min'][0]:.2f}, {mesh_bbox['max'][0]:.2f}]")
    print(f"  Scan  X 范围    : [{scan_bbox['min'][0]:.2f}, {scan_bbox['max'][0]:.2f}]")
    print(f"  BBox Overlap IoU : {overlap:.6f}  (期望 ≈ 0)")
    print(f"  Mean  : {stats['mean']:.4f} m")
    print(f"  P50   : {stats['p50']:.4f} m")
    print(f"  Min expected ≈  : {offset_x - 5.0 - 0.5:.2f} m  （点云近边 - mesh 远边）")
    print(f"  Max expected ≈  : {offset_x + 5.0:.2f} m  （点云远边到 mesh 的距离）")

    # 期望：mean 应远大于 offset_x - 10（点云近边到 mesh 的距离）
    expected_min_mean = offset_x - 5.0 - 0.5  # 约 94.5m
    passed = overlap < 1e-6 and stats["mean"] > expected_min_mean
    print(f"\n  结论: {'✓ PASS' if passed else '✗ FAIL'}")
    if not passed:
        if overlap >= 1e-6:
            print(f"    BBox 仍有 overlap（{overlap:.6f}），说明分离未完全——平移量需更大")
        if stats["mean"] <= expected_min_mean:
            print(f"    均值距离 {stats['mean']:.4f}m 远小于预期 {expected_min_mean:.2f}m")
            print(f"    ⚠️  BUG DETECTED: 完全分离时 C2M 仍返回了极小距离！")
    return passed


def test_separation_sensitivity():
    """测试 3：逐步增大分离距离，验证 C2M 结果单调递增。"""
    print(SEPARATOR)
    print("测试 3：分离距离单调递增验证")
    print(SEPARATOR)

    mesh = make_unit_cube_mesh()
    scan_pcd = make_point_cloud_in_bbox(
        center=np.array([0.0, 0.0, 0.0]),
        half_size=np.array([5.0, 5.0, 2.0]),
        n_points=30000,
    )

    offsets = [0.0, 5.5, 10.0, 50.0, 100.0, 500.0]
    prev_mean = -1.0
    all_monotone = True

    print(f"  {'Offset X':>12} | {'BBox IoU':>12} | {'Mean Dist':>12} | {'Monotone':>10}")
    print("  " + "-" * 56)

    for offset in offsets:
        m16 = make_translation_matrix16(offset, 0.0, 0.0)
        result = run_c2m(mesh, scan_pcd, m16)
        mean = result["stats"]["mean"]
        iou = result["bboxOverlap"]
        monotone = mean >= prev_mean - 0.01  # 允许极小浮动
        if not monotone:
            all_monotone = False
        print(f"  {offset:>12.1f} | {iou:>12.6f} | {mean:>12.4f} | {'✓' if monotone else '✗ NON-MONOTONE':>10}")
        prev_mean = mean

    print(f"\n  结论: {'✓ PASS - 距离单调递增，算法正确' if all_monotone else '✗ FAIL - 距离非单调，算法存在 bug'}")
    return all_monotone


if __name__ == "__main__":
    print("\n" + SEPARATOR)
    print("  C2M Distance Algorithm Correctness Tests")
    print(SEPARATOR + "\n")

    results = []
    results.append(("对齐场景", test_aligned()))
    print()
    results.append(("完全分离场景（100m）", test_completely_separated(offset_x=100.0)))
    print()
    results.append(("单调递增验证", test_separation_sensitivity()))

    print("\n" + SEPARATOR)
    print("  测试汇总")
    print(SEPARATOR)
    all_pass = True
    for name, passed in results:
        print(f"  {'✓ PASS' if passed else '✗ FAIL'} - {name}")
        if not passed:
            all_pass = False

    print(SEPARATOR)
    print(f"  总体结论: {'✓ 算法正确' if all_pass else '✗ 算法存在问题，请排查'}")
    print(SEPARATOR + "\n")
    sys.exit(0 if all_pass else 1)
