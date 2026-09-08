"""Independent XY occupancy/density + Z histogram branch, with source-row labels.

The normal branch's classes and trees are never read. Normals are used only to
fit the low tabletop. All image decisions operate on the remaining raw points.
"""
from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
import time

import numpy as np
from scipy import ndimage
from scipy.signal import find_peaks


VERSION = "projection-geometry-v2"
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


def classify_projection(positions, normals, normal_valid, *, params=None, workers=1, output=None, progress=None):
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
    table = _table_plane(positions, normals, normal_valid, lo[2], params)
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
    counts = np.bincount(labels, minlength=4)
    layer_counts = np.bincount(layer_ids.astype(np.int32)*4+labels, minlength=(len(layers)+1)*4).reshape(-1, 4)
    steel_layers = [i for i, layer in enumerate(layers) if layer_counts[i+1, 3] > layer_counts[i+1, 2]]
    for i, layer in enumerate(layers):
        if i in steel_layers:
            name = "钢筋候选层" if len(steel_layers) == 1 else "下层钢筋候选" if i == steel_layers[0] else "上层钢筋候选" if i == steel_layers[-1] else "中间钢筋候选"
        else:
            name = "夹具上表面候选"
        layer.update(name=name, pointCount=int(layer_counts[i+1].sum()),
                     classCounts=dict(zip(("table", "fixture", "rebar"), map(int, layer_counts[i+1, 1:4]))))
    timings["sourceProjectionS"] = time.perf_counter()-t0
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
    report = {"version": VERSION, "pointCount": count, "classNames": CLASS_NAMES, "colors": COLORS,
              "counts": dict(zip(("table", "fixture", "rebar"), map(int, counts[1:4]))),
              "parameters": asdict(params), "pixelSizeM": float(pixel), "gridShape": [ny, nx],
              "recovery": {"recoveredPoints": int(source_recovered.sum()), "method": "bounded crossing islands with opposing original steel support", "passes": 1},
              "xyOriginM": origin.tolist(), "tableRemoval": table, "layers": layers,
              "timings": timings, "elapsedS": time.perf_counter()-started,
              "independentInputs": ["source XYZ", "Step 1 normals for tabletop fit only"],
              "diagnostics": {"verticalPixels": int(vertical.sum()), "widePixels": int(wide.sum()),
                              "steelPixels": int(steel.sum()), "remainingPoints": int((~table_mask).sum()),
                              "detectedLayerCount": len(layers), "forcedLayerCount": False},
              "layerMeaning": "Measured native Z bands, not instances; slanted web connectivity is deferred",
              "images": {}}
    return report, cache, output
