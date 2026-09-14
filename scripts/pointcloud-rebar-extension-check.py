#!/usr/bin/env python3
"""Check full-source extension artifacts and optionally render the same source points."""
import argparse
import colorsys
import hashlib
import importlib.util
import json
from pathlib import Path

import laspy
import numpy as np
from threadpoolctl import threadpool_limits

ATTRIBUTES = {'complete_class':'u1', 'complete_instance':'<u4', 'complete_segment':'<u4', 'complete_confidence':'<f4'}


def validate(run, baseline=None):
    manifest = json.loads((run/'manifest.json').read_text())
    report = manifest['completeRebar']
    attributes = dict(ATTRIBUTES)
    if 'clusters' in report:
        attributes['complete_cluster'] = '<u4'
    final = manifest.get('lineConstraint')
    if final:
        attributes.update(final_class='u1', final_instance='<u4', final_component='<u4', final_line_support='u1')
    arrays = {name:np.load(run/f'{name}.npy', mmap_mode='r') for name in attributes}
    classes, instances, segments = [arrays['complete_'+name] for name in ('class','instance','segment')]
    refined = np.load(run/'refined_class.npy', mmap_mode='r')
    internal = np.load(run/'internal_instance.npy', mmap_mode='r')
    zones = np.load(run/'refined_zone.npy', mmap_mode='r')
    assert sum(report['counts'].values()) == manifest['source']['pointCount'] == len(classes)
    assert np.isin(classes, [1,2,3,4]).all()
    protected = (refined != 3) | (zones == 1)
    np.testing.assert_array_equal(classes[protected], refined[protected])
    np.testing.assert_array_equal(instances[zones == 1], internal[zones == 1])
    assert np.all(instances[classes != 3] == 0)
    seg_counts = np.bincount(segments, minlength=len(report['segments'])+1)
    instance_counts = np.bincount(instances, minlength=max([0]+[i['id'] for i in report['instances']])+1)
    for seg in report['segments']:
        assert seg_counts[seg['id']] == seg['pointCount']
        assert np.all(instances[segments == seg['id']] == seg['instanceId'])
    for instance in report['instances']:
        assert instance_counts[instance['id']] == instance['pointCount']
    if 'clusters' in report:
        cluster_ids = arrays['complete_cluster']
        cluster_sizes = np.bincount(cluster_ids, minlength=len(report['clusters'])+1)
        polished = {op['clusterId']: op['pointCount'] for op in report.get('designReview', {}).get('operations', [])
                    if op.get('reason') == 'hook_straight_collar_fixture_contact_outside_local_cylinder'}
        for cluster in report['clusters']:
            mask = cluster_ids == cluster['id']
            assert cluster_sizes[cluster['id']] == cluster['pointCount']
            if cluster['id'] in polished:
                filtered = mask & (classes == 4); retained = mask & (classes == 3)
                assert filtered.sum() == polished[cluster['id']] and np.all(instances[filtered] == 0)
                assert np.all(instances[retained] == cluster['finalInstanceId'])
            else:
                assert len(np.unique(classes[mask])) == len(np.unique(instances[mask])) == 1
                assert np.all(instances[mask] == cluster['instanceId'])
                assert np.all(classes[mask] == (4 if cluster['status'] == 'noise' else 3))
    assert json.loads((run/'complete-instances.json').read_text()) == report
    if final:
        fc, fi, groups, support = [arrays['final_'+name] for name in ('class','instance','component','line_support')]
        changed = fc != classes
        assert np.all((classes[changed] == 3) & (fc[changed] == 4))
        assert changed.sum() == final['removedPointCount']
        np.testing.assert_array_equal(fi[fc == 3], instances[fc == 3])
        assert np.all(fi[fc != 3] == 0)
        assert np.all(groups[classes != 3] == 0)
        assert np.isin(support, [0,1]).all()
        assert np.all(support[classes != 3] == 0)
        if final['enabled']:
            assert np.all(groups[classes == 3] > 0)
        assert final['counts'] == dict(zip(('table','fixture','rebar','noise'), map(int,np.bincount(fc,minlength=5)[1:5])))
        assert final['retainedPointCount'] == (fc == 3).sum()
        sizes = np.bincount(groups)
        votes = np.bincount(groups, weights=support)
        retained = np.bincount(groups, weights=fc == 3)
        assert len(final['components']) == final['componentCount']
        assert sum(c['status'] == 'noise' for c in final['components']) == final['removedComponentCount']
        for component in final['components']:
            i = component['id']
            assert sizes[i] == component['pointCount']
            assert votes[i] == component['lineSupportedPointCount']
            assert retained[i] == (sizes[i] if component['status'] == 'retained' else 0)
        # Ordinary exterior clusters remain indivisible. Protected hook atoms may
        # lose only the explicitly audited straight-collar fixture contact rows.
        original = arrays['complete_cluster']
        old_sizes = np.bincount(original)
        old_retained = np.bincount(original, weights=fc == 3)
        ordinary = np.ones(len(old_sizes), bool)
        ordinary[0] = False
        ordinary[list(polished)] = False
        assert np.all((old_retained[ordinary] == 0) | (old_retained[ordinary] == old_sizes[ordinary]))
        counts = np.bincount(fi)
        for instance in final['instances']:
            assert counts[instance['id']] == instance['pointCount']
        assert final['lineCount'] == len(final['lines'])
        for line in final['lines']:
            midpoint = (line['lowM'] + line['highM']) / 2
            assert line['startM'][2] == line['endM'][2] == midpoint
        assert json.loads((run/'projection-line-constraint.json').read_text()) == final
        assert (run/'final-rebar-lines.png').stat().st_size > 10000
    source = Path(manifest['source']['path'])
    with source.open('rb') as stream:
        assert hashlib.file_digest(stream,'sha256').hexdigest() == manifest['source']['sha256']
    offset = 0
    with laspy.open(source) as original, laspy.open(run/'pointcloud-with-classes.las') as exported:
        assert original.header.point_count == exported.header.point_count
        for chunk in original.chunk_iterator(262144):
            saved = exported.read_points(len(chunk)); stop = offset+len(chunk)
            for name in original.header.point_format.dimension_names:
                np.testing.assert_array_equal(saved[name], chunk[name])
            for name, array in arrays.items():
                np.testing.assert_array_equal(saved[name], array[offset:stop])
            np.testing.assert_array_equal(saved.source_record_index, np.arange(offset,stop))
            offset = stop
    subsets = [('complete-steel.las',3,classes),('noise-only.las',4,classes)]
    if final:
        subsets += [('final-steel.las',3,fc),('final-noise.las',4,fc)]
    for name, category, subset_classes in subsets:
        ids = np.flatnonzero(subset_classes == category); offset = 0
        with laspy.open(run/name) as reader:
            assert reader.header.point_count == len(ids)
            for chunk in reader.chunk_iterator(262144):
                selected = ids[offset:offset+len(chunk)]; offset += len(chunk)
                np.testing.assert_array_equal(chunk.source_record_index, selected)
                for attribute, array in arrays.items():
                    np.testing.assert_array_equal(chunk[attribute], array[selected])
    ids = np.fromfile(run/'preview/source_indices.bin', dtype='<u8')
    for name, dtype in attributes.items():
        np.testing.assert_array_equal(np.fromfile(run/f'preview/{name}.bin', dtype=dtype), arrays[name][ids])
    unchanged = []
    if baseline:
        for path in baseline.glob('*.npy'):
            np.testing.assert_array_equal(np.load(path,mmap_mode='r'), np.load(run/path.name,mmap_mode='r'))
            unchanged.append(path.name)
    return {'runId':manifest['runId'], 'pointCount':len(classes), 'checks':'source fields/hash, LAS/NPY/preview/subsets, class and instance ownership, JSON counts',
            'unchangedBaselineColumns':sorted(unchanged), 'counts':report['counts'],
            'finalCounts':final['counts'] if final else None,
            'matchedExteriorPointCount':report['matchedExteriorPointCount'], 'rejectedExteriorPointCount':report['rejectedExteriorPointCount']}


def figure(run, output):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    spec = importlib.util.spec_from_file_location('raster',Path(__file__).with_name('pointcloud-classification-compare.py'))
    raster = importlib.util.module_from_spec(spec); spec.loader.exec_module(raster); raster.choose_font()
    manifest=json.loads((run/'manifest.json').read_text()); report=manifest['completeRebar']
    points=np.load(run/'positions.npy',mmap_mode='r'); refined=np.load(run/'refined_class.npy',mmap_mode='r')
    classes=np.load(run/'complete_class.npy',mmap_mode='r'); ids=np.load(run/'complete_instance.npy',mmap_mode='r')
    palette=np.array([[148,163,184],[100,116,139],[245,158,11],[45,212,191],[239,71,111]],np.uint8)
    instance_palette=np.array([[148,163,184]]+[[round((12.92*v if v<=.0031308 else 1.055*v**(1/2.4)-.055)*255)
        for v in colorsys.hls_to_rgb((i*.61803398875)%1,.58,.72)] for i in range(1,report['instanceCount']+1)],np.uint8)
    crop=np.array([[5.95,6.45],[-2.35,-1.25],[0.,.15]] if 'clusters' in report else [[2.15,2.75],[-2.5,-1.25],[.012,.14]])
    panels=[('延伸前 · 外部候选仍统一标为钢筋',refined,palette,None,None),
            ('延伸后 · 台面 / 夹具 / 钢筋 / 噪音',classes,palette,None,None),
            (('弯钩端局部' if 'clusters' in report else '左端局部')+' · 保留钢筋与被过滤噪音',classes,palette,crop,refined==3),
            ('同一局部 · 内外沿用同一实例颜色',ids,instance_palette,crop,classes==3)]
    fig,axes=plt.subplots(2,2,figsize=(16,12),facecolor='#0c1421')
    for ax,(title,labels,colors,window,keep) in zip(axes.flat,panels):
        bounds,_=raster.projected_bounds([points],window)
        raster.PALETTE=colors
        selected=points if keep is None else points[keep]; selected_labels=labels if keep is None else labels[keep]
        img,_,_=raster.rasterize(selected,selected_labels,window,bounds)
        ax.imshow(img,origin='lower');ax.set_title(title,color='#e6edf6',loc='left',pad=12);ax.axis('off')
    fig.suptitle(f"{'钢筋整簇接续' if 'clusters' in report else '钢筋轴向接续'} · {report['matchedExteriorPointCount']:,} 点归入已有实例\n{report['rejectedExteriorPointCount']:,} 个外部候选点归为噪音（粉红）",color='#e6edf6',x=.05,ha='left',fontsize=20)
    fig.text(.05,.025,'全部图像直接投影源点；未补造遮挡点。颜色为算法判定，未经过人工真值标注。',color='#9dacc2',fontsize=12)
    fig.subplots_adjust(left=.04,right=.98,top=.87,bottom=.06,hspace=.12,wspace=.06)
    output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(output,dpi=120,facecolor=fig.get_facecolor());plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    parser.add_argument('--baseline',type=Path);parser.add_argument('--figure',type=Path);args=parser.parse_args()
    with threadpool_limits(limits=1):
        result=validate(args.run,args.baseline)
        if args.figure: figure(args.run,args.figure)
    print(json.dumps(result,ensure_ascii=False,indent=2))
