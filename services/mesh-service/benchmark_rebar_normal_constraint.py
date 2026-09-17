"""Repeatable synthetic timing for the bounded normal-constraint query."""
from __future__ import annotations

import json
from time import perf_counter

import numpy as np
from scipy.spatial import cKDTree

from rebar_deviation import constrained_nearest


def main() -> None:
    rng = np.random.default_rng(20260913)
    scan = np.column_stack((rng.uniform(0, 20, 100_000), rng.normal(0, .01, 100_000), rng.normal(0, .01, 100_000)))
    vertices = np.column_stack((np.linspace(0, 20, 100_000), np.zeros(100_000), np.zeros(100_000)))
    tangents = np.tile([1., 0., 0.], (len(vertices), 1))
    normals = np.tile([0., 1., 0.], (len(vertices), 1))
    started = perf_counter()
    values, _indices, _axial, accepted = constrained_nearest(
        cKDTree(scan), scan, vertices, tangents, normals, k=32,
        max_angle_deg=30, half_space_only=False, fallback_mode="unknown")
    print(json.dumps({"seed": 20260913, "scanPoints": len(scan), "vertices": len(vertices),
                      "k": 32, "seconds": round(perf_counter() - started, 6),
                      "known": int(np.isfinite(values).sum()), "constrained": int(accepted.sum())}))


if __name__ == "__main__":
    main()
