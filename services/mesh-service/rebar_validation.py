"""Independent analytic acceptance scenes for public rebar algorithms.

This is a diagnostic harness, not training data and not a BIM-label evaluator.
Every point carries a synthetic physical truth label before an algorithm sees it.
"""

from __future__ import annotations

import argparse, hashlib, json, time
from dataclasses import asdict, dataclass, is_dataclass
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


def _prediction_arrays(truth: TruthScene, attrs: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    pred_scene = np.asarray(
        attrs.scene_class
        if attrs.scene_class is not None
        else (attrs.rebar_class > 0).astype(np.uint8) * REBAR,
        dtype=np.uint8,
    )
    pred_instance = np.asarray(attrs.rebar_instance, np.uint32)
    gt_rebar = truth.scene == REBAR
    pred_rebar = (pred_scene == REBAR) | (np.asarray(attrs.rebar_class) > 0)
    return pred_scene, pred_instance, gt_rebar, pred_rebar


def _instance_metrics(
    truth: TruthScene,
    pred_instance: np.ndarray,
    gt_rebar: np.ndarray,
    pred_rebar: np.ndarray,
    valid: np.ndarray,
) -> dict[str, Any]:
    """Score only physical truth IDs; zero is deliberately not an instance."""
    gt_ids = sorted(int(x) for x in np.unique(truth.instance[gt_rebar & valid]) if x)
    pred_ids = sorted(
        int(x)
        for x in np.unique(pred_instance[pred_rebar & valid])
        if x not in (0, 0xFFFFFFFF)
    )
    iou = np.zeros((len(gt_ids), len(pred_ids)))
    for i, ground_truth_id in enumerate(gt_ids):
        ground_truth_mask = (truth.instance == ground_truth_id) & gt_rebar & valid
        for j, predicted_id in enumerate(pred_ids):
            predicted_mask = (pred_instance == predicted_id) & pred_rebar & valid
            union = np.count_nonzero(ground_truth_mask | predicted_mask)
            iou[i, j] = (
                np.count_nonzero(ground_truth_mask & predicted_mask) / union if union else 0
            )
    matched: list[tuple[int, int, float]] = []
    if iou.size:
        rows, columns = linear_sum_assignment(1 - iou)
        matched = [
            (gt_ids[i], pred_ids[j], float(iou[i, j]))
            for i, j in zip(rows, columns)
            if iou[i, j] >= 0.1
        ]
    matched_gt = {ground_truth_id for ground_truth_id, _, _ in matched}
    matched_pred = {predicted_id for _, predicted_id, _ in matched}
    matched_at_half = [item for item in matched if item[2] >= 0.5]
    return {
        "groundTruth": len(gt_ids),
        "predicted": len(pred_ids),
        "matched": len(matched),
        "missed": len(set(gt_ids) - matched_gt),
        "spurious": len(set(pred_ids) - matched_pred),
        "merged": sum(np.count_nonzero(iou[:, j] >= 0.1) > 1 for j in range(len(pred_ids))),
        "split": sum(np.count_nonzero(iou[i] >= 0.1) > 1 for i in range(len(gt_ids))),
        "iou50Recall": len(matched_at_half) / len(gt_ids) if gt_ids else 0.0,
        "iou50Precision": len(matched_at_half) / len(pred_ids) if pred_ids else 0.0,
        "perGroundTruthIoU": {
            str(ground_truth_id): next(
                (score for candidate, _, score in matched if candidate == ground_truth_id), 0.0
            )
            for ground_truth_id in gt_ids
        },
    }


def _semantic_metrics(
    truth: TruthScene,
    pred_scene: np.ndarray,
    gt_rebar: np.ndarray,
    pred_rebar: np.ndarray,
    valid: np.ndarray,
) -> dict[str, Any]:
    true_positive = int(np.count_nonzero(pred_rebar & gt_rebar & valid))
    predicted = int(np.count_nonzero(pred_rebar & valid))
    actual = int(np.count_nonzero(gt_rebar & valid))
    fixture = np.count_nonzero(pred_rebar & (truth.scene == FIXTURE) & valid)
    fixture_total = np.count_nonzero((truth.scene == FIXTURE) & valid)
    per_case: dict[str, dict[str, float]] = {}
    fixture_cases: dict[str, float] = {}
    for name in np.unique(truth.case_labels):
        mask = (truth.case_labels == name) & valid
        ground_truth = gt_rebar & mask
        if np.any(ground_truth):
            per_case[str(name)] = {
                "recall": float(np.count_nonzero(pred_rebar & ground_truth) / np.count_nonzero(ground_truth))
            }
        fixture_mask = mask & (truth.scene == FIXTURE)
        if np.any(fixture_mask):
            fixture_cases[str(name)] = float(
                np.count_nonzero(pred_rebar & fixture_mask) / np.count_nonzero(fixture_mask)
            )
    return {
        "precision": true_positive / predicted if predicted else 0.0,
        "recall": true_positive / actual if actual else 0.0,
        "fixtureLeakage": fixture / fixture_total if fixture_total else 0.0,
        "perCase": per_case,
        "fixtureCaseLeakage": fixture_cases,
        "sceneConfusionMatrix": np.bincount(
            (truth.scene[valid].astype(int) * 5 + pred_scene[valid]), minlength=25
        ).reshape(5, 5).tolist(),
    }


def _score(
    truth: TruthScene,
    attrs: Any,
    elapsed_s: float,
    *,
    semantic_valid: np.ndarray,
    instance_valid: np.ndarray,
) -> dict[str, Any]:
    pred_scene, pred_instance, gt_rebar, pred_rebar = _prediction_arrays(truth, attrs)
    semantic = _semantic_metrics(
        truth, pred_scene, gt_rebar, pred_rebar, semantic_valid
    )
    instances = _instance_metrics(
        truth, pred_instance, gt_rebar, pred_rebar, instance_valid
    )
    hook_mask = (truth.instance == truth.hook_instance) & instance_valid
    hook_pred = pred_instance[hook_mask]
    hook_pred = hook_pred[(hook_pred != 0) & (hook_pred != 0xFFFFFFFF)]
    hook_recall = (
        float(np.count_nonzero(pred_rebar & hook_mask) / np.count_nonzero(hook_mask))
        if np.any(hook_mask)
        else 0.0
    )
    return {
        "semantic": semantic,
        "instances": instances,
        "hookParentConsistency": float(np.max(np.bincount(hook_pred.astype(int))) / len(hook_pred))
        if len(hook_pred)
        else 0.0,
        "hookRecall": hook_recall,
        "rawCounts": {
            "points": len(truth.points),
            "ambiguousExcluded": int(np.count_nonzero(truth.ambiguous & ~semantic_valid)),
            "rebarTruth": int(np.count_nonzero(gt_rebar)),
        },
        "elapsedS": elapsed_s,
    }


def evaluate(truth: TruthScene, attrs: Any, elapsed_s: float) -> dict[str, Any]:
    """Legacy score, retaining its historical ambiguous-row exclusion."""
    valid = ~truth.ambiguous
    return _score(
        truth, attrs, elapsed_s, semantic_valid=valid, instance_valid=valid
    )


def _family_recall(per_case: dict[str, dict[str, float]], names: tuple[str, ...]) -> float:
    values = [per_case[name]["recall"] for name in names if name in per_case]
    return float(sum(values) / len(values)) if values else 0.0



V5_ACCEPTANCE_THRESHOLDS = {
    "basePrecisionMin": 0.99,
    "baseRecallMin": 0.98,
    "hookRecallMin": 0.90,
    "webRecallMin": 0.90,
    "tableSteelDeletionRateMax": 0.01,
    "fixtureSteelDeletionRateMax": 0.01,
    "iou50PrecisionMin": 0.90,
    "iou50RecallMin": 0.90,
    "hookParentConsistencyMin": 0.95,
}


def v5_acceptance(result: dict[str, Any]) -> dict[str, Any]:
    """Apply the fixed gate without changing truth data or its thresholds."""
    semantic = result["semantic"]
    instances = result["instances"]
    family = semantic["familyRecall"]
    values = {
        "basePrecisionMin": family["basePrecision"],
        "baseRecallMin": family["base"],
        "hookRecallMin": family["hook"],
        "webRecallMin": family["web"],
        "tableSteelDeletionRateMax": semantic["tableSteelDeletionRate"],
        "fixtureSteelDeletionRateMax": semantic["fixtureSteelDeletionRate"],
        "iou50PrecisionMin": instances["iou50Precision"],
        "iou50RecallMin": instances["iou50Recall"],
        "hookParentConsistencyMin": result["hookParentConsistency"],
    }
    checks = {
        name: {
            "value": value,
            "threshold": V5_ACCEPTANCE_THRESHOLDS[name],
            "passed": value <= V5_ACCEPTANCE_THRESHOLDS[name]
            if name.endswith("Max")
            else value >= V5_ACCEPTANCE_THRESHOLDS[name],
        }
        for name, value in values.items()
    }
    return {
        "thresholds": V5_ACCEPTANCE_THRESHOLDS,
        "checks": checks,
        "passed": all(check["passed"] for check in checks.values()),
    }


def evaluate_v5(truth: TruthScene, attrs: Any, analysis: Any, elapsed_s: float) -> dict[str, Any]:
    """Score V5 semantics inclusively while keeping ID 0 out of instance truth.

    Ambiguity describes a hard physical observation, never an excuse to remove it
    from semantic recall or precision.  ID 0 has no physical instance identity,
    so it is reported separately and cannot affect IoU matching.
    """
    semantic_valid = np.ones(len(truth.points), dtype=bool)
    # Only positive rebar observations without an instance annotation lack
    # instance truth. Table/fixture/noise observations ARE negative truth:
    # assigning a rod ID to them must count as a false-positive instance.
    instance_valid = ~((truth.scene == REBAR) & (truth.instance == 0))
    result = _score(
        truth,
        attrs,
        elapsed_s,
        semantic_valid=semantic_valid,
        instance_valid=instance_valid,
    )
    pred_scene, _, gt_rebar, _ = _prediction_arrays(truth, attrs)
    base_names = ("baseline", "close_pair_16mm", "close_pair_22mm", "edge_bar", "crossing")
    result["semantic"]["familyRecall"] = {
        "base": _family_recall(result["semantic"]["perCase"], base_names),
        # Precision must include every predicted-rebar point, including fixture
        # and table false positives. A base-only case mask hides those failures.
        "basePrecision": result["semantic"]["precision"],
        "hook": _family_recall(result["semantic"]["perCase"], ("hook_90", "hook_180")),
        "web": _family_recall(result["semantic"]["perCase"], ("inclined_45", "inclined_60")),
    }
    steel_count = int(np.count_nonzero(gt_rebar))
    result["semantic"]["tableSteelDeletionRate"] = (
        float(np.count_nonzero(gt_rebar & (pred_scene == TABLE)) / steel_count)
        if steel_count
        else 0.0
    )
    result["semantic"]["fixtureSteelDeletionRate"] = (
        float(np.count_nonzero(gt_rebar & (pred_scene == FIXTURE)) / steel_count)
        if steel_count
        else 0.0
    )
    result["instanceTruth"] = {
        "truthIdZeroNoInstanceTruthCount": int(np.count_nonzero(gt_rebar & (truth.instance == 0))),
        "ambiguousPointCount": int(np.count_nonzero(truth.ambiguous)),
    }
    scene = np.asarray(attrs.scene_class, dtype=np.uint8)
    result["rawSourceProjection"] = {
        "pointCount": int(len(scene)),
        "sceneClassCounts": dict(zip(
            ("unknown", "table", "rebar", "noise", "fixture"),
            map(int, np.bincount(scene, minlength=5)),
        )),
        "ambiguousPointCount": int(np.count_nonzero(np.asarray(attrs.rebar_flags, dtype=np.uint8) & 2)),
    }
    result["intersectionCount"] = int(len(analysis.data.get("intersections", [])))
    result["acceptance"] = v5_acceptance(result)
    return result


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def source_fingerprint(algorithm: str) -> dict[str, Any]:
    """Stable digest of the V5 implementation recorded before a run starts."""
    root = Path(__file__).resolve().parent
    files = [Path(__file__).resolve()]
    if algorithm == "geometric-v5":
        files.extend(sorted((root / "algorithms" / "rebar_v5").glob("*.py")))
    digest = hashlib.sha256()
    recorded = []
    for path in files:
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        recorded.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})
    return {"sha256": digest.hexdigest(), "files": recorded}


def _with_density(truth: TruthScene, density: float, seed: int) -> TruthScene:
    if not 0 < density <= 1:
        raise ValueError("density must be in (0, 1]")
    if density == 1:
        return truth
    rng = np.random.default_rng(seed)
    keep = rng.random(len(truth.points)) < density
    # Keep at least one point for every physical case, without modifying its truth.
    for name in np.unique(truth.case_labels):
        candidates = np.flatnonzero(truth.case_labels == name)
        if len(candidates) and not np.any(keep[candidates]):
            keep[candidates[0]] = True
    return TruthScene(
        truth.points[keep], truth.scene[keep], truth.instance[keep], truth.ambiguous[keep],
        truth.hook_instance,
        {name: int(np.count_nonzero(truth.case_labels[keep] == name)) for name in truth.cases},
        truth.case_labels[keep],
    )


V5_VALIDATION_CASES = (
    {"id": "parameter-top", "split": "parameter", "seed": 20260905, "topArcs": True, "density": 1.0},
    {"id": "parameter-full", "split": "parameter", "seed": 20260905, "topArcs": False, "density": 1.0},
    {"id": "parameter-top-half-density", "split": "parameter", "seed": 20260905, "topArcs": True, "density": 0.5},
    {"id": "holdout-top", "split": "holdout", "seed": 20261017, "topArcs": True, "density": 1.0},
    {"id": "holdout-full-half-density", "split": "holdout", "seed": 20261017, "topArcs": False, "density": 0.5},
)


def _freeze_configuration(
    algorithm: str, parameters: dict[str, Any] | None = None
) -> tuple[Any, dict[str, Any]]:
    """Normalize once and fingerprint before any truth scene is analysed."""
    from algorithms import REBAR_ALGORITHM_REGISTRY

    if algorithm == "geometric-v4":
        from algorithms.rebar_geometric_v4 import GeometricV4Adapter

        algorithm_impl = GeometricV4Adapter()
    else:
        algorithm_impl = REBAR_ALGORITHM_REGISTRY.get(algorithm)
    normalized = algorithm_impl.normalize_parameters(parameters or {})
    return normalized, {
        "normalizedParameters": _json_value(normalized),
        "sourceFingerprint": source_fingerprint(algorithm),
    }


def run(
    algorithm: str,
    seed: int,
    top_arcs: bool,
    *,
    density: float = 1.0,
    parameters: dict[str, Any] | None = None,
    frozen_configuration: tuple[Any, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from algorithms import REBAR_ALGORITHM_REGISTRY

    # A suite passes its pre-analysis freeze here. Legacy callers retain the
    # previous one-case behavior while still receiving a frozen snapshot.
    normalized_parameters, frozen = frozen_configuration or _freeze_configuration(
        algorithm, parameters
    )
    truth = _with_density(make_truth_scene(seed, top_arcs=top_arcs), density, seed)
    if algorithm == "geometric-v4":
        from algorithms.rebar_geometric_v4 import GeometricV4Adapter

        algo = GeometricV4Adapter()
    else:
        algo = REBAR_ALGORITHM_REGISTRY.get(algorithm)
    t = time.perf_counter()
    analysis = None
    try:
        analysis = algo.analyze(truth.points, normalized_parameters)
        attrs = algo.project_points(truth.points, analysis)
        attrs.validate(len(truth.points))
        elapsed = time.perf_counter() - t
        out = evaluate_v5(truth, attrs, analysis, elapsed) if algorithm == "geometric-v5" else evaluate(truth, attrs, elapsed)
    finally:
        if analysis is not None and hasattr(algo, "close"):
            algo.close(analysis)
    out.update(
        algorithm=algorithm,
        seed=seed,
        topArcs=top_arcs,
        density=density,
        cases=truth.cases,
        **frozen,
    )
    return out

def _load_parameters(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    candidate = Path(value)
    text = candidate.read_text(encoding="utf-8") if candidate.exists() else value
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("--parameters-json must contain a JSON object")
    return parsed


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    """Durably replace a report; a crash cannot leave a half-written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(_json_value(value), sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_v5_suite(
    *,
    parameters: dict[str, Any] | None = None,
    include_holdout: bool = False,
    output_callback: Any | None = None,
) -> dict[str, Any]:
    """Run fixed V5 cases and checkpoint a report before and after every case.

    ``output_callback`` receives the complete journal state. It is optional to
    preserve the programmatic API used by existing callers and tests.
    """
    selected = [
        case for case in V5_VALIDATION_CASES
        if include_holdout or case["split"] != "holdout"
    ]
    frozen_configuration = _freeze_configuration("geometric-v5", parameters)
    result: dict[str, Any] = {
        "schema": "rebar-v5-validation-suite-v1",
        "status": "running",
        "thresholds": V5_ACCEPTANCE_THRESHOLDS,
        "frozen": frozen_configuration[1],
        "plannedCases": selected,
        "pendingCases": [case["id"] for case in selected],
        "holdoutCasesNotRun": [
            case["id"] for case in V5_VALIDATION_CASES
            if case["split"] == "holdout" and not include_holdout
        ],
        "results": [],
        "errors": [],
        "passed": False,
    }
    if output_callback:
        output_callback(result)
    for case in selected:
        try:
            case_result = run(
                "geometric-v5", case["seed"], case["topArcs"],
                density=case["density"], parameters=parameters,
                frozen_configuration=frozen_configuration,
            )
        except Exception as error:
            result["status"] = "failed"
            result["errors"].append({
                "case": case["id"],
                "type": type(error).__name__,
                "message": str(error),
            })
            if output_callback:
                output_callback(result)
            raise
        case_result["validationCase"] = case["id"]
        case_result["validationSplit"] = case["split"]
        result["results"].append(case_result)
        result["pendingCases"].remove(case["id"])
        if output_callback:
            output_callback(result)
    result["status"] = "complete"
    result["casesRun"] = [case["id"] for case in selected]
    result["passed"] = bool(result["results"]) and all(
        case_result["acceptance"]["passed"] for case_result in result["results"]
    )
    if output_callback:
        output_callback(result)
    return result

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--algorithm",
        choices=["geometric-v3", "geometric-v4", "geometric-v5", "both"],
        default="geometric-v3",
    )
    parser.add_argument("--output")
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--full-circle", action="store_true")
    parser.add_argument("--v5-suite", action="store_true", help="run the fixed V5 parameter scenarios")
    parser.add_argument("--include-holdout", action="store_true", help="also execute fixed holdout seeds")
    parser.add_argument("--parameters-json", help="JSON object or path to a JSON object; snapshot before analysis")
    args = parser.parse_args()
    parameters = _load_parameters(args.parameters_json)
    if args.v5_suite:
        if args.algorithm != "geometric-v5":
            parser.error("--v5-suite requires --algorithm geometric-v5")
        output_path = Path(args.output) if args.output else None
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        callback = (lambda report: _atomic_json_write(output_path, report)) if output_path else None
        result = run_v5_suite(
            parameters=parameters,
            include_holdout=args.include_holdout,
            output_callback=callback,
        )
    else:
        names = (
            ["geometric-v3", "geometric-v4", "geometric-v5"]
            if args.algorithm == "both"
            else [args.algorithm]
        )
        result = {
            "schema": "rebar-validation-v1",
            "results": [
                run(name, args.seed, not args.full_circle, parameters=parameters)
                for name in names
            ],
        }
    text = json.dumps(_json_value(result), sort_keys=True)
    if args.output and not args.v5_suite:
        _atomic_json_write(Path(args.output), result)
    print(text)


if __name__ == "__main__":
    main()
