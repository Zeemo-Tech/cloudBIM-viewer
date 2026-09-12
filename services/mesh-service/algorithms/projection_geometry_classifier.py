"""Independent XY/Z classification with sliced side views for inclined rods.

The normal branch's classes and trees are never read. Normals are used only to
fit the low tabletop. All image decisions operate on the remaining raw points.
"""
from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np
from scipy import ndimage
from scipy.signal import find_peaks
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


VERSION = "projection-geometry-v11-preserved-height-retention"
CLASS_NAMES = {"1": "台面", "2": "夹具（含方管）", "3": "钢筋"}
COLORS = {"1": "#64748b", "2": "#f59e0b", "3": "#2dd4bf"}


@dataclass(frozen=True)
class ProjectionParameters:
    pixel_size: float = .002
    max_pixels: int = 8_000_000
    table_clearance: float = .005
    max_bar_width: float = .014
    min_line_length: float = .030
    density_z_bin: float = .002
    vertical_span: float = .028
    vertical_coverage: float = .55
    histogram_bin: float = .0005
    layer_merge_gap: float = .016
    layer_margin: float = .004
    side_views_deg: tuple = (0., 45., 90., 135.)
    side_slice_width: float = .060
    web_voxel_size: float = .003
    web_radius: float = .025
    web_joint_radius: float = .015
    web_min_linearity: float = .78
    web_min_vertical_direction: float = .15
    web_bend_reach: float = .035
    web_bend_width: float = .022
    web_bend_gap: float = .008
    fixture_edge_reach: float = .018
    fixture_plane_tolerance: float = .0025
    retain_bottom_height: bool = True
    retain_top_height: bool = True
    top_height_margin: float = .004
    fixture_footprint_width: float = .030
    fixture_footprint_edge: float = .006


def _disk(radius):
    y, x = np.ogrid[-radius:radius+1, -radius:radius+1]
    return x*x+y*y <= radius*radius


def _table_plane(positions, normals, valid, z_origin, params):
    # A bounded deterministic sample is enough for a broad table fit; the
    # resulting removal mask and every histogram below use ALL source rows.
    stride = max(1, int(np.ceil(len(positions)/250_000)))
    p = positions[::stride]
    n = normals[::stride]
    upright = valid[::stride].astype(bool) & (n[:, 2]**2 > .85)
    bins = np.floor((p[:, 2]-z_origin)/params.histogram_bin).astype(np.int64)
    histogram = np.bincount(bins[upright], minlength=int(bins.max())+1)
    if upright.sum() < 30:
        return None
    smooth = ndimage.gaussian_filter1d(histogram.astype(float), 2)
    mode = z_origin+(int(np.argmax(smooth))+.5)*params.histogram_bin
    keep = upright & (np.abs(p[:, 2]-mode) < .006)
    candidate = p[keep]
    if len(candidate) < 30 or np.ptp(candidate[:, 0]) < .2 or np.ptp(candidate[:, 1]) < .2:
        return None
    origin = candidate.mean(axis=0)
    delta = candidate-origin
    slopes = np.linalg.lstsq(delta[:, :2], delta[:, 2], rcond=None)[0]
    # Reject a steep surface: this branch explicitly assumes an XY tabletop.
    if np.linalg.norm(slopes) > .15:
        return None
    residual = delta[:, 2]-delta[:, :2] @ slopes
    tight = np.abs(residual-np.median(residual)) < .003
    if tight.sum() >= 30:
        slopes = np.linalg.lstsq(delta[tight, :2], delta[tight, 2], rcond=None)[0]
        offset = float(np.median(delta[tight, 2]-delta[tight, :2] @ slopes))
        origin[2] += offset
    return {"origin": origin.tolist(), "slopes": slopes.tolist(),
            "sampleSupport": int(keep.sum()), "clearanceM": params.table_clearance}


def _height_layers(histogram, z_origin, table, params):
    smooth = ndimage.gaussian_filter1d(histogram.astype(float), 2)
    if not smooth.any():
        return [], smooth
    peak_ids, properties = find_peaks(np.r_[0., smooth, 0.], distance=max(1, round(.007/params.histogram_bin)),
                                      prominence=max(3., float(smooth.max())*.04))
    peak_ids -= 1  # Zero padding also detects a layer at the highest source Z.
    # Explicitly report evidence, rather than forcing the expected three modes.
    if len(peak_ids) > 12:
        strongest = np.argsort(properties["prominences"])[-12:]
        peak_ids = np.sort(peak_ids[strongest])
    groups = []
    for peak in peak_ids:
        if groups and (peak-groups[-1][-1])*params.histogram_bin < params.layer_merge_gap:
            groups[-1].append(int(peak))
        else:
            groups.append([int(peak)])
    layers = []
    table_z = table["origin"][2] if table else z_origin
    for group in groups:
        best = max(group, key=lambda index: smooth[index])
        height = z_origin+(best+.5)*params.histogram_bin
        peaks = [z_origin+(index+.5)*params.histogram_bin for index in group]
        layers.append({"id": len(layers)+1, "heightM": float(height),
                       "relativeHeightM": float(height-table_z),
                       "lowM": float(peaks[0]-params.layer_margin),
                       "highM": float(peaks[-1]+params.layer_margin),
                       "peaksM": peaks, "name": f"高度层 {len(layers)+1}",
                       "peakCount": int(histogram[best])})
    return layers, smooth


def _long_thin_components(mask, pixel_size, params):
    component, count = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if not count:
        return mask.copy()
    y, x = np.nonzero(mask)
    ids = component[y, x]
    mass = np.bincount(ids, minlength=count+1)
    lowx = np.full(count+1, mask.shape[1], np.int32); highx = np.zeros(count+1, np.int32)
    lowy = np.full(count+1, mask.shape[0], np.int32); highy = np.zeros(count+1, np.int32)
    np.minimum.at(lowx, ids, x); np.maximum.at(highx, ids, x)
    np.minimum.at(lowy, ids, y); np.maximum.at(highy, ids, y)
    sx, sy = highx-lowx+1, highy-lowy+1
    long = np.maximum(sx, sy); short = np.maximum(np.minimum(sx, sy), 1)
    fill = mass/np.maximum(sx.astype(float)*sy, 1)
    good = (long*pixel_size >= params.min_line_length) & (mass >= 8)
    # Open wire networks and diagonal lines have sparse bounding rectangles;
    # short, solid tabs do not pass merely because they are under the width cap.
    good &= (long/short >= 3) | (fill < .55)
    good[0] = False
    return good[component]


def _restore_crossings(present, joined, static, thin, walls, pixel, params):
    """Remove short width-rule islands only when original steel flanks them.

    A crossing is wider in XY than either of its rods. Its opened island is
    small and has steel on opposing sides; a fixture rail is a long/broad
    component. Never use restored pixels as evidence for another iteration.
    """
    components, _ = ndimage.label(static, structure=np.ones((3, 3), bool))
    restored_static = np.zeros_like(static)
    margin = max(2, int(np.ceil(params.max_bar_width / pixel)))
    maximum_span = max(3, int(np.ceil(params.max_bar_width * 2.5 / pixel)))
    for label, box in enumerate(ndimage.find_objects(components), 1):
        if box is None or max(part.stop-part.start for part in box) > maximum_span:
            continue
        y, x = box
        ys = slice(max(0, y.start-margin), min(present.shape[0], y.stop+margin))
        xs = slice(max(0, x.start-margin), min(present.shape[1], x.stop+margin))
        sy, sx = np.nonzero(thin[ys, xs])
        if len(sx) < 8:
            continue
        cx, cy = (x.start+x.stop-1)*.5-xs.start, (y.start+y.stop-1)*.5-ys.start
        vectors = np.column_stack((sx-cx, sy-cy))
        length = np.linalg.norm(vectors, axis=1)
        directions = vectors / np.maximum(length[:, None], 1.)
        # Eight angular sectors make the check independent of axis alignment.
        angles = np.arctan2(directions[:, 1], directions[:, 0])
        sectors = np.bincount((np.floor((angles+np.pi)*4/np.pi).astype(int) % 8), minlength=8) >= 3
        opposing = any(sectors[i] and any(sectors[(i+j)%8] for j in (3, 4, 5)) for i in range(8))
        if opposing:
            restored_static[box] |= (components[box] == label) & ~walls[box]
    if not restored_static.any():
        return thin, np.zeros_like(thin)
    remaining_static = static & ~restored_static
    connected = _long_thin_components(joined & ~remaining_static, pixel, params)
    connected = ndimage.binary_dilation(connected, structure=_disk(1)) & ~remaining_static & present
    return connected | thin, connected & ~thin


def _multiview_webs(positions, table_mask, labels, params, workers, progress, region_owned=None):
    """Recover measured inclined rods hidden in XY, without using 02A labels.

    Overlapping depth slabs prevent distant fixtures merging into the same side
    silhouette. A side silhouette is only a proposal: unsliced 3D neighbourhoods
    must also be narrow and linear, so a sliced plate edge is not a rod.
    """
    recovered = np.zeros(len(positions), bool)
    rows = np.flatnonzero(~table_mask)
    cache, views = {'source_web_recovered': recovered}, []
    if not len(rows) or not params.side_views_deg:
        cache.update(source_bend_recovered=recovered.copy(), source_fixture_edge=recovered.copy())
        return recovered, {"views": [], "recoveredPoints": 0, "bendRecoveredPoints": 0, "fixtureEdgePoints": 0}, cache
    points = np.asarray(positions[rows], dtype=np.float64)
    points -= points.min(axis=0)
    votes = np.zeros(len(rows), np.uint8)
    projections = []
    for angle in params.side_views_deg:
        progress("投影路线：重叠侧切片 / 腹杆证据", len(views), len(params.side_views_deg))
        radians = np.deg2rad(angle)
        u = points[:, 0]*np.cos(radians) + points[:, 1]*np.sin(radians)
        depth = -points[:, 0]*np.sin(radians) + points[:, 1]*np.cos(radians)
        u -= u.min(); depth -= depth.min()
        pixel = params.pixel_size
        area = (u.max()/pixel+5)*(points[:, 2].max()/pixel+5)
        if area > params.max_pixels:
            pixel *= np.sqrt(area/params.max_pixels)*1.01
        nx = int(np.floor(u.max()/pixel))+5
        ny = int(np.floor(points[:, 2].max()/pixel))+5
        ids = (np.floor(points[:, 2]/pixel).astype(np.int32)+2)*nx + np.floor(u/pixel).astype(np.int32)+2
        order = np.argsort(depth, kind="stable")
        sorted_depth = depth[order]
        proposed = np.zeros(len(rows), bool)
        width = max(params.side_slice_width, params.max_bar_width*3)
        step = width/2
        slices = 0
        for low in np.arange(-step, depth.max()+step*.5, step):
            begin, end = np.searchsorted(sorted_depth, [low, low+width])
            selected = order[begin:end]
            if len(selected) < 8:
                continue
            present = np.zeros((ny, nx), bool)
            present.ravel()[ids[selected]] = True
            joined = ndimage.binary_closing(present, structure=np.ones((3, 3), bool))
            radius = max(1, int(np.ceil(params.max_bar_width/(2*pixel))))
            wide = ndimage.binary_opening(joined, structure=_disk(radius))
            static = ndimage.binary_dilation(wide, structure=_disk(1))
            thin = _long_thin_components(joined & ~static, pixel, params)
            thin = ndimage.binary_dilation(thin, structure=_disk(1)) & ~static & present
            proposed[selected] |= thin.ravel()[ids[selected]]
            slices += 1
        votes += proposed
        key = f"side_{angle:g}"
        projections.append((key, ids, (ny, nx)))
        views.append({"key": key, "angleDeg": float(angle), "sliceCount": slices,
                      "pixelSizeM": float(pixel), "candidatePoints": int(proposed.sum()),
                      "gridShape": [ny, nx]})

    candidates = (votes > 0) & (labels[rows] == 2)
    edge_reclassified = np.zeros(len(positions), bool)
    bend_recovered = np.zeros(len(positions), bool)
    if len(rows):
        # Equal occupied-voxel weights stop scan density from making a plate
        # look linear. The tree sees ALL non-table voxels, never sliced data.
        cells = np.floor(points/params.web_voxel_size).astype(np.int64)
        shape = cells.max(axis=0)+1
        codes = (cells[:, 0]*shape[1]+cells[:, 1])*shape[2]+cells[:, 2]
        _, inverse = np.unique(codes, return_inverse=True)
        mass = np.bincount(inverse)
        centers = np.column_stack([np.bincount(inverse, weights=points[:, axis])/mass for axis in range(3)])
        del cells, codes
        tree = cKDTree(centers)
        accepted = np.zeros(len(centers), bool)
        bend_shape = np.zeros(len(centers), bool)
        face_seed = np.zeros(len(centers), bool)
        face_normal = np.zeros((len(centers), 3))
        face_center = np.zeros((len(centers), 3))
        fixture_candidates = (labels[rows] == 2)
        if region_owned is not None:
            fixture_candidates &= ~region_owned[rows]
        fixture_mass = np.bincount(inverse, weights=fixture_candidates)/mass
        fixture_cells = np.bincount(inverse, weights=~region_owned[rows], minlength=len(centers)) > 0 if region_owned is not None else np.ones(len(centers), bool)
        query_ids = np.arange(len(centers))
        def analyze_chunk(start):
            selected = query_ids[start:start+2048]
            distance, neighbours = tree.query(centers[selected], k=min(256, len(centers)),
                                              distance_upper_bound=params.web_radius, workers=1)
            if distance.ndim == 1:
                return
            for radius in (params.web_radius, params.web_joint_radius):
                valid = np.isfinite(distance) & (distance <= radius)
                support = valid.sum(axis=1)
                delta = centers[np.minimum(neighbours, len(centers)-1)]-centers[selected, None]
                delta[~valid] = 0
                mean = delta.sum(axis=1)/np.maximum(support[:, None], 1)
                covariance = (delta.transpose(0, 2, 1) @ delta)/np.maximum(support[:, None, None], 1)
                covariance -= mean[:, :, None]*mean[:, None, :]
                values, vectors = np.linalg.eigh(covariance)
                major = np.maximum(values[:, 2], 1.e-15)
                linearity = 1-values[:, 1]/major
                transverse_width = 4*np.sqrt(np.maximum(values[:, 1], 0))
                # The smaller neighbourhood avoids absorbing a neighbouring
                # chord into the web fit at joints. Both scales see full 3D.
                good = (support >= 8) & (linearity >= params.web_min_linearity)
                good &= transverse_width <= params.max_bar_width
                good &= np.sqrt(12*major) >= min(params.min_line_length*.8, radius*1.2)
                good &= np.abs(vectors[:, 2, 2]) >= params.web_min_vertical_direction
                accepted[selected] |= good
                if radius == params.web_joint_radius:
                    # Bends may be horizontal at the apex and less linear at
                    # a chord junction. They still need a narrow local shape
                    # and a nearby, independently accepted inclined web seed.
                    bend_shape[selected] = ((support >= 8) & (linearity >= .40)
                        & (transverse_width <= params.web_bend_width)
                        & (np.sqrt(12*major) >= radius*1.2))
                if radius == params.web_radius:
                    # Only broad, filled, already-fixture neighbourhoods are
                    # plane anchors. A thin rod or sparse coplanar wire grid
                    # cannot claim neighbouring steel merely by proximity.
                    plane_rows = np.flatnonzero(fixture_cells[selected])
                    plane_selected = selected[plane_rows]
                    neighbour_fixture = fixture_mass[np.minimum(neighbours[plane_rows], len(centers)-1)]
                    plane_valid = valid[plane_rows] & (neighbour_fixture >= .8)
                    plane_support = plane_valid.sum(axis=1)
                    plane_delta = delta[plane_rows]*plane_valid[:, :, None]
                    plane_mean = plane_delta.sum(axis=1)/np.maximum(plane_support[:, None], 1)
                    plane_cov = (plane_delta.transpose(0, 2, 1) @ plane_delta)/np.maximum(plane_support[:, None, None], 1)
                    plane_cov -= plane_mean[:, :, None]*plane_mean[:, None, :]
                    plane_values, plane_vectors = np.linalg.eigh(plane_cov)
                    middle = np.maximum(plane_values[:, 1], 1.e-15)
                    planar = (plane_support >= 35) & (fixture_mass[plane_selected] >= .8)
                    planar &= (plane_values[:, 0]/middle < .045) & (middle/np.maximum(plane_values[:, 2], 1.e-15) > .30)
                    planar &= np.sqrt(12*middle) >= .020
                    face_seed[plane_selected] = planar
                    face_normal[plane_selected] = plane_vectors[:, :, 0]
                    face_center[plane_selected] = centers[plane_selected]+plane_mean
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(analyze_chunk, range(0, len(query_ids), 2048)))
        # Test actual source-to-face distances, not a blanket XY dilation:
        # bars over/under a fixture remain distinct even in the same pixel.
        source_face = np.zeros(len(rows), bool)
        if face_seed.any() and params.fixture_edge_reach > 0:
            anchors = np.flatnonzero(face_seed)
            faces = cKDTree(centers[anchors])
            face_rows = np.flatnonzero(~region_owned[rows]) if region_owned is not None else np.arange(len(rows))
            for start in range(0, len(face_rows), 65536):
                selected = face_rows[start:start+65536]
                distance, neighbour = faces.query(points[selected], k=min(8, len(anchors)),
                    distance_upper_bound=params.fixture_edge_reach, workers=max(1, workers))
                if distance.ndim == 1:
                    distance, neighbour = distance[:, None], neighbour[:, None]
                ids = anchors[np.minimum(neighbour, len(anchors)-1)]
                offset = points[selected, None]-face_center[ids]
                plane_distance = np.abs(np.sum(offset*face_normal[ids], axis=2))
                source_face[selected] = np.any(np.isfinite(distance) &
                    (plane_distance <= params.fixture_plane_tolerance), axis=1)
        seeds = np.unique(inverse[candidates & accepted[inverse] & ~source_face])
        near_web = np.zeros(len(centers), bool)
        if len(seeds) and params.web_bend_reach > 0:
            seed_tree = cKDTree(centers[seeds])
            distance, _ = seed_tree.query(centers, distance_upper_bound=params.web_bend_reach, workers=max(1, workers))
            near_web = np.isfinite(distance)
            # A nearby disconnected sliver is not a bend. Grow only through
            # measured neighbouring voxels, always bounded by ORIGINAL seeds.
            allowed = near_web & bend_shape
            allowed[seeds] = True
            allowed_ids = np.flatnonzero(allowed)
            pairs = cKDTree(centers[allowed_ids]).query_pairs(params.web_bend_gap, output_type='ndarray')
            graph = coo_matrix((np.ones(len(pairs), np.uint8), (pairs[:, 0], pairs[:, 1])),
                               shape=(len(allowed_ids), len(allowed_ids)))
            _, components = connected_components(graph, directed=False)
            seed_components = np.unique(components[np.searchsorted(allowed_ids, seeds)])
            near_web[:] = False
            near_web[allowed_ids] = np.isin(components, seed_components)
        core = candidates & accepted[inverse] & ~source_face
        bends = (labels[rows] == 2) & bend_shape[inverse] & near_web[inverse] & ~source_face & ~core
        recovered[rows] = core | bends
        bend_recovered[rows] = bends
        # Include v3's strict web proposals vetoed by a measured fixture face,
        # so the diagnostic accounts for every steel-to-fixture correction.
        edge_reclassified[rows] = ((labels[rows] == 3) | (candidates & accepted[inverse])) & source_face
    for key, ids, shape in projections:
        image = np.zeros(shape, np.uint8)
        image.ravel()[ids] = 1
        image.ravel()[ids[recovered[rows]]] = 3
        cache[key] = image
    cache['source_web_recovered'] = recovered
    cache['source_bend_recovered'] = bend_recovered
    cache['source_fixture_edge'] = edge_reclassified
    report = {"views": views, "sliceWidthM": float(max(params.side_slice_width, params.max_bar_width*3)),
              "sliceOverlap": .5, "candidatePoints": int(candidates.sum()),
              "recoveredPoints": int(recovered.sum()),
              "bendRecoveredPoints": int(bend_recovered.sum()), "bendReachM": params.web_bend_reach,
              "fixtureEdgePoints": int(edge_reclassified.sum()), "fixtureEdgeReachM": params.fixture_edge_reach,
              "method": "sliced side web seeds, bounded measured bends and coplanar fixture-face ownership"}
    return recovered, report, cache


def prepare_projection(positions, normals, normal_valid, *, params=None, progress=None, fixed_table=None, fixed_table_mask=None):
    """Fit the table and rasterize once, before independent classification branches."""
    params = params or ProjectionParameters()
    progress = progress or (lambda *args: None)
    started = time.perf_counter(); timings = {}
    count = len(positions)
    lo, hi = positions.min(axis=0), positions.max(axis=0)
    pixel = params.pixel_size
    area = np.prod((hi[:2]-lo[:2])/pixel+5)
    if area > params.max_pixels:
        pixel *= np.sqrt(area/params.max_pixels)*1.01
    origin = lo[:2]-pixel*2
    nx, ny = (np.floor((hi[:2]-origin)/pixel).astype(int)+3).tolist()
    pixels = nx*ny
    nz = int(np.floor((hi[2]-lo[2])/params.histogram_bin))+1
    t0 = time.perf_counter()
    table = _table_plane(positions, normals, normal_valid, lo[2], params) if fixed_table_mask is None else fixed_table
    timings["tableFitS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    progress("投影路线：移除台面 / 汇总 XY 和 Z", 0, count)
    source_to_pixel = np.empty(count, np.int32)
    table_mask = np.zeros(count, bool)
    density = np.zeros(pixels, np.int32)
    zmin = np.full(pixels, np.inf, np.float64); zmax = np.full(pixels, -np.inf, np.float64)
    all_hist = np.zeros(nz, np.int64); histogram = np.zeros(nz, np.int64)
    z_cells = int(np.floor((hi[2]-lo[2])/params.density_z_bin))+1
    # Cache integer XYZ occupancy codes to distinguish continuous vertical
    # walls from sparse overlapping steel layers in the same top-view pixel.
    codes = []
    for start in range(0, count, 262144):
        stop = min(start+262144, count); p = positions[start:stop]
        xy = np.floor((p[:, :2]-origin)/pixel).astype(np.int32)
        ids = xy[:, 1]*nx+xy[:, 0]; source_to_pixel[start:stop] = ids
        if table:
            plane = np.asarray(table["origin"]); slopes = np.asarray(table["slopes"])
            height = p[:, 2]-plane[2]-(p[:, :2]-plane[:2]) @ slopes
            table_mask[start:stop] = height <= params.table_clearance
        if fixed_table_mask is not None:
            table_mask[start:stop] = fixed_table_mask[start:stop]
        keep = ~table_mask[start:stop]
        kept_ids = ids[keep]; z = p[keep, 2]
        # bincount + occupied-index assignment avoids an Npixels allocation
        # per input chunk, while each source row still contributes once.
        unique, mass = np.unique(kept_ids, return_counts=True)
        density[unique] += mass.astype(np.int32)
        np.minimum.at(zmin, kept_ids, z); np.maximum.at(zmax, kept_ids, z)
        zbin = np.floor((p[:, 2]-lo[2])/params.histogram_bin).astype(np.int32)
        all_hist += np.bincount(zbin, minlength=nz)
        histogram += np.bincount(zbin[keep], minlength=nz)
        codes.append(kept_ids.astype(np.int64)*z_cells + np.floor((z-lo[2])/params.density_z_bin).astype(np.int64))
    occupied_codes = np.unique(np.concatenate(codes)); del codes
    occupied_heights = np.bincount(occupied_codes//z_cells, minlength=pixels).astype(np.uint16)
    span = np.zeros(pixels, np.float32)
    occupied = density > 0
    span[occupied] = zmax[occupied]-zmin[occupied]
    # Continuous occupied Z runs distinguish a wall from two separated steel
    # layers. A one-bin acquisition gap is allowed; a large empty gap is not.
    wall_low = np.full(pixels, np.inf, np.float64); wall_high = np.full(pixels, -np.inf, np.float64)
    if len(occupied_codes):
        columns = occupied_codes//z_cells
        bins = occupied_codes % z_cells
        first = np.flatnonzero(np.r_[True, (np.diff(columns) != 0) | (np.diff(occupied_codes) > 2)])
        last = np.r_[first[1:]-1, len(occupied_codes)-1]
        run_width = bins[last]-bins[first]+1
        run_coverage = (last-first+1)/run_width
        good = (run_width*params.density_z_bin >= params.vertical_span) & (run_coverage >= params.vertical_coverage)
        # Keep the longest continuous wall run in each XY pixel, not the air
        # gap between a fixture below and an upper bar passing over it.
        longest = np.zeros(pixels, np.int32)
        np.maximum.at(longest, columns[first[good]], run_width[good])
        good &= run_width == longest[columns[first]]
        np.minimum.at(wall_low, columns[first[good]], lo[2]+bins[first[good]]*params.density_z_bin)
        np.maximum.at(wall_high, columns[first[good]], lo[2]+(bins[last[good]]+1)*params.density_z_bin)
    vertical = np.isfinite(wall_low)
    timings["rasterizeS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    layers, smooth = _height_layers(histogram, lo[2], table, params)
    timings["layersS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    progress("投影路线：二值图细长结构 / 密度竖面", 0, pixels)
    binary = occupied.reshape(ny, nx)
    closed = ndimage.binary_closing(binary, structure=np.ones((3, 3), bool))
    radius = max(1, int(np.ceil(params.max_bar_width/(2*pixel))))
    wide = ndimage.binary_opening(closed, structure=_disk(radius))
    timings["footprintS"] = time.perf_counter()-t0
    return {"params": params, "count": count, "lo": lo, "hi": hi, "pixel": pixel,
            "origin": origin, "nx": nx, "ny": ny, "pixels": pixels, "table": table,
            "source_to_pixel": source_to_pixel, "table_mask": table_mask,
            "density": density, "zmax": zmax, "span": span, "all_hist": all_hist,
            "histogram": histogram, "occupied_heights": occupied_heights,
            "wall_low": wall_low, "wall_high": wall_high, "vertical": vertical,
            "layers": layers, "smooth": smooth, "binary": binary, "closed": closed,
            "radius": radius, "wide": wide, "timings": timings,
            "elapsedS": time.perf_counter()-started}


def classify_projection(positions, normals, normal_valid, *, params=None, workers=1, output=None,
                        progress=None, prepared=None, region_owned=None):
    from copy import deepcopy
    params = params or ProjectionParameters()
    progress = progress or (lambda *args: None)
    started = time.perf_counter()
    shared = prepared is not None
    prepared = prepared if shared else prepare_projection(positions, normals, normal_valid, params=params, progress=progress)
    if prepared["params"] != params or prepared["count"] != len(positions):
        raise ValueError("Projection preparation must match source population and parameters")
    count, lo, hi = prepared['count'], prepared['lo'], prepared['hi']
    pixel, origin = prepared['pixel'], prepared['origin']
    nx, ny, pixels = prepared['nx'], prepared['ny'], prepared['pixels']
    table, table_mask = prepared['table'], prepared['table_mask']
    source_to_pixel, density, zmax = (prepared[k] for k in ('source_to_pixel', 'density', 'zmax'))
    span, all_hist, histogram = (prepared[k] for k in ('span', 'all_hist', 'histogram'))
    occupied_heights, wall_low, wall_high, vertical = (prepared[k] for k in ('occupied_heights', 'wall_low', 'wall_high', 'vertical'))
    layers, smooth = deepcopy(prepared['layers']), prepared['smooth']
    binary, closed, radius, wide = (prepared[k] for k in ('binary', 'closed', 'radius', 'wide'))
    timings = {name: 0.0 for name in prepared['timings']} if shared else dict(prepared['timings'])
    t0 = time.perf_counter()
    fixture = ndimage.binary_dilation(wide | vertical.reshape(ny, nx), structure=_disk(1))
    steel = _long_thin_components(closed & ~fixture, pixel, params)
    # One-pixel fill returns boundary samples to the detected thin feature.
    steel = ndimage.binary_dilation(steel, structure=_disk(1)) & ~fixture & binary
    steel, global_recovered = _restore_crossings(binary, closed, fixture, steel, vertical.reshape(ny, nx), pixel, params)
    image_labels = np.where(steel, 3, np.where(binary, 2, 0)).astype(np.uint8)

    def layer_image(layer):
        keep = (~table_mask) & (positions[:, 2] >= layer["lowM"]) & (positions[:, 2] <= layer["highM"])
        present = np.bincount(source_to_pixel[keep], minlength=pixels).reshape(ny, nx) > 0
        joined = ndimage.binary_closing(present, structure=np.ones((3, 3), bool))
        wide_layer = ndimage.binary_opening(joined, structure=_disk(radius))
        walls = vertical & (wall_low <= layer["highM"]) & (wall_high >= layer["lowM"])
        static = ndimage.binary_dilation(wide_layer | walls.reshape(ny, nx), structure=_disk(1))
        thin = _long_thin_components(joined & ~static, pixel, params)
        thin = ndimage.binary_dilation(thin, structure=_disk(1)) & ~static & present
        thin, recovered = _restore_crossings(present, joined, static, thin, walls.reshape(ny, nx), pixel, params)
        return np.where(thin, 3, np.where(present, 2, 0)).astype(np.uint8), recovered

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(layers)))) as pool:
        layer_results = list(pool.map(layer_image, layers))
    layer_images = [item[0] for item in layer_results]
    source_recovered = np.zeros(count, bool)
    timings["imageClassifyS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    output = output or {"projection_class": np.empty(count, np.uint8), "projection_layer": np.empty(count, np.uint8)}
    labels = output["projection_class"]; layer_ids = output["projection_layer"]
    for start in range(0, count, 262144):
        stop = min(start+262144, count); z = positions[start:stop, 2]
        result = image_labels.ravel()[source_to_pixel[start:stop]].copy()
        recovery = global_recovered.ravel()[source_to_pixel[start:stop]].copy()
        result[table_mask[start:stop]] = 1
        labels[start:stop] = result
        ids = np.zeros(stop-start, np.uint8)
        for layer in layers:
            in_layer = (z >= layer["lowM"]) & (z <= layer["highM"]) & ~table_mask[start:stop]
            ids[in_layer] = layer["id"]
            result[in_layer] = layer_images[layer["id"]-1].ravel()[source_to_pixel[start:stop][in_layer]]
            recovery[in_layer] = layer_results[layer["id"]-1][1].ravel()[source_to_pixel[start:stop][in_layer]]
        labels[start:stop] = result
        ids[table_mask[start:stop]] = 0
        layer_ids[start:stop] = ids
        source_recovered[start:stop] = recovery & (result == 3)
    timings["sourceProjectionS"] = time.perf_counter()-t0
    t0 = time.perf_counter()
    web_recovered, multiview, web_cache = _multiview_webs(positions, table_mask, labels, params, workers, progress, region_owned=region_owned)
    labels[web_recovered] = 3
    labels[web_cache.get('source_fixture_edge', np.zeros(count, bool))] = 2
    source_recovered |= web_recovered
    source_recovered &= labels == 3
    timings["multiviewWebS"] = time.perf_counter()-t0
    source_steel_evidence = (labels == 3).copy()
    counts = np.bincount(labels, minlength=4)
    layer_counts = np.bincount(layer_ids.astype(np.int32)*4+labels, minlength=(len(layers)+1)*4).reshape(-1, 4)
    steel_layers = [i for i, layer in enumerate(layers) if layer_counts[i+1, 3] > layer_counts[i+1, 2]]
    # Reuse the already identified lowest steel height band. Within that
    # interval height alone decides steel, after all fixture/web decisions.
    bottom_mask = np.zeros(count, bool)
    bottom_recovered = np.zeros(count, bool)
    bottom_height = None
    if params.retain_bottom_height and steel_layers:
        bottom = layers[steel_layers[0]]
        bottom_mask = (~table_mask) & (positions[:, 2] >= bottom['lowM']) & (positions[:, 2] <= bottom['highM'])
        bottom_recovered = bottom_mask & (labels != 3)
        edge_override = int(np.count_nonzero(bottom_mask & web_cache['source_fixture_edge']))
        labels[bottom_mask] = 3
        source_recovered |= bottom_recovered
        web_cache['source_fixture_edge'] &= ~bottom_mask
        multiview['fixtureEdgePoints'] = int(web_cache['source_fixture_edge'].sum())
        bottom_height = {'layerId': bottom['id'], 'lowM': bottom['lowM'], 'highM': bottom['highM'],
                         'pointCount': int(bottom_mask.sum()), 'recoveredPoints': int(bottom_recovered.sum()),
                         'overriddenFixtureEdgePoints': edge_override,
                         'method': 'lowest identified steel band; native Z takes priority over silhouette and fixture edge'}
        counts = np.bincount(labels, minlength=4)
        layer_counts = np.bincount(layer_ids.astype(np.int32)*4+labels, minlength=(len(layers)+1)*4).reshape(-1, 4)
    top_mask = np.zeros(count, bool)
    top_recovered = np.zeros(count, bool)
    top_height = None
    if params.retain_top_height and len(steel_layers) >= 2:
        top = layers[steel_layers[-1]]
        top_low = top['lowM']-params.top_height_margin
        top_high = top['highM']+params.top_height_margin
        top_mask = (~table_mask) & (positions[:, 2] >= top_low) & (positions[:, 2] <= top_high)
        top_recovered = top_mask & (labels != 3)
        labels[top_mask] = 3
        source_recovered |= top_recovered
        web_cache['source_fixture_edge'] &= ~top_mask
        top_height = {'layerId': top['id'], 'lowM': top_low, 'highM': top_high,
                      'pointCount': int(top_mask.sum()), 'recoveredPoints': int(top_recovered.sum()),
                      'marginM': params.top_height_margin,
                      'method': 'upper steel height band including rod surface margin'}

    # Locate broad fixtures in their own measured height bands, not in the
    # union of the upper/lower rod silhouettes. Reuse the existing XY grid.
    fixture_ceiling = np.full((ny, nx), -np.inf)
    fixture_floor = np.full((ny, nx), np.inf)
    fixture_mask = np.zeros(count, bool)
    fixture_bands = []
    if params.fixture_footprint_width > 0:
        core_radius = max(1, int(np.ceil(params.fixture_footprint_width/(2*pixel))))
        edge_radius = max(1, int(np.ceil(params.fixture_footprint_edge/pixel)))
        fixture_pixels = prepared.get('fixture_candidate_pixels')
        for i, layer in enumerate(layers):
            if i in steel_layers:
                continue
            present = layer_images[i] != 0
            candidates = layer_images[i] == 2
            if fixture_pixels is not None:
                candidates &= fixture_pixels
            broad = ndimage.binary_opening(candidates, structure=_disk(core_radius))
            footprint = ndimage.binary_dilation(broad, structure=_disk(edge_radius))
            footprint &= ndimage.binary_dilation(present, structure=_disk(1))
            if fixture_pixels is not None:
                footprint &= fixture_pixels
            if footprint.any():
                floor = np.full((ny, nx), np.inf)
                components, _ = ndimage.label(footprint)
                for component, box in enumerate(ndimage.find_objects(components), 1):
                    if box is None:
                        continue
                    inside = components[box] == component
                    lows = wall_low.reshape(ny, nx)[box]
                    highs = wall_high.reshape(ny, nx)[box]
                    supported = inside & (highs >= layer['lowM']) & (lows <= layer['highM'])
                    # Extend a footprint downward only along measured fixture
                    # sides. A suspended plate cannot claim rods far below it.
                    low = layer['lowM']
                    if np.count_nonzero(supported) >= 3:
                        low = min(low, float(np.min(lows[supported])))
                    floor[box][inside] = low-params.fixture_plane_tolerance
                high = layer['highM']+params.fixture_plane_tolerance
                fixture_mask |= ((positions[:, 2] >= floor.ravel()[source_to_pixel]) &
                                 (positions[:, 2] <= high) & ~table_mask)
                fixture_floor = np.minimum(fixture_floor, floor)
                fixture_ceiling[footprint] = np.maximum(fixture_ceiling[footprint], high)
                fixture_bands.append(layer['id'])
        # Existing continuously occupied wall columns remain fixture evidence
        # even where a horizontal steel height band passes through them.
        fixture_mask |= ((positions[:, 2] >= wall_low[source_to_pixel]) &
                         (positions[:, 2] <= wall_high[source_to_pixel]) & ~table_mask)
        fixture_floor = np.minimum(fixture_floor, wall_low.reshape(ny, nx))
        fixture_ceiling = np.maximum(fixture_ceiling, np.where(vertical, wall_high, -np.inf).reshape(ny, nx))
        if fixture_pixels is not None:
            fixture_floor[~fixture_pixels] = np.inf
            fixture_ceiling[~fixture_pixels] = -np.inf
    if region_owned is not None:
        fixture_mask &= ~region_owned
    fixture_reclaimed = fixture_mask & (labels == 3)
    labels[fixture_mask] = 2
    source_steel_evidence &= labels == 3
    source_shape_steel_evidence = source_steel_evidence.copy()
    # The measured lower/upper height bands are part of B's accepted steel
    # classifier, not shared XY ownership. They contribute one B-route vote.
    source_height_steel_evidence = (bottom_mask | top_mask) & (labels == 3)
    source_steel_evidence |= source_height_steel_evidence
    if region_owned is not None:
        labels[region_owned & ~table_mask] = 3
    source_steel_candidate = labels == 3
    source_recovered &= labels == 3
    bottom_recovered &= labels == 3
    top_recovered &= labels == 3
    for key in ('source_web_recovered', 'source_bend_recovered'):
        web_cache[key] &= labels == 3
    multiview['recoveredPoints'] = int(web_cache['source_web_recovered'].sum())
    multiview['bendRecoveredPoints'] = int(web_cache['source_bend_recovered'].sum())
    multiview['fixtureEdgePoints'] = int(web_cache['source_fixture_edge'].sum())
    for height, mask, recovered in ((bottom_height, bottom_mask, bottom_recovered), (top_height, top_mask, top_recovered)):
        if height:
            height['recoveredPoints'] = int(recovered.sum())
            height['retainedPoints'] = int(np.count_nonzero(mask & (labels == 3)))
            height['fixtureExcludedPoints'] = int(np.count_nonzero(mask & fixture_mask))
    counts = np.bincount(labels, minlength=4)
    layer_counts = np.bincount(layer_ids.astype(np.int32)*4+labels, minlength=(len(layers)+1)*4).reshape(-1, 4)
    for i, layer in enumerate(layers):
        if i in steel_layers:
            name = "钢筋候选层" if len(steel_layers) == 1 else "下层钢筋候选" if i == steel_layers[0] else "上层钢筋候选" if i == steel_layers[-1] else "中间钢筋候选"
        else:
            name = "夹具上表面候选"
        layer.update(name=name, pointCount=int(layer_counts[i+1].sum()),
                     classCounts=dict(zip(("table", "fixture", "rebar"), map(int, layer_counts[i+1, 1:4]))))
    # Colour the actually observed topmost source point in each image pixel.
    top_labels = np.zeros(pixels, np.uint8)
    for start in range(0, count, 262144):
        stop = min(start+262144, count); ids = source_to_pixel[start:stop]
        visible = ~table_mask[start:stop] & (np.abs(positions[start:stop, 2]-zmax[ids]) < 1.e-7)
        np.maximum.at(top_labels, ids[visible], labels[start:stop][visible])
    image_labels = top_labels.reshape(ny, nx)
    cache = {"source_to_pixel": source_to_pixel, "table_mask": table_mask,
             "source_recovered": source_recovered,
             "density": density.reshape(ny, nx), "binary": binary,
             "height_span": span.reshape(ny, nx), "vertical": vertical.reshape(ny, nx),
             "wide": wide, "steel": steel, "image_labels": image_labels,
             "layer_images": np.stack(layer_images) if layer_images else np.empty((0, ny, nx), np.uint8),
             "wall_low": wall_low.reshape(ny, nx), "wall_high": wall_high.reshape(ny, nx),
             "histogram_all": all_hist, "histogram_remaining": histogram, "histogram_smooth": smooth,
             "histogram_origin": float(lo[2]), "occupied_height_counts": occupied_heights.reshape(ny, nx)}
    cache.update(web_cache)
    cache["source_steel_evidence"] = source_steel_evidence
    cache["source_shape_steel_evidence"] = source_shape_steel_evidence
    cache["source_height_steel_evidence"] = source_height_steel_evidence
    cache["source_steel_candidate"] = source_steel_candidate
    cache["source_bottom_height"] = bottom_mask
    cache["source_bottom_height_recovered"] = bottom_recovered
    cache["source_top_height"] = top_mask
    cache["source_top_height_recovered"] = top_recovered
    cache["source_fixture_footprint"] = fixture_mask
    cache["source_fixture_footprint_reclaimed"] = fixture_reclaimed
    cache["fixture_footprint_ceiling"] = fixture_ceiling
    cache["fixture_footprint_floor"] = fixture_floor
    cache["fixture_footprint_image"] = np.where(np.isfinite(fixture_ceiling), 2, np.where(binary, 1, 0)).astype(np.uint8)
    edge_image = np.where(binary, 1, 0).astype(np.uint8)
    edge_image.ravel()[source_to_pixel[web_cache['source_fixture_edge']]] = 2
    cache['fixture_edge_image'] = edge_image
    report = {"version": VERSION, "pointCount": count, "classNames": CLASS_NAMES, "colors": COLORS,
              "retentionRestored": True,
              "counts": dict(zip(("table", "fixture", "rebar"), map(int, counts[1:4]))),
              "parameters": asdict(params), "pixelSizeM": float(pixel), "gridShape": [ny, nx],
              "recovery": {"recoveredPoints": int(source_recovered.sum()), "method": "XY crossings, bounded web/bends and final bottom-height retention", "passes": 1},
              "multiview": multiview,
              "bottomHeight": bottom_height,
              "topHeight": top_height,
              "fixtureFootprint": {"layerIds": fixture_bands, "minWidthM": params.fixture_footprint_width,
                                   "edgeReachM": params.fixture_footprint_edge, "pointCount": int(fixture_mask.sum()),
                                   "reclaimedPoints": int(fixture_reclaimed.sum()),
                                   "method": "broad fixture XY footprint bounded by measured surface and connected vertical sides"},
              "xyOriginM": origin.tolist(), "tableRemoval": table, "layers": layers,
              "timings": timings, "elapsedS": time.perf_counter()-started,
              "independentInputs": ["source XYZ", "shared table and region masks" if shared else "Step 1 normals for tabletop fit only"],
              "diagnostics": {"sharedPreparation": shared, "tableFitCalls": 0 if shared else 1,
                              "regionOwnedPoints": int(region_owned.sum()) if region_owned is not None else 0,
                              "fixtureSearchExcludedPoints": int(region_owned.sum()) if region_owned is not None else 0, "verticalPixels": int(vertical.sum()), "widePixels": int(wide.sum()),
                              "steelPixels": int(steel.sum()), "remainingPoints": int((~table_mask).sum()),
                              "detectedLayerCount": len(layers), "forcedLayerCount": False},
              "layerMeaning": "Measured native Z bands, not instances; inclined rods are also checked in overlapping side slices",
              "images": {}}
    return report, cache, output
