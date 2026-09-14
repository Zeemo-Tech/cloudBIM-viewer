import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

from algorithms.fixture_regions import classify_regions
from algorithms.region_refinement import frame_zones, refine_regions, same_surface_votes
from test_fixture_regions import synthetic_frame


def context_for_regions(points, classes, report, cache):
    # Deliberately unreliable local votes isolate the region-prior behavior.
    grid = SimpleNamespace(points=points.copy(), origin=np.zeros(3), source_to_cell=np.arange(len(points)), tree=cKDTree(points))
    return SimpleNamespace(positions=points, fused_class=classes.copy(), fused_region=report[2],
        region_cache=report[1], classification_cache={'grid': grid, 'residual_ids': np.arange(len(points)),
            'broad_fixture': classes == 2, 'strong_bars': np.flatnonzero(classes == 3),
            'features': {'surface_normal': np.tile([0., 0., 1.], (len(points), 1))}})


class RegionRefinementTests(unittest.TestCase):
    def test_rotated_inner_prior_overrides_fixture_but_preserves_table_and_outer_steel(self):
        points, classes, p, cache, _, _ = synthetic_frame()
        rr = classify_regions(points, classes, p, cache)
        context = context_for_regions(points, classes, rr, cache)
        before = context.fused_class.copy()
        report = refine_regions(context, rr[0], workers=2)
        inner = context.refined_zone == 1
        self.assertGreater(np.count_nonzero(inner & (before == 2)), 0)
        self.assertTrue(np.all(context.refined_class[inner & (before != 1)] == 3))
        np.testing.assert_array_equal(context.refined_class[before == 1], 1)
        outer_steel = (context.refined_zone == 3) & (before == 3)
        np.testing.assert_array_equal(context.refined_class[outer_steel], 3)
        np.testing.assert_array_equal(context.fused_class, before)
        np.testing.assert_array_equal(context.refined_changed, context.refined_class != before)
        self.assertEqual(sum(report['counts'].values()), len(points))
        self.assertEqual(sum(report['regionCounts'].values()), len(points))

    def test_same_surface_speckle_is_fixed_without_merging_close_parallel_surfaces(self):
        x, y = np.meshgrid(np.arange(-.008, .009, .002), np.arange(-.008, .009, .002))
        plane = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
        points = np.vstack((plane, plane+[0., 0., .003]))
        n = np.tile([0., 0., 1.], (len(points), 1))
        labels = np.r_[np.full(len(plane), 2), np.full(len(plane), 3)].astype(np.uint8)
        center = len(plane)//2
        labels[center] = 3  # Wrong green point embedded in the fixture sheet.
        labels[len(plane)+center] = 2  # Wrong yellow point in the steel sheet.
        candidates = np.zeros(len(points), bool); candidates[[center, len(plane)+center]] = True
        tree = cKDTree(points)
        result = same_surface_votes(points, n, tree, labels, ~candidates, candidates, workers=2)
        self.assertEqual(result[center], 2)
        self.assertEqual(result[len(plane)+center], 3)
        np.testing.assert_array_equal(result[~candidates], labels[~candidates])
        np.testing.assert_array_equal(result, same_surface_votes(points, -n, tree, labels, ~candidates, candidates, workers=1))

    def test_missing_double_frame_is_a_noop(self):
        points, classes, p, cache, _, _ = synthetic_frame(include_frame=False)
        rr = classify_regions(points, classes, p, cache)
        context = context_for_regions(points, classes, rr, cache)
        report = refine_regions(context, rr[0])
        self.assertFalse(report['diagnostics']['enabled'])
        np.testing.assert_array_equal(context.refined_class, classes)
        np.testing.assert_array_equal(context.refined_region, rr[2])
        self.assertFalse(context.refined_changed.any())

    def test_exact_inner_boundary_belongs_to_fixture_band(self):
        xy = np.array([[0., 0.], [.8, 0.], [1., 0.], [1.01, 0.]])
        np.testing.assert_array_equal(frame_zones(xy, np.eye(2), [-1, 1, -1, 1], [-.8, .8, -.8, .8]), [1, 2, 2, 3])


if __name__ == '__main__':
    unittest.main()
