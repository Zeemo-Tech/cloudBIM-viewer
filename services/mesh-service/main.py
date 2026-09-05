"""网格处理微服务。

提供 REST API：
- /remesh：接收网格文件，执行均匀化/简化后返回处理结果
- /c2m/compute：Cloud-to-Mesh Distance 计算
- /rebar/segment：无标注钢筋几何 PoC

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
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from functools import wraps
from typing import Annotated, Any, Callable, Literal

import numpy as np

import pymeshlab
import trimesh
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from algorithms import ALGORITHM_REGISTRY
from rebar_api import include_rebar_router


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动时预热 PyMeshLab，避免第一次请求触发冷启动延迟（30-60s）。

    PyMeshLab 动态库（OpenGL、CGAL 等）在首次调用 filter 时才真正加载。
    启动阶段构造最小三角网格并跑一遍 remove_null_faces，强制完成所有库的加载，
    确保第一个真实请求能立即得到响应。
    """
    t0 = time.time()
    _ensure_c2m_output_dir(migrate_existing=True)
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


_HEAVY_TASK_GATE = threading.BoundedSemaphore(value=1)
_HEAVY_TASK_STATE_LOCK = threading.Lock()
_ACTIVE_HEAVY_TASK: str | None = None


def _single_heavy_task(task_name: str):
    """Allow only one CPU/memory intensive mesh operation per process."""
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            global _ACTIVE_HEAVY_TASK

            if not _HEAVY_TASK_GATE.acquire(blocking=False):
                with _HEAVY_TASK_STATE_LOCK:
                    active_task = _ACTIVE_HEAVY_TASK
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": 429,
                        "msg": "计算服务正忙，请稍后重试",
                        "activeTask": active_task,
                    },
                    headers={"Retry-After": "5"},
                )

            with _HEAVY_TASK_STATE_LOCK:
                _ACTIVE_HEAVY_TASK = task_name
            try:
                return func(*args, **kwargs)
            finally:
                with _HEAVY_TASK_STATE_LOCK:
                    _ACTIVE_HEAVY_TASK = None
                _HEAVY_TASK_GATE.release()

        return wrapped
    return decorator


include_rebar_router(app, heavy_task=_single_heavy_task)


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
@_single_heavy_task("remesh")
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

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class C2MParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = "quick"
    voxel_size: FiniteFloat = Field(default=0.05, ge=0.001, le=5.0)
    max_colormap_distance: FiniteFloat = Field(default=0.10, ge=0.001, le=10.0)
    max_histogram_distance: FiniteFloat = Field(default=0.10, ge=0.001, le=10.0)
    histogram_bins: int = Field(default=50, ge=10, le=200)
    # 合格界限：容差上下限（±X），青/黄颜色出现在此处
    tolerance_limit: FiniteFloat = Field(default=0.05, ge=0.0001, le=10.0)
    # 当前对外契约统一使用 raw distances 做统计、直方图和着色。
    # 保留字段是为了与 Go 代理兼容；在定义可复核 benchmark 前不开放平滑。
    smoothing_iterations: Literal[0] = 0
    smoothing_strength: Literal[0.5] = 0.5
    # 法向约束的符号语义尚未完成 benchmark，因此仅保留兼容字段与安全默认。
    knn_k: int = Field(default=8, ge=1, le=64)
    normal_constraint_enabled: Literal[False] = False
    normal_half_space_only: bool = True
    normal_max_angle_deg: FiniteFloat = Field(default=75.0, gt=0.0, le=180.0)
    normal_fallback_mode: Literal["nearest"] = "nearest"

    @model_validator(mode="after")
    def validate_visualization_ranges(self):
        if self.tolerance_limit > self.max_colormap_distance:
            raise ValueError("tolerance_limit 不能大于 max_colormap_distance")
        return self


class C2MRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_path: str = Field(min_length=1)
    mesh_path: str = Field(min_length=1)
    alignment_matrix: list[FiniteFloat] = Field(min_length=16, max_length=16)
    params: C2MParams = Field(default_factory=C2MParams)


C2M_OUTPUT_DIR = "/storage/c2m_results"
C2M_QUICK_ALGORITHM_VERSION = "c2m-quick-v2"


def _is_c2m_artifact_name(name: str) -> bool:
    return (
        (name.startswith("colored_") or name.startswith(".colored_"))
        and name.endswith(".ply")
    ) or (
        (name.startswith("dist_") or name.startswith(".dist_"))
        and name.endswith(".bin")
    )


def _ensure_c2m_output_dir(*, migrate_existing: bool = False) -> None:
    """Keep the shared artifact directory writable by the host backend group."""
    raw_gid = os.getenv("C2M_OUTPUT_GID", str(os.getgid())).strip()
    try:
        output_gid = int(raw_gid)
    except ValueError as exc:
        raise RuntimeError("C2M_OUTPUT_GID 必须是非负整数") from exc
    if output_gid < 0:
        raise RuntimeError("C2M_OUTPUT_GID 必须是非负整数")

    os.makedirs(C2M_OUTPUT_DIR, exist_ok=True)
    os.chown(C2M_OUTPUT_DIR, -1, output_gid)
    # setgid keeps every atomically published artifact in the shared group.
    os.chmod(C2M_OUTPUT_DIR, 0o2770)
    if migrate_existing:
        for entry in os.scandir(C2M_OUTPUT_DIR):
            if not _is_c2m_artifact_name(entry.name) or not entry.is_file(follow_symlinks=False):
                continue
            os.chown(entry.path, -1, output_gid, follow_symlinks=False)
            os.chmod(entry.path, 0o660, follow_symlinks=False)


def _c2m_visualization(params: Any) -> dict[str, Any]:
    """Return the effective visualization contract using backend-facing names."""
    return {
        "maxColormapDistance": params.max_colormap_distance,
        "maxHistogramDistance": params.max_histogram_distance,
        "histogramBins": params.histogram_bins,
        "toleranceLimit": params.tolerance_limit,
        "colorDistanceField": "raw",
        "smoothingIterations": 0,
        "smoothingStrength": 0.5,
    }


def _write_c2m_output_atomically(
    filename: str,
    writer: Callable[[str], Any],
    *,
    expected_size: int | None = None,
) -> tuple[str, int]:
    """Write a new artifact in-place atomically without exposing partial files."""
    _ensure_c2m_output_dir()
    final_path = os.path.join(C2M_OUTPUT_DIR, filename)
    suffix = os.path.splitext(filename)[1]
    file_descriptor, temporary_path = tempfile.mkstemp(
        dir=C2M_OUTPUT_DIR,
        prefix=f".{filename}.",
        suffix=suffix,
    )
    os.close(file_descriptor)
    replaced = False
    try:
        write_result = writer(temporary_path)
        if write_result is False:
            raise OSError(f"failed to write {suffix or 'C2M'} artifact")
        actual_size = os.path.getsize(temporary_path)
        if actual_size <= 0:
            raise OSError("C2M artifact is empty")
        if expected_size is not None and actual_size != expected_size:
            raise OSError(
                f"C2M artifact size mismatch: expected {expected_size}, got {actual_size}"
            )
        os.chmod(temporary_path, 0o660)
        with open(temporary_path, "rb") as output_file:
            os.fsync(output_file.fileno())
        os.replace(temporary_path, final_path)
        replaced = True
        return final_path, actual_size
    except Exception:
        if replaced:
            try:
                os.remove(final_path)
            except FileNotFoundError:
                pass
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _triangle_mesh_error(mesh: Any) -> str | None:
    """Return a client-safe validation message for an unusable triangle mesh."""
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or len(vertices) == 0:
        return "PLY 网格为空（0 个顶点）"
    if triangles.ndim != 2 or triangles.shape[1:] != (3,) or len(triangles) == 0:
        return "PLY 网格为空（0 个三角面）"
    if not np.all(np.isfinite(vertices)):
        return "PLY 网格包含非有限顶点坐标"
    if np.any(triangles < 0) or np.any(triangles >= len(vertices)):
        return "PLY 网格包含越界的三角面顶点索引"
    return None


@app.post("/c2m/compute")
def c2m_compute(req: C2MRequest):
    """Dispatch a declared computation profile without silently degrading accuracy."""
    profile = (req.params.profile or "quick").strip().lower()
    if profile == "reference":
        return JSONResponse(
            status_code=501,
            content={
                "code": 501,
                "msg": "reference 高精度计算尚未实现，当前仅支持 quick 预估模式",
                "profile": "reference",
                "implementedProfiles": ["quick"],
            },
        )
    if profile != "quick":
        return JSONResponse(
            status_code=400,
            content={
                "code": 400,
                "msg": f"不支持的 C2M profile: {profile}",
                "implementedProfiles": ["quick"],
            },
        )
    req.params.profile = profile
    return _c2m_compute_quick(req)


@_single_heavy_task("c2m")
def _c2m_compute_quick(req: C2MRequest):
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
    p = req.params
    created_outputs: list[str] = []

    try:
        print(f"[C2M] 开始计算，对齐矩阵={req.alignment_matrix}", flush=True)

        # 1. 读取 LAS + 降采样
        pcd, points_before, scan_bbox_raw = load_and_downsample_las(req.scan_path, p.voxel_size)
        points_after = len(pcd.points)
        if points_after == 0:
            return JSONResponse(status_code=400, content={"code": 400, "msg": "LAS 点云为空"})

        # 2. 列主序 -> 4x4 矩阵，施加变换（将 scan 点云变换到 BIM 坐标空间）
        matrix = column_major_to_matrix4(req.alignment_matrix)
        scan_bbox_transformed = apply_transform(pcd, matrix)

        # 3. 读取 PLY 三角网格
        mesh = o3d.io.read_triangle_mesh(req.mesh_path)
        if msg := _triangle_mesh_error(mesh):
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
        stat_result = compute_statistics(
            distances,
            p.max_histogram_distance,
            p.histogram_bins,
            tolerance=p.tolerance_limit,
        )
        print(
            f"[C2M] 有符号距离: min={stat_result['stats']['min']:.4f} max={stat_result['stats']['max']:.4f} "
            f"mean={stat_result['stats']['mean']:.4f} std={stat_result['stats']['std']:.4f}",
            flush=True,
        )

        # 7. 写出 per-vertex float32 原始有符号距离（与 mesh 顶点顺序一致，供前端动态着色与点选插值）
        output_token = uuid.uuid4().hex
        dist_filename = f"dist_{output_token}.bin"
        # Artifact contract is explicitly little-endian float32, independent of
        # the mesh-service host architecture.
        distances_float32 = np.asarray(distances, dtype="<f4")
        dist_path, dist_size = _write_c2m_output_atomically(
            dist_filename,
            distances_float32.tofile,
            expected_size=distances_float32.nbytes,
        )
        created_outputs.append(dist_path)

        # 8. 着色、统计和 distances.bin 统一使用同一份 raw distances。
        colorize_mesh_by_signed_distance(mesh, distances, p.max_colormap_distance, p.tolerance_limit)
        colored_filename = f"colored_{output_token}.ply"
        colored_path, colored_size = _write_c2m_output_atomically(
            colored_filename,
            lambda path: o3d.io.write_triangle_mesh(path, mesh, write_vertex_colors=True),
        )
        created_outputs.append(colored_path)

        return {
            "profile": "quick",
            "algorithmVersion": C2M_QUICK_ALGORITHM_VERSION,
            "approximation": {"voxelSize": p.voxel_size},
            "metricDirection": "mesh-vertices-to-scan-points",
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
            "visualization": _c2m_visualization(p),
        }
    except Exception:
        for output_path in created_outputs:
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass
        logger.exception("C2M 计算失败")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "C2M 计算失败"})


class C2MRecolorRequest(BaseModel):
    """重新着色请求：不重新计算距离，仅用新色彩参数生成 colored PLY。"""
    model_config = ConfigDict(extra="forbid")

    distances_path: str = Field(min_length=1)  # 已存储的 float32 raw distances 文件路径
    mesh_path: str = Field(min_length=1)        # 与 distances 顶点顺序一致的 remeshed PLY
    max_colormap_distance: FiniteFloat = Field(default=0.10, ge=0.001, le=10.0)
    max_histogram_distance: FiniteFloat = Field(default=0.10, ge=0.001, le=10.0)
    histogram_bins: int = Field(default=50, ge=10, le=200)
    tolerance_limit: FiniteFloat = Field(default=0.05, ge=0.0001, le=10.0)
    smoothing_iterations: Literal[0] = 0
    smoothing_strength: Literal[0.5] = 0.5

    @model_validator(mode="after")
    def validate_visualization_ranges(self):
        if self.tolerance_limit > self.max_colormap_distance:
            raise ValueError("tolerance_limit 不能大于 max_colormap_distance")
        return self


@app.post("/c2m/recolor")
@_single_heavy_task("c2m-recolor")
def c2m_recolor(req: C2MRecolorRequest):
    """用新色彩参数重新生成 colored PLY，不重新计算点云距离。

    流程：读 raw distances.bin → 重算统计/直方图 → 重新着色 → 原子保存新 PLY。

    该端点永远创建唯一的新 PLY，不删除任何旧产物。Go 后端在数据库
    事务切换引用成功后，才可回收旧 PLY。
    """
    import open3d as o3d

    from algorithms.c2m_distance import (
        colorize_mesh_by_signed_distance,
        compute_statistics,
    )

    if not os.path.isfile(req.distances_path):
        return JSONResponse(status_code=400, content={"code": 400, "msg": f"distances 文件不存在: {req.distances_path}"})
    if not os.path.isfile(req.mesh_path):
        return JSONResponse(status_code=400, content={"code": 400, "msg": f"PLY 文件不存在: {req.mesh_path}"})

    created_output: str | None = None
    try:
        # 1. 读取 remeshed PLY，先确定顶点数再校验 distances 精确字节数。
        mesh = o3d.io.read_triangle_mesh(req.mesh_path)
        if msg := _triangle_mesh_error(mesh):
            return JSONResponse(status_code=400, content={"code": 400, "msg": msg})

        n_verts = len(mesh.vertices)
        expected_distance_bytes = n_verts * np.dtype("<f4").itemsize
        actual_distance_bytes = os.path.getsize(req.distances_path)
        if actual_distance_bytes != expected_distance_bytes:
            return JSONResponse(
                status_code=400,
                content={
                    "code": 400,
                    "msg": (
                        f"distances 字节数 {actual_distance_bytes} 与网格顶点数 "
                        f"{n_verts} 所需的 {expected_distance_bytes} 字节不匹配"
                    ),
                },
            )
        distances = np.fromfile(req.distances_path, dtype="<f4")
        if not np.all(np.isfinite(distances)):
            return JSONResponse(
                status_code=400,
                content={"code": 400, "msg": "distances 包含 NaN 或无穷大"},
            )

        # 2. 原始距离负责统计和直方图，确保调节容差/视窗后结果同步更新。
        stat_result = compute_statistics(
            distances,
            req.max_histogram_distance,
            req.histogram_bins,
            tolerance=req.tolerance_limit,
        )

        # 3. 用同一份 raw distances 重新着色。
        colorize_mesh_by_signed_distance(
            mesh, distances,
            req.max_colormap_distance,
            req.tolerance_limit,
        )

        # 4. 先写同目录临时文件，完整后原子发布为全新 colored PLY。
        output_token = uuid.uuid4().hex
        colored_filename = f"colored_{output_token}.ply"
        colored_path, colored_size = _write_c2m_output_atomically(
            colored_filename,
            lambda path: o3d.io.write_triangle_mesh(path, mesh, write_vertex_colors=True),
        )
        created_output = colored_path

        return {
            **stat_result,
            "visualization": _c2m_visualization(req),
            "coloredPlyPath": colored_path,
            "coloredPlySize": colored_size,
        }
    except Exception:
        if created_output is not None:
            try:
                os.remove(created_output)
            except FileNotFoundError:
                pass
        logger.exception("C2M 重新着色失败")
        return JSONResponse(status_code=500, content={"code": 500, "msg": "C2M 重新着色失败"})


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
@_single_heavy_task("fine-align")
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
