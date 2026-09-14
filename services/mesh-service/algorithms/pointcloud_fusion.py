"""Resolve independent classifier disagreements using measured geometry.

Two classifiers cannot break a 1:1 tie by majority vote. Fixture faces and
observed steel axes decide ties; class-rule support numbers are not probabilities.
"""
import time
from dataclasses import asdict

import numpy as np
from scipy.spatial import cKDTree

from algorithms.normal_geometry_classifier import Parameters, recover_rebar


VERSION = "geometry-projection-fusion-v5-preserved-branch-evidence"
REASONS = {"1": "两路一致", "2": "台面证据", "3": "法向量钢筋证据",
           "4": "投影细长证据", "5": "钢筋轴线连续性恢复", "6": "实体夹具面支持",
           "7": "共享区域钢筋归属", "8": "共享悬浮去噪否决"}
PROTECTION_THRESHOLD = .9
SCORE_LEVELS = {"none": 0., "regionOnly": .25, "axisOnly": .5,
                "singleMeasured": .65, "bothMeasured": 1.}
EVIDENCE_NAMES = {"1": "法向量几何与恢复支持", "2": "投影形态或上下层高度支持",
                  "4": "融合轴线恢复", "8": "区域或高度候选"}


def _boolean_source_array(value, count, name):
    result = np.asarray(value, dtype=bool)
    if result.shape != (count,):
        raise ValueError(f"{name} 必须与源点逐行对应")
    return result


def fuse_classifications(context, *, workers=1, output=None, progress=None):
    started = time.perf_counter()
    progress = progress or (lambda *args: None)
    a, b = context.geometry_class, context.projection_class
    if a is None or b is None or context.classification_cache is None:
        raise ValueError("融合需要同一轮的法向量分类和投影分类")
    cache = context.classification_cache
    projection_cache = context.projection_cache or {}
    shared_scene = getattr(context, "scene_cache", None)
    shared_table_value = getattr(context, "shared_table_mask", None)
    has_shared_scene = shared_scene is not None or shared_table_value is not None
    if "source_steel_evidence" in cache:
        normal_evidence = _boolean_source_array(cache["source_steel_evidence"], len(a),
                                                "classification_cache.source_steel_evidence")
    else:
        normal_evidence = np.zeros(len(a), bool) if has_shared_scene else a == 3
    if "source_steel_evidence" in projection_cache:
        projection_evidence = _boolean_source_array(projection_cache["source_steel_evidence"], len(a),
                                                    "projection_cache.source_steel_evidence")
    else:
        projection_evidence = np.zeros(len(a), bool) if has_shared_scene else b == 3
    shared_table = (np.zeros(len(a), bool) if shared_table_value is None else
                    _boolean_source_array(shared_table_value, len(a), "shared_table_mask"))
    region_value = shared_scene.get("region_owned") if isinstance(shared_scene, dict) else None
    region_owned = (np.zeros(len(a), bool) if region_value is None else
                    _boolean_source_array(region_value, len(a), "scene_cache.region_owned"))
    region_owned = region_owned & ~shared_table
    candidate_value = projection_cache.get('source_steel_candidate', b == 3)
    projection_candidate = _boolean_source_array(candidate_value, len(a), 'projection_cache.source_steel_candidate')
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
    steel_mass = np.bincount(grid.source_to_cell, weights=projection_candidate, minlength=cells)
    population = np.bincount(grid.source_to_cell, minlength=cells)
    projection_seed = steel_mass[residual] >= np.maximum(population[residual] * .6, 3)
    projection_seed &= ~fixture_core[residual]
    projection_seed &= features["connected_support"]
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
    output = {} if output is None else output
    for name, dtype in (("fused_class", np.uint8), ("fused_recovered", np.uint8),
                        ("fused_reason", np.uint8), ("fused_steel_score", np.float32),
                        ("fused_steel_evidence", np.uint8)):
        if name not in output:
            output[name] = np.empty(len(a), dtype)
    labels, recovered, reasons = (output[name] for name in ("fused_class", "fused_recovered", "fused_reason"))
    steel_scores, steel_evidence = output["fused_steel_score"], output["fused_steel_evidence"]
    projection_recovered = projection_cache.get("source_recovered")
    agreement = 0
    recovered_both = 0
    high_confidence = 0
    evidence_counts = np.zeros(16, np.int64)
    for start in range(0, len(a), 262144):
        stop = min(len(a), start+262144)
        aa, bb = a[start:stop], b[start:stop]
        cell = grid.source_to_cell[start:stop]
        core = fixture_core[cell]
        agree = aa == bb
        result = aa.copy()
        reason = np.where(agree, 1, 6).astype(np.uint8)
        shared_table_chunk = shared_table[start:stop]
        region = region_owned[start:stop]
        table = shared_table_chunk | (aa == 1) | ((bb == 1) & (aa != 3))
        result[table] = 1
        reason[table & ~agree] = 2
        normal_steel = (aa == 3) & ~table
        reason[normal_steel & ~agree] = 3
        image_steel = projection_candidate[start:stop] & ~core & ~table & (aa != 3)
        result[image_steel] = 3
        reason[image_steel & ~agree] = 4
        axis_restore = recovered_cells[cell] & ~core & ~table & (result != 3)
        result[axis_restore] = 3
        reason[axis_restore] = 5
        result[region] = 3
        reason[region] = 7
        # Only classifier evidence participates in fusion. Design cloth hits
        # are review candidates for step 05, never an independent class vote.
        noise = ((aa == 4) & ~projection_evidence[start:stop]) | ((bb == 4) & ~normal_evidence[start:stop])
        result[noise] = 4
        reason[noise] = 8
        # The score records measured branch support, not agreement between
        # labels that may have been assigned from the shared spatial scene.
        spatial_candidate = region.copy()
        if 'source_steel_candidate' in projection_cache:
            spatial_candidate |= projection_candidate[start:stop] & ~projection_evidence[start:stop]
        evidence = (normal_evidence[start:stop].astype(np.uint8) |
                    (projection_evidence[start:stop].astype(np.uint8) << 1) |
                    (axis_restore.astype(np.uint8) << 2) |
                    (spatial_candidate.astype(np.uint8) << 3))
        evidence[result != 3] = 0
        direct = evidence & 3
        score = np.zeros(stop-start, np.float32)
        score[direct != 0] = SCORE_LEVELS["singleMeasured"]
        score[direct == 3] = SCORE_LEVELS["bothMeasured"]
        score[(direct == 0) & ((evidence & 4) != 0)] = SCORE_LEVELS["axisOnly"]
        score[(direct == 0) & ((evidence & 4) == 0) & ((evidence & 8) != 0)] = SCORE_LEVELS["regionOnly"]
        changed = (result == 3) & (bb != 3)
        if projection_recovered is not None:
            changed |= (result == 3) & projection_recovered[start:stop]
        labels[start:stop], recovered[start:stop], reasons[start:stop] = result, changed, reason
        steel_scores[start:stop], steel_evidence[start:stop] = score, evidence
        agreement += int(agree.sum())
        recovered_both += int(np.count_nonzero((aa == 2) & (bb == 2) & (result == 3)))
        high_confidence += int(np.count_nonzero(score >= PROTECTION_THRESHOLD))
        evidence_counts += np.bincount(evidence, minlength=16)
    timings["sourceMappingS"] = time.perf_counter()-t0
    counts = np.bincount(labels, minlength=5)
    report = {"version": VERSION, "pointCount": len(a), "elapsedS": time.perf_counter()-started,
              "counts": dict(zip(("table", "fixture", "rebar", "noise"), map(int, counts[1:5]))),
              "noiseClass": 4,
              "agreement": {"agreePoints": agreement, "disagreePoints": len(a)-agreement},
              "recovery": {"recoveredFromProjectionPoints": int(recovered.sum()),
                           "recoveredBothPoints": recovered_both, "passes": 1},
              "evidence": {"fixtureCoreCells": int(fixture_core.sum()), "steelAxisSeeds": len(seeds),
                           "additionalProjectionSeeds": int(len(seeds)-len(cache["strong_bars"]))},
              "reasonNames": REASONS, "timings": timings, "recoveryParameters": asdict(params),
              "score": {"meaning": "保留分类与评分证据分开；B上下层高度规则计一路支持，仅共享分区不计独立支持",
                        "protectionThreshold": PROTECTION_THRESHOLD, "lowScoreThreshold": .5, "levels": SCORE_LEVELS,
                        "branchLabelsAreIndependent": False, "branchRetentionPreserved": True,
                        "projectionHeightCountsAsEvidence": True,
                        "denoisingPolicy": "高分提供强支持；第05步允许独立三维与多视图反证推翻高分；不能仅凭低分删除",
                        "highConfidencePoints": high_confidence, "evidenceNames": EVIDENCE_NAMES,
                        "evidenceCounts": {str(i): int(n) for i, n in enumerate(evidence_counts) if n}},
              "policy": "agreement + broad fixture veto + steel evidence + bounded 3D axis recovery; no probability voting"}
    context.fused_class, context.fused_recovered, context.fused_reason = labels, recovered, reasons
    context.fused_steel_score, context.fused_steel_evidence = steel_scores, steel_evidence
    context.fusion_cache = {"fixture_core": fixture_core, "axis_recovered_cells": recovered_cells,
                            "axis_seed_ids": seeds, "axis_tree": tree}
    return report
