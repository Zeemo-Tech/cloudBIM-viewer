"""Final Step 06 count/shape checks and retained hooked-bar ownership."""
import unittest
import numpy as np

from algorithms.design_guided_instances import refine_instances
from algorithms.rebar_cluster_quality import final_fragment_filter
from rebar_design_prior import inventory_from_bars
from test_design_guided_instances import scene, inventory


class ClusterQualityTests(unittest.TestCase):
    def test_disconnected_hook_is_not_claimed_and_upstream_noise_stays_removed(self):
        rods = [([0, 0, 0], [2, 0, 0]), ([2, 0, .035], [2, 0, .06])]
        ctx, report, sizes = scene(rods, ids=[1, 0], zones=[1, 3])
        ctx.fused_steel_score = np.ones(len(ctx.positions), np.float32)
        ctx.internal_type[-10:] = 5
        report['denoising'] = {'highScoreOverrideAllowed': True}
        design = inventory_from_bars([dict(designBarId='hook', points=[[0,0,0],[2,0,0],[2,0,.06]],
            radiusM=.0025, coverage='complete')], np.eye(4).ravel().tolist())
        r = refine_instances(ctx, report, design)
        self.assertEqual(r['designReview']['hookAttachedPointCount'], 0)
        self.assertFalse(np.any(ctx.complete_instance[sizes[0]:] == 1))
        np.testing.assert_array_equal(ctx.complete_class[-10:], 4)

    def test_small_rigid_tilt_does_not_look_like_a_wide_rod(self):
        ctx, report, _ = scene([([0, 0, 0], [2, .025, 0])])
        r = refine_instances(ctx, report, inventory([([0, 0, 0], [2, 0, 0])]))
        self.assertEqual(r['designReview']['clusterQuality']['shapeMismatchCount'], 0)

    def test_long_hook_with_mixed_scores_keeps_one_parent_and_measured_width(self):
        rods = [([0, 0, 0], [2, 0, 0]), ([2, 0, 0], [2, 0, .06]),
                ([2, 0, .06], [1.97, 0, .06])]
        ctx, report, sizes = scene(rods, ids=[1, 0, 0], zones=[1, 3, 3])
        ctx.fused_steel_score = np.resize([.95, .25, .65], len(ctx.positions)).astype(np.float32)
        points, scores = ctx.positions.copy(), ctx.fused_steel_score.copy()
        design = inventory_from_bars([dict(designBarId='hook', points=[rods[0][0]]+[r[1] for r in rods],
            radiusM=.0025, coverage='complete')], np.eye(4).ravel().tolist())
        result = refine_instances(ctx, report, design)
        np.testing.assert_array_equal(ctx.complete_class, 3)
        np.testing.assert_array_equal(ctx.complete_instance, 1)
        np.testing.assert_array_equal(ctx.positions, points)
        np.testing.assert_array_equal(ctx.fused_steel_score, scores)
        quality = result['designReview']['clusterQuality']
        self.assertTrue(quality['countMatches'])
        self.assertEqual(quality['shapeMismatchCount'], 0)
        self.assertGreater(result['designReview']['hookAttachedPointCount'], 0)
        self.assertGreater(max(quality['instances'][0]['observedWidthsM']), .055)
        counts = np.bincount(ctx.complete_segment)
        for s in result['segments']:
            self.assertEqual(s['pointCount'], counts[s['id']])

    def test_locally_displaced_hook_and_occluded_collar_merge_as_one_locked_cluster(self):
        rods = [([0, 0, 0], [1.72, 0, 0]),
                ([1.8, .026, 0], [2, .026, 0]),
                ([2, .026, 0], [2, .026, .06])]
        ctx, report, sizes = scene(rods, ids=[1, 0, 0], zones=[1, 3, 3])
        ctx.fused_steel_score = np.ones(len(ctx.positions), np.float32)
        design = inventory_from_bars([dict(designBarId='hook',
            points=[[0,0,0],[2,0,0],[2,0,.06]], radiusM=.0025,
            coverage='complete')], np.eye(4).ravel().tolist())
        result = refine_instances(ctx, report, design)
        hooks = result['designReview']['hookClusters']
        self.assertEqual(hooks['detectedClusterCount'], 1)
        self.assertEqual(hooks['mergedClusterCount'], 1)
        self.assertEqual(hooks['filteredPointCount'], 0)
        self.assertEqual(hooks['splitClusterCount'], 0)
        np.testing.assert_array_equal(ctx.complete_class, 3)
        np.testing.assert_array_equal(ctx.complete_instance, 1)
        self.assertEqual(len(np.unique(ctx.complete_cluster[sizes[0]:])), 1)

    def test_shortened_bar_is_allowed_but_severe_loss_is_reported_without_deletion(self):
        for length, mismatch in ((.8, 0), (.4, 1)):
            ctx, report, _ = scene([([0, 0, 0], [length, 0, 0])])
            result = refine_instances(ctx, report, inventory([([0, 0, 0], [1, 0, 0])]))
            q = result['designReview']['clusterQuality']
            self.assertEqual(q['tooShortCount'], mismatch)
            np.testing.assert_array_equal(ctx.complete_class, 3)

    def test_web_diagonals_count_separately_and_equal_totals_do_not_hide_missing_unit(self):
        rods = [([0, 0, 0], [.18, 0, .18]), ([.18, 0, .18], [.36, 0, 0])]
        ctx, report, _ = scene(rods)
        design = inventory_from_bars([dict(designBarId='web', points=[rods[0][0], rods[0][1], rods[1][1]],
            radiusM=.0025, coverage='complete')], np.eye(4).ravel().tolist())
        r = refine_instances(ctx, report, design)
        self.assertEqual(r['designReview']['clusterQuality']['expectedClusterCount'], 2)
        self.assertTrue(r['designReview']['clusterQuality']['countMatches'])
        ctx, report, _ = scene([([0, 0, 0], [.5, 0, 0]), ([0, .008, 0], [.5, .008, 0])])
        r = refine_instances(ctx, report, inventory([([0, 0, 0], [.5, 0, 0]), ([0, 2, 0], [.5, 2, 0])]))
        q = r['designReview']['clusterQuality']
        self.assertEqual(q['countDelta'], 0)
        self.assertFalse(q['countMatches'])

    def test_final_filter_checks_full_component_score_size_and_support(self):
        ctx, _, _ = scene([([0, 0, 0], [1, 0, 0])])
        n = len(ctx.positions)
        # Tiny low-score float, high-score float, connected low-score tip,
        # low-score elongated fragment, mixed-score island, medium-score float.
        tail = np.array([[.5, .2, 0], [.501, .2, 0], [.5, .3, 0],
                         [1.002, 0, 0], [.5, .4, 0], [.52, .4, 0],
                         [.5, .5, 0], [.501, .5, 0], [.5, .6, 0]])
        # Densely connect the elongated fragment to keep it one component.
        long = np.c_[np.linspace(.5, .52, 20), np.full(20, .4), np.zeros(20)]
        ctx.positions = np.vstack([ctx.positions, tail, long])
        ctx.fused_steel_score = np.r_[np.ones(n), [.2, .3, 1., .2, .2, .2, .2, 1., .65], np.full(20, .2)]
        out = {'complete_class': np.full(len(ctx.positions), 3, np.uint8),
               'complete_instance': np.r_[np.ones(n), np.zeros(len(tail)+len(long))].astype(np.uint32),
               'complete_segment': np.zeros(len(ctx.positions), np.uint32),
               'complete_confidence': np.zeros(len(ctx.positions), np.float32)}
        r = final_fragment_filter(ctx, out)
        self.assertEqual(r['removedPointCount'], 2)
        np.testing.assert_array_equal(out['complete_class'][n:n+2], 4)
        np.testing.assert_array_equal(out['complete_class'][n+2:], 3)

        # A tiny satellite must not evade the same review just by carrying an ID.
        out['complete_class'][n:n+2] = 3
        out['complete_instance'][n:n+2] = 1
        out['complete_segment'][n:n+2] = 1
        r = final_fragment_filter(ctx, out)
        self.assertEqual(r['assignedSatellitePointCount'], 2)
        np.testing.assert_array_equal(out['complete_instance'][n:n+2], 0)
        np.testing.assert_array_equal(out['complete_segment'][n:n+2], 0)


if __name__ == '__main__':
    unittest.main()
