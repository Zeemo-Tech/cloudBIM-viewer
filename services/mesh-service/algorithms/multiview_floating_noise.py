"""Observed-component denoising with frozen measured support and sliced side views.

All retained steel, including exterior pieces, supplies context. Only rows in
review_mask can be removed. A high fusion score raises the evidence threshold;
it is not a substitute for measured shape or physical continuation.
"""
from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np
from scipy.ndimage import uniform_filter
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree, ConvexHull, QhullError
from .spatial_keys import unique_integer_rows


VERSION = 'floating-multiview-v3-conservative-top-view'


@dataclass(frozen=True)
class Parameters:
    voxel_size: float = .002
    connection_radius: float = .004
    slice_width: float = .020
    view_angles: tuple = (0., 30., 60., 90., 120., 150.)
    projected_support_margin: float = .006
    observed_support_radius: float = .003
    minimum_negative_views: int = 3
    high_score_negative_views: int = 4
    local_round_radius: float = .014
    top_view_margin: float = .012
    top_view_fixture_fraction: float = .25
    density_window_cells: int = 13
    density_fixture_fraction: float = .50
    high_score_density_fixture_fraction: float = .65
    density_minimum_fixture_voxels: int = 8
    fixture_surface_radius: float = .006
    fixture_surface_error: float = .0015
    fixture_normal_alignment: float = .90
    fixture_component_fraction: float = .65
    high_score_fixture_component_fraction: float = .90


def observed_cylinder_support(points, segment_ids, confidence, segments, *,
                              bin_size=.004, maximum_gap=.010, minimum_span=.020):
    """Freeze fitted surface samples only along actually occupied axial runs.

    A long fitted cylinder is not evidence in its unobserved gaps. In particular,
    even perfect-fit/high-score echoes in one isolated bin cannot become anchors.
    The 10 mm gap tolerates sparse sampling; a run needs three bins and 20 mm span.
    """
    points = np.asarray(points)
    segment_ids, confidence = np.asarray(segment_ids), np.asarray(confidence)
    supported = np.zeros(len(points), bool)
    rows = np.flatnonzero((segment_ids > 0) & (confidence >= .65-1e-6))
    order = rows[np.argsort(segment_ids[rows], kind='stable')]
    ids = segment_ids[order]
    for segment in segments:
        low = np.searchsorted(ids, segment['id'], side='left')
        high = np.searchsorted(ids, segment['id'], side='right')
        members = order[low:high]
        if not len(members):
            continue
        start = np.asarray(segment['startM'])
        axis = np.asarray(segment['endM'])-start
        length = np.linalg.norm(axis)
        if length <= 1e-12:
            continue
        axial = (points[members]-start)@(axis/length)
        bins, inverse = np.unique(np.floor(axial/bin_size).astype(np.int64), return_inverse=True)
        breaks = np.r_[0, np.flatnonzero(np.diff(bins)*bin_size > maximum_gap)+1, len(bins)]
        reliable = np.zeros(len(bins), bool)
        for begin, end in zip(breaks[:-1], breaks[1:]):
            if end-begin >= 3 and (bins[end-1]-bins[begin])*bin_size >= minimum_span:
                reliable[begin:end] = True
        supported[members] = reliable[inverse]
    return supported


def observed_cylinder_continuation(points, normals, segment_ids, segments, observed, *,
                                   maximum_gap=.010, surface_error=.001, workers=1):
    """Recognize exposed surface returns continuing an actually observed run.

    A nearby fixture can meet the same local tangent plane at a rod end. Its
    density must not overrule returns following the measured cylinder. Bound
    extension by the nearest frozen sample (not the fitted segment endpoint),
    and require radius plus radial-normal agreement. This does not interpolate
    an empty fitted span or make fusion scores into geometric anchors.
    """
    protected = np.zeros(len(points), bool)
    if normals is None or not np.any(observed) or not segments:
        return protected
    anchor_rows = np.flatnonzero(observed)
    distance, nearest = cKDTree(points[anchor_rows]).query(points, k=1,
        distance_upper_bound=maximum_gap, workers=workers)
    rows = np.flatnonzero(np.isfinite(distance) & ~observed)
    if not len(rows):
        return protected
    owners = segment_ids[anchor_rows[nearest[rows]]]
    order = np.argsort(owners, kind='stable'); sorted_owners = owners[order]
    for segment in segments:
        low, high = np.searchsorted(sorted_owners, [segment['id'], segment['id']+1])
        members = rows[order[low:high]]
        if not len(members):
            continue
        start = np.asarray(segment['startM'])
        axis = np.asarray(segment['endM'])-start
        length = np.linalg.norm(axis)
        if length <= 1e-12:
            continue
        axis /= length
        delta = points[members]-start
        radial = delta-(delta@axis)[:, None]*axis
        radius = np.linalg.norm(radial, axis=1)
        nn = normals[members]; norm = np.linalg.norm(nn, axis=1)
        agreement = np.abs(np.sum(radial*nn, axis=1))/np.maximum(radius*norm, 1e-12)
        protected[members] = (np.isfinite(nn).all(axis=1) & (norm > .5) &
            (np.abs(radius-segment['radiusM']) <= surface_error) & (agreement >= .85))
    return protected


def _components(points, p):
    keys, inverse = unique_integer_rows(np.floor((points-points.min(0))/p.voxel_size).astype(np.int64),
                                        return_inverse=True)
    mass = np.bincount(inverse)
    centers = np.column_stack([np.bincount(inverse, weights=points[:, i])/mass for i in range(3)])
    pairs = cKDTree(centers).query_pairs(p.connection_radius, output_type='ndarray')
    graph = coo_matrix((np.ones(len(pairs), np.uint8), (pairs[:, 0], pairs[:, 1])),
                       shape=(len(keys), len(keys))).tocsr()
    count, labels = connected_components(graph, directed=False)
    return centers, inverse, labels, count


def _round_fragment(points, normals, *, minimum_length=.004, surface_error=.0012,
                    normal_spread=.05):
    """Independent cross-section evidence protects even a short exposed stub."""
    if normals is None or len(points) < 8:
        return False
    lengths = np.linalg.norm(normals, axis=1)
    valid = np.isfinite(normals).all(axis=1) & (lengths > .5)
    if np.count_nonzero(valid) < 8:
        return False
    nn = normals[valid]/lengths[valid, None]
    values, axes = np.linalg.eigh(nn.T@nn/len(nn))
    if values[1] < normal_spread or values[0] > min(.06, .15*values[1]):
        return False
    axis = axes[:, 0]
    centered = points-points.mean(0)
    if np.ptp(centered@axis) < minimum_length:
        return False
    cross = axes[:, 1:]
    xy = centered@cross
    coefficients, *_ = np.linalg.lstsq(np.column_stack((2*xy, np.ones(len(xy)))),
                                      np.sum(xy*xy, axis=1), rcond=None)
    radius2 = coefficients[2]+coefficients[:2]@coefficients[:2]
    if not .0012**2 <= radius2 <= .012**2:
        return False
    radial = xy-coefficients[:2]
    radius = np.sqrt(radius2)
    residual = np.abs(np.linalg.norm(radial, axis=1)-radius)
    radial3 = radial[valid]@cross.T
    alignment = np.abs(np.sum(radial3*nn, axis=1))/np.maximum(np.linalg.norm(radial3, axis=1), 1e-12)
    return bool(np.quantile(residual, .8) <= surface_error and np.mean(alignment >= .75) >= .8)


def _round_geometry(points):
    """A measured partial circular section remains useful with noisy normals."""
    if len(points) < 10:
        return False
    centered = points-points.mean(0)
    values, axes = np.linalg.eigh(centered.T@centered/len(points))
    if values[2] < 1.5*values[1] or np.ptp(centered@axes[:, 2]) < .012:
        return False
    xy = centered@axes[:, :2]
    coefficients, *_ = np.linalg.lstsq(np.column_stack((2*xy, np.ones(len(xy)))),
                                      np.sum(xy*xy, axis=1), rcond=None)
    radius2 = coefficients[2]+coefficients[:2]@coefficients[:2]
    if not .0012**2 <= radius2 <= .012**2:
        return False
    radial = xy-coefficients[:2]
    lengths = np.linalg.norm(radial, axis=1)
    unit = radial/np.maximum(lengths[:, None], 1e-12)
    spread = np.linalg.eigvalsh(unit.T@unit/len(unit))[0]
    return bool(spread >= .08 and np.quantile(np.abs(lengths-np.sqrt(radius2)), .8) <= .0008)


def _bent_fragment(points, normals):
    """A hook may contain several locally round directions, not one cylinder."""
    if normals is None or len(points) < 16:
        return False
    tree = cKDTree(points)
    supported = np.zeros(len(points), bool)
    for row in np.linspace(0, len(points)-1, min(64, len(points)), dtype=int):
        neighbors = tree.query_ball_point(points[row], .014)
        if _round_fragment(points[neighbors], normals[neighbors], minimum_length=.012,
                           surface_error=.0006, normal_spread=.1):
            supported[neighbors] = True
    # Mixed/occluded components need not expose a full circle everywhere. This
    # is frozen, normal-and-surface evidence, not just a short fitted arc in XYZ.
    return bool(np.mean(supported) >= .3)


def _definite_nonrod(points, normals):
    """Positive ball/broad-sheet evidence; a failed cylinder fit is inconclusive."""
    if len(points) < 8 or normals is None:
        return False
    lengths = np.linalg.norm(normals, axis=1)
    valid = np.isfinite(normals).all(axis=1) & (lengths > .5)
    if valid.sum() < 8:
        return False
    nn = normals[valid] / lengths[valid, None]
    normal_values = np.linalg.eigvalsh(nn.T @ nn / len(nn))
    centered = points - points.mean(0)
    values, axes = np.linalg.eigh(centered.T @ centered / len(points))
    extent = np.ptp(centered @ axes, axis=0)
    ball = values[0] > .35 * values[2] and normal_values[0] > .15
    sheet = (extent[1] >= .020 and values[0] < .04 * values[1]
             and normal_values[2] > .95)
    return bool(ball or sheet)


def _local_round_support(points, normals, high_fraction, p):
    """Rescue measured rod patches inside mixed components, never the whole island."""
    supported = np.zeros(len(points), bool)
    if normals is None or len(points) < 8 or not np.any(high_fraction):
        return supported
    tree = cKDTree(points)
    for row in range(len(points)):
        neighbors = tree.query_ball_point(points[row], p.local_round_radius)
        if np.mean(high_fraction[neighbors]) < .25:
            continue
        if _round_fragment(points[neighbors], normals[neighbors], minimum_length=.008,
                           surface_error=.0008, normal_spread=.08):
            supported[neighbors] = True
    return supported


def _outside_top_footprint(points, reference, margin):
    """Conservative XY hull of frozen measured steel; no candidate self-support."""
    outside = np.zeros(len(points), bool)
    if len(reference) < 3:
        return outside, 0
    # Translate before Qhull/equation evaluation for survey coordinates.
    origin = reference[:, :2].mean(0)
    try:
        hull = ConvexHull(reference[:, :2] - origin)
    except QhullError:
        return outside, 0
    for equation in hull.equations:
        outside |= ((points[:, :2] - origin) @ equation[:2] + equation[2]) > margin
    return outside, len(hull.vertices)


def _view_support(centers, reliable, angle, p):
    """Compare islands with independent observed traces in overlapping slices.

    A candidate never supplies its own projected support. Continuous 2D distance
    queries on occupied 3D voxel centroids avoid raster phase/edge artifacts.
    """
    radians = np.deg2rad(angle)
    u = centers[:, 0]*np.cos(radians)+centers[:, 1]*np.sin(radians)
    depth = -centers[:, 0]*np.sin(radians)+centers[:, 1]*np.cos(radians)
    half_slice = p.slice_width/2
    bins = np.floor((depth-depth.min())/half_slice).astype(np.int64)
    order = np.argsort(bins, kind='stable')
    sorted_bins = bins[order]
    supported = np.zeros(len(centers), bool)
    slices = 0
    for key in np.unique(np.r_[bins-1, bins]):
        low, high = np.searchsorted(sorted_bins, [key, key+2])
        rows = order[low:high]
        if not len(rows):
            continue
        xy = np.column_stack((u[rows], centers[rows, 2]))
        anchors = reliable[rows]
        supported[rows[anchors]] = True
        if anchors.any() and (~anchors).any():
            distance = cKDTree(xy[anchors]).query(xy[~anchors], k=1, workers=1)[0]
            supported[rows[~anchors]] |= distance <= p.projected_support_margin
        slices += 1
    return supported, slices


def _fixture_voxels(points, normals, p):
    """Only independently classified fixture surfaces with valid normals count."""
    if points is None:
        return np.empty((0, 3)), np.empty((0, 3))
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1:] != (3,) or not np.isfinite(points).all():
        raise ValueError('fixture_points must be finite Nx3 coordinates')
    if normals is None:
        return np.empty((0, 3)), np.empty((0, 3))
    normals = np.asarray(normals, dtype=float)
    if normals.shape != points.shape:
        raise ValueError('fixture_normals must correspond to fixture XYZ')
    lengths = np.linalg.norm(normals, axis=1)
    valid = np.isfinite(normals).all(axis=1) & (lengths > .5)
    points, normals = points[valid], normals[valid]/lengths[valid, None]
    if not len(points):
        return points, normals
    _, first, inverse = unique_integer_rows(np.floor(points/p.voxel_size).astype(np.int64),
                                            return_index=True, return_inverse=True)
    mass = np.bincount(inverse)
    centers = np.column_stack([np.bincount(inverse, weights=points[:, i])/mass for i in range(3)])
    return centers, normals[first]


def _fixture_surface_support(centers, normals, fixture, fixture_normals, p, workers):
    """Require nearby samples to agree on both tangent planes, not just distance.

    Three distinct occupied fixture voxels must support each candidate voxel.
    Unsigned normal agreement tolerates arbitrary normal orientation. A touching
    rod has limited contact coverage; a misclassified plate lip follows the
    fixture surface over most of its component.
    """
    supported = np.zeros(len(centers), bool)
    if normals is None or not len(fixture):
        return supported
    lengths = np.linalg.norm(normals, axis=1)
    valid_normals = np.isfinite(normals).all(axis=1) & (lengths > .5)
    unit = np.zeros_like(normals, dtype=float)
    unit[valid_normals] = normals[valid_normals]/lengths[valid_normals, None]
    tree = cKDTree(fixture)
    for start in range(0, len(centers), 8192):
        stop = min(start+8192, len(centers))
        distance, neighbors = tree.query(centers[start:stop], k=16,
            distance_upper_bound=p.fixture_surface_radius, workers=workers)
        valid = np.isfinite(distance) & valid_normals[start:stop, None]
        neighbors = np.minimum(neighbors, len(fixture)-1)
        delta = fixture[neighbors]-centers[start:stop, None]
        nn, other = unit[start:stop, None], fixture_normals[neighbors]
        valid &= np.abs(np.sum(nn*other, axis=2)) >= p.fixture_normal_alignment
        valid &= np.abs(np.sum(delta*nn, axis=2)) <= p.fixture_surface_error
        valid &= np.abs(np.sum(delta*other, axis=2)) <= p.fixture_surface_error
        supported[start:stop] = valid.sum(axis=1) >= 3
    return supported


def _density_view(steel, fixture, angle, p):
    """Contrast occupied steel/fixture layers in each overlapping depth slice.

    Count occupied 3D cells, not raw returns: repeated echoes cannot manufacture
    density. Both overlapping slices must show fixture dominance. The nearby
    fixture layer is positive contrary evidence only when paired with 3D surface
    continuation; low steel density by itself never rejects an exposed rod.
    """
    ordinary = np.zeros(len(steel), bool)
    strong = ordinary.copy()
    if not len(fixture):
        return ordinary, strong
    points = np.vstack((steel, fixture))
    radians = np.deg2rad(angle)
    u = points[:, 0]*np.cos(radians)+points[:, 1]*np.sin(radians)
    depth = -points[:, 0]*np.sin(radians)+points[:, 1]*np.cos(radians)
    bins = np.floor((depth-depth.min())/(p.slice_width/2)).astype(np.int64)
    order = np.argsort(bins, kind='stable'); sorted_bins = bins[order]
    uv = np.floor(np.column_stack((u-u.min(), points[:, 2]-points[:, 2].min()))/p.voxel_size).astype(np.int64)
    ordinary[:] = True; strong[:] = True
    window = p.density_window_cells
    for key in np.unique(np.r_[bins-1, bins]):
        low, high = np.searchsorted(sorted_bins, [key, key+2])
        rows = order[low:high]
        is_fixture = rows >= len(steel)
        source = rows[~is_fixture]
        if not len(source):
            continue
        if not is_fixture.any():
            ordinary[source] = False; strong[source] = False
            continue
        origin = uv[rows].min(0); shape = uv[rows].max(0)-origin+1
        xy = uv[rows]-origin; query = uv[source]-origin
        # Widely separated islands must not allocate an unbounded dense image.
        if int(shape[0])*int(shape[1]) > 2_000_000:
            fd = cKDTree(xy[is_fixture]).query_ball_point(query, window//2, p=np.inf, return_length=True)
            sd = cKDTree(xy[~is_fixture]).query_ball_point(query, window//2, p=np.inf, return_length=True)
        else:
            code = xy[:, 0]*shape[1]+xy[:, 1]
            counts = []
            for mask in (is_fixture, ~is_fixture):
                raster = np.bincount(code[mask], minlength=int(shape.prod())).reshape(shape).astype(float)
                local = uniform_filter(raster, size=window, mode='constant')*window**2
                counts.append(local[query[:, 0], query[:, 1]])
            fd, sd = counts
        ratio = fd/np.maximum(fd+sd, 1)
        sufficient = fd >= p.density_minimum_fixture_voxels-1e-6
        ordinary[source] &= sufficient & (ratio >= p.density_fixture_fraction-1e-6)
        strong[source] &= sufficient & (ratio >= p.high_score_density_fixture_fraction-1e-6)
    return ordinary, strong


def multiview_noise_mask(points, *, review_mask, normals=None, steel_scores=None,
                         observed_support_mask=None, fixture_points=None, fixture_normals=None,
                         observed_continuation_mask=None, exterior_mask=None,
                         workers=1, params=None, progress=None):
    """Return original-row noise decisions and an auditable component report."""
    p = params or Parameters()
    started = time.perf_counter()
    progress = progress or (lambda *args: None)
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1:] != (3,) or not np.isfinite(points).all():
        raise ValueError('points must be finite Nx3 coordinates')
    review = np.asarray(review_mask, dtype=bool)
    if review.shape != (len(points),):
        raise ValueError('review_mask must correspond to source rows')
    scores = np.zeros(len(points)) if steel_scores is None else np.asarray(steel_scores)
    if scores.shape != (len(points),) or not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
        raise ValueError('steel_scores must be row-aligned values in 0..1')
    if normals is not None:
        normals = np.asarray(normals)
        if normals.shape != points.shape:
            raise ValueError('normals must correspond to source XYZ')
    observed = None if observed_support_mask is None else np.asarray(observed_support_mask, dtype=bool)
    if observed is not None and observed.shape != (len(points),):
        raise ValueError('observed_support_mask must correspond to source rows')
    continuation = (np.zeros(len(points), bool) if observed_continuation_mask is None
                    else np.asarray(observed_continuation_mask, dtype=bool))
    if continuation.shape != (len(points),):
        raise ValueError('observed_continuation_mask must correspond to source rows')
    exterior = np.zeros(len(points), bool) if exterior_mask is None else np.asarray(exterior_mask, dtype=bool)
    if exterior.shape != (len(points),):
        raise ValueError('exterior_mask must correspond to source rows')
    high = scores >= .9-1e-6
    removed = np.zeros(len(points), bool)
    report = dict(version=VERSION, candidatePointCount=int(review.sum()),
                  reviewedCandidatePointCount=int(review.sum()), removedPointCount=0,
                  highScoreCandidatePointCount=int(np.count_nonzero(review & high)),
                  highScoreRemovedPointCount=0, protectedCandidatePointCount=0,
                  blockedRemovalPointCount=0, contextPointCount=len(points),
                  exteriorContextPointCount=int(np.count_nonzero(~review)),
                  highScoreOverrideAllowed=True, protectionThreshold=.9,
                  parameters=asdict(p), views=[], components=[],
                  scope='review_mask candidates; all retained steel supplies context',
                  densityReview=dict(enabled=False, removedPointCount=0, highScoreRemovedPointCount=0),
                  topViewReview=dict(enabled=False, removedPointCount=0),
                  rule='sliced silhouettes + explicit nonrod/fixture evidence for high scores; local rod protection and exterior XY footprint review')
    if not len(points) or not review.any():
        report['skippedReason'] = 'no review candidates'
        return removed, report
    progress('第 5 步：建立内外钢筋三维连通片段', 0, len(points))
    # Freeze reliable observed samples before inspecting the residuals. A noise
    # bridge to a real rod must not give its entire connected component immunity.
    anchor_points = points[observed] if observed is not None else np.empty((0, 3))
    active = np.arange(len(points))
    if len(anchor_points):
        distance = cKDTree(anchor_points).query(points, k=1, workers=workers)[0]
        active = np.flatnonzero(distance > p.observed_support_radius)
    report['frozenObservedPointCount'] = len(anchor_points)
    report['observedNeighborhoodPointCount'] = int(len(points)-len(active))
    if not len(active):
        report['elapsedS'] = time.perf_counter()-started
        report['protectedCandidatePointCount'] = int(review.sum())
        return removed, report
    centers, inverse, labels, count = _components(points[active], p)
    # One representative normal per occupied voxel prevents repeated echoes from
    # manufacturing independent shape evidence. Invalid normals add no support.
    representatives = np.full(len(centers), len(active), np.int64)
    np.minimum.at(representatives, inverse, np.arange(len(active)))
    order = np.argsort(labels, kind='stable')
    starts = np.r_[0, np.cumsum(np.bincount(labels, minlength=count))]
    row_labels = labels[inverse]
    plausible = np.zeros(count, bool)
    spans = np.zeros(count)
    definite_nonrod = np.zeros(count, bool)
    local_round = np.zeros(len(centers), bool)
    high_fraction = np.bincount(inverse, weights=high[active]) / np.bincount(inverse)
    for component in range(count):
        cells = order[starts[component]:starts[component+1]]
        xyz = centers[cells]
        spans[component] = np.linalg.norm(np.ptp(xyz, axis=0))
        nn = None if normals is None else normals[active[representatives[cells]]]
        # Size/density/elongation alone cannot certify steel: a long narrow
        # floating sheet can have all three. Require measured round surface.
        plausible[component] = (_round_fragment(xyz, nn) or _round_geometry(xyz) or
                                (spans[component] >= .020 and _bent_fragment(xyz, nn)))
        definite_nonrod[component] = _definite_nonrod(xyz, nn)
        if not plausible[component]:
            local_round[cells] = _local_round_support(xyz, nn, high_fraction[cells], p)
    fixture, fixture_nn = _fixture_voxels(fixture_points, fixture_normals, p)
    cell_normals = None if normals is None else normals[active[representatives]]
    surface_support = _fixture_surface_support(centers, cell_normals, fixture, fixture_nn, p, workers)
    density_enabled = bool(surface_support.any())
    has_steel_reference = bool(plausible.any() or len(anchor_points))
    report['densityReview'].update(enabled=density_enabled, fixtureVoxelCount=len(fixture),
        surfaceSupportedVoxelCount=int(surface_support.sum()))
    if not has_steel_reference and not density_enabled:
        report['skippedReason'] = 'no independent observed steel component'
        return removed, report
    # Spatially balanced frozen observations join the view context once. No
    # newly rescued candidate becomes an anchor during view evaluation.
    if len(anchor_points):
        _, first = unique_integer_rows(np.floor((anchor_points-anchor_points.min(0))/p.voxel_size).astype(np.int64),
                                       return_index=True)
        view_points = np.vstack((centers, anchor_points[first]))
        view_reliable = np.r_[plausible[labels], np.ones(len(first), bool)]
    else:
        view_points, view_reliable = centers, plausible[labels]
    negative = np.zeros(len(centers), np.uint8)
    density_negative = np.zeros(len(centers), np.uint8)
    density_strong = np.zeros(len(centers), np.uint8)
    def check_view(angle):
        support, slices = _view_support(view_points, view_reliable, angle, p)
        density, strong = (_density_view(view_points, fixture, angle, p) if density_enabled
                          else (np.zeros(len(view_points), bool), np.zeros(len(view_points), bool)))
        return support, slices, density, strong
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(p.view_angles)))) as pool:
        results = pool.map(check_view, p.view_angles)
        view_results = list(results)
    for index, (angle, (support, slices, density, strong)) in enumerate(zip(p.view_angles, view_results)):
        progress('第 5 步：多侧视切片检查悬浮孤岛', index, len(p.view_angles))
        support = support[:len(centers)]
        negative += ~support
        density_negative += density[:len(centers)]
        density_strong += strong[:len(centers)]
        report['views'].append(dict(angleDegrees=angle, sliceCount=slices,
                                    isolatedVoxelCount=int(np.count_nonzero(~support)),
                                    fixtureDominatedVoxelCount=int(np.count_nonzero(density[:len(centers)]))))
    # Require component-wide negative evidence, not one unfavorable pixel. The
    # lower quartile means at least 75% of occupied voxels meet the vote count.
    votes = np.zeros(count, np.uint8)
    density_votes = np.zeros(count, np.uint8)
    strong_density_votes = np.zeros(count, np.uint8)
    surface_fraction = np.zeros(count)
    for component in range(count):
        cells = order[starts[component]:starts[component+1]]
        votes[component] = int(np.quantile(negative[cells], .25))
        density_votes[component] = int(np.quantile(density_negative[cells], .25))
        strong_density_votes[component] = int(np.quantile(density_strong[cells], .25))
        surface_fraction[component] = float(np.mean(surface_support[cells]))
    density_reject = ((surface_fraction >= p.fixture_component_fraction) &
                      (density_votes >= p.minimum_negative_views))
    strong_density_reject = ((surface_fraction >= p.high_score_fixture_component_fraction) &
                             (strong_density_votes >= p.high_score_negative_views))
    # Only strong measured round components with substantial independent high
    # scores extend the frozen footprint. Ambiguous rescued points cannot expand it.
    component_high = np.bincount(labels, weights=high_fraction, minlength=count) / np.bincount(labels)
    footprint_components = plausible & (component_high >= .5) & (surface_fraction < .1)
    footprint = np.vstack((anchor_points, centers[footprint_components[labels]]))
    top_outside, hull_vertices = _outside_top_footprint(centers, footprint, p.top_view_margin)
    top_fraction = np.zeros(count)
    for component in range(count):
        cells = order[starts[component]:starts[component+1]]
        top_fraction[component] = np.mean(top_outside[cells])
    top_reject = ((top_fraction >= .75) & (surface_fraction >= p.top_view_fixture_fraction)
                  & (hull_vertices >= 3))
    # A footprint alone is never permission to discard an exposed rod, and this
    # extra branch cannot override a high fusion score.
    protected_rows = continuation[active] | local_round[inverse]
    top_round = local_round.copy()
    for component in np.flatnonzero(top_reject & plausible):
        cells = order[starts[component]:starts[component+1]]
        nn = None if cell_normals is None else cell_normals[cells]
        top_round[cells] = _local_round_support(centers[cells], nn, high_fraction[cells], p)
    top_rows = (top_reject[row_labels] & exterior[active] & ~protected_rows
                & top_outside[inverse] & surface_support[inverse]
                & ~top_round[inverse] & ~high[active])
    unsupported = ~plausible[row_labels] & has_steel_reference & ~protected_rows
    density_rows = density_reject[row_labels] & ~protected_rows
    strong_density_rows = strong_density_reject[row_labels] & ~protected_rows
    ordinary_reject = (unsupported & (votes[row_labels] >= p.minimum_negative_views)) | density_rows | top_rows
    strong_reject = (unsupported & definite_nonrod[row_labels] & (votes[row_labels] >= p.high_score_negative_views)) | strong_density_rows
    removed[active] = review[active] & np.where(high[active], strong_reject, ordinary_reject)
    report['highScoreIsolationBlockedPointCount'] = int(np.count_nonzero(
        review[active] & high[active] & unsupported & (votes[row_labels] >= p.high_score_negative_views)
        & ~definite_nonrod[row_labels] & ~strong_density_rows))
    report['localRoundProtectedPointCount'] = int(np.count_nonzero(review[active] & local_round[inverse] & ~removed[active]))
    report['topViewReview'].update(enabled=bool(hull_vertices >= 3 and exterior.any()),
        referencePointCount=len(footprint), hullVertexCount=hull_vertices,
        outsideCandidatePointCount=int(np.count_nonzero(review[active] & exterior[active] & top_outside[inverse])),
        removedPointCount=int(np.count_nonzero(removed[active] & top_rows)),
        additionalRemovedPointCount=int(np.count_nonzero(removed[active] & top_rows & ~density_rows & ~(unsupported & (votes[row_labels] >= p.minimum_negative_views)))),
        rule='XY measured-steel hull + component-wide exterior distance + independent fixture surface; high scores preserved')
    density_removed = removed[active] & np.where(high[active], strong_density_rows, density_rows)
    report['densityReview'].update(removedPointCount=int(density_removed.sum()),
        highScoreRemovedPointCount=int(np.count_nonzero(density_removed & high[active])),
        observedContinuationProtectedPointCount=int(np.count_nonzero(review[active] & continuation[active] & density_reject[row_labels])),
        rejectedComponentCount=int(np.count_nonzero(density_reject)),
        highScoreRejectedComponentCount=int(np.count_nonzero(strong_density_reject)))
    report['removedPointCount'] = int(removed.sum())
    report['highScoreRemovedPointCount'] = int(np.count_nonzero(removed & high))
    protected_active = review[active] & ~removed[active] & (plausible[row_labels] | protected_rows)
    report['protectedCandidatePointCount'] = int(review.sum()-review[active].sum()+protected_active.sum())
    report['blockedRemovalPointCount'] = int(np.count_nonzero(review[active] & high[active] & ordinary_reject & ~strong_reject))
    report['componentCount'] = count
    report['voxelCount'] = len(centers)
    report['removedComponentCount'] = len(np.unique(row_labels[removed[active]]))
    report['scoreDecisions'] = [dict(band=name, candidatePoints=int(np.count_nonzero(review & mask)),
                                    removedPoints=int(np.count_nonzero(removed & mask)))
                                for name, mask in [('low', scores <= .5+1e-6),
                                                   ('medium', (scores > .5+1e-6) & ~high), ('high', high)]]
    source_count = np.bincount(row_labels, minlength=count)
    review_count = np.bincount(row_labels, weights=review[active], minlength=count).astype(int)
    removed_count = np.bincount(row_labels, weights=removed[active], minlength=count).astype(int)
    for component in np.flatnonzero(review_count):
        report['components'].append(dict(id=int(component), pointCount=int(source_count[component]),
            reviewedPointCount=int(review_count[component]), removedPointCount=int(removed_count[component]),
            spanM=float(spans[component]), negativeViews=int(votes[component]),
            measuredRoundSurface=bool(plausible[component]),
            definiteNonrodShape=bool(definite_nonrod[component]),
            topViewOutsideFraction=float(top_fraction[component]),
            topViewFixtureRejected=bool(top_reject[component]),
            measuredShapeProtected=bool(plausible[component] and not removed_count[component]),
            fixtureSurfaceFraction=float(surface_fraction[component]),
            densityNegativeViews=int(density_votes[component]),
            highScoreDensityNegativeViews=int(strong_density_votes[component]),
            fixtureDensityRejected=bool(density_reject[component])))
    report['elapsedS'] = time.perf_counter()-started
    return removed, report
