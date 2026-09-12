"""Apply scene-region ownership, then one bounded same-surface label cleanup.

The user's scene prior owns non-table points inside the measured inner frame.
Outside it, proximity alone is insufficient: votes require compatible surface
normals AND mutual plane distance, and never iterate on their own predictions.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import time

import numpy as np


VERSION = "region-cleanup-v2-cached-partition"
ZONE_NAMES = {"0": "未定位", "1": "内框内部", "2": "夹具边带", "3": "外框外部"}
REASON_NAMES = {"0": "保持融合结果", "1": "内框钢筋区域约束", "2": "同体素多数归属", "3": "同表面邻域一致性"}


@dataclass(frozen=True)
class RefinementParameters:
    voxel_majority: float = .8
    surface_radius: float = .010
    surface_distance: float = .0018
    normal_cosine: float = .88
    min_surface_votes: int = 5
    surface_majority: float = .9
    max_neighbors: int = 32


def frame_zones(xy, axes, outer, inner):
    local = xy @ axes.T
    within_outer = ((local[:, 0] >= outer[0]) & (local[:, 0] <= outer[1]) &
                    (local[:, 1] >= outer[2]) & (local[:, 1] <= outer[3]))
    within_inner = ((local[:, 0] > inner[0]) & (local[:, 0] < inner[1]) &
                    (local[:, 1] > inner[2]) & (local[:, 1] < inner[3]))
    return np.where(within_inner, 1, np.where(within_outer, 2, 3)).astype(np.uint8)


def same_surface_votes(points, normals, tree, labels, reliable, candidates, *, workers=1, params=None):
    """Return one vote per support cell using the frozen source label snapshot."""
    params = params or RefinementParameters()
    predicted = labels.copy()
    rows = np.flatnonzero(candidates)
    if not len(rows) or not len(points):
        return predicted
    k = min(params.max_neighbors, len(points))

    def chunk(start):
        selected = rows[start:start+4096]
        distance, neighbor = tree.query(points[selected], k=k, distance_upper_bound=params.surface_radius, workers=1)
        if k == 1:
            distance, neighbor = distance[:, None], neighbor[:, None]
        valid = np.isfinite(distance) & (distance > 1.e-7)
        neighbor = np.minimum(neighbor, len(points)-1)
        ni, nj = normals[selected, None, :], normals[neighbor]
        delta = points[neighbor]-points[selected, None, :]
        valid &= reliable[neighbor] & ((labels[neighbor] == 2) | (labels[neighbor] == 3))
        valid &= np.abs(np.sum(ni*nj, axis=2)) >= params.normal_cosine
        valid &= np.abs(np.sum(delta*ni, axis=2)) <= params.surface_distance
        valid &= np.abs(np.sum(delta*nj, axis=2)) <= params.surface_distance
        weights = np.where(valid, 1. / (distance**2+.003**2), 0.)
        total = weights.sum(axis=1)
        steel = (weights*(labels[neighbor] == 3)).sum(axis=1)/np.maximum(total, 1.e-20)
        supported = valid.sum(axis=1) >= params.min_surface_votes
        predicted[selected[supported & (steel >= params.surface_majority)]] = 3
        predicted[selected[supported & (steel <= 1.-params.surface_majority)]] = 2

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(chunk, range(0, len(rows), 4096)))
    return predicted


def refine_regions(context, region_report, *, workers=1, params=None, output=None, progress=None):
    params = params or RefinementParameters()
    progress = progress or (lambda *args: None)
    if context.fused_class is None or context.fused_region is None:
        raise ValueError("双框约束需要同一轮的融合分类与围框区域")
    started = time.perf_counter()
    before = context.fused_class
    count = len(before)
    output = output if output is not None else {name: np.empty(count, np.uint8) for name in
        ("refined_class", "refined_region", "refined_zone", "refined_changed", "refined_reason")}
    labels, regions, zones, changed, reasons = (output[name] for name in
        ("refined_class", "refined_region", "refined_zone", "refined_changed", "refined_reason"))
    labels[:] = before; regions[:] = context.fused_region
    shared_zones = getattr(context, "partition_zone", None)
    zones[:] = shared_zones if shared_zones is not None else 0
    changed[:] = 0; reasons[:] = 0
    frame = region_report['frame']
    enabled = bool(frame.get('detected') and frame.get('innerDetected'))
    timings = {}
    diagnostics = {"enabled": enabled, "reusedResidualTree": False, "surfaceCandidateCells": 0,
                   "reusedPartition": shared_zones is not None}
    result_cache = {}
    if enabled:
        t0 = time.perf_counter()
        progress("第 4 步：内框钢筋区域约束", 0, count)
        rc = context.region_cache
        axes, outer, inner = rc['frame_axes'], rc['frame_bounds_local'], rc['frame_inner_bounds_local']
        if shared_zones is None:
            for start in range(0, count, 262144):
                stop = min(count, start+262144)
                zones[start:stop] = frame_zones(context.positions[start:stop, :2], axes, outer, inner)
        timings['zonesS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        cache = context.classification_cache
        grid, residual = cache['grid'], cache['residual_ids']
        total_cells = len(grid.points)
        c2 = np.bincount(grid.source_to_cell[before == 2], minlength=total_cells)
        c3 = np.bincount(grid.source_to_cell[before == 3], minlength=total_cells)
        support = c2+c3
        cell_labels = np.where(c3 > c2, 3, 2).astype(np.uint8)
        cell_labels[support == 0] = 0
        original_cell_labels = cell_labels.copy()
        reliable = (np.maximum(c2, c3) >= np.maximum(params.voxel_majority*support, 3))
        if shared_zones is not None:
            # A cell crossing the inner edge is never wholly inner. Source
            # ownership still uses exact per-point zones below.
            cell_zones = np.zeros(total_cells, np.uint8)
            np.maximum.at(cell_zones, grid.source_to_cell, shared_zones)
        else:
            cell_zones = frame_zones(grid.points[:, :2]+grid.origin[:2], axes, outer, inner)
        cell_labels[(cell_zones == 1) & (support > 0)] = 3
        reliable[(cell_zones == 1) & (support >= 3)] = True
        frozen = np.zeros(total_cells, bool)
        frozen[residual] = cache['broad_fixture'] & (cell_labels[residual] == 2)
        strong = residual[cache['strong_bars']]
        frozen[strong] |= cell_labels[strong] == 3
        # Geometric evidence can vote even at sparse tips. Keep this separate
        # from voxel-majority reliability used to overwrite source rows.
        vote_reliable = reliable | frozen
        candidates = (cell_zones[residual] != 1) & (support[residual] > 0) & ~frozen[residual]
        timings['cellConsensusS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress("第 4 步：边带与外露区同表面混杂整理", 0, int(candidates.sum()))
        predicted = same_surface_votes(grid.points[residual], cache['features']['surface_normal'], grid.tree,
            cell_labels[residual], vote_reliable[residual], candidates, workers=workers, params=params)
        surface_changed = predicted != cell_labels[residual]
        target = original_cell_labels.copy()
        target[residual] = np.where(cell_zones[residual] == 1, original_cell_labels[residual], predicted)
        surface_flags = np.zeros(total_cells, bool); surface_flags[residual] = surface_changed
        timings['surfaceConsensusS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        for start in range(0, count, 262144):
            stop = min(count, start+262144)
            old = before[start:stop]
            zone = zones[start:stop]
            cell = grid.source_to_cell[start:stop]
            result = old.copy()
            why = np.zeros(stop-start, np.uint8)
            update = (zone != 1) & (old != 1) & (target[cell] != 0) & (reliable[cell] | surface_flags[cell])
            result[update] = target[cell[update]]
            why[update & (result != old)] = np.where(surface_flags[cell[update & (result != old)]], 3, 2)
            owned = (zone == 1) & (old != 1)
            result[owned] = 3
            why[owned & (result != old)] = 1
            steel_score = getattr(context, 'fused_steel_score', None)
            if steel_score is not None:
                protected = (old == 3) & (steel_score[start:stop] >= .9) & (result != 3)
                diagnostics['scoreProtectedPoints'] = diagnostics.get('scoreProtectedPoints', 0)+int(protected.sum())
                result[protected] = 3
                why[protected] = 0
            labels[start:stop] = result; reasons[start:stop] = why
            changed[start:stop] = result != old
            regions[start:stop] = np.where(result == 1, 0, np.where(result == 2, 3, np.where(zone == 3, 2, 1)))
        timings['sourceMappingS'] = time.perf_counter()-t0
        diagnostics.update(reusedResidualTree=True, surfaceCandidateCells=int(candidates.sum()),
                           surfaceChangedCells=int(surface_changed.sum()), reliableCells=int(reliable.sum()),
                           reliableVoteCells=int(vote_reliable.sum()))
        result_cache = {'cell_target': target, 'surface_changed': surface_flags, 'cell_zones': cell_zones}
    counts = np.bincount(labels, minlength=4)
    region_counts = np.bincount(regions, minlength=5)
    def transitions(zone, target):
        return int(np.count_nonzero((zones == zone) & (labels == target) & (labels != before)))
    changes = {'interiorToSteel': transitions(1, 3), 'bandToFixture': transitions(2, 2),
               'bandToSteel': transitions(2, 3), 'exteriorToFixture': transitions(3, 2),
               'exteriorToSteel': transitions(3, 3), 'totalChanged': int(changed.sum())}
    report = {'version': VERSION, 'pointCount': count, 'parameters': asdict(params), 'elapsedS': time.perf_counter()-started,
              'counts': dict(zip(('table', 'fixture', 'rebar', 'noise'), map(int, counts[1:5]))),
              'regionCounts': dict(zip(('table', 'interior', 'exterior', 'fixture', 'unlocated'), map(int, region_counts))),
              'changes': changes, 'frame': frame, 'diagnostics': diagnostics, 'timings': timings,
              'reasonNames': REASON_NAMES, 'zoneNames': ZONE_NAMES,
              'policy': 'non-table inner points are steel by scene prior; one frozen same-surface consensus outside inner frame',
              'images': {}}
    for name, array in output.items():
        setattr(context, name, array)
    context.refinement_cache = result_cache
    return report


def reuse_fusion_partition(context, region_report, *, output=None):
    """Bind read-only compatibility views; materialize only for legacy callers.

    The shared algorithm has no refinement pass. Publishing callers write these
    views to their legacy NPY/LAS columns after computation has finished.
    """
    if context.fused_class is None or context.partition_zone is None:
        raise ValueError('需要本轮融合结果与共享分区')
    sources = {'refined_class': context.fused_class, 'refined_region': context.fused_region,
               'refined_zone': context.partition_zone}
    if output is None:
        output = {name: np.asarray(source).view() for name, source in sources.items()}
        zero = np.broadcast_to(np.array(0, np.uint8), (len(context.positions),))
        output.update(refined_changed=zero, refined_reason=zero)
        for array in output.values():
            array.flags.writeable = False
    else:
        for name, source in sources.items():
            output[name][:] = source
        output['refined_changed'][:] = 0
        output['refined_reason'][:] = 0
    for name, array in output.items():
        setattr(context, name, array)
    context.refinement_cache = {}
    counts = np.bincount(context.fused_class, minlength=5)
    regions = np.bincount(context.fused_region, minlength=5)
    return {'version': 'fusion-partition-pass-through-v1', 'mode': 'fusion-pass-through',
            'pointCount': len(context.positions), 'elapsedS': 0., 'timings': {},
            'counts': dict(zip(('table', 'fixture', 'rebar', 'noise'), map(int, counts[1:5]))),
            'regionCounts': dict(zip(('table', 'interior', 'exterior', 'fixture', 'unlocated'), map(int, regions))),
            'frame': region_report['frame'], 'zoneNames': ZONE_NAMES, 'reasonNames': {'0': '沿用融合结果'},
            'changes': {'totalChanged': 0}, 'diagnostics': {'enabled': False, 'reusedPartition': True},
            'policy': '融合后直接去噪；不再执行边带类别投票', 'images': {}}
