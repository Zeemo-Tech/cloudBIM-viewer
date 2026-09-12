"""The fragment veto precedes attachment and survives design reassignment/export."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import laspy
import numpy as np
from pointcloud_step_pipeline import run_from_source
from rebar_design_prior import SCHEMA, digest_file
from test_design_robustness_workbench import half_scene
from algorithms import design_guided_instances as guided

class FragmentIntegrationTests(unittest.TestCase):
    def test_premerge_veto_is_applied_before_attachment_and_never_restored(self):
        points,truth,inventory=half_scene();cut=int(np.flatnonzero(truth==1)[5]);invocations=[]
        def review(context,groups,design,**kw):
            labels=np.zeros(len(points),np.uint32)
            for i,group in enumerate(groups,1):labels[group['rows']]=i
            mask=np.zeros(len(points),bool)
            if not invocations:
                self.assertGreater(labels[cut],0);mask[cut]=True
            else:
                self.assertEqual(context.complete_class[cut],4)
                self.assertFalse(any(cut in group['rows'] for group in groups))
            invocations.append(len(groups))
            return mask,labels,dict(removedPointCount=int(mask.sum()),fragments=[],decisions=[])
        real_attach=guided._early_exterior_support
        def attach(context,jobs,original,out,*args,**kwargs):
            self.assertEqual(out['complete_class'][cut],4);self.assertEqual(out['complete_instance'][cut],0)
            self.assertFalse(any(cut in rows for _,rows,_ in jobs))
            return real_attach(context,jobs,original,out,*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.las';las=laspy.LasData(laspy.LasHeader(point_format=3));las.header.scales=[.00001]*3
            las.x,las.y,las.z=points.T;las.write(source)
            snapshot=dict(schema=SCHEMA,sourcePath=str(source),sourceSha256=digest_file(source),inventory=inventory,fingerprint='fragment-veto',modelInfo={})
            with patch('algorithms.rebar_fragment_terminals.review_fragment_terminals',side_effect=review),patch.object(guided,'_early_exterior_support',side_effect=attach):
                run=run_from_source(source,root/'runs',workers=1,through_step=7,prior_mode='topology',robustness_mode='design-evidence',terminal_mode='fixture-peel',design_prior=snapshot,preview_limit=100)
            self.assertEqual(len(invocations),2);self.assertEqual(run.context.refined_class[cut],3)
            self.assertNotEqual(run.context.internal_type[cut],5);self.assertEqual(run.context.complete_class[cut],4)
            self.assertEqual(run.context.complete_instance[cut],0);self.assertEqual(run.context.terminal_reason[cut],2)
            self.assertEqual(run.context.terminal_origin[cut],1);self.assertGreater(run.context.terminal_previous_instance[cut],0)
            self.assertEqual(run.manifest['terminalCleanup']['premergeRemovedPointCount'],1)
            saved=laspy.read(run.directory/'pointcloud-with-classes.las')
            for name in ('terminal_removed','terminal_fragment','terminal_origin','terminal_previous_instance'):
                np.testing.assert_array_equal(saved[name],np.load(run.directory/(name+'.npy')))

if __name__=='__main__':unittest.main()
