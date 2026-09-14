import unittest
import numpy as np
from algorithms.floating_noise import floating_noise_mask


class FloatingNoiseTests(unittest.TestCase):
    def setUp(self):
        self.bands = [{'low': .02, 'high': .04}, {'low': .09, 'high': .11}]
        self.segments = [dict(type=2, startM=[0, 0, .1], endM=[1, 0, .1], radiusM=.004, pointCount=100),
                         dict(type=3, startM=[0, .2, .03], endM=[1, .2, .1], radiusM=.003, pointCount=100)]

    def test_preserves_each_support_and_assigned_points_but_removes_floating_clusters(self):
        points = np.array([[.5, .5, .03], [.5, .008, .1], [.5, .208, .065],
                           [.5, .5, .065], [.501, .5, .065], [2, 0, .1], [.5, .5, .065]])
        types = np.array([4, 4, 4, 4, 4, 4, 3])
        original = points.copy()
        for workers in (1, 2):
            mask, report = floating_noise_mask(points, types, self.bands, self.segments, workers=workers)
            np.testing.assert_array_equal(mask, [False, False, False, True, True, True, False])
            self.assertEqual(report['removedPointCount'], 3)
        np.testing.assert_array_equal(points, original)

    def test_partial_evidence_is_usable_but_no_evidence_skips(self):
        points = np.array([[3, 3, .03], [3, 3, .06]])
        mask, report = floating_noise_mask(points, np.full(2, 4), self.bands[:1], [])
        np.testing.assert_array_equal(mask, [False, True])
        self.assertNotIn('skippedReason', report)

        upper_only = [self.segments[0]]
        points = np.array([[.5, .01, .1], [3, 3, .06]])
        mask, report = floating_noise_mask(points, np.full(2, 4), [], upper_only)
        np.testing.assert_array_equal(mask, [False, True])
        self.assertNotIn('skippedReason', report)

        mask, report = floating_noise_mask(np.array([[3, 3, .06]]), np.array([4]), [], [])
        self.assertFalse(mask.any())
        self.assertIn('skippedReason', report)

    def test_high_fused_support_blocks_only_its_own_removal(self):
        points = np.array([[.5, .5, .065], [.501, .5, .065], [.502, .5, .065]])
        mask, report = floating_noise_mask(points, np.full(3, 4), self.bands, self.segments,
                                           protected=np.array([False, True, False]))
        np.testing.assert_array_equal(mask, [True, False, True])
        self.assertEqual(report['protectedCandidatePointCount'], 1)
        self.assertEqual(report['blockedRemovalPointCount'], 1)
        self.assertEqual(report['removedPointCount'], 2)

    def test_blocked_count_excludes_high_score_points_with_geometry_support(self):
        points = np.array([[.5, .008, .1], [3., 3., .06]])
        mask, report = floating_noise_mask(points, np.array([2, 2]), self.bands,
                                           self.segments, steel_scores=np.array([.9, .9]))
        self.assertFalse(mask.any())
        self.assertEqual(report['protectedCandidatePointCount'], 2)
        self.assertEqual(report['blockedRemovalPointCount'], 1)

    def test_float32_point_nine_is_hard_protected(self):
        mask, report = floating_noise_mask(
            np.array([[3., 3., .06]]), np.array([2]), self.bands, self.segments,
            steel_scores=np.array([.9], dtype=np.float32))
        self.assertFalse(mask.any())
        self.assertEqual(report['protectedCandidatePointCount'], 1)

    def test_protection_shape_is_checked(self):
        with self.assertRaisesRegex(ValueError, '逐行对应'):
            floating_noise_mask(np.zeros((2, 3)), np.full(2, 4), self.bands, self.segments,
                                protected=np.ones(1, bool))

    def test_empty_candidates_and_lower_band_boundary(self):
        mask, _ = floating_noise_mask(np.array([[3, 3, .043], [3, 3, .017]]),
                                      np.array([4, 4]), self.bands, self.segments)
        self.assertFalse(mask.any())
        mask, _ = floating_noise_mask(np.empty((0, 3)), np.empty(0), [], [])
        self.assertEqual(len(mask), 0)

    def test_scores_review_assigned_and_single_route_but_hard_protect_high(self):
        points = np.array([
            [.5, .5, .03],       # low score, lower-band support
            [.5, .010, .10],     # low score, observed upper-cylinder support
            [3., 3., .06],       # low score assigned web, unsupported
            [3.1, 3., .06],      # ordinary single-route score, still reviewed
            [3.2, 3., .06],      # dual-route score, hard protected
            [3.3, 3., .06],      # explicit protection is also hard
            [3.4, 3., .06],      # non-steel type remains outside the scope
        ])
        types = np.array([1, 2, 3, 2, 3, 1, 0])
        scores = np.array([.2, .2, .2, .65, .9, .2, .2])
        mask, report = floating_noise_mask(
            points, types, self.bands, self.segments, steel_scores=scores,
            protected=np.array([False, False, False, False, False, True, False]),
            observed_support_points=np.array([[.5, .5, .034], [.5, .006, .1]]))
        np.testing.assert_array_equal(mask, [False, False, True, True, False, False, False])
        self.assertEqual(report['lowScoreCandidatePointCount'], 4)
        self.assertEqual(report['protectedCandidatePointCount'], 2)

    def test_single_route_uses_tight_surface_tolerance(self):
        segment = dict(type=2, startM=[0, 0, .1], endM=[1, 0, .1],
                       radiusM=.004, pointCount=100, measuredSupportPointCount=20)
        points = np.array([[.5, .008, .1], [.504, .0075, .1]])
        mask, report = floating_noise_mask(points, np.array([2, 2]), self.bands, [segment],
                                           steel_scores=np.array([.5, .65]))
        np.testing.assert_array_equal(mask, [True, False])
        self.assertEqual(report['supportMarginM'], .012)

    def test_prior_only_model_cannot_self_protect_low_score_air_fit(self):
        prior_only = dict(type=2, startM=[0, 0, .1], endM=[1, 0, .1],
                          radiusM=.004, pointCount=100, measuredSupportPointCount=0)
        measured = dict(type=2, startM=[0, 1, .1], endM=[1, 1, .1],
                        radiusM=.004, pointCount=100, measuredSupportPointCount=20)
        points = np.array([[.5, .005, .1], [.5, 1.005, .1]])
        mask, report = floating_noise_mask(points, np.array([2, 2]), self.bands,
                                           [prior_only, measured], steel_scores=np.array([.2, .2]),
                                           observed_support_points=np.array([[.5, 1.006, .1]]))
        np.testing.assert_array_equal(mask, [True, False])
        self.assertEqual(report['priorOnlyRejectedSegmentCount'], 1)
        self.assertEqual(report['reliableSegmentCount'], 1)

    def test_scored_lower_band_requires_surface_or_frozen_observation(self):
        lower = dict(type=1, startM=[0, 0, .03], endM=[1, 0, .03],
                     radiusM=.004, pointCount=100, measuredSupportPointCount=100)
        points = np.array([[.5, .0045, .03], [.504, .0045, .03],
                           [.5, .05, .03], [.6, .05, .03]])
        mask, _ = floating_noise_mask(points, np.ones(4, int), self.bands, [lower],
                                      steel_scores=np.full(4, .65))
        np.testing.assert_array_equal(mask, [False, False, True, True])

    def test_frozen_observation_preserves_hook_and_finite_endpoint_neighbor(self):
        lower = dict(type=1, startM=[0, 0, .03], endM=[1, 0, .03],
                     radiusM=.004, pointCount=100, measuredSupportPointCount=100)
        points = np.array([[1.004, .008, .03], [.5, .03, .03]])
        anchors = np.array([[1.002, .002, .03]])
        mask, _ = floating_noise_mask(
            points, np.ones(2, int), self.bands, [lower],
            steel_scores=np.full(2, .65), observed_support_points=anchors)
        np.testing.assert_array_equal(mask, [False, True])

    def test_long_unobserved_cylinder_gap_does_not_protect_a_lone_point(self):
        lower = dict(type=1, startM=[0, 0, .03], endM=[1, 0, .03],
                     radiusM=.004, pointCount=100, measuredSupportPointCount=2)
        point = np.array([[.5, .004, .03]])
        anchors = np.array([[0, .004, .03], [1, .004, .03]])
        mask, _ = floating_noise_mask(
            point, np.array([1]), self.bands, [lower], steel_scores=np.array([.65]),
            observed_support_points=anchors)
        self.assertTrue(mask[0])

    def test_missing_lower_model_only_removes_isolated_strong_negative(self):
        points = np.array([[0, 0, .03], [1, 1, .03], [1.002, 1, .03]])
        mask, _ = floating_noise_mask(points, np.ones(3, int), self.bands, [],
                                      steel_scores=np.full(3, .65))
        np.testing.assert_array_equal(mask, [True, False, False])

    def test_each_finite_cylinder_uses_its_own_radius(self):
        segments = [
            dict(type=2, startM=[0, 1, .1], endM=[1, 1, .1],
                 radiusM=.050, pointCount=100),
            dict(type=2, startM=[0, 0, .1], endM=[1, 0, .1],
                 radiusM=.002, pointCount=100),
        ]
        # The point is outside the small cylinder. The unrelated large radius
        # must not enlarge that cylinder's envelope.
        mask, _ = floating_noise_mask(np.array([[.5, .020, .1]]), np.array([4]),
                                      self.bands, segments)
        self.assertTrue(mask[0])

    def test_finite_endpoint_boundary_is_exact(self):
        segment = dict(type=2, startM=[0, 0, .1], endM=[1, 0, .1],
                       radiusM=.004, pointCount=100)
        threshold = .004 + .012
        points = np.array([[1 + threshold, 0, .1],
                           [1 + threshold + 1e-6, 0, .1],
                           [2., 0, .1]])
        mask, _ = floating_noise_mask(points, np.full(3, 4), self.bands, [segment])
        np.testing.assert_array_equal(mask, [False, True, True])

    def test_type_one_cylinder_is_support_when_lower_band_is_missing(self):
        segment = dict(type=1, startM=[0, 0, .03], endM=[1, 0, .03],
                       radiusM=.004, pointCount=100)
        points = np.array([[.5, .01, .03], [.5, .03, .03]])
        mask, _ = floating_noise_mask(points, np.full(2, 4), [], [segment])
        np.testing.assert_array_equal(mask, [False, True])

    def test_scoreless_mode_keeps_assigned_scope_compatibility(self):
        points = np.array([[3, 3, .06], [3.1, 3, .06]])
        mask, report = floating_noise_mask(points, np.array([2, 4]), self.bands, self.segments)
        np.testing.assert_array_equal(mask, [False, True])
        self.assertIn('legacy scoreless mode', report['scope'])

    def test_score_contract_is_checked(self):
        with self.assertRaisesRegex(ValueError, '逐行对应'):
            floating_noise_mask(np.zeros((2, 3)), np.full(2, 4), self.bands, self.segments,
                                steel_scores=np.ones(1))
        with self.assertRaisesRegex(ValueError, '0..1'):
            floating_noise_mask(np.zeros((2, 3)), np.full(2, 4), self.bands, self.segments,
                                steel_scores=np.array([.5, 1.1]))


if __name__ == '__main__':
    unittest.main()
