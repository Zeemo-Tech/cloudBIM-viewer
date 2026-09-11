"""Extend existing instances into observed exterior steel; never synthesize points."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from algorithms.region_refinement import frame_zones

VERSION = 'rebar-cluster-extension-v3-score-protection'
ATTRIBUTES = {'complete_class': 'u1', 'complete_instance': '<u4',
              'complete_segment': '<u4', 'complete_confidence': '<f4', 'complete_cluster': '<u4'}


@dataclass(frozen=True)
class ExtensionParameters:
    maximum_extension: float = .50
    surface_tolerance: float = .0035
    support_bin: float = .01
    maximum_gap: float = .025
    maximum_frame_bridge: float = .15
    minimum_support_bins: int = 3
    minimum_points_per_bin: int = 3
    maximum_axis_normal: float = .65
    cluster_voxel: float = .002
    cluster_connection_radius: float = .0045
    competing_support_ratio: float = .35


def _terminal_rays(models):
    groups = {}
    for index, model in enumerate(models):
        groups.setdefault(model['instanceId'], []).append((index, model))
    for group in groups.values():
        reference = max(group, key=lambda item: item[1]['high']-item[1]['low'])[1]['axis']
        ends = [(index, m, sign, m['center']+m[key]*m['axis'])
                for index, m in group for sign, key in ((-1, 'low'), (1, 'high'))]
        for end in (min(ends, key=lambda e: e[3]@reference), max(ends, key=lambda e: e[3]@reference)):
            yield end


def exterior_clusters(points, params):
    """Freeze exterior connectivity BEFORE consulting any internal cylinder.

    Existing stages expose semantic labels, not exterior instance/cluster IDs.
    Voxel components retain every source row, including bends and weak normals;
    no cylinder residual or direction participates in this grouping.
    """
    if not len(points):
        return np.zeros(0, np.uint32)
    cells = np.floor((points-points.min(axis=0))/params.cluster_voxel).astype(np.int64)
    _, inverse = np.unique(cells, axis=0, return_inverse=True)
    mass = np.bincount(inverse)
    centers = np.column_stack([np.bincount(inverse, weights=points[:, axis])/mass for axis in range(3)])
    pairs = cKDTree(centers).query_pairs(params.cluster_connection_radius, output_type='ndarray')
    graph = coo_matrix((np.ones(len(pairs), np.uint8), (pairs[:, 0], pairs[:, 1])), shape=(len(centers),len(centers))).tocsr()
    _, labels = connected_components(graph, directed=False)
    return (labels[inverse]+1).astype(np.uint32)


def extend_rebar_instances(context, internal_report, *, workers=1, params=None, output=None, progress=None):
    params = params or ExtensionParameters()
    if context.internal_rebar_cache is None or context.internal_instance is None:
        raise ValueError('轴向延伸需要内部钢筋实例')
    started = time.perf_counter()
    progress = progress or (lambda *args: None)
    count = len(context.positions)
    output = output if output is not None else {name: np.zeros(count, dtype) for name, dtype in ATTRIBUTES.items()}
    classes = output['complete_class']; classes[:] = context.refined_class
    classes[~np.isin(classes, (1, 2, 3))] = 4
    fused_score = getattr(context, 'fused_steel_score', None)
    protected = (np.zeros(count, bool) if fused_score is None else
                 (np.asarray(fused_score) >= .9) & (context.refined_class == 3))
    internal_noise = getattr(context, 'internal_type', None)
    if internal_noise is not None:
        classes[(internal_noise == 5) & ~protected] = 4
    for name in ('instance', 'segment', 'confidence'):
        output['complete_'+name][:] = getattr(context, 'internal_'+name)
    candidates = np.flatnonzero((context.refined_class == 3) & (context.refined_zone != 1))
    # Missing seed evidence is not evidence of noise: leave this run conservative.
    models = context.internal_rebar_cache['models']
    usable = bool(models) and bool(np.any(context.internal_instance))
    frame = context.region_cache or {}
    located = all(np.shape(frame.get(key)) == (4,) for key in ('frame_bounds_local', 'frame_inner_bounds_local'))
    enabled = bool(usable and located)
    best = np.full(len(candidates), np.inf)
    output['complete_cluster'][:] = 0
    cluster_report = []
    ambiguous = np.zeros(len(candidates), bool)
    carried_count = 0
    owners = np.zeros(len(candidates), np.uint32)
    segments = deepcopy(internal_report['segments'])
    segment_by_id = {segment['id']: segment for segment in segments}
    extensions = []
    if enabled and len(candidates):
        progress('第 6 步：沿内部实例轴向接续外露钢筋', 0, len(candidates))
        points = context.positions[candidates]
        normals = context.normals[candidates]
        clusters = exterior_clusters(points, params)
        output['complete_cluster'][candidates] = clusters
        cluster_count = int(clusters.max())
        matches = {}
        tree = cKDTree(points)
        sample_t = np.arange(0, params.maximum_extension+params.support_bin*.5, params.support_bin)
        rays = list(_terminal_rays(models))

        def grow(ray):
            index, model, sign, start = ray
            # A model with no source ownership cannot establish an exterior instance.
            if not segment_by_id.get(index+1, {}).get('pointCount', 0):
                return None
            axis = model['axis']*sign
            samples = start+sample_t[:, None]*axis
            zones = frame_zones(samples[:, :2], frame['frame_axes'], frame['frame_bounds_local'], frame['frame_inner_bounds_local'])
            # Only endpoints at the inner boundary can jump the occluded rail.
            band = zones == 2
            bridge = band if np.count_nonzero(band)*params.support_bin <= params.maximum_frame_bridge else np.zeros(len(band), bool)
            neighbors = tree.query_ball_point(samples, model['radius']+params.surface_tolerance+params.support_bin, workers=1)
            ids = np.unique(np.concatenate([np.asarray(row, np.int64) for row in neighbors]))
            if not len(ids):
                return None
            delta = points[ids]-start
            along = delta@axis
            radial = delta-along[:, None]*axis
            distance = np.linalg.norm(radial, axis=1)
            error = np.abs(distance-model['radius'])
            axis_normal = np.abs(normals[ids]@axis)
            radial_normal = np.abs(np.sum(normals[ids]*radial, axis=1))/np.maximum(distance, 1.e-9)
            valid = (along >= 0) & (along <= params.maximum_extension) & (error <= params.surface_tolerance)
            valid &= (axis_normal <= params.maximum_axis_normal) & (radial_normal >= .35)
            ids, along, error, axis_normal = ids[valid], along[valid], error[valid], axis_normal[valid]
            if not len(ids):
                return None
            bins = np.minimum((along/params.support_bin).astype(int), len(sample_t)-1)
            occupied = np.bincount(bins, minlength=len(sample_t)) >= params.minimum_points_per_bin
            last = -1; empty_length = 0.
            for b in range(len(sample_t)):
                if occupied[b]:
                    last = b; empty_length = 0.
                elif not bridge[b]:
                    empty_length += params.support_bin
                    if empty_length > params.maximum_gap:
                        break
            if last < 0 or np.count_nonzero(occupied[:last+1]) < params.minimum_support_bins:
                return None
            keep = (bins <= last) & occupied[bins]
            ids, along, error, axis_normal = ids[keep], along[keep], error[keep], axis_normal[keep]
            score = error+.0015*axis_normal**2
            return index, sign, start, axis, ids, along, score

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for result in pool.map(grow, rays):
                if result is None:
                    continue
                index, sign, start, axis, ids, along, score = result
                # Match an attachment region, then assign its frozen component.
                # Several points along the axis must belong to THIS cluster.
                for cluster_id in np.unique(clusters[ids]):
                    selected = clusters[ids] == cluster_id
                    bin_counts = np.bincount((along[selected]/params.support_bin).astype(int))
                    if np.count_nonzero(bin_counts >= params.minimum_points_per_bin) < params.minimum_support_bins:
                        continue
                    support = int(selected.sum())
                    if support < params.minimum_points_per_bin*params.minimum_support_bins:
                        continue
                    matches.setdefault(int(cluster_id), []).append({
                        'index': index, 'sign': sign, 'start': start, 'axis': axis,
                        'ids': ids[selected], 'along': along[selected],
                        'score': float(np.median(score[selected])), 'support': support,
                        'instanceId': models[index]['instanceId']})
        cluster_owners = np.zeros(cluster_count+1, np.uint32)
        cluster_scores = np.full(cluster_count+1, np.inf)
        cluster_ambiguous = np.zeros(cluster_count+1, bool)
        cluster_sizes = np.bincount(clusters, minlength=cluster_count+1)
        for cluster_id in range(1, cluster_count+1):
            options = matches.get(cluster_id, [])
            # Same-instance local parts do not constitute an ownership conflict.
            by_instance = {}
            for option in options:
                previous = by_instance.get(option['instanceId'])
                if previous is None or (option['support'], -option['score']) > (previous['support'], -previous['score']):
                    by_instance[option['instanceId']] = option
            ranked = sorted(by_instance.values(), key=lambda o: (-o['support'], o['score'], o['instanceId']))
            winner = ranked[0] if ranked else None
            conflict = len(ranked) > 1 and ranked[1]['support'] >= winner['support']*params.competing_support_ratio
            state = 'ambiguous' if conflict else 'matched' if winner else 'noise'
            cluster_ambiguous[cluster_id] = conflict
            if winner and not conflict:
                cluster_owners[cluster_id] = winner['index']+1
                cluster_scores[cluster_id] = winner['score']
                extensions.append(winner)
                carried_count += int(cluster_sizes[cluster_id])-winner['support']
            cluster_report.append({'id': cluster_id, 'pointCount': int(cluster_sizes[cluster_id]),
                'status': state, 'instanceId': winner['instanceId'] if winner and not conflict else 0,
                'segmentId': winner['index']+1 if winner and not conflict else 0,
                'anchorPointCount': winner['support'] if winner else 0,
                'candidateInstanceIds': sorted(by_instance)})
        owners[:] = cluster_owners[clusters]
        best[:] = cluster_scores[clusters]
        ambiguous = cluster_ambiguous[clusters]
        accepted = owners > 0
        classes[candidates] = 4
        classes[candidates[accepted | ambiguous | protected[candidates]]] = 3
        instance_map = np.array([0]+[m['instanceId'] for m in models], np.uint32)
        output['complete_instance'][candidates] = instance_map[owners]
        output['complete_segment'][candidates] = owners
        output['complete_confidence'][candidates] = np.where(accepted, np.clip(1-best/(params.surface_tolerance+.002), 0, 1), 0)
        # These endpoints describe the straight attachment evidence only.
        # The retained cluster may bend away; do not claim a fitted hook axis.
        extension_report = []
        for match in extensions:
            index, sign, start, axis = (match[k] for k in ('index','sign','start','axis'))
            length = float(match['along'].max())
            end = start+length*axis
            key = 'startM' if sign < 0 else 'endM'
            segment = segment_by_id[index+1]
            old_span = (np.asarray(segment[key])-start)@axis
            if length > old_span:
                segment[key] = end.tolist()
            cluster_id = int(clusters[match['ids'][0]])
            extension_report.append({'segmentId': index+1, 'instanceId': models[index]['instanceId'],
                'clusterId': cluster_id, 'startM': start.tolist(), 'endM': end.tolist(), 'lengthM': length,
                'pointCount': int(cluster_sizes[cluster_id]), 'anchorPointCount': match['support'],
                'evidence': 'whole frozen exterior cluster; straight attachment only, hook centerline not fitted'})
    else:
        extension_report = []
    segment_counts = np.bincount(output['complete_segment'], minlength=max(segment_by_id, default=0)+1)
    for segment in segments:
        segment['pointCount'] = int(segment_counts[segment['id']])
    instances = deepcopy(internal_report['instances'])
    for instance in instances:
        parts = [s for s in segments if s['instanceId'] == instance['id']]
        instance['pointCount'] = sum(s['pointCount'] for s in parts)
        instance['internalLengthM'] = instance['lengthM']
        spans = {}
        for e in extension_report:
            if e['instanceId'] == instance['id']:
                # Several disconnected clusters can attach along the same ray.
                key = (e['segmentId'], tuple(e['startM']))
                spans[key] = max(spans.get(key, 0.), e['lengthM'])
        instance['extensionLengthM'] = sum(spans.values())
        instance['lengthMeaning'] = 'internal length plus axial attachment spans; excludes hook arc length'
        instance['lengthM'] += instance['extensionLengthM']
    for name, values in output.items():
        setattr(context, name, values)
    counts = np.bincount(classes, minlength=5)
    return {'version': VERSION, 'enabled': enabled, 'pointCount': count,
            'classNames': {'1': '台面', '2': '夹具', '3': '钢筋', '4': '噪音'},
            'counts': dict(zip(('table', 'fixture', 'rebar', 'noise'), map(int, counts[1:5]))),
            'candidatePointCount': len(candidates), 'matchedExteriorPointCount': int(np.count_nonzero(owners)),
            'rejectedExteriorPointCount': int(np.count_nonzero((owners == 0) & ~ambiguous)) if enabled else 0,
            'unassignedRebarPointCount': int(np.count_nonzero((classes == 3) & (output['complete_instance'] == 0))),
            'clusters': cluster_report,
            'clusterCount': len(cluster_report),
            'matchedClusterCount': sum(c['status'] == 'matched' for c in cluster_report),
            'ambiguousClusterCount': sum(c['status'] == 'ambiguous' for c in cluster_report),
            'ambiguousExteriorPointCount': int(np.count_nonzero(ambiguous)),
            'clusterCarriedPointCount': carried_count,
            'clusterPolicy': 'Immutable pre-match exterior components; one class and one instance per cluster. Conflicts remain whole as unassigned steel.',
            'instanceCount': len(instances), 'instances': instances, 'segments': segments, 'extensions': extension_report,
            'parameters': asdict(params), 'elapsedS': time.perf_counter()-started,
            'policy': 'Preserve internal ownership, static classes and entire exterior clusters. Axes match attachment regions, never trim a cluster. Unmatched clusters are noise; competing matches are unassigned steel.',
            'disabledReason': None if enabled else 'Missing internal instances or measured inner/outer frame'}
