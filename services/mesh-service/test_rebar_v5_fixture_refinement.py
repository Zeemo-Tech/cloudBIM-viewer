import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.pipeline import refine_fixture_support
from algorithms.rebar_v5.scene import fixture_mask, refine_fixture_faces
from algorithms.rebar_v5.spatial import SpatialStore, SpatialBudgetExceeded


class FixtureRefinementTests(unittest.TestCase):
    def setUp(self):
        x, y = np.meshgrid(np.linspace(-.5, .5, 100), np.linspace(-.06, .06, 24))
        self.points = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .05)))
        self.proposal = {
            'origin': [0., 0., .05], 'normal': [0., 0., 1.],
            'axes': [[1., 0., 0.], [0., 1., 0.]], 'halfExtent': [.51, .07],
        }

    def refine(self, points, p, chunk_size=10000, masked=False):
        with tempfile.TemporaryDirectory() as directory:
            chunks = [(np.arange(start, min(start+chunk_size, len(points)), dtype=np.uint64),
                       points[start:start+chunk_size]) for start in range(0, len(points), chunk_size)]
            store = SpatialStore(directory, p).build(chunks)
            runtime = SimpleNamespace(store=store, masks=lambda rows: np.full(len(rows), masked, dtype=bool))
            return refine_fixture_support(runtime, None, [self.proposal], p)

    def test_dense_raw_face_refines_without_exceeding_neighbourhood_budget(self):
        x, y = np.meshgrid(np.linspace(-.5, .5, 100), np.linspace(-.06, .06, 24))
        points = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .05)))
        p = Params(block_point_limit=200, neighbourhood_point_limit=800)
        proposal = {
            'origin': [0., 0., .05], 'normal': [0., 0., 1.],
            'axes': [[1., 0., 0.], [0., 1., 0.]], 'halfExtent': [.51, .07],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SpatialStore(directory, p).build([(np.arange(len(points), dtype=np.uint64), points)])
            runtime = SimpleNamespace(store=store, masks=lambda rows: np.zeros(len(rows), dtype=bool))
            faces = refine_fixture_support(runtime, None, [proposal], p)
            self.assertTrue(faces)
            interior = (np.abs(points[:, 0]) < .45) & (np.abs(points[:, 1]) < .04)
            self.assertTrue(fixture_mask(points[interior], faces, p).all())

    def test_under_budget_keeps_existing_whole_face_refinement(self):
        p = Params()
        self.assertEqual(self.refine(self.points, p), refine_fixture_faces(self.points, [self.proposal], p))

    def test_storage_boundaries_do_not_cut_observed_projection_cells(self):
        p = Params(block_point_limit=200, neighbourhood_point_limit=800)
        faces = self.refine(self.points, p)
        baseline = self.refine(self.points, Params())
        x, y = np.meshgrid(np.linspace(-.45, .45, 901), np.linspace(-.04, .04, 81))
        probes = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .05)))
        np.testing.assert_array_equal(fixture_mask(probes, faces, p), fixture_mask(probes, baseline, p))

    def test_tiled_faces_preserve_holes_boundaries_counts_and_reader_chunking(self):
        points = self.points[np.abs(self.points[:, 0]) > .05]
        p = Params(block_point_limit=200, neighbourhood_point_limit=800)
        faces = self.refine(points, p)
        self.assertEqual(faces, self.refine(points, p, chunk_size=73))
        baseline = self.refine(points, Params())
        interior = points[(np.abs(points[:, 0]) < .45) & (np.abs(points[:, 1]) < .04)]
        np.testing.assert_array_equal(fixture_mask(interior, faces, p), fixture_mask(interior, baseline, p))
        self.assertTrue(fixture_mask(interior, faces, p).all())
        probes = np.array([[0., y, .05] for y in np.linspace(-.04, .04, 9)])
        self.assertFalse(fixture_mask(probes, faces, p).any())
        masks = np.array([fixture_mask(points, [face], p) &
                          np.all((points >= face['coreBounds'][0]) & (points < face['coreBounds'][1]), axis=1)
                          for face in faces])
        self.assertTrue((masks.sum(axis=0) <= 1).all())
        for face, mask in zip(faces, masks):
            self.assertEqual(face['supportCount'], int(mask.sum()))

    def test_noise_does_not_become_fallback_surface_evidence(self):
        p = Params(block_point_limit=200, neighbourhood_point_limit=800)
        self.assertEqual(self.refine(self.points, p, masked=True), [])

    def test_physical_halo_overflow_remains_a_typed_resource_error(self):
        p = Params(block_point_limit=10, neighbourhood_point_limit=20)
        with self.assertRaises(SpatialBudgetExceeded):
            self.refine(self.points, p)


if __name__ == '__main__':
    unittest.main()
