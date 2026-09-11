"""Detect a fixture enclosure and map source points to top-view regions.

The detector deliberately uses pixels supported by fixture-labelled source rows.
The projection ``wide`` mask is only a shape prior: it prevents thin interior
webs (occasionally labelled as fixture) from becoming enclosure rails.
"""

from __future__ import annotations

import time

import numpy as np
from scipy import ndimage, signal


VERSION = "fixture-regions-v3-rail-boundaries"
REGION_NAMES = {
    "0": "台面",
    "1": "内部钢筋",
    "2": "外露钢筋",
    "3": "夹具",
    "4": "未定位钢筋",
}
COLORS = {
    "0": "#64748b",
    "1": "#2dd4bf",
    "2": "#f472b6",
    "3": "#f59e0b",
    "4": "#60a5fa",
}


def _line_pair(values: np.ndarray, bin_size: float):
    """Return two substantial parallel-line peaks and their outer runs."""
    low = float(values.min()) - bin_size
    bins = np.floor((values - low) / bin_size).astype(np.int32)
    hist = np.bincount(bins).astype(np.float64)
    smooth = ndimage.gaussian_filter1d(hist, 1.5)
    if len(smooth) < 5 or smooth.max(initial=0) < 4:
        return None
    peaks, props = signal.find_peaks(
        smooth, distance=max(2, round(0.035 / bin_size)),
        prominence=max(3.0, float(smooth.max()) * 0.06),
    )
    if len(peaks) < 2:
        return None
    strength = smooth[peaks]
    keep = strength >= float(strength.max()) * 0.22
    peaks, strength = peaks[keep], strength[keep]
    if len(peaks) < 2:
        return None
    minimum_gap = max(5, round(0.12 / bin_size))
    choices = []
    for i in range(len(peaks)):
        for j in range(i + 1, len(peaks)):
            gap = int(peaks[j] - peaks[i])
            if gap >= minimum_gap:
                # Both sides must be strong.  The modest span term breaks ties
                # between a true outer pair and an equally strong interior web.
                score = min(strength[i], strength[j]) + 0.08 * (
                    strength[i] + strength[j]
                ) + 0.002 * gap * strength.max()
                choices.append((float(score), i, j))
    if not choices:
        return None
    _, first, second = max(choices)
    chosen = peaks[[first, second]]

    # Recover the full physical rail width around each peak. Closing bridges
    # the hollow centre of a square tube, while the threshold excludes remote
    # brackets and thin web strands.
    # Thirty percent tracks the body of a rail without absorbing attached feet,
    # handles, or steel supports into its outer edge.
    threshold = smooth >= min(strength[first], strength[second]) * 0.30
    bridge = max(1, round(0.045 / bin_size))
    # Pad before closing: a rail peak near a histogram edge must not be
    # eroded into background, whose component would span unrelated gaps.
    threshold = ndimage.binary_closing(np.pad(threshold, bridge),
        structure=np.ones(bridge, bool))[bridge:-bridge]
    components, _ = ndimage.label(threshold)
    runs = []
    for peak in chosen:
        label = int(components[peak])
        if label == 0:
            return None
        rows = np.flatnonzero(components == label)
        if not len(rows):
            rows = np.array([peak])
        runs.append((float(low + rows[0] * bin_size),
                     float(low + (rows[-1] + 1) * bin_size)))
    centers = low + (chosen.astype(np.float64) + 0.5) * bin_size
    return {
        "centers": centers,
        "runs": runs,
        "strengths": strength[[first, second]],
        "score": float(min(strength[first], strength[second])),
        "histogramMax": float(smooth.max()),
    }


def _orientation_score(xy: np.ndarray, angle: float, bin_size: float):
    c, s = np.cos(angle), np.sin(angle)
    axes = np.array([[c, s], [-s, c]], dtype=np.float64)
    local = xy @ axes.T
    pairs = (_line_pair(local[:, 0], bin_size),
             _line_pair(local[:, 1], bin_size))
    if pairs[0] is None or pairs[1] is None:
        return -np.inf, axes, local, pairs
    score = np.sqrt(pairs[0]["score"] * pairs[1]["score"])
    return float(score), axes, local, pairs


def _detect_frame(xy: np.ndarray, pixel_size: float):
    if len(xy) < 80:
        return None, {"reason": "insufficient fixture rail pixels",
                      "candidatePixels": int(len(xy))}
    bin_size = max(float(pixel_size) * 2.0, 0.003)
    best = None
    # A coarse global search handles square and rotated enclosures, where PCA
    # has no stable major eigenvector. Refinement keeps corner error sub-pixel.
    for degrees in np.arange(0.0, 90.0, 1.0):
        item = _orientation_score(xy, np.deg2rad(degrees), bin_size)
        if best is None or item[0] > best[0]:
            best = (*item, degrees)
    if best is None or not np.isfinite(best[0]):
        return None, {"reason": "four substantial orthogonal rails not found",
                      "candidatePixels": int(len(xy))}
    coarse = best[-1]
    for degrees in np.arange(coarse - 1.0, coarse + 1.001, 0.1):
        item = _orientation_score(xy, np.deg2rad(degrees), bin_size)
        if item[0] > best[0]:
            best = (*item, degrees)
    score, axes, local, pairs, degrees = best
    u_pair, v_pair = pairs
    u0, u1 = sorted(map(float, u_pair["centers"]))
    v0, v1 = sorted(map(float, v_pair["centers"]))
    width, height = u1 - u0, v1 - v0
    if min(width, height) < max(0.12, pixel_size * 12):
        return None, {"reason": "rail pair separation is too small",
                      "candidatePixels": int(len(xy))}

    band = max(0.045, pixel_size * 5)
    u_spans = []
    for center in (u0, u1):
        along = local[np.abs(local[:, 0] - center) <= band, 1]
        u_spans.append(float(np.ptp(np.quantile(along, [.01, .99]))) if len(along) >= 10 else 0.0)
    v_spans = []
    for center in (v0, v1):
        along = local[np.abs(local[:, 1] - center) <= band, 0]
        v_spans.append(float(np.ptp(np.quantile(along, [.01, .99]))) if len(along) >= 10 else 0.0)
    span_ratios = [u_spans[0] / height, u_spans[1] / height,
                   v_spans[0] / width, v_spans[1] / width]
    if min(span_ratios) < 0.48:
        return None, {
            "reason": "candidate lines do not form four enclosing rails",
            "candidatePixels": int(len(xy)),
            "railSpanRatios": list(map(float, span_ratios)),
        }

    # Runs describe the outside edges, not rail centres. The lower rail's low
    # edge and upper rail's high edge make steel above a rail remain interior.
    u_runs = sorted(u_pair["runs"])
    v_runs = sorted(v_pair["runs"])
    bounds = np.array([u_runs[0][0], u_runs[1][1],
                       v_runs[0][0], v_runs[1][1]], dtype=np.float64)
    # The same measured rail runs provide BOTH physical sides. Do not infer
    # the inner boundary by shrinking the exterior with a guessed tube width.
    inner_bounds = np.array([u_runs[0][1], u_runs[1][0],
                             v_runs[0][1], v_runs[1][0]], dtype=np.float64)
    local_corners = np.array([
        [bounds[0], bounds[2]], [bounds[1], bounds[2]],
        [bounds[1], bounds[3]], [bounds[0], bounds[3]],
    ])
    corners = local_corners @ axes
    inner_valid = (inner_bounds[1]-inner_bounds[0] > .08 and inner_bounds[3]-inner_bounds[2] > .08
                   and bounds[0] < inner_bounds[0] < inner_bounds[1] < bounds[1]
                   and bounds[2] < inner_bounds[2] < inner_bounds[3] < bounds[3])
    inner_corners = (np.array([[inner_bounds[0], inner_bounds[2]], [inner_bounds[1], inner_bounds[2]],
                               [inner_bounds[1], inner_bounds[3]], [inner_bounds[0], inner_bounds[3]]]) @ axes
                     if inner_valid else np.empty((0, 2)))
    diagnostics = {
        "candidatePixels": int(len(xy)),
        "angleDegrees": float(degrees % 90.0),
        "orientationScore": float(score),
        "railCentersM": {"u": [u0, u1], "v": [v0, v1]},
        "railStrengthPixels": {
            "u": list(map(float, u_pair["strengths"])),
            "v": list(map(float, v_pair["strengths"])),
        },
        "railSpanM": {"u": u_spans, "v": v_spans},
        "railSpanRatios": list(map(float, span_ratios)),
        "binSizeM": float(bin_size),
        "railWidthsM": [float(b-a) for a, b in u_runs+v_runs],
    }
    return {"axes": axes, "bounds": bounds, "corners": corners,
            "inner_bounds": inner_bounds if inner_valid else np.empty(0), "inner_corners": inner_corners}, diagnostics


def detect_frame_geometry(positions, projection_report, projection_cache, fixture_counts):
    """Detect a measured double frame from a cached raster, before classification."""
    started = time.perf_counter()
    shape = tuple(map(int, projection_report["gridShape"]))
    pixel = float(projection_report["pixelSizeM"])
    origin = np.asarray(projection_report["xyOriginM"], dtype=np.float64)
    source_to_pixel = np.asarray(projection_cache["source_to_pixel"])
    if len(source_to_pixel) != len(positions):
        raise ValueError("source_to_pixel must contain one entry per source row")
    pixels = int(np.prod(shape))

    t0 = time.perf_counter()
    fixture_counts = np.asarray(fixture_counts).reshape(shape)
    fixture_pixels = fixture_counts > 0
    wide = np.asarray(projection_cache.get("wide", np.ones(shape, bool)), dtype=bool)
    if wide.shape != shape:
        raise ValueError("projection wide map does not match gridShape")
    candidate = fixture_pixels & ndimage.binary_dilation(wide, iterations=1)
    row, column = np.nonzero(candidate)
    xy = origin + (np.column_stack((column, row)) + 0.5) * pixel
    raster_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    frame, diagnostics = _detect_frame(xy, pixel)
    detect_time = time.perf_counter() - t0

    inside_image = np.zeros(shape, dtype=bool)
    t0 = time.perf_counter()
    if frame is None:
        frame_report = {"detected": False, "cornersM": [],
                        "method": "rotation-aware wide fixture rail peaks",
                        "support": diagnostics}
        axes = np.empty((0, 2), dtype=np.float64)
        bounds = np.empty(0, dtype=np.float64)
        corners = np.empty((0, 2), dtype=np.float64)
        inner_bounds = np.empty(0, dtype=np.float64)
        inner_corners = np.empty((0, 2), dtype=np.float64)
    else:
        axes, bounds, corners = frame["axes"], frame["bounds"], frame["corners"]
        inner_bounds, inner_corners = frame['inner_bounds'], frame['inner_corners']
        # Raster mapping is also chunked so the maximum eight-million-pixel
        # projection does not need two full float64 coordinate matrices.
        flat_inside = inside_image.ravel()
        nx = shape[1]
        for start in range(0, pixels, 524288):
            stop = min(start + 524288, pixels)
            ids = np.arange(start, stop, dtype=np.int64)
            x = origin[0] + (ids % nx + 0.5) * pixel
            y = origin[1] + (ids // nx + 0.5) * pixel
            u = x * axes[0, 0] + y * axes[0, 1]
            v = x * axes[1, 0] + y * axes[1, 1]
            flat_inside[start:stop] = ((u >= bounds[0]) & (u <= bounds[1]) &
                                       (v >= bounds[2]) & (v <= bounds[3]))
        frame_report = {"detected": True, "cornersM": corners.tolist(),
                        "method": "rotation-aware wide fixture rail peaks",
                        "support": diagnostics}
    table_origin = (projection_report.get('tableRemoval') or {}).get('origin')
    display_height = (table_origin[2] if table_origin is not None else float(positions[:, 2].min()))+.002
    frame_report.update(outerCornersM=corners.tolist(), innerCornersM=inner_corners.tolist(),
                        innerDetected=bool(len(inner_bounds)), displayHeightM=float(display_height))
    map_time = time.perf_counter() - t0

    timings = {"fixtureRasterS": raster_time, "frameDetectionS": detect_time,
               "sourceMappingS": map_time}
    report = {
        "version": VERSION,
        "pointCount": int(len(positions)),
        "regionNames": REGION_NAMES,
        "colors": COLORS,
        "frame": frame_report,
        "timings": timings,
        "elapsedS": float(time.perf_counter() - started),
    }
    cache = {
        "fixture_counts": fixture_counts.astype(np.int32, copy=False),
        "fixture_candidate": candidate,
        "inside": inside_image,
        "frame_axes": axes,
        "frame_bounds_local": bounds,
        "frame_corners_xy": corners,
        "frame_inner_bounds_local": inner_bounds,
        "frame_inner_corners_xy": inner_corners,
    }
    return report, cache


def classify_regions(positions, classes, projection_report, projection_cache, *, workers=1):
    """Legacy standalone entry point; the shared pipeline detects its frame earlier."""
    del workers
    started = time.perf_counter()
    positions, classes = np.asarray(positions), np.asarray(classes)
    if positions.ndim != 2 or positions.shape[1] != 3 or classes.shape != (len(positions),):
        raise ValueError("positions must be Nx3 and classes must contain N rows")
    shape = tuple(map(int, projection_report['gridShape']))
    source_to_pixel = np.asarray(projection_cache['source_to_pixel'])
    if len(source_to_pixel) != len(positions):
        raise ValueError("source_to_pixel must contain one entry per source row")
    fixture_counts = np.bincount(source_to_pixel[classes == 2], minlength=int(np.prod(shape))).reshape(shape)
    report, cache = detect_frame_geometry(positions, projection_report, projection_cache, fixture_counts)
    regions = np.zeros(len(classes), np.uint8)
    regions[classes == 2] = 3
    regions[classes == 3] = 4
    if report['frame']['detected']:
        axes, bounds = cache['frame_axes'], cache['frame_bounds_local']
        for start in range(0, len(positions), 524288):
            stop = min(start+524288, len(positions))
            rows = np.flatnonzero(classes[start:stop] == 3)+start
            local = positions[rows, :2] @ axes.T
            inside = ((local[:, 0] >= bounds[0]) & (local[:, 0] <= bounds[1]) &
                      (local[:, 1] >= bounds[2]) & (local[:, 1] <= bounds[3]))
            regions[rows] = np.where(inside, 1, 2)
    report['counts'] = dict(zip(('table', 'interior', 'exterior', 'fixture', 'unlocated'),
                               map(int, np.bincount(regions, minlength=5))))
    report['elapsedS'] = time.perf_counter()-started
    return report, cache, regions
