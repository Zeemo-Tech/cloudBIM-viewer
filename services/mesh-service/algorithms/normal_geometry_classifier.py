"""Step 02: table, rebar and remaining fixture, with whole-fixture ownership.

Local planes are only fixture anchors, never a requirement that every fixture
point be planar. Edges, corners and unclaimed points belong to the same fixture
class. Every source row receives one of the three scene classes.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict, dataclass, replace
import time

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from threadpoolctl import threadpool_limits

from algorithms.pointcloud_normals import PointCloudContext, available_workers
from algorithms.normal_cylinder_recovery import recover_cylindrical_cells

VERSION = "normal-geometry-v9-preserved-retention"
CLASS_NAMES = {"1": "台面", "2": "夹具（含方管）", "3": "钢筋"}
COLORS = {"1": "#64748b", "2": "#f59e0b", "3": "#2dd4bf"}


@dataclass(frozen=True)
class Parameters:
    voxel_size: float = .003
    surface_radius: float = .008
    support_radius: float = .035
    max_neighbors: int = 96
    plane_distance: float = .0012
    min_plane_width: float = .012
    min_plane_area: float = .0003
    min_plane_fill: float = .25
    min_narrow_plane_fill: float = .50
    max_bar_width: float = .025
    min_bar_support: float = .020
    recovery_reach: float = .050
    recovery_fixture_width: float = .018
    connectivity_radius: float = .0054


def matrix6(values):
    result = np.empty((*values.shape[:-1], 3, 3), dtype=values.dtype)
    result[..., 0, 0], result[..., 1, 1], result[..., 2, 2] = values[..., 0], values[..., 1], values[..., 2]
    result[..., 0, 1] = result[..., 1, 0] = values[..., 3]
    result[..., 0, 2] = result[..., 2, 0] = values[..., 4]
    result[..., 1, 2] = result[..., 2, 1] = values[..., 5]
    return result


def projector6(normals):
    x, y, z = normals.T
    return np.column_stack((x*x, y*y, z*z, x*y, x*z, y*z))


@dataclass
class SupportGrid:
    points: np.ndarray
    projectors: np.ndarray
    counts: np.ndarray
    valid_counts: np.ndarray
    source_to_cell: np.ndarray
    origin: np.ndarray
    tree: cKDTree | None = None
    non_table_counts: np.ndarray | None = None


def aggregate_grid(context, size):
    xyz = context.positions
    origin = xyz.min(axis=0)
    cells = np.floor((xyz - origin) / size).astype(np.int64)
    dims = cells.max(axis=0).astype(object) + 1
    if int(dims[0]) * int(dims[1]) * int(dims[2]) >= np.iinfo(np.int64).max:
        raise ValueError("点云空间范围超过分类格网编码范围")
    keys = (cells[:, 0] * int(dims[1]) + cells[:, 1]) * int(dims[2]) + cells[:, 2]
    del cells
    unique, inverse = np.unique(keys, return_inverse=True)
    del keys
    count = len(unique)
    if count >= np.iinfo(np.int32).max:
        raise ValueError("分类格网数量超过 int32 索引范围")
    inverse = inverse.astype(np.int32)
    counts = np.bincount(inverse, minlength=count)
    points = np.empty((count, 3), np.float64)
    for column in range(3):
        points[:, column] = np.bincount(inverse, weights=xyz[:, column]-origin[column], minlength=count) / counts
    # Cell mass is equal in neighborhood geometry so acquisition density does
    # not dominate. Every source normal contributes to its cell projector.
    valid = context.normal_valid.astype(bool)
    active_mass = None
    if getattr(context, "shared_table_mask", None) is not None:
        active = ~context.shared_table_mask.astype(bool)
        active_mass = np.bincount(inverse, weights=active, minlength=count)
        mixed = (active_mass > 0) & (active_mass < counts)
        rows = np.flatnonzero(active & mixed[inverse])
        if len(rows):
            for column in range(3):
                centers = np.bincount(inverse[rows], weights=xyz[rows, column]-origin[column], minlength=count)
                np.divide(centers, active_mass, out=points[:, column], where=mixed)
        valid &= active
    valid_counts = np.bincount(inverse, weights=valid, minlength=count)
    q = np.empty((count, 6), np.float32)
    for column, (a, b) in enumerate(((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))):
        weight = context.normals[:, a] * context.normals[:, b] * valid
        q[:, column] = np.bincount(inverse, weights=weight, minlength=count) / np.maximum(valid_counts, 1)
    return SupportGrid(points, q, counts, valid_counts, inverse, origin, non_table_counts=active_mass)


def dominant_plane(grid, params, workers=1):
    """Find a broad dominant plane by projector axis, offset mode and refitting.

    Does not assume world Z is vertical. A small plate alone is not called a
    tabletop: require area and both in-plane dimensions at scene scale.
    """
    eigen = np.empty((len(grid.points), 3), np.float32)
    vectors = np.empty((len(grid.points), 3, 3), np.float32)
    def solve(start):
        stop = min(start+32768, len(grid.points))
        eigen[start:stop], vectors[start:stop] = np.linalg.eigh(matrix6(grid.projectors[start:stop]))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(solve, range(0, len(grid.points), 32768)))
    coherent = (eigen[:, 2] >= .97) & (grid.valid_counts >= 3)
    if coherent.sum() < 30:
        return None, eigen[:, 2]
    _, pooled_axes = np.linalg.eigh(matrix6(grid.projectors[coherent].mean(axis=0)))
    normal = pooled_axes[:, 2].astype(np.float64)
    axes = vectors[:, :, 2]
    alignment = np.abs(axes @ normal) > .97
    values = grid.points @ normal
    support = coherent & alignment
    if support.sum() < 30:
        return None, eigen[:, 2]
    step = params.plane_distance
    if np.ptp(values[support]) < step:
        offset = float(np.median(values[support]))
    else:
        histogram, edges = np.histogram(values[support], bins=max(3, int(np.ptp(values[support]) / step) + 1))
        best = int(np.argmax(np.convolve(histogram, [1, 1, 1], mode="same")))
        offset = (edges[best] + edges[best+1]) / 2
    for _ in range(3):
        mask = coherent & (np.abs(axes @ normal) > .97) & (np.abs(grid.points @ normal - offset) < step * 2.5)
        if mask.sum() < 30:
            return None, eigen[:, 2]
        sample = grid.points[mask]
        center = sample.mean(axis=0)
        delta = sample-center
        vals, vecs = np.linalg.eigh(delta.T @ delta / len(delta))
        normal = vecs[:, 0]
        offset = float(center @ normal)
    width = 4 * np.sqrt(np.maximum(vals, 0))
    area = int(mask.sum()) * params.voxel_size**2
    if width[1] < .20 or width[2] < .40 or area < .05:
        return None, eigen[:, 2]
    return {"normal": normal, "offset": offset, "widths": width, "supportCells": int(mask.sum()), "area": area}, eigen[:, 2]


def _local_features(points, q, tree, params, workers, progress):
    count = len(points)
    result = {"surface_coherence": np.zeros(count, np.float32), "surface_flatness": np.ones(count, np.float32),
              "surface_normal": np.zeros((count, 3), np.float32), "normal_eigen": np.zeros((count, 3), np.float32),
              "linearity": np.zeros(count, np.float32), "width": np.zeros(count, np.float32),
              "length": np.zeros(count, np.float32), "axis_alignment": np.zeros(count, np.float32),
              "axis": np.zeros((count, 3), np.float32), "axis_center": np.zeros((count, 3), np.float64),
              "support_count": np.zeros(count, np.int32)}
    if not count:
        return result
    padded_points = np.vstack((points, np.zeros((1, 3))))
    padded_q = np.vstack((q, np.zeros((1, 6), np.float32)))
    chunk_size = 2048

    def process(start):
        stop = min(start+chunk_size, count)
        distances, ids = tree.query(points[start:stop], k=min(params.max_neighbors, count),
                                     distance_upper_bound=params.support_radius, workers=1)
        if ids.ndim == 1:
            ids, distances = ids[:, None], distances[:, None]
        delta = padded_points[ids] - points[start:stop, None]
        _, center_axes = np.linalg.eigh(matrix6(q[start:stop]))
        center_normal = center_axes[:, :, 2]
        plane_offset = np.abs(np.einsum("bki,bi->bk", delta, center_normal))
        normal_alignment = np.einsum("bkc,bc->bk", padded_q[ids],
                                     projector6(center_normal)*[1, 1, 1, 2, 2, 2])
        for small in (True, False):
            mask = np.isfinite(distances)
            if small:
                mask &= distances <= params.surface_radius
                # A spatial ball mixes opposite thin-wall returns and crossing
                # surfaces. Estimate the surface inside the central tangent
                # slab; keep the wide-scale shape neighborhood unchanged.
                mask &= plane_offset <= params.plane_distance*1.5
                # At a corner, perpendicular-face points can lie inside the
                # same tangent slab. Exclude their normals from this face's
                # estimate so its seed width does not depend on voxel phase.
                mask &= normal_alignment > .75
            weights = mask.astype(np.float64)
            mass = np.maximum(weights.sum(axis=1), 1)
            mean = np.einsum("bk,bki->bi", weights, delta) / mass[:, None]
            covariance = np.einsum("bk,bki,bkj->bij", weights, delta, delta) / mass[:, None, None]
            covariance -= mean[:, :, None] * mean[:, None, :]
            pe, pv = np.linalg.eigh(covariance)
            pe = np.maximum(pe, 0)
            avgq = np.einsum("bk,bki->bi", weights, padded_q[ids]) / mass[:, None]
            ne, nv = np.linalg.eigh(matrix6(avgq))
            if small:
                result["surface_coherence"][start:stop] = np.where(mass >= 6, ne[:, 2], 0)
                result["surface_flatness"][start:stop] = pe[:, 0] / np.maximum(pe[:, 1], 1.e-20)
                result["surface_normal"][start:stop] = nv[:, :, 2]
            else:
                result["normal_eigen"][start:stop] = ne
                result["linearity"][start:stop] = 1 - pe[:, 1] / np.maximum(pe[:, 2], 1.e-20)
                result["width"][start:stop] = 4 * np.sqrt(pe[:, 1])
                result["length"][start:stop] = 4 * np.sqrt(pe[:, 2])
                result["axis"][start:stop] = pv[:, :, 2]
                result["axis_center"][start:stop] = mean + points[start:stop]
                result["axis_alignment"][start:stop] = np.abs(np.sum(pv[:, :, 2] * nv[:, :, 0], axis=1))
                result["support_count"][start:stop] = weights.sum(axis=1)
        return stop-start

    starts = iter(range(0, count, chunk_size))
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for _ in range(workers*2):
            start = next(starts, None)
            if start is not None:
                pending.add(pool.submit(process, start))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                completed += future.result()
                start = next(starts, None)
                if start is not None:
                    pending.add(pool.submit(process, start))
            progress("第 2 步：多尺度法向量 / 粗细分析", completed, count)
    return result


def planar_patches(points, features, params, workers, candidates=None):
    seed = (features["surface_coherence"] > .92) & (features["surface_flatness"] < .08)
    if candidates is not None:
        seed &= candidates
    seed_ids = np.flatnonzero(seed)
    assignment = np.full(len(points), -1, np.int32)
    if len(seed_ids) < 12:
        return assignment, [], 0
    sp = points[seed_ids]
    normals = features["surface_normal"][seed_ids]
    tree = cKDTree(sp)
    distance, neighbor = tree.query(sp, k=min(17, len(sp)), distance_upper_bound=params.voxel_size*4, workers=workers)
    row = np.repeat(np.arange(len(sp)), neighbor.shape[1])
    col = neighbor.ravel()
    good = (col < len(sp)) & (col > row)
    row, col = row[good], col[good]
    delta = sp[col] - sp[row]
    good = (np.abs(np.einsum("ij,ij->i", normals[row], normals[col])) > .94)
    good &= np.abs(np.einsum("ij,ij->i", delta, normals[row])) < params.plane_distance*2
    good &= np.abs(np.einsum("ij,ij->i", delta, normals[col])) < params.plane_distance*2
    graph = coo_matrix((np.ones(good.sum(), np.uint8), (row[good], col[good])), shape=(len(sp), len(sp))).tocsr()
    component_count, labels = connected_components(graph, directed=False)
    sizes = np.bincount(labels, minlength=component_count)
    means = np.column_stack([np.bincount(labels, weights=sp[:, a], minlength=component_count) / sizes for a in range(3)])
    cov6 = np.column_stack([np.bincount(labels, weights=sp[:, a]*sp[:, b], minlength=component_count) / sizes
                           - means[:, a]*means[:, b] for a, b in ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))])
    eigen, axes = np.linalg.eigh(matrix6(cov6))
    widths = 4 * np.sqrt(np.maximum(eigen, 0))
    accepted = (sizes >= 12) & (widths[:, 1] >= params.min_plane_width) & (widths[:, 2] >= .03)
    accepted &= sizes * params.voxel_size**2 >= params.min_plane_area
    # Coplanar wire/truss strips can span a large plane while its interior is
    # empty. Require 2-D support, not just the extent of a connected outline.
    fill = sizes * params.voxel_size**2 / np.maximum(widths[:, 1]*widths[:, 2], 1.e-20)
    accepted &= fill >= params.min_plane_fill
    accepted &= np.sqrt(np.maximum(eigen[:, 0], 0)) < params.plane_distance*2
    components = np.flatnonzero(accepted)
    compact = np.full(component_count, -1, np.int32)
    compact[components] = np.arange(len(components))
    assignment[seed_ids] = compact[labels]
    patches = [{"normal": axes[c, :, 0], "long_axis": axes[c, :, 2], "center": means[c],
                "widths": widths[c], "cells": int(sizes[c]), "fillRatio": float(fill[c])} for c in components]
    # Search several supported faces: the closest seed can belong to the wrong
    # side of a corner. Fill local gaps without reserving an undecided edge band.
    accepted_ids = np.flatnonzero(assignment >= 0)
    if len(accepted_ids):
        nearest = cKDTree(points[accepted_ids])
        query_rows = np.flatnonzero(candidates) if candidates is not None else np.arange(len(points))
        d, idx = nearest.query(points[query_rows], k=min(4, len(accepted_ids)), distance_upper_bound=params.voxel_size*6, workers=workers)
        if d.ndim == 1:
            d, idx = d[:, None], idx[:, None]
        patch_ids = assignment[accepted_ids[np.minimum(idx, len(accepted_ids)-1)]]
        patch_normals = np.array([p["normal"] for p in patches])
        centers = np.array([p["center"] for p in patches])
        projected = np.abs(np.einsum("bki,bki->bk", points[query_rows, None]-centers[patch_ids], patch_normals[patch_ids]))
        aligned = np.abs(np.einsum("bi,bki->bk", features["surface_normal"][query_rows], patch_normals[patch_ids]))
        keep = np.isfinite(d) & (projected < params.plane_distance*3) & (aligned > .65)
        cost = np.where(keep, projected + .05*d, np.inf)
        best = np.argmin(cost, axis=1)
        fill = np.flatnonzero((assignment[query_rows] < 0) & np.isfinite(cost[np.arange(len(query_rows)), best]))
        assignment[query_rows[fill]] = patch_ids[fill, best[fill]]
    return assignment, patches, component_count


def normal_fourfold(context, grid, points, axes, workers, progress):
    """Distinguish continuous circular normals from two orthogonal face axes.

    |E[(nu+i*nv)^4]| / E[|nu+i*nv|^4] is invariant to normal sign,
    axial sign and basis rotation. An ideal square tube scores 1; a sampled
    full/half cylinder scores near 0. Use the cached ORIGINAL KD tree for raw
    normals here: averaging projectors in mixed corner voxels loses this signal.
    """
    result = np.ones(len(points), np.float32)
    count = len(context.positions)
    def chunk(start):
        stop = min(start+1024, len(points))
        distance, ids = context.tree.query(points[start:stop]+grid.origin, k=min(128, count),
                                           distance_upper_bound=.035, workers=1)
        keep = (ids < count) & np.isfinite(distance)
        ids = np.minimum(ids, count-1)
        keep &= context.normal_valid[ids].astype(bool)
        normals = context.normals[ids]
        axis = axes[start:stop]
        ref = np.eye(3)[np.argmin(np.abs(axis), axis=1)]
        u = np.cross(axis, ref)
        u /= np.maximum(np.linalg.norm(u, axis=1)[:, None], 1.e-12)
        v = np.cross(axis, u)
        a = np.einsum("bki,bi->bk", normals, u)
        b = np.einsum("bki,bi->bk", normals, v)
        a2, b2 = a*a, b*b
        real = np.sum((a2*a2-6*a2*b2+b2*b2)*keep, axis=1)
        imag = np.sum(4*a*b*(a2-b2)*keep, axis=1)
        denom = np.sum((a2+b2)**2*keep, axis=1)
        score = np.hypot(real, imag) / np.maximum(denom, 1.e-20)
        result[start:stop] = np.where((keep.sum(axis=1) >= 12) & (denom > 1.e-8), np.clip(score, 0, 1), 1.)
        return stop-start
    starts = iter(range(0, len(points), 1024))
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for _ in range(workers*2):
            start = next(starts, None)
            if start is not None:
                pending.add(pool.submit(chunk, start))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                done_count += future.result()
                start = next(starts, None)
                if start is not None:
                    pending.add(pool.submit(chunk, start))
            progress("第 2 步：圆截面 / 方截面法向量检验", done_count, len(points))
    return result


def connected_bar_support(points, tree, params):
    """Reject small detached islands before they can borrow a nearby bar's PCA.

    Use occupied cells, never raw return counts: hundreds of repeated returns
    on a tiny blob do not make it an observed length of steel. Connectivity is
    measured at the voxel scale, independently of the 35/50 mm feature/search
    radii. No global straight-axis requirement is imposed on bent/crossed bars.
    """
    if not len(points):
        return np.zeros(0, bool), np.zeros(0, np.int32), np.zeros(0, bool)
    pairs = tree.query_pairs(params.connectivity_radius, output_type='ndarray')
    graph = coo_matrix((np.ones(len(pairs), np.uint8), (pairs[:, 0], pairs[:, 1])),
                       shape=(len(points), len(points))).tocsr()
    count, labels = connected_components(graph, directed=False)
    low = np.full((count, 3), np.inf)
    high = np.full((count, 3), -np.inf)
    np.minimum.at(low, labels, points)
    np.maximum.at(high, labels, points)
    sizes = np.bincount(labels, minlength=count)
    # A scan can leave a short strip of an otherwise valid bar disconnected.
    # Such strips may use axial votes across a gap, but must have their OWN
    # narrow, elongated shape. Tiny round clumps cannot borrow this evidence.
    local = points - low[labels]
    means = np.column_stack([np.bincount(labels, weights=local[:, a]) / sizes for a in range(3)])
    cov6 = np.column_stack([np.bincount(labels, weights=local[:, a]*local[:, b]) / sizes
                           - means[:, a]*means[:, b] for a, b in
                           ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))])
    widths = 4*np.sqrt(np.maximum(np.linalg.eigvalsh(matrix6(cov6)), 0))
    fragments = ((sizes >= 6) & (widths[:, 2] >= params.min_bar_support*.5)
                 & (widths[:, 2] >= 1.5*widths[:, 1]) & (widths[:, 1] < params.max_bar_width))
    supported = ((sizes >= 6) & (np.linalg.norm(high-low, axis=1) >= params.min_bar_support)) | fragments
    return supported[labels], labels, fragments[labels]


def recover_rebar(points, features, strong_bars, tree, candidates, params, workers):
    """One bounded pass along measured steel axes, including caps and junctions.

    Only pre-existing strong cylinder seeds vote. Recovered points never seed
    another pass, so a junction cannot recursively flood an adjacent fixture.
    The candidate's own normal/linearity is intentionally not required.
    """
    recovered = np.zeros(len(points), bool)
    ids = np.flatnonzero(candidates)
    if tree is None or not len(ids):
        return recovered
    axes = features["axis"][strong_bars]
    centers = features["axis_center"][strong_bars]
    radii = np.clip(features["width"][strong_bars]*.45 + params.voxel_size*.5,
                    params.voxel_size, params.max_bar_width*.5)

    def chunk(start):
        rows = ids[start:start+4096]
        distance, neighbor = tree.query(points[rows], k=min(128, len(strong_bars)),
                                         distance_upper_bound=params.recovery_reach, workers=1)
        if neighbor.ndim == 1:
            distance, neighbor = distance[:, None], neighbor[:, None]
        valid = np.isfinite(distance)
        neighbor = np.minimum(neighbor, len(strong_bars)-1)
        components = features.get("support_component")
        if components is not None:
            # A detached object must establish its own elongated support.
            # Only measured narrow fragments may bridge a scan gap.
            same = components[rows, None] == components[strong_bars[neighbor]]
            fragments = features["fragment_support"]
            bridge = ((fragments[rows, None] | fragments[strong_bars[neighbor]])
                      & features["connected_support"][rows, None])
            valid &= same | bridge
        axis = axes[neighbor]
        delta = points[rows, None] - centers[neighbor]
        along = np.einsum("bki,bki->bk", delta, axis)
        perpendicular = np.linalg.norm(delta-along[:, :, None]*axis, axis=2)
        valid &= perpendicular <= radii[neighbor]
        # Require several cells along a supported narrow corridor. At a
        # crossing their votes may come from more than one steel direction.
        cost = np.where(valid, perpendicular/radii[neighbor] + .2*distance/params.recovery_reach, np.inf)
        best = np.argmin(cost, axis=1)
        reference = axis[np.arange(len(rows)), best]
        longitudinal = np.einsum("bki,bi->bk", points[strong_bars[neighbor]]-points[rows, None], reference)
        span = (np.max(np.where(valid, longitudinal, -np.inf), axis=1)
                - np.min(np.where(valid, longitudinal, np.inf), axis=1))
        good = (valid.sum(axis=1) >= 3) & (span >= params.voxel_size*2)
        recovered[rows[good]] = True

    starts = iter(range(0, len(ids), 4096))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for _ in range(workers*2):
            start = next(starts, None)
            if start is not None:
                pending.add(pool.submit(chunk, start))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
                start = next(starts, None)
                if start is not None:
                    pending.add(pool.submit(chunk, start))
    return recovered


def fixture_patch_ownership(patches, params):
    """Keep modest faces and matching perpendicular tube faces as one fixture."""
    if not patches:
        return np.zeros(0, bool)
    widths = np.array([p["widths"] for p in patches])
    blocked = widths[:, 1] > params.recovery_fixture_width
    centers = np.array([p["center"] for p in patches])
    normals = np.array([p["normal"] for p in patches])
    axes = np.array([p["long_axis"] for p in patches])
    # Work in bounded blocks: patch count can grow on much larger scenes.
    for start in range(0, len(patches), 128):
        stop = min(start+128, len(patches))
        delta = centers[start:stop, None] - centers[None]
        along = np.einsum("bki,bi->bk", delta, axes[start:stop])
        across = np.linalg.norm(delta-along[:, :, None]*axes[start:stop, None], axis=2)
        same_tube = (np.abs(normals[start:stop] @ normals.T) < .25)
        same_tube &= np.abs(axes[start:stop] @ axes.T) > .94
        same_tube &= across < params.max_bar_width*2
        same_tube &= np.abs(along) < (widths[start:stop, 2, None]+widths[None, :, 2])*.5
        blocked[start:stop] |= same_tube.any(axis=1)
    return blocked


def classify_geometry(context: PointCloudContext, *, workers=None, params=None, output=None, progress=None):
    params = params or Parameters()
    workers = available_workers() if workers is None else workers
    if type(workers) is not int or not 1 <= workers <= available_workers():
        raise ValueError("Invalid worker count")
    if context.normals is None or context.normal_valid is None:
        raise ValueError("第 2 步需要同一轮第 1 步的法向量")
    progress = progress or (lambda *args: None)
    started = time.perf_counter()
    timings = {}
    with threadpool_limits(limits=1):
        t0 = time.perf_counter()
        progress("第 2 步：汇总全量法向量", 0, len(context.positions))
        grid = aggregate_grid(context, params.voxel_size)
        timings["aggregateS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        scene = getattr(context, "scene_cache", None)
        shared_table = getattr(context, "shared_table_mask", None)
        owned = scene["region_owned"] if scene is not None else np.zeros(len(context.positions), bool)
        cell_owned = np.bincount(grid.source_to_cell, weights=owned, minlength=len(grid.points)) == grid.counts
        if shared_table is None:
            table, coherence = dominant_plane(grid, params, workers)
        else:
            table, coherence = None, None
        # Scene prior requested for this dataset: everything outside the table
        # and detected rebar is fixture, even without a valid planar patch.
        cell_labels = np.full(len(grid.points), 2, np.uint8)
        if table is not None:
            near = np.abs(grid.points @ table["normal"]-table["offset"]) < params.plane_distance*1.5
            cell_labels[near & (coherence > .93)] = 1
        if shared_table is not None:
            cell_labels[grid.non_table_counts == 0] = 1
        timings["tableS"] = time.perf_counter()-t0
        residual_ids = np.flatnonzero(cell_labels != 1)
        points = grid.points[residual_ids]
        q = grid.projectors[residual_ids]
        t0 = time.perf_counter()
        grid.tree = cKDTree(points)
        timings["supportTreeS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        features = _local_features(points, q, grid.tree, params, workers, progress)
        timings["featuresS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress("第 2 步：检查孤立点团与钢筋断段", 0, len(points))
        connected_support, features["support_component"], features["fragment_support"] = connected_bar_support(points, grid.tree, params)
        features["connected_support"] = connected_support
        timings["connectivityS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        fixture_candidates = ~cell_owned[residual_ids]
        patch_ids, patches, component_count = planar_patches(points, features, params, workers,
            **({"candidates": fixture_candidates} if scene is not None else {}))
        cell_labels[residual_ids[patch_ids >= 0]] = 2
        table_extensions = np.zeros(len(patches), bool)
        if table is not None and patches:
            # A slightly warped tabletop can form another broad local plane.
            # Require proximity, orientation AND broad 2-D extent; narrow rails
            # above the table do not qualify merely because they are parallel.
            for i, patch in enumerate(patches):
                table_extensions[i] = (patch["widths"][1] > .08 and patch["cells"]*params.voxel_size**2 > .008
                    and abs(patch["normal"] @ table["normal"]) > .995
                    and abs(patch["center"] @ table["normal"]-table["offset"]) < params.plane_distance*4)
                patch["kind"] = "table-extension" if table_extensions[i] else "fixture-plane"
            extended = (patch_ids >= 0) & table_extensions[np.maximum(patch_ids, 0)]
            cell_labels[residual_ids[extended]] = 1
        timings["planesS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        fixture_owned = (patch_ids >= 0) & (cell_labels[residual_ids] == 2)
        fixture_anchors = np.flatnonzero(fixture_owned)
        fixture_tree = None
        broad_fixture = np.zeros(len(points), bool)
        wide_fixture = np.zeros(len(points), bool)
        owned_patches = fixture_patch_ownership(patches, params)
        tube_faces = fixture_patch_ownership(patches, replace(params, recovery_fixture_width=np.inf))
        if len(fixture_anchors):
            fixture_tree = cKDTree(points[fixture_anchors])
            fixture_rows = np.flatnonzero(fixture_candidates)
            distance = np.full(len(points), np.inf)
            owner = np.full(len(points), len(fixture_anchors), np.int64)
            distance[fixture_rows], owner[fixture_rows] = fixture_tree.query(points[fixture_rows], k=1,
                                             distance_upper_bound=params.voxel_size*2, workers=workers)
            # Include the physical rim and corner volume directly in fixture.
            # No normal check or separate edge/plane output categories.
            fixture_owned |= np.isfinite(distance)
            owner_patch = patch_ids[fixture_anchors[np.minimum(owner, len(fixture_anchors)-1)]]
            # Include each supported fixture's rim even beside a steel seed.
            broad_fixture = np.isfinite(distance) & owned_patches[owner_patch]
            widths = np.array([patch['widths'][1] for patch in patches])
            wide_fixture = np.isfinite(distance) & ((widths[owner_patch] >= params.max_bar_width) | tube_faces[owner_patch])
            # Sparse tangent strips from adjacent bars can form a low-fill
            # apparent plane. A densely filled broad face is stronger evidence:
            # protect its nearby tube body from a local corner-circle fit.
            solid_faces = np.array([patch['fillRatio'] >= .7 and patch['widths'][1] >= params.recovery_fixture_width
                                    for patch in patches])
            solid_anchors = fixture_anchors[solid_faces[patch_ids[fixture_anchors]]]
            if len(solid_anchors):
                solid_tree = cKDTree(points[solid_anchors])
                solid_distance, _ = solid_tree.query(points[fixture_rows], k=1,
                    distance_upper_bound=params.max_bar_width, workers=workers)
                wide_fixture[fixture_rows] |= np.isfinite(solid_distance)
        timings["fixtureOwnershipS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        angular_ids = np.flatnonzero((patch_ids < 0) & ~fixture_owned)
        features["normal_fourfold"] = np.ones(len(points), np.float32)
        features["normal_fourfold"][angular_ids] = normal_fourfold(context, grid, points[angular_ids], features["axis"][angular_ids], workers, progress)
        timings["normalAngularS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        ne = features["normal_eigen"]
        bars = (connected_support & (patch_ids < 0) & ~fixture_owned & (features["support_count"] >= 12) & (ne[:, 1] > .04)
                & (ne[:, 0] < .075) & (features["axis_alignment"] > .80) & (features["linearity"] > .65)
                & (features["width"] < params.max_bar_width) & (features["width"] > .002)
                & (features["length"] > params.min_bar_support) & (features["normal_fourfold"] < .82))
        strong_bars = np.flatnonzero(bars)
        tree = None
        if len(strong_bars):
            tree = cKDTree(points[strong_bars])
            distance, index = tree.query(points, k=1, distance_upper_bound=params.voxel_size*4, workers=workers)
            candidate = np.flatnonzero(np.isfinite(distance) & (patch_ids < 0) & ~fixture_owned & connected_support)
            seed = strong_bars[index[candidate]]
            axis = features["axis"][seed]
            delta = points[candidate]-points[seed]
            perpendicular = np.linalg.norm(delta-np.einsum("ij,ij->i", delta, axis)[:, None]*axis, axis=1)
            good = ((perpendicular < params.max_bar_width*.5)
                    & ((features["support_component"][candidate] == features["support_component"][seed])
                       | features["fragment_support"][candidate] | features["fragment_support"][seed]))
            bars[candidate[good]] = True
            features["axis"][candidate[good]] = axis[good]
        cell_labels[residual_ids[bars]] = 3
        timings["barsS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress("第 2 步：补回钢筋末端与交叉处", 0, len(points))
        recovered = recover_rebar(points, features, strong_bars, tree,
            (cell_labels[residual_ids] == 2) & ~broad_fixture, params, workers)
        cell_labels[residual_ids[recovered]] = 3
        t0_sparse = time.perf_counter()
        sparse_recovered = np.zeros(len(points), bool)
        weak_faces = np.array([patch['widths'][1] < params.max_bar_width
                               and patch['fillRatio'] < params.min_narrow_plane_fill for patch in patches])
        if weak_faces.any():
            # Revisit narrow, mostly empty apparent faces after the original
            # pass. Preserve every existing steel label: changing the whole
            # seed population up front can crowd valid votes out of k-NN.
            remaining = fixture_anchors[~weak_faces[patch_ids[fixture_anchors]]]
            real_owned = np.zeros(len(points), bool)
            if len(remaining):
                fixture_rows = np.flatnonzero(fixture_candidates)
                real_distance, _ = cKDTree(points[remaining]).query(points[fixture_rows], k=1,
                    distance_upper_bound=params.voxel_size*2, workers=workers)
                real_owned[fixture_rows] = np.isfinite(real_distance)
            eligible = ((cell_labels[residual_ids] == 2) & ~real_owned & connected_support
                        & (features['support_count'] >= 12) & (ne[:, 1] > .04) & (ne[:, 0] < .075)
                        & (features['axis_alignment'] > .80) & (features['linearity'] > .65)
                        & (features['width'] < params.max_bar_width) & (features['width'] > .002)
                        & (features['length'] > params.min_bar_support))
            rows = np.flatnonzero(eligible)
            angular = normal_fourfold(context, grid, points[rows], features['axis'][rows], workers, progress)
            eligible[rows[angular >= .82]] = False
            if eligible.any():
                strong_bars = np.union1d(strong_bars, np.flatnonzero(eligible))
                tree = cKDTree(points[strong_bars])
                sparse_recovered = eligible | recover_rebar(points, features, strong_bars, tree,
                    (cell_labels[residual_ids] == 2) & ~real_owned, params, workers)
                cell_labels[residual_ids[sparse_recovered]] = 3
                recovered |= sparse_recovered
                broad_fixture[sparse_recovered] = False
        timings['sparseFaceRecoveryS'] = time.perf_counter()-t0_sparse
        t0_cylinder = time.perf_counter()
        progress("第 2 步：复核紧邻双筋与腹杆圆截面", 0, len(points))
        candidates = ((cell_labels[residual_ids] == 2) & connected_support
                      & (ne[:, 0] < .10) & (ne[:, 1] > .06))
        possible_continuation = recover_rebar(points, features, strong_bars, tree,
            candidates & wide_fixture, params, workers)
        candidates &= ~wide_fixture | possible_continuation
        cylinders, cylinder_axes, cylinder_centers, cylinder_radii, anchored_cylinders = recover_cylindrical_cells(
            points, q, grid.tree, features, candidates, strong_bars=strong_bars, protected=wide_fixture,
            voxel_size=params.voxel_size, workers=workers)
        circular_ids = np.flatnonzero(cylinders)
        circular_fourfold = normal_fourfold(context, grid, points[circular_ids], cylinder_axes[circular_ids], workers, progress)
        cylinders[circular_ids[circular_fourfold >= np.where(anchored_cylinders[circular_ids], .95, .75)]] = False
        cell_labels[residual_ids[cylinders]] = 3
        recovered |= cylinders
        # A narrow apparent plane can consist of tangent strips from TWO
        # cylinders. Revoke that plane veto only on verified circular shells.
        broad_fixture[cylinders] = False
        features['axis'][cylinders] = cylinder_axes[cylinders]
        features['axis_center'][cylinders] = cylinder_centers[cylinders]
        features['width'][cylinders] = cylinder_radii[cylinders]/.45
        if cylinders.any():
            strong_bars = np.union1d(strong_bars, np.flatnonzero(cylinders))
            tree = cKDTree(points[strong_bars])
        timings['crowdedCylinderS'] = time.perf_counter()-t0_cylinder
        recovered_cells = np.zeros(len(grid.points), bool)
        recovered_cells[residual_ids] = recovered
        timings["recoveryS"] = time.perf_counter()-t0
        t0 = time.perf_counter()
        count = len(context.positions)
        output = output or {"geometry_class": np.empty(count, np.uint8), "geometry_support": np.empty(count, np.float32)}
        labels, scores = output["geometry_class"], output["geometry_support"]
        recovery_flags = output.get("geometry_recovered")
        if recovery_flags is None:
            recovery_flags = np.empty(count, np.uint8)
        source_steel_evidence = np.zeros(count, bool)
        for start in range(0, count, 262144):
            stop = min(count, start+262144)
            cell = grid.source_to_cell[start:stop]
            result = cell_labels[cell].copy()
            source_steel_evidence[start:stop] = result == 3
            if shared_table is not None:
                # Preserve the branch's established recall policy. The
                # separate evidence array above controls scoring, not labels.
                result[owned[start:stop]] = 3
                result[shared_table[start:stop].astype(bool)] = 1
                source_steel_evidence[start:stop] &= ~shared_table[start:stop].astype(bool)
            # Use the supported cell's class directly, including its boundary
            # and contact points. Do not reject them again by individual normal.
            labels[start:stop] = result
            scores[start:stop] = np.choose(result, [0., .95, .5, .7])
            # A recovered support voxel can also contain source tabletop rows.
            # Recovery describes the final source label, after shared ownership.
            recovery_flags[start:stop] = recovered_cells[cell] & (result == 3)
        timings["sourceProjectionS"] = time.perf_counter()-t0
    counts = np.bincount(labels, minlength=4)
    context.geometry_class, context.geometry_support = labels, scores
    context.geometry_recovered = recovery_flags
    context.classification_cache = {"grid": grid, "residual_ids": residual_ids, "features": features,
                                    "source_steel_evidence": source_steel_evidence, "cell_region_owned": cell_owned,
                                    "patch_ids": patch_ids, "cell_labels": cell_labels, "table": table, "patches": patches,
                                    "fixture_owned": fixture_owned, "fixture_tree": fixture_tree,
                                    "rebar_tree": tree, "strong_bars": strong_bars,
                                    "recovered_cells": recovered_cells, "broad_fixture": broad_fixture}
    # Retain the original full-cloud KD tree as-is. The smaller residual index
    # above has a different point population and is cached separately.
    result = {"version": VERSION, "parameters": asdict(params), "pointCount": count,
              "classNames": CLASS_NAMES,
              "colors": COLORS,
              "classPolicy": "table-rebar-fixture-remainder",
              "retentionRestored": True,
              "counts": dict(zip(("table", "fixture", "rebar"), map(int, counts[1:4]))),
              "recovery": {"recoveredPoints": int(np.count_nonzero(recovery_flags)),
                           "recoveredCells": int(recovered.sum()), "passes": 1,
                           "sparseFaceRecoveredPoints": int(grid.counts[residual_ids[sparse_recovered]].sum()),
                           "circularRecoveredPoints": int(grid.counts[residual_ids[cylinders]].sum()),
                           "circularMethod": "measured circular shells; partial shells require existing coaxial cylinder support",
                           "connectivityPolicy": "same observed component or independently elongated fragment; compact islands cannot borrow cylinder support",
                           "meaning": "Steel restored by measured axes or verified circular surfaces; not a separate class"},
              "timings": timings, "elapsedS": time.perf_counter()-started,
              "diagnostics": {"sharedPreparation": scene is not None,
                              "tableFitCalls": 0 if scene is not None else 1,
                              "fixtureSearchExcludedCells": int(cell_owned[residual_ids].sum()), "supportCells": len(grid.points), "residualCells": len(points),
                              "planeComponents": component_count, "acceptedPlanePatches": len(patches),
                              "tableExtensionPatches": int(table_extensions.sum()),
                              "edgeProtection": False,
                              "fixtureAnchorCells": len(fixture_anchors),
                              "fixtureOwnedCells": int(fixture_owned.sum()),
                              "recoveryFixturePatches": int(owned_patches.sum()),
                              "strongRebarCells": len(strong_bars), "rawTreeRebuilt": False,
                              "unsupportedIslandCells": int(np.count_nonzero(~connected_support)),
                              "circularRecoveryCells": int(cylinders.sum()),
                              "circularCandidateCells": int(candidates.sum()),
                              "table": table, "patches": patches},
              "supportMeaning": "Rule support values, not probabilities; fixture includes the scene-prior remainder",
              "identity": "Original source row indices; no points removed; 1 table, 2 fixture, 3 rebar"}
    return result
