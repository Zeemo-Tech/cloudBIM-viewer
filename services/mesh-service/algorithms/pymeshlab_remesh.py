from __future__ import annotations

from typing import Any

import pymeshlab

from .base import RemeshAlgorithm, RemeshResult, register


def _abs_value(val: float):
    """兼容不同 pymeshlab 版本的绝对值包装器。"""
    if hasattr(pymeshlab, "AbsoluteValue"):
        return pymeshlab.AbsoluteValue(val)
    if hasattr(pymeshlab, "PureValue"):
        return pymeshlab.PureValue(val)
    return val


def _clamp_float(val: float, min_val: float, max_val: float) -> float:
    """浮点参数夹紧，避免异常输入把滤镜推到非预期区间。"""
    return max(min_val, min(max_val, val))


def _clamp_int(val: int, min_val: int, max_val: int) -> int:
    """整数参数夹紧。"""
    return max(min_val, min(max_val, val))


def _common_clean_and_repair(ms: pymeshlab.MeshSet, clean_tolerance: float) -> None:
    """公共的几何清理 + 非流形修复（在 QEM 之前执行一次）。"""
    ms.meshing_merge_close_vertices(threshold=_abs_value(clean_tolerance))
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_null_faces()
    ms.meshing_remove_unreferenced_vertices()
    ms.meshing_repair_non_manifold_edges(method=0)
    ms.meshing_repair_non_manifold_vertices(vertdispratio=0.0)


def _repair_after_qem(ms: pymeshlab.MeshSet) -> None:
    """QEM 后再做一次非流形修复，防止细分阶段崩溃。"""
    ms.meshing_repair_non_manifold_edges(method=0)
    ms.meshing_repair_non_manifold_vertices(vertdispratio=0.0)


@register
class PyMeshLabBIMPreprocessor(RemeshAlgorithm):
    """BIM 网格预处理流水线 v4（细分 + 全功能 Isotropic，边长收敛均匀）。

    专为 BIM/IFC 导出模型的 Cloud-to-Mesh 高精度距离计算设计。
    流水线：
      0. 几何清理 + 非流形修复（QEM 前）。
      1. QEM 瘦身（可选）。
      2. 非流形修复（QEM 后）。
      3. 中点细分：阈值 = target × subdivision_threshold_ratio（默认 2.0）。
         关键数学约束：阈值 ≥ 2t 时，细分后最短边 ≥ t > 0.8t（Isotropic 的
         collapse 下限），因此开启 collapse 不会与细分对抗，边长可收敛至 [0.8t, 1.33t]。
      4. Isotropic 重网格化（全功能：split+collapse+swap+smooth+reproject）。
         collapseflag 默认恢复为 True，与 thr_ratio≥2.0 配合消除短边碎片。
      5. 法线重算。
    """

    name = "bim_preprocessor"
    label = "细分 + Isotropic（均匀收敛）"

    def run(self, input_path: str, output_path: str, params: dict[str, Any]) -> RemeshResult:
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(input_path)

        if len(ms) > 1:
            ms.generate_by_merging_visible_meshes()

        v_before = ms.current_mesh().vertex_number()
        f_before = ms.current_mesh().face_number()

        target_edge_length    = _clamp_float(float(params.get("target_edge_length", 0.1)), 0.001, 1e6)
        clean_tolerance       = _clamp_float(float(params.get("clean_tolerance", 0.005)), 0.0, 1e6)
        use_decimation        = bool(params.get("use_decimation", True))
        decimation_ratio      = _clamp_float(float(params.get("decimation_ratio", 0.5)), 0.05, 0.95)
        # 性能优化：细分默认 2 轮（2 轮已可将 10m 大面边长降至 <=2.5m），避免 3 轮导致面片数 64 倍指数膨胀
        subdivision_iters     = _clamp_int(int(params.get("subdivision_iterations", 2)), 0, 10)
        # 关键参数：细分阈值系数 ≥ 2.0 时细分后最短边 ≥ t，不触发 Isotropic collapse（0.8t 下限）
        subdivision_thr_ratio = _clamp_float(float(params.get("subdivision_threshold_ratio", 2.0)), 1.0, 4.0)
        adaptive              = bool(params.get("adaptive", True))
        crease_angle          = _clamp_float(float(params.get("crease_angle", 60.0)), 0.0, 90.0)
        use_isotropic         = bool(params.get("use_isotropic", True))
        # 性能优化：基准测试证实 5 轮即可达到 >66% 的理想边长收敛，默认 5 轮大幅缩短耗时
        isotropic_iters       = _clamp_int(int(params.get("isotropic_iterations", 5)), 1, 20)
        surface_dist_ratio    = _clamp_float(float(params.get("surface_dist_ratio", 0.5)), 0.01, 2.0)
        # collapseflag 恢复 True：配合 thr_ratio≥2.0 可消除短边碎片，使边长收敛至 [0.8t, 1.33t]
        isotropic_collapse    = bool(params.get("isotropic_collapse", True))
        sliver_merge_ratio    = _clamp_float(float(params.get("sliver_merge_ratio", 0.03)), 0.0, 0.1)
        sliver_relax_checksurfdist = bool(params.get("sliver_relax_checksurfdist", True))
        # 约束闭环：仅当细分阈值系数 >= 2.0 时才允许 collapse，避免细分-塌缩互相对抗。
        effective_isotropic_collapse = isotropic_collapse and subdivision_thr_ratio >= 2.0

        # ── 阶段 0：几何清理 + 非流形修复（QEM 前）────────────────────────
        _common_clean_and_repair(ms, clean_tolerance)

        # ── 阶段 1：QEM 瘦身 ────────────────────────────────────────────
        # 性能优化：仅当原始网格面数较大（> 30,000）且设置了 decimation 时才执行减面；
        # 小于 30,000 面的 BIM 模型跳过 QEM，节省 15-30s 计算时间并避免细小构件退化
        did_decimate = False
        if use_decimation and f_before > 30000:
            target_faces = max(1000, int(f_before * decimation_ratio))
            if target_faces < f_before:
                ms.meshing_decimation_quadric_edge_collapse(
                    targetfacenum=target_faces,
                    preserveboundary=True,
                    preservenormal=True,
                    preservetopology=True,
                    optimalplacement=True,
                )
                did_decimate = True

        # ── 阶段 2：非流形修复（仅在做过 QEM 后才需再次扫描修复）─────────
        if did_decimate:
            _repair_after_qem(ms)

        # ── 阶段 3：中点细分（打碎超长边）──────────────────────────────
        # thr_ratio 默认 2.0：仅切割 > 2t 的边，产生 [t, 2t) 长的半边。
        # 这些半边 ≥ t > 0.8t，不触发 Isotropic collapse，两步不再对抗。
        if subdivision_iters > 0:
            ms.meshing_surface_subdivision_midpoint(
                iterations=subdivision_iters,
                threshold=_abs_value(target_edge_length * subdivision_thr_ratio),
            )

        # ── 阶段 4：Isotropic 重网格化 ──────────────────────────────────
        # 全功能开启（split/collapse/swap/smooth/reproject）。
        # collapse 阈值约 0.8t：thr_ratio≥2.0 确保细分后无 <t 的边，故 collapse 不破坏细分结果。
        # 5 次迭代足以让边长收敛至 [0.8t, 1.33t]（平均 ~t）。
        if use_isotropic:
            remesh_kwargs: dict[str, Any] = {
                "iterations":    isotropic_iters,
                "adaptive":      adaptive,
                "featuredeg":    crease_angle,
                "checksurfdist": True,
                "collapseflag":  effective_isotropic_collapse,
                "splitflag":     True,
                "swapflag":      True,
                "smoothflag":    True,
                "reprojectflag": True,
            }
            remesh_kwargs["targetlen"]   = _abs_value(target_edge_length)
            remesh_kwargs["maxsurfdist"] = _abs_value(target_edge_length * surface_dist_ratio)
            ms.meshing_isotropic_explicit_remeshing(**remesh_kwargs)

        # ── 阶段 5：薄片三角形后处理 ─────────────────────────────────────
        # 薄片（sliver）产生原因：checksurfdist 在特征边附近阻止了边翻转，
        # 导致两顶点极近但第三顶点很远的三角形被冻结。
        # 步骤 1：更保守地合并极短边，默认 0.03t 且不超过 clean_tolerance，降低误焊细节风险
        sliver_merge_threshold = target_edge_length * sliver_merge_ratio
        if clean_tolerance > 0:
            sliver_merge_threshold = min(sliver_merge_threshold, clean_tolerance)
        ms.meshing_merge_close_vertices(threshold=_abs_value(sliver_merge_threshold))
        ms.meshing_remove_null_faces()
        ms.meshing_remove_unreferenced_vertices()
        # 步骤 2：仅翻边 + 平滑（不增减顶点），1 轮即可完成拓扑微调，并关闭 reproject 节省空间树查询
        ms.meshing_isotropic_explicit_remeshing(
            iterations=1,
            adaptive=False,
            featuredeg=crease_angle,
            checksurfdist=sliver_relax_checksurfdist,
            collapseflag=False,
            splitflag=False,
            swapflag=True,
            smoothflag=True,
            reprojectflag=False,
            targetlen=_abs_value(target_edge_length),
            maxsurfdist=_abs_value(target_edge_length * surface_dist_ratio),
        )

        # ── 法线重算 ────────────────────────────────────────────────────
        ms.compute_normal_per_face()
        ms.compute_normal_per_vertex()

        ms.save_current_mesh(
            output_path,
            save_vertex_quality=False,
            save_face_quality=False,
            save_vertex_color=False,
            save_vertex_coord=True,
        )

        v_after = ms.current_mesh().vertex_number()
        f_after = ms.current_mesh().face_number()

        return RemeshResult(
            output_path=output_path,
            vertex_count_before=v_before,
            face_count_before=f_before,
            vertex_count_after=v_after,
            face_count_after=f_after,
        )

    def describe_params(self) -> list[dict]:
        return [
            {
                "key":     "target_edge_length",
                "label":   "目标边长 (m)",
                "type":    "float",
                "default": 0.1,
                "min":     0.001,
                "tooltip": "Isotropic 目标边长（米）。边长收敛至 [0.8t, 1.33t]，均值 ~t。当前默认 thr_ratio=2.0 + collapse=开启，实际面密度比旧版低、更均匀。",
            },
            {
                "key":     "clean_tolerance",
                "label":   "清理容差 (m)",
                "type":    "float",
                "default": 0.005,
                "min":     0.0001,
                "tooltip": "合并近顶点的距离阈值（米）。默认 5mm，用于缝合微小裂缝与消除退化面。",
            },
            {
                "key":          "use_decimation",
                "label":        "智能瘦身",
                "type":         "bool",
                "default":      True,
                "tooltip":      "启用 QEM 二次误差简化，预先降低面片密度，减轻后续 Isotropic 迭代负担。",
                "visible_when": None,
            },
            {
                "key":          "decimation_ratio",
                "label":        "瘦身保留比例",
                "type":         "float",
                "default":      0.5,
                "min":          0.05,
                "max":          0.95,
                "tooltip":      "目标面数 = 原始面数 × 比例。BIM 模型通常可压到 0.3 而不变形。",
                "visible_when": {"key": "use_decimation", "value": True},
            },
            {
                "key":     "subdivision_iterations",
                "label":   "细分迭代次数",
                "type":    "int",
                "default": 2,
                "min":     0,
                "max":     10,
                "tooltip": "中点细分轮次。用于打碎极长边（如 IFC 导出的大平面）；默认 2 轮兼顾效率与细分均匀度；设为 0 可跳过。",
            },
            {
                "key":     "subdivision_threshold_ratio",
                "label":   "细分阈值系数",
                "type":    "float",
                "default": 2.0,
                "min":     1.0,
                "max":     4.0,
                "tooltip": "仅切割长度 > 目标边长×此系数 的边。默认 2.0 是关键约束：细分后最短边 ≥ t，高于 Isotropic collapse 下限（0.8t），两步协作而非对抗，边长收敛更均匀。增大此值可减少面数但留下更多较长边。",
            },
            {
                "key":          "use_isotropic",
                "label":        "Isotropic 重网格化",
                "type":         "bool",
                "default":      True,
                "tooltip":      "开启后做 split/collapse/swap/smooth/reproject 全套均匀化。关闭则仅保留细分结果（较碎，不建议）。",
                "visible_when": None,
            },
            {
                "key":          "isotropic_collapse",
                "label":        "Isotropic 边合并",
                "type":         "bool",
                "default":      True,
                "tooltip":      "开启后 Isotropic 会合并 < 0.8t 的短边，消除碎片感。注意：当细分阈值系数 < 2.0 时运行期会自动禁用，避免与细分步骤对抗。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
            {
                "key":          "isotropic_iterations",
                "label":        "Isotropic 迭代次数",
                "type":         "int",
                "default":      5,
                "min":          1,
                "max":          20,
                "tooltip":      "等边化迭代次数。基准测试推荐 5 次（收敛速度与质量最佳平衡点，约 1~2 分钟）。增大可进一步提升边长集中度，但耗时大幅增加。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
            {
                "key":          "surface_dist_ratio",
                "label":        "表面偏差系数",
                "type":         "float",
                "default":      0.5,
                "min":          0.01,
                "max":          2.0,
                "tooltip":      "maxsurfdist = 目标边长 × 此系数。值越小对曲面（圆柱/管道）的保形约束越严，但可能导致部分操作无法执行；0.5 适合大多数 BIM 建筑构件。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
            {
                "key":          "sliver_merge_ratio",
                "label":        "薄片合并系数",
                "type":         "float",
                "default":      0.03,
                "min":          0.0,
                "max":          0.1,
                "tooltip":      "后处理短边合并阈值系数，实际阈值 = min(目标边长×系数, clean_tolerance)。默认 0.03，更保守，降低误焊细节风险。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
            {
                "key":          "sliver_relax_checksurfdist",
                "label":        "薄片后处理放宽保形",
                "type":         "bool",
                "default":      True,
                "tooltip":      "控制薄片后处理阶段的 checksurfdist。默认 True（保形优先）；设为 False 时更激进去薄片，但几何偏移风险更高。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
            {
                "key":     "adaptive",
                "label":   "自适应采样",
                "type":    "bool",
                "default": True,
                "tooltip": "曲率高处（MEP 管道、圆柱构件）边更短、平坦处更疏。对纯平面 BIM 模型开/关效果相同且耗时一致，但含圆柱构件时建议开启以获得更精细的曲面采样。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
            {
                "key":     "crease_angle",
                "label":   "折角保护阈值 (°)",
                "type":    "float",
                "default": 60.0,
                "min":     0.0,
                "max":     90.0,
                "tooltip": "超过此角度的边视为硬边并保留。60° 适合含圆柱的 BIM；需保护更多棱线时可降到 30°（PyMeshLab 默认值）。",
                "visible_when": {"key": "use_isotropic", "value": True},
            },
        ]


@register
class PyMeshLabBIMIsotropicOnly(RemeshAlgorithm):
    """BIM 网格纯 Isotropic 流水线（无细分，依靠多次迭代收敛）。

    与 bim_preprocessor 的区别：
      - 跳过中点细分步骤，完全依赖 Isotropic 的 split 操作打碎长边。
      - 需要更多迭代次数（默认 10），适合边长变化幅度较小的模型。
      - 对极端长边（如 IFC 大平面 1-10m）收敛慢，建议配合 QEM 瘦身。
    流水线：
      0. 几何清理 + 非流形修复。
      1. QEM 瘦身（可选）。
      2. 非流形修复（QEM 后）。
      3. 全功能 Isotropic 重网格化（10 次迭代）。
      4. 法线重算。
    """

    name = "bim_isotropic_only"
    label = "纯 Isotropic（无细分，慢但更均匀）"

    def run(self, input_path: str, output_path: str, params: dict[str, Any]) -> RemeshResult:
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(input_path)

        if len(ms) > 1:
            ms.generate_by_merging_visible_meshes()

        v_before = ms.current_mesh().vertex_number()
        f_before = ms.current_mesh().face_number()

        target_edge_length = _clamp_float(float(params.get("target_edge_length", 0.1)), 0.001, 1e6)
        clean_tolerance    = _clamp_float(float(params.get("clean_tolerance", 0.005)), 0.0, 1e6)
        use_decimation     = bool(params.get("use_decimation", True))
        decimation_ratio   = _clamp_float(float(params.get("decimation_ratio", 0.5)), 0.05, 0.95)
        adaptive           = bool(params.get("adaptive", True))
        crease_angle       = _clamp_float(float(params.get("crease_angle", 60.0)), 0.0, 90.0)
        isotropic_iters    = _clamp_int(int(params.get("isotropic_iterations", 15)), 1, 20)
        surface_dist_ratio = _clamp_float(float(params.get("surface_dist_ratio", 0.5)), 0.01, 2.0)
        sliver_merge_ratio = _clamp_float(float(params.get("sliver_merge_ratio", 0.03)), 0.0, 0.1)
        sliver_relax_checksurfdist = bool(params.get("sliver_relax_checksurfdist", True))

        # ── 阶段 0：几何清理 + 非流形修复 ─────────────────────────────
        _common_clean_and_repair(ms, clean_tolerance)

        # ── 阶段 1：QEM 瘦身 ────────────────────────────────────────────
        if use_decimation:
            target_faces = max(100, int(f_before * decimation_ratio))
            ms.meshing_decimation_quadric_edge_collapse(
                targetfacenum=target_faces,
                preserveboundary=True,
                preservenormal=True,
                preservetopology=True,
                optimalplacement=True,
            )

        # ── 阶段 2：非流形修复（QEM 后）────────────────────────────────
        _repair_after_qem(ms)

        # ── 阶段 3：全功能 Isotropic 重网格化 ──────────────────────────
        # 无细分预处理：Isotropic 的 split 操作每轮将 >1.33t 的边一分为二，
        # 迭代 log(L_max / t) / log(1.33) 轮后方可收敛。对 BIM 模型 10 轮通常足够。
        remesh_kwargs: dict[str, Any] = {
            "iterations":    isotropic_iters,
            "adaptive":      adaptive,
            "featuredeg":    crease_angle,
            "checksurfdist": True,
            "collapseflag":  True,
            "splitflag":     True,
            "swapflag":      True,
            "smoothflag":    True,
            "reprojectflag": True,
        }
        remesh_kwargs["targetlen"]   = _abs_value(target_edge_length)
        remesh_kwargs["maxsurfdist"] = _abs_value(target_edge_length * surface_dist_ratio)
        ms.meshing_isotropic_explicit_remeshing(**remesh_kwargs)

        # ── 薄片三角形后处理 ─────────────────────────────────────────────
        sliver_merge_threshold = target_edge_length * sliver_merge_ratio
        if clean_tolerance > 0:
            sliver_merge_threshold = min(sliver_merge_threshold, clean_tolerance)
        ms.meshing_merge_close_vertices(threshold=_abs_value(sliver_merge_threshold))
        ms.meshing_remove_null_faces()
        ms.meshing_remove_unreferenced_vertices()
        ms.meshing_isotropic_explicit_remeshing(
            iterations=3,
            adaptive=False,
            featuredeg=crease_angle,
            checksurfdist=sliver_relax_checksurfdist,
            collapseflag=False,
            splitflag=False,
            swapflag=True,
            smoothflag=True,
            reprojectflag=True,
            targetlen=_abs_value(target_edge_length),
            maxsurfdist=_abs_value(target_edge_length * surface_dist_ratio),
        )

        # ── 法线重算 ────────────────────────────────────────────────────
        ms.compute_normal_per_face()
        ms.compute_normal_per_vertex()

        ms.save_current_mesh(
            output_path,
            save_vertex_quality=False,
            save_face_quality=False,
            save_vertex_color=False,
            save_vertex_coord=True,
        )

        v_after = ms.current_mesh().vertex_number()
        f_after = ms.current_mesh().face_number()

        return RemeshResult(
            output_path=output_path,
            vertex_count_before=v_before,
            face_count_before=f_before,
            vertex_count_after=v_after,
            face_count_after=f_after,
        )

    def describe_params(self) -> list[dict]:
        return [
            {
                "key":     "target_edge_length",
                "label":   "目标边长 (m)",
                "type":    "float",
                "default": 0.1,
                "min":     0.001,
                "tooltip": "Isotropic 目标边长（米）。边长收敛至 [0.8t, 1.33t]。无细分预处理时对极端长边需更多迭代。",
            },
            {
                "key":     "clean_tolerance",
                "label":   "清理容差 (m)",
                "type":    "float",
                "default": 0.005,
                "min":     0.0001,
                "tooltip": "合并近顶点的距离阈值（米）。默认 5mm，用于缝合微小裂缝与消除退化面。",
            },
            {
                "key":          "use_decimation",
                "label":        "智能瘦身",
                "type":         "bool",
                "default":      True,
                "tooltip":      "启用 QEM 瘦身以预降面密度，减少 Isotropic 迭代需处理的极长边数量（强烈建议开启）。",
                "visible_when": None,
            },
            {
                "key":          "decimation_ratio",
                "label":        "瘦身保留比例",
                "type":         "float",
                "default":      0.5,
                "min":          0.05,
                "max":          0.95,
                "tooltip":      "目标面数 = 原始面数 × 比例。",
                "visible_when": {"key": "use_decimation", "value": True},
            },
            {
                "key":     "isotropic_iterations",
                "label":   "Isotropic 迭代次数",
                "type":    "int",
                "default": 15,
                "min":     1,
                "max":     20,
                "tooltip": "等边化迭代次数。基准测试最优：10次(目标区间72.8%/269s) → 15次(74.6%/450s，★最优) → 20次退化(71.2%/610s)！不建议超过15次。",
            },
            {
                "key":     "surface_dist_ratio",
                "label":   "表面偏差系数",
                "type":    "float",
                "default": 0.5,
                "min":     0.01,
                "max":     2.0,
                "tooltip": "maxsurfdist = 目标边长 × 此系数。值越小保形越严，但对大面平面约束可能过紧。",
            },
            {
                "key":     "sliver_merge_ratio",
                "label":   "薄片合并系数",
                "type":    "float",
                "default": 0.03,
                "min":     0.0,
                "max":     0.1,
                "tooltip": "后处理短边合并阈值系数，实际阈值 = min(目标边长×系数, clean_tolerance)。默认 0.03，更保守，降低误焊细节风险。",
            },
            {
                "key":     "sliver_relax_checksurfdist",
                "label":   "薄片后处理放宽保形",
                "type":    "bool",
                "default": True,
                "tooltip": "控制薄片后处理阶段的 checksurfdist。默认 True（保形优先）；设为 False 时更激进去薄片，但几何偏移风险更高。",
            },
            {
                "key":     "adaptive",
                "label":   "自适应采样",
                "type":    "bool",
                "default": True,
                "tooltip": "曲率高处（MEP 管道、圆柱构件）边更短、平坦处更疏。对纯平面 BIM 模型开/关效果相同且耗时一致，但含圆柱构件时建议开启。",
            },
            {
                "key":     "crease_angle",
                "label":   "折角保护阈值 (°)",
                "type":    "float",
                "default": 60.0,
                "min":     0.0,
                "max":     90.0,
                "tooltip": "超过此角度的边视为硬边并保留。60° 适合含圆柱的 BIM；需保护更多棱线时可降到 30°。",
            },
        ]
