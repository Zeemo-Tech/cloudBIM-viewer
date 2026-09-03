"""
Remesh 四版流水线综合对比 Benchmark

对比方案：
  旧版      : 非流形修复在 QEM 后，thr=t，   collapseflag=True（最初版本）
  前版 A    : 非流形修复在 QEM 前后，thr=t,  collapseflag=False（短边碎片版）
  当前 v4   : 非流形修复在 QEM 前后，thr=2t, collapseflag=True，5轮 ← bim_preprocessor 生产版
  纯 Iso    : 无细分，collapseflag=True，10轮 ← bim_isotropic_only 生产版

用法：
    python benchmark_remesh.py <input.glb|input.ply> [target_edge_length]
"""
from __future__ import annotations

import sys
import time
import tempfile
import os
import shutil

import numpy as np
import pymeshlab


# ─────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────
def _abs(val: float):
    if hasattr(pymeshlab, "AbsoluteValue"):
        return pymeshlab.AbsoluteValue(val)
    if hasattr(pymeshlab, "PureValue"):
        return pymeshlab.PureValue(val)
    return val


def convert_glb_to_ply(src: str, dst: str) -> None:
    import trimesh
    obj = trimesh.load(src)
    if isinstance(obj, trimesh.Scene):
        meshes = obj.dump(concatenate=False)
        mesh = trimesh.util.concatenate(meshes) if meshes else \
               trimesh.util.concatenate(list(obj.geometry.values()))
    else:
        mesh = obj
    mesh.export(dst)


def compute_metrics(ms: pymeshlab.MeshSet, t: float) -> dict:
    mesh  = ms.current_mesh()
    verts = mesh.vertex_matrix()
    faces = mesh.face_matrix()
    F, V  = faces.shape[0], verts.shape[0]

    v0 = verts[faces[:, 0]]; v1 = verts[faces[:, 1]]; v2 = verts[faces[:, 2]]
    e01 = np.linalg.norm(v1 - v0, axis=1)
    e12 = np.linalg.norm(v2 - v1, axis=1)
    e20 = np.linalg.norm(v0 - v2, axis=1)
    all_e = np.concatenate([e01, e12, e20])

    mean_e = float(np.mean(all_e))
    std_e  = float(np.std(all_e))
    cov    = std_e / mean_e if mean_e > 0 else 0.0
    min_e  = float(np.min(all_e))
    max_e  = float(np.max(all_e))
    p5     = float(np.percentile(all_e, 5))
    p25    = float(np.percentile(all_e, 25))
    p50    = float(np.percentile(all_e, 50))
    p75    = float(np.percentile(all_e, 75))
    p95    = float(np.percentile(all_e, 95))

    pct_zone  = float(np.sum((all_e >= 0.8*t) & (all_e <= 1.33*t))) / len(all_e) * 100
    pct_short = float(np.sum(all_e < 0.5*t)) / len(all_e) * 100   # <0.5t 碎短边
    pct_long  = float(np.sum(all_e > 2.0*t)) / len(all_e) * 100   # >2t 超长边

    # 三角形质量 2r/R（等边三角形=1，越高越好）
    cross = np.cross(v1 - v0, v2 - v0)
    area  = np.linalg.norm(cross, axis=1) / 2.0
    s     = (e01 + e12 + e20) / 2.0
    valid = area > 1e-12
    r_in  = np.zeros(F); r_ci = np.ones(F)
    r_in[valid]  = area[valid] / s[valid]
    r_ci[valid]  = (e01[valid]*e12[valid]*e20[valid]) / (4.0*area[valid] + 1e-30)
    qual = np.zeros(F)
    nz = r_ci > 1e-30
    qual[nz] = 2.0 * r_in[nz] / r_ci[nz]

    return {
        "face_count":  F,
        "vertex_count": V,
        "mean_edge":   mean_e,
        "std_edge":    std_e,
        "cov":         cov,
        "min_edge":    min_e,
        "max_edge":    max_e,
        "p5_edge":     p5,
        "p25_edge":    p25,
        "p50_edge":    p50,
        "p75_edge":    p75,
        "p95_edge":    p95,
        "pct_zone":    pct_zone,     # [0.8t, 1.33t] 内百分比
        "pct_short":   pct_short,    # < 0.5t 碎短边百分比
        "pct_long":    pct_long,     # > 2t 超长边百分比
        "mean_qual":   float(np.mean(qual)),
        "p10_qual":    float(np.percentile(qual, 10)),
    }


# ─────────────────────────────────────────────────────────────────────
# 公共子步骤
# ─────────────────────────────────────────────────────────────────────
def _clean(ms, tol):
    ms.meshing_merge_close_vertices(threshold=_abs(tol))
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_null_faces()
    ms.meshing_remove_unreferenced_vertices()

def _repair(ms):
    ms.meshing_repair_non_manifold_edges(method=0)
    ms.meshing_repair_non_manifold_vertices(vertdispratio=0.0)

def _qem(ms, f_before, ratio):
    ms.meshing_decimation_quadric_edge_collapse(
        targetfacenum=max(100, int(f_before * ratio)),
        preserveboundary=True, preservenormal=True,
        preservetopology=True, optimalplacement=True,
    )

def _subdivide(ms, thr_m):
    ms.meshing_surface_subdivision_midpoint(iterations=3, threshold=_abs(thr_m))

def _isotropic(ms, t, iters, collapse, surf_ratio=0.5):
    ms.meshing_isotropic_explicit_remeshing(
        iterations=iters, adaptive=True, featuredeg=60.0,
        checksurfdist=True, collapseflag=collapse,
        splitflag=True, swapflag=True, smoothflag=True, reprojectflag=True,
        targetlen=_abs(t), maxsurfdist=_abs(t * surf_ratio),
    )

def _normals(ms):
    ms.compute_normal_per_face()
    ms.compute_normal_per_vertex()


# ─────────────────────────────────────────────────────────────────────
# 四种流水线
# ─────────────────────────────────────────────────────────────────────
def run_pipeline(input_ply: str, output_ply: str, t: float, name: str) -> tuple[dict, float]:
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(input_ply)
    if len(ms) > 1:
        ms.generate_by_merging_visible_meshes()
    f0 = ms.current_mesh().face_number()

    t0 = time.time()

    if name == "旧版":
        # 非流形修复在 QEM 后；thr=t；collapse=True（旧版默认）
        _clean(ms, 0.005)
        _qem(ms, f0, 0.5)
        _repair(ms)
        _subdivide(ms, t)
        ms.meshing_isotropic_explicit_remeshing(
            iterations=3, adaptive=True, featuredeg=60.0,
            checksurfdist=True,
            targetlen=_abs(t), maxsurfdist=_abs(t * 0.2),
        )
        _normals(ms)

    elif name == "前版A":
        # 非流形修复在 QEM 前后；thr=t；collapse=False（短边碎片版）
        _clean(ms, 0.005)
        _repair(ms)
        _qem(ms, f0, 0.5)
        _repair(ms)
        _subdivide(ms, t)
        _isotropic(ms, t, iters=3, collapse=False, surf_ratio=0.2)
        _normals(ms)

    elif name == "当前v4":
        # bim_preprocessor 生产版：thr=2t；collapse=True；5轮；surf_ratio=0.5
        _clean(ms, 0.005)
        _repair(ms)
        _qem(ms, f0, 0.5)
        _repair(ms)
        _subdivide(ms, t * 2.0)        # 关键：阈值=2t
        _isotropic(ms, t, iters=5, collapse=True, surf_ratio=0.5)
        _normals(ms)

    elif name == "纯Iso":
        # bim_isotropic_only 生产版：无细分；collapse=True；10轮
        _clean(ms, 0.005)
        _repair(ms)
        _qem(ms, f0, 0.5)
        _repair(ms)
        # 无细分步骤
        _isotropic(ms, t, iters=10, collapse=True, surf_ratio=0.5)
        _normals(ms)

    elapsed = time.time() - t0
    ms.save_current_mesh(output_ply, save_vertex_quality=False,
                         save_face_quality=False, save_vertex_color=False,
                         save_vertex_coord=True)
    return compute_metrics(ms, t), elapsed


# ─────────────────────────────────────────────────────────────────────
# 报告
# ─────────────────────────────────────────────────────────────────────
LABEL_MAP = {
    "face_count":  "面片数",
    "vertex_count":"顶点数",
    "mean_edge":   "平均边长 (m)",
    "std_edge":    "边长标准差 (m)",
    "cov":         "变异系数 CoV",
    "min_edge":    "最短边 (m)",
    "p5_edge":     "P5 边长 (m)",
    "p25_edge":    "P25 边长 (m)",
    "p50_edge":    "中位边长 (m)",
    "p75_edge":    "P75 边长 (m)",
    "p95_edge":    "P95 边长 (m)",
    "max_edge":    "最长边 (m)",
    "pct_zone":    "落在[0.8t,1.33t]%",
    "pct_short":   "碎短边(<0.5t)%",
    "pct_long":    "超长边(>2t)%",
    "mean_qual":   "平均三角质量[0-1]",
    "p10_qual":    "P10三角质量[0-1]",
}

HIGHER_BETTER = {"pct_zone", "mean_qual", "p10_qual"}
LOWER_BETTER  = {"cov", "min_edge", "max_edge", "p5_edge", "p95_edge",
                 "std_edge", "pct_short", "pct_long"}


def print_report(results: list[tuple[str, dict, float]], t: float):
    names   = [r[0] for r in results]
    metrics = [r[1] for r in results]
    times   = [r[2] for r in results]

    W = 100
    col = 16

    print("\n" + "="*W)
    print("  四版 Remesh 流水线综合对比报告")
    print("="*W)
    print(f"  目标边长 t = {t} m  |  Isotropic keep zone = [{0.8*t:.4f}m, {1.33*t:.4f}m]")
    print(f"  旧版    : thr=t,  collapse=True(默认),  iso_iters=3  [已废弃]")
    print(f"  前版A   : thr=t,  collapse=False,       iso_iters=3  [有碎边问题]")
    print(f"  当前v4  : thr=2t, collapse=True,        iso_iters=5  ← bim_preprocessor 生产")
    print(f"  纯Iso   : 无细分, collapse=True,        iso_iters=10 ← bim_isotropic_only 生产")
    print("-"*W)

    header = f"  {'指标':<28}" + "".join(f"  {n:>{col}}" for n in names)
    print(header)
    print("-"*W)

    for key, label in LABEL_MAP.items():
        vals = [m[key] for m in metrics]

        if key in HIGHER_BETTER:
            best = max(vals)
        elif key in LOWER_BETTER:
            best = min(vals)
        else:
            # 平均边长：最接近 t 的最好
            best_idx = min(range(len(vals)), key=lambda i: abs(vals[i] - t))
            best = vals[best_idx]

        row = f"  {label:<28}"
        for v in vals:
            is_best = (v == best)
            star = "★" if is_best else " "
            if isinstance(v, int):
                row += f"  {v:{col},}{star}"[: col + 3]
                row += f"  {v:>{col},}" + ("★" if is_best else " ")
            else:
                cell = f"{v:>{col}.4f}" + ("★" if is_best else " ")
                row += "  " + cell
        print(row)

    print("-"*W)
    time_row = f"  {'运行时间 (s)':<28}" + "".join(f"  {t_:>{col}.1f} " for t_ in times)
    print(time_row)
    print("="*W)

    # ── 核心解读 ──────────────────────────────────────────────────────
    print("\n  ── 核心指标解读 " + "─"*70)
    print()

    section = [
        ("均匀度（CoV，越小越好）",     "cov",       False),
        ("目标区间命中率（越高越好）",   "pct_zone",  True),
        ("碎短边(<0.5t)占比（越少越好）","pct_short", False),
        ("超长边(>2t)占比（越少越好）",  "pct_long",  False),
        ("最长边（C2M精度，越短越好）",  "max_edge",  False),
        ("平均三角形质量（越高越好）",   "mean_qual", True),
    ]
    for title, key, hb in section:
        vals  = [m[key] for m in metrics]
        best  = max(vals) if hb else min(vals)
        parts = []
        for n, v in zip(names, vals):
            mark = "★" if v == best else " "
            parts.append(f"{n}={v:.4f}{mark}")
        print(f"  {title:<24} " + "  |  ".join(parts))

    print()
    print(f"  ── C2M 误差估算（基于最长边，R=0.1m 圆柱弦高）")
    for n, m in zip(names, metrics):
        L = m["max_edge"]
        chord_mm = L**2 / (8 * 0.1) * 1000
        print(f"  {n:<8}: 最长边={L:.4f}m → 圆柱弦高误差≈{chord_mm:.1f}mm")

    print()
    print(f"  ── 平均边长 vs 目标边长 {t}m")
    for n, m in zip(names, metrics):
        dev = abs(m["mean_edge"] - t) / t * 100
        print(f"  {n:<8}: 平均边长={m['mean_edge']:.4f}m，偏差={dev:.1f}%")

    print("="*W)

    print("\n  ── 综合建议 " + "─"*74)
    best_cov   = names[min(range(len(metrics)), key=lambda i: metrics[i]["cov"])]
    best_zone  = names[max(range(len(metrics)), key=lambda i: metrics[i]["pct_zone"])]
    best_short = names[min(range(len(metrics)), key=lambda i: metrics[i]["pct_short"])]
    best_speed = names[min(range(len(times)), key=lambda i: times[i])]
    print(f"  最均匀（CoV 最小）        : {best_cov}")
    print(f"  最多落在目标区间          : {best_zone}")
    print(f"  碎短边最少                : {best_short}")
    print(f"  最快                      : {best_speed}")
    print("="*W + "\n")


# ─────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("用法: python benchmark_remesh.py <input.glb|input.ply> [target_m]")
        sys.exit(1)

    input_file = sys.argv[1]
    t = float(sys.argv[2]) if len(sys.argv) >= 3 else 0.1
    pipelines  = ["旧版", "前版A", "当前v4", "纯Iso"]

    work_dir = tempfile.mkdtemp(prefix="remesh_bench_")
    try:
        ext = os.path.splitext(input_file)[1].lower()
        if ext in {".glb", ".gltf"}:
            print(f"[准备] 转换 {ext.upper()} → PLY ...")
            t_c = time.time()
            input_ply = os.path.join(work_dir, "input.ply")
            convert_glb_to_ply(input_file, input_ply)
            print(f"[准备] 完成 ({time.time()-t_c:.1f}s)")
        else:
            input_ply = input_file

        ms0 = pymeshlab.MeshSet()
        ms0.load_new_mesh(input_ply)
        if len(ms0) > 1:
            ms0.generate_by_merging_visible_meshes()
        print(f"\n[原始网格] 顶点: {ms0.current_mesh().vertex_number():,}  "
              f"面片: {ms0.current_mesh().face_number():,}")
        print(f"[参数]     目标边长 t = {t}m\n")
        del ms0

        results = []
        for name in pipelines:
            print(f"[{name}] 运行中...")
            out_ply = os.path.join(work_dir, f"{name}.ply")
            try:
                m, elapsed = run_pipeline(input_ply, out_ply, t, name)
                print(f"[{name}] 完成 {elapsed:.1f}s | 面片={m['face_count']:,} "
                      f"| 平均边长={m['mean_edge']:.4f}m | CoV={m['cov']:.4f} "
                      f"| 目标区间={m['pct_zone']:.1f}% | 碎短边={m['pct_short']:.1f}%")
                results.append((name, m, elapsed))
            except Exception as e:
                print(f"[{name}] 失败: {e}")

        if results:
            print_report(results, t)

            out_dir = "/tmp/remesh_benchmark"
            os.makedirs(out_dir, exist_ok=True)
            for name, _, _ in results:
                src = os.path.join(work_dir, f"{name}.ply")
                if os.path.exists(src):
                    shutil.copy(src, os.path.join(out_dir, f"bench_{name}.ply"))
            print(f"[输出] PLY 文件已保存到 {out_dir}/")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
