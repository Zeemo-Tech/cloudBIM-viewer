import unittest
from unittest.mock import patch
import numpy as np
from scipy.spatial.transform import Rotation
from algorithms.bim_scene_classification import _surface_samples, classify_scene_surfaces
from algorithms.bim_short_connectors import fit_tube_cluster
from algorithms.bim_guided_classification import structural_inventory, fit_bim_guided_scene
from algorithms.rebar_control_net import fit_control_net


def tube(radius=.004,length=.09,tilt=.15):
    rng=np.random.default_rng(2);theta=rng.uniform(-np.pi,np.pi,1000);z=rng.uniform(-length/2,length/2,1000)
    p=np.c_[radius*np.cos(theta),radius*np.sin(theta),z]
    p=p@Rotation.from_rotvec([0,tilt,0]).as_matrix().T+[.032,-.015,0]
    return p+rng.normal(0,.00025,p.shape)


class EvidenceTests(unittest.TestCase):
    def test_final_curves_keep_global_ids_for_unmatched_last_unit(self):
        # The updated parent owns unit 2; its last unit 3 has no source rows.
        # Passing only parent rows to the curve solver used to size counts to
        # max(owner)+1 and crash when updating the unmatched global unit 3.
        inv={'bars':[dict(designBarId=b,ifcGlobalId=b) for b in ('other','parent')],
             'units':[dict(designBarId=b,designUnitId=str(i)) for i,b in enumerate(('other','parent','parent'),1)]}
        rows=[dict(id=i,designUnitId=str(i),designBarId=b,pointCount=int(i==2),status='fitted' if i==2 else 'missing')
              for i,b in enumerate(('other','parent','parent'),1)]
        report={'instances':rows,'counts':{},'policy':{},'curvedPieces':[],'curveSummary':{}}
        arrays={'control_status':np.array([1],np.uint8),'control_instance':np.array([2],np.uint32)}
        scene_report={'objects':[],'policy':{}}
        prefix='algorithms.bim_guided_classification.'
        with patch(prefix+'classify_scene_surfaces',return_value=(scene_report,np.zeros(1,np.uint8),np.zeros(1,np.uint32))), \
             patch(prefix+'fit_control_net',return_value=(report,arrays)), \
             patch(prefix+'recover_connectors',return_value={'objects':[{'designBarId':'parent','status':'observed-partial-connector'}]}), \
             patch(prefix+'recover_bent_bars',return_value={'objects':[]}), \
             patch(prefix+'complete_end_faces',return_value=0), \
             patch(prefix+'recover_scene_alternatives',side_effect=lambda p,n,t,s,i,r,l,o,**kw:(r,l,o)), \
             patch(prefix+'extend_embedded_axes',return_value={}):
            out,attrs,_,_=fit_bim_guided_scene(np.zeros((1,3)),np.array([[0.,0.,1.]]),np.zeros(1,bool),inv,{'objects':[]})
        self.assertEqual(out['instances'][-1]['pointCount'],0)
        self.assertEqual(attrs['control_instance'][0],2)
        self.assertEqual(out['curveSummary']['designPieces'],0)

    def test_offset_tilt_diameter_measured(self):
        fit=fit_tube_cluster(tube(),.003,[0,0,1],np.zeros(3))
        self.assertIsNotNone(fit);self.assertAlmostEqual(fit['radius'],.004,delta=.0003)
        self.assertAlmostEqual(fit['tiltDeg'],np.rad2deg(.15),delta=1)

    def test_planar_strip_rejected(self):
        rng=np.random.default_rng(3)
        p=np.c_[rng.uniform(-.002,.002,1000),rng.normal(0,.00003,1000),rng.uniform(-.045,.045,1000)]
        self.assertIsNone(fit_tube_cluster(p,.003,[0,0,1],np.zeros(3)))

    def test_large_fixture_cylinder_rejected(self):
        self.assertIsNone(fit_tube_cluster(tube(radius=.016,tilt=0),.003,[0,0,1],np.zeros(3)))

    def test_disconnected_endpoint_patches_rejected(self):
        p=tube();p=p[abs(p[:,2])>.032]
        self.assertIsNone(fit_tube_cluster(p,.003,[0,0,1],np.zeros(3)))

    def test_moved_plate_preserves_adjacent_rebar(self):
        v=np.array([[-.15,-.12,0],[.15,-.12,0],[.15,.12,0],[-.15,.12,0]],float);f=np.array([[0,1,2],[0,2,3]])
        p,n=_surface_samples(v,f,12000);rot=Rotation.from_rotvec([.08,0,0]).as_matrix();shift=np.array([.008,.004,.018])
        p=p@rot.T+shift;n=n@rot.T
        th=np.tile(np.linspace(0,2*np.pi,36,endpoint=False),100);x=np.repeat(np.linspace(-.1,.1,100),36)
        q=np.c_[x,.004*np.cos(th),.004+.004*np.sin(th)]@rot.T+shift
        qn=np.c_[np.zeros(len(th)),np.cos(th),np.sin(th)]@rot.T
        points=np.vstack((p,q));normals=np.vstack((n,qn));before=points.copy()
        axis=np.array([[-.1,0,.004],[.1,0,.004]])@rot.T+shift
        inv={'bars':[{'unitIds':['u'],'radiusM':.004,'points':axis.tolist()}]}
        scene={'objects':[{'globalId':'plate','name':'plate','ifcType':'IfcPlate','category':'fixture','vertices':v,'faces':f}], 'diagnostics':{}}
        report,labels,_=classify_scene_surfaces(points,normals,np.zeros(len(points),bool),scene,inv)
        self.assertGreater(np.mean(labels[:len(p)]==2),.75);self.assertLess(np.mean(labels[len(p):]==2),.02)
        np.testing.assert_array_equal(points,before);self.assertGreater(report['objects'][0]['displacementM'],.01)

    def test_semantics_count_structural_parents(self):
        inv={'bars':[{'designBarId':'s','ifcGlobalId':'steel','unitIds':['u']},{'designBarId':'p','ifcGlobalId':'plate','unitIds':[]}], 'units':[{'designBarId':'s','designUnitId':'u'}],'coverage':{},'relations':[]}
        result=structural_inventory(inv,{'objects':[{'globalId':'plate','category':'fixture'}]})
        self.assertEqual(len(result['bars']),1);self.assertEqual(len(inv['bars']),2)

    def test_excluded_rows_cannot_acquire_steel(self):
        p=tube();_,a=fit_control_net(p,np.zeros(len(p),bool),{'bars':[],'units':[]},excluded_mask=np.ones(len(p),bool))
        self.assertTrue(np.all(a['control_instance']==0));self.assertTrue(np.all(a['control_status']==4))

if __name__=='__main__':unittest.main()
