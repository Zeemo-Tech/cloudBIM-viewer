"""Conservative, pure evidence refinement for geometric-v4.

This module deliberately does not fabricate centreline geometry.  It only
retains bounded fixture evidence, admits already-observed auxiliary primitives,
and records when an observed hook-fragment continuation is uniquely supported.
The caller remains responsible for tracing accepted primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from .rebar_v4_geometry import LinePrimitive


@dataclass(frozen=True)
class V4EvidenceRefinement:
    fixture_surfaces: tuple[dict[str, Any], ...]
    primitives: tuple[LinePrimitive, ...]
    hook_join_overrides: tuple[tuple[int, int], ...]
    diagnostics: dict[str, int]


def _segment_distance(points: np.ndarray, primitive: LinePrimitive) -> np.ndarray:
    axis = primitive.end - primitive.start
    length2 = float(axis @ axis)
    if length2 <= 1e-12:
        return np.linalg.norm(points - primitive.start, axis=1)
    t = np.clip(((points - primitive.start) @ axis) / length2, 0.0, 1.0)
    return np.linalg.norm(points - (primitive.start + t[:, None] * axis), axis=1)


def _has_support(points: np.ndarray, primitive: LinePrimitive, radius: float) -> bool:
    return int(np.count_nonzero(_segment_distance(points, primitive) <= radius)) >= 6


def _complete_surfaces(
    surfaces: tuple[dict[str, Any], ...], planar_support: np.ndarray, max_radius: float
) -> tuple[tuple[dict[str, Any], ...], int]:
    """Expand a trusted face only into immediately observed coplanar support."""
    completed: list[dict[str, Any]] = []
    count = 0
    for surface in surfaces:
        extent = np.asarray(surface.get("halfExtent", ()), dtype=np.float64)
        origin = np.asarray(surface.get("origin", ()), dtype=np.float64)
        axes = np.asarray(surface.get("axes", ()), dtype=np.float64)
        normal = np.asarray(surface.get("normal", ()), dtype=np.float64)
        trusted = int(surface.get("supportCount", 0)) >= 14 and float(surface.get("coverage", 0)) >= 0.70
        if not (trusted and extent.shape == (2,) and origin.shape == normal.shape == (3,) and axes.shape == (2, 3)):
            completed.append(surface)
            continue
        local = (planar_support - origin) @ axes.T
        coplanar = np.abs((planar_support - origin) @ normal) <= float(surface["distance"])
        limit = extent + 0.016
        nearby = coplanar & np.all(np.abs(local) <= limit, axis=1)
        cells = np.floor(local[nearby] / 0.006).astype(np.int64)
        has_filled_patch = (
            len(cells) >= 9
            and len(np.unique(cells[:, 0])) >= 3
            and len(np.unique(cells[:, 1])) >= 3
        )
        if not has_filled_patch:
            completed.append(surface)
            continue
        observed_extent = np.max(np.abs(local[nearby]), axis=0) + 0.003
        new_extent = np.maximum(extent, np.minimum(observed_extent, limit))
        if np.any(new_extent > extent + 1e-8):
            surface = {**surface, "halfExtent": new_extent.tolist(), "completed": True}
            count += 1
        completed.append(surface)
    return tuple(completed), count


def _covered_by_surface(primitive: LinePrimitive, surfaces: tuple[dict[str, Any], ...]) -> bool:
    for surface in surfaces:
        origin, axes, normal = (np.asarray(surface[name], dtype=np.float64) for name in ("origin", "axes", "normal"))
        extent = np.asarray(surface["halfExtent"], dtype=np.float64)
        endpoints = np.vstack((primitive.start, primitive.end))
        local = (endpoints - origin) @ axes.T
        if np.all(np.abs((endpoints - origin) @ normal) <= float(surface["distance"])) and np.all(np.abs(local) <= extent):
            return True
    return False


def _same_observed_bar(first: LinePrimitive, second: LinePrimitive) -> bool:
    if abs(float(first.tangent @ second.tangent)) < np.cos(np.deg2rad(8.0)):
        return False
    sample = first.start + np.linspace(0.0, 1.0, 7)[:, None] * (first.end - first.start)
    tolerance = max(0.004, 0.6 * max(first.radius, second.radius))
    return float(np.mean(_segment_distance(sample, second) <= tolerance)) >= 0.6


def _hook_pairs(primitives: tuple[LinePrimitive, ...], support: np.ndarray) -> tuple[list[tuple[int, int]], int]:
    """Count only unique endpoint continuations with observed local support.

    A third segment through the endpoint is a crossing/junction and blocks a
    join, even if its own endpoint is elsewhere.  This keeps welded crossings
    outside the recovery path.
    """
    candidates: list[tuple[int, int, int, int, np.ndarray, np.ndarray]] = []
    radius = 0.015
    support_cache: dict[int, bool] = {}

    def supported(index: int) -> bool:
        if index not in support_cache:
            support_cache[index] = _has_support(support, primitives[index], radius)
        return support_cache[index]

    for i, first in enumerate(primitives):
        for j, second in enumerate(primitives[i + 1 :], i + 1):
            endpoints = ((0, first.start, first.tangent), (1, first.end, -first.tangent))
            other = ((0, second.start, second.tangent), (1, second.end, -second.tangent))
            for endpoint, point, outward in endpoints:
                for other_endpoint, target, target_outward in other:
                    gap = float(np.linalg.norm(target - point))
                    if not 1e-8 < gap <= 0.04:
                        continue
                    turn = np.rad2deg(np.arccos(np.clip(float((-outward) @ target_outward), -1, 1)))
                    if not 8 <= turn <= 55:
                        continue
                    if supported(i) and supported(j):
                        candidates.append((i, j, endpoint, other_endpoint, point, target))
    endpoint_counts: dict[int, int] = {}
    for i, j, endpoint, other_endpoint, _, _ in candidates:
        for key in (2 * i + endpoint, 2 * j + other_endpoint):
            endpoint_counts[key] = endpoint_counts.get(key, 0) + 1
    accepted: list[tuple[int, int]] = []
    for i, j, endpoint, other_endpoint, point, target in candidates:
        left_key, right_key = 2 * i + endpoint, 2 * j + other_endpoint
        # A multi-fragment hook legitimately uses both ends of its middle
        # primitive.  Ambiguity is therefore judged per endpoint, not per
        # primitive.
        if endpoint_counts[left_key] != 1 or endpoint_counts[right_key] != 1:
            continue
        # Any unrelated observed segment crossing this local endpoint makes the
        # continuation ambiguous, irrespective of endpoint pairing order.
        probes = np.vstack((point, (point + target) / 2.0, target))
        if any(
            k not in (i, j) and float(np.min(_segment_distance(probes, item))) <= 0.006
            for k, item in enumerate(primitives)
        ):
            continue
        if any(
            k not in (i, j)
            and min(
                float(np.linalg.norm(point - item.start)),
                float(np.linalg.norm(point - item.end)),
                float(np.linalg.norm(target - item.start)),
                float(np.linalg.norm(target - item.end)),
            ) <= 0.04
            for k, item in enumerate(primitives)
        ):
            continue
        accepted.append(tuple(sorted((left_key, right_key))))
    return accepted, len(candidates)


def refine_v4_evidence(
    fixture_surfaces: Iterable[dict[str, Any]],
    primitives: Iterable[LinePrimitive],
    support_points: np.ndarray,
    *,
    max_radius: float,
    recovery_candidates: Iterable[LinePrimitive] = (),
    planar_support: np.ndarray | None = None,
) -> V4EvidenceRefinement:
    """Return only evidence that passes local, geometry-only checks.

    Recovery candidates must already be observed line primitives.  A candidate
    is admitted solely for sparse, high-linearity auxiliary steel with six
    local supporting points; no missing endpoints or curved geometry is made.
    """
    surfaces = tuple(fixture_surfaces)
    support = np.asarray(support_points, dtype=np.float64).reshape((-1, 3))
    planar = support if planar_support is None else np.asarray(planar_support, dtype=np.float64).reshape((-1, 3))
    kept_surfaces, fixture_completed = _complete_surfaces(surfaces, planar, max_radius)
    original_primitives = tuple(primitives)
    accepted = [item for item in original_primitives if not _covered_by_surface(item, kept_surfaces)]
    fixture_suppressed = len(original_primitives) - len(accepted)
    recovered = 0
    unsupported = 0
    duplicate_recovery = 0
    for item in recovery_candidates:
        length = float(np.linalg.norm(item.end - item.start))
        high_quality = item.point_count >= 6 and item.score >= 0.90 and length >= 0.08
        duplicate = any(_same_observed_bar(item, existing) for existing in accepted)
        if duplicate:
            duplicate_recovery += 1
        elif high_quality and _has_support(support, item, max_radius + 0.003):
            accepted.append(item)
            recovered += 1
        else:
            unsupported += 1
    primitive_tuple = tuple(accepted)
    hook_overrides, hook_candidates = _hook_pairs(primitive_tuple, support)
    return V4EvidenceRefinement(
        kept_surfaces,
        primitive_tuple,
        tuple(hook_overrides),
        {
            "fixtureInput": len(surfaces),
            "fixtureAccepted": len(kept_surfaces),
            "fixtureCompleted": fixture_completed,
            "fixtureSuppressedPrimitives": fixture_suppressed,
            "diagonalRecoveryAccepted": recovered,
            "diagonalRecoveryRejectedUnsupported": unsupported,
            "diagonalRecoveryRejectedDuplicate": duplicate_recovery,
            "hookJoinCandidates": hook_candidates,
            "hookJoinAccepted": len(hook_overrides),
        },
    )
