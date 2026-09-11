"""Verify a fresh Step 05 run against its immutable pre-denoise baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import laspy


def verify(baseline, run):
    before = json.loads((baseline/'manifest.json').read_text())
    after = json.loads((run/'manifest.json').read_text())
    assert before['source']['sha256'] == after['source']['sha256']
    assert after['steps'][-1]['id'] == '05-internal-rebar'
    assert 'completeRebar' not in after and 'designPrior' not in after
    old_types = np.load(baseline/'internal_type.npy', mmap_mode='r')
    types = np.load(run/'internal_type.npy', mmap_mode='r')
    noise_ids = np.flatnonzero(types == 5)
    assert len(noise_ids) == after['internalRebar']['counts']['noise']
    assert np.all(old_types[noise_ids] == 4)
    columns = set(before['attributes']['columns']) & set(after['attributes']['columns'])
    for name in sorted(columns):
        old = np.load(baseline/f'{name}.npy', mmap_mode='r')
        new = np.load(run/f'{name}.npy', mmap_mode='r')
        for start in range(0, len(old), 262144):
            a, b = old[start:start+262144], new[start:start+262144]
            if name == 'internal_type':
                a = a.copy()
                a[b == 5] = 5
            np.testing.assert_array_equal(a, b, err_msg=name)
    assert before['internalRebar']['instances'] == after['internalRebar']['instances']
    assert before['internalRebar']['segments'] == after['internalRebar']['segments']
    preview_ids = np.fromfile(run/'preview/source_indices.bin', dtype='<u8')
    np.testing.assert_array_equal(np.fromfile(run/'preview/internal_types.bin', dtype='u1'), types[preview_ids])
    noise = laspy.read(run/'noise-only.las')
    np.testing.assert_array_equal(noise.source_record_index, noise_ids)
    np.testing.assert_array_equal(noise.internal_type, 5)
    for filename in ('internal-steel.las', 'steel-only.las'):
        with laspy.open(run/filename) as reader:
            for chunk in reader.chunk_iterator(262144):
                assert not np.any(chunk.internal_type == 5)
                np.testing.assert_array_equal(chunk.internal_type, types[np.asarray(chunk.source_record_index)])
    source = Path(after['source']['path'])
    with source.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == after['source']['sha256']
    offset = 0
    with laspy.open(source) as raw, laspy.open(run/'pointcloud-with-classes.las') as saved:
        for original in raw.chunk_iterator(262144):
            chunk = saved.read_points(len(original))
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(chunk[name], original[name], err_msg=name)
            np.testing.assert_array_equal(chunk.internal_type, types[offset:offset+len(chunk)])
            offset += len(chunk)
        assert offset == len(types) and len(saved.read_points(1)) == 0
    return {'baseline': baseline.name, 'run': run.name, 'sourcePointCount': len(types),
            'removedPointCount': len(noise_ids), 'unchangedColumns': len(columns)-1,
            'counts': after['internalRebar']['counts'], 'timings': after['timings'],
            'sourceIdentityAndAllOriginalLasFields': 'passed', 'npyLasPreviewAndSubsets': 'passed',
            'assignedInstancesAndSegmentsUnchanged': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline', type=Path)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.baseline, args.run), ensure_ascii=False, indent=2))
