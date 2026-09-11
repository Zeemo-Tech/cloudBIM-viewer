"""Compare immutable 02A runs and verify source/NPY/LAS/preview consistency.

Use .cloudbim/mesh-venv/bin/python. Changes are candidates for visual review,
not automatically labelled noise or a measurement of classification accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path

import laspy
import numpy as np


def verify(baseline, run):
    before = json.loads((baseline/'manifest.json').read_text())
    after = json.loads((run/'manifest.json').read_text())
    assert before['source']['sha256'] == after['source']['sha256']
    source = Path(after['source']['path'])
    with source.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == after['source']['sha256']
    # The comparison must isolate classification, with identical source rows
    # and the same estimated normals rather than a changed sample or order.
    for name in ('positions', 'normals', 'normal_valid', 'curvature', 'neighbor_radius'):
        a = np.load(baseline/f'{name}.npy', mmap_mode='r')
        b = np.load(run/f'{name}.npy', mmap_mode='r')
        assert a.shape == b.shape, name
        for start in range(0, len(a), 262144):
            np.testing.assert_array_equal(a[start:start+262144], b[start:start+262144], err_msg=name)
    columns = {name: np.load(run/f'{name}.npy', mmap_mode='r')
               for name in ('geometry_class', 'geometry_support', 'geometry_recovered')}
    old = np.load(baseline/'geometry_class.npy', mmap_mode='r')
    new = columns['geometry_class']
    assert len(old) == len(new) == after['source']['pointCount']
    assert set(np.unique(new)) <= {1, 2, 3}
    counts = dict(zip(('table', 'fixture', 'rebar'), map(int, np.bincount(new, minlength=4)[1:4])))
    assert counts == after['classification']['counts']
    ids = np.fromfile(run/'preview/source_indices.bin', dtype='<u8')
    for filename, column in (('classes', 'geometry_class'), ('recovered', 'geometry_recovered')):
        np.testing.assert_array_equal(np.fromfile(run/f'preview/{filename}.bin', dtype='u1'), columns[column][ids])
    offset = 0
    with laspy.open(source) as raw, laspy.open(run/'pointcloud-with-classes.las') as saved:
        assert raw.header.point_count == saved.header.point_count == len(new)
        for original in raw.chunk_iterator(262144):
            chunk = saved.read_points(len(original))
            for name in original.point_format.dimension_names:
                np.testing.assert_array_equal(chunk[name], original[name], err_msg=name)
            for name, array in columns.items():
                np.testing.assert_array_equal(chunk[name], array[offset:offset+len(chunk)], err_msg=name)
            offset += len(chunk)
        assert offset == len(new) and len(saved.read_points(1)) == 0
    return {'baselineRun': baseline.name, 'run': run.name, 'sourceSha256': after['source']['sha256'],
            'sourcePointCount': len(new), 'beforeCounts': before['classification']['counts'], 'afterCounts': counts,
            'removedSteelCandidates': int(np.count_nonzero((old == 3) & (new != 3))),
            'addedSteelCandidates': int(np.count_nonzero((old != 3) & (new == 3))),
            'tableLabelChanges': int(np.count_nonzero((old == 1) != (new == 1))),
            'algorithmVersion': after['classification']['version'],
            'classificationElapsedS': after['classification']['elapsedS'], 'timings': after['timings'],
            'sourceIdentityAndOriginalLasFields': 'passed', 'unchangedStep01': 'passed',
            'npyLasAndPreviewConsistency': 'passed',
            'accuracyScope': 'Candidate changes only; no manually labelled full-scan ground truth.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.baseline, args.run), ensure_ascii=False, indent=2))
