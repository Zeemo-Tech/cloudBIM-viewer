"""Keep component count from multiplying full-scan buffer allocations."""
import unittest
from unittest.mock import patch

import numpy as np

from algorithms.bim_scene_classification import classify_scene_surfaces, _surface_samples


class SceneBufferTests(unittest.TestCase):
    def test_component_fits_reuse_source_aligned_eligible_buffers(self):
        vertices = np.array([[0., 0., 0.], [.1, 0., 0.], [.1, .1, 0.], [0., .1, 0.]])
        faces = np.array([[0, 1, 2], [0, 2, 3]])
        points, normals = _surface_samples(vertices, faces, 1000)
        table = np.arange(len(points)) % 3 == 0
        objects = [dict(globalId=str(i), name=str(i), ifcType='IfcPlate', category='fixture',
                        vertices=vertices.copy(), faces=faces) for i in range(6)]
        buffers = []

        def fit(samples, sample_normals, tree, eligible_points, eligible_normals, *args, **kwargs):
            # The same source-row selection must address both the tree and normals.
            np.testing.assert_array_equal(eligible_points, points[~table])
            np.testing.assert_array_equal(eligible_normals, normals[~table])
            np.testing.assert_array_equal(tree.data, eligible_points)
            buffers.append((eligible_points, eligible_normals))
            return np.eye(4)

        with patch('algorithms.bim_scene_classification._rigid_fit', side_effect=fit):
            _, labels, owners = classify_scene_surfaces(
                points, normals, table, {'objects': objects, 'diagnostics': {}}, {'bars': []})
        self.assertEqual(len(buffers), len(objects)+1)
        # Retain references so allocator address reuse cannot conceal copies.
        self.assertEqual(len({id(p) for p, _ in buffers}), 1,
                         'every component copied the entire eligible scan')
        self.assertEqual(len({id(n) for _, n in buffers}), 1,
                         'every component copied the entire normal array')
        self.assertFalse(labels[table].any())
        self.assertFalse(owners[table].any())


if __name__ == '__main__':
    unittest.main()
