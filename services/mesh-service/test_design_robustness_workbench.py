"""End-to-end contract: half-visible 6 mm rods, source identity, and export fidelity."""
from pathlib import Path
import tempfile
import unittest
import laspy
import numpy as np
from pointcloud_step_pipeline import run_from_source
from rebar_design_prior import inventory_from_bars, SCHEMA, digest_file
from test_shared_scene import measured_scene
from test_internal_rebar_recovery import cylinder


def half_scene():
    base=measured_scene(0); old=len(np.arange(-.6,.601,.003))*20
    clouds=[base.positions[:-old]];truth=[np.zeros(len(clouds[0]),int)];bars=[]
    for i,(y,z,r) in enumerate([(-.08,.07,.003),(.08,.07,.003),(-.08,.12,.004),(.08,.12,.004)]):
        start,end=[-.6,y,z],[.6,y,z]
        p,n=cylinder(start,end,radius=r,along=401,around=24);p=p[n[:,2]>=-1e-6]
        clouds.append(p);truth.append(np.full(len(p),i+1));bars.append(dict(designBarId=str(i),points=[start,end],radiusM=r,coverage='complete'))
    return np.vstack(clouds),np.concatenate(truth),inventory_from_bars(bars,np.eye(4).ravel().tolist())


class WorkbenchRobustnessTests(unittest.TestCase):
    def test_half_visible_four_rods_source_permutation_threads_and_las_formats(self):
        points,truth,inventory=half_scene()
        with tempfile.TemporaryDirectory() as tmp:
            for fmt,workers,order in [(3,1,np.arange(len(points))),(6,2,np.random.default_rng(3).permutation(len(points)))]:
                with self.subTest(format=fmt,workers=workers):
                    root=Path(tmp)/str(fmt);root.mkdir();source=root/'scan.las'
                    las=laspy.LasData(laspy.LasHeader(point_format=fmt,version='1.4' if fmt==6 else '1.2'));las.header.scales=[.00001]*3;las.x,las.y,las.z=points[order].T;las.write(source)
                    digest=digest_file(source)
                    snapshot=dict(schema=SCHEMA,sourcePath=str(source),sourceSha256=digest,inventory=inventory,fingerprint='half-four',modelInfo={})
                    run=run_from_source(source,root/'runs',workers=workers,k=32,through_step=7,prior_mode='topology',robustness_mode='design-evidence',design_prior=snapshot,preview_limit=100)
                    self.assertTrue(run.manifest['acceptance']['geometryPassed'],run.manifest['acceptance'])
                    self.assertEqual(run.manifest['completeRebar']['instanceCount'],4)
                    ids=[]
                    for rod in range(1,5):
                        selected=truth[order]==rod;owners=np.unique(run.context.complete_instance[selected]);self.assertEqual(len(owners),1);self.assertGreater(owners[0],0);ids.append(owners[0])
                    self.assertEqual(len(set(ids)),4)
                    self.assertFalse(np.any((run.context.internal_type==5)&(run.context.complete_class==3)))
                    hard=(run.context.shared_table_mask>0)|(run.context.shared_floating_noise>0)
                    self.assertFalse(np.any(run.context.review_changed[hard]))
                    self.assertEqual(digest_file(source),digest)
                    output=laspy.read(run.directory/'pointcloud-with-classes.las')
                    for name in ('review_state','review_reason','review_changed','complete_instance'):
                        np.testing.assert_array_equal(output[name],np.load(run.directory/(name+'.npy')))
                    np.testing.assert_array_equal(output.source_record_index,np.arange(len(points)))
    def test_mode_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ('invalid','design-evidence'):
                with self.assertRaises(ValueError):run_from_source(Path(tmp)/'absent.las',Path(tmp)/'runs',robustness_mode=mode,through_step=6,workers=1)
if __name__=='__main__':unittest.main()
