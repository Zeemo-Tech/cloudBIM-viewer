"""Independent analytic acceptance scenes for public rebar algorithms.

This is a diagnostic harness, not training data and not a BIM-label evaluator.
Every point carries a synthetic physical truth label before an algorithm sees it.
"""

from __future__ import annotations

import argparse, json, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np
from scipy.optimize import linear_sum_assignment


TABLE, REBAR, FIXTURE, CLUTTER = 1, 2, 4, 0


@dataclass
class TruthScene:
    points: np.ndarray
    scene: np.ndarray
    instance: np.ndarray
    ambiguous: np.ndarray
    hook_instance: int
    cases: dict[str, int]
    case_labels: np.ndarray


def _basis(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = a / np.linalg.norm(a)
    other = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(a, other)
    u /= np.linalg.norm(u)
    if np.cross(a, u)[2] < 0:
        u = -u
    return u, np.cross(a, u)


def _rod(
    rng: np.random.Generator, a, b, radius, count, *, top_arcs: bool
) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    axis = b - a
    u, v = _basis(axis)
    t = rng.uniform(0, 1, count)
    angles = rng.uniform(0, np.pi if top_arcs else 2 * np.pi, count)
    return (
        a
        + t[:, None] * axis
        + radius * (np.cos(angles)[:, None] * u + np.sin(angles)[:, None] * v)
    )


def _square_tube(
    rng: np.random.Generator, a, b, half_width: float, count: int
) -> np.ndarray:
    """Four planar faces: deliberately not a cylindrical fixture."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    axis = b - a
    u, v = _basis(axis)
    t = rng.uniform(0, 1, count)
    face = rng.integers(0, 4, count)
    q = rng.uniform(-half_width, half_width, count)
    offsets = np.where(
        (face[:, None] % 2) == 0,
        np.where(face[:, None] == 0, half_width, -half_width) * u + q[:, None] * v,
        q[:, None] * u + np.where(face[:, None] == 1, half_width, -half_width) * v,
    )
    return a + t[:, None] * axis + offsets


def _hook_surface(
    rng: np.random.Generator,
    center,
    radius,
    start,
    delta,
    tube_radius,
    count,
    z: float,
    *,
    top_arcs: bool = True,
) -> np.ndarray:
    theta = rng.uniform(start, start + delta, count)
    centre = np.c_[
        center[0] + radius * np.cos(theta),
        center[1] + radius * np.sin(theta),
        np.full(count, z),
    ]
    radial = np.c_[np.cos(theta), np.sin(theta), np.zeros(count)]
    phi = rng.uniform(0, np.pi if top_arcs else 2 * np.pi, count)
    return centre + tube_radius * (
        np.cos(phi)[:, None] * radial + np.sin(phi)[:, None] * np.array([0.0, 0.0, 1.0])
    )


def make_truth_scene(seed: int = 20260905, *, top_arcs: bool = True) -> TruthScene:
    rng = np.random.default_rng(seed)
    points = []
    scenes = []
    ids = []
    ambiguous = []
    cases = {}
    labels = []

    def add(x, sc, ident, name, amb=False):
        points.append(x)
        scenes.extend([sc] * len(x))
        ids.extend([ident] * len(x))
        ambiguous.extend([amb] * len(x))
        labels.extend([name] * len(x))
        cases[name] = cases.get(name, 0) + len(x)

    # Dense, dominant table plane.
    table = np.c_[
        rng.uniform(-2, 2, 12000),
        rng.uniform(-2, 2, 12000),
        rng.normal(0, 0.001, 12000),
    ]
    add(table, TABLE, 0, "table")

    def bar(a, b, r, i, name, n=900):
        add(_rod(rng, a, b, r, n, top_arcs=top_arcs), REBAR, i, name)

    bar((-1.5, -0.8, 0.025), (1.5, -0.8, 0.025), 0.008, 1, "baseline")
    bar((0.2, -1.5, 0.048), (0.2, 1.5, 0.048), 0.008, 2, "baseline")
    # Two 8mm-diameter pairs: centre distances exactly 16mm and 22mm.
    bar((-1.5, -0.55, 0.026), (1.5, -0.55, 0.026), 0.004, 3, "close_pair_16mm")
    bar((-1.5, -0.534, 0.026), (1.5, -0.534, 0.026), 0.004, 4, "close_pair_16mm")
    bar((-1.5, -0.45, 0.026), (1.5, -0.45, 0.026), 0.004, 11, "close_pair_22mm")
    bar((-1.5, -0.428, 0.026), (1.5, -0.428, 0.026), 0.004, 12, "close_pair_22mm")
    # Tangent straight + 90° then 180° swept tube, all physical ID 5.
    bar((-1.4, 0.4, 0.03), (0.5, 0.4, 0.03), 0.008, 5, "hook_90", 700)
    add(
        _hook_surface(
            rng,
            (0.5, 0.65),
            0.25,
            -np.pi / 2,
            np.pi / 2,
            0.008,
            600,
            0.03,
            top_arcs=top_arcs,
        ),
        REBAR,
        5,
        "hook_90",
    )
    add(
        _hook_surface(
            rng, (0.5, 0.65), 0.25, 0, np.pi, 0.008, 900, 0.03, top_arcs=top_arcs
        ),
        REBAR,
        5,
        "hook_180",
    )
    # Practical 120mm-scale 45° and 60° braces.
    bar((-0.12, 1.12, 0.04), (0.0, 1.12, 0.16), 0.006, 6, "inclined_45", 600)
    bar(
        (0.12, 1.32, 0.04),
        (0.12 + 0.12 / np.sqrt(3), 1.32, 0.16),
        0.006,
        13,
        "inclined_60",
        600,
    )
    # Hollow square tube and a broad perimeter skin are fixtures, not rods.
    add(
        _square_tube(rng, (-1.8, -0.26, 0.08), (-0.8, -0.26, 0.08), 0.02, 1500),
        FIXTURE,
        0,
        "fixture_square_tube",
    )
    add(
        np.c_[
            rng.uniform(0.7, 1.7, 1000),
            np.full(1000, 0.28) + rng.normal(0, 0.0003, 1000),
            rng.uniform(0.015, 0.11, 1000),
        ],
        FIXTURE,
        0,
        "fixture",
    )
    bar((-1.8, -0.32, 0.06), (-0.8, -0.32, 0.06), 0.008, 7, "edge_bar")
    # Separate the layer/vertical crossing fixture from the close-pair cases,
    # so solid cylinders do not physically interpenetrate other truth bars.
    bar((0.95, 0.8, 0.04), (1.75, 1.6, 0.04), 0.008, 8, "crossing", 750)
    bar((0.95, 1.6, 0.09), (1.75, 0.8, 0.09), 0.008, 9, "crossing", 750)
    bar((1.35, 1.2, 0.098), (1.35, 1.2, 0.42), 0.008, 10, "crossing", 450)
    # Only a deliberately tiny physical intersection is excluded from instance scoring.
    add(
        rng.normal([0.2, -0.8, 0.04], [0.004, 0.004, 0.004], (24, 3)),
        REBAR,
        0,
        "declared_tiny_crossing",
        True,
    )
    clutter = np.c_[
        rng.uniform(-2, 2, 900), rng.uniform(-2, 2, 900), rng.uniform(0.02, 0.25, 900)
    ]
    add(clutter, CLUTTER, 0, "clutter")
    return TruthScene(
        np.concatenate(points, axis=0),
        np.asarray(scenes, np.uint8),
        np.asarray(ids, np.uint32),
        np.asarray(ambiguous, bool),
        5,
        cases,
        np.asarray(labels),
    )


def evaluate(truth: TruthScene, attrs: Any, elapsed_s: float) -> dict[str, Any]:
    pred_scene = np.asarray(
        attrs.scene_class
        if attrs.scene_class is not None
        else (attrs.rebar_class > 0).astype(np.uint8) * 2
    )
    pred_instance = np.asarray(attrs.rebar_instance, np.uint32)
    gt_rebar = truth.scene == REBAR
    pred_rebar = (pred_scene == REBAR) | (np.asarray(attrs.rebar_class) > 0)
    valid = ~truth.ambiguous
    tp = int(np.count_nonzero(pred_rebar & gt_rebar & valid))
    pp = int(np.count_nonzero(pred_rebar & valid))
    actual = int(np.count_nonzero(gt_rebar & valid))
    precision = tp / pp if pp else 0.0
    recall = tp / actual if actual else 0.0
    gt_ids = sorted(int(x) for x in np.unique(truth.instance[(gt_rebar) & valid]) if x)
    pred_ids = sorted(
        int(x)
        for x in np.unique(pred_instance[(pred_rebar) & valid])
        if x not in (0, 0xFFFFFFFF)
    )
    iou = np.zeros((len(gt_ids), len(pred_ids)))
    for i, g in enumerate(gt_ids):
        gm = (truth.instance == g) & gt_rebar & valid
        for j, p in enumerate(pred_ids):
            pm = (pred_instance == p) & pred_rebar & valid
            union = np.count_nonzero(gm | pm)
            iou[i, j] = np.count_nonzero(gm & pm) / union if union else 0
    matched = []
    if iou.size:
        rows, cols = linear_sum_assignment(1 - iou)
        matched = [
            (gt_ids[i], pred_ids[j], float(iou[i, j]))
            for i, j in zip(rows, cols)
            if iou[i, j] >= 0.1
        ]
    matched_gt = {g for g, _, _ in matched}
    matched_pred = {p for _, p, _ in matched}
    hook_mask = (truth.instance == truth.hook_instance) & valid
    hook_pred = pred_instance[hook_mask]
    hook_pred = hook_pred[(hook_pred != 0) & (hook_pred != 0xFFFFFFFF)]
    fixture = np.count_nonzero(pred_rebar & (truth.scene == FIXTURE) & valid)
    fixture_total = np.count_nonzero((truth.scene == FIXTURE) & valid)
    per_case = {}
    fixture_cases = {}
    for name in np.unique(truth.case_labels):
        mask = (truth.case_labels == name) & valid
        g = gt_rebar & mask
        p = pred_rebar & mask
        hit = np.count_nonzero(g & p)
        if np.any(g):
            per_case[str(name)] = {
                "recall": hit / int(np.count_nonzero(g)),
            }
        fixture_mask = mask & (truth.scene == FIXTURE)
        if np.any(fixture_mask):
            fixture_cases[str(name)] = float(
                np.count_nonzero(pred_rebar & fixture_mask)
                / np.count_nonzero(fixture_mask)
            )
    hook_recall = float(
        np.count_nonzero(pred_rebar & hook_mask) / np.count_nonzero(hook_mask)
    )
    return {
        "semantic": {
            "precision": precision,
            "recall": recall,
            "fixtureLeakage": fixture / fixture_total if fixture_total else 0.0,
            "perCase": per_case,
            "fixtureCaseLeakage": fixture_cases,
            "sceneConfusionMatrix": np.bincount(
                (truth.scene[valid].astype(int) * 5 + pred_scene[valid]), minlength=25
            )
            .reshape(5, 5)
            .tolist(),
        },
        "instances": {
            "groundTruth": len(gt_ids),
            "predicted": len(pred_ids),
            "matched": len(matched),
            "missed": len(set(gt_ids) - matched_gt),
            "spurious": len(set(pred_ids) - matched_pred),
            "merged": sum(
                np.count_nonzero(iou[:, j] >= 0.1) > 1 for j in range(len(pred_ids))
            ),
            "split": sum(
                np.count_nonzero(iou[i] >= 0.1) > 1 for i in range(len(gt_ids))
            ),
            "iou50Recall": len([x for x in matched if x[2] >= 0.5]) / len(gt_ids)
            if gt_ids
            else 0.0,
            "iou50Precision": len([x for x in matched if x[2] >= 0.5]) / len(pred_ids)
            if pred_ids
            else 0.0,
            "perGroundTruthIoU": {
                str(g): next(
                    (score for candidate, _, score in matched if candidate == g), 0.0
                )
                for g in gt_ids
            },
        },
        "hookParentConsistency": float(
            np.max(np.bincount(hook_pred.astype(int))) / len(hook_pred)
        )
        if len(hook_pred)
        else 0.0,
        "hookRecall": hook_recall,
        "rawCounts": {
            "points": len(truth.points),
            "ambiguousExcluded": int(np.count_nonzero(truth.ambiguous)),
            "rebarTruth": int(np.count_nonzero(gt_rebar)),
        },
        "elapsedS": elapsed_s,
    }


def run(algorithm: str, seed: int, top_arcs: bool) -> dict[str, Any]:
    from algorithms import REBAR_ALGORITHM_REGISTRY

    truth = make_truth_scene(seed, top_arcs=top_arcs)
    if algorithm == "geometric-v4":
        from algorithms.rebar_geometric_v4 import GeometricV4Adapter

        algo = GeometricV4Adapter()
    else:
        algo = REBAR_ALGORITHM_REGISTRY.get(algorithm)
    t = time.perf_counter()
    analysis = algo.analyze(truth.points, algo.normalize_parameters({}))
    attrs = algo.project_points(truth.points, analysis)
    attrs.validate(len(truth.points))
    out = evaluate(truth, attrs, time.perf_counter() - t)
    out.update(algorithm=algorithm, seed=seed, topArcs=top_arcs, cases=truth.cases)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm",
        choices=["geometric-v3", "geometric-v4", "both"],
        default="geometric-v3",
    )
    parser.add_argument("--output")
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--full-circle", action="store_true")
    args = parser.parse_args()
    names = (
        ["geometric-v3", "geometric-v4"]
        if args.algorithm == "both"
        else [args.algorithm]
    )
    result = {
        "schema": "rebar-validation-v1",
        "results": [run(name, args.seed, not args.full_circle) for name in names],
    }
    text = json.dumps(result, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
