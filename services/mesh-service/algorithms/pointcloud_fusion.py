"""Resolve independent classifier disagreements using measured geometry.

Two classifiers cannot break a 1:1 tie by majority vote. Fixture faces and
observed steel axes decide ties; class-rule support numbers are not probabilities.
"""
import time
from dataclasses import asdict

import numpy as np
from scipy.spatial import cKDTree

from algorithms.normal_geometry_classifier import Parameters, recover_rebar


VERSION = "geometry-projection-fusion-v1"
REASONS = {"1": "两路一致", "2": "台面证据", "3": "法向量钢筋证据",
           "4": "投影细长证据", "5": "钢筋轴线连续性恢复", "6": "实体夹具面支持"}


def fuse_classifications(context, *, workers=1, output=None, progress=None):
    started = time.perf_counter()
    progress = progress or (lambda *args: None)
    a, b = context.geometry_class, context.projection_class
    if a is None or b is None or context.classification_cache is None:
        raise ValueError("融合需要同一轮的法向量分类和投影分类")
    cache = context.classification_cache
    grid, residual = cache["grid"], cache["residual_ids"]
    features = cache["features"]
    cells = len(grid.points)
    timings = {}
    t0 = time.perf_counter()
    progress("第 3 步：两路分歧与夹具实体证据", 0, len(a))
    # A known broad surface includes its physical rim, not just its exact
    # planar seed. A's fixture remainder alone is never a veto.
    fixture_core = np.zeros(cells, bool)
    fixture_core[residual] = cache["broad_fixture"]
    steel_mass = np.bincount(grid.source_to_cell, weights=(b == 3), minlength=cells)
    population = np.bincount(grid.source_to_cell, minlength=cells)
    projection_seed = steel_mass[residual] >= np.maximum(population[residual] * .6, 3)
    projection_seed &= ~fixture_core[residual]
    projection_seed &= ((features["linearity"] > .72) & (features["width"] < .025)
                        & (features["width"] > .002) & (features["length"] > .020)
                        & (features["axis_alignment"] > .7) & (features["support_count"] >= 12))
    # The unchanged original cylinder seeds and independent image-supported
    # axes vote once. Newly recovered points cannot grow another generation.
    seeds = np.union1d(cache["strong_bars"], np.flatnonzero(projection_seed))
    points = grid.points[residual]
    tree = cache["rebar_tree"] if np.array_equal(seeds, cache["strong_bars"]) else (cKDTree(points[seeds]) if len(seeds) else None)
    timings["evidenceS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    progress("第 3 步：补回交点与轴线上的断段", 0, len(residual))
    params = Parameters()
    recovered_residual = recover_rebar(points, features, seeds, tree,
        (cache["cell_labels"][residual] != 1) & ~fixture_core[residual], params, workers)
    recovered_cells = np.zeros(cells, bool)
    recovered_cells[residual] = recovered_residual
    timings["axisRecoveryS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    output = output if output is not None else {name: np.empty(len(a), np.uint8)
        for name in ("fused_class", "fused_recovered", "fused_reason")}
    labels, recovered, reasons = (output[name] for name in ("fused_class", "fused_recovered", "fused_reason"))
    projection_recovered = context.projection_cache.get("source_recovered")
    agreement = 0
    recovered_both = 0
    for start in range(0, len(a), 262144):
        stop = min(len(a), start+262144)
        aa, bb = a[start:stop], b[start:stop]
        cell = grid.source_to_cell[start:stop]
        core = fixture_core[cell]
        agree = aa == bb
        result = aa.copy()
        reason = np.where(agree, 1, 6).astype(np.uint8)
        table = (aa == 1) | ((bb == 1) & (aa != 3))
        result[table] = 1
        reason[table & ~agree] = 2
        normal_steel = (aa == 3) & ~table
        reason[normal_steel & ~agree] = 3
        image_steel = (bb == 3) & ~core & ~table & (aa != 3)
        result[image_steel] = 3
        reason[image_steel & ~agree] = 4
        axis_restore = recovered_cells[cell] & ~core & ~table & (result != 3)
        result[axis_restore] = 3
        reason[axis_restore] = 5
        changed = (result == 3) & (bb != 3)
        if projection_recovered is not None:
            changed |= (result == 3) & projection_recovered[start:stop]
        labels[start:stop], recovered[start:stop], reasons[start:stop] = result, changed, reason
        agreement += int(agree.sum())
        recovered_both += int(np.count_nonzero((aa == 2) & (bb == 2) & (result == 3)))
    timings["sourceMappingS"] = time.perf_counter()-t0
    counts = np.bincount(labels, minlength=4)
    report = {"version": VERSION, "pointCount": len(a), "elapsedS": time.perf_counter()-started,
              "counts": dict(zip(("table", "fixture", "rebar"), map(int, counts[1:4]))),
              "agreement": {"agreePoints": agreement, "disagreePoints": len(a)-agreement},
              "recovery": {"recoveredFromProjectionPoints": int(recovered.sum()),
                           "recoveredBothPoints": recovered_both, "passes": 1},
              "evidence": {"fixtureCoreCells": int(fixture_core.sum()), "steelAxisSeeds": len(seeds),
                           "additionalProjectionSeeds": int(len(seeds)-len(cache["strong_bars"]))},
              "reasonNames": REASONS, "timings": timings, "recoveryParameters": asdict(params),
              "policy": "agreement + broad fixture veto + steel evidence + bounded 3D axis recovery; no probability voting"}
    context.fused_class, context.fused_recovered, context.fused_reason = labels, recovered, reasons
    context.fusion_cache = {"fixture_core": fixture_core, "axis_recovered_cells": recovered_cells,
                            "axis_seed_ids": seeds, "axis_tree": tree}
    return report
