"""Length observation must include terminal faces and every accepted hook."""
import unittest
import numpy as np
from algorithms.rebar_control_curves import _observed_terminal_extent, _terminal_length_evidence, _finalize_terminal_length, _curve_query_stations
from test_rebar_control_curves import tube


def capped_tail(length=.06, side_end=.057):
    curve=np.array([[-.02,0.,0.],[0.,0.,0.],[.04,0.,0.]])
    centers=np.c_[np.linspace(.001,side_end,180),np.zeros((180,2))]
    points,normals=tube(centers,radius=.004)
    yy,zz=np.meshgrid(np.linspace(-.0035,.0035,11),np.linspace(-.0035,.0035,11))
    keep=yy**2+zz**2<.0035**2
    cap=np.c_[np.full(keep.sum(),length),yy[keep],zz[keep]]
    return curve,np.vstack((points,cap)),np.vstack((normals,np.tile([1.,0.,0.],(len(cap),1))))


class TerminalLengthTests(unittest.TestCase):
    def test_terminal_face_sets_length_when_side_normals_end_early(self):
        curve,points,normals=capped_tail()
        extent=_observed_terminal_extent(points,normals,curve,.004)
        self.assertIsNotNone(extent)
        self.assertAlmostEqual(extent,.06,delta=.0003)

    def test_confirmed_design_tail_gets_the_same_final_measurement(self):
        curve,points,normals=capped_tail()
        result={'curve':curve.copy(),'meta':{'tailLengthM':.04,
            'hookParameters':np.zeros(6),'bendRangeM':(0.,.02),'normal':np.array([0.,0.,1.])}}
        _finalize_terminal_length(result,points,normals,.004,.1)
        np.testing.assert_array_equal(result['curve'][:-1],curve[:-1])
        np.testing.assert_allclose(result['curve'][-1],[.06,0.,0.],atol=1e-10)
        self.assertEqual(result['meta']['tailExtentSource'],'terminal-face')
        self.assertTrue(result['meta']['tailLengthEvidence']['physicalEndObserved'])

    def test_observation_is_rigid_transform_invariant_with_tilted_cut(self):
        curve,points,normals=capped_tail()
        cap=normals[:,0]>.7
        points[cap,0]+=.25*points[cap,1]
        normals[cap]=np.array([1.,-.25,0.])/np.linalg.norm([1.,-.25,0.])
        rng=np.random.default_rng(1984)
        rotation=np.linalg.qr(rng.normal(size=(3,3)))[0]
        translation=np.array([10.,-3.,2.])
        measured=_terminal_length_evidence(points@rotation+translation,normals@rotation,
                                          curve@rotation+translation,.004)
        self.assertEqual(measured['source'],'terminal-face')
        self.assertAlmostEqual(measured['lengthM'],.06,delta=1e-8)

    def test_sparse_cap_or_offset_face_does_not_claim_physical_end(self):
        curve,points,normals=capped_tail()
        side=normals[:,0]<.7
        ids=np.r_[np.flatnonzero(side),np.flatnonzero(~side)[:3]]
        evidence=_terminal_length_evidence(points[ids],normals[ids],curve,.004)
        self.assertFalse(evidence['physicalEndObserved'])
        points[~side,1]+=.004
        evidence=_terminal_length_evidence(points,normals,curve,.004)
        self.assertFalse(evidence['physicalEndObserved'])

    def test_side_occlusion_does_not_shorten_supported_tail(self):
        curve,points,normals=capped_tail(length=.025,side_end=.022)
        side=normals[:,0]<.7
        result={'curve':curve.copy(),'meta':{'tailLengthM':.04,'hookParameters':np.zeros(6)}}
        _finalize_terminal_length(result,points[side],normals[side],.004,.1)
        np.testing.assert_array_equal(result['curve'],curve)
        self.assertNotIn('tailLengthEvidence',result['meta'])

    def test_length_can_exceed_shape_crop_but_search_cutoff_is_not_an_end(self):
        curve,points,normals=capped_tail(length=.14,side_end=.137)
        self.assertIsNone(_terminal_length_evidence(points,normals,curve,.004,.10))
        evidence=_terminal_length_evidence(points,normals,curve,.004,.20)
        self.assertEqual(evidence['source'],'terminal-face')
        self.assertAlmostEqual(evidence['lengthM'],.14,delta=1e-8)

    def test_disconnected_collinear_fragment_and_its_cap_are_not_the_tail(self):
        curve,points,normals=capped_tail()
        along=points[:,0]
        keep=(along<.026)|(along>.050)
        evidence=_terminal_length_evidence(points[keep],normals[keep],curve,.004)
        self.assertLess(evidence['lengthM'],.027)
        self.assertFalse(evidence['physicalEndObserved'])

    def test_spatial_query_covers_long_tail_between_vertices(self):
        from scipy.spatial import cKDTree
        curve=np.array([[0.,0.,0.],[.14,0.,0.]])
        points=np.c_[np.linspace(0.,.14,400),np.full(400,.004),np.zeros(400)]
        radius=.008
        queries=_curve_query_stations(curve,radius)
        found=np.unique(np.concatenate(cKDTree(points).query_ball_point(queries,r=radius)))
        self.assertEqual(len(found),len(points))

if __name__=='__main__':unittest.main()
