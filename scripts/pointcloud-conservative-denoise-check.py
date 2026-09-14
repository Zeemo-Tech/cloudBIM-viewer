"""Audit Step 05 conservative denoising against an immutable same-source run."""
import argparse
import hashlib
import json
from pathlib import Path

import laspy
import numpy as np


def verify(baseline, run):
    before = json.loads((baseline / 'manifest.json').read_text())
    after = json.loads((run / 'manifest.json').read_text())
    assert before['source']['sha256'] == after['source']['sha256']
    load = lambda root, name: np.load(root / f'{name}.npy', mmap_mode='r')
    upstream = ['positions', 'normals', 'geometry_class', 'projection_class',
                'fused_class', 'fused_steel_score', 'fused_steel_evidence', 'fused_reason',
                'shared_table_mask', 'partition_zone', 'refined_class', 'refined_zone', 'refined_region']
    for name in upstream:
        np.testing.assert_array_equal(load(baseline, name), load(run, name), err_msg=name)
    old = load(baseline, 'internal_type') == 5
    noise = load(run, 'internal_type') == 5
    high = load(run, 'fused_steel_score') >= .9 - 1e-6
    added, restored = noise & ~old, old & ~noise
    assert not np.any(added & high), 'this revision must not add high-score deletions in this baseline'
    d = after['internalRebar']['denoising']
    assert d['removedPointCount'] == int(noise.sum())
    assert d['highScoreRemovedPointCount'] == int(np.count_nonzero(noise & high))
    assert d['topViewReview']['enabled']
    for name in ['internal_instance', 'internal_segment', 'internal_confidence']:
        assert not np.any(load(run, name)[noise])
    for name, key in [('internal_instance', 'instances'), ('internal_segment', 'segments')]:
        ids = load(run, name)
        counts = np.bincount(ids)
        records = after['internalRebar'][key]
        assert {int(i) for i in np.unique(ids) if i} == {r['id'] for r in records}
        for record in records:
            assert counts[record['id']] == record['pointCount']
    if (run / 'complete_class.npy').exists():
        assert np.all(load(run, 'complete_class')[noise] == 4)
    preview_ids = np.fromfile(run / 'preview/source_indices.bin', dtype='<u8')
    np.testing.assert_array_equal(np.fromfile(run / 'preview/internal_types.bin', dtype='u1'),
                                  load(run, 'internal_type')[preview_ids])
    source = Path(after['source']['path'])
    with source.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == after['source']['sha256']
    offset = 0
    with laspy.open(source) as raw, laspy.open(run / 'pointcloud-with-classes.las') as saved:
        for original in raw.chunk_iterator(262144):
            chunk = saved.read_points(len(original))
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(chunk[name], original[name], err_msg=name)
            np.testing.assert_array_equal(chunk.internal_type, load(run, 'internal_type')[offset:offset+len(chunk)])
            np.testing.assert_array_equal(chunk.source_record_index, np.arange(offset, offset+len(chunk)))
            offset += len(chunk)
        assert offset == len(noise) and len(saved.read_points(1)) == 0
    for filename in ['steel-only.las', 'internal-steel.las', 'noise-only.las']:
        with laspy.open(run / filename) as reader:
            for chunk in reader.chunk_iterator(262144):
                ids = np.asarray(chunk.source_record_index)
                np.testing.assert_array_equal(chunk.internal_type, load(run, 'internal_type')[ids])
                if filename != 'noise-only.las':
                    assert not noise[ids].any()
    return dict(baseline=baseline.name, run=run.name, sourcePointCount=len(noise),
        upstreamArraysUnchanged=upstream, originalLasFieldsAndSourceOrder='passed',
        instanceCountsAndPreviewAndSubsets='passed',
        beforeNoise=int(old.sum()), afterNoise=int(noise.sum()),
        restoredPointCount=int(restored.sum()), restoredHighScorePointCount=int(np.count_nonzero(restored & high)),
        additionalRemovedPointCount=int(added.sum()), additionalHighScoreRemovedPointCount=int(np.count_nonzero(added & high)),
        highScoreRemovedPointCount=int(np.count_nonzero(noise & high)),
        topViewReview=d['topViewReview'], timings=after['timings'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(verify(args.baseline, args.run), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + '\n')
    print(result)
