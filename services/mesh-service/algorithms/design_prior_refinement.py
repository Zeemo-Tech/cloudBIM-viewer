"""Bounded component-level design matching; never changes measured geometry.

Only already retained steel is eligible. Design identity may link disconnected
instances; it never changes semantic classification or revives discarded points.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
import time

import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

VERSION = 'design-prior-linking-v3'
MODES = ('off', 'geometry', 'topology')
ATTRIBUTES = {'prior_class': 'u1', 'prior_instance': '<u4', 'prior_component': '<u4',
              'prior_status': 'u1', 'prior_action': 'u1'}


@dataclass(frozen=True)
class PriorParameters:
    max_candidates: int = 16
    max_components: int = 30_000
    sample_per_component: int = 512
    component_radius: float = .0055
    position_gate: float = .15
    short_position_gate: float = .35
    max_angle_degrees: float = 25.
    match_margin: float = .12
    merge_gap: float = .20
    merge_offset: float = .004
    merge_angle_degrees: float = 8.
    minimum_steel_span: float = .02
    max_surface_error: float = .0035


def _atoms(context, params):
    """Freeze the FINAL steel population and every existing instance identity."""
    rows = np.flatnonzero(context.complete_class == 3)
    owners = context.complete_instance[rows]
    clusters = context.complete_cluster[rows]
    labels = np.zeros(len(rows), np.uint32)
    positive = owners > 0
    owner_ids, inverse = np.unique(owners[positive], return_inverse=True)
    labels[positive] = inverse+1
    exterior = ~positive & (clusters > 0)
    ext_ids, inverse = np.unique(clusters[exterior], return_inverse=True)
    labels[exterior] = inverse+len(owner_ids)+1
    loose = ~(positive | exterior)
    offset = len(owner_ids)+len(ext_ids)
    if np.any(loose):
        xyz = context.positions[rows[loose]]
        cache = getattr(context, 'classification_cache', None) or {}
        grid = cache.get('grid')
        if grid is not None:
            cells, inverse = np.unique(grid.source_to_cell[rows[loose]], return_inverse=True)
            centers = grid.points[cells]+grid.origin
        else:
            voxel = np.floor((xyz-xyz.min(axis=0))/.003).astype(np.int64)
            _, inverse = np.unique(voxel, axis=0, return_inverse=True)
            mass = np.bincount(inverse)
            centers = np.column_stack([np.bincount(inverse,weights=xyz[:,j])/mass for j in range(3)])
        pairs = cKDTree(centers).query_pairs(params.component_radius, output_type='ndarray')
        graph = coo_matrix((np.ones(len(pairs),np.uint8),(pairs[:,0],pairs[:,1])),shape=(len(centers),len(centers))).tocsr()
        count, connected = connected_components(graph,directed=False)
        labels[loose] = connected[inverse]+offset+1
        offset += count
    return rows, labels, offset


def _summary(points, normals, normal_valid, source_instance, segments, params):
    center = points.mean(axis=0)
    _, variance, basis = np.linalg.svd(points-center, full_matrices=False)
    axis = basis[0]
    if axis[np.argmax(np.abs(axis))] < 0:
        axis = -axis
    along = (points-center) @ axis
    low, high = np.quantile(along, [.01, .99])
    radial = np.linalg.norm((points-center)-along[:, None]*axis, axis=1)
    radius = float(np.median(radial))
    error = float(np.median(np.abs(radial-radius)))
    valid_normals = np.linalg.norm(normals, axis=1) > .5
    if normal_valid is not None:
        valid_normals &= normal_valid.astype(bool)
    perpendicular = float(np.mean(np.abs(normals[valid_normals]@axis) < .45)) if np.any(valid_normals) else None
    length = float(high-low)
    linearity = float(1-variance[1]**2/max(variance[0]**2, 1e-15)) if len(variance) > 1 else 0.
    strong = (length >= params.minimum_steel_span and linearity >= .65 and .001 <= radius <= .018
              and error <= params.max_surface_error and (perpendicular is None or perpendicular >= .6))
    # Existing observed cylinder evidence protects curved or partially occluded
    # instances that a single PCA line cannot describe. It is not a design fit.
    fit_support = False
    supported = np.zeros(len(points),bool)
    for part in segments.get(source_instance, []):
        a, b = np.asarray(part['startM']), np.asarray(part['endM'])
        vector = b-a; span = np.linalg.norm(vector)
        if span < 1e-9:
            continue
        direction = vector/span
        t = (points-a)@direction
        r = np.linalg.norm(points-a-t[:, None]*direction, axis=1)
        ok = (t >= -.005) & (t <= span+.005) & (np.abs(r-float(part.get('radiusM', .004))) <= params.max_surface_error)
        supported |= ok
    if np.count_nonzero(supported) >= max(6,int(len(points)*.5)) and length >= params.minimum_steel_span:
        fit_support = True
    return {'center': center, 'axis': axis, 'start': center+low*axis, 'end': center+high*axis,
            'length': length, 'radius': radius, 'surfaceError': error, 'linearity': linearity,
            'normalAgreement': perpendicular, 'strong': bool(strong or fit_support),
            'fitSupport': fit_support}


def _candidates(summary, units, centers, axes, lengths, tree, params):
    if not len(units):
        return []
    # A fragment near the end of a 4 m bar can be far from its design midpoint.
    # Prune by direction/finite-axis geometry before taking the top candidates.
    nearest = range(len(units))
    results = []
    angle_gate = np.cos(np.deg2rad(params.max_angle_degrees))
    for j in np.atleast_1d(nearest):
        j = int(j); unit = units[j]
        if summary.get('webRole') and unit['kind'] != 'web':
            continue
        if summary.get('family') in (1,2,3) and unit['kind']=='web':
            continue
        agreement = abs(float(summary['axis']@axes[j]))
        if agreement < angle_gate:
            continue
        delta = summary['center']-centers[j]
        t = float(delta@axes[j])
        transverse = float(np.linalg.norm(delta-t*axes[j]))
        gate = params.short_position_gate if unit['kind'] == 'short' else params.position_gate
        axial_gap = max(0., abs(t)-(lengths[j]+summary['length'])/2)
        if transverse > gate or axial_gap > gate:
            continue
        # Visible length is a lower bound; only clear overshoot is penalized.
        length_cost = max(0., summary['length']-lengths[j]-.05)/.08
        if unit.get('coverage') != 'complete':
            length_cost = 0.
        diameter_cost = min(1., abs(2*summary['radius']-unit['diameterM'])/max(unit['diameterM'], .002))
        if not summary['strong']:
            diameter_cost *= .2
        scale = .15 if unit['kind']=='short' else .035
        family_cost = .5 if summary.get('family')==3 and unit['kind']!='short' else .5 if summary.get('family')==1 and unit['kind']=='short' else 0.
        cost = .5*(transverse/scale)**2 + .25*(axial_gap/gate)**2 + 3*(1-agreement) + length_cost + .08*diameter_cost + family_cost
        results.append((cost, j))
    return sorted(results)[:params.max_candidates]


def _select(candidates, margin):
    if not candidates or candidates[0][0] > 2.0:
        return None
    if len(candidates) > 1 and candidates[1][0]-candidates[0][0] < margin:
        return None
    return candidates[0][1]


def _conflicting_support(a, b):
    """Two substantially overlapping parallel rods cannot occupy one unit."""
    if abs(float(a['axis']@b['axis'])) < .97:
        return False
    direction=a['axis']; origin=a['start']
    ranges=[sorted(float((p-origin)@direction) for p in (s['start'],s['end'])) for s in (a,b)]
    overlap=min(x[1] for x in ranges)-max(x[0] for x in ranges)
    delta=b['center']-a['center']
    offset=np.linalg.norm(delta-direction*(delta@direction))
    return overlap > max(.02,min(a['length'],b['length'])*.25) and offset > .006


def _topology(candidates, summaries, units, relations, params):
    """Two synchronous, bounded re-scoring passes; no full graph optimization."""
    uid = {u['designUnitId']: i for i, u in enumerate(units)}
    edges = {}
    for edge in relations:
        if edge['from'] in uid and edge['to'] in uid:
            edges[(uid[edge['from']], uid[edge['to']])] = edge
    initial = [_select(c, params.match_margin) if s['strong'] else None for c, s in zip(candidates, summaries)]
    choices = initial
    if len(summaries) < 2:
        return candidates, choices
    centers = np.array([s['center'] for s in summaries])
    _, neighbors = cKDTree(centers).query(centers, k=min(9, len(summaries)))
    neighbors = np.asarray(neighbors).reshape(len(summaries), -1)
    scored = candidates
    for _ in range(2):
        anchors = {}
        for i,choice in enumerate(choices):
            if choice is not None and (choice not in anchors or summaries[i]['length']>summaries[anchors[choice]]['length']):
                anchors[choice]=i
        next_scores = []
        for i, row in enumerate(candidates):
            ranked = []
            for cost, target in row:
                anchor=anchors.get(target)
                if anchor is not None and anchor!=i and _conflicting_support(summaries[i],summaries[anchor]):
                    cost += 2.1
                penalties = []
                for neighbor in neighbors[i]:
                    if neighbor == i or choices[neighbor] is None:
                        continue
                    edge = edges.get((target, choices[neighbor]))
                    if edge is None:
                        continue
                    delta = centers[neighbor]-centers[i]
                    if edge['kind'] == 'parallel':
                        axis = np.asarray(units[target]['direction'])
                        transverse = delta-axis*(delta@axis)
                        expected = np.asarray(edge['offsetM'])
                        # Relative lanes/heights tolerate translation, not a swap.
                        penalties.append(min(1., np.linalg.norm(transverse-expected)/max(.025, np.linalg.norm(expected)*.4)))
                    elif edge['kind'] == 'crossing':
                        expected = edge['heightDifferenceM']
                        penalties.append(min(1., abs(delta[2]-expected)/.025))
                    elif edge['kind'] == 'next':
                        a, b = summaries[i], summaries[neighbor]
                        endpoint_gap = min(np.linalg.norm(x-y) for x in (a['start'], a['end']) for y in (b['start'], b['end']))
                        penalties.append(min(1., endpoint_gap/.1))
                ranked.append((cost + (.25*float(np.mean(penalties)) if penalties else 0.), target))
            next_scores.append(sorted(ranked))
        scored = next_scores
        choices = [_select(c, params.match_margin) if s['strong'] else None for c, s in zip(scored, summaries)]
    return scored, choices


def _continuous(a, b, params, *, ignore_gap=False):
    if not a['strong'] or not b['strong'] or abs(float(a['axis']@b['axis'])) < np.cos(np.deg2rad(params.merge_angle_degrees)):
        return False
    stable = a if a['length'] >= b['length'] else b
    direction = stable['axis']; origin = stable['start']
    intervals = [sorted(float((p-origin)@direction) for p in (s['start'], s['end'])) for s in (a, b)]
    overlap = min(t[1] for t in intervals)-max(t[0] for t in intervals)
    if overlap > .005 or (not ignore_gap and overlap < -params.merge_gap):
        return False
    delta = b['center']-a['center']
    offset = np.linalg.norm(delta-direction*(delta@direction))
    return bool(offset <= params.merge_offset and abs(a['radius']-b['radius']) <= .003)


def refine_design_prior(context, complete_report, inventory, *, mode='geometry', output=None, params=None, progress=None):
    if mode not in ('geometry', 'topology'):
        raise ValueError('设计复核模式必须为 geometry 或 topology')
    params = params or PriorParameters()
    started = time.perf_counter()
    n = len(context.positions)
    output = output if output is not None else {k: np.zeros(n, dtype) for k, dtype in ATTRIBUTES.items()}
    output['prior_class'][:] = context.complete_class
    output['prior_instance'][:] = context.complete_instance
    for k in ('prior_component', 'prior_status', 'prior_action'):
        output[k][:] = 0
    report = {'version': VERSION, 'mode': mode, 'enabled': bool(inventory.get('units')), 'disabledReason': None,
              'inventory': deepcopy(inventory), 'parameters': asdict(params), 'components': [], 'instances': [], 'timings': {},
              'statusNames': {'0': '范围外', '1': '已匹配', '2': '待定', '3': '噪声'},
              'actionBits': {'1': '本次过滤', '2': '本次找回', '4': '实例合并'},
              'counts': {'matched': 0, 'pending': 0, 'noise': 0, 'filteredPoints': 0, 'recoveredPoints': 0,
                         'mergedPoints': 0, 'mergedInstances': 0},
              'policy': 'One observed instance per straight web unit. Parent membership never merges instances. Unmatched steel is pending.'}
    def finish():
        for k, v in output.items():
            setattr(context, k, v)
        report['timings']['totalS'] = time.perf_counter()-started
        return report
    if not report['enabled']:
        report['disabledReason'] = '没有可靠设计匹配单元；保留原结果'
        return finish()
    progress = progress or (lambda *args: None)
    progress('第 7 步：设计匹配单元复核', 0, n)
    t = time.perf_counter()
    rows, labels, total = _atoms(context, params)
    report['policy'] = 'retained-clusters-only: immutable semantics; design units guide observed links, never quotas.'
    report['inputPolicy'] = 'complete_class == 3'
    report['links'] = []
    if total > params.max_components:
        report.update(enabled=False, disabledReason='候选簇超过预算；保留原结果')
        return finish()
    if not len(rows):
        return finish()
    output['prior_component'][rows] = labels
    order = np.argsort(labels, kind='stable')
    counts = np.bincount(labels, minlength=total+1)
    boundaries = np.r_[0, np.cumsum(counts[1:])]
    grouped = rows[order]
    segments = {}
    for part in complete_report['segments']:
        segments.setdefault(int(part['instanceId']), []).append(part)
    old_instances = {i['id']: i for i in complete_report['instances']}
    summaries, components = [], []
    valid = getattr(context, 'normal_valid', None)
    for i in range(total):
        ids = grouped[boundaries[i]:boundaries[i+1]]
        sample = ids[np.linspace(0, len(ids)-1, min(params.sample_per_component, len(ids)), dtype=int)]
        old_id = int(context.complete_instance[ids[0]])
        summary = _summary(context.positions[sample], context.normals[sample], valid[sample] if valid is not None else None,
                           old_id, segments, params)
        family = old_instances.get(old_id, {}).get('family')
        summary.update(pointCount=len(ids),family=family,
            webRole=family==4 or old_instances.get(old_id, {}).get('type')==3)
        summaries.append(summary)
        ext = list(map(int,np.unique(context.complete_cluster[ids])))
        ext = [x for x in ext if x]
        components.append({'id':i+1,'kind':'internal' if old_id else 'exterior',
            'exteriorClusterId':ext[0] if len(ext)==1 else 0,'exteriorClusterIds':ext,
            'originalInstanceId':old_id,'instanceId':old_id,'pointCount':len(ids),'baselineClass':3,
            'designBarId':None,'designUnitId':None,'status':2,'action':0,'score':None,'family':family,
            'centerM':summary['center'].tolist(),'startM':summary['start'].tolist(),'endM':summary['end'].tolist(),
            'observedDiameterM':2*summary['radius'],'observedLengthM':summary['length'],
            'geometricEvidence':{'strong':summary['strong'],'surfaceErrorM':summary['surfaceError'],
                                 'normalAgreement':summary['normalAgreement'],'linearity':summary['linearity']}})
    report['timings']['componentsS'] = time.perf_counter()-t
    t = time.perf_counter()
    units = inventory['units']
    centers = np.array([(np.asarray(u['startM'])+u['endM'])/2 for u in units])
    axes = np.array([u['direction'] for u in units]); lengths = np.array([u['lengthM'] for u in units])
    tree = cKDTree(centers)
    candidates = [_candidates(s,units,centers,axes,lengths,tree,params) for s in summaries]
    candidates, choices = _topology(candidates,summaries,units,inventory['relations'] if mode=='topology' else [],params)
    next_instance = max(old_instances,default=0)+1
    for i,(component,summary,choice) in enumerate(zip(components,summaries,choices)):
        if choice is not None:
            unit = units[choice]
            component.update(status=1,designBarId=unit['designBarId'],designUnitId=unit['designUnitId'],
                reason='retained_observation_matches_design_unit',score=float(candidates[i][0][0]))
            if not component['instanceId']:
                component['instanceId']=next_instance;next_instance+=1
        else:
            component['reason']='ambiguous_design_candidates' if candidates[i] else 'design_uncovered_or_displaced'
        component['candidateUnitIds']=[units[j]['designUnitId'] for _,j in candidates[i]]
    # Build observed adjacency within a unit, then union only conflict-free
    # components. A chain may bridge multiple gaps; its endpoints need not touch.
    groups = {}
    for i,c in enumerate(components):
        if c['status']==1: groups.setdefault(c['designUnitId'],[]).append(i)
    parents=list(range(total));members={i:[i] for i in range(total)}
    def leader(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    for unit_id,group in groups.items():
        if len(group)>128: continue
        edges=[]
        for index,i in enumerate(group):
            for j in group[index+1:]:
                if _continuous(summaries[i],summaries[j],params):
                    distance=min(float(np.linalg.norm(a-b)) for a in (summaries[i]['start'],summaries[i]['end'])
                                 for b in (summaries[j]['start'],summaries[j]['end']))
                    edges.append((-min(summaries[i]['pointCount'],summaries[j]['pointCount']),distance,i,j))
        for _,distance,i,j in sorted(edges):
            a,b=leader(i),leader(j)
            if a==b: continue
            if not all(_continuous(summaries[x],summaries[y],params,ignore_gap=True) for x in members[a] for y in members[b]):
                continue
            parents[b]=a;members[a]+=members.pop(b)
            report['links'].append({'fromComponentId':components[i]['id'],'toComponentId':components[j]['id'],
                                   'designUnitId':unit_id,'measuredEndpointGapM':distance,
                                   'reason':'same_unit_observed_axis_continuity'})
    for group in members.values():
        target=min(components[i]['instanceId'] for i in group)
        if len(group)==1 and not components[group[0]]['originalInstanceId']:
            # A standalone unassigned cluster may have a design correspondence,
            # but design identity alone must not create a new observed instance.
            components[group[0]]['instanceId']=0
            continue
        for i in group:
            c=components[i]
            if c['instanceId']!=target:
                c.update(instanceId=target,action=4,reason='same_unit_observed_continuous_fragments')
    for i,c in enumerate(components):
        ids=grouped[boundaries[i]:boundaries[i+1]]
        output['prior_instance'][ids]=c['instanceId']
        output['prior_status'][ids]=c['status']
        output['prior_action'][ids]=c['action']
    report['components']=components
    report['counts'].update(matched=sum(c['status']==1 for c in components),pending=sum(c['status']==2 for c in components),
        mergedPoints=int(np.count_nonzero(output['prior_action']&4)),mergedInstances=len(report['links']))
    by_instance = {}
    for c in components:
        if c['instanceId']:
            entry=by_instance.setdefault(c['instanceId'],{'id':c['instanceId'],'pointCount':0,
                'designBarId':c['designBarId'],'designUnitId':c['designUnitId'],'componentIds':[],'family':c['family']})
            entry['pointCount']+=c['pointCount'];entry['componentIds'].append(c['id'])
    report['instances']=list(by_instance.values())
    unit_owners = {u['designUnitId']:set() for u in units}
    for c in components:
        if c['designUnitId'] and c['instanceId']: unit_owners[c['designUnitId']].add(c['instanceId'])
    report['associationCounts']={'designPhysicalBars':inventory['coverage']['physicalBarCount'],
        'designMatchingUnits':len(units),'observedInstancesBefore':len(old_instances),'observedInstancesAfter':len(by_instance),
        'unassignedClustersBefore':sum(c['originalInstanceId']==0 for c in components),
        'unassignedClustersAfter':sum(c['instanceId']==0 for c in components),
        'observedClustersBefore':len(components),
        'observedClustersAfter':len(by_instance)+sum(c['instanceId']==0 for c in components),
        'designWebPhysicalBars':inventory['coverage']['webPhysicalBarCount'],
        'designWebUnits':inventory['coverage']['webStraightUnitCount'],
        'observedWebBefore':sum(i.get('family')==4 for i in old_instances.values()),
        'observedWebAfter':sum(i.get('family')==4 for i in by_instance.values()),
        'unobservedUnitIds':[uid for uid,owners in unit_owners.items() if not owners],
        'multipleObservedInstancesByUnit':{uid:sorted(owners) for uid,owners in unit_owners.items() if len(owners)>1},
        'policy':'No inferred missing/misplaced bar from a count alone; unresolved observations remain pending.'}
    report['timings']['matchingS']=time.perf_counter()-t
    # Actual fixture regions come from observation, including when the IFC has
    # no fixture object or exports a fixture under an unrelated entity class.
    frame = getattr(context, 'region_cache', None) or {}
    if all(key in frame for key in ('frame_axes', 'frame_bounds_local', 'frame_inner_bounds_local')):
        from .region_refinement import frame_zones
        if np.shape(frame['frame_bounds_local']) == (4,) and np.shape(frame['frame_inner_bounds_local']) == (4,):
            for unit in report['inventory']['units']:
                samples = np.linspace(unit['startM'], unit['endM'], 9)
                zones = frame_zones(samples[:, :2], frame['frame_axes'], frame['frame_bounds_local'], frame['frame_inner_bounds_local'])
                unit['observedFixtureZones'] = sorted(set(map(int, zones)))
    return finish()
