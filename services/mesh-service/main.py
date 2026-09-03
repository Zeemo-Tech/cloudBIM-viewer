"""网格处理微服务。

提供 REST API：
- /remesh：接收网格文件，执行均匀化/简化后返回处理结果
- /c2m/compute：Cloud-to-Mesh Distance 计算

离线诊断（容器内，原始网格与 remesh 产物对比法向统计）：
  docker compose run --rm mesh-service \\
    python3 compare_remesh_normals.py /storage/原始.glb /storage/remeshed.ply
  宿主机文件放在项目 ./storage 下；可选 --batch、--jobs。详见 compare_remesh_normals.py 顶部说明。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Any

import numpy as np

import pymeshlab
import trimesh
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from algorithms import ALGORITHM_REGISTRY


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动时预热 PyMeshLab，避免第一次请求触发冷启动延迟（30-60s）。

    PyMeshLab 动态库（OpenGL、CGAL 等）在首次调用 filter 时才真正加载。
    启动阶段构造最小三角网格并跑一遍 remove_null_faces，强制完成所有库的加载，
    确保第一个真实请求能立即得到响应。
    """
    t0 = time.time()
    print("[warmup] 开始预热 PyMeshLab...", flush=True)
    try:
        ms = pymeshlab.MeshSet()
        # 构造最小三角形网格（3 顶点 1 面）触发底层库完整初始化
        v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        f = np.array([[0, 1, 2]], dtype=np.int32)
        mesh = pymeshlab.Mesh(vertex_matrix=v, face_matrix=f)
        ms.add_mesh(mesh)
        ms.meshing_remove_null_faces()
        del ms
        print(f"[warmup] PyMeshLab 预热完成，耗时 {time.time() - t0:.1f}s", flush=True)
    except Exception as e:
        print(f"[warmup] 预热失败（不影响服务启动）: {e}", flush=True)
    yield


app = FastAPI(title="Mesh Remesh Service", version="0.1.0", lifespan=lifespan)


def _normalize_ply_to_float32(ply_path: str) -> None:
    """将 PLY 文件规范化为 float32 坐标，去掉 quality 等非标准属性，确保 Three.js PLYLoader 可解析。"""
    try:
        mesh = trimesh.load(ply_path, process=False)
        if isinstance(mesh, trimesh.Scene):
            meshes = mesh.dump(concatenate=False)
            mesh = trimesh.util.concatenate(meshes) if meshes else trimesh.util.concatenate(list(mesh.geometry.values()))
        # trimesh 导出的 PLY 默认是 float32 坐标，且只含 vertex/face 基本属性
        mesh.export(ply_path)
    except Exception as e:
        # 规范化失败不影响主流程，只记录日志
        import logging
        logging.warning(f"PLY normalize failed: {e}")


SUPPORTED_INPUT_EXTENSIONS = {".glb", ".gltf", ".obj", ".ply", ".stl", ".off"}
INTERMEDIATE_FORMAT = ".ply"
OUTPUT_FORMAT = ".ply"


def _convert_to_intermediate(src_path: str, dst_path: str) -> None:
    """用 trimesh 将任意格式转为中间 PLY，方便 PyMeshLab / Open3D 读取。

    关键：对 Scene（如 GLB/GLTF）使用 scene.dump() 而非 geometry.values()，
    前者会将场景图中每个节点的变换矩阵应用到顶点上，得到世界坐标；
    后者只返回局部坐标，会导致输出 PLY 的坐标与 Three.js GLTFLoader 渲染的坐标不一致。
    """
    scene_or_mesh = trimesh.load(src_path)
    if isinstance(scene_or_mesh, trimesh.Scene):
        # dump() 应用场景图变换，返回世界坐标的 Trimesh 列表
        meshes = scene_or_mesh.dump(concatenate=False)
        if meshes:
            mesh = trimesh.util.concatenate(meshes)
        else:
            # 回退：尝试直接合并（通常不会走到这里）
            mesh = trimesh.util.concatenate(list(scene_or_mesh.geometry.values()))
    else:
        mesh = scene_or_mesh
    mesh.export(dst_path)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/algorithms")
def list_algorithms():
    """列出所有已注册的均匀化算法及其参数说明。"""
    result = []
    for name, cls in ALGORITHM_REGISTRY.items():
        algo = cls()
        result.append({
            "name": name,
            "label": algo.label,
            "params": algo.describe_params(),
        })
    return result


@app.post("/remesh")
def remesh(
    file: UploadFile = File(...),
    algorithm: str = Form("bim_preprocessor"),
    params_json: str = Form("{}"),
):
    """执行网格均匀化处理。

    - file: 网格文件（GLB/OBJ/PLY/STL 等）
    - algorithm: 算法名称
    - params_json: JSON 字符串形式的算法参数
    """
    import json

    if algorithm not in ALGORITHM_REGISTRY:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "msg": f"不支持的算法: {algorithm}，可选: {list(ALGORITHM_REGISTRY.keys())}"},
        )

    try:
        params: dict[str, Any] = json.loads(params_json)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "params_json 不是合法的 JSON"})

    suffix = os.path.splitext(file.filename or "mesh.ply")[1].lower()
    if suffix not in SUPPORTED_INPUT_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "msg": f"不支持的文件格式: {suffix}，支持: {SUPPORTED_INPUT_EXTENSIONS}"},
        )

    work_dir = tempfile.mkdtemp(prefix="remesh_")
    try:
        input_path = os.path.join(work_dir, f"input{suffix}")
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        needs_convert = suffix not in {".ply", ".obj", ".stl", ".off"}
        if needs_convert:
            intermediate_path = os.path.join(work_dir, f"intermediate{INTERMEDIATE_FORMAT}")
            _convert_to_intermediate(input_path, intermediate_path)
            algo_input = intermediate_path
        else:
            algo_input = input_path

        output_path = os.path.join(work_dir, f"output{OUTPUT_FORMAT}")

        algo_instance = ALGORITHM_REGISTRY[algorithm]()
        result = algo_instance.run(algo_input, output_path, params)

        # 规范化输出：转为 float32 PLY，去掉 quality 等非标准属性，确保 Three.js 可解析
        _normalize_ply_to_float32(output_path)

        return FileResponse(
            path=result.output_path,
            media_type="application/octet-stream",
            filename=f"remeshed{OUTPUT_FORMAT}",
            headers={
                "X-Vertex-Before": str(result.vertex_count_before),
                "X-Face-Before": str(result.face_count_before),
                "X-Vertex-After": str(result.vertex_count_after),
                "X-Face-After": str(result.face_count_after),
            },
            background=_cleanup_task(work_dir),
        )
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"code": 500, "msg": f"处理失败:\n{tb}"})


def _cleanup_task(work_dir: str):
    """返回 Starlette BackgroundTask，在响应发送后清理临时目录。"""
    from starlette.background import BackgroundTask
    return BackgroundTask(shutil.rmtree, work_dir, True)


# ── C2M (Cloud-to-Mesh Distance) ───────────────────────────────────────

class C2MParams(BaseModel):
    voxel_size: float = 0.05
    max_colormap_distance: float = 0.10
    max_histogram_distance: float = 1.0
    histogram_bins: int = 50
    # 合格界限：容差上下限（±X），青/黄颜色出现在此处
    tolerance_limit: float = 0.05
    # 可视化平滑参数：用 Laplacian 平滑消除细密网格引起的高频色斑
    # 原始距离仍写入 .bin，平滑仅影响着色用的 colored PLY
    smoothing_iterations: int = 5
    smoothing_strength: float = 0.5
    # 法向量约束参数：kNN 候选 + 方向筛选
    knn_k: int = 8
    normal_constraint_enabled: bool = False
    normal_half_space_only: bool = True
    normal_max_angle_deg: float = 75.0
    normal_fallback_mode: str = "nearest"


class C2MRequest(BaseModel):
    scan_path: str
    mesh_path: str
    alignment_matrix: list[float]
    params: C2MParams = C2MParams()


C2M_OUTPUT_DIR = "/storage/c2m_results"


@app.post("/c2m/compute")
def c2m_compute(req: C2MRequest):
    """计算 Cloud-to-Mesh Distance。

    接收 LAS 和 PLY 的磁盘路径（共享存储卷），以及列主序配准矩阵，
    返回统计数据 + colored PLY 路径。
    """
    import open3d as o3d

    from algorithms.c2m_distance import (
        apply_transform,
        colorize_mesh_by_signed_distance,
        column_major_to_matrix4,
        compute_bbox_overlap,
        compute_signed_mesh_to_cloud_distances,
        compute_statistics,
        load_and_downsample_las,
        smooth_vertex_distances,
    )

    print(f"[C2M] 收到计算请求: scan_path={req.scan_path}, mesh_path={req.mesh_path}", flush=True)
    if not os.path.isfile(req.scan_path):
        msg = f"LAS 文件不存在: {req.scan_path}"
        print(f"[C2M] 校验失败: {msg}", flush=True)
        return JSONResponse(status_code=400, content={"code": 400, "msg": msg})
    if not os.path.isfile(req.mesh_path):
        msg = f"PLY 文件不存在: {req.mesh_path}"
        print(f"[C2M] 校验失败: {msg}", flush=True)
        return JSONResponse(status_code=400, content={"code": 400, "msg": msg})
    if len(req.alignment_matrix) != 16:
        msg = f"alignment_matrix 必须包含 16 个浮点数 (当前 {len(req.alignment_matrix)})"
        print(f"[C2M] 校验失败: {msg}", flush=True)
        return JSONResponse(status_code=400, content={"code": 400, "msg": msg})

    p = req.params

    try:
        print(f"[C2M] 开始计算，对齐矩阵={req.alignment_matrix}", flush=True)

        # 1. 读取 LAS + 降采样
        pcd, points_before, scan_bbox_raw = load_and_downsample_las(req.scan_path, p.voxel_size)
        points_after = len(pcd.points)

        # 2. 列主序 -> 4x4 矩阵，施加变换（将 scan 点云变换到 BIM 坐标空间）
        matrix = column_major_to_matrix4(req.alignment_matrix)
        scan_bbox_transformed = apply_transform(pcd, matrix)

        # 3. 读取 PLY 三角网格
        mesh = o3d.io.read_triangle_mesh(req.mesh_path)
        if len(mesh.triangles) == 0:
            msg = f"PLY 网格为空（0 个三角面）: {req.mesh_path}"
            print(f"[C2M] 校验失败: {msg}", flush=True)
            return JSONResponse(status_code=400, content={"code": 400, "msg": msg})
        mesh_pts = np.asarray(mesh.vertices)
        mesh_bbox = {"min": mesh_pts.min(axis=0).tolist(), "max": mesh_pts.max(axis=0).tolist()}

        # 4. bbox overlap 诊断
        overlap = compute_bbox_overlap(
            scan_bbox_transformed["min"], scan_bbox_transformed["max"],
            mesh_bbox["min"], mesh_bbox["max"],
        )

        # 5. 计算 mesh 每个顶点到最近 scan 点的有符号距离
        distances = compute_signed_mesh_to_cloud_distances(
            mesh,
            pcd,
            knn_k=p.knn_k,
            normal_constraint_enabled=p.normal_constraint_enabled,
            normal_half_space_only=p.normal_half_space_only,
            normal_max_angle_deg=p.normal_max_angle_deg,
            normal_fallback_mode=p.normal_fallback_mode,
        )

        # 6. 统计 + 对称直方图
        stat_result = compute_statistics(distances, p.max_histogram_distance, p.histogram_bins)
        print(
            f"[C2M] 有符号距离: min={stat_result['stats']['min']:.4f} max={stat_result['stats']['max']:.4f} "
            f"mean={stat_result['stats']['mean']:.4f} std={stat_result['stats']['std']:.4f}",
            flush=True,
        )

        # 7. 写出 per-vertex float32 原始有符号距离（与 mesh 顶点顺序一致，供前端动态着色与点选插值）
        os.makedirs(C2M_OUTPUT_DIR, exist_ok=True)
        output_token = uuid.uuid4().hex
        dist_filename = f"dist_{output_token}.bin"
        dist_path = os.path.join(C2M_OUTPUT_DIR, dist_filename)
        distances.astype(np.float32).tofile(dist_path)
        dist_size = os.path.getsize(dist_path)

        # 8. Laplacian 平滑：消除细密网格引起的高频色斑
        #    smoothing_iterations=0 可跳过平滑，保留原始着色效果
        distances_for_color = smooth_vertex_distances(
            mesh,
            distances,
            iterations=p.smoothing_iterations,
            strength=p.smoothing_strength,
        )

        # 9. 发散色图着色到 mesh 顶点（使用平滑后的距离）+ 写 colored mesh PLY
        colorize_mesh_by_signed_distance(mesh, distances_for_color, p.max_colormap_distance, p.tolerance_limit)
        colored_filename = f"colored_{output_token}.ply"
        colored_path = os.path.join(C2M_OUTPUT_DIR, colored_filename)
        o3d.io.write_triangle_mesh(colored_path, mesh, write_vertex_colors=True)
        colored_size = os.path.getsize(colored_path)

        return {
            "pointsBefore": points_before,
            "pointsAfter": points_after,
            "meshVertices": len(mesh_pts),
            **stat_result,
            "diagnostics": {
                "scanBboxRaw": scan_bbox_raw,
                "scanBboxAfterTransform": scan_bbox_transformed,
                "meshBbox": mesh_bbox,
                "bboxOverlapIoU": round(overlap, 4),
            },
            "coloredPlyPath": colored_path,
            "coloredPlySize": colored_size,
            "distancesPath": dist_path,
            "distancesSize": dist_size,
        }
    except Exception:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"code": 500, "msg": f"C2M 计算失败:\n{tb}"})


class C2MRecolorRequest(BaseModel):
    """重新着色请求：不重新计算距离，仅用新色彩参数生成 colored PLY。"""
    distances_path: str          # 已存储的 float32 raw distances 文件路径
    mesh_path: str               # remeshed PLY 路径（用于 Laplacian 平滑）
    max_colormap_distance: float = 0.10
    tolerance_limit: float = 0.05
    smoothing_iterations: int = 5
    smoothing_strength: float = 0.5


@app.post("/c2m/recolor")
def c2m_recolor(req: C2MRecolorRequest):
    """用新色彩参数重新生成 colored PLY，不重新计算点云距离。

    流程：读 distances.bin → Laplacian 平滑 → 重新着色 → 保存新 PLY
    """
    import open3d as o3d

    from algorithms.c2m_distance import (
        colorize_mesh_by_signed_distance,
        smooth_vertex_distances,
    )

    if not os.path.isfile(req.distances_path):
        return JSONResponse(status_code=400, content={"code": 400, "msg": f"distances 文件不存在: {req.distances_path}"})
    if not os.path.isfile(req.mesh_path):
        return JSONResponse(status_code=400, content={"code": 400, "msg": f"PLY 文件不存在: {req.mesh_path}"})

    try:
        # 1. 读取已存储的 float32 有符号距离
        distances = np.fromfile(req.distances_path, dtype=np.float32)

        # 2. 读取 remeshed PLY（用于 Laplacian 平滑的邻接关系）
        mesh = o3d.io.read_triangle_mesh(req.mesh_path)
        if len(mesh.triangles) == 0:
            return JSONResponse(status_code=400, content={"code": 400, "msg": "PLY 网格为空（0 个三角面）"})

        n_verts = len(mesh.vertices)
        if len(distances) != n_verts:
            return JSONResponse(
                status_code=400,
                content={"code": 400, "msg": f"distances 长度 {len(distances)} 与网格顶点数 {n_verts} 不匹配"},
            )

        # 3. Laplacian 平滑（仅用于着色，原始距离不变）
        distances_for_color = smooth_vertex_distances(
            mesh, distances,
            iterations=req.smoothing_iterations,
            strength=req.smoothing_strength,
        )

        # 4. 用新参数重新着色
        colorize_mesh_by_signed_distance(
            mesh, distances_for_color,
            req.max_colormap_distance,
            req.tolerance_limit,
        )

        # 5. 保存新 colored PLY
        os.makedirs(C2M_OUTPUT_DIR, exist_ok=True)
        output_token = uuid.uuid4().hex
        colored_filename = f"colored_{output_token}.ply"
        colored_path = os.path.join(C2M_OUTPUT_DIR, colored_filename)
        o3d.io.write_triangle_mesh(colored_path, mesh, write_vertex_colors=True)
        colored_size = os.path.getsize(colored_path)

        return {
            "coloredPlyPath": colored_path,
            "coloredPlySize": colored_size,
        }
    except Exception:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"code": 500, "msg": f"重新着色失败:\n{tb}"})


# ── 精细化配准 (Fine Registration) ─────────────────────────────────────

class FineAlignRequest(BaseModel):
    scan_path: str
    mesh_path: str
    init_transform: list[float]
    max_correspondence_distance: float = 0.3
    rmse_regress_ratio: float = 1.05
    fitness_regress_ratio: float = 0.95
    apply_when_regressed: bool = False


@app.post("/align/fine")
def align_fine(req: FineAlignRequest):
    """执行精细化配准（Point-to-Plane ICP）。

    以 Remesh PLY 的顶点和法线作为 Target，以原始 LAS 点云作为 Source，
    使用手动粗配准矩阵作为初始值，执行 Point-to-Plane ICP 精调。
    支持负优化告警阈值：若精调后指标退化，标记告警。
    是否应用精调结果由请求参数 apply_when_regressed 决定。

    参数（JSON Body）：
    - scan_path: 原始点云 LAS 文件路径（磁盘绝对路径）
    - mesh_path: Remesh 产物 PLY 文件路径（磁盘绝对路径）
    - init_transform: 列主序 4x4 配准矩阵（16 个浮点数，来自手动粗配准）
    - max_correspondence_distance: ICP 最大匹配距离（米），默认 0.3
    - rmse_regress_ratio: RMSE 退化阈值比例，默认 1.05
    - fitness_regress_ratio: Fitness 退化阈值比例，默认 0.95
    - apply_when_regressed: 触发告警时是否仍应用精调结果，默认 false

    返回：
    - transform: 最终列主序 16 浮点矩阵
    - quaternion: 四元数 {qx,qy,qz,qw}
    - translation: 平移向量 {tx,ty,tz}
    - regressed: 是否触发负优化告警
    - appliedFineResult: 最终是否应用精调结果
    - fallback: 兼容字段，等价于 regressed 且未应用精调
    - metrics: 详细评估指标
    """
    from algorithms.icp_registration import fine_registration

    if not os.path.isfile(req.scan_path):
        return JSONResponse(status_code=400, content={"code": 400, "msg": f"LAS 文件不存在: {req.scan_path}"})
    if not os.path.isfile(req.mesh_path):
        return JSONResponse(status_code=400, content={"code": 400, "msg": f"PLY 文件不存在: {req.mesh_path}"})
    if len(req.init_transform) != 16:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "init_transform 必须包含 16 个浮点数"})
    if req.max_correspondence_distance <= 0:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "max_correspondence_distance 必须大于 0"})
    if req.rmse_regress_ratio <= 0:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "rmse_regress_ratio 必须大于 0"})
    if req.fitness_regress_ratio <= 0:
        return JSONResponse(status_code=400, content={"code": 400, "msg": "fitness_regress_ratio 必须大于 0"})

    try:
        result = fine_registration(
            scan_path=req.scan_path,
            remesh_ply_path=req.mesh_path,
            init_matrix_16=req.init_transform,
            max_correspondence_distance=req.max_correspondence_distance,
            rmse_regress_ratio=req.rmse_regress_ratio,
            fitness_regress_ratio=req.fitness_regress_ratio,
            apply_when_regressed=req.apply_when_regressed,
        )
        return result
    except Exception:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"code": 500, "msg": f"精细化配准失败:\n{tb}"})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
