#!/usr/bin/env python3
"""Audit terminal-only changes against a matching pre-cleanup run."""
import argparse,json,numpy as np,laspy
from pathlib import Path
from collections import Counter
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('baseline',type=Path);parser.add_argument('run',type=Path);parser.add_argument('output',type=Path);parser.add_argument('--baseline-seconds',type=float,required=True)
a=parser.parse_args()
if a.output.exists():raise FileExistsError(a.output)
run=a.run;old=a.baseline;m=json.loads((run/'manifest.json').read_text());r=m['terminalCleanup']
load=lambda name:np.load(run/(name+'.npy'),mmap_mode='r')
removed=load('terminal_removed')>0;owner=load('complete_instance');previous=load('terminal_previous_instance');classes=load('complete_class')
audit={ 'runId':m['runId'],'sourcePoints':len(owner),'removedPoints':int(removed.sum()),'changedInstances':len(r['decisions']),'runtimeSeconds':m['timings']['totalS'],'frozenBaselineSeconds':a.baseline_seconds,'baselineRatio':m['timings']['totalS']/a.baseline_seconds}
for label,report in [('before',r['acceptanceBefore']),('after',m['acceptance']),('originalThresholds',m['strictAcceptance'])]:
 audit[label]={'failedInstances':report['failedInstances'],'topologyConflicts':len(report['topologyFailures']),'observedInstances':report['observedInstances'],'criteria':dict(Counter(reason for i in report['instances'] for reason in i['reasons']))}
oldcls=np.load(old/'complete_class.npy',mmap_mode='r');oldowner=np.load(old/'complete_instance.npy',mmap_mode='r');oldseg=np.load(old/'complete_segment.npy',mmap_mode='r')
audit['restoredToSteel']=int(np.count_nonzero((oldcls!=3)&(classes==3)))
audit['removalMaskExact']=bool(np.array_equal(oldcls!=classes,removed));audit['ownershipUnchangedOutsideRemoval']=bool(np.array_equal(oldowner[~removed],owner[~removed]));audit['previousIdsExact']=bool(np.array_equal(previous[removed],oldowner[removed]) and np.array_equal(load('terminal_previous_segment')[removed],oldseg[removed]))
audit['hardBoundarySteel']=int(np.count_nonzero(((load('shared_table_mask')>0)|(load('shared_floating_noise')>0)|(load('internal_type')==5))&(classes==3)))
web={i['instanceId'] for i in m['acceptance']['instances'] if i.get('kind')=='web'};audit['webPointsRemoved']=int(np.count_nonzero(np.isin(previous[removed],list(web))))
curved={c['id'] for c in m['completeRebar']['clusters'] if c.get('category')=='curved-exterior'};audit['hookPointsRemoved']=int(np.count_nonzero(np.isin(load('complete_cluster')[removed],list(curved))))
audit['earlyArraysUnchanged']={name:bool(np.array_equal(load(name),np.load(old/(name+'.npy'),mmap_mode='r'))) for name in ('positions','geometry_class','projection_class','fused_class','refined_class','internal_type','internal_instance','internal_segment','review_state','review_reason','review_changed')}
attrs=['terminal_removed','terminal_reason','terminal_previous_instance','terminal_previous_segment','complete_class','complete_instance','complete_segment'];agree={name:True for name in attrs};offset=0
with laspy.open(run/'pointcloud-with-classes.las') as reader:
 for chunk in reader.chunk_iterator(500000):
  for name in attrs:agree[name]&=bool(np.array_equal(chunk[name],load(name)[offset:offset+len(chunk)]))
  assert np.array_equal(chunk.source_record_index,np.arange(offset,offset+len(chunk)));offset+=len(chunk)
audit['lasNpyEqual']=agree;audit['lasRecords']=offset
print(json.dumps(audit,indent=2));a.output.write_text(json.dumps(audit,indent=2))
assert audit['removalMaskExact'] and audit['ownershipUnchangedOutsideRemoval'] and audit['previousIdsExact']
assert not any(audit[k] for k in ('restoredToSteel','hardBoundarySteel','webPointsRemoved','hookPointsRemoved'))
assert all(audit['earlyArraysUnchanged'].values()) and all(agree.values())
