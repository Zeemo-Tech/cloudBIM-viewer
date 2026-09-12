"""Verify a freshly published workbench run; run from the repository root."""
from pathlib import Path
import json
import sys

import laspy
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, 'services/mesh-service')
from pointcloud_tile_sidecar import pnts_positions, referenced_pnts, RECORD, HEADER

asset = Path('backend/data/assets/95b6b41c5857d9eb3407b155')
root = asset/'pointcloud-steps'
run = root/sys.argv[1]
baseline = root/'20260912T100054-e6470275'
report = json.loads((run/'complete-instances.json').read_text())
manifest = json.loads((run/'manifest.json').read_text())
assert report['version'] == 'design-guided-instances-v15-overlength-tail-recovery'
names = ['complete_class', 'complete_instance', 'complete_segment', 'complete_confidence', 'complete_cluster']
arrays = {name: np.load(run/(name+'.npy'), mmap_mode='r') for name in names}
points = np.load(run/'positions.npy', mmap_mode='r')
np.testing.assert_array_equal(points, np.load(baseline/'positions.npy', mmap_mode='r'))
for name in ('internal_instance', 'internal_segment', 'internal_type', 'refined_class'):
    np.testing.assert_array_equal(np.load(run/(name+'.npy'), mmap_mode='r'),
                                  np.load(baseline/(name+'.npy'), mmap_mode='r'))
before = {name: np.load(baseline/(name+'.npy'), mmap_mode='r') for name in names}
changed = np.flatnonzero(arrays['complete_class'] != before['complete_class'])
assert len(changed) == report['designReview']['overlengthTailFilter']['removedPointCount'] == 159
assert np.all(before['complete_class'][changed] == 3)
assert np.all(before['complete_instance'][changed] == 78)
assert np.all(arrays['complete_class'][changed] == 4)
keep = np.ones(len(points), bool)
keep[changed] = False
for name in names:
    np.testing.assert_array_equal(arrays[name][keep], before[name][keep])
np.testing.assert_array_equal(arrays['complete_cluster'], before['complete_cluster'])
for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
    assert not np.any(arrays[name][changed])
assert not np.any((arrays['complete_class'] == 3) & (arrays['complete_instance'] == 0))
owner_counts = np.bincount(arrays['complete_instance'])
segment_counts = np.bincount(arrays['complete_segment'])
for item in report['instances']:
    assert item['pointCount'] == owner_counts[item['id']]
for segment in report['segments']:
    assert segment['pointCount'] == segment_counts[segment['id']]
for i, name in enumerate(('table', 'fixture', 'rebar', 'noise'), 1):
    assert report['counts'][name] == np.count_nonzero(arrays['complete_class'] == i)

preview_indices = np.fromfile(run/'preview/source_indices.bin', dtype='<u8')
for name in names:
    np.testing.assert_array_equal(np.fromfile(run/'preview'/(name+'.bin'), dtype=arrays[name].dtype),
                                  arrays[name][preview_indices])
las_count = 0
with laspy.open(run/'pointcloud-with-classes.las') as reader:
    for chunk in reader.chunk_iterator(262144):
        end = las_count+len(chunk)
        for name in names:
            np.testing.assert_array_equal(chunk[name], arrays[name][las_count:end])
        las_count = end
assert las_count == len(points)
subset_counts = {}
for name, expected in [('complete-steel.las', report['counts']['rebar']),
                       ('resolved-steel.las', report['counts']['rebar']),
                       ('pending-steel.las', 0), ('noise-only.las', report['counts']['noise'])]:
    with laspy.open(run/name) as reader:
        subset_counts[name] = reader.header.point_count
        assert reader.header.point_count == expected

tree = cKDTree(points)
files = referenced_pnts(asset/'tiles')
tile_points = 0
for file, relative in files:
    positions = pnts_positions(file)
    _, indices = tree.query(positions, workers=4)
    labels = np.fromfile(run/'tile-attributes/complete-rebar'/(str(relative)+'.bin'),
                         dtype=RECORD, offset=HEADER.size)
    for name in RECORD.names:
        np.testing.assert_array_equal(labels[name], arrays[name][indices])
    tile_points += len(positions)

result = dict(runId=run.name, baselineRun=baseline.name, sourcePointCount=len(points),
              changedClassPoints=len(changed), changedOwnerIds=[78],
              unaffectedPointLabelsIdentical=True, sourceCoordinatesIdentical=True,
              instanceCount=report['instanceCount'], unassignedSteelPoints=0,
              previewPointCount=len(preview_indices), verifiedLasPoints=las_count,
              subsetCounts=subset_counts, verifiedTiles=len(files), verifiedTilePoints=tile_points,
              filter=report['designReview']['overlengthTailFilter'], timings=manifest['timings'])
Path(__file__).with_name('published-run-validation.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k: v for k, v in result.items() if k not in ('filter', 'timings')}, indent=2))
