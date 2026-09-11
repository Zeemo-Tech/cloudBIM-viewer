"""Source-order table/region ownership shared by both independent classifiers.

Only measured broad XY footprints locate the frame. No classifier output feeds
this stage, and frame detection is never repeated after fusion.
"""
import time
import numpy as np
from scipy import ndimage

from .projection_geometry_classifier import prepare_projection, _disk
from .fixture_regions import detect_frame_geometry, REGION_NAMES, COLORS
from .region_refinement import frame_zones, ZONE_NAMES

VERSION = 'shared-scene-v1'
ATTRIBUTES = {'shared_table_mask': 'u1', 'partition_zone': 'u1'}


def regions_from_partition(classes, zones, output=None):
    """Map semantic labels using cached source zones, with no XY recalculation."""
    regions = output if output is not None else np.empty(len(classes), np.uint8)
    for start in range(0, len(classes), 262144):
        stop = min(start+262144, len(classes))
        c, z = classes[start:stop], zones[start:stop]
        regions[start:stop] = np.where(c == 1, 0, np.where(c == 2, 3,
            np.where(z == 0, 4, np.where(z == 3, 2, 1))))
    return regions


def prepare_scene(context, *, output=None, progress=None):
    started = time.perf_counter()
    progress = progress or (lambda *args: None)
    count = len(context.positions)
    progress('共享前处理：移除台面并汇总俯视图', 0, count)
    prepared = prepare_projection(context.positions, context.normals, context.normal_valid, progress=progress)
    output = output if output is not None else {name: np.empty(count, np.uint8) for name in ATTRIBUTES}
    table, zones = output['shared_table_mask'], output['partition_zone']
    table[:] = prepared['table_mask']
    t0 = time.perf_counter()
    progress('共享前处理：钢筋区域与内外框', 0, count)
    projection = {'gridShape': [prepared['ny'], prepared['nx']], 'pixelSizeM': prepared['pixel'],
                  'xyOriginM': prepared['origin'].tolist(), 'tableRemoval': prepared['table']}
    # All non-table occupied pixels can support a rail. Width filtering and
    # four enclosing rail checks decide the frame without either classifier.
    rail_width = prepared['params'].fixture_footprint_width
    frame_wide = ndimage.binary_opening(prepared['closed'],
        structure=_disk(max(1, int(np.ceil(rail_width/(2*prepared['pixel']))))))
    prepared['frame_wide'] = frame_wide
    region_report, region_cache = detect_frame_geometry(context.positions,
        projection, {'source_to_pixel': prepared['source_to_pixel'], 'wide': frame_wide}, prepared['density'])
    zones[:] = 0
    frame = region_report['frame']
    if frame['detected'] and frame['innerDetected']:
        for start in range(0, count, 262144):
            stop = min(start+262144, count)
            zones[start:stop] = frame_zones(context.positions[start:stop, :2], region_cache['frame_axes'],
                region_cache['frame_bounds_local'], region_cache['frame_inner_bounds_local'])
    elif frame['detected']:
        # The outer frame still locates external steel, but no inner ownership
        # may be inferred when the measured rail inner edges are unavailable.
        for start in range(0, count, 262144):
            stop = min(start+262144, count)
            xy = context.positions[start:stop, :2] @ region_cache['frame_axes'].T
            b = region_cache['frame_bounds_local']
            inside = (xy[:, 0] >= b[0]) & (xy[:, 0] <= b[1]) & (xy[:, 1] >= b[2]) & (xy[:, 1] <= b[3])
            zones[start:stop] = np.where(inside, 2, 3)
    owned = (zones == 1) & ~prepared['table_mask']
    # Mixed boundary pixels remain eligible; the exact source mask prevents
    # their inner points from being claimed by a fixture later.
    prepared['fixture_candidate_pixels'] = (np.bincount(
        prepared['source_to_pixel'][~owned & ~prepared['table_mask']],
        minlength=prepared['pixels']) > 0).reshape(prepared['ny'], prepared['nx'])
    zone_counts = np.bincount(zones[~prepared['table_mask']], minlength=4)
    partition_time = time.perf_counter()-t0
    report = {'version': VERSION, 'pointCount': count,
        'tableRemoval': {'detected': prepared['table'] is not None,
            'plane': prepared['table'], 'removedPoints': int(np.count_nonzero(table)),
            'remainingPoints': int(count-np.count_nonzero(table)), 'elapsedS': prepared['timings']['tableFitS']},
        'partition': {'frame': frame, 'zoneNames': ZONE_NAMES,
            'counts': dict(zip(('unlocated', 'interior', 'band', 'exterior'), map(int, zone_counts))),
            'elapsedS': partition_time, 'policy': 'strict inner non-table ownership; measured rail edges; no classifier labels'},
        'timings': {**prepared['timings'], 'partitionS': partition_time},
        'diagnostics': {'tableFitCalls': 1, 'xyRasterBuilds': 1, 'frameDetectionCalls': 1,
            'sourcePointCount': count, 'ownedSteelPoints': int(owned.sum()),
            'frameInput': 'non-table broad rail XY footprint, before either classifier', 'frameMinWidthM': rail_width},
        'elapsedS': time.perf_counter()-started}
    # Classifiers share immutable evidence, not writable labels. B makes its
    # own copy of layer metadata before assigning class names/counts.
    for value in prepared.values():
        if isinstance(value, np.ndarray):
            value.flags.writeable = False
    owned.flags.writeable = False
    context.shared_table_mask, context.partition_zone = table, zones
    context.region_cache = region_cache
    context.scene_cache = {'projection': prepared, 'region_owned': owned, 'regions': region_report, 'report': report}
    return report


def partition_region_report(context, regions):
    report = dict(context.scene_cache['regions'])
    report['counts'] = dict(zip(('table', 'interior', 'exterior', 'fixture', 'unlocated'),
                               map(int, np.bincount(regions, minlength=5))))
    report.update(regionNames=REGION_NAMES, colors=COLORS, reusedPreclassificationFrame=True,
                  timings={'frameDetectionS': 0.0}, elapsedS=0.0)
    return report
