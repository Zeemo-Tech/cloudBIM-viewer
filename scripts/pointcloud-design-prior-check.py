#!/usr/bin/env python3
"""Verify source identity, baseline preservation, atomic labels and all export forms."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import laspy
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/mesh-service'))
from algorithms.design_prior_refinement import ATTRIBUTES


def verify(baseline, enhanced):
    a=json.loads((baseline/'manifest.json').read_text());b=json.loads((enhanced/'manifest.json').read_text())
    assert a['source']['sha256']==b['source']['sha256']
    n=a['source']['pointCount'];assert n==b['source']['pointCount']
    for name in a['attributes']['columns']:
        x=np.load(baseline/f'{name}.npy',mmap_mode='r');y=np.load(enhanced/f'{name}.npy',mmap_mode='r')
        assert x.shape==y.shape and x.dtype==y.dtype, name
        for start in range(0,len(x),262144):
            np.testing.assert_array_equal(x[start:start+262144],y[start:start+262144],err_msg=name)
    attrs={name:np.load(enhanced/f'{name}.npy',mmap_mode='r') for name in ATTRIBUTES}
    original=np.load(baseline/'complete_class.npy',mmap_mode='r')
    owners=np.load(baseline/'complete_instance.npy',mmap_mode='r')
    linking_only=b['designPrior'].get('inputPolicy')=='complete_class == 3'
    candidates=(original==3) if linking_only else np.load(baseline/'refined_class.npy',mmap_mode='r')==3
    if linking_only:
        np.testing.assert_array_equal(attrs['prior_class'],original,err_msg='linking must never alter semantic classes')
        assert not np.any(attrs['prior_action']&3),'discarded noise was recovered or retained steel removed'
    np.testing.assert_array_equal(attrs['prior_class'][~candidates],original[~candidates])
    assert np.all((attrs['prior_component']>0)==candidates)
    np.testing.assert_array_equal((attrs['prior_action']&1)>0,(attrs['prior_class']==4)&(original==3))
    np.testing.assert_array_equal((attrs['prior_action']&2)>0,(attrs['prior_class']==3)&(original==4))
    assert np.all(attrs['prior_status'][attrs['prior_class']==3]>0)
    clusters=np.load(baseline/'complete_cluster.npy',mmap_mode='r');ext=np.flatnonzero(clusters>0)
    if len(ext):
        polishable={c['id'] for c in a.get('completeRebar',{}).get('clusters',[])
                    if 'straight-collar-cylinder-polish' in c.get('allowedOperations',[])}
        ordinary=ext[~np.isin(clusters[ext],list(polishable))]
        triples=np.unique(np.column_stack((clusters[ordinary],attrs['prior_class'][ordinary],
                                           attrs['prior_instance'][ordinary])),axis=0)
        assert len(triples)==len(np.unique(clusters[ordinary])), 'ordinary exterior cluster was split'
        for cluster in polishable:
            selected=ext[clusters[ext]==cluster]
            filtered=selected[original[selected]==4];retained=selected[original[selected]==3]
            np.testing.assert_array_equal(attrs['prior_class'][filtered],4)
            assert np.all(attrs['prior_instance'][filtered]==0)
            assert len(np.unique(attrs['prior_instance'][retained]))==1
    components=b['designPrior']['components'];mass=np.bincount(attrs['prior_component'])
    for c in components:assert mass[c['id']]==c['pointCount']
    for key,bit in [('filteredPoints',1),('recoveredPoints',2),('mergedPoints',4)]:
        assert b['designPrior']['counts'][key]==np.count_nonzero(attrs['prior_action']&bit)
    # Never merge distinct observed web units just because their parent matches.
    web_ids=[i['id'] for i in a['completeRebar']['instances'] if i.get('family')==4 or i.get('type')==3]
    for original_id in web_ids:
        mapped={c['instanceId'] for c in components if c['originalInstanceId']==original_id and c['instanceId']}
        if not b['designPrior']['inventory']['coverage']['webStraightUnitCount']:
            assert mapped <= {original_id}
    if linking_only:
        report=b['designPrior'];lookup={c['id']:c for c in components}
        for link in report.get('links',[]):
            left,right=lookup[link['fromComponentId']],lookup[link['toComponentId']]
            assert left['designUnitId']==right['designUnitId']==link['designUnitId']
            assert left['instanceId']==right['instanceId'] and left['instanceId']>0
        short_ids=[i['id'] for i in a['completeRebar']['instances'] if i.get('family')==3]
        for original_id in short_ids:
            mapped={c['instanceId'] for c in components if c['originalInstanceId']==original_id}
            assert mapped=={original_id}, 'review all short-instance changes explicitly'
    preview_ids=np.fromfile(enhanced/'preview/source_indices.bin',dtype='<u8')
    for name,dtype in ATTRIBUTES.items():
        np.testing.assert_array_equal(np.fromfile(enhanced/f'preview/{name}.bin',dtype=dtype),attrs[name][preview_ids],err_msg=name)
    source=Path(a['source']['path'])
    with source.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==a['source']['sha256']
    with laspy.open(source) as raw,laspy.open(enhanced/'pointcloud-with-classes.las') as full:
        offset=0
        for original_chunk,result in zip(raw.chunk_iterator(262144),full.chunk_iterator(262144),strict=True):
            stop=offset+len(original_chunk)
            for name in original_chunk.point_format.dimension_names:
                np.testing.assert_array_equal(result[name],original_chunk[name],err_msg=name)
            np.testing.assert_array_equal(result.source_record_index,np.arange(offset,stop,dtype=np.uint64))
            for name in ATTRIBUTES:np.testing.assert_array_equal(result[name],attrs[name][offset:stop],err_msg=name)
            offset=stop
        assert offset==n
    for filename,cls in [('prior-steel.las',3),('prior-noise.las',4)]:
        expected=np.flatnonzero(attrs['prior_class']==cls)
        with laspy.open(enhanced/filename) as reader:
            offset=0
            for chunk in reader.chunk_iterator(262144):
                ids=expected[offset:offset+len(chunk)]
                np.testing.assert_array_equal(chunk.source_record_index,ids)
                for name in ATTRIBUTES:np.testing.assert_array_equal(chunk[name],attrs[name][ids],err_msg=name)
                offset+=len(chunk)
            assert offset==len(expected)
    return {'runId':b['runId'],'mode':b['designPrior']['mode'],'pointCount':n,'baselineColumns':len(a['attributes']['columns']),
            'exteriorClusters':len(np.unique(clusters[ext])),'components':len(components),'webInstances':len(web_ids),
            'checks':['old NPY columns identical','source LAS fields unchanged','source record indices',
                      'full LAS / NPY / preview / subset prior attributes identical',
                      'whole ordinary exterior clusters / audited hook collar polish','web identity separation',
                      *(['semantic classes frozen','same matching unit on every merge','short instance IDs unchanged'] if linking_only else [])],
            'passed':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--enhanced',type=Path,nargs='+',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    results=[verify(args.baseline,path) for path in args.enhanced]
    args.report.write_text(json.dumps(results,indent=2))
    print(json.dumps(results))


if __name__=='__main__':main()
