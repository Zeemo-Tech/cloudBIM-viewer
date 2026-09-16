"""Cut disks are ownership evidence; endpoint balls and nearby planes are not."""
import unittest
import numpy as np
from scipy.spatial import cKDTree
from algorithms.rebar_control_endpoints import _face_support, complete_end_faces
from test_rebar_control_net import tube

class EndFaceTests(unittest.TestCase):
    def cloud(self):
        side, normals = tube([-.04,0,0], [0,0,0], radius=.004, along=100, around=24)
        y,z=np.meshgrid(np.linspace(-.0038,.0038,17),np.linspace(-.0038,.0038,17))
        cap=np.c_[np.full(y.size,.0002),y.ravel(),z.ravel()]
        cap=cap[np.linalg.norm(cap[:,1:],axis=1)<.0039]
        points=np.vstack((side,cap)); n=np.vstack((normals,np.tile([1.,0,0],(len(cap),1))))
        seeds=np.arange(len(points))<len(side)
        return points,n,seeds

    def test_flat_cut_disk_recovered_from_owned_side(self):
        p,n,seeds=self.cloud()
        mask,ev=_face_support(p,n,np.zeros(3),np.array([1.,0,0]),.004,seeds)
        self.assertTrue(mask[~seeds].all());self.assertFalse(mask[seeds].any())
        self.assertLess(ev['planeRmseM'],1e-8)
        self.assertFalse(_face_support(p,n,np.zeros(3),np.array([1.,0,0]),.004,np.zeros(len(p),bool))[0].any())

    def test_fixture_plane_and_axial_noise_are_rejected(self):
        p,n,seeds=self.cloud()
        y,z=np.meshgrid(np.linspace(-.01,.01,31),np.linspace(-.01,.01,31))
        fixture=np.c_[np.full(y.size,.0002),y.ravel(),z.ravel()]
        mask,_=_face_support(np.vstack((p,fixture)),np.vstack((n,np.tile([1.,0,0],(len(fixture),1)))),np.zeros(3),np.array([1.,0,0]),.004,np.r_[seeds,np.zeros(len(fixture),bool)])
        self.assertFalse(mask.any())
        p[~seeds,0]=.004
        self.assertFalse(_face_support(p,n,np.zeros(3),np.array([1.,0,0]),.004,seeds)[0].any())

    def test_hemisphere_is_not_a_flat_cut(self):
        p,n,seeds=self.cloud()
        radial=np.linalg.norm(p[~seeds,1:],axis=1)
        p[~seeds,0]=np.sqrt(.004**2-radial**2)
        n[~seeds]=p[~seeds]/.004
        self.assertFalse(_face_support(p,n,np.zeros(3),np.array([1.,0,0]),.004,seeds)[0].any())

    def test_rigid_pose_and_normal_sign_preserve_the_disk(self):
        from scipy.spatial.transform import Rotation
        p,n,seeds=self.cloud();rotation=Rotation.from_rotvec([.3,-.6,.2]).as_matrix()
        expected,_=_face_support(p,n,np.zeros(3),np.array([1.,0,0]),.004,seeds)
        shift=np.array([3.,4.,5.])
        actual,_=_face_support(p@rotation.T+shift,-n@rotation.T,shift,rotation[:,0],.004,seeds)
        np.testing.assert_array_equal(actual,expected)

    def test_completion_preserves_owners_and_excluded_points(self):
        p,n,seeds=self.cloud();owner=seeds.astype(np.uint32);status=np.where(seeds,1,2).astype(np.uint8)
        cap=np.flatnonzero(~seeds);owner[cap[0]]=2;status[cap[0]]=1;status[cap[1]]=0;status[cap[2]]=4
        before=owner.copy();row={'id':1,'status':'fitted','diameterM':.008,'centerlineM':[[-.04,0,0],[0,0,0]],'pointCount':int(seeds.sum())}
        count=complete_end_faces(p,n,[row],[],owner,status,cKDTree(p),np.arange(len(p)))
        self.assertGreater(count,100);np.testing.assert_array_equal(owner[before>0],before[before>0])
        np.testing.assert_array_equal(status[cap[1:3]],[0,4]);self.assertEqual(row['pointCount'],np.sum(owner==1))
        self.assertEqual(complete_end_faces(p,n,[row],[],owner,status,cKDTree(p),np.arange(len(p))),0)

    def test_competing_end_faces_remain_unassigned(self):
        p,n,seeds=self.cloud();owner=np.zeros(len(p),np.uint32);owner[seeds]=np.arange(seeds.sum())%2+1
        status=np.where(seeds,1,2).astype(np.uint8)
        rows=[{'id':i,'status':'fitted','diameterM':.008,'centerlineM':[[-.04,0,0],[0,0,0]],'pointCount':int((owner==i).sum())} for i in (1,2)]
        self.assertEqual(complete_end_faces(p,n,rows,[],owner,status,cKDTree(p),np.arange(len(p))),0)

if __name__=='__main__':unittest.main()
