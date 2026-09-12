import unittest
from types import SimpleNamespace
import numpy as np
from algorithms.design_review import prepare_review,finalize_review
from algorithms.design_evidence_contract import ATTRIBUTES
from test_design_acceptance import tube

class ReviewAssignmentTests(unittest.TestCase):
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
