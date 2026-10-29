"""Native kernels must preserve finite geometry, source order and tie rules."""
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

import geometry_accel
from algorithms.bim_scene_classification import _CompatibleSurfaceIndex
from algorithms.rebar_control_curves import _project, _CurveResidual


@unittest.skipUnless(geometry_accel.available(), 'build native kernels with sh native/build.sh')
class GeometryAccelerationTests(unittest.TestCase):
    def test_broad_phase_covers_finite_faces_with_float32_rounding(self):
        vertices=np.array([[.013,.021,.032],[.13,.02,.03],[.02,.14,.03],[.02,.14,.03]])
        faces=np.array([[0,1,2],[2,3,3]])
        index=geometry_accel.SurfaceIndex.broad_phase([(vertices,faces,1)])
        points=np.vstack((vertices,vertices.mean(0),vertices[0]+[0,0,.00299],[[10.,20.,30.]]))
        np.testing.assert_array_equal(index.near(points,.003,2),[True]*6+[False])
        with self.assertRaises(ValueError):index.query(points,np.zeros((1,3)),.003,1)
        with self.assertRaises(ValueError):geometry_accel.project(points,vertices,vertices,np.ones(3))

    def test_unknown_nonfinite_normals_cannot_acquire_surface_ownership(self):
        mesh=(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),np.array([[0,1,2]]),1)
        normals=np.array([[0.,0.,1.],[0.,0.,.75],[0.,0.,0.],[np.nan,0.,1.],[np.inf,0.,1.]])
        points=np.tile([.2,.2,.001],(len(normals),1))
        with patch('geometry_accel.available',return_value=False):reference=_CompatibleSurfaceIndex([mesh])
        with np.errstate(invalid='ignore'):
            expected=reference.query(points,normals)
            actual=_CompatibleSurfaceIndex([mesh]).query(points,normals)
        np.testing.assert_array_equal(actual[1],[1,0,0,0,0])
        np.testing.assert_array_equal(actual[1],expected[1])
        np.testing.assert_allclose(actual[0],expected[0],atol=1e-15,rtol=0)

    def test_optimizer_residual_and_jacobian_match_numpy_expression(self):
        rng=np.random.default_rng(91)
        station=np.linspace(0,1,71)
        def model(p):return np.c_[station,.03*np.sin(station*3+p[0]),.02*station**2+p[1]]
        points=model([0.,0.])[np.arange(1300)%71]+rng.normal(0,.003,(1300,3))
        normals=rng.normal(size=points.shape);normals/=np.linalg.norm(normals,axis=1)[:,None]
        normals[::5]=0
        args=(model,points,normals,np.ones(len(points)),.004,np.zeros(2),np.ones(2)*.03,.0005)
        accelerated=_CurveResidual(*args);reference=_CurveResidual(*args)
        for parameters in (np.zeros(2),np.array([.017,.002]),np.array([-.05,-.001])):
            actual=accelerated.fun(parameters,.002),accelerated.jac(parameters,.002)
            with patch('geometry_accel.available',return_value=False):
                expected=reference.fun(parameters,.002),reference.jac(parameters,.002)
            np.testing.assert_allclose(actual[0],expected[0],atol=2e-11,rtol=0)
            np.testing.assert_allclose(actual[1],expected[1],atol=2e-8,rtol=0)

    def test_finite_faces_normals_boundaries_and_ties_match_reference(self):
        rng = np.random.default_rng(401)
        triangles = rng.uniform(-.015, .015, (150, 3, 3))
        # Include exact duplicates, zero-area faces and a separated parallel face.
        triangles[12] = triangles[1]
        triangles[13] = 0
        triangles[14] = triangles[1]+[0, 0, .001]
        points = rng.uniform(-.02, .02, (1200, 3))
        normals = rng.normal(size=points.shape)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        for rotation in (np.eye(3), Rotation.from_rotvec([.7, -.2, 1.1]).as_matrix()):
            meshes = [(t@rotation.T, np.array([[0, 1, 2]]), i+1) for i, t in enumerate(triangles)]
            with patch('geometry_accel.available', return_value=False):
                reference = _CompatibleSurfaceIndex(meshes)
            native = _CompatibleSurfaceIndex(meshes, workers=2)
            self.assertIsNotNone(native.native)
            for sign in (1, -1):
                args = (points@rotation.T, sign*normals@rotation.T)
                expected = reference.query(*args, return_triangle_ids=True)
                actual = native.query(*args, return_triangle_ids=True)
                np.testing.assert_allclose(actual[0], expected[0], atol=1e-15, rtol=0)
                np.testing.assert_array_equal(actual[1], expected[1])
                np.testing.assert_array_equal(actual[2], expected[2])

    def test_polyline_ends_degenerate_segments_and_source_order(self):
        rng = np.random.default_rng(78)
        curve = np.cumsum(rng.normal(size=(80, 3))*.002, axis=0)
        curve[11] = curve[10]
        points = np.vstack((rng.normal(size=(1500, 3))*.04, curve))
        for rotation in (np.eye(3), Rotation.from_rotvec([-.3, .9, .8]).as_matrix()):
            p = points@rotation.T+[.12, -.23, .34]
            c = curve@rotation.T+[.12, -.23, .34]
            actual = _project(p, c)
            with patch('geometry_accel.available', return_value=False):
                expected = _project(p, c)
            for a, b in zip(actual, expected):
                if a.dtype == bool:
                    np.testing.assert_array_equal(a, b)
                else:
                    np.testing.assert_allclose(a, b, atol=1e-12, rtol=0)


if __name__ == '__main__':
    unittest.main()
