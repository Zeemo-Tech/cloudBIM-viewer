"""精细化配准模块：利用 Remesh 产物进行点到面 ICP (Point-to-Plane ICP) 精调。

工作流程：
1. 从 remeshed PLY 直接提取顶点和法线，构建 Target 点云（零成本，无需采样）
2. 加载原始点云 LAS，全量不降采样
3. 以手动粗配准矩阵为初始值，执行 Point-to-Plane ICP
4. 对比初始与精调后的 Fitness / RMSE，判断是否触发负优化告警
5. 根据开关决定是否应用精调矩阵，并返回告警标记供前端提示
6. 将最终矩阵分解为四元数 + 平移，以及列主序 16 浮点，方便 Go 端直接存库和前端使用
"""

from __future__ import annotations

import logging
import time
from typing import Any

import laspy
import numpy as np
import open3d as o3d

logger = logging.getLogger(__name__)

# ICP 最大匹配距离默认值（米）
DEFAULT_MAX_CORR_DIST: float = 0.3

# 防负优化阈值默认值：精调后 RMSE 超出初始值该比例则判定退化
RMSE_REGRESS_RATIO: float = 1.05
# 防负优化阈值默认值：精调后 Fitness 低于初始值该比例则判定退化
FITNESS_REGRESS_RATIO: float = 0.95


def _load_las_as_pcd(las_path: str) -> tuple[o3d.geometry.PointCloud, int]:
    """读取 LAS 文件，全量加载，不降采样。

    返回 (PointCloud, 原始总点数)。
    """
    t0 = time.time()
    with laspy.open(las_path) as reader:
        total_points = reader.header.point_count

    las = laspy.read(las_path)
    xyz = np.vstack([las.x, las.y, las.z]).T.astype(np.float64)
    del las

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz)
    del xyz

    logger.info("LAS 全量加载: %d 点, 耗时 %.1fs", total_points, time.time() - t0)
    return pcd, total_points


def _load_remesh_as_pcd(ply_path: str) -> o3d.geometry.PointCloud:
    """读取 Remesh PLY，直接提取顶点和法线作为 Target 点云。

    因为 Remesh 流水线已完成等距细分 + 法线重算，顶点天然均匀分布
    且携带高质量法线，无需额外采样，直接复用即可。
    """
    t0 = time.time()
    mesh = o3d.io.read_triangle_mesh(ply_path)

    if not mesh.has_vertex_normals():
        mesh.compute_vertex_normals()

    target = o3d.geometry.PointCloud()
    target.points = mesh.vertices
    target.normals = mesh.vertex_normals

    n = len(target.points)
    logger.info("Remesh PLY 加载完成: %d 顶点作为 Target, 耗时 %.1fs", n, time.time() - t0)
    return target


def _column_major_to_matrix4(matrix_16: list[float]) -> np.ndarray:
    """列主序 16 浮点 -> 4x4 行主序 numpy 矩阵（与 c2m_distance.py 保持一致）。

    Three.js Matrix4 存储为列主序：[col0_row0, col0_row1, col0_row2, col0_row3, col1_row0, ...]
    reshape(4,4) 得到列主序矩阵，转置后得到标准行主序。
    """
    return np.array(matrix_16, dtype=np.float64).reshape(4, 4).T


def _matrix4_to_column_major(mat: np.ndarray) -> list[float]:
    """4x4 行主序 numpy 矩阵 -> 列主序 16 浮点（Three.js Matrix4 格式）。"""
    return mat.T.flatten().tolist()


def _matrix4_to_quat_translation(mat: np.ndarray) -> dict[str, float]:
    """从 4x4 变换矩阵提取四元数和平移向量。

    使用 Shepperd 方法将旋转矩阵转为四元数，数值稳定性好。
    返回 {"qx","qy","qz","qw","tx","ty","tz"}。
    """
    R = mat[:3, :3]
    t = mat[:3, 3]

    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        qw = 0.25 / s
        qx = (R[2, 1] - R[1, 2]) * s
        qy = (R[0, 2] - R[2, 0]) * s
        qz = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        qw = (R[2, 1] - R[1, 2]) / s
        qx = 0.25 * s
        qy = (R[0, 1] + R[1, 0]) / s
        qz = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        qw = (R[0, 2] - R[2, 0]) / s
        qx = (R[0, 1] + R[1, 0]) / s
        qy = 0.25 * s
        qz = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        qw = (R[1, 0] - R[0, 1]) / s
        qx = (R[0, 2] + R[2, 0]) / s
        qy = (R[1, 2] + R[2, 1]) / s
        qz = 0.25 * s

    norm = np.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    return {
        "qx": float(qx / norm),
        "qy": float(qy / norm),
        "qz": float(qz / norm),
        "qw": float(qw / norm),
        "tx": float(t[0]),
        "ty": float(t[1]),
        "tz": float(t[2]),
    }


def _calc_transform_delta(mat_init: np.ndarray, mat_fine: np.ndarray) -> tuple[float, float]:
    """计算两个变换矩阵之间的平移变化量（米）和旋转变化量（度）。"""
    delta_t = float(np.linalg.norm(mat_fine[:3, 3] - mat_init[:3, 3]))

    R_delta = mat_fine[:3, :3] @ mat_init[:3, :3].T
    cos_angle = np.clip((np.trace(R_delta) - 1.0) / 2.0, -1.0, 1.0)
    delta_r_deg = float(np.degrees(np.arccos(cos_angle)))

    return delta_t, delta_r_deg


def fine_registration(
    scan_path: str,
    remesh_ply_path: str,
    init_matrix_16: list[float],
    max_correspondence_distance: float = DEFAULT_MAX_CORR_DIST,
    rmse_regress_ratio: float = RMSE_REGRESS_RATIO,
    fitness_regress_ratio: float = FITNESS_REGRESS_RATIO,
    apply_when_regressed: bool = False,
) -> dict[str, Any]:
    """执行精细化配准，返回最终应用矩阵及完整评估指标。

    参数
    ----
    scan_path : str
        原始点云 LAS 文件路径（全量加载，不降采样）。
    remesh_ply_path : str
        Remesh 产物 PLY 路径，其顶点将直接作为 Target 点云使用。
    init_matrix_16 : list[float]
        列主序 4x4 配准矩阵（16 个浮点数），来自前端手动粗配准结果。
    max_correspondence_distance : float
        ICP 最大匹配距离（米），默认 0.3m。基于已有粗配准，无需设置过大。
    rmse_regress_ratio : float
        RMSE 退化阈值比例，默认 1.05。
    fitness_regress_ratio : float
        Fitness 退化阈值比例，默认 0.95。
    apply_when_regressed : bool
        触发负优化告警时，是否仍应用精调矩阵。

    返回
    ----
    {
        "transform": [16 floats, 列主序, Three.js Matrix4 兼容],
        "quaternion": {"qx", "qy", "qz", "qw"},
        "translation": {"tx", "ty", "tz"},
        "regressed": bool,          // 是否触发负优化告警
        "appliedFineResult": bool,  // 是否应用精调结果
        "fallback": bool,           // 兼容字段：regressed 且未应用精调
        "metrics": {
            "initFitness": float,      // 初始配准重合度 [0,1]
            "initRmse": float,         // 初始配准均方根误差（米）
            "fineFitness": float,      // ICP 后重合度
            "fineRmse": float,         // ICP 后均方根误差（米）
            "deltaTranslationM": float,// 精调引起的平移变化量（米）
            "deltaRotationDeg": float, // 精调引起的旋转变化量（度）
            "elapsedS": float,         // 总耗时（秒）
            "sourceTotalPoints": int,  // 原始点云总点数
            "targetPoints": int,       // Target（remesh 顶点）点数
        }
    }
    """
    t_start = time.time()

    # 1. 构建 Target：从 remesh PLY 提取顶点 + 法线
    target_pcd = _load_remesh_as_pcd(remesh_ply_path)

    # 2. 加载 Source：原始点云（全量，不降采样）
    source_pcd, total_points = _load_las_as_pcd(scan_path)

    # 3. 解析初始变换矩阵
    init_mat = _column_major_to_matrix4(init_matrix_16)

    # 4. 评估初始配准质量（将 source 施加 init_mat 后与 target 计算 fitness/rmse）
    #    注意：evaluate_registration 不修改 source，只计算统计量
    eval_init = o3d.pipelines.registration.evaluate_registration(
        source_pcd, target_pcd, max_correspondence_distance, init_mat
    )
    logger.info(
        "[ICP] 初始状态: fitness=%.4f rmse=%.4fm",
        eval_init.fitness,
        eval_init.inlier_rmse,
    )

    # 5. 执行 Point-to-Plane ICP（以 init_mat 为起始点）
    #    Point-to-Plane 利用 Target 法线约束，适合 BIM 大平面场景，收敛快且不易滑动
    icp_result = o3d.pipelines.registration.registration_icp(
        source_pcd,
        target_pcd,
        max_correspondence_distance,
        init_mat,
        o3d.pipelines.registration.TransformationEstimationPointToPlane(),
    )
    fine_mat = icp_result.transformation
    logger.info(
        "[ICP] 精调结果: fitness=%.4f rmse=%.4fm",
        icp_result.fitness,
        icp_result.inlier_rmse,
    )

    # 6. 防负优化裁判（仅告警，不直接决定最终矩阵）
    regressed = False
    init_fitness = float(eval_init.fitness)
    init_rmse = float(eval_init.inlier_rmse)
    fine_fitness = float(icp_result.fitness)
    fine_rmse = float(icp_result.inlier_rmse)

    # 仅当初始状态已有有效对应点时才进行质量对比（避免 rmse=0 时误判）
    if init_rmse > 1e-9:
        if fine_rmse > init_rmse * rmse_regress_ratio:
            logger.warning(
                "[ICP] RMSE 退化 (%.4f -> %.4f)，触发告警（阈值 %.3f）",
                init_rmse,
                fine_rmse,
                rmse_regress_ratio,
            )
            regressed = True
        if fine_fitness < init_fitness * fitness_regress_ratio:
            logger.warning(
                "[ICP] Fitness 退化 (%.4f -> %.4f)，触发告警（阈值 %.3f）",
                init_fitness,
                fine_fitness,
                fitness_regress_ratio,
            )
            regressed = True

    applied_fine = (not regressed) or apply_when_regressed
    fallback = regressed and not applied_fine
    final_mat = fine_mat if applied_fine else init_mat
    delta_t, delta_r = _calc_transform_delta(init_mat, fine_mat)

    elapsed = round(time.time() - t_start, 2)
    pose = _matrix4_to_quat_translation(final_mat)

    return {
        "transform": _matrix4_to_column_major(final_mat),
        "quaternion": {
            "qx": pose["qx"],
            "qy": pose["qy"],
            "qz": pose["qz"],
            "qw": pose["qw"],
        },
        "translation": {
            "tx": pose["tx"],
            "ty": pose["ty"],
            "tz": pose["tz"],
        },
        "regressed": regressed,
        "appliedFineResult": applied_fine,
        "fallback": fallback,
        "metrics": {
            "initFitness": init_fitness,
            "initRmse": init_rmse,
            "fineFitness": fine_fitness,
            "fineRmse": fine_rmse,
            "deltaTranslationM": delta_t,
            "deltaRotationDeg": delta_r,
            "elapsedS": elapsed,
            "sourceTotalPoints": total_points,
            "targetPoints": len(target_pcd.points),
        },
    }
