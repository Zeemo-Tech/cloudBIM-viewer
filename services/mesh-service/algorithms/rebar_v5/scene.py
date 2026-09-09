"""Observed-surface scene classification for the independent V5 pipeline.

The models intentionally retain raster occupancy rather than a convex hull:
an unobserved table hole must remain unlabelled when full-resolution points are
projected later.
"""
from __future__ import annotations

import math
import numpy as np
from ..spatial_keys import unique_integer_rows, integer_pair_membership
from .candidates import PointBoundsIndex
from scipy.spatial import cKDTree
from .features import _query_workers

from ..rebar_v4_geometry import (
    canonical_direction, orientation_modes, perpendicular_basis,
    sparse_grid_components,
)


def _features(features, name, count, default):
    value = features.get("surface_" + name, features.get(name, default))
    value = np.asarray(value)
    return value if len(value) == count else np.asarray(default)


def _plane_axes(normal):
    first, second = perpendicular_basis(normal)
    return np.vstack((first, second))


def _occupied(local, grid):
    return np.floor(np.asarray(local) / grid).astype(np.int64)


def _adaptive_grid(local, base_grid):
    """Choose a footprint cell at observed scan density, never fill holes."""
    if len(local) < 2:
        return base_grid
    tree = cKDTree(local)
    if len(local) >= 4096:
        # Prove the existing lower clamp wins before measuring far neighbours.
        # A strict majority of ALL rows with positive spacing below base/3 is
        # also a majority after zero/nonfinite spacings are excluded. Thus the
        # original median cannot enlarge the grid. Sparse scans use the exact
        # original query below; this shortcut never estimates their density.
        radius = np.nextafter(float(base_grid)/3, 0.)
        bounded, _ = tree.query(local, k=2, distance_upper_bound=radius,
                                workers=_query_workers(len(local)))
        positive = np.isfinite(bounded[:, 1]) & (bounded[:, 1] > 1e-9)
        if np.count_nonzero(positive) > len(local)//2:
            return float(base_grid)
    spacing, _ = tree.query(local, k=2, workers=1)
    nearest = spacing[:, 1]
    nearest = nearest[np.isfinite(nearest) & (nearest > 1e-9)]
    if not len(nearest):
        return base_grid
    # About three nearest-neighbour spacings yields stable occupancy for a
    # Poisson-like raw scan while preserving a physical hole as empty cells.
    return max(float(base_grid), float(3 * np.median(nearest)))


def _candidate_table(points, rows, p):
    cloud = points[rows]
    if len(cloud) < 8:
        return None
    # A height peak may include vertical clutter.  Seeded bounded RANSAC finds
    # a horizontal support before the SVD refit and footprint measurements.
    work = cloud
    if len(work) > 60_000:
        rng = np.random.default_rng(p.random_seed)
        work = work[np.sort(rng.choice(len(work), 60_000, replace=False))]
    rng = np.random.default_rng(p.random_seed)
    minimum_up = math.cos(math.radians(p.table_max_tilt_degrees))
    best = np.empty(0, dtype=np.intp)
    for _ in range(p.table_ransac_iterations):
        a, b, c = work[rng.choice(len(work), 3, replace=False)]
        normal = np.cross(b-a, c-a)
        length = np.linalg.norm(normal)
        if length <= 1e-12:
            continue
        normal /= length
        if abs(float(normal[2])) < minimum_up:
            continue
        inliers = np.flatnonzero(np.abs((work-a) @ normal) <= p.table_distance)
        if len(inliers) > len(best):
            best = inliers
    if len(best) < 8:
        return None
    origin = np.mean(work[best], axis=0)
    _, _, vectors = np.linalg.svd(work[best] - origin, full_matrices=False)
    normal = canonical_direction(vectors[-1])
    if normal[2] < 0:
        normal = -normal
    if abs(float(normal[2])) < math.cos(math.radians(p.table_max_tilt_degrees)):
        return None
    # Feature normals seed RANSAC, but the final support is every finite raw
    # observation close to that plane. Sparse scans cannot require every point
    # to have a six-neighbour normal before their footprint is validated.
    all_finite = points[np.isfinite(points).all(axis=1)]
    distance = np.abs((all_finite - origin) @ normal)
    cloud = all_finite[distance <= p.table_distance]
    if len(cloud) < 8:
        return None
    origin = np.mean(cloud, axis=0)
    axes = _plane_axes(normal)
    local = np.column_stack(((cloud - origin) @ axes[0], (cloud - origin) @ axes[1]))
    lo, hi = local.min(axis=0), local.max(axis=0)
    # Keep raw-grid occupancy for masking so an adaptive coverage cell never
    # paints an observed hole. The coarser grid is validation evidence only.
    mask_cells = unique_integer_rows(_occupied(local, p.table_grid_size))
    grid = min(_adaptive_grid(local, p.table_grid_size), float(np.min(hi-lo) / 5))
    grid = max(float(p.table_grid_size), grid)
    cells = _occupied(local, grid)
    unique = unique_integer_rows(cells)
    area = float(len(unique) * grid ** 2)
    if area < p.table_min_area:
        return None
    spans = np.maximum(1, np.ceil((hi-lo)/grid).astype(int))
    possible = max(1, int(spans[0] * spans[1]))
    # Several thin rods can cover a large bounding box while leaving most
    # cells empty.  A table needs both actual area and a filled footprint.
    # Lines of rods can be dense along their axes yet occupy only a few rows;
    # a table must have a genuine 2D footprint at its own observed density.
    occupied_spans = [len(np.unique(unique[:, axis])) for axis in (0, 1)]
    if min(spans) < 5 or min(occupied_spans) < 5 or len(unique) / possible < .55:
        return None
    return {
        "origin": origin.tolist(), "normal": normal.tolist(), "axes": axes.tolist(),
        "halfExtent": ((hi - lo) / 2).tolist(), "distance": float(p.table_distance),
        "gridSize": float(p.table_grid_size), "occupiedCells": mask_cells.tolist(),
        "supportCount": int(len(cloud)), "area": area, "coverage": float(len(unique) / possible),
    }


def detect_table(points, features, p):
    """Choose the lowest horizontal, actually occupied planar support surface."""
    points = np.asarray(points, dtype=np.float64)
    finite = np.isfinite(points).all(axis=1)
    normal = _features(features, "normal", len(points), np.zeros((len(points), 3)))
    planarity = _features(features, "planarity", len(points), np.zeros(len(points)))
    valid = _features(features, "valid", len(points), np.zeros(len(points), dtype=bool)).astype(bool)
    horizontal = np.abs(normal[:, 2]) >= math.cos(math.radians(p.table_max_tilt_degrees))
    rows = np.flatnonzero(finite & valid & (planarity >= p.min_planarity) & horizontal)
    if len(rows) < 8:
        return None
    # Occupancy peaks prevent a few low outliers from winning. Adjacent height
    # bins are considered together because a noisy surface can straddle a bin.
    bins = np.floor(points[rows, 2] / p.table_height_bin).astype(np.int64)
    candidates = []
    for key in np.unique(bins):
        selected = rows[np.abs(bins - key) <= 1]
        model = _candidate_table(points, selected, p)
        if model is not None:
            candidates.append(model)
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item["origin"][2], -item["area"]))


def table_mask(points, model, p):
    points = np.asarray(points, dtype=np.float64)
    result = np.zeros(len(points), dtype=bool)
    if not model:
        return result
    try:
        origin = np.asarray(model["origin"], dtype=np.float64)
        normal = np.asarray(model["normal"], dtype=np.float64)
        axes = np.asarray(model["axes"], dtype=np.float64)
        grid = float(model["gridSize"])
        cells = np.asarray(model["occupiedCells"])
    except (KeyError, TypeError, ValueError):
        return result
    if origin.shape != (3,) or normal.shape != (3,) or axes.shape != (2, 3) or grid <= 0:
        return result
    finite = np.isfinite(points).all(axis=1)
    distance = float(model.get("distance", p.table_distance))
    rows = np.flatnonzero(finite)
    if not len(rows):
        return result
    local3 = points[rows] - origin
    rows = rows[np.abs(local3 @ normal) <= distance]
    if not len(rows):
        return result
    local3 = points[rows] - origin
    local2 = np.column_stack((local3 @ axes[0], local3 @ axes[1]))
    keys = _occupied(local2, grid)
    result[rows] = integer_pair_membership(keys, cells)
    return result


def _fixture_face(support, normal, p, seeds=None, *, minimum_width=None):
    seed_axes = _plane_axes(normal)
    offsets = support @ normal
    width = max(p.fixture_surface_distance, p.fixture_offset_gap / 2)
    surfaces = []
    seed_offsets = (support if seeds is None else seeds) @ normal
    for key in np.unique(np.rint(seed_offsets / width).astype(np.int64)):
        cloud = support[np.abs(offsets - key * width) <= p.fixture_surface_distance]
        if len(cloud) < p.fixture_min_points:
            continue
        origin = np.mean(cloud, axis=0)
        local_seed = (cloud-origin) @ seed_axes.T
        # Split same-offset planes into connected observed patches. This keeps
        # small separate plates separate instead of fitting their empty span.
        # Connectivity is a proposal radius, not the exported mask cell size.
        # Sparse planar scans need to bridge sampling gaps before their local
        # width and occupied area can be assessed on a meaningful face.
        connection_grid=max(p.fixture_grid_cell,min(2*p.max_radius,_adaptive_grid(local_seed,p.fixture_grid_cell)))
        for component in sparse_grid_components(local_seed, connection_grid):
            patch = cloud[component]
            if len(patch) < p.fixture_min_points:
                continue
            origin = np.mean(patch, axis=0)
            # Refitting rejects the curved band from a cylinder.
            _, _, vectors = np.linalg.svd(patch - origin, full_matrices=False)
            face_normal = canonical_direction(vectors[-1])
            if abs(float(face_normal @ normal)) < 0.94:
                continue
            residual = np.abs((patch - origin) @ face_normal)
            if np.mean(residual <= p.fixture_fit_distance) < .50:
                continue
            patch = patch[residual <= p.fixture_fit_distance]
            if len(patch) < p.fixture_min_points:
                continue
            origin = np.mean(patch, axis=0)
            _, _, refined_vectors = np.linalg.svd(patch-origin, full_matrices=False)
            axes = refined_vectors[:2]
            face_normal = canonical_direction(refined_vectors[-1])
            local = np.column_stack(((patch-origin) @ axes[0], (patch-origin) @ axes[1]))
            lo, hi = np.percentile(local, [2, 98], axis=0)
            dimensions = hi-lo
            # A rod's apparent planar cap may be roughly its diameter. It is
            # not a fixture face unless it is wider than a maximum rebar plus
            # the observed surface tolerance on both sides.
            required_width = (max(p.fixture_min_width, 2 * p.max_radius + 2 * p.fixture_surface_distance)
                              if minimum_width is None else minimum_width)
            if min(dimensions) < required_width or max(dimensions) < p.fixture_min_length:
                continue
            # A bent cylindrical strip can have a wide global bounding box.
            # Require actual width across multiple local longitudinal slices.
            long_axis=int(np.argmax(dimensions));short_axis=1-long_axis
            slice_length=max(.03,4*p.fixture_grid_cell,16*dimensions[long_axis]/len(patch))
            bins=np.floor((local[:,long_axis]-local[:,long_axis].min())/slice_length).astype(int)
            widths=[];gap_fractions=[]
            for bin_id in np.unique(bins):
                values=local[bins==bin_id,short_axis]
                if len(values)>=max(5,p.fixture_min_points//2):
                    widths.append(float(np.ptp(np.percentile(values,[5,95]))))
                    gap_fractions.append(float(np.max(np.diff(np.sort(values))))/max(float(np.ptp(values)),1e-12))
            if not widths or np.median(widths)<.85*required_width:
                continue
            if np.median(gap_fractions)>.30:
                continue
            center = origin + ((lo+hi)/2) @ axes
            # Store occupancy in the final returned centre's coordinate frame.
            centered_local = np.column_stack(((patch-center) @ axes[0], (patch-center) @ axes[1]))
            cells = unique_integer_rows(_occupied(centered_local, p.fixture_grid_cell))
            coverage_grid=_adaptive_grid(centered_local,p.fixture_grid_cell)
            coverage_cells=unique_integer_rows(_occupied(centered_local,coverage_grid))
            possible = max(1, int(np.ceil(dimensions[0]/coverage_grid))*int(np.ceil(dimensions[1]/coverage_grid)))
            coverage = len(coverage_cells)/possible
            if coverage < .60:
                continue
            short = local[:, int(np.argmin(dimensions))]
            spacing=cKDTree(local).query(local,k=2)[0][:,1]
            spacing=spacing[spacing>1e-9]
            regular_spacing=float(np.median(spacing)) if len(spacing) and np.std(spacing)<.15*np.mean(spacing) else 0
            profile_grid = max(.0025, p.fixture_grid_cell/4, regular_spacing)
            occupied_profile = len(np.unique(np.floor((short-short.min())/profile_grid)))
            if occupied_profile/max(1,int(np.ceil(np.ptp(short)/profile_grid))) < .75:
                continue
            surfaces.append({"origin": center.tolist(), "normal": face_normal.tolist(), "axes": axes.tolist(),
                "halfExtent": (dimensions/2+p.fixture_grid_cell).tolist(), "distance": float(p.fixture_surface_distance),
                "supportCount": int(len(patch)), "coverage": float(coverage), "confidence": float(min(1., coverage*len(patch)/(p.fixture_min_points*1.5))),
                "kind": "plate", "occupiedCells": cells.tolist(), "gridSize": float(p.fixture_grid_cell)})
    return surfaces


def _square_tube_companions(support, faces, p):
    """Recover sparse top/bottom faces from two already observed side faces.

    This is a proposal only: each returned face is refit from its own raw
    planar observations and exports only those observations' occupied cells.
    """
    result = []
    for left, first in enumerate(faces):
        try:
            a0, n0, axes0, extent0 = (np.asarray(first[key], float) for key in ("origin", "normal", "axes", "halfExtent"))
        except (KeyError, TypeError, ValueError):
            continue
        n0 /= max(np.linalg.norm(n0), 1e-12)
        long_index = int(np.argmax(extent0))
        axis = axes0[long_index] / max(np.linalg.norm(axes0[long_index]), 1e-12)
        for second in faces[left+1:]:
            try:
                a1, n1, extent1 = (np.asarray(second[key], float) for key in ("origin", "normal", "halfExtent"))
            except (KeyError, TypeError, ValueError):
                continue
            n1 /= max(np.linalg.norm(n1), 1e-12)
            if abs(float(n0 @ n1)) < .94 or not (.5 <= max(extent0)/max(max(extent1), 1e-12) <= 2.):
                continue
            separation = abs(float((a1-a0) @ n0))
            if not (max(p.fixture_min_width, 2*p.min_radius) <= separation <= 2.5*p.fixture_min_width):
                continue
            centre = (a0+a1)/2
            # Partial scans can split the two opposing walls at different
            # positions. Propose companions only over their measured overlap.
            shift = float((a1-a0) @ axis)
            lower = max(-float(extent0[long_index]), shift-float(np.max(extent1)))
            upper = min(float(extent0[long_index]), shift+float(np.max(extent1)))
            if upper-lower < p.fixture_min_length:
                continue
            centre += ((lower+upper)/2-shift/2)*axis
            cross = np.cross(axis, n0)
            cross /= max(np.linalg.norm(cross), 1e-12)
            long_extent = (upper-lower)/2
            for sign in (-1., 1.):
                target = centre+sign*(separation/2)*cross
                delta = support-target
                longitudinal = delta @ axis
                transverse = delta @ n0
                normal_distance = np.abs(delta @ cross)
                rows = (np.abs(longitudinal) <= long_extent+p.fixture_grid_cell) & (np.abs(transverse) <= separation/2+p.fixture_surface_distance) & (normal_distance <= p.fixture_surface_distance)
                cloud = support[rows]
                if len(cloud) < p.fixture_min_points:
                    continue
                origin = np.mean(cloud, axis=0)
                residual = np.abs((cloud-origin) @ cross)
                cloud = cloud[residual <= p.fixture_fit_distance]
                if len(cloud) < p.fixture_min_points:
                    continue
                local = np.column_stack(((cloud-origin) @ axis, (cloud-origin) @ n0))
                lo, hi = np.percentile(local, [2, 98], axis=0)
                dimensions = hi-lo
                if dimensions[0] < p.fixture_min_length or dimensions[1] < max(p.fixture_min_width, .70*separation):
                    continue
                profile = np.sort(local[:,1])
                # Two isolated edge stripes do not observe a connecting wall.
                if np.max(np.diff(profile))/max(float(np.ptp(profile)),1e-12) > .35:
                    continue
                # Every longitudinal slice must show actual width; a line or a
                # pair of rod stripes cannot satisfy this rectangular support.
                bins = np.floor((local[:, 0]-lo[0])/max(.03, 3*p.fixture_grid_cell)).astype(int)
                widths = [np.ptp(local[bins == key, 1]) for key in np.unique(bins) if np.count_nonzero(bins == key) >= 4]
                if not widths or np.median(widths) < .65*separation:
                    continue
                face_centre = origin+((lo+hi)/2) @ np.vstack((axis, n0))
                centred = np.column_stack(((cloud-face_centre) @ axis, (cloud-face_centre) @ n0))
                cells = unique_integer_rows(_occupied(centred, p.fixture_grid_cell))
                result.append({"origin": face_centre.tolist(), "normal": cross.tolist(), "axes": np.vstack((axis, n0)).tolist(),
                    "halfExtent": (dimensions/2+p.fixture_grid_cell).tolist(), "distance": float(p.fixture_surface_distance),
                    "supportCount": int(len(cloud)), "coverage": float(len(cells)/max(1, int(np.ceil(dimensions[0]/p.fixture_grid_cell))*int(np.ceil(dimensions[1]/p.fixture_grid_cell)))),
                    "confidence": .8, "kind": "square-tube-face", "occupiedCells": cells.tolist(), "gridSize": float(p.fixture_grid_cell)})
    return result


def _tube_face_pair(first, second, p):
    """Two adjoining longitudinal walls, not any nearby perpendicular plates."""
    a, b = np.asarray(first['origin']), np.asarray(second['origin'])
    n, m = np.asarray(first['normal']), np.asarray(second['normal'])
    ea, eb = np.asarray(first['halfExtent']), np.asarray(second['halfExtent'])
    aa, ab = np.asarray(first['axes']), np.asarray(second['axes'])
    ia, ib = int(np.argmax(ea)), int(np.argmax(eb))
    la, lb = aa[ia], ab[ib]
    # A square tube has a common long axis and similarly long adjacent walls.
    if abs(float(n @ m)) >= .20 or abs(float(la @ lb)) < .97:
        return False
    if min(max(ea)/max(min(ea), 1e-12), max(eb)/max(min(eb), 1e-12)) < 2.5:
        return False
    delta = b-a
    overlap = max(0., min(max(ea), float(delta @ la)+max(eb))-
                  max(-max(ea), float(delta @ la)-max(eb)))
    if overlap < 1.2*min(max(ea), max(eb)):
        return False
    # Each plane intersects the other at its short edge, not across the middle
    # of an unrelated plate. halfExtent already includes one occupancy cell.
    sa, sb = max(min(ea)-p.fixture_grid_cell, 0), max(min(eb)-p.fixture_grid_cell, 0)
    tolerance = max(p.fixture_grid_cell, 2*p.fixture_surface_distance)
    return (abs(abs(float(delta @ n))-sb) <= tolerance and
            abs(abs(float(delta @ m))-sa) <= tolerance)


def _clamp_plates_near_tubes(support, faces, p):
    """Recover narrow exterior plates only beside measured tube walls.

    Retain the global anti-cylinder width gate. Contextual plate proposals
    still require the same flatness, filled cross-section and occupied cells.
    """
    result = []
    for wall in faces:
        if wall.get('kind') != 'square-tube-face':
            continue
        origin, normal, axes, extent = (np.asarray(wall[k], float) for k in ('origin','normal','axes','halfExtent'))
        center = np.asarray(wall.get('tubeCenter', origin), float)
        outward = normal * (-1 if float((origin-center) @ normal) < 0 else 1)
        delta = support-origin
        offset = delta @ outward
        local = delta @ axes.T
        reach = max(2*p.fixture_grid_cell, 2*p.fixture_offset_gap)
        rows = ((offset > 2*p.fixture_fit_distance) & (offset <= reach) &
                np.all(np.abs(local) <= extent+p.fixture_grid_cell, axis=1))
        cloud = support[rows]
        if len(cloud) < p.fixture_min_points:
            continue
        for plate in _fixture_face(cloud, normal, p, minimum_width=max(2*p.max_radius, .9*p.fixture_min_width)):
            # The narrow patch must be smaller than the supporting tube wall;
            # a parallel full-length wall is not a clamp.
            if max(plate['halfExtent']) >= .60*max(extent):
                continue
            plate['kind'] = 'plate'
            plate['evidence'] = 'observed-plate-on-tube'
            result.append(plate)
    return result


def _consistent_fixture_normals(support, faces, p, features=None):
    """A plate must agree with the uncropped surface's local normal field.

    A cylindrical U bend can fill a wide planar bounding box after selecting
    one thin normal-offset band. Its original surface normals still rotate;
    checking those prevents the clipped band from becoming a false plate.
    """
    from .features import pca_features, prepare_pca_context
    accepted = []
    context = None
    point_index = PointBoundsIndex(support) if len(faces) > 4 else None
    for face in faces:
        rows = np.flatnonzero(fixture_mask(support, [face], p, point_index=point_index))
        if len(rows) > 256:
            rows = rows[np.linspace(0, len(rows)-1, 256, dtype=int)]
        if not len(rows):
            continue
        if features is None:
            if context is None:
                context = prepare_pca_context(support)
            local = pca_features(support, support[rows], p.surface_radius, p, context=context)
            normals, valid = local['normal'], local['normal_valid'].astype(bool)
        else:
            normals = _features(features, 'normal', len(support), np.zeros((len(support),3)))[rows]
            valid = _features(features, 'valid', len(support), np.zeros(len(support),bool))[rows].astype(bool)
        aligned = np.abs(normals @ np.asarray(face['normal'])) >= math.cos(math.radians(2*p.fixture_normal_tolerance_degrees))
        agreement = float(np.mean(valid & aligned))
        if agreement >= .50:
            face['normalAgreement'] = agreement
            accepted.append(face)
    return accepted


def _opposing_fixture_faces(first, second, p):
    """Independent parallel faces can validate sparse, mixed corner normals."""
    normal, other = np.asarray(first['normal']), np.asarray(second['normal'])
    delta = np.asarray(second['origin'])-np.asarray(first['origin'])
    ea, eb = np.asarray(first['halfExtent']), np.asarray(second['halfExtent'])
    aa, ab = np.asarray(first['axes']), np.asarray(second['axes'])
    la, lb = aa[np.argmax(ea)], ab[np.argmax(eb)]
    if abs(float(normal @ other)) < .97 or abs(float(la @ lb)) < .97:
        return False
    separation = abs(float(delta @ normal))
    if not (p.fixture_min_width <= separation <= 2.5*p.fixture_min_width):
        return False
    if abs(min(ea)-min(eb)) > p.fixture_grid_cell:
        return False
    overlap = min(max(ea), float(delta @ la)+max(eb))-max(-max(ea),float(delta @ la)-max(eb))
    return (overlap >= max(p.fixture_min_length, min(max(ea),max(eb))) and
            abs(float(delta @ np.cross(la,normal))) <= 2*p.fixture_grid_cell)


def _accepted_fixture_faces(result, support, p, features=None):
    unique = []
    for surface in sorted(result, key=lambda item: -item["supportCount"]):
        normal = np.asarray(surface["normal"], dtype=float)
        origin = np.asarray(surface["origin"], dtype=float)
        if any(
            abs(float(normal @ np.asarray(other["normal"]))) >= .999
            and np.linalg.norm(origin - np.asarray(other["origin"])) <= 2 * p.fixture_grid_cell
            and abs(float((origin - np.asarray(other["origin"])) @ normal)) <= p.fixture_fit_distance
            for other in unique
        ):
            continue
        unique.append(surface)
    # Sparse companion proposals still need their own measured surface. Build
    # them before deciding whether the cross-section supports a tube subtype.
    result = unique + _square_tube_companions(support, unique, p)
    adjacency=[[] for _ in result]
    for i, first in enumerate(result):
        for j in range(i+1,len(result)):
            if _tube_face_pair(first,result[j],p):
                adjacency[i].append(j);adjacency[j].append(i)
    accepted = []
    for index,surface in enumerate(result):
        partners = [result[j] for j in adjacency[index]]
        # Two adjoining faces also describe an L bracket. Require an opposing
        # pair connected by a third measured wall before asserting a tube.
        closed = []
        for adjacent_index in adjacency[index]:
            adjacent=result[adjacent_index]
            for opposite_index in adjacency[adjacent_index]:
                opposite=result[opposite_index]
                if opposite is surface or opposite is adjacent:
                    continue
                normal = np.asarray(surface['normal'])
                delta = np.asarray(opposite['origin'])-np.asarray(surface['origin'])
                if (abs(float(normal @ np.asarray(opposite['normal']))) >= .97 and
                    abs(float(delta @ normal)) >= p.fixture_min_width):
                    closed.extend((adjacent, opposite))
        # The connecting wall itself has two opposing parallel neighbours.
        for i, first in enumerate(partners):
            for second in partners[i+1:]:
                normal = np.asarray(first['normal'])
                delta = np.asarray(second['origin'])-np.asarray(first['origin'])
                if (abs(float(normal @ np.asarray(second['normal']))) >= .97 and
                    abs(float(delta @ normal)) >= p.fixture_min_width):
                    closed.extend((first, second))
        paired = bool(closed)
        if paired:
            surface["kind"] = "square-tube-face"
            surface["confidence"] = min(1., surface["confidence"] + .15)
            surface['tubeCenter'] = np.mean([surface['origin']]+[other['origin'] for other in closed], axis=0).tolist()
        elif partners or surface.get('kind') == 'square-tube-face':
            surface['kind'] = 'fixture-unknown'
        if paired or surface["supportCount"] >= 4 * p.fixture_min_points:
            accepted.append(surface)
    # A measured rectangular shell supplies independent cross-face evidence.
    # Sparse tube corners mix normals from adjoining faces, so the single-face
    # normal-field test applies to plates and unresolved surfaces instead.
    tubes = [face for face in accepted if face['kind']=='square-tube-face']
    other = [face for face in accepted if face['kind']!='square-tube-face']
    opposing = [face for face in other if any(_opposing_fixture_faces(face, candidate, p)
                for candidate in accepted if candidate is not face)]
    for face in opposing:
        face['kind'] = 'fixture-unknown'
        face['evidence'] = 'observed-opposing-faces'
    other = [face for face in other if not any(face is candidate for candidate in opposing)]
    other.extend(_clamp_plates_near_tubes(support, tubes, p))
    return tubes + opposing + _consistent_fixture_normals(support, other, p, features)


def refine_fixture_faces(support, proposals, p):
    """Refit finite fixture proposals from bounded raw support only."""
    support = np.asarray(support, dtype=np.float64)
    result = []
    modes = set()
    for proposal in proposals:
        try:
            normal = np.asarray(proposal["normal"], dtype=float)
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(normal).all() and np.linalg.norm(normal) > 1e-12:
            normal = canonical_direction(normal)
            key = tuple(normal.tolist())
            if key in modes:
                continue
            modes.add(key)
            result.extend(_fixture_face(support, normal, p))
    return _accepted_fixture_faces(result, support, p)


def detect_fixtures(points, features, p, table=None):
    """Return finite observed planar fixture faces; cylinders are not fixtures."""
    points = np.asarray(points, dtype=np.float64)
    finite = np.isfinite(points).all(axis=1)
    normal = _features(features, "normal", len(points), np.zeros((len(points), 3)))
    planarity = _features(features, "planarity", len(points), np.zeros(len(points)))
    valid = _features(features, "valid", len(points), np.zeros(len(points), dtype=bool)).astype(bool)
    rows = finite & valid & (planarity >= p.min_planarity)
    if table:
        rows &= ~table_mask(points, table, p)
    seeds = points[rows]
    support = points[finite]
    if len(seeds) < p.fixture_min_points:
        return []
    normals = normal[rows]
    valid_normals = np.isfinite(normals).all(axis=1) & (np.linalg.norm(normals, axis=1) > .9)
    modes = orientation_modes(normals[valid_normals], planarity[rows][valid_normals],
        tolerance_degrees=p.fixture_normal_tolerance_degrees,
        minimum_votes=max(4, p.fixture_min_points // 3), maximum_modes=24)
    result = []
    for mode, _ in modes:
        seed_rows = np.abs(normal[rows] @ mode) >= math.cos(math.radians(p.fixture_normal_tolerance_degrees))
        result.extend(_fixture_face(support, mode, p, seeds[seed_rows]))
    return _accepted_fixture_faces(result, support, p, {key:np.asarray(value)[finite] for key,value in features.items()})


def fixture_candidates(points, fixtures, p):
    """Discard faces whose exact lookup box cannot intersect this point block.

    This is only a broad-phase filter. The observed-cell and finite surface
    tests in fixture_mask still decide membership, including boundary points.
    """
    points = np.asarray(points, dtype=np.float64)
    finite = np.isfinite(points).all(axis=1)
    if not np.any(finite) or not fixtures:
        return []
    coordinates = points[finite]
    lower, upper = coordinates.min(axis=0), coordinates.max(axis=0)
    candidates = []
    for face in fixtures:
        try:
            origin, normal, axes, extent = (np.asarray(face[key], dtype=float)
                for key in ("origin", "normal", "axes", "halfExtent"))
            distance = float(face.get("distance", p.fixture_surface_distance))
            reach = np.abs(axes).T @ extent + np.abs(normal) * distance
        except (KeyError, TypeError, ValueError):
            continue
        if np.any(upper < origin-reach-1e-12) or np.any(lower > origin+reach+1e-12):
            continue
        candidates.append(face)
    return candidates


def fixture_mask(points, fixtures, p, point_index=None):
    """Label only observed surface cells, never the fixture's interior volume."""
    points = np.asarray(points, dtype=np.float64)
    result = np.zeros(len(points), dtype=bool)
    finite = np.isfinite(points).all(axis=1)
    if len(fixtures) > 1:
        fixtures = fixture_candidates(points, fixtures, p)
    if point_index is None and len(fixtures) > 4:
        point_index = PointBoundsIndex(points)
    for face in fixtures:
        result[fixture_rows(points,face,p,point_index=point_index,finite=finite)] = True
    return result


def fixture_rows(points, face, p, *, point_index=None, finite=None):
    """Ascending exact support rows, avoiding a full point mask per face."""
    try:
        origin, normal, axes = (np.asarray(face[key], dtype=float) for key in ("origin", "normal", "axes"))
        grid, cells = float(face["gridSize"]), np.asarray(face["occupiedCells"])
    except (KeyError, TypeError, ValueError):
        return np.empty(0,np.intp)
    extent = np.asarray(face["halfExtent"], dtype=float)
    distance=float(face.get("distance",p.fixture_surface_distance))
    reach=np.abs(axes).T@extent+np.abs(normal)*distance
    if point_index is None:
        if finite is None:finite=np.isfinite(points).all(axis=1)
        rows=np.flatnonzero(finite&np.all(np.abs(points-origin)<=reach+1e-12,axis=1))
    else:
        rows=point_index.query(np.nextafter(origin-reach-1e-12,-np.inf),
                               np.nextafter(origin+reach+1e-12,np.inf))
        rows=rows[np.isfinite(points[rows]).all(axis=1)&np.all(np.abs(points[rows]-origin)<=reach+1e-12,axis=1)]
    if not len(rows):return rows
    local3 = points[rows]-origin
    local = np.column_stack((local3@axes[0], local3@axes[1]))
    keys = _occupied(local, grid)
    observed = integer_pair_membership(keys, cells)
    return rows[observed & (np.abs(local3@normal) <= distance) & np.all(np.abs(local) <= extent, axis=1)]
