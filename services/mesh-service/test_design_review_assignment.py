import unittest
from unittest.mock import patch
from dataclasses import asdict
from types import SimpleNamespace
import numpy as np
from algorithms.design_review import prepare_review,finalize_review
from algorithms.design_evidence_contract import ATTRIBUTES, RobustnessPolicy, Candidate, Evidence, State, Reason
from test_design_acceptance import tube

class ReviewAssignmentTests(unittest.TestCase):
    def assignment_case(self, *, incumbent_offset=0., missing_segment=False, provisional=False, outliers=False, design_incumbent=False):
        p=tube(length=.12);n=len(p)
        if outliers:p[:10,1]+=.005
        owners=np.full(n,7,np.uint32);owners[-100:]=0
        model=dict(center=np.array([0.,0.,.04]),axis=np.array([1.,0.,0.]),radius=.003,low=-.06,high=.06,type=1)
        if outliers:model['center'][1]=.005
        candidate=Candidate('rod',np.arange(n),model,dict(span_m=.12),evidence=Evidence(
            State.PROVISIONAL if provisional else State.CONFIRMED,Reason.CYLINDER_SUPPORT,score=.85))
        ctx=SimpleNamespace(positions=p,complete_class=np.full(n,3),complete_instance=owners,
            complete_segment=np.where(owners>0,1,0).astype(np.uint32),complete_confidence=np.zeros(n),
            complete_cluster=np.zeros(n,np.uint32),internal_type=np.ones(n),design_candidates=[candidate],
            design_review_report=dict(policy=asdict(RobustnessPolicy())),
            **{name:np.zeros(n,dtype) for name,dtype in ATTRIBUTES.items()})
        inventory=dict(units=[dict(designUnitId='rod',designBarId='bar',startM=[-.06,0,.04],endM=[.06,0,.04],direction=[1.,0,0],lengthM=.12,diameterM=.006,kind='straight')],relations=[])
        segment=dict(id=1,instanceId=7,type=1,startM=[-.06,incumbent_offset,.04],endM=[.06,incumbent_offset,.04],radiusM=.003)
        if outliers:segment['fitMedianErrorM']=.0002
        if design_incumbent:segment.update(fitMedianErrorM=.0002,candidateSource='design-fixed-radius')
        report=dict(instances=[dict(id=7,type=1)],segments=[] if missing_segment else [segment])
        with patch('algorithms.design_guided_instances._candidate_sets',return_value=([[(0.,0)]],None)), patch('algorithms.design_guided_instances._assign_units',return_value=({0:0},[])):
            finalize_review(ctx,report,inventory)
        return ctx,report

    def test_locally_supported_points_keep_owner_even_if_whole_instance_fails(self):
        ctx,report=self.assignment_case()
        self.assertTrue(np.all(ctx.complete_instance[:-100]==7))
        self.assertTrue(np.all(ctx.complete_segment[:-100]==1))
        self.assertTrue(np.all(ctx.complete_instance[-100:]==8))
        comparison=ctx.design_review_report['assignment']['ownershipEvidence']['comparisons'][0]
        self.assertEqual(comparison['preservedPointCount'],len(ctx.positions)-100)
        self.assertEqual(comparison['improvedTransferPointCount'],0)

    def test_independently_supported_candidate_can_replace_wrong_local_geometry(self):
        ctx,report=self.assignment_case(incumbent_offset=.02)
        self.assertTrue(np.all(ctx.complete_instance==8))
        self.assertEqual(len(report['instances']),1)

    def test_reliable_fragment_is_not_split_by_a_few_surface_outliers(self):
        ctx,_=self.assignment_case(outliers=True)
        self.assertTrue(np.all(ctx.complete_instance[:-100]==7))
        comparison=ctx.design_review_report['assignment']['ownershipEvidence']['comparisons'][0]
        self.assertEqual(comparison['supportedSegmentPointCount'],len(ctx.positions)-100)

    def test_missing_incumbent_geometry_is_not_permission_to_steal(self):
        ctx,_=self.assignment_case(missing_segment=True)
        self.assertTrue(np.all(ctx.complete_instance[:-100]==7))

    def test_provisional_candidate_cannot_displace_existing_owner(self):
        ctx,_=self.assignment_case(incumbent_offset=.02,provisional=True)
        self.assertTrue(np.all(ctx.complete_instance[:-100]==7))

    def test_design_candidate_fit_cannot_become_its_own_protection(self):
        ctx,_=self.assignment_case(incumbent_offset=.02,design_incumbent=True)
        self.assertTrue(np.all(ctx.complete_instance==8))

    def test_weak_half_surface_reclassified_but_hard_rows_remain_excluded(self):
        p=tube(length=.12,half=True);n=len(p);normals=p-[0,0,.04];normals[:,0]=0;normals/=np.linalg.norm(normals,axis=1)[:,None]
        hard=np.zeros(n);hard[::7]=1
        ctx=SimpleNamespace(positions=p,normals=normals,normal_valid=np.ones(n),shared_table_mask=np.zeros(n),shared_floating_noise=hard,refined_class=np.full(n,2),geometry_class=np.full(n,2),projection_class=np.full(n,2))
        inventory=dict(units=[dict(designUnitId='rod',designBarId='bar',startM=[-.06,0,.04],endM=[.06,0,.04],direction=[1.,0,0],lengthM=.12,diameterM=.006,kind='straight')],relations=[])
        prepare_review(ctx,inventory,output={name:np.zeros(n,dtype) for name,dtype in ATTRIBUTES.items()})
        self.assertTrue(np.all(ctx.refined_class[hard==0]==3))
        self.assertTrue(np.all(ctx.refined_class[hard>0]==2))
        self.assertTrue(np.all(ctx.geometry_class==2))
        self.assertTrue(np.all(ctx.projection_class==2))

    def test_short_translation_is_exempt_at_matching_not_only_proposal(self):
        p=tube(length=.12,center=(1.,1.,.04));n=len(p);normals=p-[1.,1.,.04];normals[:,0]=0;normals/=np.linalg.norm(normals,axis=1)[:,None]
        ctx=SimpleNamespace(positions=p,normals=normals,normal_valid=np.ones(n),shared_table_mask=np.zeros(n),shared_floating_noise=np.zeros(n),refined_class=np.full(n,3),refined_zone=np.ones(n),geometry_class=np.full(n,3),projection_class=np.full(n,3),shared_layer=np.ones(n),complete_class=np.full(n,3),complete_instance=np.zeros(n,np.uint32),complete_segment=np.zeros(n,np.uint32),complete_confidence=np.zeros(n),complete_cluster=np.zeros(n,np.uint32),internal_type=np.full(n,4))
        inventory=dict(units=[dict(designUnitId='short',designBarId='bar',startM=[-.06,0,.04],endM=[.06,0,.04],direction=[1.,0,0],lengthM=.12,diameterM=.006,kind='short',layerId=1)],relations=[])
        prepare_review(ctx,inventory,output={name:np.zeros(n,dtype) for name,dtype in ATTRIBUTES.items()})
        report=dict(instances=[],segments=[])
        acceptance=finalize_review(ctx,report,inventory)
        self.assertTrue(acceptance['geometryPassed'],acceptance)
        self.assertEqual(report['instanceCount'],1)
        self.assertTrue(np.all(ctx.complete_instance>0))
if __name__=='__main__':unittest.main()
