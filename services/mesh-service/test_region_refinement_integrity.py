import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

from algorithms.region_refinement import frame_zones, refine_regions, same_surface_votes


def refinement_context(source_points, cell_points, source_to_cell, fused_class, *, cell_labels=None,
                       broad_fixture=None, strong_bars=None):
    source_points = np.asarray(source_points, dtype=np.float64)
    cell_points = np.asarray(cell_points, dtype=np.float64)
    fused_class = np.asarray(fused_class, dtype=np.uint8)
    residual = np.arange(len(cell_points), dtype=np.int64)
    if cell_labels is None:
        cell_labels = np.full(len(cell_points), 2, np.uint8)
    if broad_fixture is None:
        broad_fixture = np.zeros(len(cell_points), bool)
    if strong_bars is None:
        strong_bars = np.empty(0, np.int64)
    grid = SimpleNamespace(
        points=cell_points,
        origin=np.zeros(3),
        source_to_cell=np.asarray(source_to_cell, dtype=np.int64),
        tree=cKDTree(cell_points),
    )
    context = SimpleNamespace(
        positions=source_points,
        fused_class=fused_class.copy(),
        fused_region=np.where(fused_class == 1, 0, np.where(fused_class == 2, 3, 1)).astype(np.uint8),
        region_cache={
            'frame_axes': np.eye(2),
            'frame_bounds_local': np.array([-1., 1., -1., 1.]),
            'frame_inner_bounds_local': np.array([-.8, .8, -.8, .8]),
        },
        classification_cache={
            'grid': grid,
            'residual_ids': residual,
            'broad_fixture': np.asarray(broad_fixture, dtype=bool),
            'strong_bars': np.asarray(strong_bars, dtype=np.int64),
            'cell_labels': np.asarray(cell_labels, dtype=np.uint8),
            'features': {'surface_normal': np.tile([0., 0., 1.], (len(cell_points), 1))},
        },
    )
    report = {'frame': {'detected': True, 'innerDetected': True}}
    return context, report


class RegionRefinementIntegrityTests(unittest.TestCase):
    def test_rotated_strict_inner_and_inclusive_outer_boundaries(self):
        angle = np.deg2rad(31.)
        axes = np.array([[np.cos(angle), np.sin(angle)],
                         [-np.sin(angle), np.cos(angle)]])
        local = np.array([
            [0., 0.], [-.8, 0.], [.8, 0.], [0., -.8], [0., .8],
            [-1., 0.], [1., 0.], [0., -1.], [0., 1.], [1.000001, 0.],
        ])
        world = local @ axes
        np.testing.assert_array_equal(
            frame_zones(world, axes, [-1., 1., -1., 1.], [-.8, .8, -.8, .8]),
            [1, 2, 2, 2, 2, 2, 2, 2, 2, 3],
        )

    def test_source_rows_sharing_a_cell_do_not_leak_inner_ownership_outward(self):
        # The support-cell center is inside, while two source rows in that same
        # cell are exactly on and just outside the inner boundary.
        source = np.array([[0., 0., .1], [.8, 0., .1], [.81, 0., .1], [0., 0., 0.]])
        context, report = refinement_context(source, [[.79, 0., .1]], [0, 0, 0, 0], [2, 2, 2, 1])
        fused_class = context.fused_class.copy()
        fused_region = context.fused_region.copy()
        refine_regions(context, report)
        np.testing.assert_array_equal(context.refined_zone, [1, 2, 2, 1])
        np.testing.assert_array_equal(context.refined_class, [3, 2, 2, 1])
        np.testing.assert_array_equal(context.refined_reason, [1, 0, 0, 0])
        np.testing.assert_array_equal(context.refined_changed, [1, 0, 0, 0])
        np.testing.assert_array_equal(context.fused_class, fused_class)
        np.testing.assert_array_equal(context.fused_region, fused_region)

        # The inverse crossing is also important: an outside cell center must
        # not prevent an inner source row from being owned by the scene prior.
        context, report = refinement_context(source, [[.81, 0., .1]], [0, 0, 0, 0], [2, 2, 2, 1])
        refine_regions(context, report)
        np.testing.assert_array_equal(context.refined_class, [3, 2, 2, 1])

    def test_surface_vote_is_sign_independent_worker_stable_and_z_separated(self):
        x, y = np.meshgrid(np.arange(-.008, .009, .002), np.arange(-.008, .009, .002))
        sheet = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
        points = np.vstack((sheet, sheet + [0., 0., .003]))
        count = len(sheet)
        labels = np.r_[np.full(count, 2), np.full(count, 3)].astype(np.uint8)
        center = count // 2
        labels[[center, count + center]] = [3, 2]
        candidates = np.zeros(len(points), bool)
        candidates[[center, count + center]] = True
        normals = np.tile([0., 0., 1.], (len(points), 1))
        normals[::2] *= -1  # Simulate independently unoriented source normals.
        tree = cKDTree(points)
        single = same_surface_votes(points, normals, tree, labels, ~candidates, candidates, workers=1)
        multi = same_surface_votes(points, normals, tree, labels, ~candidates, candidates, workers=4)
        np.testing.assert_array_equal(single, multi)
        self.assertEqual(single[center], 2)
        self.assertEqual(single[count + center], 3)
        np.testing.assert_array_equal(single[~candidates], labels[~candidates])

    def test_strong_fixture_and_bar_cells_are_not_vote_targets(self):
        x = np.arange(-.012, .013, .003)
        cells = np.column_stack((x, np.zeros(len(x)), np.full(len(x), .1)))
        source = np.repeat(cells, 3, axis=0)
        source_to_cell = np.repeat(np.arange(len(cells)), 3)
        labels = np.full(len(source), 2, np.uint8)
        middle = len(cells) // 2
        labels[source_to_cell == middle] = 3
        context, report = refinement_context(
            source, cells, source_to_cell, labels,
            broad_fixture=np.arange(len(cells)) != middle,
            strong_bars=[middle],
        )
        # Put the line in the frame band, where voting is active.
        context.positions[:, 1] = .9
        context.classification_cache['grid'].points[:, 1] = .9
        refine_regions(context, report, workers=4)
        np.testing.assert_array_equal(context.refined_class, labels)

    def test_sparse_strong_bar_evidence_can_cast_the_frozen_vote(self):
        # Each strong bar cell has only one source row, so it has no ordinary
        # three-point voxel-majority reliability. Strong geometric evidence is
        # nevertheless an explicit frozen vote source.
        cells = np.array([
            [0., .9, .1],
            [-.004, .9, .1], [-.002, .9, .1], [.002, .9, .1], [.004, .9, .1],
            [0., .896, .1], [0., .904, .1],
        ])
        labels = np.array([2, 3, 3, 3, 3, 3, 3], np.uint8)
        context, report = refinement_context(
            cells, cells, np.arange(len(cells)), labels,
            strong_bars=np.arange(1, len(cells)),
        )
        refine_regions(context, report, workers=2)
        self.assertEqual(context.refined_class[0], 3)
        self.assertEqual(context.refined_reason[0], 3)

    def test_sparse_broad_fixture_evidence_can_cast_the_frozen_vote(self):
        cells = np.array([
            [0., .9, .1],
            [-.004, .9, .1], [-.002, .9, .1], [.002, .9, .1], [.004, .9, .1],
            [0., .896, .1], [0., .904, .1],
        ])
        labels = np.array([3, 2, 2, 2, 2, 2, 2], np.uint8)
        context, report = refinement_context(
            cells, cells, np.arange(len(cells)), labels,
            broad_fixture=np.arange(len(cells)) != 0,
        )
        refine_regions(context, report, workers=2)
        self.assertEqual(context.refined_class[0], 2)
        self.assertEqual(context.refined_reason[0], 3)

    def test_missing_inner_detection_is_a_complete_noop_even_with_cached_bounds(self):
        source = np.array([[0., 0., .1], [.9, 0., .1], [1.1, 0., .1]])
        context, report = refinement_context(source, source, [0, 1, 2], [2, 3, 1])
        before_class = context.fused_class.copy()
        before_region = context.fused_region.copy()
        report['frame']['innerDetected'] = False
        result = refine_regions(context, report)
        self.assertFalse(result['diagnostics']['enabled'])
        np.testing.assert_array_equal(context.refined_class, before_class)
        np.testing.assert_array_equal(context.refined_region, before_region)
        np.testing.assert_array_equal(context.refined_zone, 0)
        np.testing.assert_array_equal(context.refined_reason, 0)
        np.testing.assert_array_equal(context.refined_changed, 0)


if __name__ == '__main__':
    unittest.main()
