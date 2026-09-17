import copy
import unittest
import numpy as np
from algorithms.rebar_control_curves import _fit_piece, _join_model, _terminal_model, _project, _CurveResidual


def tube(curve, radius=.003, angles=None):
    if angles is None: angles=np.linspace(-1.2,1.2,24)
    tangent=np.gradient(curve,axis=0);tangent/=np.linalg.norm(tangent,axis=1)[:,None]
    normal=np.array([0.,0.,1.]);side=np.cross(normal,tangent);side/=np.linalg.norm(side,axis=1)[:,None]
    radial=np.cos(angles)[None,:,None]*side[:,None,:]+np.sin(angles)[None,:,None]*normal
    return (curve[:,None,:]+radius*radial).reshape(-1,3),radial.reshape(-1,3)


def join_fixture():
    rows={'a':{'centerlineM':[[-.1,0,0],[0,0,0]],'designLengthM':.1},
          'b':{'centerlineM':[[.02,.02,0],[.02,.12,0]],'designLengthM':.1}}
    piece={'kind':'join','radiusM':.003,'primitives':[{'kind':'arc','radiusM':.015}],
           'anchors':[{'designUnitId':'a','side':'end'},{'designUnitId':'b','side':'start'}]}
    return piece,rows


def terminal_fixture():
    rows={'a':{'centerlineM':[[0,0,0],[-1.,0,0]],'designLengthM':1.,'axisEvidenceRangeM':[.08,.95]}}
    phi=np.linspace(0,2.35,31)
    curve=np.column_stack((.02*np.sin(phi),.02*(1-np.cos(phi)),np.zeros(len(phi))))
    curve=np.vstack((curve,curve[-1]+.04*np.array([np.cos(phi[-1]),np.sin(phi[-1]),0])))
    piece={'kind':'terminal','radiusM':.003,'designLengthM':.02*2.35+.04,'centerlineM':curve.tolist(),
           'primitives':[{'kind':'arc','radiusM':.02,'sweepRad':2.35}],
           'anchors':[{'designUnitId':'a','side':'start'}],
           '_designAnchors':[np.zeros(3)],'_designTangents':[np.array([1.,0,0])]}
    return piece,rows


class ParametricCurveTests(unittest.TestCase):
    def test_cached_distance_jacobian_matches_independent_parameter_differences(self):
        rng=np.random.default_rng(37)
        points=rng.uniform(-.025,.07,(80,3))
        normals=rng.normal(size=points.shape)
        normals/=np.linalg.norm(normals,axis=1)[:,None]
        normals[::4]=0. # Also exercise ordinary tube distance without normals.
        def model(p):
            return np.array([[-.02,0,0],[p[0],.02,0],[p[0],.02,0],[.06,.05,p[1]]])
        p=np.array([.01,.008]);weights=rng.uniform(.5,1.3,len(points))
        residual=_CurveResidual(model,points,normals,weights,.003,np.zeros(2),np.full(2,.02),.00036)
        for scale in [.002,.00036]:
            actual=residual.jac(p,scale)
            numeric=[]
            for j in range(2):
                step=np.zeros(2);step[j]=1e-7
                numeric.append((residual.fun(p+step,scale)-residual.fun(p-step,scale))/2e-7)
            np.testing.assert_allclose(actual,np.array(numeric).T,atol=2e-5,rtol=.003)

    def test_join_radius_and_g1_from_body_anchors(self):
        piece,rows=join_fixture();model=_join_model(piece,rows)[0]
        truth,meta=model([.019])
        phi=np.linspace(0,np.pi/2,97)
        true_curve=np.c_[.001+.019*np.sin(phi),.019*(1-np.cos(phi)),np.zeros(len(phi))]
        p,n=tube(true_curve)
        result,reason=_fit_piece(p,n,piece,rows)
        self.assertIsNotNone(result,reason)
        self.assertLess(abs(result['meta']['bendRadiusM']-.019),.00015)
        self.assertLess(np.max(np.linalg.norm(result['curve']-truth,axis=1)),.0002)
        self.assertAlmostEqual(result['curve'][0,1],0.)
        self.assertAlmostEqual(result['curve'][-1,0],.02)

    def test_missing_and_planar_evidence_stays_pending(self):
        piece,rows=join_fixture();curve,_=_join_model(piece,rows)[0]([.019])
        p,n=tube(curve,angles=np.linspace(-.01,.01,24))
        result,_=_fit_piece(p,n,piece,rows)
        self.assertIsNone(result)
        result,_=_fit_piece(p[:20],n[:20],piece,rows)
        self.assertIsNone(result)

    def test_terminal_parameters_do_not_move_body_or_extend_tail(self):
        piece,rows=terminal_fixture();before=copy.deepcopy(rows)
        model=_terminal_model(piece,rows)[0]
        params=np.array([.006,.002,-.001,.08,.022,2.4])
        curve,meta=model(params)
        dense=np.vstack([a+(b-a)*np.linspace(0,1,8,endpoint=False)[:,None] for a,b in zip(curve[:-1],curve[1:])])
        p,n=tube(dense)
        result,reason=_fit_piece(p,n,piece,rows)
        self.assertIsNotNone(result,reason)
        self.assertLess(np.max(np.linalg.norm(result['curve']-curve,axis=1)),.0015)
        self.assertEqual(rows,before)
        self.assertAlmostEqual(result['meta']['tailLengthM'],.04)

    def test_open_endpoint_is_not_a_spherical_cap(self):
        curve=np.array([[0.,0,0],[1.,0,0]])
        p=np.array([[-.003,0,0],[1.003,0,0],[.5,.003,0]])
        *_,side=_project(p,curve)
        np.testing.assert_array_equal(side,[False,False,True])

    def test_flat_join_with_adjacent_body_points(self):
        # Independent arc/line equations: straight observations outside the
        # bend must not inflate its tangency extent or round off the flat part.
        piece,rows=join_fixture()
        radius=.010;extent=.025;half=np.pi/4
        phi=np.linspace(0,half,50)
        first=np.c_[-.005+radius*np.sin(phi),radius*(1-np.cos(phi)),np.zeros(len(phi))]
        direction=np.array([np.cos(half),np.sin(half),0.])
        across=np.array([-np.sin(half),np.cos(half),0.])
        bridge=2*(extent-radius)*np.sin(half)
        straight=first[-1]+np.linspace(0,bridge,50)[:,None]*direction
        second=straight[-1]+radius*np.sin(phi)[:,None]*direction+radius*(1-np.cos(phi))[:,None]*across
        left=np.c_[np.linspace(-.09,-.005,100),np.zeros(100),np.zeros(100)]
        right=np.c_[np.full(100,.02),np.linspace(.025,.11,100),np.zeros(100)]
        truth=np.vstack((left[:-1],first[:-1],straight[:-1],second[:-1],right))
        p,n=tube(truth)
        result,reason=_fit_piece(p,n,piece,rows)
        self.assertIsNotNone(result,reason)
        self.assertLess(abs(result['meta']['bendRadiusM']-radius),.0006)
        self.assertLess(abs(result['meta']['bridgeLengthM']-bridge),.001)
        self.assertLess(abs(result['meta']['tangentRadiusM']-extent),.0006)

    def test_multiple_design_bends_are_not_silently_reduced_to_one(self):
        piece,rows=terminal_fixture()
        piece['primitives']*=2
        self.assertIsNone(_terminal_model(piece,rows))
        piece,rows=join_fixture()
        piece['primitives']*=2
        self.assertIsNone(_join_model(piece,rows))


if __name__=='__main__':unittest.main()
