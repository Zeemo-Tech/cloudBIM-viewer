"""Numerical/reference guards for the 169d1a0 performance-only changes."""
import unittest
from unittest.mock import patch

import numpy as np

from algorithms import multiview_floating_noise as noise
from algorithms import rebar_extension as extension
from algorithms.rebar_bend_cloth import build_bend_cloths
from algorithms.rebar_curve_envelope import _inside_mesh, build_curve_shells


def legacy_unique(keys, **kwargs):
    return np.unique(keys, axis=0, **kwargs)


def legacy_mesh(points, vertices, triangles):
    """Frozen winding/surface predicate from 169d1a0, independent of new algebra."""
    result = np.zeros(len(points), bool)
    tri = vertices[triangles]
    e1, e2 = tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]
    for start in range(0, len(points), 384):
        query = points[start:start+384]
        normal = np.cross(e1, e2)
        norm2 = np.einsum('ti,ti->t', normal, normal)
        relative = query[None]-tri[:, None, 0]
        plane = np.abs(np.einsum('tqi,ti->tq', relative, normal)) <= 1e-8*np.sqrt(norm2)[:, None]
        d00 = np.einsum('ti,ti->t', e1, e1)[:, None]
        d01 = np.einsum('ti,ti->t', e1, e2)[:, None]
        d11 = np.einsum('ti,ti->t', e2, e2)[:, None]
        d20 = np.einsum('tqi,ti->tq', relative, e1)
        d21 = np.einsum('tqi,ti->tq', relative, e2)
        denominator = d00*d11-d01*d01
        u = (d11*d20-d01*d21)/np.maximum(denominator, 1e-18)
        v = (d00*d21-d01*d20)/np.maximum(denominator, 1e-18)
        surface = plane & (u >= -1e-8) & (v >= -1e-8) & (u+v <= 1+1e-8)
        a, b, c = tri[:, None, 0]-query, tri[:, None, 1]-query, tri[:, None, 2]-query
        la, lb, lc = [np.linalg.norm(x, axis=2) for x in (a, b, c)]
        numerator = np.einsum('tqi,tqi->tq', a, np.cross(b, c))
        denominator = (la*lb*lc + np.einsum('tqi,tqi->tq', a, b)*lc
                       + np.einsum('tqi,tqi->tq', b, c)*la + np.einsum('tqi,tqi->tq', c, a)*lb)
        winding = np.abs(np.sum(2*np.arctan2(numerator, denominator), axis=0))
        result[start:start+len(query)] = surface.any(axis=0) | (winding > 2*np.pi)
    return result


class SpeedEquivalenceTests(unittest.TestCase):
    def test_curve_membership_matches_legacy_at_caps_edges_cavities_and_translations(self):
        rng = np.random.default_rng(829)
        curve = np.array([[0, 0, 0], [.1, 0, 0], [.14, 0, .04], [.14, 0, .16], [.1, 0, .2], [0, 0, .2]])
        paths = [dict(designBarId=str(i), radiusM=.004, points=curve+[0, .03*i, 0]) for i in range(3)]
        meshes = build_bend_cloths(paths) + build_curve_shells(paths[:1])
        matrix, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        for mesh in meshes:
            vertices, triangles = np.asarray(mesh['verticesM']), np.asarray(mesh['triangles'])
            faces = vertices[triangles]
            centers = faces.mean(axis=1)
            normals = np.cross(faces[:, 1]-faces[:, 0], faces[:, 2]-faces[:, 0])
            normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-20)
            queries = np.vstack((vertices, faces[:, :2].mean(axis=1), centers,
                                 centers+normals*2e-8, centers-normals*2e-8,
                                 rng.uniform(vertices.min(0)-.02, vertices.max(0)+.02, size=(900, 3))))
            for offset in (np.zeros(3), np.array([4e6, -3e6, 150.])):
                v = vertices@matrix + offset
                q = queries@matrix + offset
                actual, expected = _inside_mesh(q, v, triangles), legacy_mesh(q, v, triangles)
                np.testing.assert_array_equal(actual, expected)
                self.assertTrue(actual.any())
                self.assertTrue((~actual).any())
                np.testing.assert_array_equal(_inside_mesh(q, v, triangles[:, ::-1]), actual)

    def test_voxel_components_and_representative_normals_keep_source_order(self):
        rng = np.random.default_rng(710)
        points = np.vstack([rng.uniform(-.01, .01, (500, 3))+[x, 0, 0] for x in (0, .08, .2)])
        points = np.repeat(points, 2, axis=0)
        rng.shuffle(points)
        normals = rng.normal(size=points.shape)
        normals[::19] = 0
        normals[::31] = np.nan
        # Different normals at repeated coordinates expose representative drift.
        for origin in (np.zeros(3), np.array([1e6, -1e6, 3.])):
            xyz = points+origin
            for module, function, args in (
                (noise, noise._components, (xyz, noise.Parameters())),
                (noise, noise._fixture_voxels, (xyz, normals, noise.Parameters())),
                (extension, extension.exterior_clusters, (xyz, extension.ExtensionParameters())),
            ):
                actual = function(*args)
                with patch.object(module, 'unique_integer_rows', legacy_unique):
                    expected = function(*args)
                if isinstance(actual, tuple):
                    for a, b in zip(actual, expected):
                        np.testing.assert_array_equal(a, b)
                else:
                    np.testing.assert_array_equal(actual, expected)


if __name__ == '__main__':
    unittest.main()
