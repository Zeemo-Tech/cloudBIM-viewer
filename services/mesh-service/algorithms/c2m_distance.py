"""Scan vs BIM 偏差计算模块。

计算 BIM 网格每个顶点到最近扫描点的有符号距离，将偏差以发散色图赋色到网格表面。
输出是带顶点颜色的三角网格 PLY，可在前端用 MeshBasicMaterial(vertexColors) 渲染。

有符号距离约定
--------------
对 mesh 上每个顶点 V（外法线为 N），取最近 scan 点 P：
  vec = P - V
  sign = sign(dot(N, vec))
  signed_dist = sign * ||vec||

- 正值：scan 在法线朝向一侧（BIM 表面偏内，实际墙面向外凸出，存在空隙/测量超标）
- 负值：scan 在法线背侧（BIM 表面偏外，实际墙面向内凹入，存在混凝土不足）
- 颜色：蓝→青（负偏差）→绿（零偏差）→黄→红（正偏差）
- 超出配色半宽的顶点统一使用暗灰 `#3a3a3a`

对外 compute/recolor 契约统一使用 raw per-vertex distances 进行统计、
直方图和着色，以保证 PLY、distances.bin 与容差比例逐顶点一致。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import laspy
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

# 工程云图色契约，与前端 src/utils/c2mColormap.ts 的五个 stop 一致。
_C2M_COLOR_STOPS = np.array([
    [0x0D, 0x47, 0xA1],  # 蓝（强负偏差）
    [0x00, 0xBC, 0xD4],  # 青（负偏差容差边界）
    [0x00, 0xC8, 0x53],  # 绿（零偏差）
    [0xFF, 0xD6, 0x00],  # 黄（正偏差容差边界）
    [0xD5, 0x00, 0x00],  # 红（强正偏差）
], dtype=np.float64) / 255.0
_C2M_OUT_OF_RANGE_COLOR = np.array([0x3A, 0x3A, 0x3A], dtype=np.float64) / 255.0

logger = logging.getLogger(__name__)

CHUNKED_READ_THRESHOLD = 5_000_000
CHUNK_SIZE = 1_000_000


def load_and_downsample_las(
    path: str, voxel_size: float
) -> tuple[o3d.geometry.PointCloud, int, dict[str, Any]]:
    """读取 LAS 并体素降采样，控制峰值内存。

    < CHUNKED_READ_THRESHOLD 点直接全量读取；否则分块读取每块独立降采样后合并。
    返回 (降采样后 PointCloud, 原始总点数, scanBboxRaw)。
    """
    t0 = time.time()
    with laspy.open(path) as reader:
        total_points = reader.header.point_count

    bbox_raw: dict[str, Any] = {}

    if total_points < CHUNKED_READ_THRESHOLD:
        las = laspy.read(path)
        xyz = np.vstack([las.x, las.y, las.z]).T.astype(np.float32)
        bbox_raw = _bbox_dict(xyz)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(xyz)
        del las, xyz
        pcd = pcd.voxel_down_sample(voxel_size)
    else:
        logger.info("LAS 点数 %d 超过阈值，启用分块读取 + 即时降采样", total_points)
        chunks: list[np.ndarray] = []
        global_min = np.full(3, np.inf, dtype=np.float64)
        global_max = np.full(3, -np.inf, dtype=np.float64)
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(CHUNK_SIZE):
                xyz_chunk = np.vstack([chunk.x, chunk.y, chunk.z]).T.astype(np.float32)
                global_min = np.minimum(global_min, xyz_chunk.min(axis=0))
                global_max = np.maximum(global_max, xyz_chunk.max(axis=0))
                pcd_chunk = o3d.geometry.PointCloud()
                pcd_chunk.points = o3d.utility.Vector3dVector(xyz_chunk)
                pcd_chunk = pcd_chunk.voxel_down_sample(voxel_size)
                chunks.append(np.asarray(pcd_chunk.points, dtype=np.float32))
                del xyz_chunk, pcd_chunk
        bbox_raw = {"min": global_min.tolist(), "max": global_max.tolist()}
        merged = np.concatenate(chunks, axis=0)
        del chunks
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(merged)
        del merged
        pcd = pcd.voxel_down_sample(voxel_size)

    logger.info(
        "LAS 加载完成: %d -> %d 点, 耗时 %.1fs",
        total_points, len(pcd.points), time.time() - t0,
    )
    return pcd, total_points, bbox_raw


def column_major_to_matrix4(matrix_16: list[float]) -> np.ndarray:
    """将列主序 16 浮点数组转为 4x4 numpy 矩阵。

    Three.js Matrix4 存储为列主序：[col0_row0, col0_row1, col0_row2, col0_row3,
    col1_row0, ...] ，即 np.array(m16).reshape(4,4) 得到列主序矩阵，
    转置后得到标准行主序。
    """
    arr = np.array(matrix_16, dtype=np.float64).reshape(4, 4)
    return arr.T


def apply_transform(pcd: o3d.geometry.PointCloud, matrix: np.ndarray) -> dict[str, Any]:
    """对点云施加 4x4 变换矩阵，返回变换后的 bbox 信息。"""
    pcd.transform(matrix)
    pts = np.asarray(pcd.points)
    return _bbox_dict(pts)


def compute_signed_mesh_to_cloud_distances(
    mesh: o3d.geometry.TriangleMesh,
    scan_pcd: o3d.geometry.PointCloud,
    knn_k: int = 1,
    normal_constraint_enabled: bool = False,
    normal_half_space_only: bool = True,
    normal_max_angle_deg: float = 75.0,
    normal_fallback_mode: str = "nearest",
) -> np.ndarray:
    """计算 mesh 每个顶点到最近 scan 点的有符号距离。

    符号约定（见模块文档）：
      - 正值：scan 在顶点外法线一侧（BIM 表面偏内，存在空隙）
      - 负值：scan 在顶点外法线背侧（BIM 表面偏外，存在混凝土不足）

    参数
    ----
    knn_k : int
        每个顶点取 k 个候选最近邻，默认 1（退化为经典 1-NN）。
        启用法向约束时建议 8~16。
    normal_constraint_enabled : bool
        是否启用法向方向筛选。False 时退化为原始 1-NN 行为。
    normal_half_space_only : bool
        True：仅保留 dot(N, vec) >= 0 的候选点（前半空间）。
    normal_max_angle_deg : float
        法向量与候选点方向向量的夹角上限（度）。0~90，越小越严格。
        默认 75°，约等于前半空间内允许 75° 偏角。
    normal_fallback_mode : str
        候选点全被筛掉时的回退策略：
        - "nearest"：直接用 1-NN 欧氏距离（保留符号判断，不丢弃数据）。

    若网格无顶点法线，自动调用 compute_vertex_normals() 计算。
    结果为 float32 per-vertex 有符号距离数组，与 mesh.vertices 顺序一致。
    """
    t0 = time.time()

    if not mesh.has_vertex_normals():
        mesh.compute_vertex_normals()

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    scan_pts = np.asarray(scan_pcd.points, dtype=np.float64)
    n_vertices = len(vertices)

    tree = cKDTree(scan_pts)

    if not normal_constraint_enabled or knn_k <= 1:
        # 原始 1-NN 路径，行为完全不变
        distances, indices = tree.query(vertices, workers=-1)
        nearest_pts = scan_pts[indices]
        vecs = nearest_pts - vertices
        dot_products = np.einsum("ij,ij->i", normals, vecs)
        signs = np.where(dot_products >= 0, 1.0, -1.0)
        signed_distances = (signs * distances).astype(np.float32)
    else:
        # kNN + 法向筛选路径
        t_knn = time.time()
        k = max(1, int(knn_k))
        # query 返回 shape (n_vertices, k)；k=1 时退化到 1-D 数组，统一处理
        knn_dists, knn_indices = tree.query(vertices, k=k, workers=-1)
        if k == 1:
            knn_dists = knn_dists[:, np.newaxis]
            knn_indices = knn_indices[:, np.newaxis]
        logger.info(
            "[法向约束] kNN 查询完成: k=%d, 耗时 %.2fs",
            k, time.time() - t_knn,
        )

        # 余弦阈值：夹角上限对应的 cos 值（夹角 < max_angle → cos > cos_thresh）
        cos_thresh = np.cos(np.deg2rad(float(normal_max_angle_deg)))

        t_filter = time.time()
        # 候选点坐标: (n_vertices, k, 3)
        cand_pts = scan_pts[knn_indices]
        # 方向向量: (n_vertices, k, 3)
        vecs_k = cand_pts - vertices[:, np.newaxis, :]
        # 模长: (n_vertices, k)
        norms_k = np.linalg.norm(vecs_k, axis=2)

        # 法线方向点积: (n_vertices, k)
        # normals: (n_vertices, 3) → 广播到 (n_vertices, k)
        dot_k = np.einsum("ij,ikj->ik", normals, vecs_k)

        # 有效掩码（全 True 开始，按条件叠加）
        valid_mask = np.ones((n_vertices, k), dtype=bool)

        if normal_half_space_only:
            valid_mask &= (dot_k >= 0)

        # 夹角约束：cos(angle) = dot / (|N||vec|)，|N|=1 经法向归一化后
        # 避免零长度向量（扫描点与顶点完全重合）导致除零
        safe_norms = np.where(norms_k > 1e-12, norms_k, 1e-12)
        cos_angle = dot_k / safe_norms  # |N|=1，无需再除法线模
        # 仅对 dot>=0 的点检查夹角（背侧点无意义）
        valid_mask &= (cos_angle >= cos_thresh)

        logger.info(
            "[法向约束] 筛选完成: 有效候选比例 %.1f%%, 耗时 %.2fs",
            float(valid_mask.any(axis=1).mean() * 100),
            time.time() - t_filter,
        )

        t_select = time.time()
        # 对每个顶点从有效候选中选距离最小者；全筛掉则回退
        signed_distances = np.empty(n_vertices, dtype=np.float32)

        # 向量化批量处理：将无效候选的距离设为 inf，然后 argmin
        masked_dists = np.where(valid_mask, knn_dists, np.inf)
        best_idx_in_k = np.argmin(masked_dists, axis=1)
        best_dist = masked_dists[np.arange(n_vertices), best_idx_in_k]

        # 找出全部被筛掉的顶点（回退）
        fallback_mask = ~np.isfinite(best_dist)
        fallback_count = int(fallback_mask.sum())

        # 正常顶点：从筛选后最优候选获取距离与符号
        normal_mask = ~fallback_mask
        if normal_mask.any():
            best_global_idx = knn_indices[np.arange(n_vertices), best_idx_in_k]
            best_pts = scan_pts[best_global_idx]
            best_vecs = best_pts - vertices
            best_dots = np.einsum("ij,ij->i", normals, best_vecs)
            signs = np.where(best_dots >= 0, 1.0, -1.0)
            # 修正距离（knn_dists 里存的是欧氏距离，与 best_dist 一致）
            actual_dists = knn_dists[np.arange(n_vertices), best_idx_in_k]
            signed_distances[normal_mask] = (signs[normal_mask] * actual_dists[normal_mask]).astype(np.float32)

        # 回退顶点：直接用 1-NN（knn_indices 第 0 列）
        if fallback_count > 0:
            if normal_fallback_mode == "nearest":
                fb_nearest_dist = knn_dists[fallback_mask, 0]
                fb_nearest_pts = scan_pts[knn_indices[fallback_mask, 0]]
                fb_vecs = fb_nearest_pts - vertices[fallback_mask]
                fb_dots = np.einsum("ij,ij->i", normals[fallback_mask], fb_vecs)
                fb_signs = np.where(fb_dots >= 0, 1.0, -1.0)
                signed_distances[fallback_mask] = (fb_signs * fb_nearest_dist).astype(np.float32)
            else:
                # 未知回退策略，同 nearest
                fb_nearest_dist = knn_dists[fallback_mask, 0]
                fb_nearest_pts = scan_pts[knn_indices[fallback_mask, 0]]
                fb_vecs = fb_nearest_pts - vertices[fallback_mask]
                fb_dots = np.einsum("ij,ij->i", normals[fallback_mask], fb_vecs)
                fb_signs = np.where(fb_dots >= 0, 1.0, -1.0)
                signed_distances[fallback_mask] = (fb_signs * fb_nearest_dist).astype(np.float32)

        logger.info(
            "[法向约束] 选点完成: 回退顶点数=%d (%.1f%%), 耗时 %.2fs",
            fallback_count,
            fallback_count / max(n_vertices, 1) * 100,
            time.time() - t_select,
        )

    logger.info(
        "Mesh-to-Cloud 有符号距离计算完成: %d 顶点, min=%.4f, max=%.4f, 耗时 %.1fs",
        len(signed_distances),
        float(signed_distances.min()),
        float(signed_distances.max()),
        time.time() - t0,
    )
    return signed_distances


def compute_signed_scan_to_mesh_distances(
    mesh: o3d.geometry.TriangleMesh,
    scan_points: np.ndarray,
    chunk_size: int = 250_000,
) -> np.ndarray:
    """Compute signed scan-point-to-triangle distances in bounded chunks.

    This is the geometry core intended for a future ``reference`` profile. It
    deliberately has no file I/O or downsampling. Inputs and returned
    distances stay float64. RaycastingScene uses float32 tensors internally,
    so coordinates are rebased around the mesh before each query to preserve
    precision for large projected-coordinate values.

    The sign comes from the closest triangle primitive normal: points in the
    normal-facing half-space are positive and points behind it are negative.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    triangles = np.asarray(mesh.triangles, dtype=np.int64)
    points = np.asarray(scan_points, dtype=np.float64)

    if vertices.ndim != 2 or vertices.shape[1:] != (3,):
        raise ValueError("mesh must contain Nx3 vertices")
    if triangles.ndim != 2 or triangles.shape[1:] != (3,) or len(triangles) == 0:
        raise ValueError("mesh must contain triangle faces")
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("scan_points must have shape (N, 3)")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if len(points) == 0:
        return np.empty(0, dtype=np.float64)

    local_origin = (vertices.min(axis=0) + vertices.max(axis=0)) * 0.5
    local_vertices = vertices - local_origin
    triangle_vertices = local_vertices[triangles]
    primitive_normals = np.cross(
        triangle_vertices[:, 1] - triangle_vertices[:, 0],
        triangle_vertices[:, 2] - triangle_vertices[:, 0],
    )
    normal_lengths = np.linalg.norm(primitive_normals, axis=1)
    if np.any(normal_lengths <= 1e-15):
        raise ValueError("mesh contains degenerate triangle faces")
    primitive_normals /= normal_lengths[:, np.newaxis]

    tensor_mesh = o3d.t.geometry.TriangleMesh(
        o3d.core.Tensor(local_vertices.astype(np.float32), dtype=o3d.core.Dtype.Float32),
        o3d.core.Tensor(triangles.astype(np.int32), dtype=o3d.core.Dtype.Int32),
    )
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(tensor_mesh)

    signed_distances = np.empty(len(points), dtype=np.float64)
    for start in range(0, len(points), chunk_size):
        end = min(start + chunk_size, len(points))
        local_points = points[start:end] - local_origin
        query = o3d.core.Tensor(
            local_points.astype(np.float32),
            dtype=o3d.core.Dtype.Float32,
        )
        closest = scene.compute_closest_points(query)
        closest_points = np.asarray(closest["points"].numpy(), dtype=np.float64)
        primitive_ids = np.asarray(closest["primitive_ids"].numpy(), dtype=np.int64).reshape(-1)
        offsets = local_points - closest_points
        unsigned = np.linalg.norm(offsets, axis=1)
        dots = np.einsum("ij,ij->i", primitive_normals[primitive_ids], offsets)
        signs = np.where(dots >= 0.0, 1.0, -1.0)
        signed_distances[start:end] = signs * unsigned

    return signed_distances


def colorize_mesh_by_signed_distance(
    mesh: o3d.geometry.TriangleMesh,
    signed_distances: np.ndarray,
    max_colormap_distance: float,
    tolerance_limit: float = 0.05,
) -> o3d.geometry.TriangleMesh:
    """用工程云图色契约按 raw 有符号距离给 mesh 顶点着色。

    双参数分段映射（与前端 `c2mColormap.ts` 保持一致）：
      -max_colormap_distance → t=0   蓝（强负偏差）
      -tolerance_limit       → t=0.25 青（负偏差容差边界）
      0                      → t=0.5  绿（零偏差）
      +tolerance_limit       → t=0.75 黄（正偏差容差边界）
      +max_colormap_distance → t=1   红（强正偏差）
      超出 ±max_colormap_distance → 暗灰 #3a3a3a

    当 tolerance_limit == max_colormap_distance 时，远端区间自然退化为空，
    ±limit 分别使用青/黄容差边界色，不在函数内暗中改写容差。
    """
    cap = float(max_colormap_distance)
    tol = float(tolerance_limit)
    if not np.isfinite(cap) or cap <= 0:
        raise ValueError("max_colormap_distance must be finite and greater than zero")
    if not np.isfinite(tol) or tol <= 0 or tol > cap:
        raise ValueError("tolerance_limit must be finite, positive, and no greater than the color range")

    d = np.asarray(signed_distances, dtype=np.float64)
    vertex_count = len(np.asarray(mesh.vertices))
    if d.ndim != 1 or len(d) != vertex_count:
        raise ValueError(
            f"signed_distances length {d.size} does not match mesh vertex count {vertex_count}"
        )
    if not np.all(np.isfinite(d)):
        raise ValueError("signed_distances must contain only finite values")

    # 分段线性映射到 t ∈ [0, 1]
    t = np.full_like(d, 0.5)
    out_of_range = np.abs(d) > cap

    # 容差内区间始终存在，包含端点以保证 T == C 时仍定义良好。
    neg_near = (d >= -tol) & (d <= 0)
    pos_near = (d > 0) & (d <= tol)
    t[neg_near] = 0.25 + 0.25 * (d[neg_near] + tol) / tol
    t[pos_near] = 0.5 + 0.25 * d[pos_near] / tol

    if cap > tol:
        neg_far = (d >= -cap) & (d < -tol)
        pos_far = (d > tol) & (d <= cap)
        t[neg_far] = 0.25 * (d[neg_far] + cap) / (cap - tol)
        t[pos_far] = 0.75 + 0.25 * (d[pos_far] - tol) / (cap - tol)

    # 在相邻五色 stop 的 sRGB 数值间插值；Three.js 动态着色会先做同样
    # 的插值，再把结果转换到线性色彩空间供渲染器使用。
    scaled = np.clip(t, 0.0, 1.0) * (_C2M_COLOR_STOPS.shape[0] - 1)
    lower = np.minimum(np.floor(scaled).astype(np.int64), _C2M_COLOR_STOPS.shape[0] - 2)
    fraction = scaled - lower
    colors = (
        _C2M_COLOR_STOPS[lower] * (1.0 - fraction[:, np.newaxis])
        + _C2M_COLOR_STOPS[lower + 1] * fraction[:, np.newaxis]
    )

    # 超出色温范围覆盖为暗灰 #3a3a3a，与色带形成强区隔
    colors[out_of_range] = _C2M_OUT_OF_RANGE_COLOR

    mesh.vertex_colors = o3d.utility.Vector3dVector(colors)
    return mesh


def compute_statistics(
    distances: np.ndarray,
    max_hist_dist: float,
    n_bins: int,
    tolerance: float = 0.05,
) -> dict[str, Any]:
    """计算有符号距离的统计量和对称直方图。

    参数 max_hist_dist 表示直方图半宽，区间为
    [-max_hist_dist, +max_hist_dist]（n_bins 个桶）。
    """
    distances = np.asarray(distances, dtype=np.float64)
    if distances.ndim != 1 or distances.size == 0:
        raise ValueError("distances must be a non-empty one-dimensional array")
    if not np.all(np.isfinite(distances)):
        raise ValueError("distances must contain only finite values")
    r = float(max_hist_dist)
    if not np.isfinite(r) or r <= 0:
        raise ValueError("max_hist_dist must be finite and greater than zero")
    if isinstance(n_bins, bool) or int(n_bins) != n_bins or int(n_bins) <= 0:
        raise ValueError("n_bins must be a positive integer")
    n_bins = int(n_bins)
    tolerance = float(tolerance)
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and non-negative")
    absolute_distances = np.abs(distances)
    stats = {
        "min":  float(np.min(distances)),
        "max":  float(np.max(distances)),
        "mean": float(np.mean(distances)),
        "std":  float(np.std(distances)),
        "p50":  float(np.percentile(distances, 50)),
        "p90":  float(np.percentile(distances, 90)),
        "p95":  float(np.percentile(distances, 95)),
        "p99":  float(np.percentile(distances, 99)),
        "meanAbs": float(np.mean(absolute_distances)),
        "rmse": float(np.sqrt(np.mean(np.square(distances)))),
        "p95Abs": float(np.percentile(absolute_distances, 95)),
        "withinToleranceRatio": float(np.mean(absolute_distances <= tolerance)),
    }
    # 对称区间直方图：负值在左半段，正值在右半段
    counts, bin_edges = np.histogram(
        distances, bins=n_bins, range=(-r, r)
    )
    histogram = {
        "binEdges": bin_edges.tolist(),
        "counts": counts.tolist(),
        "overflowCount": int(np.count_nonzero((distances < -r) | (distances > r))),
    }
    return {"stats": stats, "histogram": histogram}


def smooth_vertex_distances(
    mesh: o3d.geometry.TriangleMesh,
    distances: np.ndarray,
    iterations: int = 0,
    strength: float = 0.5,
) -> np.ndarray:
    """对逐顶点有符号距离做 Laplacian 图平滑，消除因网格过密导致的高频噪声色斑。

    每轮迭代：
        d_new[v] = (1 - strength) * d[v] + strength * mean(d[邻居(v)])

    使用稀疏矩阵向量化，大网格效率高。平滑结果仅用于可视化着色；
    原始距离应在调用此函数前单独保存，供批注点选精确插值使用。

    参数
    ----
    iterations : 迭代次数，越多越平滑；默认 0（对外 API 使用 raw 契约）。
    strength   : 每轮向邻居均值靠拢的权重 [0, 1]；越大越激进，默认 0.5。
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse import diags as sp_diags

    triangles = np.asarray(mesh.triangles, dtype=np.int32)
    n = len(np.asarray(mesh.vertices))

    if n == 0 or len(triangles) == 0 or iterations <= 0:
        return distances.copy()

    t0 = time.time()

    # 向量化构建无向边（每条边两个方向各出现一次，共 6 × n_faces 条有向边）
    src = np.concatenate([
        triangles[:, 0], triangles[:, 1], triangles[:, 2],
        triangles[:, 1], triangles[:, 2], triangles[:, 0],
    ])
    dst = np.concatenate([
        triangles[:, 1], triangles[:, 2], triangles[:, 0],
        triangles[:, 0], triangles[:, 1], triangles[:, 2],
    ])
    ones = np.ones(len(src), dtype=np.float32)
    # 重复边自动累加，归一化后效果等同于只计一次
    adj = csr_matrix((ones, (src, dst)), shape=(n, n))

    # 行归一化：得到"均值算子" L，L @ d = 每个顶点邻居的距离均值
    degree = np.asarray(adj.sum(axis=1)).flatten()
    degree[degree == 0] = 1.0  # 孤立顶点不受影响
    inv_deg = sp_diags(1.0 / degree)
    L = (inv_deg @ adj).astype(np.float32)

    smoothed = distances.copy().astype(np.float32)
    w = float(np.clip(strength, 0.0, 1.0))
    for _ in range(iterations):
        smoothed = (1.0 - w) * smoothed + w * (L @ smoothed)

    logger.info(
        "距离 Laplacian 平滑完成: %d 顶点, iterations=%d strength=%.2f, 耗时 %.1fs",
        n, iterations, strength, time.time() - t0,
    )
    return smoothed


def compute_bbox_overlap(
    a_min: list[float], a_max: list[float],
    b_min: list[float], b_max: list[float],
) -> float:
    """计算两个 AABB 的 IoU。"""
    a_min_arr, a_max_arr = np.array(a_min), np.array(a_max)
    b_min_arr, b_max_arr = np.array(b_min), np.array(b_max)

    inter_min = np.maximum(a_min_arr, b_min_arr)
    inter_max = np.minimum(a_max_arr, b_max_arr)
    inter_size = np.maximum(inter_max - inter_min, 0)
    inter_vol = float(np.prod(inter_size))

    vol_a = float(np.prod(np.maximum(a_max_arr - a_min_arr, 0)))
    vol_b = float(np.prod(np.maximum(b_max_arr - b_min_arr, 0)))
    union_vol = vol_a + vol_b - inter_vol

    if union_vol < 1e-12:
        return 0.0
    return inter_vol / union_vol


def _bbox_dict(pts: np.ndarray) -> dict[str, Any]:
    """从点数组计算 bbox 字典。"""
    return {
        "min": pts.min(axis=0).tolist(),
        "max": pts.max(axis=0).tolist(),
    }
