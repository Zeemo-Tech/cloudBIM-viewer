"""Finite, normal-compatible alternatives must survive nearer crossing faces."""
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from algorithms.bim_scene_classification import _CompatibleSurfaceIndex, _surface_samples, classify_scene_surfaces, recover_scene_alternatives


def plate(guid, vertices, category='fixture'):
    return dict(globalId=guid, name=guid, ifcType='IfcPlate', category=category,
                vertices=np.asarray(vertices, float), faces=np.array([[0, 1, 2], [0, 2, 3]]))


class CompatibleCandidateTests(unittest.TestCase):
    def run_scene(self, rotation=np.eye(3), deferred=False, exclude_targets=False):
        horizontal=plate('horizontal', [[-.1,-.1,.002],[.1,-.1,.002],[.1,.1,.002],[-.1,.1,.002]])
        crossing=plate('crossing', [[0,-.1,-.03],[0,.1,-.03],[0,.1,.03],[0,-.1,.03]], 'embedded')
        a, an=_surface_samples(horizontal['vertices'], horizontal['faces'], 10000)
        b, bn=_surface_samples(crossing['vertices'], crossing['faces'], 4000)
        # Horizontal finite face: 2 mm away. Incompatible crossing: 0.5 mm.
        y=np.linspace(.02,.08,80)
        target=np.c_[np.full(80,.0005),y,np.zeros(80)]
        negatives=np.array([[.1035,0,0], [.0005,0,-.02], [.0005,0,.01]])
        negative_n=np.array([[0,0,1],[0,0,1],[0,1,0]])
        theta=np.linspace(-np.pi,np.pi,80,endpoint=False)
        tube=np.c_[np.full(80,.0005),.004*np.cos(theta),.004+.004*np.sin(theta)]
        tube_n=np.c_[np.zeros(80),np.cos(theta),np.sin(theta)]
        p=np.vstack((a,b,target,negatives,tube));n=np.vstack((an,bn,np.tile([0,0,1],(80,1)),negative_n,tube_n))
        shift=np.array([.17,-.21,.33]);p=p@rotation.T+shift;n=n@rotation.T
        for obj in (horizontal,crossing):obj['vertices']=obj['vertices']@rotation.T+shift
        axis=np.array([[0,0,.004],[.01,0,.004]])@rotation.T+shift
        inv={'bars':[dict(unitIds=['real-tube'],radiusM=.004,points=axis.tolist())]}
        before=p.copy()
        # Ownership-gate regression, independent of the rigid fitter.
        scene={'objects':[horizontal,crossing],'diagnostics':{}}
        with patch('algorithms.bim_scene_classification._rigid_fit',return_value=np.eye(4)):
            report, labels, owners=classify_scene_surfaces(p,n,np.zeros(len(p),bool),
                scene,inv,allow_fallback=not deferred)
        if deferred:
            np.testing.assert_array_equal(labels[14000:14080],0)
            original=owners.copy();excluded=np.zeros(len(p),bool)
            if exclude_targets:excluded[14000:14020]=True
            report,labels,owners=recover_scene_alternatives(p,n,np.zeros(len(p),bool),
                scene,inv,report,labels,owners,excluded_mask=excluded)
            np.testing.assert_array_equal(owners[original>0],original[original>0])
        np.testing.assert_array_equal(p,before)
        start=14020 if exclude_targets else 14000
        np.testing.assert_array_equal(labels[start:14080],2)
        np.testing.assert_array_equal(owners[start:14080],1)
        if exclude_targets:np.testing.assert_array_equal(labels[14000:14020],0)
        np.testing.assert_array_equal(labels[14080:14083],0)
        np.testing.assert_array_equal(labels[14083:],0)
        self.assertGreater(np.count_nonzero(owners[:10000]==1),9000)
        self.assertGreater(np.count_nonzero(owners[10000:14000]==2),3000)
        return labels,owners,report

    def test_second_finite_face_and_guards(self):
        self.run_scene()

    def test_deferred_recovery_preserves_existing_and_excluded_rows(self):
        a,b,_=self.run_scene()
        c,d,_=self.run_scene(deferred=True)
        np.testing.assert_array_equal(a,c)
        np.testing.assert_array_equal(b,d)
        self.run_scene(deferred=True,exclude_targets=True)

    def test_arbitrary_rigid_frame(self):
        a,b,_=self.run_scene()
        c,d,_=self.run_scene(Rotation.from_rotvec([.67,-.42,1.13]).as_matrix())
        np.testing.assert_array_equal(a,c)
        np.testing.assert_array_equal(b,d)

    def test_bin_boundary_cannot_hide_compatible_face_and_sign(self):
        meshes=[]
        # Many almost parallel faces: their alignment straddles .75. A single
        # nearest-face query in one direction bin would still lose the last face.
        for i in range(131):
            cosine=.74 if i<130 else .76
            normal=np.array([np.sqrt(1-cosine*cosine),0,cosine])
            u=np.array([0,1,0]);v=np.cross(normal,u)
            center=normal*(.001+i*1e-6 if i<130 else .002)
            vertices=center+np.array([-u-v,2*u-v,-u+2*v])*.1
            meshes.append((vertices,np.array([[0,1,2]]),i+1))
        for rotation in (np.eye(3),Rotation.from_rotvec([.4,.8,-.9]).as_matrix()):
            rotated=[(v@rotation.T,f,o) for v,f,o in meshes]
            index=_CompatibleSurfaceIndex(rotated)
            distance,owner=index.query(np.zeros((2,3)),np.array([[0,0,1],[0,0,-1]])@rotation.T)
            np.testing.assert_array_equal(owner,[131,131])
            np.testing.assert_allclose(distance,.002,atol=1e-8)
            # Exercise cached BVH partitions across source chunks as the
            # business classifier does.
            again_distance,again_owner=index.query(np.zeros((2,3)),np.array([[0,0,1],[0,0,-1]])@rotation.T)
            np.testing.assert_array_equal(again_owner,owner)
            np.testing.assert_allclose(again_distance,distance,atol=1e-8)


if __name__=='__main__':unittest.main()
