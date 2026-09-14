#!/usr/bin/env python3
"""Audit two source-identical runs and render an unsampled projection close-up."""
import argparse
import json
from pathlib import Path

import laspy
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def audit(baseline, result, output, roi, view='xz'):
    before = json.loads((baseline/'manifest.json').read_text())
    after = json.loads((result/'manifest.json').read_text())
    assert before['source']['sha256'] == after['source']['sha256'], 'different source LAS'
    old = np.load(baseline/'projection_class.npy', mmap_mode='r')
    new = np.load(result/'projection_class.npy', mmap_mode='r')
    points = np.load(result/'positions.npy', mmap_mode='r')
    assert len(old) == len(new) == len(points)
    transitions = np.zeros((4, 4), np.int64)
    for start in range(0, len(points), 262144):
        end = start+262144
        transitions += np.bincount(old[start:end].astype(int)*4+new[start:end], minlength=16).reshape(4, 4)
    assert sum(transitions[i, j] for i in range(4) for j in range(4) if i != j and (i, j) not in ((2, 3), (3, 2))) == 0
    for name in ('positions', 'normals', 'normal_valid', 'geometry_class', 'geometry_recovered', 'projection_layer'):
        np.testing.assert_array_equal(np.load(baseline/f'{name}.npy', mmap_mode='r'),
                                      np.load(result/f'{name}.npy', mmap_mode='r'), err_msg=name)
    with np.load(baseline/'projection-features.npz', allow_pickle=False) as previous, \
         np.load(result/'projection-features.npz', allow_pickle=False) as cache:
        added = (old == 2) & (new == 3)
        removed = (old == 3) & (new == 2)
        restored = cache['source_web_recovered'].copy()
        if 'source_bottom_height_recovered' in cache:
            restored |= cache['source_bottom_height_recovered']
            band = after['projection']['bottomHeight']
            if band:
                mask = (~cache['table_mask']) & (points[:, 2] >= band['lowM']) & (points[:, 2] <= band['highM'])
                np.testing.assert_array_equal(mask, cache['source_bottom_height'])
                excluded = cache['source_fixture_footprint'] if 'source_fixture_footprint' in cache else np.zeros(len(new), bool)
                assert np.all(new[mask & ~excluded] == 3)
                assert int(cache['source_bottom_height_recovered'].sum()) == band['recoveredPoints']
                if before['projection']['version'] == 'projection-geometry-v4-bends-fixture-edges' and 'source_fixture_footprint' not in cache:
                    np.testing.assert_array_equal(old[~mask], new[~mask])
                    assert not removed.any()
        if 'source_top_height_recovered' in cache:
            restored |= cache['source_top_height_recovered']
            assert np.all(new[cache['source_top_height_recovered']] == 3)
            band = after['projection']['topHeight']
            if band:
                mask = (~cache['table_mask']) & (points[:, 2] >= band['lowM']) & (points[:, 2] <= band['highM'])
                np.testing.assert_array_equal(mask, cache['source_top_height'])
                assert np.all(new[mask & ~cache['source_fixture_footprint']] == 3)
                assert int(cache['source_top_height_recovered'].sum()) == band['recoveredPoints']
        if 'source_subband_recovered' in cache:
            restored |= cache['source_subband_recovered']
            assert np.all(new[cache['source_subband_recovered']] == 3)
            assert int(cache['source_subband_recovered'].sum()) == after['projection']['subbands']['recoveredPoints']
        # Refining the height evidence also changes the measured fixture-face
        # anchors. A former face veto can be lifted, exposing parent XY steel.
        lifted_face = added & ~restored
        if lifted_face.any():
            assert np.all(previous['source_fixture_edge'][lifted_face])
            assert not np.any(cache['source_fixture_face'][lifted_face])
        assert np.all((restored | lifted_face)[added])
        assert np.all(new[cache['source_web_recovered']] == 3)
        if 'source_fixture_edge' in cache:
            face = cache['source_fixture_face'] if 'source_fixture_face' in cache else cache['source_fixture_edge']
            if 'source_fixture_footprint' in cache:
                face = face | cache['source_fixture_footprint']
                assert int(cache['source_fixture_footprint_reclaimed'].sum()) == after['projection']['fixtureFootprint']['reclaimedPoints']
            assert np.all(face[removed])
            assert np.all(new[face] == 2)
            assert np.all(new[cache['source_fixture_edge']] == 2)
            assert int(cache['source_fixture_edge'].sum()) == after['projection']['multiview']['fixtureEdgePoints']
            assert int(cache['source_bend_recovered'].sum()) == after['projection']['multiview']['bendRecoveredPoints']
        else:
            assert not removed.any()
    ids = np.fromfile(result/'preview/source_indices.bin', dtype='<u8')
    np.testing.assert_array_equal(np.fromfile(result/'preview/projection_classes.bin', dtype='u1'), new[ids])
    with laspy.open(after['source']['path']) as source, laspy.open(result/'pointcloud-with-classes.las') as exported:
        offset = 0
        for original in source.chunk_iterator(262144):
            written = exported.read_points(len(original))
            for name in source.header.point_format.dimension_names:
                np.testing.assert_array_equal(original[name], written[name], err_msg=name)
            np.testing.assert_array_equal(written['projection_class'], new[offset:offset+len(original)])
            offset += len(original)
        assert offset == len(new)
    lo = np.array(roi)[[0, 2, 4]]; hi = np.array(roi)[[1, 3, 5]]
    selected = np.flatnonzero(np.all((points >= lo) & (points <= hi), axis=1))
    p = points[selected]
    u, v = {'xz': (0, 2), 'xy': (0, 1), 'yz': (1, 2)}[view]
    fig, axes = plt.subplots(3, 1, figsize=(12, 7), constrained_layout=True)
    for ax, labels, title in zip(axes, [None, old, new], ['Source points', 'Before: steel green / fixture gray', 'After: steel green / fixture gray']):
        colors = '#6b7280' if labels is None else np.where(labels[selected] == 3, '#009e73', '#b3b3b3')
        ax.scatter(p[:, u], p[:, v], s=.7, c=colors, linewidths=0, rasterized=True)
        ax.set_aspect('equal'); ax.set_title(title); ax.set_xlabel('XYZ'[u]+' / m'); ax.set_ylabel('XYZ'[v]+' / m')
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output/'web-detail.png', dpi=170); plt.close(fig)
    if removed.any():
        fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
        context = np.flatnonzero(old == 3)[::10]
        edge_points = points[removed]
        ax.scatter(points[context, 0], points[context, 1], s=.15, c='#aaaaaa', linewidths=0)
        ax.scatter(edge_points[:, 0], edge_points[:, 1], s=.5, c='#d97706', linewidths=0)
        ax.set_aspect('equal'); ax.set_title('Gray: previous steel (1/10 context); orange: fixture evidence veto (all points)')
        ax.set_xlabel('X / m'); ax.set_ylabel('Y / m')
        fig.savefig(output/'fixture-edges.png', dpi=170); plt.close(fig)
    else:
        (output/'fixture-edges.png').unlink(missing_ok=True)
    summary = {'baselineRun': before['runId'], 'resultRun': after['runId'], 'sourceSha256': after['source']['sha256'],
               'pointCount': len(points), 'beforeSteel': int((old == 3).sum()), 'afterSteel': int((new == 3).sum()),
               'recoveredSourcePoints': int(transitions[2, 3]), 'liftedFixtureFaceVetoPoints': int(lifted_face.sum()),
               'reclaimedFixturePoints': int(transitions[3, 2]), 'transitionMatrix': transitions.tolist(),
               'projection': after['projection'], 'timing': after['timing'] if 'timing' in after else after.get('timings'),
               'checks': 'source LAS dimensions, row order, normals, 02A classes, height IDs, LAS/NPY/preview labels all match',
               'roiM': list(roi), 'roiSourcePoints': len(selected), 'view': view,
               'limitation': 'Recovery count is not ground-truth recall; close contacts may remain ambiguous.'}
    (output/'validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({key: summary[key] for key in ('baselineRun', 'resultRun', 'pointCount', 'beforeSteel', 'afterSteel', 'recoveredSourcePoints', 'reclaimedFixturePoints', 'checks')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('result', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--roi', type=float, nargs=6, required=True, metavar=('X0', 'X1', 'Y0', 'Y1', 'Z0', 'Z1'))
    parser.add_argument('--view', choices=('xz', 'xy', 'yz'), default='xz')
    args = parser.parse_args()
    audit(args.baseline, args.result, args.output, args.roi, args.view)
