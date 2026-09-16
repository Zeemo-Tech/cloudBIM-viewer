"""Verify new immutable run against replay, final design lengths and exports."""
from pathlib import Path
import hashlib, json, sys
import laspy
import numpy as np
from scipy.spatial import cKDTree
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'services/mesh-service'))
from pointcloud_tile_sidecar import pnts_positions, referenced_pnts, RECORD, HEADER
HERE = Path(__file__).parent
asset = ROOT/'backend/data/assets/95b6b41c5857d9eb3407b155'
run_id = json.loads((HERE/'published-run.json').read_text())['runId']
run = asset/'pointcloud-steps'/run_id
baseline = asset/'pointcloud-steps/20260912T113748-23c11a16'
report = json.loads((run/'complete-instances.json').read_text())
assert report['version'] == 'design-guided-instances-v18-closest-design-length'
names = ['complete_class','complete_instance','complete_segment','complete_confidence','complete_cluster']
arrays = {n:np.load(run/f'{n}.npy',mmap_mode='r') for n in names}
for n in names:
    np.testing.assert_array_equal(arrays[n], np.load(ROOT/f'.cloudbim/tail-closest-20260913/current/after-{n}.npy',mmap_mode='r'))
for n in ['positions','normals','refined_class','refined_zone']:
    np.testing.assert_array_equal(np.load(run/f'{n}.npy',mmap_mode='r'),np.load(baseline/f'{n}.npy',mmap_mode='r'))
points = np.load(run/'positions.npy',mmap_mode='r')
by_owner = {i['id']:i for i in report['instances']}
length_checks = []
for decision in report['designReview']['overlengthTailFilter']['decisions']:
    if decision['status'] != 'reclaimed': continue
    item = by_owner[decision['instanceId']]
    before_error = abs(decision['beforeLengthM']-decision['designLengthM'])
    assert abs(item['shapeReview']['observedLengthM']-decision['designLengthM']) < before_error
    assert abs(item['lengthM']-decision['designLengthM']) < before_error
    assert decision['afterDesignErrorM'] < decision['beforeDesignErrorM']
    length_checks.append(dict(instanceId=item['id'], designLengthM=decision['designLengthM'],
                             observedLengthM=item['shapeReview']['observedLengthM'], segmentLengthM=item['lengthM'],
                             beforeErrorM=before_error, afterErrorM=decision['afterDesignErrorM']))
preview = np.fromfile(run/'preview/source_indices.bin',dtype='<u8')
for n in names:
    np.testing.assert_array_equal(np.fromfile(run/'preview'/f'{n}.bin',dtype=arrays[n].dtype),arrays[n][preview])
las_count = 0
with laspy.open(run/'pointcloud-with-classes.las') as reader:
    for chunk in reader.chunk_iterator(262144):
        end = las_count+len(chunk)
        for n in names: np.testing.assert_array_equal(chunk[n],arrays[n][las_count:end])
        las_count = end
assert las_count == len(points)
for filename, expected in [('complete-steel.las',report['counts']['rebar']),
                           ('resolved-steel.las',report['counts']['rebar']),
                           ('pending-steel.las',0),('noise-only.las',report['counts']['noise'])]:
    with laspy.open(run/filename) as reader: assert reader.header.point_count == expected
owner_counts = np.bincount(arrays['complete_instance']); segment_counts = np.bincount(arrays['complete_segment'])
for i in report['instances']: assert i['pointCount'] == owner_counts[i['id']]
for s in report['segments']: assert s['pointCount'] == segment_counts[s['id']]
assert not np.any((arrays['complete_class']==3)&(arrays['complete_instance']==0))
tree = cKDTree(points); files = referenced_pnts(asset/'tiles'); tile_points = 0
for filename, relative in files:
    xyz = pnts_positions(filename); _,rows = tree.query(xyz,workers=4)
    records = np.fromfile(run/'tile-attributes/complete-rebar'/(str(relative)+'.bin'),dtype=RECORD,offset=HEADER.size)
    for n in RECORD.names: np.testing.assert_array_equal(records[n],arrays[n][rows])
    tile_points += len(xyz)
summary = dict(runId=run_id, sourcePointCount=len(points), allFinalLabelsMatchReplay=True,
               sourceNormalsAndRefinedLabelsUnchanged=True, verifiedPreviewPoints=len(preview),
               verifiedLasPoints=las_count, verifiedTiles=len(files), verifiedTilePoints=tile_points,
               instanceCount=report['instanceCount'], unassignedSteelPoints=0, designLengthChecks=length_checks,
               filter=report['designReview']['overlengthTailFilter'],
               codeSha256={n:hashlib.sha256((ROOT/'services/mesh-service/algorithms'/n).read_bytes()).hexdigest()
                           for n in ('rebar_overlength_tails.py','design_guided_instances.py','rebar_geometric_v6.py')})
(HERE/'published-validation.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k not in ('filter','codeSha256')},indent=2),flush=True)
