import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.interpolate import BSpline
from algorithms.bim_bent_bars import recover_bent_bars, fixture_envelope, _inside_body


def data(offset=0., radius=.004, bend=.065, seed=4):
    rng=np.random.default_rng(seed)
    x=np.repeat(np.linspace(0,1.2,500),32);angle=np.tile(np.linspace(0,2*np.pi,32,endpoint=False),500)
    y=offset-bend*np.sin(np.pi*x/1.2)**2
    derivative=-bend*np.pi/1.2*np.sin(2*np.pi*x/1.2)
    v=np.c_[-derivative,np.ones(len(x)),np.zeros(len(x))];v/=np.linalg.norm(v,axis=1)[:,None]
    normal=v*np.cos(angle)[:,None]+np.array([0,0,1])*np.sin(angle)[:,None]
    p=np.c_[x,y,np.zeros(len(x))]+radius*normal+rng.normal(0,.00015,(len(x),3))
    return p,normal


def inventory(ys):
    return {'units':[{'designUnitId':str(i),'designBarId':str(i),'kind':'straight',
        'startM':[0,y,0],'endM':[1.2,y,0],'lengthM':1.2,'diameterM':.008,'layerId':1} for i,y in enumerate(ys)],'bars':[]}


def run(p,n,inv,excluded=None,scene=None):
    rows=[{'id':i+1,'designUnitId':u['designUnitId'],'designBarId':u['designBarId'],'pointCount':0,'status':'missing','reason':'test-source'} for i,u in enumerate(inv['units'])]
    status=np.full(len(p),2,np.uint8);owner=np.zeros(len(p),np.uint32)
    report=recover_bent_bars(p,n,inv,rows,status,owner,scene or {'objects':[]},{'objects':[]},excluded=excluded)
    return report,rows,status,owner


class BentEvidenceTests(unittest.TestCase):
    def test_partial_owner_does_not_disable_completion(self):
        p,n=data();inv=inventory([0]);owner=np.zeros(len(p),np.uint32);owner[7000]=1
        status=np.full(len(p),2,np.uint8);status[7000]=1
        rows=[dict(id=1,designUnitId='0',designBarId='0',pointCount=1,status='fitted')]
        recover_bent_bars(p,n,inv,rows,status,owner,{'objects':[]},{'objects':[]})
        self.assertGreater(np.mean(owner==1),.9)
        self.assertEqual(owner[7000],1)

    def test_both_visible_sides_survive_a_long_occlusion(self):
        p,n=data();keep=~((p[:,0]>.48)&(p[:,0]<.61));p,n=p[keep],n[keep]
        _,rows,_,owner=run(p,n,inventory([0]))
        self.assertGreater(np.mean(owner[p[:,0]<.47]==1),.9)
        self.assertGreater(np.mean(owner[p[:,0]>.62]==1),.9)
        self.assertTrue(all(not (a<.54<b) for a,b in rows[0]['observedSupportIntervalsM']))

    def test_bowed_tube_recovers_source_without_changing_coordinates(self):
        p,n=data();before=p.copy();report,rows,_,owner=run(p,n,inventory([0]))
        self.assertGreater(np.mean(owner==1),.65)
        self.assertGreater(rows[0]['observedLengthM'],.8)
        self.assertGreater(report['objects'][0]['maxTransverseDisplacementM'],.05)
        np.testing.assert_array_equal(p,before)
        fit=rows[0]['flexibleRegistration']
        spline=BSpline(fit['knots'],np.asarray(fit['transverseCoefficientsM']),fit['degree'])
        station=np.linspace(0,1,97);bounded=np.clip(station,*fit['basisSupportClamp'])
        offset=spline(bounded)+(station-bounded)[:,None]*spline(bounded,nu=1)
        reconstructed=(np.asarray(fit['designStartM'])+station[:,None]*fit['designAxialLengthM']*np.asarray(fit['tangent'])
            +offset[:,:1]*fit['frameU']+offset[:,1:]*fit['frameV'])
        np.testing.assert_allclose(reconstructed,fit['fittedIfcAxisM'],atol=1e-10)
        self.assertEqual(fit['nominalRadiusM'],.004)
        self.assertFalse(fit['radiusChanged'])
        self.assertGreater(fit['heldOutSurfaceSupport'],.9)

    def test_nearer_parallel_identity_wins_even_if_other_is_missing(self):
        p,n=data(offset=.045,bend=.003)
        report,rows,_,owner=run(p,n,inventory([0,.045]))
        self.assertFalse(np.any(owner==1));self.assertGreater(np.mean(owner==2),.6)

    def test_planar_strip_and_larger_fixture_cylinder_are_rejected(self):
        rng=np.random.default_rng(9);p=np.c_[rng.uniform(0,1.2,15000),rng.uniform(-.03,.03,15000),rng.normal(0,.0001,15000)];n=np.tile([0,0,1.],(len(p),1))
        self.assertFalse(run(p,n,inventory([0]))[3].any())
        p,n=data(radius=.016,bend=0)
        self.assertFalse(run(p,n,inventory([0]))[3].any())

    def test_exclusion_and_occlusion_are_not_filled(self):
        p,n=data();keep=~((p[:,0]>.50)&(p[:,0]<.57));p,n=p[keep],n[keep]
        excluded=p[:,0]<.08
        _,rows,_,owner=run(p,n,inventory([0]),excluded)
        self.assertFalse(owner[excluded].any());self.assertGreater(owner.sum(),1000)
        self.assertGreater(len(rows[0]['observedSupportIntervalsM']),1)

    def test_arbitrary_rigid_frame_keeps_observed_support(self):
        p,n=data();inv=inventory([0]);rot=Rotation.from_rotvec([.4,-.6,.2]).as_matrix();shift=np.array([4.,-2.,1.])
        for unit in inv['units']:
            for key in ('startM','endM'):unit[key]=(rot@unit[key]+shift).tolist()
        owner=run(p@rot.T+shift,n@rot.T,inv)[3]
        self.assertGreater(np.mean(owner==1),.65)

    def test_visible_body_is_partial_evidence_with_occluded_ends(self):
        p,n=data();keep=(p[:,0]>.38)&(p[:,0]<.83);p,n=p[keep],n[keep]
        _,rows,_,owner=run(p,n,inventory([0]))
        self.assertGreater(np.mean(owner==1),.6)
        self.assertLess(rows[0]['observedLengthM'],.5)

    def test_fixture_body_boundary_preserves_axial_protrusions(self):
        envelope={'planes':[{'normal':[1,0,0],'offsetM':.2}, {'normal':[-1,0,0],'offsetM':-1.},
                            {'normal':[0,1,0],'offsetM':-.2}, {'normal':[0,-1,0],'offsetM':-.2}]}
        self.assertTrue(_inside_body(np.array([.5,0,0]),np.array([1,0,0]),envelope))
        self.assertFalse(_inside_body(np.array([.5,.25,0]),np.array([1,0,0]),envelope))
        self.assertTrue(_inside_body(np.array([1.1,.25,0]),np.array([1,0,0]),envelope))

    def test_equal_neighbour_identity_stays_unassigned(self):
        p,n=data(offset=.0225,bend=0)
        self.assertFalse(run(p,n,inventory([0,.045]))[3].any())


if __name__=='__main__':unittest.main()
