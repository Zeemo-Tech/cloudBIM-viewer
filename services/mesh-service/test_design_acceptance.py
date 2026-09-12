import unittest
import numpy as np
from algorithms.design_acceptance import observed_geometry, evaluate_acceptance

def tube(length=.3, radius=.003, center=(0,0,.04), half=False):
    t,a=np.meshgrid(np.linspace(-length/2,length/2,120),np.linspace(0,np.pi if half else 2*np.pi,40,endpoint=False))
    return np.column_stack((t.ravel(),radius*np.cos(a.ravel()),radius*np.sin(a.ravel())))+center

def inventory(kind='straight'):
    return {'units':[dict(designUnitId='a',startM=[-.15,0,.04],endM=[.15,0,.04],lengthM=.3,diameterM=.006,kind=kind)],'relations':[]}

class AcceptanceTests(unittest.TestCase):
    def check_cloud(self,p,kind='straight',**kw):
        return evaluate_acceptance(p,np.ones(len(p),int),np.full(len(p),3),[dict(id=1,designUnitId='a',lengthM=.3)],inventory(kind),**kw)
    def test_source_span_not_nominal_endpoints(self):
        result=self.check_cloud(tube(.2))
        self.assertEqual(result['instances'][0]['reasons'],['length'])
        self.assertAlmostEqual(result['instances'][0]['lengthM'],.2,places=4)
    def test_half_surface_and_order_invariance(self):
        p=tube(half=True)
        self.assertTrue(self.check_cloud(p)['geometryPassed'])
        a,b=observed_geometry(p),observed_geometry(p[::-1])
        self.assertAlmostEqual(a['diameterM'],.006,places=4)
        self.assertEqual(a['lengthM'],b['lengthM'])
    def test_short_translation_exception_is_only_same_layer(self):
        p=tube()+[.07,.08,0]
        self.assertTrue(self.check_cloud(p,'short')['geometryPassed'])
        self.assertFalse(self.check_cloud(p)['geometryPassed'])
        self.assertIn('short_layer',self.check_cloud(p+[0,0,.02],'short')['instances'][0]['reasons'])
    def test_count_does_not_override_geometry_or_performance(self):
        self.assertFalse(self.check_cloud(tube(radius=.006))['geometryPassed'])
        result=self.check_cloud(tube(),elapsed_s=3,baseline_s=1)
        self.assertTrue(result['geometryPassed']);self.assertEqual(result['status'],'failed')
    def test_missing_and_duplicate(self):
        p=tube();result=evaluate_acceptance(p,np.zeros(len(p),int),np.full(len(p),3),[],inventory())
        self.assertEqual(result['missingUnits'],['a'])
        result=evaluate_acceptance(np.r_[p,p+[0,.03,0]],np.repeat([1,2],len(p)),np.full(2*len(p),3),[dict(id=i,designUnitId='a') for i in [1,2]],inventory())
        self.assertEqual(result['duplicateUnits'],['a']);self.assertEqual(result['status'],'failed')
if __name__=='__main__':unittest.main()
