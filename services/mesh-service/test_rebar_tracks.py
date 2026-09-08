"""Physical member continuity regressions, independent of seed fragmentation."""
import unittest
from types import SimpleNamespace
import numpy as np
from scipy.spatial import cKDTree
from algorithms.pointcloud_normals import PointCloudContext
from algorithms.internal_rebar import segment_internal_rebar, assign_cylinders, InternalRebarParameters, _fit_cylinder, _split_parallel_points
from algorithms.rebar_tracks import reconcile_tracks, remove_explained_fragments, grow_track_ends, diameter_priors, regularize_models
from test_internal_rebar import cylinder, model


def fragmented_context(reverse=False):
    samples=[cylinder((-.25,y,z),(.25,y,z),radius=.0025,along=201)
             for y,z in [(0,.02),(.012,.02),(0,.08)]]
    points,normals,axes=[np.vstack([s[i] for s in samples]) for i in range(3)]
    linearity=np.full(len(points),.95)
    size=len(samples[0][0]);linearity[:size][np.abs(points[:size,0])<.06]=.1
    context=PointCloudContext.build(points)
    context.normals=-normals if reverse else normals
    context.refined_class=np.full(len(points),3,np.uint8);context.refined_zone=np.ones(len(points),np.uint8)
    context.classification_cache={'grid':SimpleNamespace(points=points,origin=np.zeros(3),source_to_cell=np.arange(len(points)),tree=cKDTree(points)),
        'residual_ids':np.arange(len(points)), 'features':{'axis':axes,'linearity':linearity}}
    context.region_cache={'frame_axes':np.eye(2)}
    return context,size


class RebarTrackTests(unittest.TestCase):
    def test_no_residual_support_preserves_internal_points_as_pending(self):
        context,_=fragmented_context();cache=context.classification_cache
        cache['residual_ids']=np.empty(0,np.int32)
        cache['features']={'axis':np.empty((0,3)),'linearity':np.empty(0)}
        cache['grid'].tree=cKDTree(np.empty((0,3)))
        report=segment_internal_rebar(context)
        self.assertEqual(report['instanceCount'],0)
        np.testing.assert_array_equal(context.internal_type,4)
        np.testing.assert_array_equal(context.internal_instance,0)

    def test_bending_tracks_and_a_bridge_do_not_join_parallel_members(self):
        models=[]
        for y in (0.,.008):
            for a,b in zip(np.linspace(0,1,11)[:-1],np.linspace(0,1,11)[1:]):
                m=model((a,y+.006*np.sin(a*3),0),(b,y+.006*np.sin(b*3),0),radius=.004)
                m['group']=len(models);models.append(m)
        bridge=model((.41,.004+.006*np.sin(.41*3),0),(.49,.004+.006*np.sin(.49*3),0),radius=.004)
        bridge['group']=20;models.append(bridge)
        reconcile_tracks(models,InternalRebarParameters())
        self.assertEqual(len({m['group'] for m in models[:10]}),1)
        self.assertEqual(len({m['group'] for m in models[10:20]}),1)
        self.assertNotEqual(models[0]['group'],models[10]['group'])

    def test_short_independent_rod_survives_length_prior(self):
        members=[]
        for i,(start,end,y) in enumerate([(0,1,0),(.3,.55,.012),(.4,.44,.0004)]):
            m=model((start,y,0),(end,y,0),radius=.004);m['group']=i
            m['seedPoints']=cylinder((start,0 if i==2 else y,0),(end,0 if i==2 else y,0),radius=.004)[0]
            members.append(m)
        kept,report=remove_explained_fragments(members)
        self.assertEqual({m['group'] for m in kept},{0,1})
        self.assertEqual(report['explainedFragmentTracks'],1)

    def test_grow_endpoint_stops_at_observed_gap(self):
        m=model((0,0,0),(1,0,0),radius=.004);m['group']=0
        points=np.vstack([cylinder((a,0,0),(b,0,0),radius=.004,along=10)[0]
                          for a,b in [(0,.004),(1,1.004),(1.015,1.019)]])
        old=m['high'];report,tree=grow_track_ends([m],points,InternalRebarParameters(),2)
        self.assertGreater(m['high'],old)
        self.assertLess(m['high']-old,.007)
        self.assertEqual(tree.n,len(points))

    def test_local_tangent_resolves_crossing_and_normal_sign_is_irrelevant(self):
        models=[model((-.1,0,0),(.1,0,0),radius=.004),model((0,-.1,0),(0,.1,0),radius=.004)]
        points=np.array([[0.,0.,.004]]);normal=np.array([[0.,0.,1.]]);tangent=np.array([[0.,1.,0.]])
        labels,_,_=assign_cylinders(points,normal,models,tangents=tangent,linearity=np.ones(1))
        reversed_labels,_,_=assign_cylinders(points,-normal,models,tangents=-tangent,linearity=np.ones(1),workers=2)
        np.testing.assert_array_equal(labels,[2]);np.testing.assert_array_equal(labels,reversed_labels)

    def test_ifc_prior_does_not_force_incompatible_layer_diameters(self):
        models=[]
        for kind,radius in [(1,.004),(2,.005),(3,.003)]:
            m=model((0,0,0),(1,0,0),radius=radius,kind=kind);models.append(m)
        values,report=diameter_priors(models,{'diametersM':[.008]})
        self.assertIn(.008,values[1]);self.assertIn(.01,values[2]);self.assertIn(.006,values[3])
        self.assertEqual(report['1']['source'],'ifc')

    def test_valid_minority_diameter_in_one_layer_is_not_discarded(self):
        params=InternalRebarParameters();models=[]
        for i,radius in enumerate([.004,.004,.004,.006]):
            points=cylinder((0,i*.03,0),(1,i*.03,0),radius=radius,along=100)[0]
            m=_fit_cylinder(points,1,params);m['group']=i;models.append(m)
        priors,_=diameter_priors(models)
        result=regularize_models(models,priors,params,2,_fit_cylinder,_split_parallel_points)
        self.assertEqual(len(result),4)
        np.testing.assert_allclose([m['radius'] for m in result],[.004,.004,.004,.006],atol=.0001)

    def test_one_continuous_bar_with_weak_middle_has_one_instance(self):
        context,size=fragmented_context()
        report=segment_internal_rebar(context,workers=1)
        ids=[]
        for i in range(3):
            member=np.unique(context.internal_instance[i*size:(i+1)*size])
            self.assertEqual(len(member),1,f'physical member {i} split into {member}')
            self.assertGreater(member[0],0)
            ids.append(member[0])
        self.assertEqual(len(set(ids)),3,'nearby parallel members must remain distinct')
        self.assertEqual(report['instanceCount'],3)


if __name__=='__main__':unittest.main()
