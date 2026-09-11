#!/usr/bin/env python3
"""Validate a full shared-scene run against source rows, artifacts and keep rules.

Run with .cloudbim/mesh-venv/bin/python; accepts a published run directory.
"""
import argparse
import hashlib
import json
from pathlib import Path

import laspy
import numpy as np


def validate(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    source = Path(manifest['source']['path'])
    with source.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == manifest['source']['sha256']
    columns = manifest['attributes']['columns']
    names = ('shared_table_mask', 'partition_zone', 'geometry_class', 'geometry_recovered',
             'projection_class', 'fused_class', 'fused_recovered',
             'fused_steel_score', 'fused_steel_evidence', 'refined_class', 'refined_zone', 'internal_type',
             'internal_instance', 'internal_segment', 'complete_class', 'complete_instance', 'complete_segment')
    arrays = {name: np.load(directory/columns[name], mmap_mode='r') for name in names if name in columns}
    n = manifest['source']['pointCount']
    assert all(a.shape == (n,) for a in arrays.values())
    for label, flag in (('geometry_class', 'geometry_recovered'), ('fused_class', 'fused_recovered')):
        assert np.all(arrays[flag] <= 1), flag
        assert np.all(arrays[label][arrays[flag] == 1] == 3), flag
    table = arrays['shared_table_mask'].astype(bool)
    zones = arrays['partition_zone']
    scores, evidence = arrays['fused_steel_score'], arrays['fused_steel_evidence']
    assert np.isfinite(scores).all() and ((scores >= 0) & (scores <= 1)).all()
    assert ((zones >= 0) & (zones <= 3)).all()
    assert ((evidence & np.uint8(240)) == 0).all()
    np.testing.assert_array_equal(scores == 1, (evidence & 3) == 3)
    assert np.all(scores[(evidence & 3) == 0] < .9)
    high = scores >= manifest['fusion']['score']['protectionThreshold']
    for name in ('geometry_class', 'projection_class', 'fused_class', 'refined_class'):
        np.testing.assert_array_equal(arrays[name] == 1, table, err_msg=name)
        inside = arrays[name][(zones == 1) & ~table]
        if name in ('geometry_class', 'projection_class') and manifest['fusion']['score'].get('branchLabelsAreIndependent'):
            assert np.all((inside == 0) | (inside == 3)), name
        else:
            assert np.all(inside == 3), name
    if manifest['fusion']['score'].get('branchLabelsAreIndependent'):
        both = (arrays['geometry_class'] == 3) & (arrays['projection_class'] == 3)
        np.testing.assert_array_equal(high, both)
    if manifest['fusion']['score'].get('projectionHeightCountsAsEvidence'):
        with np.load(directory/'projection-features.npz') as cache:
            shape, height = cache['source_shape_steel_evidence'], cache['source_height_steel_evidence']
            np.testing.assert_array_equal(cache['source_steel_evidence'], shape | height)
            assert np.all(arrays['projection_class'][shape | height] == 3)
            np.testing.assert_array_equal((evidence & 2) != 0, (shape | height) & (arrays['fused_class'] == 3))
    if manifest['refinement'].get('mode') == 'fusion-pass-through':
        np.testing.assert_array_equal(arrays['refined_class'], arrays['fused_class'])
        assert '04-refinement' not in {s['id'] for s in manifest['steps']}
    for name in ('fused_class', 'refined_class', 'complete_class'):
        if name in arrays:
            assert np.all(arrays[name][high] == 3), name
    if 'internal_type' in arrays:
        assert not np.any(arrays['internal_type'][high] == 5)
    if 'refined_zone' in arrays:
        np.testing.assert_array_equal(arrays['refined_zone'], zones)
    for report_name, prefix in (('internalRebar', 'internal'), ('completeRebar', 'complete')):
        if report_name not in manifest:
            continue
        report = manifest[report_name]
        segments = {int(s['id']): s for s in report['segments']}
        instances = {int(i['id']): i for i in report['instances']}
        segment_counts = np.bincount(arrays[prefix+'_segment'], minlength=max(segments, default=0)+1)
        instance_counts = np.bincount(arrays[prefix+'_instance'], minlength=max(instances, default=0)+1)
        for segment in segments.values():
            assert segment['instanceId'] in instances
            assert segment['pointCount'] == segment_counts[segment['id']]
        for instance in instances.values():
            assert instance['pointCount'] == instance_counts[instance['id']]
            assert all(s in segments and segments[s]['instanceId'] == instance['id'] for s in instance['segmentIds'])
        assert set(segments) == {s for instance in instances.values() for s in instance['segmentIds']}
        assert set(np.flatnonzero(segment_counts)) - {0} <= set(segments)
        assert set(np.flatnonzero(instance_counts)) - {0} <= set(instances)
    shared = manifest['preprocessing']['diagnostics']
    assert shared['tableFitCalls'] == shared['xyRasterBuilds'] == shared['frameDetectionCalls'] == 1
    assert manifest['classification']['diagnostics']['tableFitCalls'] == 0
    assert manifest['projection']['diagnostics']['tableFitCalls'] == 0
    assert manifest['regions']['reusedPreclassificationFrame']
    assert manifest['refinement']['diagnostics']['reusedPartition']
    ids = np.fromfile(directory/'preview/source_indices.bin', dtype='<u8')
    for name, preview in (('geometry_recovered', 'recovered'), ('fused_recovered', 'fused_recovered'),
                          ('shared_table_mask', 'shared_table_mask'), ('partition_zone', 'partition_zones'),
                          ('fused_steel_score', 'fused_steel_score'), ('fused_steel_evidence', 'fused_steel_evidence')):
        np.testing.assert_array_equal(np.fromfile(directory/'preview'/f'{preview}.bin', dtype=arrays[name].dtype), arrays[name][ids])
    with laspy.open(source) as original, laspy.open(directory/'pointcloud-with-classes.las') as saved:
        original_fields = list(original.header.point_format.dimension_names)
        offset = 0
        for before, after in zip(original.chunk_iterator(262144), saved.chunk_iterator(262144), strict=True):
            stop = offset+len(before)
            for field in original_fields:
                np.testing.assert_array_equal(before[field], after[field], err_msg=field)
            for field, array in arrays.items():
                np.testing.assert_array_equal(after[field], array[offset:stop], err_msg=field)
            np.testing.assert_array_equal(after.source_record_index, np.arange(offset, stop))
            offset = stop
        assert offset == n
    # Source identities prove the high-score keep rule survives subset export.
    for name in ('steel-only.las', 'fixture-only.las', 'noise-only.las', 'complete-steel.las'):
        path = directory/name
        if not path.exists():
            continue
        with laspy.open(path) as reader:
            for points in reader.chunk_iterator(262144):
                rows = np.asarray(points.source_record_index)
                np.testing.assert_array_equal(points.fused_steel_score, scores[rows])
                np.testing.assert_array_equal(points.fused_steel_evidence, evidence[rows])
                if name in ('noise-only.las', 'fixture-only.las'):
                    assert not np.any(high[rows])
    compute_keys = ('treeS', 'normalsS', 'preprocessingS', 'classifiersWallS', 'fusionS', 'regionsS', 'refinementS', 'internalRebarS')
    return {'runId': manifest['runId'], 'pointCount': n, 'originalFieldsPreserved': len(original_fields),
        'highScorePoints': int(high.sum()), 'highScoreLost': 0,
        'computeThroughStep05S': sum(manifest['timings'].get(k, 0) for k in compute_keys),
        'endToEndThroughStep06S': manifest['timings']['totalS'],
        'scoreCounts': {str(float(score)): int(count) for score, count in zip(*np.unique(scores, return_counts=True))},
        'counts': {name: {str(int(k)): int(v) for k, v in zip(*np.unique(a, return_counts=True))}
                   for name, a in arrays.items() if name != 'fused_steel_score' and not name.endswith(('_instance', '_segment'))},
        'checks': ['source hash', 'original LAS dimensions', 'source record indices', 'single preparation',
                   'shared masks', 'source recovery masks', 'instance counts and references',
                   'prior-only score exclusion', 'all denoising keep rules', 'NPY/LAS/preview/subset scores']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = validate(args.directory)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+'\n')
    print(text)
