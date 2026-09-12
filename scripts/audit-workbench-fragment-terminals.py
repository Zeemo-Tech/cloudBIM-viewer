#!/usr/bin/env python3
"""Compare premerge fragment cleanup to a previous immutable workbench run."""
import argparse,json
from pathlib import Path
import numpy as np
import laspy


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline',type=Path);p.add_argument('run',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    m=json.loads((a.run/'manifest.json').read_text());old=json.loads((a.baseline/'manifest.json').read_text())
    load=lambda root,name:np.load(root/(name+'.npy'),mmap_mode='r')
    cls=load(a.run,'complete_class');prev=load(a.baseline,'complete_class');owner=load(a.run,'complete_instance');removed=load(a.run,'terminal_removed')>0
    reason=load(a.run,'terminal_reason');origin=load(a.run,'terminal_origin');fragment=load(a.run,'terminal_fragment')
    base_names=('positions','normals','geometry_class','projection_class','fused_class','refined_class','internal_type','internal_instance','internal_segment')
    unchanged={name:bool(np.array_equal(load(a.run,name),load(a.baseline,name))) for name in base_names}
    hard=(load(a.run,'shared_table_mask')>0)|(load(a.run,'shared_floating_noise')>0)|(load(a.run,'internal_type')==5)
    curved={c['id'] for c in m['completeRebar']['clusters'] if c.get('category')=='curved-exterior'}
    previous_owner=load(a.run,'terminal_previous_instance');web={r['instanceId'] for r in m['acceptance']['instances'] if r.get('kind')=='web'}
    by_phase={str(i):int(np.count_nonzero(removed&(reason==i))) for i in (1,2,3)}
    audit=dict(runId=m['runId'],baselineRunId=old['runId'],sourcePoints=len(cls),runtimeSeconds=m['timings']['totalS'],ratioToFrozenBaseline=m['timings']['totalS']/40.88584,
        observedInstances=m['acceptance']['observedInstances'],failedInstances=m['acceptance']['failedInstances'],topologyConflicts=len(m['acceptance']['topologyFailures']),
        removedPointCount=int(removed.sum()),byPhase=by_phase,byOrigin={name:int(np.count_nonzero(removed&(origin==code))) for name,code in [('internal',1),('exterior',2)]},
        fragmentCount=m['terminalCleanup']['fragmentCount'],premergeReportCount=m['terminalCleanup']['premergeRemovedPointCount'],
        newlyRemovedVsBaseline=int(np.count_nonzero((prev==3)&(cls==4))),newSteelVsBaseline=int(np.count_nonzero((prev!=3)&(cls==3))),
        unchangedEarlierArrays=unchanged,restoredPremergePoints=int(np.count_nonzero((reason==2)&((cls==3)|(owner>0)))),
        hardBoundarySteel=int(np.count_nonzero(hard&(cls==3))),hookPointsRemoved=int(np.count_nonzero(removed&np.isin(load(a.run,'complete_cluster'),list(curved)))),
        mappedWebPointsRemoved=int(np.count_nonzero(removed&np.isin(previous_owner,list(web)))),
        fragmentProvenanceComplete=bool(np.all(fragment[removed&(reason>1)]>0)),removedRowsAreNoise=bool(np.all(cls[removed]==4)),
        manifestCountsAgree=int(removed.sum())==m['terminalCleanup']['removedPointCount'])
    new_reasons=[];old_by_unit={i['designUnitId']:i for i in old['acceptance']['instances']}
    for r in m['acceptance']['instances']:
        before=old_by_unit.get(r['designUnitId'])
        if before and set(r['reasons'])-set(before['reasons']):new_reasons.append(dict(instanceId=r['instanceId'],reasons=sorted(set(r['reasons'])-set(before['reasons']))))
    audit['newGeometryFailureReasonsVsBaseline']=new_reasons
    attrs=['terminal_removed','terminal_reason','terminal_previous_instance','terminal_previous_segment','terminal_fragment','terminal_origin','complete_class','complete_instance','complete_segment']
    agreement={name:True for name in attrs};offset=0
    with laspy.open(a.run/'pointcloud-with-classes.las') as reader:
        for chunk in reader.chunk_iterator(500000):
            for name in attrs:agreement[name]&=bool(np.array_equal(chunk[name],load(a.run,name)[offset:offset+len(chunk)]))
            assert np.array_equal(chunk.source_record_index,np.arange(offset,offset+len(chunk)))
            offset+=len(chunk)
    audit['lasNpyAgreement']=agreement
    a.output.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n');print(json.dumps(audit,ensure_ascii=False,indent=2))
    assert all(unchanged.values()) and all(agreement.values())
    assert audit['restoredPremergePoints']==audit['hardBoundarySteel']==audit['hookPointsRemoved']==audit['mappedWebPointsRemoved']==0
    assert audit['fragmentProvenanceComplete'] and audit['removedRowsAreNoise'] and audit['manifestCountsAgree']

if __name__=='__main__':main()
