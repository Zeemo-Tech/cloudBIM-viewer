#!/usr/bin/env python3
"""Verify published denoise rows, previews and exports against their original LAS.

Read-only acceptance check. A valid export is not a ground-truth accuracy score.
"""
import argparse
import hashlib
import json
from pathlib import Path

import laspy
import numpy as np

from render_denoise_comparison import load_preview


def verify(dataset):
    source, result, baseline = (Path(dataset[key]) for key in ('source', 'result', 'baselineResult'))
    manifest = json.loads((result/'manifest.json').read_text())
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    assert digest == manifest['sourceSha256'], 'Changed source content'
    with np.load(result/'source-labels.npz', allow_pickle=False) as archive:
        classes, owners, scene = (archive[key].copy() for key in ('classes', 'instances', 'sceneInstances'))
    assert len(classes) == manifest['pointsBefore']
    assert classes.shape == owners.shape == scene.shape
    assert np.array_equal(classes == 3, owners > 0), 'Steel labels and owners disagree'
    clean = laspy.read(result/'cleaned.las')
    with laspy.open(source) as reader:
        dimensions = list(reader.header.point_format.dimension_names)
        assert reader.header.point_count == len(classes)
        offset = exported = 0
        for chunk in reader.chunk_iterator(262144):
            stop = offset+len(chunk)
            selected = classes[offset:stop] == 3
            expected = chunk[selected]
            actual = clean[exported:exported+len(expected)]
            for key in dimensions:
                assert np.array_equal(actual[key], expected[key], equal_nan=True), key
            assert np.array_equal(actual['cloudbim_instance_id'], owners[offset:stop][selected])
            offset, exported = stop, exported+len(expected)
    assert exported == len(clean) == manifest['pointsAfter']
    vertices = load_preview(result/'preview.ply')
    ids = np.linspace(0, len(classes)-1, min(len(classes), 500000), dtype=np.int64)
    assert len(vertices) == len(ids)
    assert np.array_equal(vertices['label'], classes[ids])
    assert np.array_equal(vertices['instance'], owners[ids])
    source_cloud = laspy.read(source)
    for axis, key in enumerate(('x', 'y', 'z')):
        expected = (np.asarray(source_cloud[key])[ids]-manifest['previewOrigin'][axis]).astype(np.float32)
        assert np.array_equal(vertices[key], expected), f'Preview {key}'
    previous = laspy.read(baseline/'cleaned.las')
    previous_dimensions = list(previous.point_format.dimension_names)
    same_export = (len(previous) == len(clean) and previous_dimensions == list(clean.point_format.dimension_names)
                   and all(np.array_equal(previous[key], clean[key], equal_nan=True) for key in previous_dimensions))
    comparison = {'baselineSteelRecordsAndOwnersIdentical': same_export}
    if (baseline/'source-labels.npz').is_file():
        with np.load(baseline/'source-labels.npz', allow_pickle=False) as archive:
            old_classes, old_owners = archive['classes'], archive['instances']
            assert old_classes.shape == classes.shape
            comparison.update(
                oldSteelToNonSteel=int(np.count_nonzero((old_classes == 3) & (classes != 3))),
                nonSteelToSteel=int(np.count_nonzero((old_classes != 3) & (classes == 3))),
                retainedSteelOwnerChanges=int(np.count_nonzero((old_classes == 3) & (classes == 3) & (old_owners != owners))),
                classTransitionCounts=np.bincount(old_classes.astype(int)*7+classes, minlength=49).reshape(7, 7).tolist())
    else:
        old_vertices = load_preview(baseline/'preview.ply')
        assert len(old_vertices) == len(ids)
        comparison.update(baselinePreviewRows=len(ids),
                          baselinePreviewLabelsIdentical=bool(np.array_equal(old_vertices['label'], classes[ids])),
                          baselinePreviewOwnersIdentical=bool(np.array_equal(old_vertices['instance'], owners[ids])))
    return dict(scanId=dataset['scanId'], result=str(result), sourceSha256=digest,
                sourceRows=len(classes), steelRows=exported, sourceDimensionsPreserved=dimensions,
                previewRows=len(ids), previewLabelsOwnersAndCoordinatesExact=True,
                counts=manifest['counts'], elapsedSeconds=manifest['elapsedSeconds'], comparison=comparison)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = [verify(dataset) for dataset in json.loads(args.manifest.read_text())['datasets']]
    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    print(json.dumps([{'scan': r['scanId'], 'sourceRows': r['sourceRows'], 'steelRows': r['steelRows'],
                       'verified': True} for r in reports], ensure_ascii=False))


if __name__ == '__main__':
    main()
