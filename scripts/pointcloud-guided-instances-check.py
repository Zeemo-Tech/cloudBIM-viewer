#!/usr/bin/env python3
"""Audit immutable Step 06 ownership and render source-point comparison views."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    baseline=args.baseline;result=args.result
    report_path=result/'complete-instances.json'
    report=json.loads((report_path if report_path.exists() else result/'report.json').read_text())
    load=lambda root,name:np.load(root/f'{name}.npy',mmap_mode='r')
    p=load(baseline,'positions');old=load(baseline,'internal_instance');cls=load(baseline,'refined_class')
    old_type=load(baseline,'internal_type');new=load(result,'complete_instance');new_cls=load(result,'complete_class')
    old_cls=cls.copy();old_cls[old_type==5]=4
    assert len(p)==len(new)==len(new_cls)
    assert np.all(new_cls[old_cls!=3]==old_cls[old_cls!=3]),'previously rejected/non-steel points changed'
    assert np.all(new[new_cls!=3]==0)
    if (result/'positions.npy').exists():
        for start in range(0,len(p),262144):
            np.testing.assert_array_equal(load(result,'positions')[start:start+262144],p[start:start+262144])
        for key in ('internal_instance','internal_segment','internal_type','normals','refined_class'):
            np.testing.assert_array_equal(load(result,key),load(baseline,key))
    ids,counts=np.unique(new[new>0],return_counts=True)
    expected={int(i):int(n) for i,n in zip(ids,counts)}
    assert expected=={i['id']:i['pointCount'] for i in report['instances']}
    assert len(ids)==report['instanceCount']
    seg=load(result,'complete_segment');seg_ids,seg_counts=np.unique(seg[seg>0],return_counts=True)
    assert {int(i):int(n) for i,n in zip(seg_ids,seg_counts)}=={s['id']:s['pointCount'] for s in report['segments']}
    owner=np.zeros(int(seg.max())+1,np.uint32)
    for s in report['segments']:owner[s['id']]=s['instanceId']
    np.testing.assert_array_equal(owner[seg],new)
    removed=(old_cls==3)&(new_cls==4)
    assert int(removed.sum())==report['designReview']['filteredPoints']
    ops=report['designReview']['operations'];components=report['designReview']['components']
    cmap=plt.get_cmap('hsv');steel_ids=np.flatnonzero((old_cls==3)|(new_cls==3))
    def render(name,rows,axes=(0,1),title='',marked=None):
        if not len(rows):return
        # Deterministic source-row sampling only, with shared coordinates/limits.
        rows=rows[::max(1,len(rows)//240000)]
        fig,plots=plt.subplots(1,2,figsize=(14,5.4),layout='constrained')
        for plot,labels,classes,label in zip(plots,(old,new),(old_cls,new_cls),('Step 05','Step 06')):
            colors=cmap(.10+.72*((np.asarray(labels[rows],float)*.61803398875)%1))
            colors[labels[rows]==0]=[.55,.55,.55,1];colors[classes[rows]==4]=[.9,.12,.15,1]
            plot.scatter(p[rows,axes[0]],p[rows,axes[1]],c=colors,s=.35,linewidths=0,rasterized=True)
            if marked is not None:
                q=rows[marked[rows]]
                plot.scatter(p[q,axes[0]],p[q,axes[1]],s=2,c='#ff1030',linewidths=0)
            plot.set_aspect('equal');plot.set_xlabel('XYZ'[axes[0]]+' (m)');plot.set_ylabel('XYZ'[axes[1]]+' (m)');plot.set_title(label)
        fig.suptitle(title);fig.savefig(args.output/f'{name}.png',dpi=170);plt.close(fig)
    render('global-xy',steel_ids,title='Observed IDs: grey = unresolved; red = noise')
    render('global-xz',steel_ids,axes=(0,2),title='Same source points; no point motion or synthesis')
    filtered_ids=np.flatnonzero(removed)
    render('filtered-global',steel_ids,title='Newly filtered points highlighted red',marked=removed)
    for index,operation in enumerate(sorted((o for o in ops if o['action']=='filter'),key=lambda o:-o['pointCount'])[:5],1):
        target=filtered_ids[(old[filtered_ids]==operation['sourceInstanceId'])] if operation['sourceInstanceId'] else filtered_ids[load(result,'complete_cluster')[filtered_ids]==operation['clusterId']]
        if not len(target):continue
        lo=p[target].min(0)-.012;hi=p[target].max(0)+.012
        rows=np.flatnonzero(np.all((p>=lo)&(p<=hi),axis=1))
        for axes,label in [((0,1),'xy'),((0,2),'xz')]:
            render(f'filter-{index:02}-{label}',rows,axes,title=f"Fixture remnant cluster {operation['clusterId']}: {operation['pointCount']} filtered points",marked=removed)
    merges=[o for o in ops if o['action']=='merge']
    internal_merges=[o for o in merges if max(o['sourceInstanceIds'])<=int(old.max())]
    for i,o in enumerate(internal_merges,1):
        cs=[c for c in components if c['originalInstanceId'] in o['sourceInstanceIds']]
        ends=[np.array(c['evidence'][k]) for c in cs for k in ('start','end')]
        if not ends:continue
        lo=np.min(ends,axis=0)-.025;hi=np.max(ends,axis=0)+.025
        rows=steel_ids[np.all((p[steel_ids]>=lo)&(p[steel_ids]<=hi),axis=1)]
        for axes,label in [((0,1),'xy'),((0,2),'xz'),((1,2),'yz')]:
            render(f'merge-{i:02}-{label}',rows,axes,title=f"IDs {o['sourceInstanceIds']}; endpoint gap {o['gapM']*1000:.1f} mm")
    for i,o in enumerate([o for o in ops if o['action']=='split'],1):
        rows=np.flatnonzero(old==o['sourceInstanceId']) if o['sourceInstanceId'] else np.flatnonzero(load(result,'complete_cluster')==o['clusterId'])
        if not len(rows):continue
        lo=p[rows].min(0)-.02;hi=p[rows].max(0)+.02
        rows=steel_ids[np.all((p[steel_ids]>=lo)&(p[steel_ids]<=hi),axis=1)]
        for axes,label in [((0,1),'xy'),((0,2),'xz'),((1,2),'yz')]:render(f'split-{i:02}-{label}',rows,axes,title=f"Split source ID {o['sourceInstanceId']}, cluster {o['clusterId']}")
        target=np.flatnonzero(old==o['sourceInstanceId']) if o['sourceInstanceId'] else rows
        extent=np.ptp(p[target],axis=0); along=int(np.argmax(extent));mid=float(np.median(p[target,along]))
        local=target[np.abs(p[target,along]-mid)<.06]
        cross=tuple(axis for axis in range(3) if axis!=along)
        render(f'split-{i:02}-section',local,cross,title=f"Source ID {o['sourceInstanceId']}: observed cross section (120 mm axial window)")
    las_checked=False
    if (result/'manifest.json').exists():
        import laspy
        manifest=json.loads((result/'manifest.json').read_text())
        full=result/Path(manifest['files']['lasUrl']).name
        with laspy.open(manifest['source']['path']) as source, laspy.open(full) as target:
            assert source.header.point_count==target.header.point_count==len(p)
            offset=0
            for source_chunk,chunk in zip(source.chunk_iterator(262144),target.chunk_iterator(262144)):
                stop=offset+len(chunk)
                for name in source.header.point_format.dimension_names:
                    np.testing.assert_array_equal(np.asarray(chunk[name]),np.asarray(source_chunk[name]))
                np.testing.assert_array_equal(chunk.source_record_index,np.arange(offset,stop,dtype=np.uint64))
                for name in ('complete_class','complete_instance','complete_segment','complete_cluster','complete_confidence'):
                    np.testing.assert_array_equal(chunk[name],load(result,name)[offset:stop])
                offset=stop
            assert offset==len(p)
        for filename,expected in [('resolved-steel.las',int(np.count_nonzero(new))),
                                  ('pending-steel.las',int(np.sum((new_cls==3)&(new==0)))),
                                  ('complete-steel.las',int(np.sum(new_cls==3))),
                                  ('noise-only.las',int(np.sum(new_cls==4)))]:
            with laspy.open(result/filename) as reader:
                assert reader.header.point_count==expected
                previous=-1;seen=0
                for chunk in reader.chunk_iterator(262144):
                    source_ids=np.asarray(chunk.source_record_index,dtype=np.int64)
                    assert np.all(source_ids[1:]>source_ids[:-1]) and source_ids[0]>previous
                    previous=int(source_ids[-1]);seen+=len(chunk)
                    np.testing.assert_array_equal(np.c_[chunk.x,chunk.y,chunk.z],p[source_ids])
                    np.testing.assert_array_equal(chunk.complete_instance,new[source_ids])
                    np.testing.assert_array_equal(chunk.complete_class,new_cls[source_ids])
                    if filename=='resolved-steel.las':assert np.all(chunk.complete_instance>0) and np.all(chunk.complete_class==3)
                    if filename=='pending-steel.las':assert np.all(chunk.complete_instance==0) and np.all(chunk.complete_class==3)
                    if filename=='complete-steel.las':assert np.all(chunk.complete_class==3)
                    if filename=='noise-only.las':assert np.all(chunk.complete_class==4)
                assert seen==expected
        las_checked=True
    summary={'sourcePointCount':len(p),'instanceCount':len(ids),'filteredPoints':int(removed.sum()),
        'unassignedSteelPoints':int(np.sum((new_cls==3)&(new==0))),
        'internalMergeOperations':internal_merges,'splitOperations':[o for o in ops if o['action']=='split'],
        'checks':'source identity, preceding arrays, no recovery, ownership counts and segment referential integrity passed',
        'independentBaseline':baseline.resolve()!=result.resolve(),'lasRoundtripAndSubsets':las_checked,
        'quality':'no complete manual instance ground truth; render review required'}
    (args.output/'audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
