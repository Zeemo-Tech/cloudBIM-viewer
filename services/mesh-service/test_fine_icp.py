"""精细化配准（Fine ICP）离线验证脚本

用于在正式接入 Web 接口之前，验证以下内容：
1. Remesh 顶点直接作为 Target 的可行性
2. Point-to-Plane ICP 的精调效果（fitness/rmse 改善量）
3. 全量点云处理时的实际耗时
4. 防负优化回滚机制是否正常触发

运行方式（容器内）：
  docker exec zhongjian-back-mesh-service-1 python3 /app/test_fine_icp.py

可选：传入自定义路径
  docker exec zhongjian-back-mesh-service-1 python3 /app/test_fine_icp.py \
    /storage/path/to/points.las \
    /storage/mesh_remesh/7/remeshed_1774533968.ply
"""

from __future__ import annotations

import sys
import time
import numpy as np

# ── 测试数据路径（与 test_c2m_e2e_v2.py 保持一致）─────────────────────────
LAS_PATH = "/storage/project_files/org_1/project_3/scan/深圳湾工hong_2df28f2192c5/points.las"
PLY_PATH = "/storage/mesh_remesh/7/remeshed_1774533968.ply"

# DB 中存储的手动粗配准四元数 + 平移（来自 test_c2m_e2e_v2.py 的实测值）
QX = -0.7068577930838563
QY =  0.01876327147968164
QZ =  0.01876327147968164
QW =  0.7068577930838563
TX =  105647.56089233355
TY =  54.690423974867855
TZ = -17327.27976439454

SEP = "=" * 72


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
        tx, ty, tz, 1,
    ]


def add_translation_offset(m16: list, dx=0.0, dy=0.0, dz=0.0) -> list:
    m = m16[:]
    m[12] += dx
    m[13] += dy
    m[14] += dz
    return m


def print_sep(title: str = ""):
    if title:
        print(f"\n{SEP}")
        print(f"  {title}")
        print(SEP)
    else:
        print(SEP)


def print_metrics(metrics: dict):
    print(f"  初始状态: fitness={metrics['initFitness']:.4f}  rmse={metrics['initRmse']:.4f}m")
    print(f"  精调结果: fitness={metrics['fineFitness']:.4f}  rmse={metrics['fineRmse']:.4f}m")
    print(f"  变化量:   Δ平移={metrics['deltaTranslationM']:.4f}m  Δ旋转={metrics['deltaRotationDeg']:.4f}°")
    print(f"  点云:     原始总点数={metrics['sourceTotalPoints']:,}  Target点数={metrics['targetPoints']:,}")
    print(f"  耗时:     {metrics['elapsedS']:.1f}s")


def run_test(label: str, las_path: str, ply_path: str, matrix_16: list) -> dict:
    sys.path.insert(0, "/app/algorithms")
    from icp_registration import fine_registration

    print_sep(label)
    t0 = time.time()
    result = fine_registration(las_path, ply_path, matrix_16)
    elapsed = time.time() - t0

    fallback_str = "是（已回滚至手动配准）" if result["fallback"] else "否"
    print(f"  回滚: {fallback_str}")
    print_metrics(result["metrics"])
    print(f"  最终四元数: qx={result['quaternion']['qx']:.6f}  qy={result['quaternion']['qy']:.6f}  "
          f"qz={result['quaternion']['qz']:.6f}  qw={result['quaternion']['qw']:.6f}")
    print(f"  最终平移: tx={result['translation']['tx']:.4f}  "
          f"ty={result['translation']['ty']:.4f}  tz={result['translation']['tz']:.4f}")

    return result


def main():
    las_path = sys.argv[1] if len(sys.argv) > 1 else LAS_PATH
    ply_path = sys.argv[2] if len(sys.argv) > 2 else PLY_PATH

    print_sep("精细化配准（Fine ICP）离线验证")
    print(f"  LAS 路径: {las_path}")
    print(f"  PLY 路径: {ply_path}")

    m16_base = build_column_major_matrix16(QX, QY, QZ, QW, TX, TY, TZ)

    # ── 场景 1：直接用手动粗配准矩阵作为初始值 ────────────────────────────
    r1 = run_test("场景1：手动粗配准矩阵 -> ICP 精调（正常场景）", las_path, ply_path, m16_base)

    # ── 场景 2：轻微偏移（+5cm），验证 ICP 是否能微调回来 ────────────────
    m16_slight = add_translation_offset(m16_base, dx=0.05)
    r2 = run_test("场景2：轻微偏移 +5cm，验证 ICP 微调能力", las_path, ply_path, m16_slight)

    # ── 场景 3：较大偏移（+2m），期望回滚或 ICP 改善有限 ─────────────────
    m16_large = add_translation_offset(m16_base, dx=2.0)
    r3 = run_test("场景3：较大偏移 +2m，验证防退化回滚机制", las_path, ply_path, m16_large)

    # ── 汇总 ──────────────────────────────────────────────────────────────
    print_sep("汇总")
    results = [
        ("场景1（正常）", r1),
        ("场景2（微调）", r2),
        ("场景3（大偏移）", r3),
    ]
    print(f"  {'场景':<18} {'初始RMSE':>10} {'精调RMSE':>10} {'回滚':>6} {'耗时':>8}")
    print(f"  {'─'*60}")
    for name, r in results:
        m = r["metrics"]
        print(f"  {name:<18} {m['initRmse']:>10.4f} {m['fineRmse']:>10.4f} "
              f"{'是' if r['fallback'] else '否':>6} {m['elapsedS']:>7.1f}s")

    # ── 基础验证 ──────────────────────────────────────────────────────────
    print_sep("验证")
    checks = []

    # 验证 Target 已正确加载（顶点数 > 0）
    check1 = r1["metrics"]["targetPoints"] > 0
    checks.append(("Remesh PLY 顶点成功加载为 Target", check1,
                   f"targetPoints={r1['metrics']['targetPoints']:,}"))

    # 验证场景1的精调后 RMSE 有效（不为 0）
    check2 = r1["metrics"]["fineRmse"] > 0
    checks.append(("ICP 精调正常完成（fineRmse > 0）", check2,
                   f"fineRmse={r1['metrics']['fineRmse']:.4f}"))

    # 验证四元数归一化（模长应为 1）
    q = r1["quaternion"]
    q_norm = (q["qx"]**2 + q["qy"]**2 + q["qz"]**2 + q["qw"]**2) ** 0.5
    check3 = abs(q_norm - 1.0) < 1e-6
    checks.append(("返回四元数已归一化", check3, f"|q|={q_norm:.8f}"))

    print()
    all_pass = True
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name}")
        print(f"      {detail}")
        if not ok:
            all_pass = False

    print()
    print_sep()
    print(f"  总体结论: {'✓ 算法运行正常！' if all_pass else '✗ 存在异常，请排查'}")
    print(SEP + "\n")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
