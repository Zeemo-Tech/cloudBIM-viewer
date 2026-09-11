"""Layer and straight-cylinder instances within the measured inner steel region.

Horizontal members use direction-conditioned projection tracks. Web seeds are
connected only between the horizontal layers, where junctions cannot join two
rods. Source rows are then assigned to finite cylinders, not image components.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
import time

import numpy as np
from scipy import ndimage, optimize, signal, sparse
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from algorithms.rebar_tracks import diameter_priors, regularize_models, reconcile_tracks, grow_track_ends, track_statistics, remove_explained_fragments

VERSION = 'internal-rebar-tracks-v8-fixture-density-denoising'
PROTECTION_THRESHOLD = .9
TYPES = {'0': '非内部钢筋', '1': '下层钢筋', '2': '上层钢筋', '3': '腹杆', '4': '钢筋（实例待定）', '5': '悬浮噪音'}
ATTRIBUTES = {'internal_type': 'u1', 'internal_instance': '<u4',
              'internal_segment': '<u4', 'internal_confidence': '<f4'}


@dataclass(frozen=True)
class InternalRebarParameters:
    histogram_bin: float = .0015
    seed_linearity: float = .60
    minimum_horizontal_length: float = .025
    maximum_seed_gap: float = .075
    web_connection_radius: float = .0065
    min_radius: float = .0012
    max_radius: float = .009
    assignment_tolerance: float = .0035
    endpoint_margin: float = .005
    model_sample_spacing: float = .005
    horizontal_fit_span: float = .20
    residual_connection_radius: float = .0045
    residual_minimum_length: float = .020
    completion_tolerance: float = .006


def _basis(axis):
    auxiliary = np.eye(3)[np.argmin(np.abs(axis))]
    first = np.cross(axis, auxiliary); first /= np.linalg.norm(first)
    return np.array([first, np.cross(axis, first)])


def _fit_cylinder(points, kind, params):
    """PCA direction plus robust circle on the perpendicular cross section."""
    if len(points) < 12:
        return None
    center = points.mean(axis=0)
    _, vector = np.linalg.eigh((points-center).T @ (points-center))
    axis = vector[:, -1]
    if axis[np.argmax(np.abs(axis))] < 0:
        axis = -axis
    cross = _basis(axis)
    q = (points-center) @ cross.T
    # Centre coordinates avoid the condition loss of world-coordinate circles.
    coeff, *_ = np.linalg.lstsq(np.column_stack((2*q, np.ones(len(q)))), np.sum(q*q, axis=1), rcond=None)
    radius = np.sqrt(max(float(coeff[2] + coeff[:2] @ coeff[:2]), params.min_radius**2))
    initial = np.r_[np.clip(coeff[:2], -.02, .02), np.clip(radius, params.min_radius, params.max_radius)]
    fit = optimize.least_squares(lambda v: np.linalg.norm(q-v[:2], axis=1)-v[2], initial,
        bounds=([-.02, -.02, params.min_radius], [.02, .02, params.max_radius]),
        loss='soft_l1', f_scale=.0006, max_nfev=35)
    center += fit.x[:2] @ cross
    t = (points-center) @ axis
    low, high = np.quantile(t, [.001, .999])
    error = np.abs(np.linalg.norm((points-center)-t[:, None]*axis, axis=1)-fit.x[2])
    return {'type': kind, 'center': center, 'axis': axis, 'low': float(low), 'high': float(high),
            'radius': float(fit.x[2]), 'fitMedianErrorM': float(np.median(error)), 'seedCount': len(points), 'seedPoints': points,
            'radiusAtBound': bool(min(fit.x[2]-params.min_radius,params.max_radius-fit.x[2])<.00005)}


def _height_bands(points, axes, linearity, params):
    horizontal = (np.abs(axes[:, 2]) < .20) & (np.max(np.abs(axes[:, :2]), axis=1) > .94) & (linearity > params.seed_linearity)
    z = points[horizontal, 2]
    if len(z) < 24:
        return [], {'reason': 'insufficient horizontal evidence'}
    origin = float(z.min())-params.histogram_bin
    bins = np.floor((z-origin)/params.histogram_bin).astype(int)
    # Keep boundary modes even when a scan sees only a narrow top arc. Peak
    # finding needs empty bins on both sides of every observed population.
    hist = np.pad(np.bincount(bins),(3,3))
    origin -= 3*params.histogram_bin
    smooth = ndimage.gaussian_filter1d(hist.astype(float), 1.)
    peaks, _ = signal.find_peaks(smooth, prominence=max(4., smooth.max()*.025), distance=3)
    if not len(peaks):
        peaks = np.array([int(np.argmax(smooth))])
    groups = []
    for peak in peaks:
        if groups and (peak-groups[-1][-1])*params.histogram_bin < .017:
            groups[-1].append(peak)
        else:
            groups.append([peak])
    groups = [g for g in groups if smooth[g].sum() >= max(5, smooth.sum()*.012)]
    if not groups:
        return [], {'reason': 'no substantial horizontal height peaks'}
    bands = []
    for g in [groups[0]] + ([groups[-1]] if len(groups) > 1 else []):
        height = origin + (np.average(g, weights=smooth[g])+.5)*params.histogram_bin
        bands.append({'height': float(height), 'low': float(origin+min(g)*params.histogram_bin-.006),
                      'high': float(origin+(max(g)+1)*params.histogram_bin+.006)})
    return bands, {'histogramOriginM': origin, 'histogramBinM': params.histogram_bin,
                   'histogram': hist.tolist(), 'horizontalSeedCells': int(horizontal.sum())}


def _horizontal_models(points, directions, linearity, bands, params, workers):
    jobs = []
    for layer, band in enumerate(bands, 1):
        # Peaks locate layers, not clipping planes. Short raised/crossing bars
        # may lie outside a peak's narrow histogram window.
        low = (bands[layer-2]['height']+band['height'])/2 if layer>1 else -np.inf
        high = (band['height']+bands[layer]['height'])/2 if layer<len(bands) else np.inf
        at_height = (points[:, 2] >= low) & (points[:, 2] < high) & (np.abs(directions[:,2]) < .20)
        for along in (0, 1):
            transverse = 1-along
            seeds = at_height & (np.abs(directions[:, along]) > .95) & (linearity > params.seed_linearity)
            selected = np.flatnonzero(seeds)
            if len(selected) < 12:
                continue
            # Follow gently deviating bars in a direction-conditioned image.
            # The other grid direction is absent, so crossings cannot join rods.
            xy = points[selected][:, [along, transverse]]
            pixel = np.array([.005, .002])
            origin = xy.min(axis=0)-[params.maximum_seed_gap, .01]
            index = np.floor((xy-origin)/pixel).astype(int)
            nx, ny = index.max(axis=0)+[20, 8]
            if nx*ny > 8_000_000:
                continue
            image = np.zeros((ny,nx), bool); image[index[:,1],index[:,0]] = True
            # Grow along the member only: transverse growth joins two close
            # parallel bars and produces a phantom cylinder between their axes.
            joined = ndimage.binary_dilation(image, structure=np.ones((1,3), bool))
            joined = ndimage.binary_closing(joined, structure=np.ones((1,max(3,round(params.maximum_seed_gap/pixel[0]))), bool))
            component, number = ndimage.label(joined, structure=np.ones((3,3), bool))
            labels = component[index[:,1],index[:,0]]
            order = np.argsort(labels, kind='stable'); offsets = np.r_[0,np.cumsum(np.bincount(labels,minlength=number+1))]
            for label in range(1,number+1):
                group = selected[order[offsets[label]:offsets[label+1]]]
                if len(group) < 12 or np.ptp(points[group,along]) < params.minimum_horizontal_length:
                    continue
                jobs.append((points[group],layer,along,len(jobs)))
    def fit_track(job):
        p, layer, along, group = job
        span = np.ptp(p[:,along])
        edges = np.linspace(p[:,along].min(),p[:,along].max(),max(1,int(np.ceil(span/params.horizontal_fit_span)))+1)
        result = []
        for lo,hi in zip(edges[:-1],edges[1:]):
            seed = p[(p[:,along]>=lo-.025)&(p[:,along]<=hi+.025)]
            model = _fit_cylinder(seed,layer,params)
            if model is None or abs(model['axis'][along]) < .98:
                continue
            model['low'],model['high'] = sorted((v-model['center'][along])/model['axis'][along] for v in (lo,hi))
            model['group'] = group
            result.append(model)
        return result
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(fit_track,jobs))
    return [m for group in results for m in group]


def _web_models(points, residual_indices, residual_tree, grid_points, bands, params, workers):
    if len(bands) < 2:
        return [], 0
    gap = bands[1]['low']-bands[0]['high']
    if gap < .012:
        return [], 0
    lower = bands[0]['high'] + min(.006, gap*.15)
    upper = bands[1]['low'] - min(.006, gap*.15)
    mid = np.flatnonzero((points[:, 2] > lower) & (points[:, 2] < upper))
    if len(mid) < 12:
        return [], 0
    # Query the already-built residual tree, then keep only middle-height scope
    # rows. The graph omits horizontal junctions rather than trying to split them.
    reverse = np.full(residual_tree.n, -1, np.int32)
    reverse[residual_indices[mid]] = np.arange(len(mid))
    distance, neighbor = residual_tree.query(grid_points[mid], k=min(24, residual_tree.n),
        distance_upper_bound=params.web_connection_radius, workers=workers)
    if distance.ndim == 1:
        distance, neighbor = distance[:, None], neighbor[:, None]
    valid = np.isfinite(distance) & (neighbor < len(reverse))
    mapped = reverse[np.minimum(neighbor, len(reverse)-1)]
    valid &= mapped >= 0
    row = np.broadcast_to(np.arange(len(mid))[:, None], mapped.shape)[valid]
    graph = sparse.coo_matrix((np.ones(len(row), np.uint8), (row, mapped[valid])), shape=(len(mid), len(mid))).tocsr()
    components, labels = connected_components(graph, directed=False)
    order = np.argsort(labels, kind='stable')
    offsets = np.r_[0, np.cumsum(np.bincount(labels, minlength=components))]
    jobs = []
    for component in range(components):
        rows = mid[order[offsets[component]:offsets[component+1]]]
        if len(rows) < 15 or np.ptp(points[rows, 2]) < min(.018, gap*.35):
            continue
        p = points[rows]
        eigen, vectors = np.linalg.eigh(np.cov(p.T))
        if eigen[-2]/max(eigen[-1], 1.e-12) > .20 or abs(vectors[2, -1]) < .25:
            continue
        jobs.append(p)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        models = list(pool.map(lambda p: _fit_cylinder(p, 3, params), jobs))
    result = []
    for m in models:
        if m is None:
            continue
        heights = [bands[0]['height']-.002, bands[1]['height']+.002]
        m['low'], m['high'] = sorted((z-m['center'][2])/m['axis'][2] for z in heights)
        result.append(m)
    return result, len(mid)


def _split_parallel_points(points, kind, params, depth=0):
    """Separate touching neighborhoods only when two cylinders explain them.

    Splitting one cylinder's visible arc in two yields the same fitted axis, so
    centroid separation alone is insufficient. Require distinct fitted axes,
    overlapping longitudinal spans, and improved surface fits on both halves.
    """
    whole = _fit_cylinder(points,kind,params)
    if whole is None or whole['fitMedianErrorM']<.0008 or depth>=2:
        return [points]
    q=(points-whole['center'])@_basis(whole['axis']).T
    _,vectors=np.linalg.eigh(q.T@q);projection=q@vectors[:,-1]
    halves=projection>np.median(projection)
    if halves.all() or not halves.any():
        return [points]
    centers=np.array([q[~halves].mean(0),q[halves].mean(0)])
    for _ in range(15):
        labels=np.argmin(np.sum((q[:,None]-centers)**2,axis=2),axis=1)
        if min(np.bincount(labels,minlength=2))<12:
            return [points]
        updated=np.array([q[labels==i].mean(0) for i in (0,1)])
        if np.max(np.abs(updated-centers))<1.e-7:
            break
        centers=updated
    parts=[points[labels==i] for i in (0,1)]
    fits=[_fit_cylinder(p,kind,params) for p in parts]
    if any(m is None or m['fitMedianErrorM']>min(.0009,whole['fitMedianErrorM']*.65)
           or m['high']-m['low']<params.residual_minimum_length for m in fits):
        return [points]
    first,second=fits
    if abs(first['axis']@second['axis'])<.985:
        return [points]
    delta=first['center']-second['center'];axis=whole['axis']
    separation=np.linalg.norm(delta-(delta@axis)*axis)
    spans=[((p-whole['center'])@axis) for p in parts]
    overlap=min(s.max() for s in spans)-max(s.min() for s in spans)
    if separation<max(.003,(first['radius']+second['radius'])*.75) or overlap<.5*min(np.ptp(s) for s in spans):
        return [points]
    return [piece for p in parts for piece in _split_parallel_points(p,kind,params,depth+1)]


def _recover_residual_models(points, normals, models, bands, params, workers):
    """Discover omitted thin members before completing existing rod surfaces.

    Unclaimed support cells supply fresh geometric evidence, independent of the
    original direction/linearity seed gate. Only elongated connected components
    create cylinders; collinear fragments can share an existing instance.
    """
    claimed, _, assignment = assign_cylinders(points, normals, models, workers=workers, params=params)
    remaining = np.flatnonzero(claimed == 0)
    if len(remaining) < 12 or not bands:
        return [], {'remainingSupportCells':len(remaining), 'newInstances':0}, assignment
    q = points[remaining]
    tree = cKDTree(q)
    pairs = tree.query_pairs(params.residual_connection_radius, output_type='ndarray')
    graph = sparse.coo_matrix((np.ones(len(pairs),np.uint8),(pairs[:,0],pairs[:,1])),shape=(len(q),len(q))).tocsr()
    number, labels = connected_components(graph, directed=False)
    order = np.argsort(labels,kind='stable'); offsets = np.r_[0,np.cumsum(np.bincount(labels,minlength=number))]
    jobs = []
    for label in range(number):
        p = q[order[offsets[label]:offsets[label+1]]]
        if len(p)<12:
            continue
        center = p.mean(axis=0); eigen, vectors = np.linalg.eigh((p-center).T@(p-center))
        axis = vectors[:,-1]; t=(p-center)@axis
        if np.ptp(t)<params.residual_minimum_length or eigen[-2]>eigen[-1]*.12:
            continue
        kind = 3 if abs(axis[2])>.25 and len(bands)>1 else 1+int(len(bands)>1 and center[2]>(bands[0]['height']+bands[1]['height'])/2)
        # A web instance remains a single straight segment. Horizontal fragments
        # use local fits to follow the same mild bending as primary tracks.
        jobs.append((p,kind,label))
    def fit_component(job):
        p,kind,label=job; result=[]
        for part_id,part in enumerate(_split_parallel_points(p,kind,params)):
            center=part.mean(0);_,vectors=np.linalg.eigh((part-center).T@(part-center));t=(part-center)@vectors[:,-1]
            edges=np.linspace(t.min(),t.max(),2 if kind==3 else max(2,int(np.ceil(np.ptp(t)/params.horizontal_fit_span))+1))
            for lo,hi in zip(edges[:-1],edges[1:]):
                m=_fit_cylinder(part[(t>=lo-.01)&(t<=hi+.01)],kind,params)
                if m is not None and m['fitMedianErrorM']<.0018:
                    m['residualComponent']=(label,part_id); result.append(m)
        return result
    with ThreadPoolExecutor(max_workers=max(1,workers)) as pool:
        candidates=[m for result in pool.map(fit_component,jobs) for m in result]
    next_group=max((m['group'] for m in models),default=-1)+1
    component_groups={}; recovered=[]
    centers=np.array([m['center'] for m in models]).reshape(-1,3)
    axes=np.array([m['axis'] for m in models]).reshape(-1,3)
    kinds=np.array([m['type'] for m in models]); low=np.array([m['low'] for m in models]); high=np.array([m['high'] for m in models])
    for m in candidates:
        component=m.pop('residualComponent')
        if component not in component_groups:
            delta=m['center']-centers; along=np.sum(delta*axes,axis=1)
            across=np.linalg.norm(delta-along[:,None]*axes,axis=1)
            alignment=axes@m['axis']
            candidate_low=along+np.minimum(m['low']*alignment,m['high']*alignment)
            candidate_high=along+np.maximum(m['low']*alignment,m['high']*alignment)
            # Compare finite intervals, not centers: a long continuation can
            # touch an existing endpoint while its center lies far beyond it.
            beyond=np.maximum(low-candidate_high,np.maximum(candidate_low-high,0))
            compatible=(kinds==m['type'])&(np.abs(alignment)>.985)&(across<.0035)&(beyond<.015)
            # Joining web fragments would violate the one-straight-segment unit.
            compatible &= m['type']!=3
            if compatible.any():
                indices=np.flatnonzero(compatible); nearest=indices[np.argmin(across[indices]+beyond[indices])]
                component_groups[component]=models[nearest]['group']
            else:
                component_groups[component]=next_group;next_group+=1
        m['group']=component_groups[component];recovered.append(m)
    added=len(set(component_groups.values())-{m['group'] for m in models})
    return recovered, {'remainingSupportCells':len(remaining),'candidateComponents':len(jobs),
                       'addedSegments':len(recovered),'newInstances':added}, {'tree':tree,'assignment':assignment}


def assign_cylinders(points, normals, models, *, workers=1, params=None, cache=None, tangents=None, linearity=None, instance_margin=0.):
    """Finite-cylinder surface competition with sign-independent source normals."""
    params = params or InternalRebarParameters()
    labels = np.zeros(len(points), np.uint32)
    confidence = np.zeros(len(points), np.float32)
    if not models or not len(points):
        return labels, confidence, None
    center = np.array([m['center'] for m in models]); axis = np.array([m['axis'] for m in models])
    low = np.array([m['low'] for m in models]); high = np.array([m['high'] for m in models])
    radius = np.array([m['radius'] for m in models])
    physical_ids = np.array([m.get('instanceId', i+1) for i,m in enumerate(models)])
    ambiguous = np.zeros(len(points),bool)
    if cache is None:
        samples, owners = [], []
        for i, m in enumerate(models):
            t = np.linspace(low[i], high[i], max(2, int(np.ceil((high[i]-low[i])/params.model_sample_spacing))+1))
            samples.append(center[i]+t[:, None]*axis[i]); owners.append(np.full(len(t), i, np.int32))
        owners = np.concatenate(owners); tree = cKDTree(np.concatenate(samples))
    else:
        owners,tree=cache['sample_owners'],cache['tree']
    search_radius = float(radius.max())+params.assignment_tolerance+params.endpoint_margin+params.model_sample_spacing

    def chunk(start):
        stop = min(start+16384, len(points))
        pending = np.arange(start,stop); k=min(12,tree.n)
        while len(pending):
            distances, neighbors = tree.query(points[pending], k=k, distance_upper_bound=search_radius, workers=1)
            if k == 1:
                distances,neighbors = distances[:,None],neighbors[:,None]
            # Expand only crowded queries. Every possible cylinder must get a
            # vote; repeated samples of one axis may not hide another owner.
            complete = ~np.isfinite(distances[:,-1]) if k<tree.n else np.ones(len(pending),bool)
            selected = pending[complete]
            neighbors = neighbors[complete]; finite = np.isfinite(distances[complete])
            if len(selected):
                model = owners[np.minimum(neighbors,tree.n-1)]
                delta = points[selected,None,:]-center[model]
                along = np.sum(delta*axis[model],axis=2)
                radial = delta-along[:,:,None]*axis[model]
                distance = np.linalg.norm(radial,axis=2)
                error = np.abs(distance-radius[model])
                beyond = np.maximum(low[model]-along,np.maximum(along-high[model],0))
                axis_normal = np.abs(np.sum(normals[selected,None,:]*axis[model],axis=2))
                radial_normal=np.abs(np.sum(normals[selected,None,:]*radial,axis=2))/np.maximum(distance,1.e-9)
                score = error+.0015*axis_normal**2+.0007*(1-np.clip(radial_normal,0,1)**2)+2*beyond+model*1.e-12
                if tangents is not None:
                    alignment=np.sum(tangents[selected,None,:]*axis[model],axis=2)
                    strength=np.clip((linearity[selected]-.35)/.5,0,1) if linearity is not None else np.ones(len(selected))
                    score+=.0025*strength[:,None]*(1-np.clip(alignment,-1,1)**2)
                valid = finite & (error<=params.assignment_tolerance) & (beyond<=params.endpoint_margin)
                score[~valid]=np.inf
                best=np.argmin(score,axis=1); rows=np.arange(len(selected)); accepted=np.isfinite(score[rows,best])
                if instance_margin > 0:
                    alternatives = np.where(physical_ids[model] != physical_ids[model[rows,best]][:,None], score, np.inf)
                    runner_up = np.min(alternatives,axis=1)
                    ties = accepted & (runner_up <= score[rows,best] + instance_margin)
                    ambiguous[selected] = ties
                    accepted &= ~ties
                labels[selected]=np.where(accepted,model[rows,best]+1,0)
                confidence[selected]=np.where(accepted,np.clip(1-score[rows,best]/(params.assignment_tolerance+.002),0,1),0)
            pending=pending[~complete]; k=min(k*2,tree.n)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(chunk, range(0, len(points), 16384)))
    return labels, confidence, {'tree': tree, 'sample_owners': owners, 'ambiguous': ambiguous}


def segment_internal_rebar(context, *, workers=1, params=None, output=None, progress=None):
    params = params or InternalRebarParameters()
    progress = progress or (lambda *args: None)
    if context.refined_class is None or context.refined_zone is None:
        raise ValueError('内部钢筋实例需要融合类别与共享分区')
    started = time.perf_counter(); timings = {}
    count = len(context.positions)
    output = output if output is not None else {name: np.zeros(count, dtype) for name, dtype in ATTRIBUTES.items()}
    for values in output.values():
        values[:] = 0
    scope = np.flatnonzero((context.refined_class == 3) & (context.refined_zone == 1))
    output['internal_type'][scope] = 4
    models, bands, histogram = [], [], {}
    model_cache = None
    recovery_cache = None
    track_report = {}
    track_tree = None
    diagnostics = {'scope': 'refined_class=3 AND refined_zone=1; strict inner frame',
                   'reusedResidualTree': False, 'webInstanceUnit': 'one straight diagonal segment'}
    if len(scope):
        t0 = time.perf_counter()
        progress('第 5 步：提取内部钢筋 / 高度分层', 0, len(scope))
        cache = context.classification_cache; grid = cache['grid']; residual = cache['residual_ids']
        occupied = np.bincount(grid.source_to_cell[scope], minlength=len(grid.points)) > 0
        local_ids = np.flatnonzero(occupied[residual]); cells = residual[local_ids]
        rotation = np.eye(3); rotation[:2, :2] = context.region_cache['frame_axes']
        points = (grid.points[cells]+grid.origin) @ rotation.T
        directions = cache['features']['axis'][local_ids] @ rotation.T
        linearity = cache['features']['linearity'][local_ids]
        bands, histogram = _height_bands(points, directions, linearity, params)
        timings['layersS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress('第 5 步：水平钢筋圆柱实例', 0, len(cells))
        models = _horizontal_models(points, directions, linearity, bands, params, workers)
        timings['horizontalModelsS'] = time.perf_counter()-t0
        t0 = time.perf_counter()
        progress('第 5 步：腹杆分段圆柱实例', 0, len(cells))
        webs, mid_count = _web_models(points, local_ids, grid.tree, grid.points[cells], bands, params, workers)
        next_group = max((m['group'] for m in models),default=-1)+1
        for i,web in enumerate(webs):
            web['group']=next_group+i
        models += webs
        timings['webModelsS'] = time.perf_counter()-t0
        diagnostics.update(reusedResidualTree=True, scopeSupportCells=len(cells), webMiddleCells=mid_count)
        t0=time.perf_counter()
        priors,prior_report=diameter_priors(models,getattr(context,'dimension_priors',None))
        models=regularize_models(models,priors,params,workers,_fit_cylinder,_split_parallel_points)
        timings['diameterRefinementS']=time.perf_counter()-t0
        t0=time.perf_counter()
        progress('第 5 步：补建遗漏的细长钢筋实例',0,len(cells))
        # Source normals are already cached; choose a deterministic source row
        # from each occupied cell, rather than estimating a second normal field.
        representative=np.full(len(grid.points),count,np.int64)
        np.minimum.at(representative,grid.source_to_cell[scope],scope)
        support_normals=context.normals[representative[cells]]@rotation.T
        recovered,recovery,recovery_cache=_recover_residual_models(points,support_normals,models,bands,params,workers)
        recovered=regularize_models(recovered,priors,params,workers,_fit_cylinder,_split_parallel_points)
        models+=recovered
        diagnostics['recovery']=recovery
        timings['residualModelsS']=time.perf_counter()-t0
        t0=time.perf_counter()
        progress('第 5 步：轴向、直径与整根连续性',0,len(models))
        track_report=reconcile_tracks(models,params)
        models,explained=remove_explained_fragments(models)
        track_report.update(explained)
        track_report['horizontalTracks']=len({m['group'] for m in models if m['type']!=3})
        growth,track_tree=grow_track_ends(models,points,params,workers)
        track_report.update(growth)
        track_report.update(track_statistics(models))
        track_report['diameterPriors']=prior_report
        track_report['ifcPriors']=getattr(context,'dimension_priors',None)
        timings['trackRefinementS']=time.perf_counter()-t0
        # Stable IDs derive from geometry, never component traversal or thread order.
        groups = {}
        for model in models:
            groups.setdefault(model['group'],[]).append(model)
        ordered = sorted(groups, key=lambda g:(groups[g][0]['type'], *np.round(np.mean([m['center'] for m in groups[g]],axis=0),5)))
        group_ids = {g:i+1 for i,g in enumerate(ordered)}
        models.sort(key=lambda m:(group_ids[m['group']],*np.round(m['center'],5)))
        for model in models:
            model['instanceId'] = group_ids[model['group']]
            model['center'] = model['center'] @ rotation
            model['axis'] = model['axis'] @ rotation
        t0 = time.perf_counter()
        progress('第 5 步：源点圆柱归属 / 交点竞争', 0, len(scope))
        residual_lookup=np.full(len(grid.points),-1,np.int32)
        residual_lookup[residual]=np.arange(len(residual))
        source_features=residual_lookup[grid.source_to_cell[scope]]
        source_tangents=(cache['features']['axis'][np.maximum(source_features,0)] if len(residual)
                         else np.zeros((len(scope),3),np.float32))
        source_linearity=(np.where(source_features>=0,cache['features']['linearity'][np.maximum(source_features,0)],0)
                          if len(residual) else np.zeros(len(scope),np.float32))
        instance, confidence, model_cache = assign_cylinders(context.positions[scope], context.normals[scope], models,
            workers=workers, params=params, tangents=source_tangents,linearity=source_linearity)
        # Complete small surface/junction deviations only after missing models
        # exist. The finite-cylinder competition and endpoint bound still apply.
        missing=np.flatnonzero(instance==0)
        completion_params=replace(params,assignment_tolerance=params.completion_tolerance,endpoint_margin=.010)
        completion,score,_=assign_cylinders(context.positions[scope[missing]],context.normals[scope[missing]],models,
            workers=workers,params=completion_params,cache=model_cache,tangents=source_tangents[missing],linearity=source_linearity[missing])
        instance[missing]=completion;confidence[missing]=np.minimum(score,.45)
        diagnostics['completedSurfacePoints']=int(np.count_nonzero(completion))
        types = np.array([4]+[m['type'] for m in models], np.uint8)
        output['internal_type'][scope] = types[instance]
        instance_map=np.array([0]+[m['instanceId'] for m in models],np.uint32)
        output['internal_instance'][scope] = instance_map[instance]
        output['internal_segment'][scope] = instance
        output['internal_confidence'][scope] = confidence
        timings['sourceAssignmentS'] = time.perf_counter()-t0
    segment_counts = np.bincount(output['internal_segment'][scope], minlength=len(models)+1)
    fused_score = getattr(context, 'fused_steel_score', None)
    measured_counts = high_counts = None
    if fused_score is not None:
        measured = np.asarray(fused_score)[scope] >= .65 - 1.e-6
        high = np.asarray(fused_score)[scope] >= PROTECTION_THRESHOLD
        measured_counts = np.bincount(output['internal_segment'][scope][measured], minlength=len(models)+1)
        high_counts = np.bincount(output['internal_segment'][scope][high], minlength=len(models)+1)
    segments, instances = [], []
    for i, m in enumerate(models, 1):
        start = m['center']+m['low']*m['axis']; end = m['center']+m['high']*m['axis']
        segments.append({'id': i, 'instanceId': m['instanceId'], 'type': m['type'], 'startM': start.tolist(), 'endM': end.tolist(),
                         'radiusM': m['radius'], 'pointCount': int(segment_counts[i]), 'fitMedianErrorM': m['fitMedianErrorM']})
        if measured_counts is not None:
            segments[-1].update(measuredSupportPointCount=int(measured_counts[i]),
                                highConfidencePointCount=int(high_counts[i]))
    for instance_id in sorted({m['instanceId'] for m in models}):
        parts = [s for s in segments if s['instanceId']==instance_id]
        info=next(m['trackInfo'] for m in models if m['instanceId']==instance_id)
        instances.append({'id':instance_id,'type':parts[0]['type'],'pointCount':sum(s['pointCount'] for s in parts),
            'segmentIds':[s['id'] for s in parts], 'lengthM':info['lengthM'],'diameterM':info['diameterM'],
            'family':info['family'],'lengthAnomaly':info['lengthAnomaly'],
            'modelKind':'cylinder' if len(parts)==1 else 'piecewise-cylinder'})
    from .multiview_floating_noise import multiview_noise_mask, observed_cylinder_support, observed_cylinder_continuation
    t0 = time.perf_counter()
    progress('第 5 步：清理无结构支撑的悬浮点', 0, len(scope))
    support_rows = np.flatnonzero(context.refined_class == 3)
    support_points = context.positions[support_rows]
    interior = context.refined_zone[support_rows] == 1
    observed = observed_cylinder_support(support_points, output['internal_segment'][support_rows],
                                         output['internal_confidence'][support_rows], segments)
    continuation = observed_cylinder_continuation(support_points,
        None if context.normals is None else context.normals[support_rows],
        output['internal_segment'][support_rows], segments, observed, workers=workers)
    # The frame bounds instance fitting, not denoising. Exterior floating caps
    # must be reviewed too; fusion scores alone cannot turn them into anchors.
    fixture_rows = np.flatnonzero(context.refined_class == 2)
    removed, denoising = multiview_noise_mask(support_points, review_mask=np.ones(len(support_rows), bool),
        normals=None if context.normals is None else context.normals[support_rows],
        fixture_points=context.positions[fixture_rows],
        fixture_normals=None if context.normals is None else context.normals[fixture_rows],
        observed_support_mask=observed, workers=workers,
        observed_continuation_mask=continuation,
        steel_scores=None if fused_score is None else np.asarray(fused_score)[support_rows], progress=progress)
    denoising['protectionThreshold'] = PROTECTION_THRESHOLD
    denoising.update(scope='all retained steel; internal and exterior candidates share spatial review',
        exteriorReviewEnabled=True,
        interiorCandidatePointCount=int(np.count_nonzero(interior)),
        exteriorCandidatePointCount=int(np.count_nonzero(~interior)),
        interiorRemovedPointCount=int(np.count_nonzero(removed & interior)),
        exteriorRemovedPointCount=int(np.count_nonzero(removed & ~interior)))
    noise_ids = support_rows[removed]
    output['internal_type'][noise_ids] = 5
    for name in ('internal_instance', 'internal_segment', 'internal_confidence'):
        output[name][noise_ids] = 0
    # Assigned rows, including high scores, can be rejected; report surviving
    # ownership rather than the pre-denoising cylinder counts.
    final_counts = np.bincount(output['internal_segment'][scope], minlength=len(models)+1)
    if measured_counts is not None:
        measured_counts = np.bincount(output['internal_segment'][scope][measured], minlength=len(models)+1)
        high_counts = np.bincount(output['internal_segment'][scope][high], minlength=len(models)+1)
    for segment in segments:
        segment['pointCount'] = int(final_counts[segment['id']])
        if measured_counts is not None:
            segment['measuredSupportPointCount'] = int(measured_counts[segment['id']])
            segment['highConfidencePointCount'] = int(high_counts[segment['id']])
    for instance in instances:
        instance['segmentIds'] = [s for s in instance['segmentIds'] if final_counts[s] > 0]
        instance['pointCount'] = sum(int(final_counts[s]) for s in instance['segmentIds'])
    instances = [instance for instance in instances if instance['pointCount'] > 0]
    segments = [segment for segment in segments if segment['pointCount'] > 0]
    timings['floatingDenoiseS'] = time.perf_counter()-t0
    counts = np.bincount(output['internal_type'][scope], minlength=6)
    report = {'version': VERSION, 'pointCount': len(scope), 'types': TYPES,
        'counts': dict(zip(('lower', 'upper', 'web', 'unassigned', 'noise'), map(int, counts[1:6]))),
        'denoising': denoising,
        'instanceCount': len(instances), 'segmentCount': len(segments), 'instances': instances, 'segments': segments,
        'layers': {'lowerM': bands[0]['height'] if bands else None, 'upperM': bands[1]['height'] if len(bands)>1 else None,
                   'bands': bands}, 'heightHistogram': histogram, 'diagnostics': diagnostics,
        'timings': timings, 'elapsedS': time.perf_counter()-started, 'parameters': asdict(params),
        'tracks':track_report,
        'confidenceMeaning': 'geometric fit score, not a calibrated probability; unassigned points retained',
        'images': {}}
    for name, values in output.items():
        setattr(context, name, values)
    context.internal_rebar_cache = {'models': models, 'assignment': model_cache, 'scope_ids': scope,'recovery':recovery_cache,'track_tree':track_tree}
    return report
