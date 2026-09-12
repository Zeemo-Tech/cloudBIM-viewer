#!/usr/bin/env python3
"""Audit two saved workbench runs using .cloudbim/mesh-venv/bin/python."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    load = lambda root, name: np.load(root/(name+'.npy'), mmap_mode='r')
    manifests = [json.loads((p/'manifest.json').read_text()) for p in (args.before, args.after)]
    assert manifests[0]['source']['sha256'] == manifests[1]['source']['sha256']
    with Path(manifests[1]['source']['path']).open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == manifests[1]['source']['sha256']
    unchanged = ['positions', 'normals', 'normal_valid', 'fused_class', 'fused_steel_score',
                 'fused_steel_evidence', 'refined_class', 'refined_zone', 'internal_type',
                 'internal_instance', 'internal_segment', 'internal_confidence']
    for name in unchanged:
        np.testing.assert_array_equal(load(args.before, name), load(args.after, name))
    report = json.loads((args.after/'complete-instances.json').read_text())
    before, after = [load(p, 'complete_class') for p in (args.before, args.after)]
    old_ids, ids = [load(p, 'complete_instance') for p in (args.before, args.after)]
    segments = load(args.after, 'complete_segment')
    assert not np.any(ids[after != 3])
    assert not np.any(segments[after != 3])
    assert np.all((ids == 0) == (segments == 0))
    count = np.bincount(ids)
    for item in report['instances']:
        assert count[item['id']] == item['pointCount']
    count = np.bincount(segments)
    segment_owner = np.zeros(len(count), np.uint32)
    for item in report['segments']:
        assert count[item['id']] == item['pointCount']
        segment_owner[item['id']] = item['instanceId']
    np.testing.assert_array_equal(segment_owner[segments], ids)
    assert np.all(after[load(args.after, 'internal_type') == 5] == 4)
    score = load(args.after, 'fused_steel_score')
    restored = (before == 4) & (after == 3)
    removed = (before == 3) & (after == 4)
    review = report['designReview']
    quality = review['clusterQuality']
    cluster_ids = load(args.after, 'complete_cluster')
    hook_report = review.get('hookClusters')
    final_filter = review.get('finalClusterFilter')
    if hook_report:
        hooks = [c for c in report['clusters'] if c.get('category') == 'curved-exterior']
        assert len(hooks) == hook_report['detectedClusterCount']
        for hook in hooks:
            selected = cluster_ids == hook['id']
            assert selected.sum() == hook['pointCount']
            assert np.all(after[selected] == 3)
            assert len(np.unique(ids[selected])) == 1
            if hook['status'] == 'merged':
                assert np.all(ids[selected] == hook['finalInstanceId'])
        assert sum(h['pointCount'] for h in hooks) == hook_report['protectedPointCount']
        operations = review['operations']
        connections = [i for i, op in enumerate(operations) if op['action'] in ('attach', 'merge')]
        filters = [i for i, op in enumerate(operations) if op['action'] == 'filter']
        if connections and filters:
            assert max(connections) < min(filters)
    if final_filter:
        assert final_filter['phase'] == 'final_after_all_merges'
        for decision in final_filter['decisions']:
            selected = cluster_ids == decision['clusterId']
            assert selected.sum() == decision['pointCount']
            assert np.all(after[selected] == 4)
            assert decision['lengthRatio'] < final_filter['maximumLengthRatio']
            assert decision['pointCountRatio'] < final_filter['maximumPointCountRatio']
            assert len(decision['referenceInstanceIds']) >= (1 if decision['referenceKind'] == 'same_design_slot' else 3)
            assert np.count_nonzero(score[selected] >= .9) == decision['highScorePointCount']
        assert sum(d['pointCount'] for d in final_filter['decisions']) == final_filter['removedPointCount']
    result = dict(beforeRun=manifests[0]['runId'], afterRun=manifests[1]['runId'],
        sourceSha256=manifests[1]['source']['sha256'], sourcePointCount=len(after),
        unchangedArrays=unchanged, sourceAndOwnershipAudit='passed',
        restoredPointCount=int(restored.sum()), newlyRemovedPointCount=int(removed.sum()),
        newlyRemovedHighScorePointCount=int(np.count_nonzero(removed & (score >= .9))),
        newlyAssignedPointCount=int(np.count_nonzero((old_ids == 0) & (ids > 0))),
        lostOwnershipPointCount=int(np.count_nonzero((old_ids > 0) & (ids == 0))),
        hookAttachedPointCount=review['hookAttachedPointCount'],
        hookClusters=hook_report,
        finalClusterFilter=final_filter,
        finalDenoising=review['finalDenoising'],
        clusterQuality={k:v for k,v in quality.items() if k != 'instances'},
        elapsedS=report['elapsedS'], fullRunS=manifests[1]['timings']['totalS'])
    (args.output/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (args.output/'cluster-shapes.json').write_text(json.dumps(quality, ensure_ascii=False, indent=2))
    if hook_report and hooks:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        xyz = load(args.after, 'positions')
        columns = min(5, len(hooks))
        fig, axes = plt.subplots((len(hooks)+columns-1)//columns, columns,
                                 figsize=(3.6*columns, 3.4*((len(hooks)+columns-1)//columns)), squeeze=False)
        for ax, hook in zip(axes.ravel(), hooks):
            selected = cluster_ids == hook['id']
            cloud = xyz[selected]
            lo, hi = cloud.min(0)-[.03,.015,.02], cloud.max(0)+[.03,.015,.02]
            rows = np.flatnonzero(np.all((xyz >= lo) & (xyz <= hi), axis=1))
            q = xyz[rows]
            for mask, color, size in ((np.ones(len(rows), bool), '#dddddd', .4),
                                      (after[rows] == 3, '#008d9f', .8),
                                      (selected[rows], '#24a148', 1.),
                                      (removed[rows], '#da1e28', 2.)):
                ax.scatter(q[mask,0], q[mask,2], color=color, s=size, rasterized=True)
            ax.set(title=f"Parent {hook.get('finalInstanceId', 'pending')} / locked {hook['pointCount']}",
                   xlabel='Source X (m)', ylabel='Source Z (m)', aspect='equal')
            ax.grid(alpha=.15)
        for ax in axes.ravel()[len(hooks):]:
            ax.set_visible(False)
        fig.suptitle('All measured hooks: protected green; retained cyan; newly removed red')
        fig.tight_layout()
        fig.savefig(args.output/'all-protected-hooks.png', dpi=160)
        plt.close(fig)
    # Show one actual changed hook with equal axes and stable instance colours.
    hooked = {i for op in review['operations'] if op.get('reason') in (
              'connected_scored_hook_surface_of_observed_parent', 'locked_curved_exterior_cluster_merged_whole')
              for i in op['instanceIds']}
    changed = np.flatnonzero((old_ids == 0) & np.isin(ids, list(hooked)))
    if len(changed):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        xyz = load(args.after, 'positions')
        owner = int(np.bincount(ids[changed]).argmax())
        selected = changed[ids[changed] == owner]
        center = np.median(xyz[selected], axis=0)
        extent = np.array([.12, .025, .10])
        rows = np.flatnonzero(np.all(np.abs(xyz-center) < extent, axis=1))
        fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharex=True, sharey=True)
        for ax, labels, owners, title in zip(axes, (before, after), (old_ids, ids), ('Before', 'After')):
            q = xyz[rows]-center
            ax.scatter(q[:, 0]*1000, q[:, 2]*1000, s=.4, color='#d4d4d8', rasterized=True)
            for mask, color, label in ((owners[rows] == owner, '#16a34a', 'Parent instance'),
                                       ((labels[rows] == 3) & (owners[rows] == 0), '#eab308', 'Pending steel'),
                                       (labels[rows] == 4, '#ef4444', 'Noise')):
                ax.scatter(q[mask, 0]*1000, q[mask, 2]*1000, s=2, color=color, label=label, rasterized=True)
            ax.set(title=title, xlabel='Local X (mm)', ylabel='Local Z (mm)', aspect='equal')
            ax.grid(alpha=.15)
        axes[1].legend(fontsize=8)
        fig.suptitle(f'Observed hook ownership, instance {owner}; unchanged source XYZ')
        fig.tight_layout()
        fig.savefig(args.output/'hook-before-after.png', dpi=180)
        plt.close(fig)
        result['visualExample'] = dict(instanceId=owner, centerM=center.tolist(), halfExtentM=extent.tolist())
        (args.output/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
