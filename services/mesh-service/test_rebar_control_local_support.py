"""Partial scan confirmation cannot fill gaps, chase planes or unstable poses."""
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from algorithms.rebar_control_curves import _local_curve_support
from test_rebar_control_net import tube

class LocalCurveSupportTests(unittest.TestCase):
    def setUp(self):
        self.curve = np.array([[0., 0., 0.], [.12, 0., 0.]])
        self.points, self.normals = tube([0,0,0], [.12,0,0], radius=.004, along=240, around=24)

    def support(self, points=None, normals=None, check=None):
        return _local_curve_support(self.points if points is None else points,
            self.normals if normals is None else normals, self.curve,
            self.curve if check is None else check, .004, np.array([0.,0.,1.]))

    def test_two_continuous_patches_are_confirmed_without_bridging_missing_middle(self):
        keep = (self.points[:,0] < .04) | (self.points[:,0] > .07)
        selected, intervals, change = self.support(self.points[keep], self.normals[keep])
        self.assertGreater(np.mean(selected), .85)
        self.assertEqual(len(intervals), 2)
        self.assertLess(intervals[0][1], .041)
        self.assertGreater(intervals[1][0], .069)
        self.assertEqual(change, 0.)

    def test_normal_sign_and_rigid_pose_do_not_change_local_identity(self):
        expected, _, _ = self.support()
        flipped, _, _ = self.support(normals=-self.normals)
        np.testing.assert_array_equal(expected, flipped)
        rotation = Rotation.from_rotvec([.4,-.3,.2]).as_matrix()
        moved = self.curve@rotation.T + [3.,4.,5.]
        selected, _, _ = _local_curve_support(self.points@rotation.T+[3.,4.,5.],
            self.normals@rotation.T, moved, moved, .004, rotation@np.array([0.,0.,1.]))
        self.assertGreater(np.mean(selected == expected), .99)

    def test_unstable_validation_pose_and_wrong_normals_do_not_claim_surface(self):
        selected, intervals, _ = self.support(check=self.curve+[0.,.004,0.])
        self.assertFalse(selected.any()); self.assertFalse(intervals)
        selected, _, _ = self.support(normals=np.tile([1.,0.,0.], (len(self.points),1)))
        self.assertFalse(selected.any())
        selected, _, _ = self.support(normals=np.zeros_like(self.normals))
        self.assertFalse(selected.any())

    def test_flat_tangent_strip_is_not_a_tube_even_with_small_residual(self):
        x,y=np.meshgrid(np.linspace(0,.12,240),np.linspace(-.0005,.0005,24))
        points=np.c_[x.ravel(),y.ravel(),np.full(x.size,.004)]
        selected, intervals, _ = self.support(points,np.tile([0.,0.,1.],(len(points),1)))
        self.assertFalse(selected.any()); self.assertFalse(intervals)

    def test_sparse_rings_and_points_beyond_cut_do_not_form_side_evidence(self):
        keep=np.isin(np.arange(len(self.points))//24,[20,40,80,120,160,200])
        selected, _, _ = self.support(self.points[keep],self.normals[keep])
        self.assertFalse(selected.any())
        selected, _, _ = self.support(self.points+[.13,0.,0.],self.normals)
        self.assertFalse(selected.any())

if __name__=='__main__': unittest.main()
